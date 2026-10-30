"""Owned, offline prediction runtimes for reviewed computer-use checkpoints."""
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from hf_download import Downloader, HubClient
from hf_runtime import checked_model_files, _hash, _python, _available_memory

CONFIG = json.loads(Path(__file__).with_name('computer-models.json').read_text(encoding='utf-8'))
VERSION = 1


def profile_for(entry):
    for runtime, profile in CONFIG['profiles'].items():
        if (entry.get('id'), entry.get('repository'), entry.get('revision')) == (profile['id'], profile['repository'], profile['revision']):
            return runtime, profile
    return None, None


def supports(entry): return profile_for(entry)[0] is not None


def request_values(body, runtime):
    if not isinstance(body, dict): raise ValueError('Enter a computer-use request')
    task = body.get('task')
    if not isinstance(task, str) or not task.strip() or len(task) > 3000:
        raise ValueError('Enter a task of up to 3,000 characters')
    state = body.get('state', '')
    if not isinstance(state, str) or len(state) > 3000: raise ValueError('State text exceeds its limit')
    result = {'task': task.strip(), 'state': state}
    if runtime != 'hobbylm-v1':
        image = body.get('image')
        if not isinstance(image, str) or not image or len(image) > 7*1024*1024:
            raise ValueError('Choose a screenshot up to 5 MB')
        try: decoded = base64.b64decode(image, validate=True)
        except ValueError: raise ValueError('Invalid screenshot encoding') from None
        if not 0 < len(decoded) <= 5*1024*1024: raise ValueError('Choose a screenshot up to 5 MB')
        result['image'] = image
    if runtime == 'gui-jepa-v1':
        candidates = body.get('candidates')
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= 80:
            raise ValueError('GUI-JEPA needs 1–80 accessibility candidates with text and normalized bounds')
        result['candidates'] = []
        for candidate in candidates:
            if not isinstance(candidate, dict): raise ValueError('Invalid accessibility candidate')
            text, bounds = candidate.get('text', ''), candidate.get('bounds')
            if not isinstance(text, str) or len(text) > 1000 or not isinstance(bounds, list) or len(bounds) != 4:
                raise ValueError('Each candidate needs text and four normalized bounds')
            if not all(type(value) in (int, float) and 0 <= value <= 1 for value in bounds) or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
                raise ValueError('Candidate bounds must be [left, top, right, bottom] between 0 and 1')
            result['candidates'].append({'text': text, 'bounds': bounds,
                'editable': candidate.get('editable') is True, 'clickable': candidate.get('clickable', True) is True,
                'scrollable': candidate.get('scrollable') is True, 'longClickable': candidate.get('longClickable') is True})
    return result


