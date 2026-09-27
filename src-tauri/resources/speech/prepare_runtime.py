"""Prepare the optional speech environment; never download model weights here."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

PACKAGES = {'faster-whisper': '1.2.1', 'ctranslate2': '4.8.2',
            'nvidia-cublas-cu12': '12.9.2.10', 'nvidia-cudnn-cu12': '9.26.0.51'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    python = root / 'venv/Scripts/python.exe'
    if args.check:
        for package, version in PACKAGES.items():
            if importlib.metadata.version(package) != version:
                raise RuntimeError(f'{package} requires version {version}')
        import faster_whisper
        import ctranslate2
        (root / 'runtime.json').write_text(json.dumps(PACKAGES), encoding='utf-8')
        return
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 202_000_000_000:
        raise RuntimeError('Speech setup must preserve 200 GB of free disk space.')
    if not python.is_file():
        subprocess.run([sys.executable, '-m', 'venv', str(root / 'venv')], check=True)
    check = [str(python), str(Path(__file__).resolve()), '--root', str(root), '--check']
    result = subprocess.run(check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '--no-cache-dir', *[f'{name}=={version}' for name, version in PACKAGES.items()]], check=True)
        subprocess.run(check, check=True)
    print('Speech engine is ready.', flush=True)


if __name__ == '__main__':
    main()
