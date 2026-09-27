"""Run full-Q6 qualification, then bridge training; never launch benchmarks or the app."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / 'src-tauri/resources'))

from fusion.q6_identity import CHECKPOINTS, file_digest
from fusion.q6_preflight import preflight
from fusion.qualification import execution_configuration, validate_qualification

DISK_RESERVE = 200_000_000_000
REPORT_MARGIN = 1_048_576


def _commands(args):
    common = ['--nanbeige', str(args.nanbeige), '--k2', str(args.k2),
        '--context', str(args.context), '--rank', str(args.rank), '--seed', str(args.seed),
        '--dll', str(args.dll), '--runtime', str(args.runtime)]
    if args.recompute:
        common.append('--recompute')
    qualification = [sys.executable, str(APP / 'scripts/fusion/qualify_native.py'),
        *common, '--output', str(args.output / 'qualification.json')]
    training = [sys.executable, str(APP / 'scripts/fusion/train_native.py'), *common,
        '--qualification', str(args.output / 'qualification.json'),
        '--corpus', str(args.corpus), '--corpus-sha256', args.corpus_sha256,
        '--output', str(args.output / 'adapter'), '--epochs', str(args.epochs),
        '--max-target-tokens', str(args.max_target_tokens), '--lr', str(args.lr),
        '--checkpoint-every', str(args.checkpoint_every)]
    if args.resume is not None:
        training += ['--resume', str(args.resume)]
    return qualification, training


def run_child(command, log_path):
    """Stream child output while preserving it, and stop only this owned child."""
    env = {**os.environ, 'OMP_NUM_THREADS': '2', 'OPENBLAS_NUM_THREADS': '2',
        'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}
    with log_path.open('x', encoding='utf-8') as log:
        child = subprocess.Popen(command, cwd=APP, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace', env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for line in child.stdout:
                if shutil.disk_usage(log_path.parent).free < DISK_RESERVE + REPORT_MARGIN + len(line.encode('utf-8')):
                    raise ValueError('Training log would violate the 200 GB reserve')
                log.write(line)
                log.flush()
                print(line, end='', flush=True)
            return child.wait()
        except BaseException:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
            raise
        finally:
            child.stdout.close()


def inspect_completed_adapter(path, qualification, args):
    """Validate small trained tensors on CPU after the training child has exited."""
    from fusion.adapter import inspect_adapter
    _, receipt = inspect_adapter(path, qualification['binding'])
    training = receipt['training']
    configuration = execution_configuration(context=args.context, rank=args.rank,
        seed=args.seed, recompute=args.recompute)
    if ({key: training.get('configuration', {}).get(key) for key in configuration} != configuration
            or training.get('corpus_sha256') != args.corpus_sha256
            or training.get('qualification_sha256') != file_digest(args.output / 'qualification.json')
            or training.get('driver_sha256') != file_digest(APP / 'scripts/fusion/train_native.py')
            or training.get('configuration', {}).get('max_target_tokens') != args.max_target_tokens):
        raise ValueError('Completed adapter training identity changed')
    schedule = training.get('schedule', {})
    if (schedule.get('epochs') != args.epochs or schedule.get('epoch') != args.epochs
            or schedule.get('seed') != args.seed or schedule.get('next_index') != 0
            or schedule.get('order') != []):
        raise ValueError('Completed adapter did not finish the requested training schedule')
    return receipt


def execute(args, *, run_stage=run_child, check_resources=preflight,
            inspect_trained=inspect_completed_adapter):
    configuration = execution_configuration(context=args.context, rank=args.rank,
        seed=args.seed, recompute=args.recompute)
    if (args.epochs < 1 or args.max_target_tokens < 1 or args.checkpoint_every < 1
            or not math.isfinite(args.lr) or args.lr <= 0):
        raise ValueError('Training budgets and learning rate must be positive and finite')
    for name in ('nanbeige', 'k2', 'dll', 'corpus', 'runtime', 'output'):
        setattr(args, name, Path(getattr(args, name)).resolve())
    if args.resume is not None:
        args.resume = Path(args.resume).resolve()
        if not args.resume.exists():
            raise ValueError('The requested resume state does not exist')
    if args.output.exists() or not args.output.parent.is_dir():
        raise ValueError('Use a fresh sequence directory under an existing parent')
    if (any(not getattr(args, name).is_file() for name in ('nanbeige', 'k2', 'dll', 'corpus'))
            or not args.runtime.is_dir()):
        raise ValueError('Use existing complete checkpoint, DLL, runtime and corpus paths')
    if (not re.fullmatch('[0-9a-f]{64}', args.corpus_sha256)
            or file_digest(args.corpus) != args.corpus_sha256):
        raise ValueError('Training corpus content identity changed')
    if shutil.disk_usage(args.output.parent).free < DISK_RESERVE + REPORT_MARGIN:
        raise ValueError('Sequence metadata would violate the 200 GB reserve')
    drivers = {str(path): file_digest(path) for path in
        (Path(__file__), APP / 'scripts/fusion/qualify_native.py',
         APP / 'scripts/fusion/train_native.py')}
    commands = _commands(args)
    report = {'schema': 1, 'status': 'prepared', 'stage': 'prepared',
        'time_utc': datetime.now(timezone.utc).isoformat(), 'configuration': configuration,
        'corpus': {'path': str(args.corpus), 'sha256': args.corpus_sha256},
        'checkpoints': CHECKPOINTS, 'driver_sha256': drivers,
        'commands': {'qualification': commands[0], 'training': commands[1]},
        'resume_store': str(args.output / 'adapter.checkpoints'),
        'gpu_qualified': False, 'training_completed': False,
        'model_quality_measured': False, 'app_activated': False,
        'scope': 'Sequential full-Q6 qualification and bridge training only. Benchmark and activation gates remain separate.'}
    args.output.mkdir(exist_ok=False)

    def save(stage, **changes):
        if shutil.disk_usage(args.output).free < DISK_RESERVE + REPORT_MARGIN:
            raise ValueError('Sequence report would violate the 200 GB reserve')
        report.update(stage=stage, **changes)
        temporary = args.output / 'sequence.json.tmp'
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        temporary.replace(args.output / 'sequence.json')
        print(json.dumps({'stage': stage, 'status': report['status']}), flush=True)

    def check_drivers():
        if any(file_digest(Path(path)) != digest for path, digest in drivers.items()):
            raise ValueError('Training sequence driver source changed during execution')

    save('prepared')
    if args.prepare_only:
        return report
    stage = 'qualification_resource_check'
    try:
        save(stage, status='running')
        plan = check_resources(args.context, args.output)
        check_drivers()
        stage = 'qualification'
        save(stage, resources_before_qualification=plan)
        code = run_stage(commands[0], args.output / 'qualification.log')
        if code != 0:
            raise RuntimeError(f'Qualification exited with exit code {code}')
        stage = 'qualification_validation'
        proof_path = args.output / 'qualification.json'
        proof = json.loads(proof_path.read_text(encoding='utf-8'))
        validate_qualification(proof, configuration, plan['gpu']['uuid'])
        if (proof.get('binding', {}).get('checkpoints') != CHECKPOINTS
                or proof.get('driver_sha256') != drivers[str(APP / 'scripts/fusion/qualify_native.py')]):
            raise ValueError('Qualification driver or full checkpoint identities changed')
        save(stage, gpu_qualified=True, qualification_sha256=file_digest(proof_path))
        stage = 'training_resource_check'
        save(stage)
        current = check_resources(args.context, args.output)
        validate_qualification(proof, configuration, current['gpu']['uuid'])
        check_drivers()
        stage = 'training'
        save(stage, resources_before_training=current)
        code = run_stage(commands[1], args.output / 'training.log')
        if code != 0:
            raise RuntimeError(f'Training exited with exit code {code}')
        stage = 'adapter_validation'
        save(stage)
        check_drivers()
        receipt = inspect_trained(args.output / 'adapter', proof, args)
        training = receipt['training']
        save('complete', status='adapter_validated_unbenchmarked', training_completed=True,
            training={'steps': training['steps'], 'tokens': training['tokens'],
                'held_out_loss': training['validation']['loss']})
        return report
    except BaseException as error:
        save(stage, status='interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
             error=f'{type(error).__name__}: {error}')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nanbeige', type=Path, required=True)
    parser.add_argument('--k2', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--corpus-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--context', type=int, default=1024)
    parser.add_argument('--rank', type=int, default=256)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--recompute', action='store_true')
    parser.add_argument('--epochs', type=int, default=1)
    parser.add_argument('--max-target-tokens', type=int, default=512)
    parser.add_argument('--checkpoint-every', type=int, default=8)
    parser.add_argument('--lr', type=float, default=0.0002)
    parser.add_argument('--dll', type=Path, default=APP / 'src-tauri/resources/fusion/native/build/Release/twincore.dll')
    parser.add_argument('--runtime', type=Path, default=APP / 'src-tauri/resources/doucode/runtime')
    parser.add_argument('--prepare-only', action='store_true',
                        help='Write commands and source/corpus identities without checking the GPU or starting a child')
    execute(parser.parse_args())


if __name__ == '__main__':
    main()
