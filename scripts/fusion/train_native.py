"""Train only the native TwinCore bridge after a bound full-Q6 resource probe."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / 'src-tauri/resources'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nanbeige', type=Path, required=True)
    parser.add_argument('--k2', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--corpus-sha256', required=True)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--context', type=int, default=1024)
    parser.add_argument('--rank', type=int, default=256)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--epochs', type=int, default=1)
    parser.add_argument('--max-target-tokens', type=int, default=512)
    parser.add_argument('--checkpoint-every', type=int, default=8, help='Save resume-only state every N completed examples; keep two states')
    parser.add_argument('--lr', type=float, default=0.0002)
    parser.add_argument('--recompute', action='store_true')
    parser.add_argument('--dll', type=Path, default=APP / 'src-tauri/resources/fusion/native/build/Release/twincore.dll')
    parser.add_argument('--runtime', type=Path, default=APP / 'src-tauri/resources/doucode/runtime')
    args = parser.parse_args()
    if args.epochs < 1 or args.max_target_tokens < 1 or args.lr <= 0 or args.checkpoint_every < 1:
        raise ValueError('Training budgets and learning rate must be positive')
    if args.output.exists() or not args.output.parent.is_dir():
        raise ValueError('Use a fresh adapter directory under an existing output parent')
    from fusion.qualification import execution_configuration, validate_qualification
    from fusion.q6_preflight import preflight
    qualification = json.loads(args.qualification.read_text(encoding='utf-8'))
    configuration = execution_configuration(context=args.context, rank=args.rank, seed=args.seed, recompute=args.recompute)
    plan = preflight(args.context, args.output.parent)
    validate_qualification(qualification, configuration, plan['gpu']['uuid'])
    from fusion.adapter import file_digest, load_adapter, save_adapter, tensor_fingerprint
    from fusion.checkpoints import CheckpointStore, TrainingSchedule, resolve_resume
    from fusion.q6_pair import open_pair
    from fusion.training import GRADIENT_MODE, evaluate, inspect_corpus, read_corpus, teacher_forced
    import torch
    corpus = read_corpus(args.corpus, expected_sha256=args.corpus_sha256)
    checkpoints = CheckpointStore(args.output)
    with open_pair(args.nanbeige, args.k2, args.dll, args.runtime, context=args.context,
                   rank=args.rank, seed=args.seed, recompute=args.recompute) as pair:
        validate_qualification(qualification, pair.configuration, pair.resource_plan['gpu']['uuid'], binding=pair.binding)
        inspection = inspect_corpus(pair.native, corpus, max_tokens=args.max_target_tokens)
        print(json.dumps({'phase': 'corpus_capacity_checked', 'train_samples': inspection['train_samples'],
            'validation_samples': inspection['validation_samples'],
            'maximum_target_tokens': inspection['maximum_target_tokens'],
            'maximum_native_prefix_tokens': inspection['maximum_native_prefix_tokens']}), flush=True)
        optimizer = torch.optim.AdamW(pair.bridge.bridge_parameters(), lr=args.lr)
        initial = tensor_fingerprint(pair.bridge)
        steps, tokens = 0, 0
        receipt = None
        if args.resume:
            receipt = load_adapter(resolve_resume(args.resume), pair.bridge, pair.binding, optimizer=optimizer)
            if receipt['training']['corpus_sha256'] != corpus.sha256:
                raise ValueError('Optimizer resume corpus identity changed')
            initial = receipt['training']['initial_bridge_sha256']
            steps, tokens = receipt['training']['steps'], receipt['training']['tokens']
        resumed_checkpoint = receipt is not None and receipt.get('checkpoint') is True
        if resumed_checkpoint:
            previous = receipt['training']
            if previous.get('configuration', {}).get('max_target_tokens') != args.max_target_tokens:
                raise ValueError('Checkpoint target token budget changed')
            schedule_state = previous.get('schedule')
            if schedule_state is None:
                raise ValueError('Checkpoint training schedule is missing')
            baseline, initial_validation = previous['baseline_validation'], previous['initial_bridge_validation']
            initial_validation_step = previous['initial_validation_step']
        else:
            schedule_state = None
            baseline = evaluate(pair.native, None, corpus.validation, max_tokens=args.max_target_tokens)
            initial_validation = evaluate(pair.native, pair.bridge, corpus.validation, max_tokens=args.max_target_tokens)
            initial_validation_step = steps
        start = time.monotonic()
        schedule = TrainingSchedule([row['id'] for row in corpus.train], epochs=args.epochs,
                                    seed=args.seed, state=schedule_state)
        examples = {row['id']: row for row in corpus.train}
        truncated_examples = receipt['training'].get('truncated_training_examples', 0) if resumed_checkpoint else 0

        def evidence_for(validation, validation_step):
            return {'steps': steps, 'tokens': tokens, 'initial_bridge_sha256': initial,
                'corpus_sha256': corpus.sha256, 'gradient_mode': GRADIENT_MODE,
                'validation': {**validation, 'baseline_loss': baseline['loss']},
                'validation_step': validation_step, 'validation_is_current': validation_step == steps,
                'baseline_validation': baseline, 'initial_bridge_validation': initial_validation,
                'initial_validation_step': initial_validation_step, 'schedule': schedule.snapshot(),
                'driver_sha256': file_digest(__file__), 'qualification_sha256': file_digest(args.qualification),
                'time_utc': datetime.now(timezone.utc).isoformat(), 'elapsed_seconds': time.monotonic() - start,
                'truncated_training_examples': truncated_examples, 'corpus_inspection': inspection,
                'configuration': {**pair.configuration, 'epochs_this_run': args.epochs,
                    'max_target_tokens': args.max_target_tokens, 'checkpoint_every': args.checkpoint_every,
                    'optimizer': 'AdamW', 'lr_requested': args.lr, 'lr_effective': optimizer.param_groups[0]['lr'],
                    'torch': torch.__version__, 'torch_cuda_build': torch.version.cuda},
                'scope': 'Frozen-Q6 training state. Checkpoints are resume-only; final held-out loss and coding benchmarks are separate gates.'}

        while schedule.current() is not None:
            epoch, identifier = schedule.current()
            example = examples[identifier]
            optimizer.zero_grad(set_to_none=True)
            report = teacher_forced(pair.native, pair.bridge, example, max_tokens=args.max_target_tokens)
            if not report.complete:
                raise ValueError('Incomplete training target; refusing optimizer update')
            report.loss.backward()
            gradients = list(pair.bridge.bridge_parameters())
            if any(parameter.grad is None or not torch.isfinite(parameter.grad).all() for parameter in gradients):
                raise ValueError('Training gradients are missing or nonfinite')
            torch.nn.utils.clip_grad_norm_(gradients, 1.0, error_if_nonfinite=True)
            optimizer.step()
            steps += 1
            tokens += report.tokens
            schedule.advance(identifier)
            print(json.dumps({'phase': 'training', 'epoch': epoch + 1, 'id': identifier,
                'steps': steps, 'tokens': tokens, 'loss': float(report.loss.detach()),
                'complete_target': True, 'seconds': time.monotonic() - start}), flush=True)
            if (steps % args.checkpoint_every == 0
                    or schedule.epoch == args.epochs - 1 and schedule.index == len(schedule.order)):
                saved = checkpoints.save(pair.bridge, pair.binding,
                                         evidence_for(initial_validation, initial_validation_step), optimizer)
                print(json.dumps({'phase': 'checkpoint_saved', 'directory': str(saved), 'steps': steps,
                                  'resume_only': True}), flush=True)
        measured = evaluate(pair.native, pair.bridge, corpus.validation, max_tokens=args.max_target_tokens)
        evidence = evidence_for(measured, steps)
        save_adapter(args.output, pair.bridge, pair.binding, evidence, optimizer=optimizer)
        print(json.dumps({'phase': 'adapter_saved', 'directory': str(args.output),
                          'steps': steps, 'tokens': tokens, 'validation_loss': measured['loss']}), flush=True)


if __name__ == '__main__':
    main()
