"""Bind an active evaluation endpoint to the exact local checkpoint and runtime."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess

from benchmark_capture import file_hash, request

APP = Path(__file__).resolve().parents[2]
NAMES = {'baseline': 'lfm-baseline-q8', 'dualcore-kv': 'DualCore KV',
         'dualcore-echo': 'DualCore ECHO', 'fusioncore-kv': 'FusionCore KV',
         'fusioncore-echo': 'FusionCore ECHO'}


def artifact(path):
    return {'path': str(path.resolve()), 'bytes': path.stat().st_size, 'sha256': file_hash(path)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=NAMES, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resources', type=Path, default=APP / 'src-tauri/resources',
                        help='Exact resource directory used by the running endpoint')
    args = parser.parse_args()
    resources = args.resources.resolve()
    lfm = resources / 'lfm'
    runtime = resources / 'doucode/runtime'
    if args.output.exists():
        raise FileExistsError(f'Refusing to overwrite model identity: {args.output}')
    spec = json.loads((lfm / 'checkpoint.json').read_text())
    weight = artifact(args.checkpoint)
    if weight['sha256'] != spec['sha256'] or weight['bytes'] != spec['bytes']:
        raise ValueError('Checkpoint differs from the pinned LFM source')
    health = request(args.url.rstrip('/') + '/health')
    props = request(args.url.rstrip('/') + '/props')
    name = NAMES[args.profile]
    if args.profile == 'baseline':
        if props.get('model_alias') != name or Path(props['model_path']).resolve() != args.checkpoint.resolve():
            raise ValueError('Native endpoint is not serving the declared checkpoint and alias')
    elif (props.get('model') != name or props['lfm']['checkpoint']['sha256'] != weight['sha256']):
        raise ValueError('Paired endpoint differs from the declared profile or checkpoint')
    files = [*runtime.glob('*.dll'), *runtime.glob('*.exe'), runtime / 'build-info.json']
    if args.profile != 'baseline':
        files += [lfm / name for name in ('serve_lfm.py', 'dual.py', 'fusion.py', 'protocol.py', 'checkpoint.json')]
        files += [lfm / 'native' / name for name in ('fusioncore.cpp', 'fusioncore.dll', 'build-info.json')]
        files += [resources / 'doucode/duocore' / name
                  for name in ('runtime.py', 'selection.py', 'spec.py')]
    git = subprocess.check_output(['git', '-C', str(APP), 'rev-parse', 'HEAD'], text=True).strip()
    gpu = subprocess.check_output(['nvidia-smi', '--query-gpu=name,driver_version,memory.total,memory.used',
                                  '--format=csv,noheader'], text=True).strip()
    baseline = args.profile == 'baseline'
    dual = args.profile.startswith('dualcore')
    value = {
        'schema': 1, 'evidence_kind': 'real_model', 'model': name, 'profile': args.profile,
        'created': datetime.now(timezone.utc).isoformat(), 'checkpoint': spec,
        'artifacts': [weight], 'runtime_files': [artifact(path) for path in sorted(set(files))],
        'app_git_base': git, 'runtime_source_note': 'File hashes bind the actual selected runtime source; the Git base alone is not an artifact identity.',
        'platform': platform.platform(), 'gpu_at_identity_recording': gpu,
        'runtime_resources_declared': str(resources),
        'health': health, 'properties': props,
        'complete_towers': 1 if baseline else 2, 'candidate_budget': 2 if dual else 1,
        'sampling': {'evaluation_temperature': 0, 'draft_temperature': 0,
                     'review_temperature': 0 if dual else None,
                     'repeat_penalty': 1.08 if args.profile == 'dualcore-kv' else 1.0,
                     'review_order': 'randomized blind opposing orders' if dual else None},
        'coupling_trained': False if args.profile.startswith('fusioncore') else None,
        'request_isolation': 'fresh_conversation_per_sample' if args.profile.endswith('echo') else None,
        'scope': 'Exact shipped Q8 source; profile engineering is experimental. This identity is not a quality result.',
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'model': name, 'checkpoint_sha256': weight['sha256'],
                      'runtime_files': len(value['runtime_files']), 'identity': str(args.output)}))


if __name__ == '__main__':
    main()
