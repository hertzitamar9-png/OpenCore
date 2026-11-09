"""Local Phonon-2.5 dictation, independent of the Phonon-2 Python environment."""
import base64
import binascii
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import threading
import time
from urllib.request import Request, urlopen
import wave

from hf_download import Paused, atomic_json, managed_path

SOURCE_REVISION = '2eac2e77d9c7ef8899f7a86a46e693ac5ac1a5cd'
MODEL_REVISION = '07a08a3a405d20c5434b9a1442f6e6cf571f57ad'
QUANTS = {'packed', 'q4_k', 'q5_k', 'q6_k', 'q8_0'}
MAX_AUDIO_BYTES = 32 * 1024 * 1024


def supports(entry):
    return (entry.get('speechRuntime') == 'parakeet-phonon25-v1'
            and entry.get('repository') == 'mudler/parakeet-cpp-gguf'
            and entry.get('revision') == MODEL_REVISION
            and entry.get('speechQuantization') in QUANTS)


def decode_audio(value):
    if not isinstance(value, str) or len(value) > (MAX_AUDIO_BYTES + 2) // 3 * 4:
        raise ValueError('The recording exceeds the supported size')
    try:
        data = base64.b64decode(value, validate=True)
        with wave.open(io.BytesIO(data), 'rb') as audio:
            if (audio.getcomptype() != 'NONE' or audio.getsampwidth() != 2
                    or audio.getnchannels() != 1 or audio.getframerate() != 16000
                    or not 0 < audio.getnframes() <= 16000 * 600):
                raise ValueError('Use a mono 16 kHz PCM WAV recording of up to ten minutes')
            if len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2:
                raise ValueError('The WAV recording is incomplete')
    except (binascii.Error, wave.Error, EOFError):
        raise ValueError('The microphone must provide a valid PCM WAV recording') from None
    return data


def file_hash(path, pause=None):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        while block := source.read(1024 * 1024):
            if pause is not None and pause.is_set():
                raise Paused('Speech preparation paused')
            result.update(block)
    return result.hexdigest()


