"""Fetch and verify the preserved native/runtime inputs, then apply source overlays."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from package import file_hash

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', type=Path, required=True)
    args = parser.parse_args()
    inputs = args.inputs.resolve()
    inputs.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((HERE / 'baseline.json').read_text(encoding='utf-8'))
    native_tree = subprocess.check_output(['git', 'rev-parse', 'HEAD:src-tauri/src'], cwd=HERE.parent, text=True).strip()
    if native_tree != baseline['historicalNativeSourceTree']:
        raise ValueError('Native source changed without a matching restored native baseline; refusing to publish code that would not be shipped')
    subprocess.run(['gh', 'release', 'download', baseline['tag'], '--repo', baseline['repository'],
                    '--dir', str(inputs), *[item for asset in baseline['assets'].values()
                                          for item in ['--pattern', asset['name']]]], check=True)
    for asset in baseline['assets'].values():
        if file_hash(inputs / asset['name']) != asset['sha256']:
            raise ValueError('The preserved build input failed verification: ' + asset['name'])
    app = inputs / 'app'
    if app.exists():
        raise ValueError('Extraction directory must be new')
    subprocess.run([str(inputs / baseline['assets']['installer']['name']), '/S', '/TEST', '/D=' + str(app)],
                   check=True, timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if file_hash(app / 'OpenCore.exe') != '1b83a4999ea312d53c5e651d73c80cd6719591ffd374f258270c4efa011d6fda':
        raise ValueError('The extracted runtime baseline is not the verified app')
    resource_map = json.loads((HERE / 'resource-map.json').read_text(encoding='utf-8'))
    repository_root = HERE.parent.resolve()
    for installed_path, source_path in resource_map.items():
        source = (repository_root / source_path).resolve(strict=True)
        target = (app / installed_path).resolve()
        if not source.is_relative_to(repository_root) or not target.is_relative_to(app.resolve()):
            raise ValueError('Resource mapping leaves the verified source or installation root')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    environment = dict(os.environ, OPENCORE_NATIVE_BASE=str(inputs / baseline['assets']['native']['name']),
                       OPENCORE_PATCHED_APP=str(app / 'OpenCore.exe'))
    subprocess.run(['node', str(HERE / 'build-ui-repair.cjs')], cwd=HERE, env=environment, check=True)
    subprocess.run(['node', str(HERE / 'patch-executable.cjs')], cwd=HERE, env=environment, check=True)
    print(json.dumps({'prepared': True, 'app': str(app), 'nativeBaseline': baseline['nativeVersion']}))


if __name__ == '__main__':
    main()
