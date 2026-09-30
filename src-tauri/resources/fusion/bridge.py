"""Trainable coupling shared by complete Torch and native Q6 decoder towers."""
from dataclasses import dataclass
from typing import Callable

import torch
from torch import nn

from .alignment import ExactSurfaceAlignment


@dataclass
class CoupledFeedback:
    nanbeige: torch.Tensor
    k2: torch.Tensor


@dataclass
class CoupledStep:
    logits: torch.Tensor
    feedback: CoupledFeedback
    nanbeige_native_logits: torch.Tensor
    k2_native_logits: torch.Tensor
    first_cache: object | None = None
    second_cache: object | None = None


class CouplingBridge(nn.Module):
    def __init__(self, alignment: ExactSurfaceAlignment, *, nanbeige_hidden: int,
                 k2_hidden: int, rank: int = 256,
                 nanbeige_device='cpu', k2_device='cpu') -> None:
        super().__init__()
        if min(nanbeige_hidden, k2_hidden, rank) <= 0:
            raise ValueError('Hidden dimensions and bridge rank must be positive')
        self.alignment = alignment
        self.nanbeige_hidden, self.k2_hidden, self.rank = nanbeige_hidden, k2_hidden, rank
        self.k2_to_nanbeige = nn.Sequential(nn.Linear(k2_hidden, rank, bias=False), nn.SiLU(),
                                           nn.Linear(rank, nanbeige_hidden, bias=False)).to(nanbeige_device)
        self.nanbeige_to_k2 = nn.Sequential(nn.Linear(nanbeige_hidden, rank, bias=False), nn.SiLU(),
                                           nn.Linear(rank, k2_hidden, bias=False)).to(k2_device)
        self.nanbeige_gate = nn.Parameter(torch.tensor(-6.0, device=nanbeige_device))
        self.k2_gate = nn.Parameter(torch.tensor(-6.0, device=k2_device))
        self.lexical_gate = nn.Parameter(torch.tensor(-6.0, device=nanbeige_device))

    def bridge_parameters(self):
        for module in (self.k2_to_nanbeige, self.nanbeige_to_k2):
            yield from module.parameters()
        yield self.nanbeige_gate
        yield self.k2_gate
        yield self.lexical_gate

    @staticmethod
    def _head_input(project, delta):
        # Torch heads retain their original compute precision/placement. Native
        # Q6 projectors receive the small CPU float vector without dense weights.
        weight = getattr(project, 'weight', None)
        return delta if not isinstance(weight, torch.Tensor) else delta.to(device=weight.device, dtype=weight.dtype)

    def forward(self, n_hidden: torch.Tensor, k_hidden: torch.Tensor,
                n_native: torch.Tensor, k_native: torch.Tensor,
                n_project: Callable, k_project: Callable) -> CoupledStep:
        if (n_hidden.ndim != 2 or k_hidden.ndim != 2
                or n_hidden.shape[0] != k_hidden.shape[0]
                or n_hidden.shape[1] != self.nanbeige_hidden or k_hidden.shape[1] != self.k2_hidden):
            raise ValueError('Both hidden features must match the native batch and hidden dimensions')
        batch = n_hidden.shape[0]
        if (n_native.shape != (batch, self.alignment.nanbeige_vocab_size)
                or k_native.shape != (batch, self.alignment.k2_vocab_size)):
            raise ValueError('Both native logits must match the native batch and vocabulary')
        n_weight, k_weight = self.k2_to_nanbeige[0].weight, self.nanbeige_to_k2[0].weight
        n_delta = torch.sigmoid(self.nanbeige_gate) * self.k2_to_nanbeige(k_hidden.to(device=n_weight.device, dtype=n_weight.dtype))
        k_delta = torch.sigmoid(self.k2_gate) * self.nanbeige_to_k2(n_hidden.to(device=k_weight.device, dtype=k_weight.dtype))
        n_delta, k_delta = self._head_input(n_project, n_delta), self._head_input(k_project, k_delta)
        n_adjustment, k_adjustment = n_project(n_delta), k_project(k_delta)
        if n_adjustment.shape != n_native.shape or k_adjustment.shape != k_native.shape:
            raise ValueError('Native head projection returned the wrong vocabulary geometry')
        n_scores, k_scores = n_native + n_adjustment, k_native + k_adjustment
        lexical = self.alignment.project(k_scores).to(n_scores.device)
        fused = n_scores.float() + torch.sigmoid(self.lexical_gate) * lexical
        return CoupledStep(fused, CoupledFeedback(n_delta, k_delta), n_native, k_native)
