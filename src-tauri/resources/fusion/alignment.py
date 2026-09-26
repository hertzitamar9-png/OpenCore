"""Sparse, exact-surface mapping between native Nanbeige and K2 token IDs."""

from __future__ import annotations

import math

import torch
from torch import nn


class ExactSurfaceAlignment(nn.Module):
    def __init__(
        self,
        nanbeige_ids: list[int],
        k2_ids: list[int],
        nanbeige_vocab_size: int,
        k2_vocab_size: int,
    ) -> None:
        super().__init__()
        if len(nanbeige_ids) != len(k2_ids) or not nanbeige_ids:
            raise ValueError("Exact surface alignment needs at least one token pair")
        if len(set(nanbeige_ids)) != len(nanbeige_ids):
            raise ValueError("A Nanbeige token must not map to multiple K2 tokens")
        if max(nanbeige_ids) >= nanbeige_vocab_size or max(k2_ids) >= k2_vocab_size:
            raise ValueError("Aligned token ID exceeds an LM head vocabulary")
        self.nanbeige_vocab_size = int(nanbeige_vocab_size)
        self.k2_vocab_size = int(k2_vocab_size)
        self.register_buffer("nanbeige_ids", torch.tensor(nanbeige_ids, dtype=torch.long))
        self.register_buffer("k2_ids", torch.tensor(k2_ids, dtype=torch.long))

    @property
    def size(self) -> int:
        return int(self.nanbeige_ids.numel())

    @classmethod
    def from_tokenizers(
        cls,
        nanbeige_tokenizer,
        k2_tokenizer,
        nanbeige_vocab_size: int,
        k2_vocab_size: int,
    ) -> "ExactSurfaceAlignment":
        nanbeige_vocab = nanbeige_tokenizer.get_vocab()
        k_vocab = k2_tokenizer.get_vocab()
        nanbeige_special = set(nanbeige_tokenizer.all_special_ids)
        k_special = set(k2_tokenizer.all_special_ids)
        pairs = []
        for piece in nanbeige_vocab.keys() & k_vocab.keys():
            n_id, k_id = nanbeige_vocab[piece], k_vocab[piece]
            if (
                n_id in nanbeige_special or k_id in k_special
                or n_id >= nanbeige_vocab_size or k_id >= k2_vocab_size
            ):
                continue
            n_surface = nanbeige_tokenizer.decode(
                [n_id], skip_special_tokens=False, clean_up_tokenization_spaces=False
            )
            if not n_surface or "\ufffd" in n_surface:
                continue
            k_surface = k2_tokenizer.decode(
                [k_id], skip_special_tokens=False, clean_up_tokenization_spaces=False
            )
            if n_surface == k_surface:
                pairs.append((n_id, k_id))
        pairs.sort()
        return cls(
            [n for n, _ in pairs],
            [k for _, k in pairs],
            nanbeige_vocab_size,
            k2_vocab_size,
        )

    def project(self, k2_logits: torch.Tensor) -> torch.Tensor:
        """Centered K2 log evidence in Nanbeige vocabulary; zero for unmatched IDs."""
        if k2_logits.ndim != 2 or k2_logits.shape[1] != self.k2_vocab_size:
            raise ValueError("K2 logits must be [batch, native K2 vocabulary]")
        source = k2_logits.float()
        log_prob = source.log_softmax(dim=-1)
        matched = log_prob.index_select(1, self.k2_ids.to(source.device))
        matched = matched + math.log(self.k2_vocab_size)
        target = torch.zeros(
            (source.shape[0], self.nanbeige_vocab_size), device=source.device, dtype=source.dtype
        )
        return target.scatter(1, self.nanbeige_ids.to(source.device).expand(source.shape[0], -1), matched)
