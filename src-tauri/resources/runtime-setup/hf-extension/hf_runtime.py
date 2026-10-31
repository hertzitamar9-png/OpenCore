"""Owned local inference workers for Hub imports; downloaded Python is never loaded."""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
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


def checked_model_files(entry, root, cancel=None):
    folder = _inside(entry.get('localDirectory', ''), Path(root) / 'models/hub')
    receipt_path = _inside(entry.get('receiptPath', folder / 'install-receipt.json'), folder)
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
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


def _python(root):
    for recipe in ('managed-transformers-text-v1', 'edgerock-portable-v1'):
        path = Path(root) / 'runtime-setup/environments' / recipe / 'Scripts/python.exe'
        if path.is_file():
            return str(path)
    return sys.executable


def runtime_plan(entry, root, resources):
    root, resources = Path(root).resolve(), Path(resources).resolve()
    fmt = entry.get('format', '')
    quant = str(entry.get('quantization', '')).upper()
    if fmt not in ('gguf', 'safetensors'):
        raise ValueError('This format has no integrated Windows runtime adapter. The downloaded files are retained.')
    if fmt == 'safetensors' and any(name in quant for name in ('NVFP4', 'MLX', 'GPTQ', 'AWQ')):
        raise ValueError('This quantization requires a specialized runtime and compatible hardware; NVFP4 is not a plain Transformers checkpoint.')
    folder = _inside(entry.get('localDirectory', ''), root / 'models/hub')
    models = [folder / f['filename'] for f in entry.get('files', []) if f.get('role') == 'model']
    if not models:
        raise ValueError('No model weights are present for the runtime adapter')
    context = min(int(entry.get('contextTokens') or 32768), 32768)
    if context < 8192:
        raise ValueError('This model has less than the 8,192-token context required by the chat agent. Its downloaded files are retained.')
    if fmt == 'gguf':
        from managed_engines import engine_for, executable
        publisher_engine = engine_for(entry)
        engine = executable(root, publisher_engine) if publisher_engine else root / 'runtime/llama-server.exe'
        if not publisher_engine:
            if not engine.is_file():
                engine=resources/'opencore/runtime/llama-server.exe'
            if not engine.is_file():
                raise ValueError('The bundled GGUF runtime is missing. Reinstall OpenCore to restore it.')
        model = sorted(models, key=lambda p: p.name)[0]
        command = [str(engine), '--model', str(model), '--host', '127.0.0.1', '--port', '{port}',
                   '--ctx-size', str(context), '--threads', '2', '--threads-batch', '2',
                   '--gpu-layers', 'auto', '--fit', 'on', '--no-webui', '--api-key-file', '{keyfile}']
        if publisher_engine:
            # The publisher CPU builds work without a CUDA SDK or a particular GPU.
            command[command.index('--gpu-layers') + 1] = '0'
            command[command.index('--fit') + 1] = 'off'
            command += ['--no-repack', '--cache-ram', '0', '--parallel', '1', '--no-warmup', '--jinja',
                        '--batch-size', '64', '--ubatch-size', '16']
            if publisher_engine == 'edgerock-cpu-v1':
                command += ['--no-context-shift']
            if publisher_engine == 'velociq8-cpu-v1':
                # Q8A64 stored tensors require the publisher's repacked buffers.
                command.remove('--no-repack')
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
        command = [_python(root), '-I', str(Path(__file__).resolve()), '--worker', 'text',
                   '--resources', str(resources), '--model-dir', str(directory), '--model-id', entry['id'],
                   '--context', str(context), '--port', '{port}']
    return {'command': command, 'context': context, 'format': fmt, 'folder': str(folder),
            **({'publisherEngine': publisher_engine} if fmt == 'gguf' and publisher_engine else {})}


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