class ComputerManager:
    def __init__(self, root, resources, downloader=None):
        self.root, self.resources = Path(root).resolve(), Path(resources).resolve()
        self.downloader = downloader or Downloader(self.root, HubClient())
        self.operation, self.lock = threading.Lock(), threading.RLock()
        self.cancelled = threading.Event()
        self.child, self.job = None, None

    def dependency(self, profile):
        entry = next(value for value in CONFIG['dependencies'] if value['id'] == profile['dependencyId'])
        folder = self.downloader.directory(entry)
        return {**entry, 'localDirectory': str(folder), 'receiptPath': str(folder/'install-receipt.json')}

    def python(self, profile):
        if not profile['recipe']: return _python(self.root)
        return str(self.root/'runtime-setup/environments'/profile['recipe']/('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))

    def ready(self, entry, proof):
        runtime, profile = profile_for(entry)
        if not runtime or entry.get('status') != 'installed' or not isinstance(proof, dict): return False
        if proof.get('workerVersion') != VERSION or proof.get('revision') != entry['revision'] or proof.get('inferenceVerified') is not True: return False
        try:
            dependency = self.dependency(profile)
            if _hash(dependency['receiptPath']) != proof.get('dependencyReceiptSha256'): return False
            if profile['recipe'] and not Path(self.python(profile)).is_file(): return False
            receipt = json.loads(Path(dependency['receiptPath']).read_text(encoding='utf-8'))
            current = [{'filename': pin['filename'], 'bytes': Path(pin['localPath']).stat().st_size,
                        'modifiedNs': Path(pin['localPath']).stat().st_mtime_ns} for pin in receipt['files']]
            return current == proof.get('dependencyFiles')
        except (OSError, ValueError, KeyError): return False

    def stop(self):
        self.cancelled.set()
        with self.lock: child = self.child
        if child and child.poll() is None:
            child.terminate()
        return {'stopped': True}

    def _run(self, command, request=None, pause=None, timeout=900):
        folder = self.root/'runtime-setup/computer-jobs'/uuid.uuid4().hex
        folder.mkdir(parents=True)
        log_path = folder/'runtime.log'
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                           TORCH_FORCE_WEIGHTS_ONLY_LOAD='1', TOKENIZERS_PARALLELISM='false')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
        with log_path.open('wb') as log:
            child = subprocess.Popen([str(value) for value in command], stdin=subprocess.PIPE if request is not None else subprocess.DEVNULL,
                                     stdout=subprocess.PIPE if request is not None else log, stderr=log, env=environment,
                                     creationflags=flags, start_new_session=os.name != 'nt')
            windows_job = None
            if os.name == 'nt':
                # Closing this job also stops a ternary server spawned by the worker.
                import importlib.util
                path = self.resources/'runtime-setup/setup_manager.py'
                spec = importlib.util.spec_from_file_location('computer_setup_helpers', path)
                helper = importlib.util.module_from_spec(spec)
                try: spec.loader.exec_module(helper); windows_job = helper.WindowsJob(child)
                except Exception:
                    child.terminate(); child.wait(timeout=5); raise
            with self.lock: self.child = child
            payload = json.dumps(request, ensure_ascii=True).encode('utf-8') if request is not None else None
            deadline, output = time.monotonic()+timeout, b''
            try:
                while True:
                    if self.cancelled.is_set() or pause is not None and pause.is_set():
                        raise RuntimeError('Computer-use preparation stopped; downloaded files were retained')
                    if time.monotonic() > deadline: raise RuntimeError('The local computer-use operation timed out')
                    try:
                        output, _ = child.communicate(input=payload, timeout=.25); break
                    except subprocess.TimeoutExpired: payload = None
                if request is None:
                    if child.returncode: raise RuntimeError('Computer-use dependencies could not be prepared. See '+str(log_path))
                    return {}
                if len(output) > 1024*1024: raise RuntimeError('The model response exceeded its limit')
                try: result = json.loads(output.decode('utf-8'))
                except (ValueError, UnicodeError): raise RuntimeError('The computer-use worker returned no result. See '+str(log_path)) from None
                if child.returncode or result.get('error'): raise RuntimeError(str(result.get('error') or 'Local prediction failed')[:1000])
                return result
            finally:
                if windows_job: windows_job.close()
                elif child.poll() is None and os.name != 'nt': os.killpg(child.pid, signal.SIGTERM)
                if child.poll() is None: child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
                with self.lock:
                    if self.child is child: self.child = None

    def _worker(self, entry, mode, body=None, pause=None):
        runtime, profile = profile_for(entry)
        available = _available_memory()
        if available is not None and available < 6*1024**3: raise RuntimeError('This computer-use runtime needs at least 6 GiB of free host memory')
        checked_model_files(entry, self.root, pause)
        dependency = self.dependency(profile)
        checked_model_files(dependency, self.root, pause)
        request = {'mode': mode, 'runtime': runtime, 'folder': entry['localDirectory'], 'base': dependency['localDirectory'], **(body or {})}
        if runtime == 'ternarypilot-v2':
            from managed_engines import ensure
            request['engine'] = str(ensure(self.root, 'prism-cpu-v1'))
        return self._run([self.python(profile), '-I', Path(__file__).with_name('hf_computer_worker.py')], request, pause, timeout=360)

    def prepare(self, entry, report, pause):
        runtime, profile = profile_for(entry)
        if not runtime or entry.get('status') != 'installed': raise ValueError('Install this exact reviewed checkpoint first')
        if not self.operation.acquire(blocking=False): raise RuntimeError('Another computer-use operation is running')
        self.cancelled.clear()
        try:
            report({'stage': 'preparing-base', 'detail': 'Preparing the pinned base components ('+str(round(profile['baseBytes']/1e9, 2))+' GB)'})
            self.downloader.install(self.dependency(profile), pause, report)
            if profile['recipe']:
                report({'stage': 'installing-dependencies', 'detail': 'Preparing the isolated Windows computer-use runtime'})
                self._run([_python(self.root), '-I', self.resources/'runtime-setup/setup_manager.py', '--root', self.root,
                           '--resources', self.resources, '--target', entry['id'], '--recipe-id', profile['recipe']], pause=pause, timeout=1800)
            report({'stage': 'verifying-inference', 'detail': 'Checking an actual prediction with the selected adapter and base'})
            result = self._worker(entry, 'verify', pause=pause)
            if result.get('inferenceVerified') is not True: raise RuntimeError('The model did not produce a verified prediction')
            receipt = json.loads(Path(self.dependency(profile)['receiptPath']).read_text(encoding='utf-8'))
            return {'inferenceVerified': True, 'revision': entry['revision'], 'workerVersion': VERSION, 'verifiedAt': time.time(),
                    'dependencyReceiptSha256': _hash(self.dependency(profile)['receiptPath']),
                    'dependencyFiles': [{'filename': pin['filename'], 'bytes': Path(pin['localPath']).stat().st_size,
                                         'modifiedNs': Path(pin['localPath']).stat().st_mtime_ns} for pin in receipt['files']],
                    'sampleSha256': hashlib.sha256(str(result.get('text', result.get('action'))).encode()).hexdigest()}
        finally: self.operation.release()

    def start(self, entry, body):
        runtime, profile = profile_for(entry)
        if not runtime or not entry.get('specialistReady'): raise ValueError('Prepare this model runtime before requesting a prediction')
        body = request_values(body, runtime)
        if not self.operation.acquire(blocking=False): raise RuntimeError('Another computer-use prediction is running')
        self.cancelled.clear()
        identity = uuid.uuid4().hex
        with self.lock: self.job = {'id': identity, 'modelId': entry['id'], 'status': 'running', 'stage': 'Loading and running the local model', 'startedAt': time.time()}
        def work():
            start = time.monotonic()
            try:
                result = self._worker(entry, 'predict', body)
                value = {'id': identity, 'modelId': entry['id'], 'status': 'complete', 'result': result, 'elapsedSeconds': time.monotonic()-start}
            except Exception as error:
                value = {'id': identity, 'modelId': entry['id'], 'status': 'failed', 'error': str(error)[:1000], 'elapsedSeconds': time.monotonic()-start}
            finally:
                with self.lock: self.job = value
                self.operation.release()
        threading.Thread(target=work, daemon=True).start()
        return self.status()

    def status(self):
        with self.lock: return dict(self.job) if self.job else {'status': 'idle'}
