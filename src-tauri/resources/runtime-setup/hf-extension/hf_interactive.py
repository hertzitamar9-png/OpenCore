"""Owned, offline Haru audio and deskpaw pointer workers; no remote model code is executed."""
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid
import wave

from hf_download import atomic_json, Paused
from hf_runtime import checked_model_files, _python
from managed_engines import runtime_lock

HERE = Path(__file__).resolve().parent
ENVIRONMENT = 'interactive-models-v1'
VERSION = 1
MODELS = {
    'haru': ('higurashi-ai/Haru-4B', '8d6e83bcd3701a76fc27a764965665da1df7da78'),
    'deskpaw': ('llstar/deskpaw-cua-2b', 'b36bcd79729b8a81563ead920b9bb4aff3186658'),
}


def supports(entry, kind):
    return (entry.get('repository'), entry.get('revision')) == MODELS[kind]


def validate_frame(value):
    if not isinstance(value, str) or len(value) != 8536:
        raise ValueError('Haru needs one 3200-sample, 24 kHz mono PCM16 frame')
    try: raw = base64.b64decode(value, validate=True)
    except ValueError: raise ValueError('Invalid audio frame') from None
    if len(raw) != 6400: raise ValueError('Invalid audio frame length')
    return raw


def validate_reference(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 8_000_000:
        raise ValueError('Choose a WAV reference voice, 3–30 seconds long')
    try:
        raw = base64.b64decode(value, validate=True)
        with wave.open(io.BytesIO(raw), 'rb') as clip:
            seconds = clip.getnframes() / clip.getframerate()
            if not (3 <= seconds <= 30 and clip.getnchannels() in (1, 2) and clip.getsampwidth() == 2
                    and 8000 <= clip.getframerate() <= 48000 and clip.getcomptype() == 'NONE'):
                raise ValueError()
            if len(clip.readframes(clip.getnframes())) != clip.getnframes() * clip.getnchannels() * 2:
                raise ValueError()
    except (ValueError, wave.Error, EOFError, ZeroDivisionError):
        raise ValueError('Choose an uncompressed PCM16 WAV reference voice, 3–30 seconds long') from None
    return value


def screen_point(scores, width, height, rows, columns):
    if (type(rows) is not int or type(columns) is not int or min(rows, columns, width, height) <= 0
            or len(scores) != rows * columns or not scores or not all(math.isfinite(float(v)) for v in scores)):
        raise ValueError('The pointer head returned an invalid patch grid')
    index = max(range(len(scores)), key=scores.__getitem__)
    x, y = (index % columns + .5) / columns, (index // columns + .5) / rows
    return {'x': min(width - 1, int(x * width)), 'y': min(height - 1, int(y * height)),
            'normalizedX': x, 'normalizedY': y}


class InteractiveManager:
    def __init__(self, root, resources):
        self.root, self.resources = Path(root).resolve(), Path(resources).resolve()
        self.here = self.resources / 'runtime-setup/hf-extension'
        self.directory = self.root / 'runtime-setup/interactive'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.environment = self.root / 'runtime-setup/environments' / ENVIRONMENT
        self.python = self.environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        self.operation, self.exchange_lock, self.state_lock = threading.Lock(), threading.Lock(), threading.RLock()
        self.child, self.session_id, self.messages, self.log = None, None, None, None
        self.current = {'status': 'idle'}
        self.cancelled = threading.Event()
        self.cancelled_sessions = set()

    def _digest(self, paths):
        value = hashlib.sha256()
        for path in sorted(paths):
            value.update(str(path.relative_to(self.here)).encode())
            value.update(path.read_bytes().replace(b'\r\n', b'\n'))
        return value.hexdigest()

    def identity(self):
        return self._digest([self.here / 'interactive_worker.py', self.here / 'hf_interactive.py',
                             self.here / 'deskpaw_vendor.py', self.here / 'interactive-python-lock.txt',
                             *self.here.glob('haru/**/*.py'), *self.here.glob('haru/assets/*.json')])

    def _environment_receipt(self): return self.directory / 'environment.json'

    def dependencies_ready(self):
        try:
            receipt = json.loads(self._environment_receipt().read_bytes())
            return (self.python.is_file() and receipt.get('environment') == ENVIRONMENT
                    and receipt.get('lockSha256') == self._digest([self.here / 'interactive-python-lock.txt']))
        except (OSError, ValueError): return False

    def ensure_environment(self, report=lambda value: None, pause=None):
        with runtime_lock(self.directory / 'environment.lock'):
            if self.dependencies_ready(): return
            commands = []
            if not self.python.is_file():
                commands.append([_python(self.root), '-I', '-m', 'venv', str(self.environment)])
            commands.append([str(self.python), '-I', '-m', 'pip', 'install', '--progress-bar', 'off', '--require-hashes',
                             '--no-deps', '--extra-index-url', 'https://download.pytorch.org/whl/cu128',
                             '-r', str(self.here / 'interactive-python-lock.txt')])
            log = self.directory / 'environment.log'
            report({'stage': 'installing-dependencies', 'detail': 'Preparing the isolated Haru/deskpaw Python and CUDA runtime'})
            for command in commands:
                with log.open('ab') as output:
                    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    deadline = time.monotonic() + 5400
                    try:
                        while child.poll() is None:
                            if self.cancelled.is_set() or pause and pause.is_set(): raise Paused('Runtime setup paused; files retained')
                            if time.monotonic() > deadline: raise RuntimeError('Interactive runtime setup timed out')
                            time.sleep(.2)
                    finally:
                        if child.poll() is None: child.terminate(); child.wait(timeout=15)
                if child.returncode: raise RuntimeError('Interactive runtime setup failed. See ' + str(log))
            probe = subprocess.run([str(self.python), '-I', '-c', 'import torch,transformers,accelerate,soundfile,scipy,PIL;print(torch.__version__,transformers.__version__)'],
                                   capture_output=True, text=True, timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if probe.returncode: raise RuntimeError('Interactive runtime import check failed: ' + probe.stderr[-1200:])
            atomic_json(self._environment_receipt(), {'environment': ENVIRONMENT,
                        'lockSha256': self._digest([self.here / 'interactive-python-lock.txt']), 'versions': probe.stdout.strip()})

    def _fingerprint(self, entry):
        folder = Path(entry.get('localDirectory') or self.root / 'models/hub' / entry['id']).resolve()
        expected = (self.root / 'models/hub' / entry['id']).resolve()
        if folder != expected: raise ValueError('The checkpoint is outside its managed directory')
        receipt = folder / 'install-receipt.json'
        return {'receipt': hashlib.sha256(receipt.read_bytes()).hexdigest(),
                'files': [(p['filename'], (folder / p['filename']).stat().st_size,
                           (folder / p['filename']).stat().st_mtime_ns) for p in entry['files']]}

    def ready(self, entry, proof):
        if not isinstance(proof, dict) or entry.get('status') != 'installed': return False
        try:
            # JSON round trips turn tuples into lists.
            fingerprint = json.loads(json.dumps(self._fingerprint(entry)))
            return (proof.get('workerVersion') == VERSION and proof.get('revision') == entry['revision']
                    and proof.get('inferenceVerified') is True and proof.get('runtimeReady') is True
                    and proof.get('workerSha256') == self.identity() and proof.get('modelFingerprint') == fingerprint
                    and self.dependencies_ready())
        except (OSError, ValueError, KeyError): return False

    def _launch(self, entry, kind, reference=None, persona='', verify=False, session_id=None):
        if verify or not self.ready(entry, entry.get('runtimePreparation')):
            checked_model_files(entry, self.root, self.cancelled)
        if self.cancelled.is_set() or session_id is not None and self.session_id != session_id:
            raise RuntimeError('The call was cancelled while its model was loading')
        messages = queue.Queue()
        environment = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
                           PYTHONIOENCODING='utf-8', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
        with self.state_lock:
            if self.cancelled.is_set() or session_id is not None and self.session_id != session_id:
                raise RuntimeError('This call has ended')
            log = (self.directory / (kind + '-worker.log')).open('ab')
            try:
                child = subprocess.Popen([str(self.python), '-I', str(self.here / 'interactive_worker.py')],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=environment,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0))
            except Exception:
                log.close()
                raise
            self.child, self.messages, self.log = child, messages, log
        def read():
            try:
                while line := child.stdout.readline(2 * 1024 * 1024 + 1):
                    if len(line) > 2 * 1024 * 1024: raise ValueError('Worker response exceeded its limit')
                    messages.put(json.loads(line))
            except Exception as error: messages.put({'error': str(error)})
            finally: messages.put({'error': 'The model worker stopped. See ' + str(self.directory / (kind + '-worker.log'))})
        threading.Thread(target=read, daemon=True).start()
        return self._exchange({'command': 'load', 'kind': kind, 'folder': entry.get('localDirectory') or str(self.root / 'models/hub' / entry['id']),
                               'reference': reference, 'persona': persona, 'verify': verify}, timeout=180, session_id=session_id)

    def _exchange(self, request, timeout=120, session_id=None):
        with self.exchange_lock:
            if session_id is not None and self.session_id != session_id: raise RuntimeError('This call has ended')
            child, messages = self.child, self.messages
            if child is None or child.poll() is not None: raise RuntimeError('The model is no longer running')
            child.stdin.write((json.dumps(request, ensure_ascii=True) + '\n').encode()); child.stdin.flush()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.cancelled.is_set() or self.child is not child or session_id is not None and self.session_id != session_id:
                    raise RuntimeError('The model operation stopped')
                try: result = messages.get(timeout=.2)
                except queue.Empty: continue
                if result.get('error'): raise RuntimeError(str(result['error'])[:1600])
                return result
            raise RuntimeError('The model did not finish within its time limit')

    def prepare(self, entry, kind, report=lambda value: None, pause=None):
        if not supports(entry, kind) or entry.get('status') != 'installed': raise ValueError('Install the exact pinned model first')
        if not self.operation.acquire(False): raise RuntimeError('Hang up the live call or wait for the current pointer prediction')
        self.cancelled.clear()
        try:
            self.ensure_environment(report, pause)
            report({'stage': 'verifying-inference', 'detail': 'Loading and testing real ' + kind + ' model output'})
            def watch():
                while not finished.wait(.2):
                    if pause and pause.is_set(): self.cancelled.set(); self._stop_child(); return
            finished = threading.Event(); threading.Thread(target=watch, daemon=True).start()
            try:
                loaded = self._launch(entry, kind, verify=True)
                result = self._exchange({'command': 'verify'}, timeout=180)
            finally: finished.set()
            if not result.get('inferenceVerified'): raise RuntimeError('The model did not return valid output')
            return {'workerVersion': VERSION, 'revision': entry['revision'], 'workerSha256': self.identity(),
                    'modelFingerprint': json.loads(json.dumps(self._fingerprint(entry))), 'runtimeReady': True,
                    'inferenceVerified': True, 'verifiedAt': time.time(), 'device': loaded.get('device'),
                    'offloadedLayers': loaded.get('offloadedLayers', 0), 'sample': result}
        finally:
            self._stop_child(); self.operation.release()

    def status(self):
        with self.state_lock: return dict(self.current)

    def start(self, entry, body):
        identity = body.get('sessionId') or uuid.uuid4().hex
        try: uuid.UUID(identity)
        except (ValueError, TypeError, AttributeError): raise ValueError('Choose a valid call identity') from None
        with self.state_lock:
            if identity in self.cancelled_sessions: raise ValueError('This call was cancelled before it started')
        reference = validate_reference(body.get('reference'))
        persona = body.get('persona', '')
        if not isinstance(persona, str) or len(persona) > 2000: raise ValueError('Keep the character description below 2,000 characters')
        if not supports(entry, 'haru') or not self.ready(entry, entry.get('runtimePreparation')):
            raise ValueError('Prepare and verify Haru in Models before starting a call')
        if not self.operation.acquire(False): raise RuntimeError('Another interactive model is running')
        with self.state_lock:
            if identity in self.cancelled_sessions:
                self.operation.release()
                raise ValueError('This call was cancelled before it started')
            self.cancelled.clear(); self.session_id = identity
            self.current = {'sessionId': identity, 'status': 'loading', 'startedAt': time.time()}
        def load():
            try:
                result = self._launch(entry, 'haru', reference, persona, session_id=identity)
                with self.state_lock:
                    if self.session_id == identity: self.current = {**self.current, **result, 'status': 'ready'}
            except Exception as error:
                with self.state_lock:
                    if self.session_id == identity: self.current.update(status='failed', error=str(error))
                self.stop(identity)
        threading.Thread(target=load, daemon=True).start()
        return self.status()

    def step(self, body):
        if body.get('sessionId') != self.session_id or self.status().get('status') != 'ready':
            raise ValueError('This live call is no longer active')
        validate_frame(body.get('audio'))
        result = self._exchange({'command': 'frame', 'audio': body['audio']}, timeout=45, session_id=body['sessionId'])
        with self.state_lock: self.current.update(lastFrame=result.get('index'), frameSeconds=result.get('seconds'))
        return result

    def predict(self, entry, body):
        if not supports(entry, 'deskpaw') or not self.ready(entry, entry.get('runtimePreparation')):
            raise ValueError('Prepare and verify deskpaw first')
        if not self.operation.acquire(False): raise RuntimeError('Hang up Haru before running a pointer prediction')
        self.cancelled.clear()
        try:
            self._launch(entry, 'deskpaw')
            return self._exchange({'command': 'predict', **body}, timeout=180)
        finally: self._stop_child(); self.operation.release()

    def _stop_child(self):
        with self.state_lock:
            child, log = self.child, self.log
            self.child, self.messages, self.log = None, None, None
        self._dispose_child(child, log)

    @staticmethod
    def _dispose_child(child, log):
        if child:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=10)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
            for stream in (child.stdin, child.stdout):
                if stream:
                    try: stream.close()
                    except OSError: pass
        if log: log.close()

    def stop(self, session_id=None):
        with self.state_lock:
            if session_id is not None:
                if len(self.cancelled_sessions) >= 128: self.cancelled_sessions.clear()
                self.cancelled_sessions.add(session_id)
            if session_id is not None and session_id != self.session_id: return {'stopped': False}
            live = self.session_id is not None
            self.cancelled.set()
            child, log = self.child, self.log
            self.child, self.messages, self.log = None, None, None
            self.session_id = None
            if self.current.get('status') != 'failed': self.current = {'status': 'idle'}
        try: self._dispose_child(child, log)
        finally:
            if live: self.operation.release()
        return {'stopped': True}
