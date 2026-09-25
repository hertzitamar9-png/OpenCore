from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BridgeConfig:
    k2_hidden: int = 2560
    nanbeige_hidden: int = 3072
    shared_hidden: int = 2048
    rank: int = 256
    slots: int = 128


def build_bridge(config: BridgeConfig = BridgeConfig()):
    try:
        import torch
        from torch import nn
        import torch.nn.functional as F
    except ImportError as error:
        raise RuntimeError("TwinCore latent bridge requires PyTorch") from error

    class LowRankProjector(nn.Module):
        def __init__(self, source: int, target: int, rank: int):
            super().__init__()
            self.down = nn.Linear(source, rank, bias=False)
            self.up = nn.Linear(rank, target, bias=False)

        def forward(self, value):
            return self.up(self.down(value))

    class TwinLatentBridge(nn.Module):
        def __init__(self):
            super().__init__()
            c = config
            self.config = c
            self.slots = nn.Parameter(torch.empty(c.slots, c.shared_hidden))
            nn.init.normal_(self.slots, std=0.02)
            self.slot_query = nn.Linear(c.shared_hidden, c.rank, bias=False)
            self.k2_key = nn.Linear(c.k2_hidden, c.rank, bias=False)
            self.nb_key = nn.Linear(c.nanbeige_hidden, c.rank, bias=False)
            self.k2_value = LowRankProjector(c.k2_hidden, c.shared_hidden, c.rank)
            self.nb_value = LowRankProjector(c.nanbeige_hidden, c.shared_hidden, c.rank)
            self.gate = nn.Sequential(
                nn.LayerNorm(c.shared_hidden * 2),
                nn.Linear(c.shared_hidden * 2, c.rank),
                nn.SiLU(),
                nn.Linear(c.rank, 2),
            )
            self.to_k2 = LowRankProjector(c.shared_hidden, c.k2_hidden, c.rank)
            self.to_nb = LowRankProjector(c.shared_hidden, c.nanbeige_hidden, c.rank)
            self.shared_norm = nn.LayerNorm(c.shared_hidden)

        @staticmethod
        def _attend(query, key, value):
            scale = query.shape[-1] ** -0.5
            score = torch.matmul(query, key.transpose(-1, -2)) * scale
            weight = torch.softmax(score, dim=-1)
            return torch.matmul(weight, value)

        def forward(self, k2_hidden, nanbeige_hidden):
            if k2_hidden.ndim != 3 or nanbeige_hidden.ndim != 3:
                raise ValueError("TwinCore hidden states must be [batch, sequence, hidden]")
            if k2_hidden.shape[0] != nanbeige_hidden.shape[0]:
                raise ValueError("TwinCore twins must use the same batch size")
            batch = k2_hidden.shape[0]
            query = self.slot_query(self.slots).unsqueeze(0).expand(batch, -1, -1)
            k2_summary = self._attend(query, self.k2_key(k2_hidden), self.k2_value(k2_hidden))
            nb_summary = self._attend(query, self.nb_key(nanbeige_hidden), self.nb_value(nanbeige_hidden))
            logits = self.gate(torch.cat([k2_summary, nb_summary], dim=-1))
            weights = torch.softmax(logits, dim=-1)
            shared = self.shared_norm(
                weights[..., :1] * k2_summary + weights[..., 1:] * nb_summary
            )
            conflict = 1.0 - F.cosine_similarity(k2_summary, nb_summary, dim=-1).mean(dim=-1)
            return {
                "shared": shared,
                "conflict": conflict,
                "gate": weights,
                "feedback_k2": self.to_k2(shared),
                "feedback_nanbeige": self.to_nb(shared),
            }

    return TwinLatentBridge()
