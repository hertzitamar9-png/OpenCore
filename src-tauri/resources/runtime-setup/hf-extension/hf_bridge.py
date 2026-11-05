"""OpenCore-owned loopback Hub bridge. Importing this module starts no service."""
import argparse
from contextlib import contextmanager
import copy
import ctypes
from ctypes import wintypes
import hmac
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

# The bundled interpreter is launched with -I; only this managed extension folder is admitted.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from hf_catalog import build_plan, parse_repository, DRAFT
from hf_lineage import source_identity
from hf_download import Downloader, HubClient, HubError, Paused, atomic_json, managed_path, validate_entry, partial_identity
from hf_security import TokenStore
from hf_specialist_ocr import OCRManager, supports as supports_ocr
from hf_image_runtime import ImageRuntimeManager, supports as supports_image
from hf_memory_repair import MemoryRepair
from hf_prompt_assistant import PromptAssistant
from hf_chat_media import ChatMedia, saved_conversations_page, delete_empty_projects
from hf_connectors import ConnectorProfiles
from hf_telemetry import LiveTelemetry
from hf_computer_runtime import ComputerManager, supports as supports_computer
from model_modes import apply_operating_modes, supports_text_generation, mode_context
from hf_download_queue import DownloadQueue

AUTH_HEADER = "X-OpenCore-Hub-Secret"
CATALOG_HEADER = "X-OpenCore-Catalog-Revision"
TAURI_ORIGINS = {"http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"}
ID = re.compile(r"hf-[0-9a-f]{24}\Z")
ACTIVE = {"running", "pausing", "cancelling"}
MAX_CATALOG_ENTRIES = 10000
MAX_CATALOG_BYTES = 64 * 1024 * 1024


class Conflict(ValueError):
    pass


class RuntimeUnavailable:
    def __init__(self, reason="The managed inference adapter is not installed"):
        self.reason = reason
    def models(self, catalog):
        return {"object": "list", "data": []}
    def prepare(self, entry):
        raise ValueError(self.reason)
    def chat(self, entry, payload):
        raise ValueError(self.reason)
    def stop(self):
        return {"stopped": True}


def _read_json(path, limit=16 * 1024 * 1024):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError("Managed state exceeds the supported size limit")
    return json.loads(path.read_text(encoding="utf-8"))


