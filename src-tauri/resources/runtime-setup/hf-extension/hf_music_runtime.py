"""Install the YuE2 song runtime that Music Studio needs, in one step.

The native app treats a YuE folder as installed once it holds
``studio/server.py`` and ``.venv/Scripts/python.exe``. This manager builds that
folder from public, pinned sources:

* an isolated virtual environment made from OpenCore's bundled Python,
* the one PyTorch 2.10.0 build this PC can use (NVIDIA CUDA, Intel XPU or CPU),
* the YuE2 inference package (Apache-2.0) fetched file by file at a pinned
  commit and checked against SHA-256 pins,
* OpenCore's own studio server, written last so a half-finished install is
  never reported as installed.

The model weights are downloaded separately from Models (CC BY-NC 4.0).
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import Request, urlopen

RUNTIME_DIR = Path(__file__).resolve().parents[1] / 'music-runtime'
CONFIG = json.loads((RUNTIME_DIR / 'music-runtime.json').read_text(encoding='utf-8'))
SERVER_SOURCE = RUNTIME_DIR / 'studio_server.py'
SERVER_MARKER = 'opencore-yue2-studio-server'
RECEIPT_NAME = '_opencore-runtime.json'
DISK_RESERVE = 1024 ** 3
PROGRESS_LINE = re.compile(r'Progress (\d+) of (\d+)')
# Share of the overall bar given to each step; PyTorch dominates the download.
WEIGHTS = {'environment': 3, 'torch': 75, 'packages': 14, 'code': 4, 'verify': 4}


def music_root():
    """Resolve the YuE folder exactly like the native Music Studio does."""
    configured = os.environ.get('OPENCORE_MUSIC_HOME')
    if configured:
        return Path(configured)
    return Path(os.environ.get('USERPROFILE') or Path.home()) / 'YuE'


def venv_python(root):
    return Path(root) / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def version_tuple(value):
    return tuple(int(part) for part in re.findall(r'\d+', str(value))[:3])


class Cancelled(RuntimeError):
    pass


def _quiet(args, timeout=15):
    options = {'capture_output': True, 'text': True, 'encoding': 'utf-8', 'errors': 'replace', 'timeout': timeout}
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(args, check=False, **options)


def detect_hardware(run=_quiet):
    """Return the GPUs found and the PyTorch build to install for them."""
    variants = CONFIG['torch']['variants']
    nvidia = []
    smi = shutil.which('nvidia-smi')
    if smi:
        for query in ('name,memory.total,driver_version,compute_cap', 'name,memory.total,driver_version'):
            try:
                result = run([smi, f'--query-gpu={query}', '--format=csv,noheader,nounits'])
            except (OSError, subprocess.SubprocessError):
                break
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    parts = [part.strip() for part in line.split(',')]
                    if len(parts) < 3:
                        continue
                    try:
                        memory = float(parts[1]) / 1024
                    except ValueError:
                        memory = None
                    try:
                        capability = float(parts[3]) if len(parts) > 3 else None
                    except ValueError:
                        capability = None
                    nvidia.append({'name': parts[0], 'vramGiB': memory, 'driver': parts[2], 'computeCapability': capability})
                break
    if nvidia:
        best = max(nvidia, key=lambda gpu: ((gpu['computeCapability'] or 0) >= CONFIG['minimumComputeCapability'], gpu['vramGiB'] or 0))
        driver = version_tuple(best['driver'])
        capability = best['computeCapability']
        if capability is not None and capability < CONFIG['minimumComputeCapability']:
            return _plan('cpu', best, f"{best['name']} has no BF16 support, which YuE2 needs on NVIDIA GPUs. "
                         'Songs will be generated on the CPU, which can take hours per song.')
        for variant in ('cu128', 'cu126'):
            if driver >= version_tuple(variants[variant]['minimumDriver']):
                note = None
                if best['vramGiB'] is not None and best['vramGiB'] < 7.5:
                    note = f"{best['name']} has {best['vramGiB']:.0f} GB of memory. YuE2 is reported to work from 8 GB; longer songs may run out of memory."
                return _plan(variant, best, note)
        return _plan('cpu', best, f"The NVIDIA driver ({best['driver']}) is too old for PyTorch 2.10. "
                     f"Update it to {variants['cu126']['minimumDriver']} or newer and install again to use the GPU. "
                     'For now songs would be generated on the CPU, which can take hours per song.')
    names = []
    if os.name == 'nt':
        try:
            result = run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                          '(Get-CimInstance Win32_VideoController).Name'], timeout=20)
            names = [line.strip() for line in result.stdout.splitlines() if line.strip()] if result.returncode == 0 else []
        except (OSError, subprocess.SubprocessError):
            names = []
    arc = next((name for name in names if re.search(r'\bArc\b.*\b(A|B|Pro)\s?\d', name, re.I)), None)
    if arc:
        return _plan('xpu', {'name': arc}, 'Intel Arc support in YuE2 is experimental and needs a current Intel graphics driver.')
    amd = next((name for name in names if re.search(r'\b(AMD|Radeon)\b', name, re.I)), None)
    note = (f'{amd} is not supported by YuE2 on Windows yet. ' if amd else 'No compatible GPU was found. ')
    return _plan('cpu', {'name': amd} if amd else None, note + 'Songs will be generated on the CPU, which can take hours per song.')


def _plan(variant, gpu, note):
    pin = CONFIG['torch']['variants'][variant]
    return {'variant': variant, 'label': pin['label'], 'gpu': gpu, 'note': note,
            'downloadBytes': pin['downloadBytes'], 'requiredBytes': pin['diskBytes'] + DISK_RESERVE}


def free_bytes(path):
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


def server_state(root):
    server = Path(root) / 'studio' / 'server.py'
    if not server.is_file():
        return 'missing'
    try:
        head = server.read_text(encoding='utf-8', errors='replace')[:4096]
    except OSError:
        return 'foreign'
    return 'opencore' if SERVER_MARKER in head else 'foreign'


class MusicRuntimeManager:
    def __init__(self, root=None, resources=None, *, runner_factory=None, detect=detect_hardware, fetch=None,
                 python=None, root_resolver=music_root):
        self.resolve_root = root_resolver
        self.runner_factory = runner_factory or self._default_runner
        self.detect = detect
        self.fetch = fetch or self._fetch
        self.python = python or getattr(sys, '_base_executable', None) or sys.executable
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.worker = None
        self.plan = None
        self.progress = {'status': 'idle'}

    # Status ---------------------------------------------------------------
    def _hardware(self):
        with self.lock:
            if self.plan is None:
                self.plan = self.detect()
            return self.plan

    def status(self):
        root = self.resolve_root()
        state = server_state(root)
        receipt = {}
        try:
            receipt = json.loads((root / 'studio' / RECEIPT_NAME).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        installed = state != 'missing' and venv_python(root).is_file()
        with self.lock:
            value = dict(self.progress)
            busy = bool(self.worker and self.worker.is_alive())
        plan = self.plan
        try:
            free = free_bytes(root)
        except OSError:
            free = None
        value.update(installed=installed, busy=busy, folder=str(root), supported=os.name == 'nt',
                     server=state, foreign=state == 'foreign', freeBytes=free,
                     runtimeVersion=CONFIG['runtimeVersion'], installedVersion=receipt.get('runtimeVersion'),
                     updateAvailable=state == 'opencore' and receipt.get('runtimeVersion') != CONFIG['runtimeVersion'],
                     sourceUrl=CONFIG['source']['repository'], codeLicense=CONFIG['source']['codeLicense'])
        if plan:
            value.update(variant=plan['variant'], variantLabel=plan['label'], note=plan['note'],
                         downloadBytes=plan['downloadBytes'], requiredBytes=plan['requiredBytes'],
                         gpu=(plan['gpu'] or {}).get('name'))
        return value

    # Control --------------------------------------------------------------
    def start(self):
        if os.name != 'nt':
            raise ValueError('The music runtime installer currently supports Windows only.')
        root = self.resolve_root()
        if server_state(root) == 'foreign':
            raise ValueError(f'{root} already has its own studio/server.py. OpenCore will not replace it; '
                             'remove that folder or set OPENCORE_MUSIC_HOME to an empty folder, then try again.')
        if sys.version_info[:2] < tuple(CONFIG['python']['minimum']) or sys.version_info[:2] >= tuple(CONFIG['python']['maximumExclusive']):
            raise ValueError('OpenCore\'s bundled Python is not compatible with the music runtime. Reinstall OpenCore and try again.')
        with self.lock:
            if self.worker and self.worker.is_alive():
                return self.status()
        plan = self._hardware()
        free = free_bytes(root)
        if free < plan['requiredBytes']:
            raise ValueError(f"Installing the music runtime ({plan['label']}) needs about {plan['requiredBytes'] / 1024 ** 3:.1f} GB free "
                             f"on the drive holding {root}, but only {free / 1024 ** 3:.1f} GB is free. Free up space and try again.")
        with self.lock:
            if self.worker and self.worker.is_alive():
                return self.status()
            self.cancelled.clear()
            self.progress = {'status': 'installing', 'step': 'environment', 'stage': 'Preparing the Python environment',
                             'percent': 0, 'downloadedBytes': 0, 'totalBytes': 0, 'startedAt': time.time()}
            self.worker = threading.Thread(target=self._install, args=(root, plan), daemon=True, name='opencore-music-runtime')
            self.worker.start()
        return self.status()

    def cancel(self):
        self.cancelled.set()
        return self.status()

    # Install --------------------------------------------------------------
    def _update(self, **values):
        with self.lock:
            self.progress.update(values)

    def _step(self, step, stage, fraction=0.0):
        order = list(WEIGHTS)
        done = sum(WEIGHTS[name] for name in order[:order.index(step)])
        percent = min(99.0, done + WEIGHTS[step] * max(0.0, min(1.0, fraction)))
        self._update(step=step, stage=stage, percent=round(percent, 1))

    def _check(self):
        if self.cancelled.is_set():
            raise Cancelled('Installation cancelled. Run Install again to continue where it stopped.')

    def _default_runner(self, reporter):
        helper = str(Path(__file__).resolve().parents[1])
        if helper not in sys.path:
            sys.path.insert(0, helper)
        from setup_manager import Runner, Cancelled as SetupCancelled
        manager = self

        class EventRunner(Runner):
            def check_cancel(self):
                if manager.cancelled.is_set():
                    raise SetupCancelled('Installation cancelled. Run Install again to continue where it stopped.')
        return EventRunner(None, reporter)

    def _pip_reporter(self, step, stage):
        def report(_kind, detail='', **_values):
            match = PROGRESS_LINE.search(detail or '')
            if match:
                done, total = int(match.group(1)), int(match.group(2))
                values = {'downloadedBytes': done, 'totalBytes': total}
                if step == 'torch' and total > 50_000_000:
                    self._step(step, stage, done / total * 0.9)
                self._update(**values)
            elif detail and detail.startswith(('Collecting ', 'Downloading ', 'Installing collected')):
                self._update(detail=detail[:200])
                if detail.startswith('Installing collected') and step == 'torch':
                    self._step(step, 'Installing PyTorch', 0.92)
        return report

    def _pip(self, python, args, step, stage, timeout):
        runner = self.runner_factory(self._pip_reporter(step, stage))
        environment = {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME', 'PIP_USER', 'PIP_TARGET', 'PIP_PREFIX')}
        environment.update(PYTHONNOUSERSITE='1', PIP_NO_INPUT='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONIOENCODING='utf-8')
        runner.run([python, '-I', '-m', 'pip', 'install', '--only-binary=:all:', '--no-cache-dir', '--progress-bar', 'raw', *args],
                   env=environment, timeout=timeout)

    def _environment(self, root):
        self._step('environment', 'Preparing the Python environment')
        runner = self.runner_factory(lambda *_args, **_values: None)
        target = venv_python(root)
        environment = root / '.venv'
        if not target.is_file():
            runner.run([self.python, '-I', '-m', 'venv', '--without-pip', str(environment)], timeout=300)
        info = runner.run([target, '-I', '-c', "import json,sys;print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix,'version':list(sys.version_info[:2])}))"], timeout=60)
        value = json.loads(info.stdout.strip().splitlines()[-1])
        if Path(value['prefix']).resolve() != environment.resolve() or value['prefix'] == value['base']:
            raise RuntimeError(f'{environment} is not an isolated Python environment. Remove it and try again.')
        if not tuple(CONFIG['python']['minimum']) <= tuple(value['version']) < tuple(CONFIG['python']['maximumExclusive']):
            raise RuntimeError(f'{environment} uses an incompatible Python version. Remove it and try again.')
        if runner.run([target, '-I', '-m', 'pip', '--version'], check=False, timeout=60).returncode:
            runner.run([target, '-I', '-m', 'ensurepip', '--upgrade', '--default-pip'], timeout=300)
        return target

    def _fetch(self, url, limit):
        request = Request(url, headers={'User-Agent': 'OpenCore-music-runtime'})
        with urlopen(request, timeout=60) as response:
            data = response.read(limit + 1)
        return data

    def _code(self, root):
        source = CONFIG['source']
        files = source['files']
        total = sum(pin['bytes'] for pin in files.values())
        staging = Path(tempfile.mkdtemp(prefix='.opencore-yue2-', dir=root))
        try:
            done = 0
            for index, (name, pin) in enumerate(sorted(files.items())):
                self._check()
                self._step('code', 'Downloading the YuE2 song code', index / len(files))
                data = self.fetch(source['rawBase'] + name, pin['bytes'])
                if len(data) != pin['bytes'] or hashlib.sha256(data).hexdigest() != pin['sha256']:
                    raise RuntimeError(f'{name} from {source["repository"]} did not match its pinned checksum. '
                                       'The download may be blocked or altered; try again later.')
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                done += len(data)
                self._update(downloadedBytes=done, totalBytes=total)
            self._check()
            for name in files:
                destination = root / name
                if destination.is_symlink():
                    raise RuntimeError(f'{destination} is a link; OpenCore will not write through it.')
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging / name, destination)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _verify(self, root, python, plan):
        self._step('verify', 'Checking the music runtime')
        code = (
            "import json,sys\n"
            f"sys.path[:0]=[{str(root / 'src')!r},{str(root)!r}]\n"
            "import torch,transformers,soundfile,tiktoken,safetensors,accelerate,numpy\n"
            "import yue2,yue2.pipeline,yue2.protocol\n"
            "value={'torch':torch.__version__,'yue2':yue2.__version__,'cuda':torch.cuda.is_available(),"
            "'xpu':bool(getattr(torch,'xpu',None) and torch.xpu.is_available())}\n"
            "device='cuda' if value['cuda'] else 'xpu' if value['xpu'] else 'cpu'\n"
            "if sys.argv[1] in ('cu128','cu126'):\n"
            "    assert value['cuda'], 'PyTorch cannot see the NVIDIA GPU'\n"
            "if sys.argv[1]=='xpu':\n"
            "    assert value['xpu'], 'PyTorch cannot see the Intel Arc GPU'\n"
            "x=torch.arange(16,dtype=torch.float32,device=device)\n"
            "assert float((x*2).sum().cpu())==240.0, 'A test calculation on '+device+' returned a wrong result'\n"
            "value['device']=device\n"
            "if device=='cuda': value['bf16']=bool(torch.cuda.is_bf16_supported())\n"
            "print(json.dumps(value))\n")
        runner = self.runner_factory(lambda *_args, **_values: None)
        result = runner.run([python, '-I', '-c', code, plan['variant']], check=False, timeout=600)
        if result.returncode:
            reason = (result.stdout.strip().splitlines() or ['unknown error'])[-1][:400]
            hint = ' Update the graphics driver and run Install again.' if plan['variant'] != 'cpu' else ''
            raise RuntimeError(f'The music runtime was installed but failed its check: {reason}.{hint}')
        return json.loads(result.stdout.strip().splitlines()[-1])

    def _install(self, root, plan):
        try:
            root.mkdir(parents=True, exist_ok=True)
            python = self._environment(root)
            self._check()
            torch = CONFIG['torch']
            pin = torch['variants'][plan['variant']]
            stage = f"Downloading PyTorch for {plan['label']}"
            self._step('torch', stage)
            self._update(downloadedBytes=0, totalBytes=pin['downloadBytes'])
            self._pip(python, [f"torch=={torch['version']}+{plan['variant']}", '--index-url', pin['indexUrl']],
                      'torch', stage, timeout=6 * 3600)
            self._check()
            self._step('packages', 'Installing the YuE2 libraries')
            self._update(downloadedBytes=0, totalBytes=0)
            self._pip(python, [f'{name}=={version}' for name, version in CONFIG['packages'].items()],
                      'packages', 'Installing the YuE2 libraries', timeout=3600)
            self._check()
            self._code(root)
            self._check()
            checked = self._verify(root, python, plan)
            self._check()
            studio = root / 'studio'
            studio.mkdir(parents=True, exist_ok=True)
            receipt = {'runtimeVersion': CONFIG['runtimeVersion'], 'variant': plan['variant'], 'torch': checked.get('torch'),
                       'yue2': checked.get('yue2'), 'device': checked.get('device'), 'sourceCommit': CONFIG['source']['commit'],
                       'installedAt': time.strftime('%Y-%m-%dT%H:%M:%S')}
            _atomic_write(studio / RECEIPT_NAME, json.dumps(receipt, indent=2).encode('utf-8'))
            # Written last: the native app reports the runtime installed once this exists.
            _atomic_write(studio / 'server.py', SERVER_SOURCE.read_bytes())
            self._update(status='installed', step='done', stage='Music runtime installed', percent=100, error=None)
        except Exception as error:
            cancelled = self.cancelled.is_set() or type(error).__name__ == 'Cancelled'
            message = 'Installation cancelled. Run Install again to continue where it stopped.' if cancelled else str(error)[-1200:]
            self._update(status='cancelled' if cancelled else 'failed', error=message)


def _atomic_write(path, data):
    temporary = path.with_name(f'.{path.name}.tmp')
    temporary.write_bytes(data)
    os.replace(temporary, path)
