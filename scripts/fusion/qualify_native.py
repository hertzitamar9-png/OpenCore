"""Measure the actual full Q6 pair; preflight alone never qualifies a model."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import faulthandler
import json
from pathlib import Path
import sys
import time

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / 'src-tauri/resources'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nanbeige', type=Path)
    parser.add_argument('--k2', type=Path)
    parser.add_argument('--context', type=int, default=1024)
    parser.add_argument('--rank', type=int, default=256)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--recompute', action='store_true')
    parser.add_argument('--dll', type=Path, default=APP / 'src-tauri/resources/fusion/native/build/Release/twincore.dll')
    parser.add_argument('--runtime', type=Path, default=APP / 'src-tauri/resources/doucode/runtime')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        raise ValueError('Use a fresh report path in an existing directory')
    from fusion.q6_preflight import gpu_snapshot, require_q6_resources
    from fusion.qualification import execution_configuration
    from fusion.q6_identity import file_digest
    import shutil
    faulthandler.enable()
    started = time.monotonic()
    report = {'schema': 2, 'status': 'probe_in_progress', 'gpu_qualified': False, 'training_qualified': False,
              'time_utc': datetime.now(timezone.utc).isoformat(), 'models_loaded': False,
              'models_released': False,
              'driver_sha256': file_digest(__file__),
              'configuration': execution_configuration(context=args.context, rank=args.rank,
                                                       seed=args.seed, recompute=args.recompute)}

    def save(phase):
        report['phase'] = phase
        report['elapsed_seconds'] = time.monotonic() - started
        temporary = args.output.with_suffix(args.output.suffix + '.tmp')
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        temporary.replace(args.output)
        print(json.dumps({'phase': phase, 'status': report['status'],
                          'seconds': report['elapsed_seconds']}), flush=True)

    save('resource_check')
    try:
        report['gpu_before'] = gpu_snapshot()
        report['budget'] = require_q6_resources(context=args.context,
            free_gpu_bytes=report['gpu_before']['free_bytes'], free_disk_bytes=shutil.disk_usage(APP).free)
    except (ValueError, OSError) as error:
        report.update(status='preflight_refused', error=str(error))
        save('preflight_refused')
        return 2
    if args.preflight_only:
        report.update(status='preflight_only', scope='Budget estimate only; no checkpoint, GPU fit or quality was tested')
        save('preflight_only')
        return 0
    if not args.nanbeige or not args.k2:
        raise ValueError('Both full Q6 checkpoint paths are required')
    from fusion.q6_pair import open_pair
    from fusion.training import teacher_forced
    begin = time.monotonic()
    pair = None
    try:
        save('loading_full_q6')
        with open_pair(args.nanbeige, args.k2, args.dll, args.runtime,
                       context=args.context, rank=args.rank, seed=args.seed, recompute=args.recompute) as pair:
            report['models_loaded'] = True
            report['binding'], report['placement'] = pair.binding, pair.placement
            report['loaded_numerical_libraries'] = pair.native.api.loaded_libraries
            report['gpu_loaded'] = gpu_snapshot()
            sample = {'messages': [{'role': 'user', 'content': 'Write a Python addition function.'}],
                      'answer': 'def add(a, b):\n    return a + b\n'}
            save('forward_backward')
            measured = teacher_forced(pair.native, pair.bridge, sample, max_tokens=64)
            save('bridge_backward')
            measured.loss.backward()
            import torch
            finite_gradients = all(parameter.grad is not None and torch.isfinite(parameter.grad).all()
                                   for parameter in pair.bridge.bridge_parameters())
            if not finite_gradients or not measured.complete:
                raise ValueError('The full pair did not produce a complete finite forward/backward probe')
            report['probe'] = {'tokens': measured.tokens, 'loss': float(measured.loss.detach()),
                'elapsed_seconds': time.monotonic() - begin, 'finite_bridge_gradients': True,
                'complete_target': measured.complete, 'torch': torch.__version__, 'torch_cuda_build': torch.version.cuda,
                'scope': 'Teacher-forced full Q6 forward/backward resource probe with initialized adapters; no generated coding score'}
            report['gpu_after_probe'] = gpu_snapshot()
            report['sampled_peak_vram_bytes'] = max(report[key]['used_bytes'] for key in
                ('gpu_loaded', 'gpu_after_probe'))
            save('releasing_models')
        if pair.native.closed is not True:
            raise ValueError('The full Q6 models did not confirm native release')
        report['models_released'] = True
        report['gpu_after_close'] = gpu_snapshot()
        report.update(status='full_q6_resource_probe_passed', gpu_qualified=True,
                      scope='Both full frozen Q6 towers and heads ran on GPU with finite adapter gradients. No trained quality or app activation claim.')
    except Exception as error:
        report['models_released'] = pair is not None and pair.native.closed is True
        report.update(status='full_q6_probe_failed', error=str(error))
        save('full_q6_probe_failed')
        raise
    save('complete')
    print(json.dumps({key: value for key, value in report.items() if key != 'binding'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
