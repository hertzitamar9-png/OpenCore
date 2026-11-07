"""Owned local inference workers for Hub imports; downloaded Python is never loaded."""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
if __name__ == '__main__':
    # -I workers resolve only these installed, app-owned sibling modules.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_modes import mode_context, supports_text_generation, NATIVE_TARGET, ECHO_TARGET


def _hash(path, cancel=None):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            if cancel and cancel.is_set():
                raise RuntimeError('Runtime preparation paused; downloaded files were retained.')
            value.update(chunk)
    return value.hexdigest()


def _inside(path, parent):
    def normalized(value):
        text=str(Path(value).resolve())
        if os.name=='nt' and text.startswith('\\\\?\\UNC\\'):text='\\\\'+text[8:]
        elif os.name=='nt' and text.startswith('\\\\?\\'):text=text[4:]
        return Path(text)
    result,base = normalized(path),normalized(parent)
    if not result.is_relative_to(base) or result == base:
        raise ValueError('Model path is outside its managed directory')
    return result


def _weight_folder(entry, root):
    base = 'models/library' if (entry.get('installReceipt') or {}).get('nativeArtifactId') else 'models/hub'
    return _inside(entry.get('localDirectory', ''), Path(root) / base)


def checked_model_files(entry, root, cancel=None):
    folder = _weight_folder(entry, root)
    native = (entry.get('installReceipt') or {}).get('nativeArtifactId')
    receipt_parent = Path(root) / 'models/receipts' if native else folder
    receipt_path = _inside(entry.get('receiptPath', folder / 'install-receipt.json'), receipt_parent)
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if native:
        if receipt != [native]:
            raise ValueError('The native installation receipt no longer matches this checkpoint')
        pins = {pin['filename']: pin for pin in entry['installReceipt']['files']}
    else:
        if receipt.get('id') != entry['id'] or receipt.get('repository') != entry['repository'] or receipt.get('revision') != entry['revision']:
            raise ValueError('The installed receipt belongs to another model or revision')
        pins = {pin['filename']: pin for pin in receipt.get('files', [])}
    models = []
    for pin in entry.get('files', []):
        installed = pins.get(pin['filename'])
        if not installed or int(installed.get('bytes', -1)) != int(pin['bytes']):
            raise ValueError('The installed receipt is missing a pinned file')
        local = _inside(installed.get('localPath', folder / pin['filename']), folder)
        if local != (folder / pin['filename']).resolve() or not local.is_file() or local.stat().st_size != pin['bytes']:
            raise ValueError('An installed file changed size or location')
        expected = pin.get('sha256') or installed.get('actualSha256')
        if not expected or _hash(local, cancel) != expected:
            raise ValueError('An installed file checksum changed; reinstall this package')
        if pin.get('role') == 'model':
            models.append(local)
    if not models:
        raise ValueError('The verified package contains no model weights')
    return models


def conversation_key(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512 or any(c in value for c in ('..', '/', '\\', '\x00')):
        raise ValueError('Invalid ECHO conversation identity')
    # Preserve real OpenCore IDs, including their ':' separators, inside ECHO.
    return value


def admitted_context(requested, props):
    """Use the server's real per-slot capacity when it exposes one."""
    settings = props.get('default_generation_settings') if isinstance(props, dict) else None
    actual = settings.get('n_ctx') if isinstance(settings, dict) else None
    if type(actual) is not int or actual <= 0:
        actual = props.get('n_ctx') if isinstance(props, dict) else None
    verified = type(actual) is int and actual > 0
    context = min(requested, actual) if verified else requested
    if context < 8192:
        raise ValueError(f'This runtime provides {context:,} context tokens; the ECHO chat agent needs at least 8,192. Choose a compatible model or runtime.')
    return context, verified


def _python(root):
    for recipe in ('managed-transformers-text-v1', 'edgerock-portable-v1'):
        path = Path(root) / 'runtime-setup/environments' / recipe / 'Scripts/python.exe'
        if path.is_file():
            return str(path)
    return sys.executable


def visual_text_probe(entry):
    """Small, offline, synthetic media probe for candidate conditional-generation runtimes.

    An input model is never marked ready on a text-only smoke test. No external
    URLs or user images are fetched during this verification.
    """
    inputs=entry.get('inputModalities') or []
    adapter = (entry.get('runtime') or {}).get('adapter')
    gguf_vision = (entry.get('format') == 'gguf' and adapter == 'gguf' and 'image' in inputs
                   and any(pin.get('role') == 'companion' for pin in entry.get('files', [])))
    transformers_vision = (entry.get('format') == 'safetensors' and adapter == 'transformers'
                           and any(value in inputs for value in ('image', 'video')))
    if not (gguf_vision or transformers_vision):
        return None
    import base64
    import struct
    import zlib

    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)

    width=height=224
    scanline=b'\x00'+(b'\x32\x66\x99'*width)
    png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>2I5B',width,height,8,2,0,0,0))+
         chunk(b'IDAT',zlib.compress(scanline*height))+chunk(b'IEND',b''))
    data='data:image/png;base64,'+base64.b64encode(png).decode('ascii')
    if transformers_vision and 'video' in inputs:
        media={'type':'video','frames':[{'timestamp':0,'image_url':data},{'timestamp':1,'image_url':data}]}
        kind='video-frames'
    else:
        media={'type':'image_url','image_url':{'url':data}}
        kind='image'
    return kind,{'messages':[{'role':'user','content':[{'type':'text','text':'Describe what you see in one sentence.'},media]}],
                 'max_tokens':64,'temperature':0,'stream':False,'opencore_reasoning_effort':'off',
                 'reasoning_budget_tokens':0,'chat_template_kwargs':{'enable_thinking':False}}


