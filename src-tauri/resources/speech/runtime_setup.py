"""Create isolated ASR packages without changing or duplicating usable CUDA libraries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def probe(python):
    try:
        result = subprocess.run([str(python), '-c',
            'import json,sys,site,torch; print(json.dumps({"version":torch.__version__,"cuda":torch.version.cuda,'
            '"sites":site.getsitepackages(),"base":sys._base_executable}))'],capture_output=True,text=True,timeout=30)
        if not result.returncode:
            info = json.loads(result.stdout.strip().splitlines()[-1])
            major, minor = map(int,info['version'].split('+')[0].split('.')[:2])
            if (major,minor) >= (2,5) and info['cuda']:
                return info
    except (OSError,ValueError,subprocess.TimeoutExpired):
        pass
    return None


def prepare(root, name, packages):
    target = root / name / 'Scripts/python.exe'
    candidates = [os.environ.get('OPENCORE_SPEECH_TORCH_PYTHON'),
        str(Path(os.environ.get('LOCALAPPDATA','')) / 'OpenCore/training-envs/lfm-bf16-py311/Scripts/python.exe'),
        str(root / 'venv/Scripts/python.exe'),sys.executable]
    shared = next((info for candidate in candidates if candidate and (info:=probe(candidate))),None)
    if not target.is_file():
        base = shared['base'] if shared else sys.executable
        subprocess.run([base,'-c','import ctypes,tempfile; d=tempfile.TemporaryDirectory(); d.cleanup()'],check=True)
        subprocess.run([base,'-m','venv',str(root/name)],check=True)
    if shared:
        sites = json.loads(subprocess.check_output([str(target),'-c','import json,site;print(json.dumps(site.getsitepackages()))'],text=True))
        own = next(Path(p) for p in sites if p.endswith('site-packages'))
        (own/'opencore_torch_shared.pth').write_text('\n'.join(shared['sites'])+'\n',encoding='utf-8')
    elif not probe(target):
        if shutil.disk_usage(root).free < 106_000_000_000:
            raise RuntimeError('A CUDA speech runtime needs more disk space while preserving 100 GB free. No existing model was deleted.')
        subprocess.run([str(target),'-m','pip','install','--disable-pip-version-check','--no-cache-dir',
            '--index-url','https://download.pytorch.org/whl/cu124','torch==2.5.1'],check=True)
    subprocess.run([str(target),'-m','pip','install','--disable-pip-version-check','--no-cache-dir',
        *[f'{package}=={version}' for package,version in packages.items()]],check=True)
    return target, shared
