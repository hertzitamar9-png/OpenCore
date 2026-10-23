from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class BackboneSpec:
    name: str
    repo: str
    gguf_repo: str
    gguf_file: str
    hidden_size: int
    num_hidden_layers: int
    num_attention_heads: int
    num_key_value_heads: int
    head_dim: int
    vocab_size: int
    native_context: int
    port: int

    @property
    def kv_width(self) -> int:
        return self.num_key_value_heads * self.head_dim

    def q4_kv_bytes(self, context_tokens: int) -> int:
        # Approximate packed Q4 K+V payload; allocator/metadata overhead is extra.
        return context_tokens * self.num_hidden_layers * self.kv_width


@dataclass(frozen=True)
class LayaJudgeSpec:
    enabled: bool = False
    checkpoint: str = "weights/laya-multilingual"
    revision: str = ""
    expected_sha256: str = ""
    device: str = "auto"
    min_cuda_free_mib: int = 3072
    max_len: int = 1024
    min_confidence: float = 0.64
    min_margin: float = 0.10


@dataclass(frozen=True)
class TwinCoreConfig:
    model_id: str
    live_window_tokens: int
    latent_size: int
    latent_slots: int
    bridge_rank: int
    bridge_checkpoint: str
    latent_agreement_weight: float
    max_consensus_cycles: int
    agreement_threshold: float
    confidence_threshold: float
    host: str
    port: int
    echo_port: int
    k2: BackboneSpec
    nanbeige: BackboneSpec
    laya_judge: LayaJudgeSpec | None = None
    max_negotiation_rounds: int = 4

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def from_dict(cls, value: dict) -> "TwinCoreConfig":
        """Load the consensus runtime fields while allowing package metadata beside them."""
        return cls(
            model_id=value["model_id"],
            live_window_tokens=int(value["live_window_tokens"]),
            latent_size=int(value["latent_size"]),
            latent_slots=int(value["latent_slots"]),
            bridge_rank=int(value["bridge_rank"]),
            bridge_checkpoint=value["bridge_checkpoint"],
            latent_agreement_weight=float(value["latent_agreement_weight"]),
            max_consensus_cycles=int(value["max_consensus_cycles"]),
            agreement_threshold=float(value["agreement_threshold"]),
            confidence_threshold=float(value["confidence_threshold"]),
            host=value["host"],
            port=int(value["port"]),
            echo_port=int(value["echo_port"]),
            k2=BackboneSpec(**value["k2"]),
            nanbeige=BackboneSpec(**value["nanbeige"]),
            laya_judge=(
                LayaJudgeSpec(**value["laya_judge"])
                if value.get("laya_judge") is not None
                else None
            ),
            max_negotiation_rounds=max(1, int(value.get("max_negotiation_rounds", 4))),
        )

    @classmethod
    def load(cls, path: Path) -> "TwinCoreConfig":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def default_twincore_config() -> TwinCoreConfig:
    return TwinCoreConfig(
        model_id="twincore-k2-nanbeige-consensus",
        live_window_tokens=262144,
        latent_size=2048,
        latent_slots=128,
        bridge_rank=256,
        bridge_checkpoint="bridge/twincore-bridge-v1.pt",
        latent_agreement_weight=0.35,
        max_consensus_cycles=6,
        agreement_threshold=0.82,
        confidence_threshold=0.64,
        host="127.0.0.1",
        port=8830,
        echo_port=8833,
        k2=BackboneSpec(
            name="K2-Horizon-3.7B",
            repo="IFM/K2-Horizon-3.7B",
            gguf_repo="IFM/K2-Horizon-3.7B-GGUF",
            gguf_file="K2-Horizon-4B-Q4_K_M.gguf",
            hidden_size=2560,
            num_hidden_layers=36,
            num_attention_heads=32,
            num_key_value_heads=8,
            head_dim=128,
            vocab_size=250624,
            native_context=524288,
            port=8831,
        ),
        nanbeige=BackboneSpec(
            name="Nanbeige4.2-3B",
            repo="Nanbeige/Nanbeige4.2-3B",
            gguf_repo="bartowski/Nanbeige_Nanbeige4.2-3B-GGUF",
            gguf_file="Nanbeige_Nanbeige4.2-3B-Q4_K_M.gguf",
            hidden_size=3072,
            num_hidden_layers=22,
            num_attention_heads=48,
            num_key_value_heads=8,
            head_dim=128,
            vocab_size=166144,
            native_context=262144,
            port=8832,
        ),
    )