def _chat_template_path(entry, folder):
    declaration = entry.get('chatTemplate')
    if declaration is None:
        return None
    try:
        from hf_catalog import safe_filename, HEX40, HEX64
        if not isinstance(declaration, dict):
            raise ValueError()
        name = safe_filename(declaration.get('filename'))
        size, digest = declaration.get('bytes'), declaration.get('sha256')
        if (declaration.get('repo') != entry['repository'] or declaration.get('revision') != entry['revision']
                or type(size) is not int or not 0 < size < 1048576
                or not isinstance(digest, str) or not HEX64.fullmatch(digest)):
            raise ValueError()
        matches = [pin for pin in entry.get('files', []) if pin.get('filename') == name]
        if len(matches) != 1:
            raise ValueError()
        pin = matches[0]
        if (pin.get('role') != 'support' or pin.get('pinned') is not True or pin.get('bytes') != size
                or pin.get('repository') != entry['repository'] or pin.get('revision') != entry['revision']):
            raise ValueError()
        blob = pin.get('gitBlobSha1')
        if pin.get('sha256'):
            if pin['sha256'] != digest:
                raise ValueError()
        elif not isinstance(blob, str) or not HEX40.fullmatch(blob):
            raise ValueError()
        if declaration.get('gitBlobSha1') != blob:
            raise ValueError()
        path = _inside(folder / name, folder)
        if path.stat().st_size != size:
            raise ValueError()
        with path.open('rb') as stream:
            raw = stream.read(1048576)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError()
        if blob and hashlib.sha1(b'blob ' + str(size).encode('ascii') + b'\0' + raw).hexdigest() != blob:
            raise ValueError()
        if '\x00' in raw.decode('utf-8', errors='strict'):
            raise ValueError()
        return path
    except (KeyError, TypeError, ValueError, OSError):
        raise ValueError('The external chat template is not a verified pinned UTF-8 support file for this model.') from None


