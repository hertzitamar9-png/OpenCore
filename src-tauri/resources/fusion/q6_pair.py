"""Construct the approved full Q6 pair after resource and content verification."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import torch

from .adapter import make_binding
from .bridge import CouplingBridge
from .canonical import native_alignment
from .native import NativeAPI, NativeTwinCore
from .q6_preflight import preflight, verify_q6_checkpoint
from .qualification import execution_configuration


@dataclass
class Q6Pair:
    native: NativeTwinCore
    bridge: CouplingBridge
    binding: dict
    resource_plan: dict
    placement: list[dict]
    configuration: dict

    def close(self):
        self.native.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def open_pair(nanbeige: Path, k2: Path, dll: Path, runtime: Path, *,
              context=1024, rank=256, seed=7, recompute=False):
    # No torch.cuda call: the small adapters stay on CPU while the native
    # runtime alone owns GPU allocations. A second CUDA framework is unnecessary.
    torch.set_num_threads(2)
    configuration = execution_configuration(context=context, rank=rank, seed=seed, recompute=recompute)
    plan = preflight(context, Path(nanbeige).resolve().parent)
    checkpoints = {key: verify_q6_checkpoint(path, key) for key, path in (('nanbeige', nanbeige), ('k2', k2))}
    api = NativeAPI(dll, runtime)
    native = NativeTwinCore(api, nanbeige, k2, context=context, gpu_layers=99, recompute=recompute)
    try:
        placement = [native.statistics(brain) for brain in (0, 1)]
        if any(not record['head_on_gpu'] or record['physical_matrix_layers'] < 1
               or record['gpu_matrix_layers'] != record['physical_matrix_layers'] for record in placement):
            raise ValueError('Both complete Q6 decoders and heads must run on GPU; CPU fallback was rejected')
        alignment = native_alignment(native.surfaces())
        torch.manual_seed(seed)
        bridge = CouplingBridge(alignment, nanbeige_hidden=native.geometry[0]['hidden'],
                                k2_hidden=native.geometry[1]['hidden'], rank=rank)
        binding = make_binding(bridge, checkpoints, api.identity)
        return Q6Pair(native, bridge, binding, plan, placement, configuration)
    except Exception:
        native.close()
        raise