class BridgeState:
    def __init__(self, root, resources, *, client=None, runtime=None):
        self.root, self.resources = Path(root).resolve(), Path(resources).resolve()
        self.lock = threading.RLock()
        self.catalog_path = managed_path(self.root, "models", "hub-imports.json")
        self.job_path = managed_path(self.root, "runtime-setup", "hub-bridge", "jobs.json")
        self.tokens = TokenStore(self.root)
        self.client = client or HubClient(self.tokens.get)
        self.downloader = Downloader(self.root, self.client)
        self.entries, self.plans, self.receipt_cache = {}, {}, {}
        self.live_telemetry = LiveTelemetry()
        self.removal_plans = {}
        self.catalog_epoch, self.catalog_snapshot = 0, None
        self.catalog_snapshot_at, self.catalog_fingerprint, self.catalog_revision = 0, None, None
        self.progress, self.worker = None, None
        self.ocr = OCRManager(self.root, self.resources)
        self.images=ImageRuntimeManager(self.root,self.resources)
        self.connectors = ConnectorProfiles(self.root)
        self.computer = ComputerManager(self.root, self.resources, self.downloader)
        self.owner, self.remote, self.private, self.groups = None, None, None, None
        self.memory_repair = None
        self.prompt_assistant = None
        self.autoloop = None
        self._memory_lock = threading.RLock()
        self.queue_prepare_lock = threading.Lock()
        self.pause_event, self.closed = threading.Event(), False
        self.cancel_event = threading.Event()
        self._load_catalog()
        self._seed_publisher_catalog()
        try:
            saved = _read_json(self.job_path, 1024 * 1024)
            if isinstance(saved, dict) and isinstance(saved.get("progress"), dict):
                self.progress = saved["progress"]
                if self.progress.get("status") in ACTIVE:
                    self.progress.update(status="paused" if self.progress.get("kind") == "download" else "failed", stage="interrupted", error="OpenCore stopped before the job completed")
        except FileNotFoundError:
            pass
        if runtime is not None:
            self.runtime = runtime
        else:
            try:
                from hf_runtime import RuntimeManager
                self.runtime = RuntimeManager(self.root, self.resources)
            except ImportError:
                self.runtime = RuntimeUnavailable()
        self._native_bindings = None
        self._register_queue_shared_files()
        self.download_queue = DownloadQueue(managed_path(self.root, 'runtime-setup', 'hub-bridge', 'download-queue.json'),
                                            self._queue_resolve, self._queue_execute, discard=self._queue_discard,
                                            available=lambda: not self.progress or self.progress.get('status') not in ACTIVE)

    def _bindings(self):
        if self._native_bindings is None:
            source = self.resources / 'runtime-setup/hf-extension/native-model-bindings.json'
            data = _read_json(source) if source.is_file() else {'models': [], 'artifacts': []}
            self._native_bindings = ({m['id']: m for m in data['models']}, {a['id']: a for a in data['artifacts']})
        return self._native_bindings

    def _register_queue_shared_files(self):
        from hf_catalog import safe_filename
        for artifact in self._bindings()[1].values():
            self.downloader.register_shared(artifact, managed_path(self.root, safe_filename(artifact['path'])))
        for entry in self.entries.values():
            if entry.get('installable'):
                for pin in entry.get('files', []):
                    self.downloader.register_shared(pin, managed_path(self.downloader.directory(entry), pin['filename']))

    def _queue_resolve(self, value):
        if not isinstance(value, dict): raise ValueError('Choose a verified catalog model')
        command, args, identity = value.get('command'), value.get('args', {}), value.get('modelId')
        if command not in ('install_model', 'runtime_setup_start', 'install_publisher_model', 'hub_prepare_model') or not isinstance(args, dict):
            raise ValueError('Unsupported model download command')
        if not isinstance(identity, str) or not 1 <= len(identity) <= 512:
            raise ValueError('A verified model identity is required')
        target = args.get('targetId') or args.get('id') or args.get('modelId') or identity
        if target != identity: raise ValueError('The queue model and selected download must match')
        label = value.get('label') or identity
        if not isinstance(label, str) or len(label) > 512: raise ValueError('Invalid model label')
        model, artifacts = self._bindings()
        clean = {'modelId': identity, 'label': label, 'command': command, 'args': {}}
        if command == 'runtime_setup_start':
            options = args.get('options') or {}
            if not isinstance(options, dict): raise ValueError('Invalid runtime setup options')
            clean['args'] = {'targetId': identity, 'options': {key: options.get(key) is True for key in ('installWeights', 'verifyInference', 'acceptLicenses', 'allowAdministrator')}}
            if clean['args']['options']['installWeights']: clean['args']['options']['verifyInference'] = True
        else: clean['args'] = {'id': identity}
        if identity in model:
            selected = model[identity]
            if selected.get('installable') is False or not selected.get('artifacts'):
                raise ValueError('This model has no complete pinned download plan')
            pins = [artifacts[a] for a in selected['artifacts']]
            clean.update(key='native:' + hashlib.sha256(json.dumps(sorted((p['path'], p['sha256'], p['bytes']) for p in pins)).encode()).hexdigest(),
                         totalBytes=sum(p['bytes'] for p in pins), source='native')
            if command == 'runtime_setup_start':
                manifest = _read_json(self.resources / 'runtime-setup/recipes.json')
                recipe = next((r for r in manifest['recipes'] if identity in r['modelIds']), None)
                if not recipe: raise ValueError('Automatic setup has no verified publisher-compatible recipe for this model')
                clean['key'] += ':prepare:' + recipe['id']
                if recipe.get('requiresLicenseAcceptance') and not clean['args']['options']['acceptLicenses']:
                    raise ValueError('This model requires accepting its publisher license before setup')
        else:
            entry = self.entry(identity)
            validate_entry(entry)
            if entry.get('origin') == 'autoloop': raise ValueError('AutoLoop checkpoints are generated locally')
            clean.update(key='hub:' + identity, totalBytes=entry['bytes'], source='hub')
        return clean

    def _queue_discard(self, item):
        if item.get('source') == 'native':
            model, artifacts = self._bindings()
            paths = [managed_path(self.root, artifacts[a]['path']) for a in model[item['modelId']]['artifacts']]
            with self.downloader.owned_targets(paths, threading.Event(), lambda _: None):
                self.discard_native_partials(item['modelId'])
        else:
            entry = self.entry(item['modelId'])
            paths = [managed_path(self.downloader.directory(entry), pin['filename']) for pin in entry['files']]
            with self.downloader.owned_targets(paths, threading.Event(), lambda _: None):
                self.downloader.discard_partials(entry)

    def _queue_execute(self, item, pause, cancel, report):
        resolved = self._queue_resolve(item)
        identity, command = resolved['modelId'], resolved['command']
        if resolved['source'] == 'native':
            models, artifacts = self._bindings()
            options = resolved['args'].get('options', {})
            speech = identity in ('phonon-2', 'whisper-large-v3', 'whisper-large-v3-turbo')
            external_speech = self._queue_external_speech(identity) if speech else None
            if external_speech:
                report({'stage': 'verifying-existing-speech', 'detail': 'Reusing the existing speech checkpoint; preparing its managed runtime', 'bytesTotal': 0})
            elif identity == 'yue2' and self._queue_existing_music(models[identity], artifacts, pause, report):
                return
            elif command != 'runtime_setup_start' or options.get('installWeights'):
                self.downloader.install_native(models[identity], artifacts, pause, report, parallel_ranges=not item.get('_parallel'))
            if command == 'runtime_setup_start' or speech:
                self._queue_prepare(item, pause, report, lambda: self._queue_native_setup(identity, options, pause, report, item['id']))
            return
        entry = self.entry(identity)
        preparing = command == 'hub_prepare_model' or command == 'runtime_setup_start' and not resolved['args']['options'].get('installWeights')
        if not preparing:
            self.downloader.install(entry, pause, report, parallel_ranges=not item.get('_parallel'))
            preparing = supports_computer(entry) or supports_ocr(entry) or supports_image(entry) or entry.get('runtime', {}).get('adapter') and getattr(self.runtime, 'automatic_preparation', False)
        if preparing:
            def prepare():
                progress = {'id': identity, 'jobId': item['id'], 'status': 'running', 'kind': 'prepare'}
                self._work(self.entry(identity, installed=True), 'prepare', pause, cancel, progress=progress, external_report=report)
                if progress['status'] == 'failed': raise ValueError(progress.get('error') or 'Model runtime preparation failed')
                if progress['status'] == 'paused': raise Paused('Runtime preparation paused')
            self._queue_prepare(item, pause, report, prepare)

    def _queue_external_speech(self, identity):
        if identity not in ('whisper-large-v3', 'whisper-large-v3-turbo'): return None
        home = Path(os.environ.get('USERPROFILE', str(self.root)))
        configured = os.environ.get('OPENCORE_WHISPER_MODEL')
        candidates = ([Path(configured)] if configured else []) + [home / 'OpenCore-Model-Test/asr' / identity]
        metadata = ('config.json', 'generation_config.json', 'preprocessor_config.json', 'tokenizer_config.json', 'normalizer.json', 'special_tokens_map.json', 'added_tokens.json')
        for directory in candidates:
            try:
                if not directory.is_absolute() or not all((directory / name).is_file() for name in metadata): continue
                config = _read_json(directory / 'config.json', 1024 * 1024)
                if config.get('model_type') != 'whisper' or config.get('decoder_layers') != (32 if identity == 'whisper-large-v3' else 4): continue
                weights = (directory / 'model.safetensors').is_file() or all((directory / name).is_file() for name in ('model.safetensors.index.json', 'model-00001-of-00002.safetensors', 'model-00002-of-00002.safetensors'))
                tokenizer = (directory / 'tokenizer.json').is_file() or all((directory / name).is_file() for name in ('vocab.json', 'merges.txt'))
                if weights and tokenizer: return directory
            except (OSError, ValueError): continue
        directory = self.root / 'speech/large-v3'
        if identity == 'whisper-large-v3':
            try:
                if all((directory / name).is_file() for name in ('config.json', 'preprocessor_config.json', 'tokenizer.json', 'vocabulary.json')) and (directory / 'model.bin').stat().st_size == 3087284237:
                    receipt = self.root / 'models/receipts/model-whisper-large-v3.json'
                    if not receipt.is_file() or 'whisper-full-v3-model-bin' not in _read_json(receipt): return directory
            except (OSError, ValueError): pass
        return None

    def _queue_existing_music(self, model, artifacts, pause, report):
        home = Path(os.environ.get('USERPROFILE', str(self.root)))
        candidates = [Path(value) for value in (os.environ.get('OPENCORE_YUE_MODELS'),) if value]
        candidates += [Path(os.environ.get('OPENCORE_MUSIC_HOME', str(home / 'YuE'))) / 'models', home / 'YuE/models']
        receipt_path = self.root / 'models/receipts/music-external.json'
        try:
            saved = _read_json(receipt_path)
            if saved:
                first = Path(next(iter(saved.values()))['path'])
                candidates.insert(0, first.parent.parent)
        except (OSError, ValueError, KeyError, TypeError): pass
        pins = [artifacts[a] for a in model['artifacts'] if artifacts[a].get('repo', '').startswith('m-a-p/YuE2-')]
        if not pins: return False
        for directory in candidates:
            paths = [(pin, directory / pin['path'].removeprefix('models/music/')) for pin in pins]
            try:
                if not all(path.is_file() and path.stat().st_size == pin['bytes'] for pin, path in paths): continue
                records = {}
                for pin, path in paths:
                    report({'stage': 'verifying-existing-music', 'detail': 'Verifying the existing YuE checkpoint without copying its files', 'bytesTotal': 0})
                    stamp = path.stat().st_mtime_ns
                    if not self.downloader._verify(path, pin, pause) or path.stat().st_mtime_ns != stamp:
                        raise ValueError('The existing YuE checkpoint failed its immutable pin; existing files were kept')
                    records[pin['id']] = {'path': str(path), 'sha256': pin['sha256'], 'bytes': pin['bytes'], 'modified': stamp}
                atomic_json(receipt_path, records)
                return True
            except OSError: continue
        return False

    def _queue_prepare(self, item, pause, report, prepare):
        report({'stage': 'waiting-runtime', 'detail': 'Download verified. Waiting for shared runtime preparation.'})
        while not self.queue_prepare_lock.acquire(timeout=.1):
            if pause.is_set(): raise Paused('Preparation paused; model files were retained')
        try:
            if pause.is_set(): raise Paused('Preparation paused; model files were retained')
            while self.images.operation.locked() or self.computer.operation.locked() or getattr(self.runtime, '_generation', threading.Lock()).locked():
                report({'stage': 'waiting-runtime', 'detail': 'Download verified. Waiting for the current model operation.'})
                if pause.wait(.2): raise Paused('Preparation paused; model files were retained')
            report({'stage': 'preparing-runtime', 'detail': 'Preparing and verifying the managed model runtime', 'bytesTotal': 0})
            finished = threading.Event()
            def watch_pause():
                while not finished.wait(.1):
                    if pause.is_set():
                        if item.get('source') == 'native': return  # Its subprocess owns its cancellation file.
                        entry = self.entries.get(item['modelId'], {})
                        if supports_computer(entry): self.computer.stop()
                        elif supports_ocr(entry): self.ocr.stop()
                        elif supports_image(entry): self.images.stop()
                        else:
                            cancellation = getattr(self.runtime, '_cancel', None)
                            if cancellation: cancellation.set()
                            else: self.runtime.stop()
                        return
            watcher = threading.Thread(target=watch_pause, daemon=True); watcher.start()
            try: prepare()
            finally:
                finished.set()
                watcher.join()
        finally:
            self.queue_prepare_lock.release()

    def _queue_native_setup(self, identity, options, pause, report, queue_id):
        # Reuse the bundled recipe executor, its managed environments and its
        # receipt verification. No caller-supplied package, command or URL runs.
        from queue import Queue, Empty
        helper = str(self.resources / 'runtime-setup')
        if helper not in sys.path: sys.path.insert(0, helper)
        from setup_manager import WindowsJob, recipe_for, read_receipt
        directory = managed_path(self.root, 'runtime-setup', 'runs', 'queue-' + queue_id)
        directory.mkdir(parents=True, exist_ok=True)
        options_path, cancel_path = directory / 'options.json', directory / 'cancel'
        cancel_path.unlink(missing_ok=True)
        atomic_json(options_path, options)
        process = subprocess.Popen([sys.executable, '-I', str(self.resources / 'runtime-setup/hf-extension/native_queue_setup.py'),
                    '--root', str(self.root), '--resources', str(self.resources), '--target', identity,
                    '--options-file', str(options_path), '--cancel-file', str(cancel_path)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        ownership = WindowsJob(process) if os.name == 'nt' else None
        messages = Queue()
        def read():
            try:
                for line in iter(lambda: process.stdout.readline(65537), ''):
                    if len(line) > 65536: messages.put({'error': 'Oversized setup progress message'}); break
                    try: messages.put(json.loads(line))
                    except ValueError: pass
            finally: messages.put(None)
        reader = threading.Thread(target=read, daemon=True); reader.start()
        stopped, error, pipe_done = None, None, False
        try:
            while process.poll() is None or not pipe_done:
                if pause.is_set() and stopped is None:
                    cancel_path.write_text('cancel', encoding='utf-8'); stopped = time.monotonic()
                if stopped is not None and time.monotonic() - stopped > 12:
                    if ownership: ownership.stop()
                    process.kill(); process.wait(timeout=5); raise Paused('Runtime preparation stopped; verified files were retained')
                try: value = messages.get(timeout=.1)
                except Empty: continue
                if value is None: pipe_done = True; continue
                if not isinstance(value, dict): continue
                if value.get('error'): error = str(value['error'])[:1000]
                if value.get('stage') != 'diagnostic': report(value)
            if pause.is_set(): raise Paused('Runtime preparation paused; verified files were retained')
            if process.returncode: raise ValueError(error or 'The managed dependency recipe failed')
            recipe = recipe_for(identity)
            receipt = read_receipt(self.root / 'runtime-setup/receipts' / (identity + '.json'), recipe)
            if not receipt: raise ValueError('The managed runtime dependency receipt failed verification')
            report({'stage': 'dependencies-verified', 'detail': 'Model files and runtime dependencies verified. Generation requires its model runtime.'})
        finally:
            if process.poll() is None:
                if ownership: ownership.stop()
                process.kill(); process.wait(timeout=5)
            if ownership: ownership.close()
            process.stdout.close()

    def _autoloop_helper(self):
        with self.lock:
            if self.autoloop is None:
                from autoloop_manager import AutoLoopManager
                self.autoloop = AutoLoopManager(self.root, self.resources, self)
            return self.autoloop

    def _memory_helper(self):
        # Construction opens no database; receipts also work with no model loaded.
        with self._memory_lock:
            if self.memory_repair is None:
                appdata = os.environ.get('APPDATA')
                data = Path(appdata).resolve() / 'ai.opencore.control-center' if appdata else self.root / 'runtime-setup'
                self.memory_repair = MemoryRepair(data, allowed_ports={8811, 8813})
            return self.memory_repair

    def _memory_runtime(self, supplied):
        if not isinstance(supplied, dict):
            raise ValueError('The current native runtime snapshot is required for memory recovery')
        runtime = {k: supplied[k] for k in ('status', 'profile', 'backendPort', 'echoPort', 'baseUrl', 'echoBaseUrl') if k in supplied}
        for field, url_field in (('backendPort', 'baseUrl'), ('echoPort', 'echoBaseUrl')):
            port = runtime.get(field)
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError('Valid native backend and ECHO ports are required')
            expected = 'http://127.0.0.1:%d' % port
            endpoint = runtime.get(url_field, expected)
            if not isinstance(endpoint, str) or endpoint.rstrip('/') != expected:
                raise ValueError('Memory recovery only accepts the exact native IPv4 loopback endpoints')
        # The authenticated PC/paired-device descriptor supplies the native ports.
        # When process inspection is available, additionally prove ownership and
        # obtain backend authorization internally rather than accepting it from UI.
        try:
            import psutil
        except ImportError:
            return runtime
        if not self.owner:
            return runtime
        try:
            username = psutil.Process().username()
            for port_field, pid_field in (('backendPort', 'modelPid'), ('echoPort', 'echoPid')):
                pid = supplied.get(pid_field)
                if type(pid) is not int or pid <= 0:
                    # Some native adapters omit child PIDs. The strict descriptor
                    # still applies; the recovery module verifies the RAM branch.
                    continue
                process = psutil.Process(pid)
                processes = [process] + process.children(recursive=True)[:16]
                listener = None
                for candidate in processes:
                    if candidate.username() != username:
                        continue
                    ancestry = {p.pid for p in candidate.parents()}
                    if candidate.pid != self.owner['pid'] and self.owner['pid'] not in ancestry:
                        continue
                    connections = candidate.net_connections(kind='tcp')
                    if any(c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == runtime[port_field]
                           and c.laddr.ip in ('127.0.0.1', '::ffff:127.0.0.1') for c in connections):
                        listener = candidate
                        break
                if listener is None:
                    raise ValueError('The native memory runtime port is not owned by this OpenCore process')
                if port_field == 'echoPort':
                    command = [str(a).replace('\\', '/').lower() for a in listener.cmdline()]
                    if any(a.endswith(('/echo_server.py', '/echo_server_runtime.py')) for a in command):
                        try:
                            key = listener.environ().get('ECHO_UPSTREAM_API_KEY')
                        except (psutil.AccessDenied, OSError):
                            key = None
                        if isinstance(key, str) and key and len(key) <= 4000 and all(32 <= ord(c) <= 126 for c in key):
                            runtime['authorization'] = 'Bearer ' + key
        except (psutil.AccessDenied, NotImplementedError):
            # Optional inspection must not install packages or elevate privileges.
            pass
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            raise ValueError('The native text runtime exited before memory recovery') from None
        return runtime

    def memory_action(self, action, body):
        helper = self._memory_helper()
        if action == 'receipts':
            return helper.receipts(body.get('conversationId'))
        if action == 'revalidate':
            return helper.revalidate(body.get('proof'))
        if action != 'recover':
            raise ValueError('Unknown memory recovery action')
        runtime = self._memory_runtime(body.get('runtime'))
        with self._memory_lock:
            helper.allowed_ports = frozenset((*helper.allowed_ports, runtime['backendPort'], runtime['echoPort']))
        return helper.recover({**body, 'runtime': runtime})

    def generation_settings(self, body):
        import importlib.util
        source=self.resources/'echo/evoagent/generation_settings.py'
        spec=importlib.util.spec_from_file_location('opencore_generation_settings',source)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with self.lock:
            parameters=body.get('parameters')
            if parameters is None:
                identity=body.get('modelId')
                entry=next((entry for entry in self.entries.values() if identity and identity in [entry['id'],entry.get('profileId'),*entry.get('nativeModelIds',[])]),None)
                if entry:
                    from hf_catalog import generation_defaults
                    parameters=entry.get('generationDefaults') or None
                    if not parameters:
                        receipt=self._receipt(entry)
                        if receipt:
                            path=managed_path(Path(receipt['localDirectory']),entry.get('subdirectory') or '.', 'generation_config.json')
                            if path.is_file() and path.stat().st_size<65536:
                                try:parameters=generation_defaults({'generation_config':json.loads(path.read_text(encoding='utf-8'))}) or None
                                except (OSError,ValueError):parameters=None
            return module.save(self.root/'echo/generation-settings.json',body.get('conversationId'),parameters)

    def _load_catalog(self):
        try:
            saved = _read_json(self.catalog_path, MAX_CATALOG_BYTES)
        except FileNotFoundError:
            return
        if not isinstance(saved, dict) or saved.get("schema") != 1 or not isinstance(saved.get("entries"), list) or len(saved["entries"]) > MAX_CATALOG_ENTRIES:
            raise ValueError("Managed Hugging Face catalog is invalid")
        for entry in saved["entries"]:
            if not isinstance(entry, dict) or not ID.fullmatch(str(entry.get("id", ""))):
                raise ValueError("Managed Hugging Face catalog has an invalid model ID")
            parse_repository(entry.get("repository"))
            if entry.get("installable") is True:
                validate_entry(entry)
            self.entries[entry["id"]] = entry

    def persist_catalog(self):
        value = {"schema": 1, "entries": list(self.entries.values())}
        if len(json.dumps(value, ensure_ascii=True, indent=2).encode('utf-8')) > MAX_CATALOG_BYTES:
            raise ValueError('Managed model metadata exceeds the catalog size limit')
        atomic_json(self.catalog_path, value)
        self.catalog_epoch += 1

    def _seed_publisher_catalog(self):
        source = self.resources / 'runtime-setup/hf-extension/publisher-models.json'
        if not source.is_file():
            return
        seed = _read_json(source, MAX_CATALOG_BYTES)
        if seed.get('schema') != 1 or not isinstance(seed.get('entries'), list):
            raise ValueError('The bundled publisher catalog is invalid')
        changed = False
        for identity, existing in list(self.entries.items()):
            weights = [pin for pin in existing.get('files', []) if pin.get('role') == 'model']
            if existing.get('builtIn') and weights and all(DRAFT.search(pin.get('filename', '')) for pin in weights):
                # Earlier catalogs advertised separate MTP draft components as
                # full-model quants. Remove the metadata only; keep local files.
                del self.entries[identity]
                changed = True
        for value in seed['entries']:
            if value.get('installable'):
                validate_entry(value)
            else:
                parse_repository(value.get('repository'))
            existing = self.entries.get(value['id'])
            entry = copy.deepcopy(value)
            if existing and existing['revision'] == value['revision']:
                for key in ('providerId', 'profileId', 'runtimePreparation', 'runtimePreparations', 'operatingMode', 'specialistPreparation'):
                    if key in existing:
                        entry[key] = copy.deepcopy(existing[key])
            if existing != entry:
                self.entries[value['id']] = entry
                changed = True
        if changed:
            self.persist_catalog()

    def _receipt(self, entry):
        directory = managed_path(managed_path(self.root, "models", "hub"), entry["id"])
        path = managed_path(directory, "install-receipt.json")
        try:
            stat = path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
            cached = self.receipt_cache.get(entry["id"])
            if cached and cached[0] == key:
                receipt = cached[1]
            else:
                receipt = _read_json(path)
                self.receipt_cache[entry["id"]] = (key, receipt)
            if receipt.get("schema") != 1 or any(receipt.get(k) != entry.get(k) for k in ("id", "repository", "revision", "bytes")):
                return None
            pins = receipt.get("files")
            if not isinstance(pins, list) or len(pins) != len(entry["files"]):
                return None
            for requested, verified in zip(entry["files"], pins):
                if any(verified.get(k) != requested.get(k) for k in requested):
                    return None
                target = managed_path(directory, requested["filename"])
                if str(target) != verified.get("localPath") or not target.is_file() or target.stat().st_size != requested["bytes"]:
                    return None
            return receipt
        except (FileNotFoundError, OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def _hydrate(self, entry):
        result = copy.deepcopy(entry)
        receipt = self._receipt(entry) if entry.get("installable") else None
        result["status"] = "installed" if receipt else "not-installed"
        if supports_ocr(entry):
            result['specialist'] = 'ocr'
            result['specialistReady'] = self.ocr.ready(result, entry.get('specialistPreparation'))
        if supports_computer(entry):
            result['specialist'] = 'computer'
            result['specialistReady'] = self.computer.ready(result, entry.get('specialistPreparation'))
        mode=entry.get('operatingMode','echo')
        preparation = (entry.get('runtimePreparations') or {}).get(mode) or entry.get("runtimePreparation") or {}
        if preparation.get('operatingMode','echo')!=mode:preparation={}
        result['runtimePreparation']=copy.deepcopy(preparation)
        result['operatingMode']=mode
        result["runtimeReady"] = bool(receipt and preparation.get("runtimeReady") is True and preparation.get("receiptRevision") == entry["revision"])
        if supports_image(entry):result['runtimeReady']=self.images.ready(result,preparation)
        if result['runtimeReady'] and entry.get('category') in ('text','brain','omni','ocr','computer-use') and supports_text_generation(entry) and mode=='echo' and preparation.get('echoMode')!='main-context-v1':
            result['runtimeReady']=False
        if result['runtimeReady']:
            from managed_engines import engine_for, definition
            selected = engine_for(entry)
            if selected and (preparation.get('engineArchiveSha256') != definition(selected)['sha256'] or mode=='echo' and preparation.get('echoMode') != 'main-context-v1'):
                result['runtimeReady'] = False
        if receipt:
            result.update(localDirectory=receipt["localDirectory"], receiptPath=receipt["receiptPath"], installReceipt=copy.deepcopy(receipt))
        if self.progress and self.progress.get("id") == entry["id"] and self.progress.get("status") in ACTIVE | {"paused", "failed"}:
            result["downloadProgress"] = copy.deepcopy(self.progress)
        return result

    def catalog(self):
        with self.lock:
            return [self._hydrate(x) for x in self.entries.values()]

    def image_generated(self,entry,proof):
        with self.lock:
            if self.entries.get(entry['id'],{}).get('revision')==entry['revision']:
                self.entries[entry['id']]['runtimePreparation']=proof
                self.persist_catalog()

    def status(self, catalog_revision=None):
        with self.lock:
            job = self.progress or {}
            fingerprint = (self.catalog_epoch, job.get('jobId'), job.get('status'))
            now = time.monotonic()
            if (catalog_revision is None or self.catalog_snapshot is None or fingerprint != self.catalog_fingerprint
                    or now - self.catalog_snapshot_at >= 30):
                self.catalog_snapshot = self.catalog()
                self.catalog_revision = hashlib.sha256(json.dumps(self.catalog_snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                self.catalog_fingerprint, self.catalog_snapshot_at = fingerprint, now
            result = {"schema": 1, "healthy": True, "catalogRevision": self.catalog_revision, "progress": copy.deepcopy(self.progress),
                      "tokenConfigured": self.tokens.status()["configured"], "runtimeAdapterAvailable": not isinstance(self.runtime, RuntimeUnavailable),
                      "connectorProfiles": self.connectors.status()}
            result['downloadQueue'] = self.download_queue.snapshot()
            if catalog_revision != self.catalog_revision:
                result['catalog'] = copy.deepcopy(self.catalog_snapshot)
            return result

    def plan(self, repository):
        metadata = self.client.metadata(parse_repository(repository))
        plan = build_plan(metadata)
        canonical, identities = source_identity(metadata)
        with self.lock:
            family = next((entry for entry in self.entries.values() if entry.get('familyId') and (
                str(entry.get('originalRepository', '')).lower() == canonical
                or entry['repository'].lower() in identities)), None)
            family = copy.deepcopy(family) if family else None
        for entry in plan['variants']:
            entry.update(originalRepository=canonical, familyId=family['familyId'] if family else
                         'checkpoint-' + hashlib.sha256(canonical.encode()).hexdigest()[:24],
                         familyLabel=family['familyLabel'] if family else metadata['id'].split('/')[-1])
            if family:
                entry['nativeModelIds'] = family.get('nativeModelIds', [])
                for field in ('builtIn', 'builtInDescription', 'libraryCategory'):
                    if field in family:
                        entry[field] = family[field]
                if family.get('managedEngine'):
                    entry['managedEngine'] = family['managedEngine']
                if not entry.get('modelType') and family.get('modelType'):
                    entry['modelType'] = family['modelType']
            elif entry['format'] == 'gguf':
                entry['managedEngine'] = 'flint-cpu-v1'
        apply_operating_modes(plan['variants'],lambda _:metadata)
        plan['originalRepository'] = canonical
        plan['planId'] = hashlib.sha256(json.dumps(plan['variants'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self.lock:
            now = time.monotonic()
            self.plans = {key: item for key, item in self.plans.items() if now - item[0] < 900}
            if len(self.plans) >= 8:
                self.plans.pop(next(iter(self.plans)))
            self.plans[plan["planId"]] = (now, plan)
        return plan

    def add(self, plan_id, variant_ids=None):
        with self.lock:
            saved = self.plans.get(plan_id)
            if not saved or time.monotonic() - saved[0] >= 900:
                raise ValueError("Inspect this repository again before adding its pinned variants")
            plan = saved[1]
            if variant_ids is not None and (not isinstance(variant_ids, list) or not variant_ids or len(variant_ids) > 2000 or any(not isinstance(i, str) for i in variant_ids)):
                raise ValueError("Select one or more variant IDs from the inspected repository")
            wanted = set(variant_ids) if variant_ids is not None else {x["id"] for x in plan["variants"]}
            variants = [copy.deepcopy(x) for x in plan["variants"] if x["id"] in wanted]
            if len(variants) != len(wanted) or not variants:
                raise ValueError("Selected variants do not belong to this inspected repository")
            if len(set(self.entries) | wanted) > MAX_CATALOG_ENTRIES:
                raise ValueError("The managed model catalog is full")
            if self.progress and self.progress.get("status") in ACTIVE and self.progress.get("id") in wanted:
                raise Conflict("Wait for or pause the active model job before updating its metadata")
            family = next((self.entries[value['id']] for value in plan['variants']
                           if self.entries.get(value['id'], {}).get('familyId')), None)
            previous_entries = dict(self.entries)
            for entry in variants:
                previous = self.entries.get(entry["id"], {})
                if family and plan.get('originalRepository'):
                    for field in ('familyId', 'familyLabel', 'nativeModelIds'):
                        if field in family:
                            entry[field] = copy.deepcopy(family[field])
                for field in ("profileId", "providerId", "familyId", "familyLabel", "nativeModelIds"):
                    if previous.get(field):
                        entry[field] = previous[field]
                if previous.get("revision") == entry["revision"] and previous.get("files") == entry["files"] and previous.get("runtimePreparation"):
                    entry["runtimePreparation"] = previous["runtimePreparation"]
                self.entries[entry["id"]] = entry
            try:
                self.persist_catalog()
            except Exception:
                self.entries = previous_entries
                raise
            return {"added": [x["id"] for x in variants], "catalog": self.catalog()}

    def entry(self, model_id, installed=False):
        if not isinstance(model_id, str) or not ID.fullmatch(model_id):
            raise ValueError("Select a valid managed Hugging Face model")
        with self.lock:
            if model_id not in self.entries:
                raise ValueError("The selected model is not in the managed catalog")
            entry = self._hydrate(self.entries[model_id])
        if installed and entry["status"] != "installed":
            raise ValueError("Download and verify this model before preparing or using it")
        return entry

    def _persist_progress(self):
        atomic_json(self.job_path, {"schema": 1, "progress": self.progress})

    def start(self, model_id, kind):
        with self.lock:
            if self.closed:
                raise Conflict("The bridge is shutting down")
            if self.progress and self.progress.get("status") in ACTIVE:
                raise Conflict("A model download or runtime preparation is already active")
            if self.download_queue.snapshot()['activeIds']:
                raise Conflict('A queued model installation owns the runtime. Use the download queue to add another model.')
            if self.worker and self.worker.is_alive():
                raise Conflict('The previous installation worker is still stopping')
            if self.images.operation.locked():raise Conflict('Wait for the current image operation before installing another model')
            if self.computer.operation.locked():raise Conflict('Wait for the current computer-use prediction before installing another model')
            entry = self.entry(model_id, installed=kind == "prepare")
            if kind == 'download' and entry.get('origin') == 'autoloop':
                raise ValueError('AutoLoop checkpoints are generated locally, not downloaded. Run a new verified experiment in Learning Studio.')
            validate_entry(entry)
            self.pause_event = threading.Event()
            self.cancel_event = threading.Event()
            self.progress = {"jobId": secrets.token_hex(16), "id": model_id, "kind": kind, "status": "running",
                             "stage": "preparing" if kind == "prepare" else "queued", "bytesDone": 0, "bytesTotal": entry["bytes"], "startedAt": time.time()}
            initial = copy.deepcopy(self.progress)
            self._persist_progress()
            self.worker = threading.Thread(target=self._work, args=(entry, kind, self.pause_event, self.cancel_event), daemon=True)
            self.worker.start()
            return initial

    def runtime_mode(self, model_id, mode):
        with self.lock:
            if mode not in ('native','echo'):raise ValueError('Choose Native or ECHO')
            entry=self.entry(model_id)
            if not supports_text_generation(entry):raise ValueError('This is not a text-generation model')
            if self.progress and self.progress.get('status') in ACTIVE:raise Conflict('Wait for model installation before changing operating mode')
            if entry.get('operatingMode','echo')!=mode:
                change=getattr(self.runtime,'change_mode',None)
                if change:
                    try:change(entry,mode)
                    except RuntimeError as error:raise Conflict(str(error)) from error
                saved=self.entries[model_id]
                old=saved.get('runtimePreparation')
                if old:saved.setdefault('runtimePreparations',{})[old.get('operatingMode','echo')]=copy.deepcopy(old)
                saved['operatingMode']=mode
                saved['runtimePreparation']=copy.deepcopy(saved.get('runtimePreparations',{}).get(mode,{}))
                self.persist_catalog()
            return self.entry(model_id)

    def _work(self, entry, kind, pause, cancel, *, progress=None, external_report=None):
        progress = self.progress if progress is None else progress
        last_saved = [0.0]
        def report(value):
            with self.lock:
                progress.update(value, updatedAt=time.time())
                if cancel.is_set(): progress.update(status='cancelling', stage='cancelling')
                if external_report: external_report(value)
                if not external_report and time.monotonic() - last_saved[0] > 1:
                    self._persist_progress()
                    last_saved[0] = time.monotonic()
        def prepare_ocr():
            result = self.ocr.prepare(self.entry(entry['id'], installed=True), report, pause)
            if pause.is_set():
                raise Paused('OCR preparation stopped; downloaded files were retained')
            with self.lock:
                self.entries[entry['id']]['specialistPreparation'] = result
                self.persist_catalog()
            return {'status': 'ready', 'stage': 'ready', 'bytesDone': entry['bytes'], 'bytesTotal': entry['bytes']}
        def prepare_computer():
            result = self.computer.prepare(self.entry(entry['id'], installed=True), report, pause)
            if pause.is_set(): raise Paused('Computer-use preparation stopped; downloaded files were retained')
            with self.lock:
                self.entries[entry['id']]['specialistPreparation'] = result
                self.persist_catalog()
            return {'status': 'ready', 'stage': 'ready', 'bytesDone': entry['bytes'], 'bytesTotal': entry['bytes']}
        try:
            if kind == "download":
                result = self.downloader.install(entry, pause, report)
                if pause.is_set(): raise Paused('Installation stopped after downloading; completed files were retained')
                final = {"status": "installed", "stage": "installed", "bytesDone": entry["bytes"]}
                if supports_computer(entry):
                    final = prepare_computer()
                elif supports_ocr(entry):
                    final = prepare_ocr()
                elif supports_image(entry):
                    result=self.images.prepare(self.entry(entry['id'],installed=True),report,pause)
                    with self.lock:
                        self.entries[entry['id']]['runtimePreparation']=result
                        self.persist_catalog()
                    final={'status':'ready','stage':'ready','bytesDone':entry['bytes'],'bytesTotal':entry['bytes']}
                elif entry.get('runtime',{}).get('adapter') and getattr(self.runtime,'automatic_preparation',False):
                    if pause.is_set():raise Paused('Installation paused after downloading; files were retained')
                    report({'stage':'preparing-runtime','detail':'Download verified. Preparing and testing the model runtime.','bytesTotal':0})
                    self.runtime.on_progress=lambda **value:report(value)
                    result=self.runtime.prepare(self.entry(entry['id'],installed=True))
                    if pause.is_set():
                        self.runtime.stop()
                        raise Paused('Runtime preparation stopped; files were retained')
                    if not isinstance(result,dict) or result.get('runtimeReady') is not True:
                        raise ValueError('The runtime did not confirm this model is ready')
                    with self.lock:
                        proof={**result,'receiptRevision':entry['revision']}
                        self.entries[entry['id']]['runtimePreparation']=proof
                        self.entries[entry['id']].setdefault('runtimePreparations',{})[entry.get('operatingMode','echo')]=copy.deepcopy(proof)
                        self.persist_catalog()
                    final={'status':'ready','stage':'ready','bytesDone':entry['bytes'],'bytesTotal':entry['bytes']}
            elif supports_computer(entry):
                final = prepare_computer()
            elif supports_ocr(entry):
                final = prepare_ocr()
            elif supports_image(entry):
                result=self.images.prepare(entry,report,pause)
                with self.lock:
                    self.entries[entry['id']]['runtimePreparation']=result
                    self.persist_catalog()
                final={'status':'ready','stage':'ready','bytesDone':entry['bytes']}
            else:
                self.runtime.on_progress=lambda **value:report(value)
                result = self.runtime.prepare(entry)
                if not isinstance(result, dict) or result.get("runtimeReady") is not True:
                    raise ValueError("The runtime did not confirm this model is ready")
                if pause.is_set():
                    self.runtime.stop()
                    raise Paused("Runtime preparation stopped")
                with self.lock:
                    self.entries[entry["id"]]["runtimePreparation"] = {**result, "receiptRevision": entry["revision"]}
                    self.entries[entry['id']].setdefault('runtimePreparations',{})[entry.get('operatingMode','echo')]=copy.deepcopy(self.entries[entry['id']]['runtimePreparation'])
                    self.persist_catalog()
                final = {"status": "ready", "stage": "ready", "bytesDone": entry["bytes"]}
            report({**final, "finishedAt": time.time()})
        except Paused as error:
            report({"status": "paused", "stage": "paused", "error": str(error), "finishedAt": time.time()})
        except Exception as error:
            # Never include request bodies, credentials, signed URLs or traceback text in UI errors.
            detail = str(error) if isinstance(error, (ValueError, HubError, RuntimeError)) else "Managed model operation failed; verified files and resumable partials are retained"
            report({"status": "paused" if pause.is_set() else "failed", "stage": "paused" if pause.is_set() else "failed", "error": detail[:1000], "finishedAt": time.time()})
        finally:
            with self.lock:
                if cancel.is_set():
                    cleanup = self.downloader.discard_partials(entry) if kind == 'download' and not external_report else {'removedBytes': 0, 'retainedPartials': []}
                    progress.update(status='cancelled', stage='cancelled', error=None, finishedAt=time.time(),
                                         detail='Installation cancelled. Completed and shared files were kept.', **cleanup)
                if not external_report: self._persist_progress()

    def pause(self, job_id=None):
        with self.lock:
            if not self.progress or self.progress.get("status") not in ACTIVE:
                return {"paused": False, "progress": copy.deepcopy(self.progress)}
            if job_id is not None and job_id != self.progress["jobId"]:
                raise Conflict("The requested job is no longer the active model job")
            stage = self.progress.get('stage')
            self.pause_event.set()
            self.progress.update(status="pausing", stage="pausing")
            ocr_job = supports_ocr(self.entries.get(self.progress.get('id'), {}))
            if supports_computer(self.entries.get(self.progress.get('id'), {})): self.computer.stop()
            if supports_image(self.entries.get(self.progress.get('id'),{})) and self.images.operation.locked():self.images.stop()
            if ocr_job and self.ocr.active():
                self.ocr.stop()
            if not ocr_job and (self.progress.get('kind')=='prepare' or stage in ('preparing-runtime','installing-runtime','installing-dependencies','checking-hardware','loading-model','verifying-inference','preparing-echo','verifying-echo')):
                cancellation=getattr(self.runtime,'_cancel',None)
                if cancellation:cancellation.set()
                else:self.runtime.stop()
            self._persist_progress()
            return {"paused": True, "progress": copy.deepcopy(self.progress)}

    def cancel(self, job_id):
        with self.lock:
            if not isinstance(job_id, str) or not self.progress or job_id != self.progress.get('jobId'):
                raise Conflict('The requested installation is no longer the current model job')
            if self.progress.get('status') == 'cancelled':
                return {'cancelled': True, 'progress': copy.deepcopy(self.progress)}
            if self.progress.get('status') not in ACTIVE | {'paused'}:
                raise Conflict('This installation has already finished; completed files were kept')
            self.cancel_event.set()
            self.pause(job_id)
            self.progress.update(status='cancelling', stage='cancelling', error=None)
            if not self.worker or not self.worker.is_alive():
                entry = self.entry(self.progress['id'])
                cleanup = self.downloader.discard_partials(entry) if self.progress.get('kind') == 'download' else {'removedBytes': 0, 'retainedPartials': []}
                self.progress.update(status='cancelled', stage='cancelled', finishedAt=time.time(),
                                     detail='Installation cancelled. Completed and shared files were kept.', **cleanup)
            self._persist_progress()
            return {'cancelling': self.progress['status'] == 'cancelling', 'progress': copy.deepcopy(self.progress)}

    def discard_native_partials(self, model_id):
        """The frontend stops and awaits native admission before requesting cleanup."""
        source = self.resources / 'runtime-setup/hf-extension/native-model-bindings.json'
        data = _read_json(source)
        model = next((value for value in data.get('models', []) if value.get('id') == model_id or model_id in (value.get('modeProfiles') or {}).values()), None)
        if model is None: raise ValueError('This native model has no verified partial-file manifest')
        ids = set(model.get('artifacts', []))
        removed, retained = 0, []
        for artifact in data.get('artifacts', []):
            if artifact.get('id') not in ids: continue
            relative = artifact.get('path')
            if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts: continue
            path = self.root / (relative + '.partial')
            identity = partial_identity(path, self.root)
            if identity is None and not path.exists(): continue
            if not identity or type(artifact.get('bytes')) is not int or identity[2] > artifact['bytes']:
                retained.append(relative); continue
            try:
                if partial_identity(path, self.root) != identity: retained.append(relative); continue
                path.unlink(); removed += identity[2]
            except OSError:
                retained.append(relative)
        return {'cancelled': True, 'removedBytes': removed, 'retainedPartials': retained}

    def profile(self, body):
        with self.lock:
            entry = self.entry(body.get("id"))
            for field in ("profileId", "providerId"):
                value = body.get(field)
                if not isinstance(value, str) or not 1 <= len(value) <= 200 or any(ord(c) < 32 for c in value):
                    raise ValueError("Native provider and profile IDs are required")
            self.entries[entry["id"]].update(profileId=body["profileId"], providerId=body["providerId"])
            self.persist_catalog()
            return self.entry(entry["id"])

    def stop(self):
        self.pause()
        self.computer.stop()
        result = self.runtime.stop()
        return result if isinstance(result, dict) else {"stopped": True}

    def _removal_files(self, model_id):
        folder=managed_path(managed_path(self.root,'models','hub'),self.entry(model_id)['id'])
        files=[]
        if folder.is_dir():
            for path in sorted(folder.rglob('*')):
                if path.is_symlink() or not path.resolve().is_relative_to(folder):
                    raise ValueError('Linked model files cannot be deleted by this dialog')
                if path.is_file():
                    stat=path.stat()
                    files.append({'path':str(path),'bytes':stat.st_size,'modifiedNs':stat.st_mtime_ns,'external':False,'sharedWith':[]})
        return folder,files

    def removal_plan(self, model_id):
        with self.lock:
            entry=self.entry(model_id)
            _,files=self._removal_files(model_id)
            token=secrets.token_hex(32)
            self.removal_plans={key:value for key,value in self.removal_plans.items() if value['expires']>time.monotonic()}
            self.removal_plans[token]={'id':model_id,'files':files,'expires':time.monotonic()+300}
            return {'modelId':model_id,'label':entry.get('name',entry['repository']),'files':files,
                    'retainedFiles':[],'totalBytes':sum(file['bytes'] for file in files),'confirmationToken':token}

    def uninstall(self, model_id, confirmation_token=None):
        with self.lock:
            plan=self.removal_plans.pop(confirmation_token,None)
            folder,files=self._removal_files(model_id)
            if not plan or plan['id']!=model_id or plan['expires']<time.monotonic() or plan['files']!=files:
                raise ValueError('Model files changed or the confirmation expired. Open the uninstall dialog again.')
            if self.progress and self.progress.get('status') in ACTIVE:
                raise Conflict('Pause or finish the current installation before removing model files')
            if self.ocr.active():
                raise Conflict('Wait for the current OCR operation to finish before removing model files')
            if self.computer.operation.locked():
                raise Conflict('Stop the active computer-use prediction before removing model files')
            if self.images.operation.locked():raise Conflict('Wait for the current image operation before removing model files')
            entry=self.entry(model_id)
            if getattr(self.runtime,'_generation',None) and self.runtime._generation.locked():
                raise Conflict('Wait for the current response before removing this model')
            if getattr(self.runtime,'_active',None) and self.runtime._active['id']==model_id:
                self.runtime.stop()
            if folder.is_dir():shutil.rmtree(folder)
            self.entries[model_id].pop('runtimePreparation',None)
            self.entries[model_id].pop('specialistPreparation',None)
            if entry.get('origin') == 'autoloop': self.entries.pop(model_id,None)
            self.receipt_cache.pop(model_id,None)
            self.persist_catalog()
            return {'uninstalled':True,'id':model_id}

    def close(self):
        self.download_queue.close()
        if self.closed:
            return
        if self.groups: self.groups.close()
        if self.autoloop: self.autoloop.close()
        if self.remote: self.remote.shutdown()
        if self.private: self.private.shutdown()
        self.closed = True
        self.pause_event.set()
        self.ocr.stop()
        self.images.stop()
        self.computer.stop()
        self.runtime.stop()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=2)


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16
    def __init__(self, address, state, secret):
        self.state, self.secret = state, secret
        self.instance_id = secrets.token_hex(16)
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, BridgeHandler)
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address
    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Busy\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise
    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()
    def handle_error(self, request, address):
        pass


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "OpenCoreHub/1"
    sys_version = ""
    def log_message(self, *_):
        pass
    def _send(self, status, result=None, content_type="application/json"):
        # Drain only small, already bounded rejected requests before close. Windows
        # otherwise resets the socket with unread input and loses the HTTP error.
        if status>=400 and not getattr(self,'_body_consumed',False):
            length=self.headers.get('Content-Length','')
            if length.isdigit() and 0<int(length)<=1024*1024:
                try:self.rfile.read(int(length))
                except (OSError,TimeoutError):pass
            self._body_consumed=True
        payload = result if isinstance(result, bytes) else json.dumps(result, ensure_ascii=True).encode("utf-8") if result is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self.headers.get("Origin")
        if origin in TAURI_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        if self.command == "OPTIONS":
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-OpenCore-Hub-Secret, X-OpenCore-Catalog-Revision, Authorization")
            self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _authorize(self, preflight=False):
        if len(self.path) > 2048 or self.headers.get("Host") != "127.0.0.1:" + str(self.server.server_port):
            self._send(403, {"error": "Invalid loopback request host"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in TAURI_ORIGINS:
            self._send(403, {"error": "Only the OpenCore desktop origin is allowed"})
            return False
        if preflight:
            requested = {h.strip().lower() for h in self.headers.get("Access-Control-Request-Headers", "").split(",") if h.strip()}
            if not origin or self.headers.get("Access-Control-Request-Method") not in {"GET", "POST", "PUT", "DELETE"} or not requested <= {"content-type", AUTH_HEADER.lower(), CATALOG_HEADER.lower(), "authorization"}:
                self._send(403, {"error": "Invalid desktop preflight"})
                return False
            return True
        supplied = self.headers.get(AUTH_HEADER, "")
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            supplied = authorization[7:]
        if not hmac.compare_digest(supplied, self.server.secret):
            self._send(401, {"error": "A valid OpenCore instance secret is required"})
            return False
        return True

    def do_OPTIONS(self):
        if self._authorize(True):
            self._send(204)

    def _body(self, limit):
        if self.headers.get("Transfer-Encoding"):
            self._send(400, {"error": "Chunked request bodies are not accepted"})
            return None
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._send(415, {"error": "Use application/json"})
            return None
        length = self.headers.get("Content-Length", "0")
        if not length.isdigit():
            self._send(400, {"error": "Invalid request size"})
            return None
        length = int(length)
        if length > limit:
            self._send(413, {"error": "Request body exceeds the supported size limit"})
            return None
        self._body_consumed=True
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("The request body ended early")
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, UnicodeError):
            raise ValueError("Invalid JSON request") from None
        if not isinstance(body, dict):
            raise ValueError("The JSON request must be an object")
        return body

    def _dispatch(self):
        # A group runner has a single-task capability, never the desktop secret.
        # Admit only this narrow loopback route before normal instance auth.
        if self.path == '/api/groups/agent/tool':
            if (self.command != 'POST' or self.headers.get('Origin') is not None
                    or self.headers.get('Host') != '127.0.0.1:' + str(self.server.server_port)
                    or self.client_address[0] != '127.0.0.1'):
                return self._send(403, {'error':'Only the local group task runner may use this endpoint'})
            manager = self.server.state.groups
            if manager is None:
                return self._send(403, {'error':'No active group task'})
            body = self._body(256 * 1024)
            if body is not None:
                self._send(200, manager.agent_tool(self.headers.get('Authorization'), body))
            return
        if not self._authorize():
            return
        path = urlsplit(self.path).path
        if urlsplit(self.path).query or urlsplit(self.path).fragment:
            raise ValueError("Query parameters are not accepted")
        route = path[4:] if path.startswith("/api/") else path
        state = self.server.state
        if (route.startswith('/remote/') or route.startswith('/mobile/host/') or route.startswith('/groups/')) and state.remote is None:
            from remote_access import RemoteAccess
            with state.lock:
                if state.remote is None: state.remote = RemoteAccess(state.root, state.resources, state.owner)
        if route.startswith('/groups/') and state.groups is None:
            from group_chats import GroupManager
            with state.lock:
                if state.groups is None: state.groups = GroupManager(state.root, state.remote)
        if route.startswith('/private/') and state.private is None:
            from private_chat import PrivateChats
            with state.lock:
                if state.private is None:
                    state.private = PrivateChats(state.resources, backend=getattr(state.runtime, 'private_backend', None))
        if self.command == "GET":
            if route == '/health':
                self._send(200, {'schema': 1, 'healthy': not state.closed, 'instanceId': self.server.instance_id})
            elif route == '/status':
                revision = self.headers.get(CATALOG_HEADER)
                if revision is not None and not re.fullmatch('[a-f0-9]{64}', revision):
                    raise ValueError('Invalid catalog revision')
                self._send(200, {**state.status(revision), "instanceId": self.server.instance_id})
            elif route == "/catalog":
                self._send(200, {"catalog": state.catalog()})
            elif route in ('/download-queue', '/download-queue/snapshot'):
                self._send(200, state.download_queue.snapshot())
            elif route == "/token":
                self._send(200, state.tokens.status())
            elif route == '/connectors/status':
                self._send(200, state.connectors.status())
            elif route == '/autoloop/status':
                self._send(200, state._autoloop_helper().status())
            elif route == '/autoloop/models':
                self._send(200, state._autoloop_helper().models())
            elif route == '/runtime/modes':
                source=state.resources/'runtime-setup/hf-extension/native-model-bindings.json'
                data=_read_json(source,16*1024*1024)
                fields=('id','variantOf','memoryMode','modeProfiles','modeAliasOf','contextTokens','publishedContextTokens','operatingModes','supportsTextGeneration','category','artifacts','precision')
                self._send(200,{'models':[{key:model[key] for key in fields if key in model} for model in data['models']]})
            elif route == '/remote/status':
                self._send(200, state.remote.status())
            elif route == '/specialist/ocr/status':
                self._send(200, state.ocr.status())
            elif route == '/specialist/computer/status':
                self._send(200, state.computer.status())
            elif route == '/images/jobs':
                self._send(200, {'jobs':state.images.list()})
            elif route == '/images/conversations':
                self._send(200, {'conversations':state.images.conversation_summaries()})
            elif route == '/mobile/host/poll':
                devices = lambda: set(state.remote.devices.copy()) if state.remote.server else set()
                self._send(200, state.remote.mobile.take(devices,wait=20))
            elif route == '/mobile/host/preferences':
                self._send(200, {'values': dict(state.remote.mobile.preferences)})
            elif route == "/v1/models":
                self._send(200, state.runtime.models(state.catalog()))
            else:
                self._send(404, {"error": "Unknown bridge endpoint"})
            return
        if self.command == "DELETE" and route == "/token":
            self._send(200, state.tokens.clear())
            return
        body = self._body(48 * 1024 * 1024 if route.startswith('/mobile/host/') else 8 * 1024 * 1024 if route in ('/specialist/ocr', '/specialist/computer') else 2 * 1024 * 1024 if route == "/v1/chat/completions" else 512 * 1024 if route.startswith('/groups/') else 256 * 1024)
        if body is None:
            return
        if self.command == "PUT" and route == "/token":
            self._send(200, state.tokens.save(body.get("token"), state.client))
        elif self.command != "POST":
            self._send(405, {"error": "Method not allowed"})
        elif route.startswith('/groups/'):
            self._send(200, state.groups.local(route[len('/groups/'):], body,
                base_url='http://127.0.0.1:' + str(self.server.server_port)))
        elif route.startswith('/autoloop/'):
            manager = state._autoloop_helper()
            action = route[len('/autoloop/'):]
            if action == 'inspect': self._send(200, manager.inspect(body))
            elif action == 'start': self._send(202, manager.start(body))
            elif action == 'cancel': self._send(200, manager.cancel(body.get('runId')))
            elif action == 'report': self._send(200, manager.report(body.get('runId')))
            else: self._send(404, {'error':'Unknown AutoLoop action'})
        elif route=='/prompts/improve':
            runtime=state._memory_runtime(body.get('runtime'))
            with state._memory_lock:
                if state.prompt_assistant is None:
                    memory=state._memory_helper()
                    state.prompt_assistant=PromptAssistant(memory.data,allowed_ports={runtime['backendPort'],runtime['echoPort']})
                    state.prompt_assistant._discarded_sessions=memory._discarded_sessions
                state.prompt_assistant.allowed_ports=frozenset((runtime['backendPort'],runtime['echoPort']))
            self._send(200,state.prompt_assistant.improve({**body,'runtime':runtime}))
        elif route in ('/memory/recover', '/memory/revalidate', '/memory/receipts'):
            self._send(200, state.memory_action(route.rsplit('/', 1)[1], body))
        elif route == "/plan":
            self._send(200, state.plan(body.get("repository")))
        elif route == "/search":
            self._send(200, state.client.search_models(body))
        elif route == '/remote/start':
            self._send(200, state.remote.start(body))
        elif route == '/remote/stop':
            self._send(200, state.remote.close())
        elif route == '/remote/pairing':
            self._send(200, state.remote.pairing(body.get('address')))
        elif route == '/remote/revoke':
            self._send(200, state.remote.revoke(body.get('deviceId')))
        elif route == '/remote/revoke-disconnected':
            self._send(200, state.remote.revoke_disconnected())
        elif route == '/remote/name':
            self._send(200, state.remote.rename(body.get('name')))
        elif route == '/remote/ddns':
            self._send(200, state.remote.configure_ddns(body.get('domain'), body.get('token')))
        elif route == '/remote/permanent':
            self._send(200, state.remote.ensure_permanent())
        elif route == '/mobile/host/result':
            self._send(200, state.remote.mobile.result(body, set(state.remote.devices)))
        elif route == '/mobile/host/event':
            self._send(200, state.remote.mobile.event(body, set(state.remote.devices)))
        elif route == '/mobile/host/preferences':
            self._send(200, state.remote.mobile.publish_preferences(body.get('values')))
        elif route == '/private/open':
            backend = state.runtime.private_backend(body.get('runtime'))
            self._send(200, state.private.open(backend=backend))
        elif route == '/private/close':
            self._send(200, state.private.close(body.get('sessionId')))
        elif route == '/private/send':
            raw = state.private.send(body.get('sessionId'), body.get('text'))
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                origin = self.headers.get('Origin')
                if origin in TAURI_ORIGINS:
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Vary', 'Origin')
                self.end_headers()
                self.close_connection = True
                for chunk in raw: self.wfile.write(chunk); self.wfile.flush()
            finally: raw.close()
        elif route == '/telemetry':
            runtime = body.get('runtime') or {}
            if not isinstance(runtime,dict): raise ValueError('A valid runtime is required')
            conversation=body.get('conversationId')
            if conversation is not None and (not isinstance(conversation,str) or len(conversation)>500):
                raise ValueError('A valid conversation is required')
            entry=next((e for e in state.entries.values() if e.get('profileId')==body.get('profile') and body.get('profile')),None)
            source=getattr(state.runtime,'telemetry_source',lambda *args:None)(entry['id'],conversation) if entry and conversation else None
            if source:
                value=state.live_telemetry.read(source[0],conversation,source[1])
                value['runtime']={'status':'running','profile':body['profile'],'contextSize':value.get('context',{}).get('modelContextTokens') or entry['contextTokens']}
                return self._send(200,value)
            if runtime.get('status') == 'running': runtime = state._memory_runtime(runtime)
            self._send(200, state.live_telemetry.read(runtime, conversation))
        elif route == '/connectors/disconnect':
            self._send(200, state.connectors.disconnect(body.get('id')))
        elif route == '/connectors/reconnect':
            self._send(200, state.connectors.reconnect(body.get('id')))
        elif route == '/connectors/history-enable':
            self._send(200, state.connectors.enable_history(body.get('id')))
        elif route == '/runtime/mode':
            self._send(200, state.runtime_mode(body.get('id'),body.get('operatingMode',body.get('mode'))))
        elif route == '/runtime/native-profile':
            from native_modes import activate
            self._send(200,activate(state.root,state.resources,body.get('id'),body.get('operatingMode')))
        elif route == "/catalog/add":
            self._send(200, state.add(body.get("planId"), body.get("variantIds")))
        elif route == "/catalog/profile":
            self._send(200, state.profile(body))
        elif route == '/download-queue/action':
            self._send(200, state.download_queue.action(body))
        elif route == "/install":
            self._send(202, state.start(body.get("id"), "download"))
        elif route == "/prepare":
            self._send(202, state.start(body.get("id"), "prepare"))
        elif route == '/specialist/computer':
            with state.lock:
                if state.progress and state.progress.get('status') in ACTIVE:
                    raise Conflict('Wait for the active installation before requesting a computer-use prediction')
                entry = state.entry(body.get('id'), installed=True)
                self._send(202, state.computer.start(entry, body))
        elif route == '/specialist/computer/stop':
            self._send(200, state.computer.stop())
        elif route == '/specialist/ocr':
            with state.lock:
                if state.progress and state.progress.get('status') in ACTIVE:
                    raise Conflict('Wait for the active installation to finish before using OCR')
                entry = state.entry(body.get('id'), installed=True)
                self._send(202, state.ocr.start(entry, body.get('image')))
        elif route=='/images/upscaler/prepare':
            self._send(200,state.images.prepare_upscaler())
        elif route=='/images/generate':
            with state.lock:
                if state.progress and state.progress.get('status') in ACTIVE:raise Conflict('Wait for the active installation before generating an image')
                entry=state.entry((body.get('request') or {}).get('modelId'),installed=True)
                self._send(202,state.images.start(entry,body.get('request'),on_ready=lambda proof:state.image_generated(entry,proof)))
        elif route=='/images/preview':
            self._send(200,state.images.preview(body.get('id'),body.get('path')))
        elif route=='/images/cancel':
            self._send(200,state.images.stop(body.get('id')))
        elif route=='/conversations/list':
            appdata=os.environ.get('APPDATA')
            if not appdata:raise ValueError('The OpenCore application data directory is unavailable')
            self._send(200,saved_conversations_page(Path(appdata)/'ai.opencore.control-center',body.get('after'),body.get('limit',200)))
        elif route=='/projects/delete-empty':
            appdata=os.environ.get('APPDATA')
            if not appdata:raise ValueError('The OpenCore application data directory is unavailable')
            self._send(200,delete_empty_projects(Path(appdata)/'ai.opencore.control-center',body.get('ids')))
        elif route=='/images/conversation':
            self._send(200,{'entries':state.images.conversation_entries(body.get('id'))})
        elif route=='/images/conversation/update':
            self._send(200,state.images.update_conversation(body))
        elif route in ('/images/conversation/delete-check','/images/conversation/delete'):
            appdata=os.environ.get('APPDATA')
            if not appdata:raise ValueError('The OpenCore application data directory is unavailable')
            active=getattr(state.runtime,'_active',None) or {}
            urls=['http://127.0.0.1:8813']+([active['url']] if active.get('url') else [])
            media=ChatMedia(Path(appdata)/'ai.opencore.control-center',state.images,state.root,urls)
            if route.endswith('delete-check'):
                media.check(body.get('id'));self._send(200,{'allowed':True})
            else:self._send(200,media.delete(body.get('id')))
        elif route == "/pause":
            self._send(200, state.pause(body.get("jobId")))
        elif route == '/cancel':
            self._send(202, state.cancel(body.get('jobId')))
        elif route == '/native/cancel':
            self._send(200, state.discard_native_partials(body.get('id')))
        elif route == "/stop":
            self._send(200, state.stop())
        elif route == "/uninstall":
            self._send(200,state.uninstall(body.get('id'),body.get('confirmationToken')))
        elif route == '/removal-plan':
            self._send(200,state.removal_plan(body.get('id')))
        elif route == '/generation/settings':
            self._send(200,state.generation_settings(body))
        elif route in ("/context", "/context/release"):
            method = getattr(state.runtime, "set_context" if route == "/context" else "release_context", None)
            if method is None:
                raise ValueError("This runtime does not support conversation contexts")
            fields = ("modelId", "conversationId", "requestId") if route == "/context" else ("requestId",)
            if any(not isinstance(body.get(f), str) or not 1 <= len(body[f]) <= 200 or any(ord(c) < 32 for c in body[f]) for f in fields):
                raise ValueError("Valid conversation and request IDs are required")
            if route == "/context":
                state.entry(body["modelId"], installed=True)
            self._send(200, method(*(body[f] for f in fields)))
        elif route == "/v1/chat/completions":
            entry = state.entry(body.get("model"), installed=True)
            with state.lock:
                if state.progress and state.progress.get("status") in ACTIVE:
                    raise Conflict("Wait for the active managed model job before generating")
            if not isinstance(body.get("messages"), list) or not body["messages"]:
                raise ValueError("Chat messages are required")
            raw, content_type = state.runtime.chat(entry, body)
            if content_type not in ("application/json", "text/event-stream") or not (isinstance(raw,bytes) or content_type=='text/event-stream' and hasattr(raw,'__iter__')):
                raise ValueError("The managed runtime returned an invalid response")
            if isinstance(raw,bytes):
                self._send(200, raw, content_type)
            else:
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.send_header('Cache-Control','no-store')
                self.send_header('Connection','close')
                origin=self.headers.get('Origin')
                if origin in TAURI_ORIGINS:
                    self.send_header('Access-Control-Allow-Origin',origin)
                    self.send_header('Vary','Origin')
                self.end_headers()
                self.close_connection=True
                try:
                    for chunk in raw:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                finally:
                    close=getattr(raw,'close',None)
                    if close:close()
        else:
            self._send(404, {"error": "Unknown bridge endpoint"})

    def _handle(self):
        try:
            self._dispatch()
        except Conflict as error:
            self._send(409, {"error": str(error)})
        except HubError as error:
            self._send(403 if error.status in (401, 403, 404) else 502, {"error": str(error), "authRequired": error.status in (401, 403, 404)})
        except PermissionError as error:
            self._send(403, {"error": str(error)[:1000]})
        except ValueError as error:
            self._send(400, {"error": str(error)[:1000]})
        except (BrokenPipeError, ConnectionError, socket.timeout):
            pass
        except Exception:
            self._send(500, {"error": "The managed bridge could not complete this request"})
    do_GET = do_POST = do_PUT = do_DELETE = _handle


def process_info(pid):
    """Read actual executable and creation identity, including Python launcher stubs."""
    if os.name != "nt":
        try:
            stat = Path("/proc", str(pid), "stat").read_text()
            rest = stat[stat.rfind(")") + 2:].split()
            return {"pid": pid, "parentPid": int(rest[1]), "path": str(Path("/proc", str(pid), "exe").resolve()), "created": rest[19]}
        except (OSError, ValueError, IndexError):
            return None
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD), ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes, kernel.CreateToolhelp32Snapshot.restype = [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return None
    parent, entry = None, ProcessEntry()
    entry.size = ctypes.sizeof(entry)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.pid == pid:
                parent = entry.parent
                break
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if parent is None:
        return None
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        return {"pid": pid, "parentPid": parent, "path": "", "created": ""}
    try:
        if kernel.WaitForSingleObject(handle, 0) != 258:
            return None
        size, buffer = wintypes.DWORD(32768), ctypes.create_unicode_buffer(32768)
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) or not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return {"pid": pid, "parentPid": parent, "path": "", "created": ""}
        created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        return {"pid": pid, "parentPid": parent, "path": buffer.value, "created": created}
    finally:
        kernel.CloseHandle(handle)


def _normal_path(value):
    return os.path.normcase(str(Path(value).resolve())).removeprefix("\\\\?\\")


def find_owner(root, resources, *, start_pid=None, process_info=process_info):
    resources = Path(resources).resolve()
    candidates = {resources / "OpenCore.exe", resources.parent / "OpenCore.exe", Path(root).resolve() / "OpenCore.exe"}
    candidates = {_normal_path(p) for p in candidates}
    current, visited = start_pid or os.getpid(), set()
    for _ in range(64):
        if current in visited or not current:
            break
        visited.add(current)
        item = process_info(current)
        if not item:
            break
        if item.get("path") and _normal_path(item["path"]) in candidates and item.get("created"):
            return {k: item[k] for k in ("pid", "path", "created")}
        current = item.get("parentPid")
    return None


def owner_alive(owner, *, process_info=process_info):
    if not isinstance(owner, dict) or type(owner.get("pid")) is not int or not owner.get("path") or not owner.get("created"):
        return False
    current = process_info(owner["pid"])
    return bool(current and current.get("path") and current.get("created") == owner["created"] and _normal_path(current["path"]) == _normal_path(owner["path"]))


@contextmanager
def _startup_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = managed_path(directory, "startup.lock")
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + 70
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("The managed Hub bridge is still starting")
                time.sleep(.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _existing_descriptor(path, root, resources, owner):
    try:
        descriptor = _read_json(path, 65536)
        if descriptor.get("schema") != 1 or descriptor.get("owner") != owner or descriptor.get("root") != str(root) or descriptor.get("resources") != str(resources):
            return None
        parsed = urlsplit(descriptor.get("url", ""))
        secret = descriptor.get("secret")
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path or parsed.query or parsed.fragment or not parsed.port or not isinstance(secret, str) or len(secret) < 32:
            return None
        if not owner_alive(descriptor.get("process")):
            return None
        request = Request(descriptor["url"] + "/health", headers={AUTH_HEADER: secret})
        with urlopen(request, timeout=1) as response:
            value = json.loads(response.read(16 * 1024 * 1024 + 1))
        if value.get("instanceId") != descriptor.get("instanceId") or value.get("healthy") is not True:
            return None
        return {"available": True, "url": descriptor["url"], "baseUrl": descriptor["url"] + "/v1", "secret": secret, "authHeader": AUTH_HEADER}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def ensure_service(root, resources):
    """Called by setup inventory; only a verified installed OpenCore ancestor may start it."""
    root = Path(root).resolve()
    resources = Path(resources or Path(__file__).resolve().parents[1]).resolve()
    owner = find_owner(root, resources)
    if owner is None:
        return {"available": False, "reason": "The managed Hub bridge requires the installed OpenCore desktop process"}
    directory = managed_path(root, "runtime-setup", "hub-bridge")
    descriptor = managed_path(directory, "service.json")
    try:
        with _startup_lock(directory):
            existing = _existing_descriptor(descriptor, root, resources, owner)
            if existing:
                return existing
            command = [sys.executable, "-I", str(Path(__file__).resolve()), "--serve", "--root", str(root), "--resources", str(resources),
                       "--owner-pid", str(owner["pid"]), "--owner-created", owner["created"], "--owner-path", owner["path"]]
            options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options["start_new_session"] = True
            process = subprocess.Popen(command, **options)
            # Initial catalog and remote-access setup can take longer on a cold
            # machine. Keep the lock while the child is starting so concurrent
            # callers do not start duplicate services.
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                existing = _existing_descriptor(descriptor, root, resources, owner)
                if existing:
                    return existing
                if process.poll() is not None:
                    break
                time.sleep(.05)
            return {"available": False, "reason": "The managed Hub bridge could not start"}
    except (OSError, ValueError, RuntimeError):
        return {"available": False, "reason": "The managed Hub bridge is unavailable"}


def serve(root, resources, owner):
    if not owner_alive(owner):
        return 1
    from hf_bridge_policy import retire_automatic_plugin, retire_legacy_files, retire_legacy_auth
    state = BridgeState(root, resources)
    state.owner = owner
    remote_config = managed_path(Path(root).resolve(), 'runtime-setup', 'remote-access.json')
    if remote_config.exists():
        from remote_access import RemoteAccess
        state.remote = RemoteAccess(state.root, state.resources, owner)
        state.remote.restore()
    secret = secrets.token_urlsafe(48)
    server = BridgeServer(("127.0.0.1", 0), state, secret)
    directory = managed_path(Path(root).resolve(), "runtime-setup", "hub-bridge")
    descriptor = managed_path(directory, "service.json")
    current = process_info(os.getpid())
    atomic_json(descriptor, {"schema": 1, "root": str(Path(root).resolve()), "resources": str(Path(resources).resolve()),
        "owner": owner, "process": {k: current[k] for k in ("pid", "path", "created")}, "secret": secret,
        "url": "http://127.0.0.1:" + str(server.server_port), "instanceId": server.instance_id})
    def retire_legacy_plugin():
        actions=[lambda:retire_automatic_plugin(resources=resources)]
        if os.environ.get('APPDATA'):
            app_data=Path(os.environ['APPDATA'])/'ai.opencore.control-center'
            actions.extend((lambda:retire_legacy_auth(app_data),lambda:retire_legacy_files(app_data)))
        for action in actions:
            try:action()
            except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired):
                print('Legacy Claude bridge cleanup could not finish; it will be retried on the next launch',file=sys.stderr)
    threading.Thread(target=retire_legacy_plugin,daemon=True).start()
    finished = threading.Event()
    def watch_owner():
        while not finished.wait(2):
            if not owner_alive(owner):
                state.close()
                server.shutdown()
                return
    threading.Thread(target=watch_owner, daemon=True).start()
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        finished.set()
        state.close()
        server.server_close()
        try:
            if _read_json(descriptor, 65536).get("secret") == secret:
                descriptor.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--owner-pid", type=int, required=True)
    parser.add_argument("--owner-created", required=True)
    parser.add_argument("--owner-path", required=True)
    args = parser.parse_args()
    owner = {"pid": args.owner_pid, "path": args.owner_path, "created": args.owner_created}
    # The CLI cannot substitute an arbitrary owner executable for OpenCore.
    candidates = {_normal_path(args.resources / "OpenCore.exe"), _normal_path(args.resources.parent / "OpenCore.exe"), _normal_path(args.root / "OpenCore.exe")}
    if _normal_path(owner["path"]) not in candidates:
        return 1
    return serve(args.root, args.resources, owner)


if __name__ == "__main__":
    raise SystemExit(main())
