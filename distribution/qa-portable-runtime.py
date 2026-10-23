"""Exercise helpers using only the interpreter and engine from the installer."""
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve(strict=True)
if Path(sys.executable).resolve() != (root / 'test-python/python.exe').resolve():
    raise RuntimeError('This check must run with the extracted clean interpreter')
sys.path.insert(0, str(root / 'runtime-setup'))
runpy.run_path(str(root / 'runtime-setup/setup_manager.py'), run_name='opencore_release_probe')
sys.path.insert(0, str(root / 'runtime-setup/hf-extension'))
import hf_bridge, hf_catalog, hf_download, hf_runtime, hf_security

environment = dict(os.environ, PATH=str(root / 'doucode/runtime') + os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32'))
engine = subprocess.run([str(root / 'opencore/runtime/llama-server.exe'), '--version'],
                        env=environment, capture_output=True, text=True, timeout=30, check=True)
node = subprocess.run([str(root / 'claude/node.exe'), '--version'],
                      env=environment, capture_output=True, text=True, timeout=30, check=True)
with tempfile.TemporaryDirectory(prefix='opencore-release-python-') as temporary:
    temporary_root = Path(temporary).resolve()
    venv = temporary_root / 'managed-environment'
    assert venv.resolve().is_relative_to(temporary_root)
    subprocess.run([sys.executable, '-I', '-B', '-m', 'venv', '--without-pip', str(venv)],
                   capture_output=True, text=True, timeout=60, check=True)
    python = venv / 'Scripts/python.exe'
    subprocess.run([str(python), '-I', '-B', '-m', 'ensurepip', '--upgrade', '--default-pip'],
                   capture_output=True, text=True, timeout=90, check=True)
    pip = subprocess.run([str(python), '-I', '-B', '-m', 'pip', '--version'],
                         capture_output=True, text=True, timeout=30, check=True)
result = {'passed': True, 'helperImports': True, 'bundledPython': sys.version.split()[0],
          'freshVenvBootstrap': True, 'pipVersion': ' '.join(pip.stdout.split()[:2]),
          'bundledNode': node.stdout.strip(), 'engineStartup': True,
          'engineVersion': (engine.stdout + engine.stderr).strip()[:1200]}
args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, indent=2))
