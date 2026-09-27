"""Bind a qualified active TwinCore endpoint before full benchmark capture.

This records model identity, not coding quality. It imports no tensor framework
and starts no model. The server separately verifies the trained tensor contents.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.parse

from benchmark_capture import file_hash, request


APP = Path(__file__).resolve().parents[2]
FUSION = APP / 'src-tauri/resources/fusion'
sys.path.insert(0, str(FUSION.parent))
from fusion.q6_identity import CHECKPOINTS, SOURCE_COMMIT, canonical
from fusion.qualification import execution_configuration, validate_qualification
from fusion.native_library import CPU_LIBRARIES, CUDA_LIBRARIES, validate_loaded_libraries


NAMES = {'twincore-kv': 'opencore-twincore-q6-kv', 'twincore-echo': 'opencore-twincore-q6-echo'}
REQUIRED_LIBRARIES = (*CPU_LIBRARIES, *CUDA_LIBRARIES)
COUPLING_SOURCES = ('bridge.py', 'alignment.py', 'native.py', 'native_library.py',
                    'q6_identity.py', 'canonical.py', 'chat_template.py', 'training.py',
                    'adapter.py', 'qualification.py', 'checkpoints.py')


def artifact(path):
    path = Path(path).resolve()
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': file_hash(path)}


def _completed_receipt(adapter, binding):
    receipt = json.loads((adapter / 'receipt.json').read_text(encoding='utf-8'))
    declared = receipt.pop('receipt_sha256', None)
    if receipt.get('schema') != 1 or hashlib.sha256(canonical(receipt)).hexdigest() != declared:
        raise ValueError('Trained adapter receipt integrity mismatch')
    receipt['receipt_sha256'] = declared
    if receipt.get('checkpoint') is not False:
        raise ValueError('Benchmark requires a completed adapter, not a resume-only checkpoint')
    if canonical(receipt.get('binding')) != canonical(binding):
        raise ValueError('Trained adapter and qualification identity differ')
    training = receipt.get('training', {})
    if (any(type(training.get(name)) is not int or training[name] < 1 for name in ('steps', 'tokens'))
            or training.get('initial_bridge_sha256') == receipt.get('tensor_fingerprint')
            or not isinstance(receipt.get('tensor_fingerprint'), str)
            or len(receipt['tensor_fingerprint']) != 64
            or not isinstance(training.get('initial_bridge_sha256'), str)
            or len(training['initial_bridge_sha256']) != 64):
        raise ValueError('Benchmark requires trained adapter evidence')
    validation = training.get('validation', {})
    if (training.get('validation_is_current') is not True
            or training.get('validation_step') != training['steps']
            or type(validation.get('tokens')) is not int or validation['tokens'] < 1
            or any(type(validation.get(key)) not in (int, float) or not math.isfinite(validation[key])
                   or validation[key] < 0 for key in ('loss', 'baseline_loss'))):
        raise ValueError('Trained adapter requires current held-out validation')
    files = receipt.get('files', {})
    if ('bridge.safetensors' not in files
            or not set(files).issubset({'bridge.safetensors', 'optimizer.safetensors'})):
        raise ValueError('Trained adapter tensor identity is missing')
    rows = [artifact(adapter / name) for name in sorted(files)]
    if any(row['sha256'] != files[Path(row['path']).name] for row in rows):
        raise ValueError('Trained adapter tensor integrity mismatch')
    return receipt, rows


def _native_files(dll, runtime, binding, fusion_root):
    manifest_path = dll.with_name('build-info.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (manifest.get('source_commit') != SOURCE_COMMIT or manifest.get('abi') != 1
            or canonical(manifest) != canonical(binding.get('native'))):
        raise ValueError('Native library build identity changed')
    records = manifest.get('libraries', [])
    keys = {(row.get('scope'), row.get('path')) for row in records}
    required = {('native', 'twincore.dll')} | {('runtime', name) for name in REQUIRED_LIBRARIES if name != 'twincore.dll'}
    if len(keys) != len(records) or not required.issubset(keys):
        raise ValueError('Native library identities are missing or duplicated')
    rows = [artifact(manifest_path)]
    for row in records:
        if row.get('scope') not in ('native', 'runtime') or Path(row['path']).name != row['path']:
            raise ValueError('Invalid native library path or scope')
        base = dll.parent if row['scope'] == 'native' else runtime
        actual = artifact(base / row['path'])
        if any(actual[key] != row[key] for key in ('bytes', 'sha256')):
            raise ValueError('Native library content differs from its pinned build')
        rows.append(actual)
    sources = manifest.get('sources', [])
    names = {row.get('path') for row in sources}
    if len(names) != len(sources) or not {'twincore.cpp', 'head_projection.h', 'CMakeLists.txt'}.issubset(names):
        raise ValueError('Compiled native source identities are missing or duplicated')
    for row in sources:
        if row.get('scope') != 'source' or Path(row['path']).name != row['path']:
            raise ValueError('Invalid compiled native source identity')
        actual = artifact(fusion_root / 'native' / row['path'])
        if any(actual[key] != row[key] for key in ('bytes', 'sha256')):
            raise ValueError('Native source differs from the compiled build receipt')
        rows.append(actual)
    return rows, records


def build_identity(args, *, fetch=request, fusion_root=FUSION):
    parts = urllib.parse.urlsplit(args.url)
    if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ('', '/')):
        raise ValueError('Identity recording requires a loopback HTTP endpoint')
    model = NAMES[args.profile]
    configuration = execution_configuration(context=args.context, rank=args.rank, seed=args.seed,
                                             recompute=args.profile == 'twincore-echo')
    report = json.loads(args.qualification.read_text(encoding='utf-8'))
    health = fetch(args.url.rstrip('/') + '/health')
    props = fetch(args.url.rstrip('/') + '/props')
    if health.get('ready') is not True or health.get('status') != 'ok' or health.get('model') != model:
        raise ValueError('The requested TwinCore endpoint is not ready')
    if (props.get('model') != model or props.get('n_ctx') != args.context
            or canonical(props.get('configuration')) != canonical(configuration)):
        raise ValueError('TwinCore endpoint execution configuration differs')
    active = props.get('runtime_identity') or {}
    binding = report.get('binding') or {}
    validate_qualification(report, configuration, active.get('gpu_uuid'), binding=active.get('binding'))
    if active.get('precision') != 'Q6_K' or canonical(active.get('binding')) != canonical(binding):
        raise ValueError('TwinCore endpoint model/runtime identity differs')
    if active.get('qualification_sha256') != file_hash(args.qualification):
        raise ValueError('TwinCore endpoint qualification identity differs')
    placement = active.get('placement', [])
    if (len(placement) != 2 or any(row.get('head_on_gpu') is not True
            or type(row.get('physical_matrix_layers')) is not int or row['physical_matrix_layers'] < 1
            or row.get('gpu_matrix_layers') != row['physical_matrix_layers'] for row in placement)):
        raise ValueError('TwinCore current GPU placement is incomplete')
    if binding.get('checkpoints') != CHECKPOINTS:
        raise ValueError('TwinCore checkpoint identities changed')
    sources = binding.get('coupling_sources') or {}
    if set(sources) != set(COUPLING_SOURCES) or any(file_hash(fusion_root / name) != sources[name] for name in sources):
        raise ValueError('TwinCore coupling source identity changed')
    receipt, adapter_files = _completed_receipt(args.adapter, binding)
    if active.get('adapter_receipt_sha256') != receipt['receipt_sha256']:
        raise ValueError('The running TwinCore adapter differs from the requested artifact')
    weights = []
    for key in ('nanbeige', 'k2'):
        row = artifact(getattr(args, key))
        if any(row[name] != CHECKPOINTS[key][name] for name in ('bytes', 'sha256')):
            raise ValueError('TwinCore checkpoint content differs from the pinned complete model')
        weights.append(row)
    native_files, libraries = _native_files(args.dll, args.runtime, binding, fusion_root)
    loaded = active.get('loaded_libraries') or {}
    try:
        measured = validate_loaded_libraries(libraries, {name: row['path'] for name, row in loaded.items()},
                                            required=REQUIRED_LIBRARIES)
    except RuntimeError as error:
        raise ValueError('TwinCore loaded numerical library identity differs') from error
    if canonical(measured) != canonical(loaded):
        raise ValueError('TwinCore loaded numerical library content differs')
    source_paths = [path for path in fusion_root.rglob('*') if path.is_file()
                    and not {'build', 'runtime', '__pycache__'}.intersection(path.relative_to(fusion_root).parts)
                    and path.suffix in ('.py', '.cpp', '.h', '.hpp', '.txt', '.json')]
    runtime_files = {row['path']: row for row in [*native_files, *measured.values()]}
    for path in [*source_paths, Path(__file__), Path(__file__).with_name('benchmark_capture.py')]:
        row = artifact(path)
        runtime_files[row['path']] = row
    value = {'schema': 1, 'evidence_kind': 'real_model', 'model': model, 'profile': args.profile,
        'created': datetime.now(timezone.utc).isoformat(), 'checkpoints': CHECKPOINTS,
        'artifacts': [*weights, *adapter_files, artifact(args.adapter / 'receipt.json'), artifact(args.qualification)],
        'runtime_files': [runtime_files[key] for key in sorted(runtime_files)],
        'health': health, 'properties': props, 'complete_towers': 2, 'candidate_budget': 1,
        'coupling_trained': True, 'sampling': {'evaluation_temperature': 0},
        'scope': 'Complete Q6 towers with a trained frozen-decoder coupling bridge. Identity and held-out loss are not benchmark scores.'}
    if args.profile == 'twincore-echo':
        value['request_isolation'] = 'fresh_conversation_per_sample'
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=NAMES, required=True)
    for name in ('nanbeige', 'k2', 'adapter', 'qualification', 'dll', 'runtime', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--context', type=int, default=8192)
    parser.add_argument('--rank', type=int, default=256)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--resources', type=Path, default=FUSION.parent,
                        help='Resources actually used by the loaded endpoint')
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        raise ValueError('Use a fresh identity path under an existing output parent')
    value = build_identity(args, fusion_root=args.resources / 'fusion')
    value.update(app_git_base=subprocess.check_output(['git', '-C', str(APP), 'rev-parse', 'HEAD'], text=True).strip(),
                 platform=platform.platform(),
                 runtime_source_note='Actual files and active endpoint identities are bound; Git HEAD alone is not model identity.')
    data = json.dumps(value, indent=2, ensure_ascii=False).encode('utf-8') + b'\n'
    if shutil.disk_usage(args.output.parent).free < 200_000_000_000 + len(data):
        raise ValueError('Model identity would violate the 200 GB free-space reserve')
    with args.output.open('xb') as output:
        output.write(data)
    print(json.dumps({'model': value['model'], 'runtime_files': len(value['runtime_files']), 'identity': str(args.output)}))


if __name__ == '__main__':
    main()
