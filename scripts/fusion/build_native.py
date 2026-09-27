"""Build the small TwinCore boundary against existing pinned llama.cpp assets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

PIN = '42adf019f76013dac873b5b43950d54d5ab27216'


def record(path, scope):
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': path.name, 'scope': scope, 'bytes': path.stat().st_size, 'sha256': digest}


def main():
    app = Path(__file__).resolve().parents[2]
    defaults = app.parents[1] / '.opencore-runtime-packages/twincore/llama-k2'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llama-source', type=Path, default=defaults)
    parser.add_argument('--llama-build', type=Path)
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args()
    source = args.llama_source.resolve()
    vendor_build = (args.llama_build or source / 'build-ninja').resolve()
    if shutil.disk_usage(app).free < 200_000_000_000:
        raise RuntimeError('The 200 GB free-space reserve must be retained')
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != PIN:
        raise RuntimeError('llama.cpp source does not match the shipped ABI')
    # Existing experimental server patches do not enter these three libraries.
    # Refuse drift in every header/core directory used by the native boundary.
    subprocess.run(['git', '-C', str(source), 'diff', '--exit-code', 'HEAD', '--',
                    'include', 'src', 'ggml'], check=True)
    other_changes = subprocess.check_output(['git', '-C', str(source), 'diff', '--name-only', 'HEAD'], text=True).splitlines()
    for relative in ('src/llama.lib', 'ggml/src/ggml-base.lib', 'ggml/src/ggml-cpu.lib', 'ggml/src/ggml.lib'):
        if not (vendor_build / relative).is_file():
            raise RuntimeError(f'Missing existing import library: {relative}')
    native = app / 'src-tauri/resources/fusion/native'
    build = native / 'build'
    subprocess.run(['cmake', '-S', str(native), '-B', str(build), '-G', 'Visual Studio 17 2022', '-A', 'x64',
                    f'-DLLAMA_SOURCE={source}', f'-DLLAMA_BUILD={vendor_build}',
                    f'-DTWINCORE_BUILD_PROBE={"ON" if args.probe else "OFF"}'], check=True)
    subprocess.run(['cmake', '--build', str(build), '--config', 'Release', '--parallel', '2'], check=True)
    output, runtime = build / 'Release', app / 'src-tauri/resources/doucode/runtime'
    libraries = [record(output / 'twincore.dll', 'native')]
    shipped = json.loads((runtime / 'build-info.json').read_text(encoding='utf-8'))
    if shipped.get('source_commit') != PIN:
        raise RuntimeError('The shipped runtime does not match the linked ABI')
    for expected in shipped['files']:
        name = expected['path']
        if not name.lower().endswith('.dll'):
            continue
        actual = record(runtime / name, 'runtime')
        if actual['bytes'] != expected['bytes'] or actual['sha256'] != expected['sha256']:
            raise RuntimeError(f'The shipped runtime binary drifted: {name}')
        libraries.append(actual)
    sources = [record(native / name, 'source') for name in ('twincore.cpp', 'head_projection.h', 'CMakeLists.txt')]
    imports = [record(vendor_build / name, 'import') for name in
               ('src/llama.lib', 'ggml/src/ggml-base.lib', 'ggml/src/ggml-cpu.lib', 'ggml/src/ggml.lib')]
    manifest = {'schema': 1, 'abi': 1, 'source_commit': PIN, 'libraries': libraries, 'sources': sources,
                'imports': imports, 'unlinked_server_changes': other_changes,
                'gpu_qualified': False, 'training_qualified': False,
                'scope': 'Compiled boundary; CPU tests are not full-model qualification'}
    (output / 'build-info.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'dll': str(output / 'twincore.dll'), 'sha256': libraries[0]['sha256'],
                      'probe': args.probe, 'free_bytes': shutil.disk_usage(app).free}))


if __name__ == '__main__':
    main()
