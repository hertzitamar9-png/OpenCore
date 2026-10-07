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
            'import ctypes,json,sys,site,struct,torch; from pathlib import Path; '
            'assert (3,10)<=sys.version_info[:2]<=(3,12) and struct.calcsize("P")==8; '
            'print(json.dumps({"version":torch.__version__,"cuda":torch.version.cuda,'
            '"sites":list(dict.fromkeys([*site.getsitepackages(),str(Path(torch.__file__).resolve().parent.parent)])),"base":sys._base_executable}))'],capture_output=True,text=True,timeout=30)
        if not result.returncode:
            info = json.loads(result.stdout.strip().splitlines()[-1])
            major, minor = map(int,info['version'].split('+')[0].split('.')[:2])
            if (major,minor) >= (2,5) and info['cuda']:
                return info
    except (OSError,ValueError,subprocess.TimeoutExpired):
        pass
    return None


def python_candidates(root):
    """Find usable existing CUDA libraries without modifying those environments."""
    root = Path(root)
    candidates = [os.environ.get('OPENCORE_SPEECH_TORCH_PYTHON'), sys.executable,
                  shutil.which('python'), shutil.which('python3')]
    for variable in ('VIRTUAL_ENV', 'CONDA_PREFIX'):
        if os.environ.get(variable):
            candidates.extend([str(Path(os.environ[variable]) / 'Scripts/python.exe'), str(Path(os.environ[variable]) / 'python.exe')])
    directories = [root, root.parent / 'training-envs', root.parent / 'runtime-setup/environments']
    local = os.environ.get('LOCALAPPDATA')
    if local:
        directories.extend([Path(local) / 'OpenCore/training-envs', Path(local) / 'Programs/Python'])
    for directory in directories:
        if directory.is_dir():
            for child in sorted(directory.iterdir())[:64]:
                if child.is_dir():
                    candidates.extend([str(child / 'Scripts/python.exe'), str(child / 'python.exe'), str(child / 'bin/python')])
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def prepare(root, name, packages):
    target = root / name / 'Scripts/python.exe'
    candidates = python_candidates(root)
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
        if shutil.disk_usage(root).free < 6_000_000_000 + 64 * 1024 * 1024:
            raise RuntimeError('Not enough disk space: CUDA speech runtime installation needs about 6 GB of temporary and installed package space.')
        subprocess.run([str(target),'-m','pip','install','--disable-pip-version-check','--no-cache-dir',
            '--index-url','https://download.pytorch.org/whl/cu124','torch==2.5.1'],check=True)
    subprocess.run([str(target),'-m','pip','install','--disable-pip-version-check','--no-cache-dir',
        *[f'{package}=={version}' for package,version in packages.items()]],check=True)
    return target, shared