class SpeechManager:
    def __init__(self, root, resources, get_entry):
        self.root, self.resources, self.get_entry = Path(root), Path(resources), get_entry
        self.settings_path = managed_path(self.root, 'speech', 'phonon25-settings.json')
        self.settings = {'modelId': None, 'enabled': False, 'idleMode': 'ram'}
        try:
            saved = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if isinstance(saved, dict):
                self.settings.update({key: saved[key] for key in self.settings if key in saved})
        except (OSError, ValueError):
            pass
        self.lock, self.operation = threading.RLock(), threading.Lock()
        self.child, self.port, self.session_id = None, None, None
        self.phase, self.started_at, self.cold_ms = 'idle', None, None
        self.engine_fingerprint = None

    def selected(self, identity):
        with self.lock:
            return self.settings['modelId'] == identity

    def _engine(self):
        folder = self.resources / 'speech/parakeet'
        manifest = json.loads((folder / 'engine.json').read_text(encoding='utf-8'))
        if (manifest.get('sourceRevision') != SOURCE_REVISION or manifest.get('f16Quantizer') != 1
                or manifest.get('build') != 'gnu-simd-v1'):
            raise ValueError('Update OpenCore to install the compatible Phonon-2.5 speech engine')
        names = ('parakeet-cli.exe', 'parakeet-server.exe', 'verification.wav')
        fingerprint = tuple((name, (folder / name).stat().st_size, (folder / name).stat().st_mtime_ns)
                            for name in names)
        if fingerprint != self.engine_fingerprint:
            if any(file_hash(folder / name) != manifest['files'].get(name) for name in names):
                raise ValueError('The managed speech engine failed verification. Reinstall the OpenCore update')
            self.engine_fingerprint = fingerprint
        return folder

    def _model_path(self, entry):
        folder = managed_path(self.root, 'models', 'hub', entry['id'])
        filename = ('phonon-2.5-' + entry['speechConversion'] + '.gguf'
                    if entry.get('speechConversion') else
                    next(pin['filename'] for pin in entry['files'] if pin['role'] == 'model'))
        return managed_path(folder, filename)

    def ready(self, entry, proof=None):
        if not supports(entry) or entry.get('status') != 'installed':
            return False
        proof = proof or entry.get('runtimePreparation') or {}
        try:
            model = self._model_path(entry)
            stat = model.stat()
            self._engine()
            return (proof.get('runtimeReady') is True and proof.get('inferenceVerified') is True
                    and proof.get('engineRevision') == SOURCE_REVISION
                    and proof.get('receiptRevision') == MODEL_REVISION
                    and proof.get('quantization') == entry['speechQuantization']
                    and proof.get('modelBytes') == stat.st_size
                    and proof.get('modelModifiedNs') == stat.st_mtime_ns)
        except (OSError, ValueError, KeyError, StopIteration):
            return False

    def _run(self, command, pause, timeout=180):
        child = subprocess.Popen([str(value) for value in command], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + timeout
        try:
            while True:
                if pause.is_set():
                    raise Paused('Speech preparation paused; downloaded files were retained')
                if time.monotonic() > deadline:
                    raise ValueError('The speech decoder exceeded its preparation deadline')
                try:
                    stdout, stderr = child.communicate(timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if child.returncode:
                raise ValueError('The Phonon-2.5 decoder could not finish its execution test')
            return stdout.decode('utf-8'), stderr.decode('utf-8', errors='replace')
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)

    def prepare(self, entry, report, pause):
        if not supports(entry):
            raise ValueError('This package is not a reviewed Phonon-2.5 quantization')
        with self.operation:
            engine = self._engine()
            model = self._model_path(entry)
            if self.ready(entry):
                return entry['runtimePreparation']
            if entry.get('speechConversion'):
                source = managed_path(model.parent, next(pin['filename'] for pin in entry['files'] if pin['role'] == 'model'))
                report({'stage': 'preparing-quantization', 'detail': 'Preparing ' + entry['quantization'] + ' from the verified Phonon-2.5 checkpoint', 'bytesTotal': 0})
                temporary = model.with_suffix('.preparing.gguf')
                try:
                    output, _ = self._run([engine / 'parakeet-cli.exe', 'quantize', source, temporary,
                                           entry['speechConversion']], pause)
                    count = re.search(r'quantized (\d+) tensor', output)
                    if not count or int(count[1]) == 0 or temporary.stat().st_size >= source.stat().st_size:
                        raise ValueError('The speech quantizer did not produce quantized model weights')
                    os.replace(temporary, model)
                finally:
                    temporary.unlink(missing_ok=True)
            report({'stage': 'verifying-speech', 'detail': 'Testing Phonon-2.5 with a bundled speech recording', 'bytesTotal': 0})
            output, _ = self._run([engine / 'parakeet-cli.exe', 'transcribe', '--model', model,
                                   '--input', engine / 'verification.wav', '--threads', self._threads(), '--json'], pause)
            result = json.loads(output)
            if 'quick brown fox' not in result.get('text', '').lower():
                raise ValueError('Phonon-2.5 did not recognize the speech verification recording')
            stat = model.stat()
            return {'runtimeReady': True, 'inferenceVerified': True, 'receiptRevision': MODEL_REVISION,
                    'engineRevision': SOURCE_REVISION, 'runtimeBackend': 'cpu',
                    'quantization': entry['speechQuantization'], 'modelBytes': stat.st_size,
                    'modelModifiedNs': stat.st_mtime_ns, 'modelSha256': file_hash(model, pause)}

    @staticmethod
    def _threads():
        return min(4, os.cpu_count() or 2)

    def status(self):
        with self.lock:
            settings = dict(self.settings)
            worker_ready = self.child is not None and self.child.poll() is None and self.phase != 'loading'
        if not settings['modelId']:
            return None
        try:
            entry = self.get_entry(settings['modelId'])
            installed = self.ready(entry)
        except ValueError:
            installed = False
        return dict(settings, installed=installed, workerReady=worker_ready,
                    coldStartMs=self.cold_ms, warmWakeMs=0 if worker_ready else None,
                    phase=self.phase if settings['enabled'] else 'off', runtimePrecision='original',
                    loadingElapsedMs=int((time.monotonic() - self.started_at) * 1000) if self.phase == 'loading' and self.started_at else None,
                    runtimeDescription='Phonon-2.5 local speech recognition')

    def select(self, identity):
        if identity:
            entry = self.get_entry(identity)
            if not self.ready(entry):
                raise ValueError('Finish installing this Phonon-2.5 quantization before using it for dictation')
        self._stop()
        with self.lock:
            self.settings.update(modelId=identity, enabled=bool(identity))
            self.session_id = None
            atomic_json(self.settings_path, self.settings)
        return self.status()

    def configure(self, key, value):
        if not (key == 'enabled' and type(value) is bool or key == 'idleMode' and value in ('cold', 'ram')):
            raise ValueError('Invalid speech setting')
        if key == 'enabled' and not value or key == 'idleMode' and value == 'cold':
            self._stop()
        with self.lock:
            self.settings[key] = value
            atomic_json(self.settings_path, self.settings)
        return self.status()

    def start(self, session_id):
        if not isinstance(session_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', session_id):
            raise ValueError('Invalid microphone session')
        status = self.status()
        if not status or not status['enabled']:
            raise ValueError('Enable speech to text in Models before using the microphone')
        if not status['installed']:
            raise ValueError('Finish installing the selected Phonon-2.5 quantization in Models')
        entry = self.get_entry(status['modelId'])
        with self.operation:
            with self.lock:
                if self.session_id and self.session_id != session_id:
                    raise ValueError('Finish the current microphone recording first')
                self.session_id = session_id
                if self.child and self.child.poll() is None:
                    return session_id
                self.phase, self.started_at = 'loading', time.monotonic()
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', 0))
                    self.port = probe.getsockname()[1]
                command = [self._engine() / 'parakeet-server.exe', '--model', self._model_path(entry),
                           '--host', '127.0.0.1', '--port', str(self.port), '--threads', str(self._threads())]
                self.child = subprocess.Popen([str(value) for value in command], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                child, port = self.child, self.port
            try:
                while time.monotonic() - self.started_at < 40:
                    with self.lock:
                        if self.session_id != session_id:
                            raise ValueError('Microphone recording cancelled')
                    if child.poll() is not None:
                        raise ValueError('The Phonon-2.5 speech worker stopped during loading')
                    try:
                        with urlopen('http://127.0.0.1:' + str(port) + '/health', timeout=.5) as response:
                            if json.load(response).get('status') == 'ok':
                                with self.lock:
                                    self.cold_ms = int((time.monotonic() - self.started_at) * 1000)
                                    self.phase = 'recording'
                                return session_id
                    except OSError:
                        time.sleep(.1)
                raise ValueError('The Phonon-2.5 speech worker took too long to load')
            except Exception:
                self.cancel(session_id)
                raise

    def transcribe(self, session_id, audio):
        data = decode_audio(audio)
        with self.operation:
            with self.lock:
                if not session_id or session_id != self.session_id or not self.child or self.child.poll() is not None:
                    raise ValueError('This microphone session is no longer active. Record again')
                self.phase = 'transcribing'
                port = self.port
            boundary = 'OpenCore' + secrets.token_hex(16)
            body = ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="recording.wav"\r\n'
                    'Content-Type: audio/wav\r\n\r\n').encode() + data + ('\r\n--' + boundary + '--\r\n').encode()
            request = Request('http://127.0.0.1:' + str(port) + '/v1/audio/transcriptions', data=body,
                              headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
            try:
                with urlopen(request, timeout=180) as response:
                    result = json.loads(response.read(1024 * 1024))
                with self.lock:
                    if self.session_id != session_id:
                        raise ValueError('Microphone recording cancelled')
                return {'text': str(result.get('text', '')).strip(), 'language': 'auto'}
            except OSError:
                raise ValueError('The speech worker could not finish this recording. Record again') from None
            finally:
                with self.lock:
                    if self.session_id == session_id:
                        self.phase = 'recording'

    def cancel(self, session_id):
        with self.lock:
            if self.session_id != session_id:
                return
            self.session_id = None
            stop = self.settings['idleMode'] == 'cold' or self.phase in ('loading', 'transcribing')
            self.phase = 'idle'
            if stop:
                self._stop()

    def _stop(self):
        with self.lock:
            child, self.child = self.child, None
            self.phase = 'idle'
            self.session_id = None
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=10)

    def close(self):
        self._stop()