def _bundled_cuda_available(server, resources):
    from managed_engines import cuda_driver_version
    if not Path(server).is_file() or not cuda_driver_version():
        return False
    try:
        env = dict(os.environ, PATH=str(Path(resources) / 'doucode/runtime') + os.pathsep + os.environ.get('PATH', ''))
        result = subprocess.run([str(server), '--list-devices'], cwd=str(Path(server).parent), env=env,
                                capture_output=True, text=True, timeout=8, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode == 0 and re.search(r'^\s*CUDA\d+\s*:', result.stdout, re.I | re.M) is not None
    except (OSError, subprocess.TimeoutExpired):
        return False


def runtime_plan(entry, root, resources, prefer_gpu=True, backend_override=None):
    root, resources = Path(root).resolve(), Path(resources).resolve()
    fmt = entry.get('format', '')
    quant = str(entry.get('quantization', '')).upper()
    if fmt not in ('gguf', 'safetensors'):
        raise ValueError('This format has no integrated Windows runtime adapter. The downloaded files are retained.')
    if fmt == 'safetensors' and any(name in quant for name in ('NVFP4', 'MLX', 'GPTQ', 'AWQ')):
        raise ValueError('This quantization requires a specialized runtime and compatible hardware; NVFP4 is not a plain Transformers checkpoint.')
    folder = _weight_folder(entry, root)
    models = [folder / f['filename'] for f in entry.get('files', []) if f.get('role') == 'model']
    if not models:
        raise ValueError('No model weights are present for the runtime adapter')
    context = mode_context(entry)
    if context < 8192:
        raise ValueError('This model has less than the 8,192-token context required by the chat agent. Its downloaded files are retained.')
    managed_cuda = backend_override == 'managed-cuda'
    preference = 'cuda' if managed_cuda else backend_override or (entry.get('runtimePreference', 'auto') if prefer_gpu else 'cpu')
    if fmt == 'gguf':
        from managed_engines import engine_for, executable, select_engine
        # The generic b11960 build and each publisher-specific build remain pinned
        # to their own tested llama.cpp revision.
        specialized_engine = engine_for(entry)
        base_engine = specialized_engine or 'lfm3-cpu-v1'
        bundled = root / 'runtime/llama-server.exe'
        if not bundled.is_file():bundled = resources / 'opencore/runtime/llama-server.exe'
        use_bundled_cuda = (not specialized_engine and not managed_cuda and preference in ('auto', 'cuda')
                            and _bundled_cuda_available(bundled, resources))
        publisher_engine = None if use_bundled_cuda else select_engine(base_engine, preference)
        backend = ('cuda' if use_bundled_cuda or '-cuda' in publisher_engine
                   else 'vulkan' if '-vulkan' in publisher_engine else 'cpu')
        engine = bundled if use_bundled_cuda else executable(root, publisher_engine)
        model = sorted(models, key=lambda p: p.name)[0]
        command = [str(engine), '--model', str(model), '--host', '127.0.0.1', '--port', '{port}',
                   '--ctx-size', str(context), '--threads', '2', '--threads-batch', '2',
                   '--parallel', '1', '--no-webui', '--api-key-file', '{keyfile}',
                   '--no-warmup', '--jinja', '--batch-size', '64', '--ubatch-size', '16']
        if backend == 'cpu':
            command += ['--gpu-layers', '0', '--fit', 'off', '--cache-ram', '0']
            if publisher_engine == 'edgerock-cpu-v1':
                command += ['--no-context-shift']
            # Q8A64 stored tensors require the publisher's repacked buffers.
            if publisher_engine != 'velociq8-cpu-v1':
                command.append('--no-repack')
        else:
            # llama.cpp fits layers and working buffers into available VRAM.
            # Remaining model layers execute on the CPU using system RAM.
            command += ['--gpu-layers', 'auto', '--fit', 'on', '--fit-ctx', '8192', '--log-verbosity', '4']
            if publisher_engine == 'dory-cuda13-v1':
                from managed_engines import dory_cuda_devices
                # Dory's CUDA worker can prefill a full prompt in larger
                # batches than the conservative shared CPU launch.
                command[command.index('--batch-size') + 1] = '512'
                command[command.index('--ubatch-size') + 1] = '128'
                command += ['--device', ','.join(dory_cuda_devices())]
        template = _chat_template_path(entry, folder)
        if template is not None:
            command += ['--chat-template-file', str(template)]
        companions = [folder / f['filename'] for f in entry['files'] if f.get('role') == 'companion' and f['filename'].lower().endswith('.gguf')]
        if companions:
            command += ['--mmproj', str(companions[0])]
    else:
        subdirectory=entry.get('subdirectory','')
        directory = _inside(folder / subdirectory,folder) if subdirectory else folder
        if not (directory / 'config.json').is_file():
            raise ValueError('The selected checkpoint has no model configuration')
        raw = json.loads((directory / 'config.json').read_text(encoding='utf-8'))
        quant_config = raw.get('quantization_config')
        if quant_config:
            raise ValueError('This checkpoint declares a specialized quantization runtime. A matching managed adapter must be integrated before execution.')
        from managed_engines import cuda_driver_version
        if preference not in ('auto', 'cuda', 'cpu'):
            raise ValueError('Choose Automatic, CUDA or CPU for the Transformers runtime')
        backend = 'cpu' if preference == 'cpu' else 'cuda'
        if backend == 'cuda' and cuda_driver_version() < 12000:
            raise ValueError('This Transformers runtime needs a compatible NVIDIA GPU and CUDA driver. The model files are kept. Choose CPU explicitly to run without CUDA.')
        recipe = 'managed-transformers-text-cuda-v1' if backend == 'cuda' else 'managed-transformers-text-v1'
        interpreter = root / 'runtime-setup/environments' / recipe / 'Scripts/python.exe'
        command = [str(interpreter), '-I', str(Path(__file__).resolve()), '--worker', 'text',
                   '--resources', str(resources), '--model-dir', str(directory), '--model-id', entry['id'],
                   '--context', str(context), '--port', '{port}', '--device', 'cuda-hybrid' if backend == 'cuda' else 'cpu']
    return {'command': command, 'context': context, 'operatingMode':entry.get('operatingMode','echo'),
            'requestedContextTokens':NATIVE_TARGET if entry.get('operatingMode')=='native' else ECHO_TARGET,
            'format': fmt, 'folder': str(folder), 'runtimeBackend': backend,
            **({'publisherEngine': publisher_engine} if fmt == 'gguf' and publisher_engine else {}),
            **({'engineFamily': base_engine, 'bundledCuda': use_bundled_cuda} if fmt == 'gguf' else {'recipe': recipe})}


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _available_memory():
    if os.name != 'nt':
        return None
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in
            ('totalPhysical', 'availablePhysical', 'totalPage', 'availablePage', 'totalVirtual', 'availableVirtual', 'extended')]
    data = Memory()
    data.length = ctypes.sizeof(data)
    return data.availablePhysical if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(data)) else None


def gpu_offloaded_layers(log_path):
    """Read llama.cpp's actual layer placement after the server is ready."""
    try:
        with Path(log_path).open('rb') as log:
            log.seek(max(0, log.seek(0, os.SEEK_END) - 1024 * 1024))
            matches = re.findall(rb'offloaded (\d+)/(\d+) layers to GPU', log.read())
    except OSError:
        return 0
    return int(matches[-1][0]) if matches else 0


