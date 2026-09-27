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

    Prefixes are recomputed each step. This is intentionally slower than a
    production cache, but handles cross-tokenizer suffix changes correctly.
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
    def _tower(model, ids: torch.Tensor, bias: torch.Tensor | None):
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
            output = text_base(inputs_embeds=embeds, use_cache=False)
            if output.last_hidden_state is None:
                raise RuntimeError("Full base decoder did not return its final state")
            last = output.last_hidden_state[:, -1, :]
            head = model.get_output_embeddings()
            head_input = last.to(device=head.weight.device, dtype=head.weight.dtype)
            return last, head(head_input)
        output = model(
            inputs_embeds=embeds,
            output_hidden_states=True,
            use_cache=False,
            logits_to_keep=1,
        )
        if not output.hidden_states:
            raise RuntimeError("Full decoder did not return hidden states")
        return output.hidden_states[-1][:, -1, :], output.logits[:, -1, :]

    def step(
        self,
        nanbeige_ids: torch.Tensor,
        k2_ids: torch.Tensor,
        *,
        feedback: CoupledFeedback | None = None,
    ) -> CoupledStep:
        if (
            nanbeige_ids.ndim != 2 or k2_ids.ndim != 2
            or nanbeige_ids.shape[0] != k2_ids.shape[0]
            or nanbeige_ids.shape[1] == 0 or k2_ids.shape[1] == 0
        ):
            raise ValueError("Both native token streams need a nonempty matching batch")
        n_hidden, n_native = self._tower(
            self.nanbeige, nanbeige_ids, None if feedback is None else feedback.nanbeige
        )
        k_hidden, k_native = self._tower(
            self.k2, k2_ids, None if feedback is None else feedback.k2
        )
        return super().forward(n_hidden, k_hidden, n_native, k_native,
                               self.nanbeige.get_output_embeddings(), self.k2.get_output_embeddings())

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
        feedback = None
        n_device = self.nanbeige.get_input_embeddings().weight.device
        k_device = self.k2.get_input_embeddings().weight.device
        if n_device.type == "meta":
            n_device = torch.device("cpu")
        if k_device.type == "meta":
            k_device = torch.device("cpu")
        eos = nanbeige_tokenizer.all_special_ids
        with torch.inference_mode():
            for _ in range(max_new_tokens):
                n_ids = nanbeige_tokenizer.encode(text, add_special_tokens=False)
                k_ids = k2_tokenizer.encode(k_text, add_special_tokens=False)
                self.last_text = text
                self.last_k2_ids = k_ids
                output = self.step(
                    torch.tensor([n_ids], dtype=torch.long, device=n_device),
                    torch.tensor([k_ids], dtype=torch.long, device=k_device),
                    feedback=feedback,
                )
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
                generated += piece
                feedback = output.feedback
                self.last_text = text
                self.last_k2_ids = k2_tokenizer.encode(k_text, add_special_tokens=False)
                self.last_generated_text = generated
                yield piece
