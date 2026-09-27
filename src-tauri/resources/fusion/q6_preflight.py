"""Fail before loading full weights when the user's GPU/disk budget is unavailable."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from .q6_identity import CHECKPOINTS, file_digest
from .budget import estimate_bf16_kv_bytes, require_device_budget

SCRATCH_RESERVE = 1_073_741_824


def require_q6_resources(*, context, free_gpu_bytes, free_disk_bytes, environment=None):
    environment = os.environ if environment is None else environment
    visible = environment.get('CUDA_VISIBLE_DEVICES')
    if visible is not None and (not visible.strip() or visible.strip().split(',')[0] == '-1'):
        raise ValueError('CUDA is disabled; refusing full native Q6 CPU fallback')
    if context < 32 or context > 8192:
        raise ValueError('TwinCore context must remain within the experimental 32–8192 token bounds')
    if free_disk_bytes < 200_000_000_000:
        raise ValueError('The 200 GB free disk reserve must be retained')
    weights = sum(entry['bytes'] for entry in CHECKPOINTS.values())
    kv = estimate_bf16_kv_bytes(context)
    margin = require_device_budget(free_gpu_bytes, weights, kv, SCRATCH_RESERVE)
    return {'context': context, 'q6_checkpoint_bytes': weights, 'estimated_bf16_kv_bytes': kv,
            'scratch_reserve_bytes': SCRATCH_RESERVE, 'required_gpu_bytes': weights + kv + SCRATCH_RESERVE,
            'gpu_margin_bytes': margin, 'free_disk_bytes': free_disk_bytes,
            'scope': 'Conservative load estimate; actual full-model GPU fit still needs measurement'}


def gpu_snapshot():
    output = subprocess.check_output([
        'nvidia-smi', '--query-gpu=index,uuid,memory.total,memory.used,memory.free,utilization.gpu',
        '--format=csv,noheader,nounits'], text=True, timeout=10)
    rows = [line.split(',') for line in output.strip().splitlines() if line.strip()]
    if not rows:
        raise ValueError('No NVIDIA GPU is available for native Q6 training')
    visible = os.environ.get('CUDA_VISIBLE_DEVICES', '0').split(',')[0].strip()
    row = next((values for values in rows if visible in (values[0].strip(), values[1].strip())), None)
    if row is None:
        raise ValueError('The visible native CUDA GPU cannot be matched to its memory budget')
    index, uuid, total, used, free, utilization = [value.strip() for value in row]
    return {'index': int(index), 'uuid': uuid, 'total_bytes': int(total) * 1_048_576,
            'used_bytes': int(used) * 1_048_576, 'free_bytes': int(free) * 1_048_576,
            'utilization_percent': int(utilization)}


def preflight(context, directory):
    gpu = gpu_snapshot()
    budget = require_q6_resources(context=context, free_gpu_bytes=gpu['free_bytes'],
                                  free_disk_bytes=shutil.disk_usage(directory).free)
    return {'gpu': gpu, 'budget': budget}


def verify_q6_checkpoint(path: Path, brain: str):
    expected = CHECKPOINTS[brain]
    path = Path(path)
    if not path.is_file() or path.stat().st_size != expected['bytes'] or file_digest(path) != expected['sha256']:
        raise ValueError(f'{brain} checkpoint identity does not match the complete pinned Q6_K file')
    return dict(expected)
