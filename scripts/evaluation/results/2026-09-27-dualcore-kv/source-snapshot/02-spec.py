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
class DuoCoreConfig:
    model_id: str
    live_window_tokens: int
    host: str
    port: int
    echo_port: int
    k2: BackboneSpec
    nanbeige: BackboneSpec

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def from_dict(cls, value: dict) -> "DuoCoreConfig":
        """Load the candidate-selection runtime fields; ignore old package metadata."""
        return cls(
            model_id=value["model_id"],
            live_window_tokens=int(value["live_window_tokens"]),
            host=value["host"],
            port=int(value["port"]),
            echo_port=int(value["echo_port"]),
            k2=BackboneSpec(**value["k2"]),
            nanbeige=BackboneSpec(**value["nanbeige"]),
        )

    @classmethod
    def load(cls, path: Path) -> "DuoCoreConfig":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def default_duocore_config() -> DuoCoreConfig:
    return DuoCoreConfig(
        model_id="DuoCore",
        live_window_tokens=65536,
        host="127.0.0.1",
        port=8830,
        echo_port=8833,
        k2=BackboneSpec(
            name="K2-Horizon-3.7B",
            repo="IFM/K2-Horizon-3.7B",
            gguf_repo="IFM/K2-Horizon-3.7B-GGUF",
            gguf_file="K2-Horizon-4B-Q6_K.gguf",
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
            gguf_file="Nanbeige_Nanbeige4.2-3B-Q6_K.gguf",
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
