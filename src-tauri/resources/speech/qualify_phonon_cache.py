"""Qualify installed BF16/FP32 cache miss/hit in separate CPU processes.

No dependencies, checkpoint files, settings or publisher sources are modified.
The caller must supply a new cache directory outside the installed checkpoint.
"""
import argparse
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def child(model_root, cache_root, precision, verify):
    started = time.perf_counter()
    numba_cache = cache_root.parent / (cache_root.name + '-numba')
    numba_cache.mkdir(parents=True, exist_ok=True)
    os.environ['NUMBA_CACHE_DIR'] = str(numba_cache)
    import faulthandler
    faulthandler.dump_traceback_later(90, repeat=True)
    import torch
    import transformers
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    from phonon_loading import load_model
    sys.path.insert(0, str(model_root))
    dtype = torch.bfloat16 if precision == 'bf16' else torch.float32
    stages = []
    def progress(stage):
        stages.append(stage)
        print(stage, file=sys.stderr, flush=True)
    model, processor, receipt = load_model(model_root / 'model.fermion', model_root / 'processor', dtype,
        progress, cache_dir=cache_root)
    ready_ms = round((time.perf_counter() - started) * 1000)
    print(f'ready {ready_ms}ms; checking publisher tensor parity', file=sys.stderr, flush=True)
    # Force every mapped tensor page to be read while verifying the exact values.
    from reference_transformers import container_state_dict
    expected, index = container_state_dict(str(model_root / 'model.fermion')) if verify else ({}, [])
    compared, elements = 0, 0
    if verify:
        actual = model.state_dict()
        assert expected.keys() == actual.keys(), 'state keys differ from publisher decoder'
        for name, value in actual.items():
            reference = expected.pop(name)
            wanted = reference.to(dtype) if reference.is_floating_point() else reference
            assert value.dtype == wanted.dtype and value.shape == wanted.shape, name
            assert torch.equal(value, wanted), name
            compared += 1
            elements += value.numel()
            del wanted, reference
        del actual, expected, index
    import psutil
    result = {'precision': precision, 'readyMs': ready_ms, 'loadReceipt': receipt, 'stages': stages,
              'publisherTensorParity': verify, 'tensorKeysCompared': compared, 'tensorElementsCompared': elements,
              'peakWorkingSetBytes': psutil.Process().memory_info().peak_wset if sys.platform == 'win32' else psutil.Process().memory_info().rss,
              'torchVersion': str(torch.__version__), 'transformersVersion': str(transformers.__version__), 'device': 'cpu'}
    del model, processor
    gc.collect()
    print(json.dumps(result), flush=True)
    faulthandler.cancel_dump_traceback_later()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--child', choices=('bf16', 'fp32'))
    args = parser.parse_args()
    model, cache = args.model.resolve(), args.cache_dir.resolve()
    if cache == model or model in cache.parents:
        raise ValueError('Qualification cache must be outside the installed checkpoint folder')
    if args.child:
        child(model, cache, args.child, verify=True)
        return
    if not args.out:
        parser.error('--out is required')
    output = args.out.resolve()
    if output == model or model in output.parents:
        raise ValueError('Qualification output must be outside the installed checkpoint folder')
    if cache.exists() and any(cache.iterdir()):
        raise ValueError('Choose a new or empty qualification cache directory to measure a real miss')
    from phonon_cache import digest
    watched = [model / 'model.fermion', model / 'fermion_container.py', model / 'reference_transformers.py', model.parent / 'settings.json']
    before = {str(path): digest(path) for path in watched if path.is_file()}
    rows = []
    for precision in ('bf16', 'fp32'):
        for phase in ('miss', 'hit'):
            started = time.perf_counter()
            try:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--model', str(model),
                                         '--cache-dir', str(cache), '--child', precision], capture_output=True, text=True, timeout=240)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f'CPU qualification timed out: {error.stderr!r}') from error
            if result.returncode:
                raise RuntimeError(result.stderr[-12000:] + result.stdout[-4000:])
            row = json.loads(result.stdout.strip().splitlines()[-1])
            assert row['loadReceipt']['denseCache']['hit'] == (phase == 'hit'), f'Not an actual {phase}'
            row.update(phase=phase, processWallMs=round((time.perf_counter() - started) * 1000))
            rows.append(row)
            print(json.dumps({'precision': precision, 'phase': phase, 'readyMs': row['readyMs'],
                              'cacheHit': row['loadReceipt']['denseCache']['hit'], 'tensorKeysCompared': row['tensorKeysCompared']}), flush=True)
    after = {str(path): digest(path) for path in watched if path.is_file()}
    assert before == after, 'Checkpoint, publisher code or saved settings changed during qualification'
    receipt = {'sourceFilesUnchanged': True, 'sourceSha256': before, 'cudaInferencePerformed': False,
               'downloadedDependencies': False, 'results': rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2), encoding='utf-8')


if __name__ == '__main__': main()