class RuntimeManager:
    automatic_preparation=True

    def loaded(self, model_id):
        with self._state_lock:
            active = self._active
            if not active or active['id'] != model_id or any(child.poll() is not None for child in self._children):
                return None
            return active['proof']

    def loading_stage(self, model_id):
        with self._state_lock:
            return self._loading[1] if self._loading and self._loading[0] == model_id else None

    def _report_progress(self, **progress):
        with self._state_lock:
            if self._loading and progress.get('detail'):
                self._loading = (self._loading[0], progress['detail'])
        self.on_progress(**progress)

    def unload(self, model_id):
        with self._state_lock:
            if self._generation.locked() or self._preparation.locked() or self._context:
                raise RuntimeError('Finish or stop the active model request before unloading.')
            if self._active and self._active['id'] == model_id:
                self.stop()
            return {'stopped': True}

    def telemetry_source(self, model_id, conversation):
        with self._state_lock:
            active=self._active
            if not active or active['id']!=model_id:return None
            url,key=active['url'],active['key']
        from urllib.parse import quote,urlsplit
        def read():
            if active.get('mode')=='native':
                metrics=active.get('usage',{})
                value={'modelContextTokens':active['proof']['contextTokens'],'modelActiveTokens':metrics.get('prompt_tokens'),
                       'modelOutputTokens':metrics.get('completion_tokens',0),'contextMode':'native','modelSessionActive':self._generation.locked()}
                try:
                    with self._request(url+'/slots',key=key,timeout=.8) as response:slots=json.load(response)
                    if isinstance(slots,list) and slots:
                        value['modelActiveTokens']=slots[0].get('n_past') or slots[0].get('n_prompt_tokens') or value['modelActiveTokens']
                        if type(slots[0].get('n_decoded')) is int:value['modelOutputTokens']=slots[0]['n_decoded']
                except (OSError,RuntimeError,ValueError):pass
                return value
            with self._request(url+'/echo/context?conversation='+quote(conversation,safe=''),key=key,timeout=.8) as response:
                raw=response.read(256*1024+1)
            if len(raw)>256*1024:raise ValueError('Context sample too large')
            return json.loads(raw)
        return {'status':'running','profile':model_id,'echoPort':urlsplit(url).port},read

    def private_backend(self, native=None):
        with self._state_lock:
            if self._active:
                upstream = self._active.get('upstream')
                if not upstream: raise ValueError('Reload this local model to enable temporary chat')
                return upstream, self._active['key']
        if not isinstance(native, dict) or native.get('status') != 'running':
            raise ValueError('Start a local model before opening temporary chat')
        port, profile = native.get('backendPort'), str(native.get('profile') or '')
        if type(port) is not int or not 1024 <= port <= 65535 or port in (8812, 8813) or profile.startswith('api:'):
            raise ValueError('Temporary chat needs the direct local model backend, separate from saved ECHO or cloud providers')
        return 'http://127.0.0.1:' + str(port), None
    def __init__(self, root, resources):
        self.root, self.resources = Path(root).resolve(), Path(resources).resolve()
        self._children, self._logs = [], []
        self._context = None
        self._active = None
        self._loading = None
        self._memory_watchers = []
        self._state_lock = threading.RLock()
        self._generation = threading.Lock()
        self._preparation = threading.Lock()
        self._cancel = threading.Event()
        self._stop_reason = ''
        self.on_progress = lambda **values: None

    def models(self, catalog):
        values = catalog.get('models', []) if isinstance(catalog, dict) else catalog
        return {'object': 'list', 'data': [{'id': e['id'], 'object': 'model', 'owned_by': 'local-huggingface',
            'context_length': max(1, int((e.get('runtimePreparation') or {}).get('contextTokens') or mode_context(e)))} for e in values
            if supports_text_generation(e) and e.get('status') == 'installed' and (e.get('runtimeReady') or e.get('runtimePreparation', {}).get('runtimeReady'))]}

    def set_context(self, model_id, conversation_id, request_id):
        conversation_key(conversation_id)
        if not request_id or len(str(request_id)) > 512:
            raise ValueError('A chat request identity is required')
        with self._state_lock:
            if self._context and self._context['requestId'] != request_id:
                raise RuntimeError('Wait for the active local conversation to finish before starting another one')
            self._context = {'modelId': model_id, 'conversationId': conversation_id, 'requestId': request_id}
        return {'accepted': True}

    def release_context(self, request_id):
        with self._state_lock:
            if self._context and self._context['requestId'] == request_id:
                self._context = None
        return {'released': True}

    def _request(self, url, payload=None, key=None, timeout=30, extra=None):
        headers = {'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + key} if key else {}), **(extra or {})}
        request = urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            return opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode('utf-8', 'replace')
            raise RuntimeError(f'The local runtime returned HTTP {error.code}: {detail}') from None

    def _spawn(self, command, environment, log_path, cwd=None):
        log = Path(log_path).open('ab', buffering=0)
        self._logs.append(log)
        if os.name == 'nt':
            ctypes.windll.kernel32.SetErrorMode(0x8003)  # Report loader errors through exit status, not a blocking dialog.
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        if cwd is not None:
            flags |= getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment,
            creationflags=flags, cwd=str(cwd or self.root))
        self._children.append(child)
        if cwd is not None:
            def protect_available_memory():
                while child.poll() is None and not self._cancel.wait(1):
                    available = _available_memory()
                    if available is not None and available < 3 * 1024 ** 3:
                        self._stop_reason = 'Available host memory became too low. The managed runtime was stopped; choose a smaller quantization.'
                        self._cancel.set()
                        try:
                            child.terminate()
                        except OSError:
                            pass
                        return
            watcher = threading.Thread(target=protect_available_memory, daemon=True)
            watcher.start()
            self._memory_watchers.append(watcher)
        return child

    def _wait_health(self, child, url, key, log_path, deadline=300):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            if self._cancel.is_set():
                raise RuntimeError(self._stop_reason or 'Runtime preparation paused; downloaded files were retained.')
            available = _available_memory()
            if available is not None and available < 3 * 1024 ** 3:
                raise RuntimeError('Available host memory became too low. The managed runtime was stopped; choose a smaller quantization.')
            if child.poll() is not None:
                with Path(log_path).open('rb') as log:
                    log.seek(max(0, log.seek(0, 2) - 5000))
                    detail = log.read().decode('utf-8', 'replace')
                raise RuntimeError('The managed runtime exited with code ' + str(child.returncode) + ': ' + detail[-2500:])
            try:
                with self._request(url + '/health', key=key, timeout=1) as response:
                    if response.status == 200:
                        return
            except (OSError, RuntimeError):
                pass
            self._cancel.wait(.2)
        raise RuntimeError('The model runtime did not become ready within five minutes. Its files were retained.')

    def _ensure_transformers(self, entry, log_path, recipe):
        helper = self.resources / 'runtime-setup/setup_manager.py'
        if not helper.is_file():
            raise ValueError('The managed text dependency helper is missing')
        options = self.root / 'runtime-setup' / ('hub-options-' + entry['id'] + '.json')
        options.parent.mkdir(parents=True, exist_ok=True)
        options.write_text(json.dumps({'modelType': entry.get('modelType'), 'remainingWeightBytes': 0}), encoding='utf-8')
        self.on_progress(stage='installing-dependencies', detail='Preparing the isolated text runtime')
        command = [sys.executable, '-I', str(helper), '--root', str(self.root), '--resources', str(self.resources),
                   '--target', entry['id'], '--recipe-id', recipe, '--options-file', str(options)]
        child = self._spawn(command, dict(os.environ, PYTHONIOENCODING='utf-8'), log_path)
        while child.poll() is None:
            if self._cancel.wait(.2):
                raise RuntimeError('Runtime preparation paused; downloaded files were retained.')
        if child.returncode:
            raise RuntimeError('The text runtime does not support this checkpoint or its dependencies could not be prepared. See its runtime log: ' + str(log_path))
        self._children.remove(child)

    def prepare(self, entry):
        if not self._preparation.acquire(blocking=False):
            raise RuntimeError('Another model runtime is being prepared')
        with self._state_lock:
            self._loading = (entry['id'], 'Loading model…')
        try:
            return self._prepare(entry)
        except Exception:
            self.stop()
            raise
        finally:
            with self._state_lock:
                self._loading = None
            self._preparation.release()

    def change_mode(self, entry, mode):
        if mode not in ('native','echo'):raise ValueError('Choose Native or ECHO')
        with self._state_lock:
            if self._generation.locked() or self._preparation.locked() or self._context:
                raise RuntimeError('Wait for the active model request before changing operating mode')
            if self._active and self._active['id']==entry['id'] and self._active.get('mode','echo')!=mode:
                self.stop()
        return {'operatingMode':mode}

    def change_backend(self, entry):
        with self._state_lock:
            if self._generation.locked() or self._preparation.locked() or self._context:
                raise RuntimeError('Wait for the active model request before changing runtime')
            if self._active and self._active['id'] == entry['id']:
                self.stop()

    def _prepare(self, entry, backend_override=None):
        if self._generation.locked():
            raise RuntimeError('Wait for the current local response to finish')
        if self._active and self._active['id'] == entry['id'] and self._active.get('mode','echo')==entry.get('operatingMode','echo') and all(child.poll() is None for child in self._children):
            return self._active['proof']
        self.stop(clear_context=False)
        self._cancel.clear()
        self._stop_reason = ''
        plan = runtime_plan(entry, self.root, self.resources, backend_override=backend_override)
        plan.setdefault('operatingMode',entry.get('operatingMode','echo'))
        plan.setdefault('requestedContextTokens',NATIVE_TARGET if plan['operatingMode']=='native' else ECHO_TARGET)
        self._report_progress(stage='verifying-files', detail='Checking the installed checkpoint before execution')
        files = checked_model_files(entry, self.root, self._cancel)
        available = _available_memory()
        weight_bytes = sum(path.stat().st_size for path in files)
        if plan['format'] == 'gguf' and plan.get('runtimeBackend') == 'cpu' and available and weight_bytes + 3 * 1024 ** 3 > available:
            raise RuntimeError(f'This model uses a CPU-only runtime and needs about {(weight_bytes + 3 * 1024 ** 3) / 1e9:.1f} GB of free RAM including working space; GPU memory is not used by that runtime. The downloaded model is kept. Free RAM or choose a smaller quantization, then prepare it again.')
        run_dir = self.root / 'runtime-setup/hub-runs' / (entry['id'] + '-' + uuid.uuid4().hex[:12])
        run_dir.mkdir(parents=True)
        log_path = run_dir / 'runtime.log'
        cuda_library_dir = None
        def try_other_gpu(error):
            from managed_engines import VULKAN_VARIANTS, cuda_driver_version, vulkan_gpu_available
            if self._cancel.is_set():
                raise error
            preference = entry.get('runtimePreference', 'auto')
            if plan.get('bundledCuda') and cuda_driver_version() >= 12000:
                self._report_progress(stage='loading-model', detail='Trying the managed CUDA runtime for this checkpoint.')
                return self._prepare(entry, backend_override='managed-cuda')
            if (preference == 'auto' and plan.get('runtimeBackend') == 'cuda'
                    and plan.get('engineFamily') in VULKAN_VARIANTS and vulkan_gpu_available()):
                self._report_progress(stage='loading-model', detail='CUDA could not start the model. Trying Vulkan GPU.')
                return self._prepare(entry, backend_override='vulkan')
            raise error
        if plan.get('publisherEngine'):
            from managed_engines import ensure, folder
            def check_cancel():
                if self._cancel.is_set():
                    raise RuntimeError('Runtime preparation paused; downloaded files were retained.')
            try:
                ensure(self.root, plan['publisherEngine'], self._report_progress, check_cancel)
                if plan.get('runtimeBackend') == 'cuda':
                    version = '13' if '-cuda13-' in plan['publisherEngine'] else '12'
                    library_id = 'cuda' + version + '-libs-v1'
                    ensure(self.root, library_id, self._report_progress, check_cancel)
                    cuda_library_dir = folder(self.root, library_id)
            except (OSError, RuntimeError, ValueError) as error:
                return try_other_gpu(error)
        if plan['format'] == 'safetensors':
            self._ensure_transformers(entry, log_path, plan.get('recipe', 'managed-transformers-text-v1'))
            plan = runtime_plan(entry, self.root, self.resources)
        port, echo_port, key = _port(), _port(), secrets.token_urlsafe(32)
        key_file = run_dir / 'worker-key'
        key_file.write_text(key, encoding='utf-8')
        self._key_file = key_file
        command = [str(value).replace('{port}', str(port)).replace('{keyfile}', str(key_file)) for value in plan['command']]
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', OPENCORE_HF_INTERNAL_KEY=key,
                           PATH=(str(cuda_library_dir)+os.pathsep if cuda_library_dir else '')+str(self.resources/'doucode/runtime')+os.pathsep+os.environ.get('PATH',''))
        self._report_progress(stage='loading-model', detail='Loading the selected checkpoint in its managed runtime')
        try:
            child = self._spawn(command, environment, log_path,
                                cwd=Path(command[0]).parent if plan.get('publisherEngine') else None)
        except OSError as error:
            return try_other_gpu(error)
        upstream = f'http://127.0.0.1:{port}'
        try:
            self._wait_health(child, upstream, key, log_path)
        except RuntimeError as error:
            return try_other_gpu(error)
        if plan['format'] == 'gguf' and plan.get('runtimeBackend') in ('cuda', 'vulkan'):
            plan['gpuOffloadedLayers'] = gpu_offloaded_layers(log_path)
            if not plan['gpuOffloadedLayers']:
                return try_other_gpu(RuntimeError(
                    f"The {plan['runtimeBackend'].upper()} runtime did not place any model layers on the GPU. "
                    f"The downloaded weights are kept. See the runtime log: {log_path}"))
        try:
            with self._request(upstream + '/props', key=key, timeout=5) as response:
                props = json.load(response)
        except (OSError, ValueError, RuntimeError):
            props = {}
        if plan['format'] == 'safetensors' and plan.get('runtimeBackend') == 'cuda':
            gpu_modules = props.get('gpuModules')
            if type(gpu_modules) is not int or gpu_modules < 1 or props.get('diskModules') != 0:
                raise RuntimeError('The Transformers worker did not confirm a GPU and RAM load plan. The model files were kept.')
            plan['gpuModules'] = gpu_modules
            plan['cpuModules'] = props.get('cpuModules', 0)
        plan['context'], context_verified = admitted_context(plan['context'], props)
        self._report_progress(stage='verifying-inference', detail='Checking an actual short generation')
        with self._request(upstream + '/v1/chat/completions', {'model': entry['id'], 'messages': [{'role': 'user', 'content': 'Say hello.'}],
                'max_tokens': 8, 'temperature': 0, 'stream': False}, key, timeout=120) as response:
            proof_result = json.load(response)
        choices = proof_result.get('choices', [])
        message = choices[0].get('message', {}) if choices else {}
        sample = message.get('content') or message.get('reasoning_content') or message.get('reasoning')
        if not sample or proof_result.get('error'):
            raise RuntimeError('The runtime did not return a real generated response')
        media_probe=visual_text_probe(entry)
        media_verified=None
        if media_probe:
            kind,request=media_probe
            self._report_progress(stage='verifying-vision',detail='Verifying real generated text from '+kind)
            with self._request(upstream+'/v1/chat/completions',request,key,timeout=180) as response:
                media_result=json.load(response)
            media_choices=media_result.get('choices',[])
            media_message=media_choices[0].get('message',{}) if media_choices else {}
            media_sample=media_message.get('content') or media_message.get('reasoning_content')
            if not isinstance(media_sample,str) or not media_sample.strip() or media_result.get('error'):
                raise RuntimeError('The multimodal checkpoint did not generate text from the supplied inline '+kind)
            media_verified=kind
        if plan['operatingMode']=='native':
            proof=self._runtime_proof(entry,plan,files,proof_result,sample,context_verified,log_path)
            if media_verified:proof['verifiedInputModality']=media_verified
            (run_dir/'inference-receipt.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
            self._active={'id':entry['id'],'url':upstream,'upstream':upstream,'key':key,'proof':proof,'mode':'native','usage':{}}
            return proof
        echo = self.resources / 'echo/echo_server.py'
        if not echo.is_file():
            raise RuntimeError('The installed ECHO runtime is missing')
        self._report_progress(stage='preparing-echo', detail='Connecting ECHO archive and bounded context')
        echo_command = [sys.executable, '-I', str(Path(__file__).resolve()), '--worker', 'echo', '--resources', str(self.resources),
                        '--port', str(echo_port), '--upstream', upstream, '--archive', str(self.root / 'echo/archives'),
                        '--context', str(plan['context'])]
        echo_child = self._spawn(echo_command, dict(environment, ECHO_UPSTREAM_API_KEY=key), log_path)
        echo_url = f'http://127.0.0.1:{echo_port}'
        self._wait_health(echo_child, echo_url, key, log_path, deadline=60)
        self._report_progress(stage='verifying-echo', detail='Checking a generated reply through the ECHO endpoint')
        with self._request(echo_url + '/v1/chat/completions', {
                'model': entry['id'], 'messages': [{'role': 'user', 'content': 'Say hello.'}],
                'max_tokens': 512, 'echo_max_total_tokens': 512, 'temperature': 0, 'stream': False,
                'conversation_id': 'installation-check-' + uuid.uuid4().hex,
                'opencore_reasoning_effort': 'off', 'chat_template_kwargs': {'enable_thinking': False}}, key, timeout=180) as response:
            echo_result = json.load(response)
        echo_choices = echo_result.get('choices', [])
        echo_message = echo_choices[0].get('message', {}) if echo_choices else {}
        if not (echo_message.get('content') or echo_message.get('reasoning_content')):
            raise RuntimeError('The ECHO endpoint did not return an actual generated reply')
        if media_probe:
            kind,request=media_probe
            self._report_progress(stage='verifying-multimodal-echo',detail='Verifying '+kind+' survives ECHO chat routing')
            echo_request={**request,'conversation_id':'installation-media-check-'+uuid.uuid4().hex,
                          'echo_max_total_tokens':128}
            with self._request(echo_url+'/v1/chat/completions',echo_request,key,timeout=180) as response:
                echo_media_result=json.load(response)
            choices=echo_media_result.get('choices',[])
            message=choices[0].get('message',{}) if choices else {}
            text=message.get('content') or message.get('reasoning_content')
            if not isinstance(text,str) or not text.strip() or echo_media_result.get('error'):
                raise RuntimeError('ECHO did not preserve the '+kind+' input for text generation')
        proof=self._runtime_proof(entry,plan,files,proof_result,sample,context_verified,log_path)
        if media_verified:proof['verifiedInputModality']=media_verified
        (run_dir / 'inference-receipt.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
        self._active = {'id': entry['id'], 'url': echo_url, 'upstream': upstream, 'key': key, 'proof': proof,'mode':'echo'}
        return proof

    def _runtime_proof(self,entry,plan,files,proof_result,sample,context_verified,log_path):
        stats = [{'filename': str(path.relative_to(Path(plan['folder']))), 'bytes': path.stat().st_size,
                  'modifiedNs': path.stat().st_mtime_ns} for path in files]
        proof = {'runtimeReady': True, 'inferenceVerified': True, 'echoReady': plan['operatingMode']=='echo', 'contextTokens': plan['context'],
                 'operatingMode':plan['operatingMode'],'requestedContextTokens':plan['requestedContextTokens'],
                 'echoMode': 'main-context-v1' if plan['operatingMode']=='echo' else None, 'contextVerified': context_verified,
                 'verifiedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'repository': entry['repository'],
                 'revision': entry['revision'], 'receiptSha256': _hash(entry['receiptPath']),
                 'generatedTokens': proof_result.get('usage', {}).get('completion_tokens'),
                 'sampleSha256': hashlib.sha256(str(sample).encode()).hexdigest(), 'files': stats, 'logPath': str(log_path)}
        if plan.get('publisherEngine'):
            from managed_engines import definition
            proof['engineId'] = plan['publisherEngine']
            proof['engineArchiveSha256'] = definition(plan['publisherEngine'])['sha256']
        if plan.get('runtimeBackend'):
            proof['runtimeBackend'] = plan['runtimeBackend']
        if plan.get('gpuOffloadedLayers'):
            proof['gpuOffloadedLayers'] = plan['gpuOffloadedLayers']
        if plan.get('gpuModules'):
            proof.update(gpuModules=plan['gpuModules'], cpuModules=plan['cpuModules'])
        return proof

    def chat(self, entry, payload):
        if not self._active or self._active['id'] != entry['id'] or self._active.get('mode','echo')!=entry.get('operatingMode','echo') or any(child.poll() is not None for child in self._children):
            self.prepare(entry)
        with self._state_lock:
            active = self._active
            if not active or active['id'] != entry['id'] or not self._generation.acquire(blocking=False):
                raise RuntimeError('The local model is already generating or was unloaded. Retry when it is ready.')
            selected = self._context if self._context and self._context['modelId'] == entry['id'] else None
        try:
            conversation = selected['conversationId'] if selected else 'hub-direct-' + uuid.uuid4().hex
            body = {**payload, 'conversation_id': conversation}
            if active.get('mode')=='native':
                active['usage']={}
                for field in list(body):
                    if field.startswith('echo_'):body.pop(field,None)
                maximum=active['proof']['contextTokens']
                if body.get('max_tokens') is not None:body['max_tokens']=max(1,min(int(body['max_tokens']),maximum-1))
                if body.get('stream'):body['stream_options']={**(body.get('stream_options') or {}),'include_usage':True}
            headers = {'X-Echo-Conversation': conversation}
            if selected:
                headers['X-OpenCore-Managed-Turn'] = '1'
            response=self._request(active['url'] + '/v1/chat/completions', body, active['key'], timeout=600, extra=headers)
            if payload.get('stream'):
                def chunks():
                    try:
                        with response:
                            total=0
                            while True:
                                chunk=response.readline(1024*1024)
                                if not chunk:break
                                total+=len(chunk)
                                if total>64*1024*1024:raise RuntimeError('The local response exceeded its stream limit')
                                if active.get('mode')=='native' and chunk.startswith(b'data:'):
                                    try:
                                        usage=json.loads(chunk[5:].strip()).get('usage')
                                        if isinstance(usage,dict):active['usage']=usage
                                    except (ValueError,AttributeError):pass
                                yield chunk
                    finally:
                        self._generation.release()
                result=chunks()
                release=False
                return result,'text/event-stream'
            with response:
                data = response.read(64 * 1024 * 1024 + 1)
                if len(data) > 64 * 1024 * 1024:
                    raise RuntimeError('The local response exceeded the bounded response buffer')
                if active.get('mode')=='native':
                    try:active['usage']=json.loads(data).get('usage',{})
                    except (ValueError,AttributeError):pass
                return data, response.headers.get('Content-Type', 'application/json')
        finally:
            if locals().get('release',True):self._generation.release()

    def stop(self, clear_context=True):
        self._cancel.set()
        for child in reversed(self._children):
            try:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            except OSError:
                pass
        self._children.clear()
        for watcher in self._memory_watchers:
            watcher.join(timeout=1)
        self._memory_watchers.clear()
        for log in self._logs:
            log.close()
        self._logs.clear()
        if getattr(self, '_key_file', None):
            self._key_file.unlink(missing_ok=True)
        self._active = None
        if clear_context:
            self._context = None
        return {'stopped': True}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _authorized_handler(parent):
    class Authorized(parent):
        def _allowed(self):
            key = os.environ.get('OPENCORE_HF_INTERNAL_KEY', '')
            return bool(key) and secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + key)
        def do_GET(self):
            if self._allowed(): return super().do_GET()
            self.send_error(401)
        def do_POST(self):
            if self._allowed(): return super().do_POST()
            self.send_error(401)
    return Authorized


def system_prefix(payload):
    """Adapt ECHO's late memory blocks to templates accepting one initial system message."""
    messages = payload.get('messages') or []
    pinned = [message for message in messages if message.get('role') in ('system', 'developer')]
    if not pinned:
        return payload
    content = [message.get('content') or '' for message in pinned]
    if all(isinstance(value, str) for value in content):
        combined = '\n\n'.join(content)
    else:
        combined = []
        for value in content:
            if isinstance(value, list):
                combined.extend(value)
            else:
                combined.append({'type': 'text', 'text': str(value)})
    prefix = {**pinned[0], 'role': 'system', 'content': combined}
    return {**payload, 'messages': [prefix] + [message for message in messages
            if message.get('role') not in ('system', 'developer')]}


def _echo_handler(parent):
    class Compatible(_authorized_handler(parent)):
        private_live_preview = True

        def _upstream(self, path, payload, stream):
            return super()._upstream(path, system_prefix(payload), stream)
    return Compatible


def _worker_main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', choices=['text', 'echo'], required=True)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--model-id')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--context', type=int, required=True)
    parser.add_argument('--upstream')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--device', choices=('auto','cpu','cuda-hybrid'), default='auto')
    args = parser.parse_args()
    if args.worker == 'echo':
        module = _load('opencore_hub_echo', args.resources / 'echo/echo_server.py')
        module.Handler = _echo_handler(module.Handler)
        sys.argv = ['echo_server.py', '--port', str(args.port), '--host', '127.0.0.1', '--upstream', args.upstream,
                    '--archive', str(args.archive), '--context-size', str(args.context),
                    '--max-continuations', '0', '--warm-cache-budget-mb', '128', '--no-console', '--quiet']
        return module.main()
    module = _load('opencore_hub_text', args.resources / 'text-runtime/serve_text.py')
    original_server = module.ThreadingHTTPServer
    def owned_server(address, handler):
        return original_server(address, _authorized_handler(handler))
    module.ThreadingHTTPServer = owned_server
    model, tokenizer, context, device = module.load(args.model_dir, args.context, device_preference=args.device)
    module.serve(model, tokenizer, context, args.model_id, args.port)


if __name__ == '__main__':
    _worker_main()
