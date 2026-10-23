"""Create isolated ASR packages without changing or duplicating usable CUDA libraries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

RUNTIME_SCHEMA = 3
EXECUTION_CONTRACT = 'cuda-kernel-v1'


def verify(python, packages, imports):
    """Verify versions/imports and a small CUDA kernel before claiming readiness."""
    code = '''import importlib,importlib.metadata,json,sys
packages,imports=json.loads(sys.argv[1])
versions={}
for name,want in packages.items():
    actual=importlib.metadata.version(name)
    if actual.split('+')[0]!=want: raise RuntimeError(f'{name} version {actual} does not match {want}')
    versions[name]=actual
for name,attributes in imports.items():
    module=importlib.import_module(name)
    for attribute in attributes: getattr(module,attribute)
import torch
if tuple(map(int,torch.__version__.split('+')[0].split('.')[:2]))<(2,5): raise RuntimeError('Speech requires PyTorch 2.5 or newer')
if not torch.version.cuda or not torch.cuda.is_available(): raise RuntimeError('A working NVIDIA CUDA runtime is required for speech')
tensor=torch.ones((8,8),device='cuda',dtype=torch.float32)
if (tensor@tensor).sum().item()!=512: raise RuntimeError('CUDA kernel verification failed')
torch.cuda.synchronize()
if 'ctranslate2' in imports and importlib.import_module('ctranslate2').get_cuda_device_count()<1: raise RuntimeError('CTranslate2 CUDA runtime is unavailable')
hardware={'torch':torch.__version__,'cudaBuild':torch.version.cuda,'cudaAvailable':True,'kernelVerified':True}
del tensor
torch.cuda.empty_cache()
print(json.dumps({'packages':versions,'hardware':hardware}))
'''
    result = subprocess.run([str(python), '-I', '-c', code, json.dumps([packages, imports])], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-4000:] or 'Speech dependency verification failed')
    verification = json.loads(result.stdout.strip().splitlines()[-1])
    hardware = verification.get('hardware', {})
    if not hardware.get('cudaBuild') or hardware.get('cudaAvailable') is not True or hardware.get('kernelVerified') is not True:
        raise RuntimeError('Speech dependency verification returned no CUDA kernel evidence')
    if any(verification.get('packages', {}).get(name, '').split('+')[0] != want for name, want in packages.items()):
        raise RuntimeError('Speech dependency verification returned mismatched package versions')
    return verification


def receipt_shared_cuda_runtime(path):
    """Retain external Torch paths when refreshing verified dependency evidence."""
    try:
        return json.loads(path.read_text(encoding='utf-8')).get('sharedCudaRuntime')
    except (OSError, ValueError, AttributeError):
        return None


def write_receipt(path, verification, **details):
    receipt = {'schema': RUNTIME_SCHEMA, 'executionContract': EXECUTION_CONTRACT,
               'dependenciesVerified': True, 'inferenceVerified': False, **details, **verification}
    temporary = path.with_suffix('.json.partial')
    temporary.write_text(json.dumps(receipt), encoding='utf-8')
    temporary.replace(path)


def probe(python):
    try:
        result = subprocess.run([str(python), '-c',
            'import ctypes,json,sys,site,struct,torch; from pathlib import Path; '
            'assert (3,10)<=sys.version_info[:2]<=(3,12) and struct.calcsize("P")==8; '
            'print(json.dumps({"version":torch.__version__,"cuda":torch.version.cuda,'
            '"sites":list(dict.fromkeys([*site.getsitepackages(),str(Path(torch.__file__).resolve().parent.parent)])),'
            '"torchSite":str(Path(torch.__file__).resolve().parent.parent),"base":sys._base_executable}))'],capture_output=True,text=True,timeout=30)
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


def probe_packages(python, packages, imports=None):
    """Check actual selected imports, including lazy worker classes, not just pip metadata."""
    code = '''import importlib,importlib.metadata,json,sys
packages,imports=json.loads(sys.argv[1])
failed=[]
for name,want in packages.items():
    try:
        if importlib.metadata.version(name).split('+')[0]!=want: raise RuntimeError('version mismatch')
        module_name=name.replace('-','_')
        module=importlib.import_module(module_name)
        for attribute in imports.get(module_name,[]): getattr(module,attribute)
    except Exception:
        failed.append(name)
print(json.dumps(failed))
'''
    try:
        result = subprocess.run([str(python), '-I', '-c', code, json.dumps([packages, imports or {}])],
                                capture_output=True, text=True, timeout=120)
        if not result.returncode:
            failed = json.loads(result.stdout.strip().splitlines()[-1])
            if isinstance(failed, list) and all(name in packages for name in failed):
                return list(dict.fromkeys(failed))
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    return list(packages)


def managed_site(target, environment):
    sites = json.loads(subprocess.check_output([str(target), '-I', '-c',
        'import json,site;print(json.dumps(site.getsitepackages()))'], text=True))
    environment = environment.resolve()
    for site in sites:
        own = Path(site).resolve()
        if own.name == 'site-packages' and own.is_relative_to(environment):
            return own
    raise RuntimeError('Speech repair could not identify the isolated managed package directory')


def quarantine_managed_torch(own, environment):
    """Only move local Torch files; never mutate a reused external environment."""
    entries = [entry for entry in own.iterdir() if entry.name in ('torch', 'torchgen', 'functorch')
               or (entry.name.startswith('torch-') and entry.name.endswith('.dist-info'))]
    if any(not entry.resolve().is_relative_to(own) for entry in entries):
        raise RuntimeError('Speech repair refused a Torch package outside the managed environment')
    if entries:
        quarantine = environment / '.opencore-runtime-quarantine' / uuid.uuid4().hex
        quarantine.mkdir(parents=True)
        for entry in entries:
            entry.rename(quarantine / entry.name)


def prepare(root, name, packages, imports=None):
    root = Path(root)
    environment = root / name
    target = root / name / 'Scripts/python.exe'
    effective = probe(target) if target.is_file() else None
    shared = effective if effective and effective.get('torchSite') and not Path(effective['torchSite']).is_relative_to(environment.resolve()) else None
    if not effective:
        shared = next((info for candidate in python_candidates(root) if candidate and (info:=probe(candidate))),None)
    if not target.is_file():
        base = shared['base'] if shared else sys.executable
        subprocess.run([base,'-c','import ctypes,tempfile; d=tempfile.TemporaryDirectory(); d.cleanup()'],check=True)
        subprocess.run([base,'-m','venv',str(environment)],check=True)
    if shared and not effective:
        own = managed_site(target, environment)
        quarantine_managed_torch(own, environment)
        # Append shared paths so selected local Transformers and other pins retain precedence.
        (own/'opencore_torch_shared.pth').write_text('\n'.join(shared['sites'])+'\n',encoding='utf-8')
        effective = probe(target)
    if not effective:
        if shutil.disk_usage(root).free < 6_000_000_000 + 64 * 1024 * 1024:
            raise RuntimeError('Not enough disk space: CUDA speech runtime installation needs about 6 GB of temporary and installed package space.')
        subprocess.run([str(target),'-m','pip','install','--disable-pip-version-check','--no-cache-dir',
            '--force-reinstall','--ignore-installed', '--index-url','https://download.pytorch.org/whl/cu124','torch==2.5.1'],check=True)
        if not probe(target):
            raise RuntimeError('The managed speech runtime still cannot import CUDA PyTorch after repair')
        shared = None
    subprocess.run([str(target),'-m','pip','install','--disable-pip-version-check','--no-cache-dir',
        *[f'{package}=={version}' for package,version in packages.items()]],check=True)
    failed = probe_packages(target, packages, imports)
    if failed:
        # Overlay only broken selected pins. --ignore-installed protects shared package owners.
        subprocess.run([str(target),'-m','pip','install','--disable-pip-version-check','--no-cache-dir',
            '--force-reinstall','--ignore-installed','--no-deps',
            *[f'{package}=={packages[package]}' for package in failed]],check=True)
        failed = probe_packages(target, packages, imports)
        if failed:
            raise RuntimeError('Selected speech dependencies remain unavailable after repair: '+', '.join(failed))
    return target, shared
