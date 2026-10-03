"""Conservative, model-specific Q6 resource accounting for Qwen+K2.

This module estimates only the BF16 KV payload of full-attention layers. It is
not a model loader, memory-fit proof, training bridge, or speed qualification.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_MANIFEST_PATH = Path(__file__).with_name("qwen_k2_q6_manifest.json")
with _MANIFEST_PATH.open("r", encoding="utf-8") as _manifest_file:
    MANIFEST: dict[str, Any] = json.load(_manifest_file)

QWEN_Q6: dict[str, Any] = MANIFEST["artifacts"]["qwen"]
K2_Q6: dict[str, Any] = MANIFEST["artifacts"]["k2"]
Q6_WEIGHT_BYTES = QWEN_Q6["bytes"] + K2_Q6["bytes"]

_QWEN_FULL_ATTENTION_ELEMENTS = (
    MANIFEST["attention_layout"]["qwen"]["full_attention_layers"]
    * MANIFEST["attention_layout"]["qwen"]["kv_heads"]
    * MANIFEST["attention_layout"]["qwen"]["head_dim"]
)
_K2_FULL_ATTENTION_ELEMENTS = (
    MANIFEST["attention_layout"]["k2"]["full_attention_layers"]
    * MANIFEST["attention_layout"]["k2"]["kv_heads"]
    * MANIFEST["attention_layout"]["k2"]["head_dim"]
)
_KV_BYTES_PER_TOKEN = (
    (_QWEN_FULL_ATTENTION_ELEMENTS + _K2_FULL_ATTENTION_ELEMENTS)
    * 2  # key and value
    * MANIFEST["attention_layout"]["kv_dtype_bytes"]
)


def _nonnegative_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def estimate_attention_kv_bytes(tokens: int) -> int:
    """Return the BF16 KV lower bound for both towers' full-attention layers."""
    tokens = _nonnegative_int("token count", tokens)
    return tokens * _KV_BYTES_PER_TOKEN


def require_gpu_budget(
    available_gpu_bytes: int,
    *,
    context_tokens: int,
    resident_weight_bytes: int | None = None,
    runtime_reserve_bytes: int = 1024**3,
) -> dict[str, int | bool | str]:
    """Reject a plan whose known lower-bound allocations exceed GPU capacity.

    Passing a resident weight subset describes a hypothetical offload layout;
    it does not assert that the backend can implement that layout efficiently.
    """
    available_gpu_bytes = _nonnegative_int("available GPU bytes", available_gpu_bytes)
    context_tokens = _nonnegative_int("token count", context_tokens)
    runtime_reserve_bytes = _nonnegative_int("runtime reserve", runtime_reserve_bytes)
    if resident_weight_bytes is None:
        resident_weight_bytes = Q6_WEIGHT_BYTES
    resident_weight_bytes = _nonnegative_int("resident weights", resident_weight_bytes)
    if resident_weight_bytes > Q6_WEIGHT_BYTES:
        raise ValueError("resident weights cannot exceed the pinned Q6 model weights")

    attention_kv_bytes = estimate_attention_kv_bytes(context_tokens)
    required_gpu_bytes = resident_weight_bytes + attention_kv_bytes + runtime_reserve_bytes
    if required_gpu_bytes > available_gpu_bytes:
        raise ValueError(
            "resident weights plus attention KV lower bound and runtime reserve "
            f"require {required_gpu_bytes:,} bytes, exceeding available GPU memory "
            f"{available_gpu_bytes:,} bytes"
        )

    return {
        "available_gpu_bytes": available_gpu_bytes,
        "required_gpu_bytes": required_gpu_bytes,
        "gpu_margin_bytes": available_gpu_bytes - required_gpu_bytes,
        "resident_weight_bytes": resident_weight_bytes,
        "nonresident_weight_bytes": Q6_WEIGHT_BYTES - resident_weight_bytes,
        "attention_kv_bytes_lower_bound": attention_kv_bytes,
        "runtime_reserve_bytes": runtime_reserve_bytes,
        "known_payloads_fit": True,
        "qualified_for_device": False,
        "throughput_qualified": False,
        "scope": (
            "Known-payload budget only. Excludes Qwen linear-attention recurrent "
            "state, activations, graph/workspace, allocator fragmentation and "
            "runtime overhead beyond the explicit reserve. CPU offload feasibility "
            "and generation speed require measurement."
        ),
    }
