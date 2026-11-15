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
# One PyTorch build per variant. Only the selected one is installed.
CONTRACTS = {'cuda': EXECUTION_CONTRACT, 'cpu': 'cpu-kernel-v1'}
TORCH_INDEX = {'cuda': 'https://download.pytorch.org/whl/cu124', 'cpu': 'https://download.pytorch.org/whl/cpu'}
INSTALL_BYTES = {'cuda': 6_000_000_000, 'cpu': 2_000_000_000}


def _hardware_profile():
    shared = Path(__file__).resolve().parents[1] / 'runtime-setup/hf-extension'
    if str(shared) not in sys.path:
        sys.path.append(str(shared))
    import hardware_profile
    return hardware_profile


def selected_variant(data_root, runtime_id, choices=('cuda', 'cpu')):
    """The variant this PC should use: the person's choice, else CUDA on NVIDIA and CPU otherwise."""
    if os.environ.get('OPENCORE_SPEECH_VARIANT') in choices:
        return os.environ['OPENCORE_SPEECH_VARIANT']
    try:
        return _hardware_profile().chosen_variant(data_root, runtime_id, choices)
    except Exception:
        return choices[0]


def cuda_ready():
    """True when this PC has an NVIDIA GPU with a CUDA 12+ driver (or the profile cannot be read)."""
    try:
        return bool(_hardware_profile().profile().get('cuda'))
    except Exception:
        return True


