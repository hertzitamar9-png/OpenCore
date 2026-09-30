"""Safe backend boundary. Native inference owns positions, KV and recurrent state.

No adapter exports or relocates historical KV. Canonical evidence is tokenized
by the CURRENT backend, then normally prefills the bounded active prompt.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class EchoModelAdapter:
    properties: dict
    count_tokens: object
    family: str = "generic_transformer"

    @property
    def identity(self):
        # Include every supplied backend property, including model revision,
        # tokenizer, RoPE, quantization and cache format. Unknown properties
        # cannot accidentally make an old materialization appear compatible.
        return fingerprint({"adapter_version": 1, "family": self.family,
                            "properties": self.properties})

    def inspect_capabilities(self):
        attention = self.properties.get("attention") or {}
        if not isinstance(attention, dict):
            attention = {}
        kv_heads, heads = attention.get("num_key_value_heads"), attention.get("num_attention_heads")
        return {
            "adapter": self.family,
            "model_fingerprint": self.identity,
            "materialization_mode": "rematerialization",
            "materialization_cache": "canonical text and current-tokenizer counts; backend prefix cache",
            "supports_direct_kv_reuse": False,
            "supports_rope_rebase": False,
            "supports_virtual_positions": False,
            "supports_external_memory": False,
            "supports_sliding_window": bool(attention.get("sliding_window")),
            "supports_gqa": isinstance(kv_heads, int) and isinstance(heads, int) and 1 < kv_heads < heads,
            "supports_mqa": kv_heads == 1 and isinstance(heads, int) and heads > 1,
            "supports_decoder_pause": False,
            "stored_kv_formats": [],
        }

    def tokenize_memory(self, text):
        return max(0, int(self.count_tokens(text)))

    def prepare_transcript(self, live):
        if live.model_fingerprint == self.identity:
            return False
        # Canonical history is portable; stored token counts and native cache
        # ownership are not. Recompute only the bounded live working set once.
        for entry in live.entries:
            entry["tokens"] = live.cost(entry["message"], self.count_tokens)
            entry["backend_sent"] = False
            if entry.get("kind") == live.MEMORY:
                entry["echo_retrieval_tokens"] = entry["tokens"]
        live.model_fingerprint = self.identity
        live.save()
        return True

    def plan_memory_layout(self, pinned_tokens, evidence_tokens, recent_tokens, reserve_tokens, capacity):
        if pinned_tokens + evidence_tokens + recent_tokens + reserve_tokens > capacity:
            raise ValueError("ECHO active layout exceeds the configured model capacity")
        return {
            "pinned": [0, pinned_tokens],
            "echo": [pinned_tokens, pinned_tokens + evidence_tokens],
            "recent": [pinned_tokens + evidence_tokens, pinned_tokens + evidence_tokens + recent_tokens],
            "reserve": reserve_tokens,
            "capacity": capacity,
            "positions": "logical token-accounting ranges; backend applies its own chat template and valid positions",
        }

    def materialize_memory(self, text):
        return {"content": text, "tokens": self.tokenize_memory(text) + 8,
                "mode": self.inspect_capabilities()["materialization_mode"]}

    def attach_memory(self, live, text, tokens, hashes, metadata):
        current = [e for e in live.entries if e.get("kind") == live.MEMORY]
        if not current and not text:
            return False
        if len(current) == 1 and current[0]["message"].get("content") == text:
            current[0].update(metadata)
            current[0]["tokens"] = tokens
            current[0]["echo_retrieval_tokens"] = tokens
            return False
        changed_processed_prefix = any(e.get("backend_sent") for e in live.entries)
        live.entries = [e for e in live.entries if e.get("kind") != live.MEMORY]
        if text:
            entry = {"message": {"role": "user", "content": text}, "tokens": tokens,
                     "kind": live.MEMORY, "backend_sent": False,
                     "echo_source_hashes": hashes, "echo_retrieval_tokens": tokens, **metadata}
            # Logical evidence slots precede recent turns. A changed processed
            # prefix MUST be re-prefilled, including on recurrent backends.
            live.entries.insert(0, entry)
        if changed_processed_prefix:
            for entry in live.entries:
                entry["backend_sent"] = False
        return changed_processed_prefix

    def detach_memory(self, live):
        return self.attach_memory(live, "", 0, [], {})

    def rebase_kv_if_supported(self, *args, **kwargs):
        raise NotImplementedError("Raw KV relocation has not been validated for this backend")

    def estimate_vram(self, tokens):
        per_token = self.properties.get("kv_bytes_per_token")
        return int(per_token * tokens) if isinstance(per_token, (int, float)) else None

    def invalidate_cache(self, archive):
        with archive._lock:
            archive.db.execute("DELETE FROM echo_materializations WHERE model=?", (self.identity,))
            archive.db.commit()


class RecurrentModelAdapter(EchoModelAdapter):
    def inspect_capabilities(self):
        value = super().inspect_capabilities()
        value["materialization_mode"] = "textual_reprefill"
        value["recurrent_state"] = "native backend state; reset and replay active text when layout changes"
        return value


ADAPTER_REGISTRY = (
    (("mamba", "rwkv", "lfm", "recurrent", "ssm"), RecurrentModelAdapter),
    (("llama", "qwen", "mistral", "gemma", "phi", "moe", "transformer"), EchoModelAdapter),
)


def adapter_for(properties, count_tokens):
    architecture = str(properties.get("architecture") or properties.get("model_type") or "").lower()
    for patterns, adapter in ADAPTER_REGISTRY:
        if any(pattern in architecture for pattern in patterns):
            return adapter(dict(properties), count_tokens, architecture or "generic_transformer")
    return EchoModelAdapter(dict(properties), count_tokens)