class RuntimeManager:
    automatic_preparation=True
    def telemetry_source(self, model_id, conversation):
        with self._state_lock:
            active=self._active
            if not active or active['id']!=model_id:return None
            url,key=active['url'],active['key']
        from urllib.parse import quote,urlsplit
        def read():
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
            'context_length': max(1, min(int(e.get('contextTokens') or 32768), 32768))} for e in values
            if e.get('category','text')=='text' and e.get('status') == 'installed' and (e.get('runtimeReady') or e.get('runtimePreparation', {}).get('runtimeReady'))]}

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

    def _ensure_transformers(self, entry, log_path):
        helper = self.resources / 'runtime-setup/setup_manager.py'
        if not helper.is_file():
            raise ValueError('The managed text dependency helper is missing')
        options = self.root / 'runtime-setup' / ('hub-options-' + entry['id'] + '.json')
        options.parent.mkdir(parents=True, exist_ok=True)
        options.write_text(json.dumps({'modelType': entry.get('modelType'), 'remainingWeightBytes': 0}), encoding='utf-8')
        self.on_progress(stage='installing-dependencies', detail='Preparing the isolated text runtime')
        command = [_python(self.root), '-I', str(helper), '--root', str(self.root), '--resources', str(self.resources),
                   '--target', entry['id'], '--recipe-id', 'managed-transformers-text-v1', '--options-file', str(options)]
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
        try:
            return self._prepare(entry)
        except Exception:
            self.stop()
            raise
        finally:
            self._preparation.release()

    def _prepare(self, entry):
        if self._generation.locked():
            raise RuntimeError('Wait for the current local response to finish')
        if self._active and self._active['id'] == entry['id'] and all(child.poll() is None for child in self._children):
            return self._active['proof']
        self.stop(clear_context=False)
        self._cancel.clear()
        self._stop_reason = ''
        plan = runtime_plan(entry, self.root, self.resources)
        self.on_progress(stage='verifying-files', detail='Checking the installed checkpoint before execution')
        files = checked_model_files(entry, self.root, self._cancel)
        available = _available_memory()
        weight_bytes = sum(path.stat().st_size for path in files)
        if plan.get('publisherEngine') and available and weight_bytes + 3 * 1024 ** 3 > available:
            raise RuntimeError(f'This CPU package needs about {(weight_bytes + 3 * 1024 ** 3) / 1e9:.1f} GB of available host RAM including working space. Choose a smaller quantization or close other applications.')
        if available and weight_bytes > available + 8 * 1024**3:
            raise RuntimeError(f'This checkpoint has {weight_bytes / 1e9:.1f} GB of weights; there is not enough available host/GPU memory for this managed runtime. Choose a smaller quantization.')
        run_dir = self.root / 'runtime-setup/hub-runs' / (entry['id'] + '-' + uuid.uuid4().hex[:12])
        run_dir.mkdir(parents=True)
        log_path = run_dir / 'runtime.log'
        if plan.get('publisherEngine'):
            from managed_engines import ensure
            def check_cancel():
                if self._cancel.is_set():
                    raise RuntimeError('Runtime preparation paused; downloaded files were retained.')
            ensure(self.root, plan['publisherEngine'], self.on_progress, check_cancel)
        if plan['format'] == 'safetensors':
            self._ensure_transformers(entry, log_path)
            plan = runtime_plan(entry, self.root, self.resources)
        port, echo_port, key = _port(), _port(), secrets.token_urlsafe(32)
        key_file = run_dir / 'worker-key'
        key_file.write_text(key, encoding='utf-8')
        self._key_file = key_file
        command = [str(value).replace('{port}', str(port)).replace('{keyfile}', str(key_file)) for value in plan['command']]
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', OPENCORE_HF_INTERNAL_KEY=key,
                           PATH=str(self.resources/'doucode/runtime')+os.pathsep+os.environ.get('PATH',''))
        self.on_progress(stage='loading-model', detail='Loading the selected checkpoint in its managed runtime')
        child = self._spawn(command, environment, log_path,
                            cwd=Path(command[0]).parent if plan.get('publisherEngine') else None)
        upstream = f'http://127.0.0.1:{port}'
        self._wait_health(child, upstream, key, log_path)
        self.on_progress(stage='verifying-inference', detail='Checking an actual short generation')
        with self._request(upstream + '/v1/chat/completions', {'model': entry['id'], 'messages': [{'role': 'user', 'content': 'Say hello.'}],
                'max_tokens': 8, 'temperature': 0, 'stream': False}, key, timeout=120) as response:
            proof_result = json.load(response)
        choices = proof_result.get('choices', [])
        message = choices[0].get('message', {}) if choices else {}
        sample = message.get('content') or message.get('reasoning_content') or message.get('reasoning')
        if not sample or proof_result.get('error'):
            raise RuntimeError('The runtime did not return a real generated response')
        echo = self.resources / 'echo/echo_server.py'
        if not echo.is_file():
            raise RuntimeError('The installed ECHO runtime is missing')
        self.on_progress(stage='preparing-echo', detail='Connecting ECHO archive and bounded context')
        echo_command = [_python(self.root), '-I', str(Path(__file__).resolve()), '--worker', 'echo', '--resources', str(self.resources),
                        '--port', str(echo_port), '--upstream', upstream, '--archive', str(self.root / 'echo/archives'),
                        '--context', str(plan['context'])]
        echo_child = self._spawn(echo_command, dict(environment, ECHO_UPSTREAM_API_KEY=key), log_path)
        echo_url = f'http://127.0.0.1:{echo_port}'
        self._wait_health(echo_child, echo_url, key, log_path, deadline=60)
        self.on_progress(stage='verifying-echo', detail='Checking a generated reply through the ECHO endpoint')
        with self._request(echo_url + '/v1/chat/completions', {
                'model': entry['id'], 'messages': [{'role': 'user', 'content': 'Say hello.'}],
                'max_tokens': 16, 'temperature': 0, 'stream': False,
                'conversation_id': 'installation-check-' + uuid.uuid4().hex,
                'chat_template_kwargs': {'enable_thinking': False}}, key, timeout=180) as response:
            echo_result = json.load(response)
        echo_choices = echo_result.get('choices', [])
        echo_message = echo_choices[0].get('message', {}) if echo_choices else {}
        if not (echo_message.get('content') or echo_message.get('reasoning_content')):
            raise RuntimeError('The ECHO endpoint did not return an actual generated reply')
        stats = [{'filename': str(path.relative_to(Path(plan['folder']))), 'bytes': path.stat().st_size,
                  'modifiedNs': path.stat().st_mtime_ns} for path in files]
        proof = {'runtimeReady': True, 'inferenceVerified': True, 'echoReady': True, 'contextTokens': plan['context'],
                 'echoMode': 'main-context-v1',
                 'verifiedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'repository': entry['repository'],
                 'revision': entry['revision'], 'receiptSha256': _hash(entry['receiptPath']),
                 'generatedTokens': proof_result.get('usage', {}).get('completion_tokens'),
                 'sampleSha256': hashlib.sha256(str(sample).encode()).hexdigest(), 'files': stats, 'logPath': str(log_path)}
        if plan.get('publisherEngine'):
            from managed_engines import definition
            proof['engineId'] = plan['publisherEngine']
            proof['engineArchiveSha256'] = definition(plan['publisherEngine'])['sha256']
        (run_dir / 'inference-receipt.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
        self._active = {'id': entry['id'], 'url': echo_url, 'upstream': upstream, 'key': key, 'proof': proof}
        return proof

    def chat(self, entry, payload):
        if not self._active or self._active['id'] != entry['id'] or any(child.poll() is not None for child in self._children):
            self.prepare(entry)
        if not self._generation.acquire(blocking=False):
            raise RuntimeError('The local model is already generating a response')
        try:
            with self._state_lock:
                selected = self._context if self._context and self._context['modelId'] == entry['id'] else None
                conversation = selected['conversationId'] if selected else 'hub-direct-' + uuid.uuid4().hex
            body = {**payload, 'conversation_id': conversation}
            response=self._request(self._active['url'] + '/v1/chat/completions', body, self._active['key'], timeout=600,
                               extra={'X-Echo-Conversation': conversation})
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
    model, tokenizer, context, device = module.load(args.model_dir, args.context)
    module.serve(model, tokenizer, context, args.model_id, args.port)


if __name__ == '__main__':
    _worker_main()