def environment_signature(python, packages):
    """Cheap package/interpreter identity; never imports Torch or starts CUDA."""
    code = '''import hashlib,importlib.metadata,json,os,sys
from pathlib import Path
rows=[]
def record(path):
    path=Path(path)
    if path.is_file():
        stat=path.stat();rows.append([str(path.resolve()),stat.st_size,stat.st_mtime_ns])
record(sys.executable)
record(Path(sys.prefix)/'pyvenv.cfg')
for name,want in json.loads(sys.argv[1]).items():
    dist=importlib.metadata.distribution(name)
    if dist.version.split('+')[0]!=want:raise RuntimeError('Package version changed')
    record(dist._path/'METADATA');record(dist._path/'RECORD')
    top=dist.read_text('top_level.txt') or name.replace('-','_')
    for module in top.splitlines():record(dist.locate_file(module)/'__init__.py')
torch=importlib.metadata.distribution('torch')
record(torch._path/'METADATA');record(torch._path/'RECORD')
for path in Path(torch.locate_file('torch')).glob('_C*.pyd'):record(path)
record(torch.locate_file('torch/lib/torch_cuda.dll'))
for site in sys.path:
    if Path(site).name=='site-packages':
        for path in Path(site).glob('*.pth'):record(path)
if os.name=='nt':record(Path(os.environ.get('WINDIR','C:/Windows'))/'System32/nvcuda.dll')
print(hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest())
'''
    try:
        result = subprocess.run([str(python), '-I', '-c', code, json.dumps(packages)],
                                capture_output=True, text=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        value = result.stdout.strip()
        if not result.returncode and len(value) == 64 and all(c in '0123456789abcdef' for c in value):
            return value
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def cached_verification(root, name, packages, variant='cuda'):
    """Reuse completed evidence only while this managed environment is unchanged."""
    root = Path(root)
    python = root / (name + '-venv') / 'Scripts/python.exe'
    try:
        receipt = json.loads((root / (name + '-runtime.json')).read_text(encoding='utf-8'))
        hardware = receipt.get('hardware') or {}
        gpu_ready = variant != 'cuda' or (hardware.get('cudaBuild') and hardware.get('cudaAvailable') is True)
        if (not python.is_file() or receipt.get('schema') != RUNTIME_SCHEMA or
                receipt.get('executionContract') != CONTRACTS[variant] or not receipt.get('dependenciesVerified') or
                receipt.get('variant', 'cuda') != variant or
                receipt.get('python') != str(python.resolve()) or not receipt.get('environmentSignature') or
                not gpu_ready or hardware.get('kernelVerified') is not True or
                any(receipt.get('packages', {}).get(key, '').split('+')[0] != value for key, value in packages.items())):
            return None
        return receipt if environment_signature(python, packages) == receipt['environmentSignature'] else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def verify(python, packages, imports, variant='cuda'):
    """Verify versions/imports and a small kernel on the selected device before claiming readiness."""
    if variant == 'cpu':
        return verify_cpu(python, packages, imports)
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


def verify_cpu(python, packages, imports):
    """CPU variant: the same package checks, with the kernel check run on the processor."""
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
tensor=torch.ones((8,8),dtype=torch.float32)
if (tensor@tensor).sum().item()!=512: raise RuntimeError('CPU kernel verification failed')
hardware={'torch':torch.__version__,'cudaBuild':torch.version.cuda,'cudaAvailable':bool(torch.version.cuda and torch.cuda.is_available()),'kernelVerified':True,'device':'cpu'}
print(json.dumps({'packages':versions,'hardware':hardware}))
'''
    result = subprocess.run([str(python), '-I', '-c', code, json.dumps([packages, imports])], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-4000:] or 'Speech dependency verification failed')
    verification = json.loads(result.stdout.strip().splitlines()[-1])
    if (verification.get('hardware') or {}).get('kernelVerified') is not True:
        raise RuntimeError('Speech dependency verification returned no kernel evidence')
    if any(verification.get('packages', {}).get(name, '').split('+')[0] != want for name, want in packages.items()):
        raise RuntimeError('Speech dependency verification returned mismatched package versions')
    return verification


def receipt_shared_cuda_runtime(path):
    """Retain external Torch paths when refreshing verified dependency evidence."""
    try:
        return json.loads(path.read_text(encoding='utf-8')).get('sharedCudaRuntime')
    except (OSError, ValueError, AttributeError):
        return None


def write_receipt(path, verification, variant='cuda', **details):
    receipt = {'schema': RUNTIME_SCHEMA, 'executionContract': CONTRACTS[variant], 'variant': variant,
               'dependenciesVerified': True, 'inferenceVerified': False, **details, **verification}
    temporary = path.with_suffix('.json.partial')
    temporary.write_text(json.dumps(receipt), encoding='utf-8')
    temporary.replace(path)


def probe(python, variant='cuda'):
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
            # CPU speech runs on any PyTorch build; CUDA speech needs a CUDA build.
            if (major,minor) >= (2,5) and (info['cuda'] or variant == 'cpu'):
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


def prepare(root, name, packages, imports=None, variant='cuda'):
    root = Path(root)
    environment = root / name
    target = root / name / 'Scripts/python.exe'
    effective = probe(target, variant) if target.is_file() else None
    shared = effective if effective and effective.get('torchSite') and not Path(effective['torchSite']).is_relative_to(environment.resolve()) else None
    if not effective:
        shared = next((info for candidate in python_candidates(root) if candidate and (info:=probe(candidate, variant))),None)
    if not target.is_file():
        base = shared['base'] if shared else sys.executable
        subprocess.run([base,'-c','import ctypes,tempfile; d=tempfile.TemporaryDirectory(); d.cleanup()'],check=True)
        subprocess.run([base,'-m','venv',str(environment)],check=True)
    if shared and not effective:
        own = managed_site(target, environment)
        quarantine_managed_torch(own, environment)
        # Append shared paths so selected local Transformers and other pins retain precedence.
        (own/'opencore_torch_shared.pth').write_text('\n'.join(shared['sites'])+'\n',encoding='utf-8')
        effective = probe(target, variant)
    if not effective:
        label = 'CUDA' if variant == 'cuda' else 'CPU'
        if shutil.disk_usage(root).free < INSTALL_BYTES[variant] + 64 * 1024 * 1024:
            raise RuntimeError('Not enough disk space: ' + label + ' speech runtime installation needs about '
                               + str(INSTALL_BYTES[variant] // 1_000_000_000)
                               + ' GB of temporary and installed package space.')
        subprocess.run([str(target),'-m','pip','install','--disable-pip-version-check','--no-cache-dir',
            '--force-reinstall','--ignore-installed', '--index-url',TORCH_INDEX[variant],'torch==2.5.1'],check=True)
        if not probe(target, variant):
            raise RuntimeError('The managed speech runtime still cannot import ' + label + ' PyTorch after repair')
        shared = None
    failed = probe_packages(target, packages, imports)
    if failed:
        subprocess.run([str(target),'-m','pip','install','--disable-pip-version-check','--no-cache-dir',
            *[f'{package}=={packages[package]}' for package in failed]],check=True)
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
