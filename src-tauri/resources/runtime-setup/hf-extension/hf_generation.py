"""Managed image-to-3D jobs. Preparation never runs a model or manufactures a Ready receipt."""
import base64
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from hf_catalog import stable_id
from hf_download import atomic_json, managed_path, Paused, validate_entry, HubError
from hf_image_runtime import ImageRuntimeManager, ACTIVE
from hf_runtime import _inside, _python, checked_model_files
from managed_engines import runtime_lock

ENVIRONMENT = 'budget3d-idol-v1'
MODEL = ('BudgetAny/Budget3D-Idol', '2dd6d83c57e7853234dbbb24c54b78edc842d074')
IDOL = ('speridlabs/idol', '2731c2291fc65a797c5cb627a5e5526fc1490902')
VERSION = 1


def supports(entry):
    return (entry.get('repository'), entry.get('revision')) in (MODEL, IDOL)


def validate_request(value):
    if not isinstance(value, dict): raise ValueError('Invalid 3D generation request')
    settings = value.get('settings', {})
    if not isinstance(settings, dict): raise ValueError('3D controls must be an object')
    allowed = {'inputPath', 'budget', 'steps', 'guidanceScale', 'rescaleT', 'seed', 'numGaussians', 'imageMode', 'outputFormat'}
    if set(settings) - allowed: raise ValueError('Unsupported 3D generation controls: ' + ', '.join(sorted(set(settings) - allowed)))
    path = settings.get('inputPath')
    if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
        raise ValueError('Choose a local PNG, JPEG or WebP input image')
    normalized = copy.deepcopy(settings)
    for name, default, low, high, integer in [
        ('budget', .5, .0625, 1, False), ('steps', 50, 1, 150, True),
        ('guidanceScale', 3., 0, 15, False), ('rescaleT', 3., .1, 10, False),
        ('seed', 42, 0, 2**32 - 1, True), ('numGaussians', 262144, 16384, 524288, True),
    ]:
        item = settings.get(name, default)
        if type(item) not in (int, float) or not math.isfinite(item) or not low <= item <= high or integer and type(item) is not int:
            raise ValueError(f'{name} must be {"an integer" if integer else "a number"} from {low} to {high}')
        normalized[name] = item
    if settings.get('imageMode', 'synthetic') not in ('synthetic', 'real'): raise ValueError('Choose Synthetic or Photograph')
    if settings.get('outputFormat', 'ply') != 'ply': raise ValueError('This model exports Gaussian splats as PLY, not a textured mesh')
    normalized.update(imageMode=settings.get('imageMode', 'synthetic'), outputFormat='ply')
    conversation = value.get('conversationId')
    if conversation is not None and (not isinstance(conversation, str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,200}', conversation)):
        raise ValueError('Invalid conversation identity')
    mode = value.get('operatingMode', 'native')
    if mode not in ('native', 'echo'): raise ValueError('Choose Native or ECHO')
    prompt = value.get('prompt', 'Generate a 3D Gaussian splat from this image')
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 16000: raise ValueError('Enter a short description for this job')
    return {'modelId': value.get('modelId'), 'prompt': prompt.strip(), 'settings': normalized,
            'conversationId': conversation, 'operatingMode': mode}


def verify_ply(path):
    """Accept a nonempty, binary Gaussian-splat PLY; a preview image alone never passes."""
    path = Path(path)
    if not path.is_file() or not 256 <= path.stat().st_size <= 512 * 1024**2: raise ValueError('The 3D worker returned no valid PLY artifact')
    with path.open('rb') as source: header = source.read(16384)
    marker=b'end_header\n'
    end = header.find(marker)
    if end < 0: marker=b'end_header\r\n';end=header.find(marker)
    if not header.startswith(b'ply\n') and not header.startswith(b'ply\r\n') or end < 0: raise ValueError('Invalid PLY header')
    text = header[:end].decode('ascii', errors='strict')
    if 'format binary_little_endian 1.0' not in text: raise ValueError('Expected a binary Gaussian PLY')
    count = re.search(r'(?m)^element vertex ([0-9]+)\r?$', text)
    if not count or not 1 <= int(count[1]) <= 524288: raise ValueError('The PLY has no valid Gaussian vertices')
    fields = set(re.findall(r'(?m)^property float ([A-Za-z0-9_]+)\r?$', text))
    if not {'x', 'y', 'z', 'f_dc_0', 'opacity', 'scale_0', 'rot_0'} <= fields: raise ValueError('The PLY is missing Gaussian-splat properties')
    minimum = (end + len(marker)) + int(count[1]) * len(fields) * 4
    if path.stat().st_size < minimum: raise ValueError('The PLY vertex data is truncated')
    return int(count[1])


class GenerationManager(ImageRuntimeManager):
    def __init__(self, root, resources, downloader):
        # Same durable job schema as image jobs, separate files, interpreter and cancellation domain.
        self.root, self.resources = Path(root).resolve(), Path(resources).resolve()
        self.here = self.resources / 'runtime-setup/hf-extension'
        self.vendor = self.here / 'budget3d-runtime'
        self.directory = managed_path(self.root, 'studio', 'hub-3d-jobs')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.inputs = managed_path(self.root, 'studio', 'hub-3d-inputs')
        self.inputs.mkdir(parents=True, exist_ok=True)
        self.lock, self.operation = threading.RLock(), threading.Lock()
        self.child, self.cancelled, self.jobs, self.conversations = None, threading.Event(), [], {}
        self.downloader = downloader
        try:
            saved = json.loads((self.directory / 'jobs.json').read_bytes())
            self.jobs, self.conversations = saved['jobs'], saved.get('conversations', {})
            for job in self.jobs:
                if job['status'] in ACTIVE: job.update(status='interrupted', stage='Interrupted when OpenCore closed', error=None)
        except (OSError, ValueError, KeyError, TypeError): pass

    def python(self):
        return self.root / 'runtime-setup/environments' / ENVIRONMENT / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def components(self):
        values = json.loads((self.vendor / 'components.json').read_bytes())['components']
        return [{**item, 'componentId': item['id'], 'id': stable_id(item['repository'], 'budget3d-component:' + item['id']),
                 'installable': True, 'format': 'safetensors', 'subdirectory': ''} for item in values]

    def worker_identity(self):
        digest = hashlib.sha256()
        paths = [self.here / 'generation_worker.py', self.here / 'hf_generation.py', *self.vendor.rglob('*.py'),
                 self.vendor / 'components.json', self.vendor / 'python-lock.txt']
        for path in sorted(paths):
            digest.update(path.relative_to(self.here).as_posix().encode())
            digest.update(path.read_bytes().replace(b'\r\n', b'\n'))
        return digest.hexdigest()

    def dependencies_ready(self):
        try:
            receipt = json.loads((self.directory / 'environment.json').read_bytes())
            lock = hashlib.sha256((self.vendor / 'python-lock.txt').read_bytes()).hexdigest()
            return self.python().is_file() and receipt.get('lockSha256') == lock and receipt.get('importsVerified') is True
        except (OSError, ValueError): return False

    def ensure_environment(self, job):
        with runtime_lock(self.directory / 'environment.lock'):
            if self.dependencies_ready(): return
            self._update(job, stage='Preparing the isolated 3D runtime; no model is loaded')
            if not self.python().is_file():
                self._spawn([_python(self.root), '-I', '-m', 'venv', self.python().parent.parent], job, timeout=180)
            self._spawn([self.python(), '-I', '-m', 'pip', 'install', '--require-hashes', '--no-deps', '--only-binary=:all:',
                         '--progress-bar', 'off', '-r', self.vendor / 'python-lock.txt'], job, timeout=5400)
            # Import checks are CPU-only. They do not create CUDA tensors or run model inference.
            self._spawn([self.python(), '-I', '-c',
                         'import os;os.environ["CUDA_VISIBLE_DEVICES"]="";import torch,torchvision,transformers,diffusers,tensordict,scipy,cv2,einops,plyfile,PIL'], job, timeout=180)
            atomic_json(self.directory / 'environment.json', {'environment': ENVIRONMENT, 'importsVerified': True,
                        'lockSha256': hashlib.sha256((self.vendor / 'python-lock.txt').read_bytes()).hexdigest()})

    def _component_paths(self):
        return {item['componentId']: self.downloader.directory(item) for item in self.components()}

    def _fingerprint(self, entry):
        packages = [entry, *self.components()]
        result = []
        for package in packages:
            folder = self.downloader.directory(package)
            receipt = json.loads((folder / 'install-receipt.json').read_bytes())
            if receipt.get('revision') != package['revision']: raise ValueError('A 3D component revision changed')
            pins = {file['filename']: file for file in receipt['files']}
            for pin in package['files']:
                saved = pins.get(pin['filename'], {})
                if any(saved.get(k) != pin.get(k) for k in ('repository', 'revision', 'filename', 'bytes', 'sha256', 'gitBlobSha1')):
                    raise ValueError('A 3D component receipt no longer matches its pin')
                file = managed_path(folder, pin['filename'])
                info = file.stat()
                if info.st_size != pin['bytes']: raise ValueError('A 3D component file is missing or incomplete')
                result.append([package['id'], pin['filename'], info.st_size, info.st_mtime_ns])
        return result

    def ready(self, entry, proof):
        try:
            return bool(supports(entry) and entry.get('status') == 'installed' and isinstance(proof, dict)
                        and proof.get('inferenceVerified') is True and proof.get('receiptRevision') == entry['revision']
                        and proof.get('workerVersion') == VERSION and proof.get('workerSha256') == self.worker_identity()
                        and proof.get('modelFingerprint') == self._fingerprint(entry) and self.dependencies_ready())
        except (OSError, ValueError, KeyError, TypeError): return False

    def _new(self, entry, request, internal=False):
        if not supports(entry) or entry.get('status') != 'installed': raise ValueError('Install the selected IDOL checkpoint first')
        if not self.operation.acquire(blocking=False): raise ValueError('Another 3D job is already running')
        self.cancelled.clear()
        try:
            (self.directory / 'cancel').unlink(missing_ok=True)
            job = {'id': 'hub-3d-' + uuid.uuid4().hex, 'source': 'hub-generation', 'category': '3d', 'request': request,
                   'status': 'queued', 'stage': 'Queued', 'outputs': [], 'error': None, 'internalVerification': internal,
                   'requiresTurnHandoff':request.get('operatingMode')=='echo',
                   'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
            with self.lock: self.jobs.append(job)
            self._update(job)
            return job
        except Exception:
            self.operation.release()
            raise

    def check_access(self):
        # Access preflight precedes multi-GB component downloads. Tokens stay in HubClient/DPAPI.
        for item in self.components():
            validate_entry(item)
            if item.get('gated'):
                folder = self.downloader.directory(item)
                if all((folder / pin['filename']).is_file() and (folder / pin['filename']).stat().st_size == pin['bytes'] for pin in item['files']):
                    continue  # An already-installed, pinned encoder also works offline; install() rechecks its hashes below.
                try:
                    with self.downloader.client.open_file(item['files'][0], 0) as response: response.read(1)
                except Exception as error:
                    if isinstance(error, HubError) and error.status not in (401,403,404):
                        raise RuntimeError('Could not check DINOv3 access: '+str(error)+'. Downloaded components were kept; retry when Hugging Face is reachable.') from None
                    raise ValueError('DINOv3 access is required. Accept its publisher terms at https://huggingface.co/'
                                     + item['repository'] + ' and save an authorized Hugging Face token in OpenCore Settings. ' + str(error)[:400]) from None

    def _prepare(self, entry, job, report=lambda value: None, pause=None):
        checked_model_files(entry, self.root, self.cancelled)
        class Stop:
            def is_set(inner): return self.cancelled.is_set() or pause is not None and pause.is_set()
        stop = Stop()
        components = self.components()
        self.check_access()
        for item in components:
            if stop.is_set(): raise Paused('3D preparation paused; completed files retained')
            def progress(value):
                report(value)
                self._update(job, stage='Preparing ' + item['repository'] + ' · ' + value.get('stage', ''), progress=value)
            self.downloader.install(item, stop, progress)
        self.ensure_environment(job)
        if stop.is_set(): raise Paused('3D preparation paused; completed files retained')
        return {'dependenciesPrepared': True, 'inferenceVerified': False, 'runtimeReady': False,
                'receiptRevision': entry['revision'], 'workerVersion': VERSION, 'workerSha256': self.worker_identity()}

    def prepare(self, entry, report, pause):
        job = self._new(entry, {'modelId': entry['id'], 'prompt': 'Prepare 3D dependencies', 'settings': {}}, internal=True)
        try:
            proof = self._prepare(entry, job, report, pause)
            self._update(job, status='completed', stage='3D dependencies prepared; generate an object to verify inference')
            return proof
        except Exception as error:
            self._update(job, status='cancelled' if pause.is_set() or self.cancelled.is_set() else 'failed', stage='Preparation stopped', error=str(error)[:2000])
            raise
        finally: self.operation.release()

    def _run(self, entry, request, job):
        self._prepare(entry, job)
        if getattr(self,'before_inference',None):self.before_inference(job,self.cancelled,self._update)
        original = Path(request['settings']['inputPath'])
        if not original.is_file() or not 1 <= original.stat().st_size <= 16 * 1024**2: raise ValueError('Choose an input image up to 16 MB')
        output = managed_path(self.directory, job['id'])
        output.mkdir(exist_ok=False)
        # Snapshot input once. A later edit of the user's file cannot alter this job's provenance.
        image = output / ('source' + original.suffix.lower())
        with original.open('rb') as stream: raw=stream.read(16*1024**2+1)
        if len(raw)>16*1024**2:raise ValueError('The input image grew beyond 16 MB; choose the image again')
        image.write_bytes(raw)
        payload = {**request, 'settings': {**request['settings'], 'inputPath': str(image)},
                   'modelRoot': str(self.root), 'modelDirectory': str(self.downloader.directory(entry)),
                   'repository': entry['repository'], 'revision': entry['revision'],
                   'components': {key: str(path) for key, path in self._component_paths().items()}}
        request_file = self.directory / (job['id'] + '.json')
        atomic_json(request_file, payload)
        self._update(job, status='running', stage='Loading image encoders')
        self._spawn([self.python(), '-I', self.here / 'generation_worker.py', '--request', request_file, '--output', output], job, output=output, timeout=3600)
        proof = json.loads((output / 'generation-result.json').read_bytes())
        if proof.get('modelId') != entry['id'] or proof.get('revision') != entry['revision'] or proof.get('inferenceVerified') is not True:
            raise ValueError('The 3D worker did not confirm this checkpoint generated an artifact')
        ply = _inside(proof.get('ply', ''), output)
        vertices = verify_ply(ply)
        preview = _inside(proof.get('inputPreview', ''), output)
        if preview.suffix != '.png' or not preview.is_file(): raise ValueError('The 3D worker returned no prepared input image')
        self._update(job, status='completed', stage='3D Gaussian splat generated', outputs=[str(ply), str(preview)],
                     progress={'vertices': vertices, 'outputKind': 'gaussian-splat', 'seed': request['settings']['seed']})
        return {'runtimeReady': True, 'inferenceVerified': True, 'receiptRevision': entry['revision'],
                'workerVersion': VERSION, 'workerSha256': self.worker_identity(), 'verifiedAt': time.time(),
                'modelFingerprint': self._fingerprint(entry)}

    def start(self, entry, request, on_ready=None):
        request = validate_request(request)
        if request['modelId'] != entry['id']: raise ValueError('The request selects a different 3D checkpoint')
        if entry['repository'] == IDOL[0] and request['settings']['budget'] != 1:
            raise ValueError('Original IDOL uses the full budget; select Budget3D for reduced token budgets')
        job = self._new(entry, request)
        def work():
            try:
                proof = self._run(entry, request, job)
                if self.cancelled.is_set(): raise RuntimeError('3D generation cancelled')
                if on_ready: on_ready(proof)
            except Exception as error:
                self._update(job, status='cancelled' if self.cancelled.is_set() else 'failed',
                             stage='Cancelled' if self.cancelled.is_set() else 'Failed', error=None if self.cancelled.is_set() else str(error)[:2000])
            finally: self.operation.release()
        threading.Thread(target=work, daemon=True).start()
        return copy.deepcopy(job)

    def import_image(self, value):
        if not isinstance(value, str) or len(value) > 24 * 1024**2: raise ValueError('Choose an image up to 16 MB')
        try: raw = base64.b64decode(value, validate=True)
        except ValueError: raise ValueError('Invalid image data') from None
        if not 1 <= len(raw) <= 16 * 1024**2: raise ValueError('Choose an image up to 16 MB')
        suffix = '.png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else '.jpg' if raw.startswith(b'\xff\xd8\xff') else '.webp' if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' else None
        if suffix is None: raise ValueError('Choose a PNG, JPEG or WebP image')
        path = managed_path(self.inputs, hashlib.sha256(raw).hexdigest() + suffix)
        if not path.exists(): path.write_bytes(raw)
        return {'path': str(path), 'bytes': len(raw)}

    def stop(self, identity=None):
        with self.lock:
            active = next((job for job in self.jobs if job['status'] in ACTIVE and (identity is None or job['id'] == identity)), None)
            if identity is not None and active is None: raise ValueError('This 3D job is no longer active')
            if active: self._update(active, stage='Cancelling and releasing the 3D worker')
        return super().stop(identity)

    def artifact(self, identity, path):
        with self.lock: job = next((j for j in self.jobs if j['id'] == identity), None)
        if not job or job['status'] != 'completed' or path not in job['outputs']: raise ValueError('Choose an output from a completed 3D job')
        file = _inside(path, self.directory / identity)
        if file.suffix not in ('.ply', '.png') or not file.is_file(): raise ValueError('The generated artifact is missing')
        if file.suffix == '.ply': verify_ply(file)
        return file
