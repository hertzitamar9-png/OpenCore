"""Experimental full-weight, single-stream OpenCore TwinCore runtime.

This is not yet exposed as an app model: its Nanbeige source weights are not
installed and its bridge is untrained.
"""

from .alignment import ExactSurfaceAlignment
from .budget import (
    BF16_CHECKPOINT_BYTES,
    K2_BF16_CHECKPOINT_BYTES,
    NANBEIGE_BF16_CHECKPOINT_BYTES,
    estimate_bf16_kv_bytes,
    require_device_budget,
)
from .coupled import CoupledFusion, CoupledFeedback, CoupledStep
from .heads import ChunkedOutputHead, offload_output_head
from .manifest import verify_checkpoint

__all__ = [
    "BF16_CHECKPOINT_BYTES",
    "K2_BF16_CHECKPOINT_BYTES",
    "NANBEIGE_BF16_CHECKPOINT_BYTES",
    "CoupledFeedback",
    "CoupledFusion",
    "CoupledStep",
    "ChunkedOutputHead",
    "ExactSurfaceAlignment",
    "estimate_bf16_kv_bytes",
    "require_device_budget",
    "verify_checkpoint",
    "offload_output_head",
]
