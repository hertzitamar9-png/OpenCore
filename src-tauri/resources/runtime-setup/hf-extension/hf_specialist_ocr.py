"""Managed adapter for the pinned Unlimited OCR checkpoint, separate from text chat."""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

MODEL_ID = 'hf-7baefbb2d1bf1bedf992295b'
REPOSITORY = 'baidu/Unlimited-OCR-2'
REVISION = '9cf077c4b206e0e2a31879df937b0b0bcba3661c'
RECIPE = 'unlimited-ocr-2-v1'
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def supports(entry):
    return (entry.get('id'), entry.get('repository'), entry.get('revision')) == (MODEL_ID, REPOSITORY, REVISION)


def decode_payload(value):
    if not isinstance(value, str) or not value or len(value) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError('Choose an image up to 5 MB')
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('The image data is invalid') from None
    if not 0 < len(raw) <= MAX_IMAGE_BYTES:
        raise ValueError('Choose an image up to 5 MB')
    return raw


def readable_text(value):
    value = re.sub(r'<BLOCK>.*?<FORK>', '\n', str(value), flags=re.DOTALL)
    value = re.sub(r'<[^>]+>', '', value)
    return '\n'.join(line.strip() for line in value.splitlines() if line.strip())


class OCRManager:
    def __init__(self, root, resources):
        self.root, self.resources = Path(root).resolve(), Path(resources).resolve()
        self.lock, self.operation = threading.RLock(), threading.Lock()
        self.child, self.cancelled = None, threading.Event()
        self.job = None

    def python(self):
        return self.root / 'runtime-setup' / 'environments' / RECIPE / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def dependencies_ready(self):
        try:
            manifest = json.loads((self.resources / 'runtime-setup' / 'recipes.json').read_text(encoding='utf-8'))
            recipe = next(item for item in manifest['recipes'] if item['id'] == RECIPE and MODEL_ID in item['modelIds'])
            fingerprint = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            receipt = json.loads((self.root / 'runtime-setup' / 'receipts' / (MODEL_ID + '.json')).read_text(encoding='utf-8'))
            hardware = receipt.get('hardware', {})
            return bool(receipt.get('schema') == 1 and receipt.get('targetId') == MODEL_ID and receipt.get('recipeFingerprint') == fingerprint
                        and receipt.get('dependenciesVerified') is True and hardware.get('cudaAvailable') is True
                        and hardware.get('kernelVerified') is True and Path(receipt.get('python', '')).resolve() == self.python().resolve()
                        and self.python().is_file())
        except (OSError, ValueError, TypeError, KeyError, StopIteration):
            return False

    def ready(self, entry, proof):
        return bool(supports(entry) and entry.get('status') == 'installed' and isinstance(proof, dict)
                    and proof.get('inferenceVerified') is True and proof.get('revision') == REVISION
                    and proof.get('workerVersion') == 1 and self.dependencies_ready())

    def active(self):
        return self.operation.locked()

    def stop(self):
        self.cancelled.set()
        with self.lock:
            child = self.child
        if child and child.poll() is None:
            self._terminate(child)

    @staticmethod
    def _terminate(child):
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=10)
            else:
                child.terminate()
        except (OSError, subprocess.TimeoutExpired):
            try:
                child.kill()
            except OSError:
                pass

    def _spawn(self, command, *, request=None, timeout=1800):
        if self.cancelled.is_set():
            raise RuntimeError('OCR operation stopped')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONIOENCODING='utf-8')
        child = subprocess.Popen([str(x) for x in command], stdin=subprocess.PIPE if request is not None else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE if request is not None else subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=flags, env=env)
        with self.lock:
            self.child = child
        try:
            payload = json.dumps(request, ensure_ascii=True).encode('utf-8') if request is not None else None
            try:
                output, _ = child.communicate(input=payload, timeout=timeout)
            except subprocess.TimeoutExpired:
                self._terminate(child)
                child.communicate()
                raise RuntimeError('The OCR operation timed out') from None
            if self.cancelled.is_set():
                raise RuntimeError('OCR operation stopped')
            if request is None:
                if child.returncode:
                    raise RuntimeError('The isolated OCR dependencies could not be prepared; retry installation')
                return {}
            try:
                result = json.loads(output.decode('utf-8'))
            except (ValueError, UnicodeError, AttributeError):
                raise RuntimeError('The local OCR worker did not return a result') from None
            if child.returncode or result.get('error'):
                raise RuntimeError(str(result.get('error') or 'The local OCR worker failed')[:500])
            return result
        finally:
            with self.lock:
                if self.child is child:
                    self.child = None

    def _worker(self, entry, mode, image=None):
        from hf_runtime import _available_memory
        available = _available_memory()
        if available is not None and available < 4 * 1024**3:
            raise RuntimeError('Unlimited OCR needs at least 4 GiB of free system memory')
        script = Path(__file__).with_name('hf_specialist_ocr_worker.py')
        request = {'mode': mode, 'folder': entry['localDirectory']}
        if image is not None:
            request['image'] = base64.b64encode(image).decode('ascii')
        return self._spawn([self.python(), '-I', script], request=request, timeout=300)

    def prepare(self, entry, report, pause):
        if not supports(entry) or entry.get('status') != 'installed':
            raise ValueError('The pinned OCR checkpoint must be downloaded first')
        if not self.operation.acquire(blocking=False):
            raise RuntimeError('Another OCR operation is running')
        self.cancelled.clear()
        try:
            from hf_runtime import checked_model_files, _python
            report({'stage': 'verifying-files', 'detail': 'Verifying the downloaded OCR checkpoint'})
            checked_model_files(entry, self.root, pause)
            if pause.is_set():
                raise RuntimeError('OCR preparation stopped')
            if not self.dependencies_ready():
                report({'stage': 'installing-dependencies', 'detail': 'Installing the isolated OCR and CUDA runtime'})
                helper = self.resources / 'runtime-setup' / 'setup_manager.py'
                self._spawn([_python(self.root), '-I', helper, '--root', self.root, '--resources', self.resources,
                             '--target', MODEL_ID, '--recipe-id', RECIPE])
            if pause.is_set():
                raise RuntimeError('OCR preparation stopped')
            report({'stage': 'verifying-inference', 'detail': 'Testing local OCR on sample images'})
            result = self._worker(entry, 'verify')
            if result.get('inferenceVerified') is not True:
                raise RuntimeError('The OCR model did not pass its verification images')
            return {**result, 'revision': REVISION, 'workerVersion': 1, 'verifiedAt': time.time()}
        finally:
            self.operation.release()

    def start(self, entry, encoded):
        image = decode_payload(encoded)
        if not supports(entry) or not entry.get('specialistReady'):
            raise ValueError('Prepare the OCR runtime before using this model')
        if not self.operation.acquire(blocking=False):
            raise RuntimeError('Another OCR operation is running')
        self.cancelled.clear()
        identity = uuid.uuid4().hex
        with self.lock:
            self.job = {'id': identity, 'status': 'running'}
        def work():
            try:
                result = self._worker(entry, 'recognize', image)
                value = {'id': identity, 'status': 'complete', 'text': result.get('text', ''), 'raw': result.get('raw', '')}
            except Exception as error:
                value = {'id': identity, 'status': 'failed', 'error': str(error)[:500]}
            finally:
                with self.lock:
                    self.job = value
                self.operation.release()
        threading.Thread(target=work, daemon=True).start()
        return {'id': identity, 'status': 'running'}

    def status(self):
        with self.lock:
            return dict(self.job) if self.job else {'status': 'idle'}
