"""Experimental full-weight, single-stream OpenCore TwinCore runtime.

The native Q6 bridge is experimental until full-model qualification, training
and evaluation pass. Resource checks import no tensor or GPU framework.
"""

from importlib import import_module

_EXPORTS = {
    'ExactSurfaceAlignment': 'alignment', 'CouplingBridge': 'bridge',
    'CoupledFusion': 'coupled', 'CoupledFeedback': 'bridge', 'CoupledStep': 'bridge',
    'ChunkedOutputHead': 'heads', 'offload_output_head': 'heads',
    'verify_checkpoint': 'manifest',
    **{name: 'budget' for name in ('BF16_CHECKPOINT_BYTES', 'K2_BF16_CHECKPOINT_BYTES',
        'NANBEIGE_BF16_CHECKPOINT_BYTES', 'estimate_bf16_kv_bytes', 'require_device_budget')},
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module('.' + _EXPORTS[name], __name__), name)
    globals()[name] = value
    return value

__all__ = [
    "BF16_CHECKPOINT_BYTES",
    "K2_BF16_CHECKPOINT_BYTES",
    "NANBEIGE_BF16_CHECKPOINT_BYTES",
    "CoupledFeedback",
    "CouplingBridge",
    "CoupledFusion",
    "CoupledStep",
    "ChunkedOutputHead",
    "ExactSurfaceAlignment",
    "estimate_bf16_kv_bytes",
    "require_device_budget",
    "verify_checkpoint",
    "offload_output_head",
]
