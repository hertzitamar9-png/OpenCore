"""One-token reference decoder using both complete native causal LMs."""

from __future__ import annotations

from typing import Iterator

import torch
import torch.nn.functional as F
from torch import nn

from .alignment import ExactSurfaceAlignment
from .bridge import CouplingBridge, CoupledFeedback, CoupledStep


class CoupledFusion(CouplingBridge):
    """Reference Fusion: two frozen full towers, trainable bidirectional adapter.

    Generation reuses each tower's native cache. If the second tokenizer
    revises a prior boundary, only that tower is replayed from the full text;
    the first tower consumes its own generated token IDs incrementally.
    """

    def __init__(
        self,
        nanbeige: nn.Module,
        k2: nn.Module,
        alignment: ExactSurfaceAlignment,
        *,
        nanbeige_hidden: int,
        k2_hidden: int,
        rank: int = 256,
    ) -> None:
        super().__init__(alignment, nanbeige_hidden=nanbeige_hidden, k2_hidden=k2_hidden,
                         rank=rank, nanbeige_device=nanbeige.get_output_embeddings().weight.device,
                         k2_device=k2.get_output_embeddings().weight.device)
        self.nanbeige = nanbeige.requires_grad_(False).eval()
        self.k2 = k2.requires_grad_(False).eval()
        self.last_text = ""
        self.last_k2_ids: list[int] = []
        self.last_generated_text = ""

    def train(self, mode: bool = True):
        super().train(mode)
        self.nanbeige.eval()
        self.k2.eval()
        return self

    @staticmethod
    def _tower(
        model,
        ids: torch.Tensor,
        bias: torch.Tensor | None,
        *,
        past_key_values=None,
        use_cache: bool = False,
    ):
        embedding = model.get_input_embeddings()
        # Accelerate represents CPU-offloaded parameters as meta placeholders
        # between calls. The reference runtime keeps these two tables resident
        # in CPU RAM; call F.embedding directly so a stale Accelerate hook does
        # not send CPU token IDs back to CUDA while the table remains on CPU.
        embedding_device = embedding.weight.device
        if embedding_device.type == "cpu":
            embeds = F.embedding(ids.to("cpu"), embedding.weight, embedding.padding_idx)
        elif embedding_device.type == "meta":
            raise RuntimeError("A Fusion input embedding was left offloaded to meta memory")
        else:
            embeds = embedding(ids.to(embedding_device))
        base = getattr(model, "model", None)
        if base is not None:
            text_base = getattr(base, "language_model", base)
            layers = getattr(text_base, "layers", None)
            if layers is not None and len(layers):
                decoder_device = next(layers[0].parameters()).device
            else:
                decoder_device = next(base.parameters()).device
            embeds = embeds.to(decoder_device)
        if bias is not None:
            if bias.shape != (ids.shape[0], embeds.shape[-1]):
                raise ValueError("Feedback size does not match native model hidden size")
            bias = bias.to(device=embeds.device, dtype=embeds.dtype)
            embeds = torch.cat((embeds[:, :-1], embeds[:, -1:] + bias[:, None, :]), dim=1)
        if base is not None:
            # K2's pinned causal-LM wrapper does not expose hidden_states even
            # when requested. Its base decoder does expose last_hidden_state.
            tower_kwargs = {
                "inputs_embeds": embeds,
                "use_cache": use_cache,
            }
            if use_cache or past_key_values is not None:
                tower_kwargs["past_key_values"] = past_key_values
            output = text_base(**tower_kwargs)
            if output.last_hidden_state is None:
                raise RuntimeError("Full base decoder did not return its final state")
            last = output.last_hidden_state[:, -1, :]
            head = model.get_output_embeddings()
            head_input = last.to(device=head.weight.device, dtype=head.weight.dtype)
            return last, head(head_input), getattr(output, "past_key_values", None)
        kwargs = {
            "inputs_embeds": embeds,
            "output_hidden_states": True,
            "use_cache": use_cache,
            "logits_to_keep": 1,
        }
        if use_cache or past_key_values is not None:
            kwargs["past_key_values"] = past_key_values
        output = model(**kwargs)
        if not output.hidden_states:
            raise RuntimeError("Full decoder did not return hidden states")
        return (
            output.hidden_states[-1][:, -1, :],
            output.logits[:, -1, :],
            getattr(output, "past_key_values", None),
        )

    def step(
        self,
        nanbeige_ids: torch.Tensor,
        k2_ids: torch.Tensor,
        *,
        feedback: CoupledFeedback | None = None,
        first_cache=None,
        second_cache=None,
        use_cache: bool = False,
    ) -> CoupledStep:
        if (
            nanbeige_ids.ndim != 2 or k2_ids.ndim != 2
            or nanbeige_ids.shape[0] != k2_ids.shape[0]
            or nanbeige_ids.shape[1] == 0 or k2_ids.shape[1] == 0
        ):
            raise ValueError("Both native token streams need a nonempty matching batch")
        n_hidden, n_native, next_first_cache = self._tower(
            self.nanbeige,
            nanbeige_ids,
            None if feedback is None else feedback.nanbeige,
            past_key_values=first_cache,
            use_cache=use_cache,
        )
        k_hidden, k_native, next_second_cache = self._tower(
            self.k2,
            k2_ids,
            None if feedback is None else feedback.k2,
            past_key_values=second_cache,
            use_cache=use_cache,
        )
        result = super().forward(
            n_hidden,
            k_hidden,
            n_native,
            k_native,
            self.nanbeige.get_output_embeddings(),
            self.k2.get_output_embeddings(),
        )
        result.first_cache = next_first_cache
        result.second_cache = next_second_cache
        return result

    def stream_text(
        self,
        prompt: str,
        nanbeige_tokenizer,
        k2_tokenizer,
        *,
        k2_prompt: str | None = None,
        max_new_tokens: int,
    ) -> Iterator[str]:
        if not prompt:
            raise ValueError("A nonempty prompt is required by this reference decoder")
        if max_new_tokens < 0:
            raise ValueError("max_new_tokens must not be negative")
        text = prompt
        k_text = prompt if k2_prompt is None else k2_prompt
        if not k_text:
            raise ValueError("K2 needs a nonempty native prompt")
        generated = ""
        self.last_generated_text = generated
        if max_new_tokens == 0:
            return
        n_device = self.nanbeige.get_input_embeddings().weight.device
        k_device = self.k2.get_input_embeddings().weight.device
        if n_device.type == "meta":
            n_device = torch.device("cpu")
        if k_device.type == "meta":
            k_device = torch.device("cpu")
        eos = nanbeige_tokenizer.all_special_ids
        with torch.inference_mode():
            n_ids = nanbeige_tokenizer.encode(text, add_special_tokens=False)
            k_ids = k2_tokenizer.encode(k_text, add_special_tokens=False)
            output = self.step(
                torch.tensor([n_ids], dtype=torch.long, device=n_device),
                torch.tensor([k_ids], dtype=torch.long, device=k_device),
                use_cache=True,
            )
            if output.first_cache is None or output.second_cache is None:
                raise RuntimeError("Both Fusion towers must return incremental decode caches")

            for token_index in range(max_new_tokens):
                next_id = int(output.logits.argmax(dim=-1).item())
                if next_id in eos:
                    return
                piece = nanbeige_tokenizer.decode(
                    [next_id], skip_special_tokens=False,
                    clean_up_tokenization_spaces=False,
                )
                if not piece or "\ufffd" in piece:
                    raise RuntimeError("Generated token has no complete text surface")
                text += piece
                k_text += piece
                next_k_ids = k2_tokenizer.encode(k_text, add_special_tokens=False)
                self.last_text = text
                self.last_k2_ids = next_k_ids
                generated += piece
                self.last_generated_text = generated
                yield piece

                # Preserve each tower's cache while its tokenizer keeps the
                # already-cached token prefix stable. If K2 merges/revises a
                # token at the append boundary, replay that complete K2 prefix
                # instead of feeding a subtly different token history.
                if token_index + 1 == max_new_tokens:
                    return
                if (
                    len(next_k_ids) > len(k_ids)
                    and next_k_ids[:len(k_ids)] == k_ids
                ):
                    next_k_input = next_k_ids[len(k_ids):]
                    next_k_cache = output.second_cache
                else:
                    next_k_input = next_k_ids
                    next_k_cache = None
                output = self.step(
                    torch.tensor([[next_id]], dtype=torch.long, device=n_device),
                    torch.tensor([next_k_input], dtype=torch.long, device=k_device),
                    feedback=output.feedback,
                    first_cache=output.first_cache,
                    second_cache=next_k_cache,
                    use_cache=True,
                )
                if output.first_cache is None or output.second_cache is None:
                    raise RuntimeError("Both Fusion towers must return incremental decode caches")
                k_ids = next_k_ids
