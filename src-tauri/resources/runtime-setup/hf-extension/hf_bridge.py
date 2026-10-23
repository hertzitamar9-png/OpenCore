"""OpenCore-owned loopback Hub bridge. Importing this module starts no service."""
import argparse
from contextlib import contextmanager
import copy
import ctypes
from ctypes import wintypes
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

# The bundled interpreter is launched with -I; only this managed extension folder is admitted.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from hf_catalog import build_plan, parse_repository
from hf_download import Downloader, HubClient, HubError, Paused, atomic_json, managed_path, validate_entry
from hf_security import TokenStore

AUTH_HEADER = "X-OpenCore-Hub-Secret"
TAURI_ORIGINS = {"http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"}
ID = re.compile(r"hf-[0-9a-f]{24}\Z")
ACTIVE = {"running", "pausing"}


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
        self.progress, self.worker = None, None
        self.pause_event, self.closed = threading.Event(), False
        self._load_catalog()
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

    def _load_catalog(self):
        try:
            saved = _read_json(self.catalog_path)
        except FileNotFoundError:
            return
        if not isinstance(saved, dict) or saved.get("schema") != 1 or not isinstance(saved.get("entries"), list) or len(saved["entries"]) > 2000:
            raise ValueError("Managed Hugging Face catalog is invalid")
        for entry in saved["entries"]:
            if not isinstance(entry, dict) or not ID.fullmatch(str(entry.get("id", ""))):
                raise ValueError("Managed Hugging Face catalog has an invalid model ID")
            parse_repository(entry.get("repository"))
            if entry.get("installable") is True:
                validate_entry(entry)
            self.entries[entry["id"]] = entry

    def persist_catalog(self):
        atomic_json(self.catalog_path, {"schema": 1, "entries": list(self.entries.values())})

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
        preparation = entry.get("runtimePreparation") or {}
        result["runtimeReady"] = bool(receipt and preparation.get("runtimeReady") is True and preparation.get("receiptRevision") == entry["revision"])
        if receipt:
            result.update(localDirectory=receipt["localDirectory"], receiptPath=receipt["receiptPath"], installReceipt=copy.deepcopy(receipt))
        if self.progress and self.progress.get("id") == entry["id"] and self.progress.get("status") in ACTIVE | {"paused", "failed"}:
            result["downloadProgress"] = copy.deepcopy(self.progress)
        return result

    def catalog(self):
        with self.lock:
            return [self._hydrate(x) for x in self.entries.values()]

    def status(self):
        with self.lock:
            return {"schema": 1, "healthy": True, "catalog": self.catalog(), "progress": copy.deepcopy(self.progress),
                    "tokenConfigured": self.tokens.status()["configured"], "runtimeAdapterAvailable": not isinstance(self.runtime, RuntimeUnavailable)}

    def plan(self, repository):
        plan = build_plan(self.client.metadata(parse_repository(repository)))
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
            if len(set(self.entries) | wanted) > 2000:
                raise ValueError("The managed model catalog is full")
            if self.progress and self.progress.get("status") in ACTIVE and self.progress.get("id") in wanted:
                raise Conflict("Wait for or pause the active model job before updating its metadata")
            for entry in variants:
                previous = self.entries.get(entry["id"], {})
                for field in ("profileId", "providerId"):
                    if previous.get(field):
                        entry[field] = previous[field]
                if previous.get("revision") == entry["revision"] and previous.get("files") == entry["files"] and previous.get("runtimePreparation"):
                    entry["runtimePreparation"] = previous["runtimePreparation"]
                self.entries[entry["id"]] = entry
            self.persist_catalog()
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
            entry = self.entry(model_id, installed=kind == "prepare")
            validate_entry(entry)
            self.pause_event = threading.Event()
            self.progress = {"jobId": secrets.token_hex(16), "id": model_id, "kind": kind, "status": "running",
                             "stage": "preparing" if kind == "prepare" else "queued", "bytesDone": 0, "bytesTotal": entry["bytes"], "startedAt": time.time()}
            initial = copy.deepcopy(self.progress)
            self._persist_progress()
            self.worker = threading.Thread(target=self._work, args=(entry, kind, self.pause_event), daemon=True)
            self.worker.start()
            return initial

    def _work(self, entry, kind, pause):
        last_saved = [0.0]
        def report(value):
            with self.lock:
                self.progress.update(value, updatedAt=time.time())
                if time.monotonic() - last_saved[0] > 1:
                    self._persist_progress()
                    last_saved[0] = time.monotonic()
        try:
            if kind == "download":
                result = self.downloader.install(entry, pause, report)
                final = {"status": "installed", "stage": "installed", "bytesDone": entry["bytes"]}
                if entry.get('runtime',{}).get('adapter') and getattr(self.runtime,'automatic_preparation',False):
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
                        self.entries[entry['id']]['runtimePreparation']={**result,'receiptRevision':entry['revision']}
                        self.persist_catalog()
                    final={'status':'ready','stage':'ready','bytesDone':entry['bytes'],'bytesTotal':entry['bytes']}
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
                self._persist_progress()

    def pause(self, job_id=None):
        with self.lock:
            if not self.progress or self.progress.get("status") not in ACTIVE:
                return {"paused": False, "progress": copy.deepcopy(self.progress)}
            if job_id is not None and job_id != self.progress["jobId"]:
                raise Conflict("The requested job is no longer the active model job")
            self.pause_event.set()
            self.progress.update(status="pausing", stage="pausing")
            if self.progress.get('kind')=='prepare' or self.progress.get('stage') not in ('queued','downloading','verifying'):
                cancellation=getattr(self.runtime,'_cancel',None)
                if cancellation:cancellation.set()
                else:self.runtime.stop()
            self._persist_progress()
            return {"paused": True, "progress": copy.deepcopy(self.progress)}

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
        result = self.runtime.stop()
        return result if isinstance(result, dict) else {"stopped": True}

    def uninstall(self, model_id):
        with self.lock:
            if self.progress and self.progress.get('status') in ACTIVE:
                raise Conflict('Pause or finish the current installation before removing model files')
            entry=self.entry(model_id)
            if getattr(self.runtime,'_generation',None) and self.runtime._generation.locked():
                raise Conflict('Wait for the current response before removing this model')
            if getattr(self.runtime,'_active',None) and self.runtime._active['id']==model_id:
                self.runtime.stop()
            folder=managed_path(managed_path(self.root,'models','hub'),entry['id'])
            if folder.is_dir():shutil.rmtree(folder)
            self.entries[model_id].pop('runtimePreparation',None)
            self.receipt_cache.pop(model_id,None)
            self.persist_catalog()
            return {'uninstalled':True,'id':model_id}

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.pause_event.set()
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
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-OpenCore-Hub-Secret, Authorization")
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
            if not origin or self.headers.get("Access-Control-Request-Method") not in {"GET", "POST", "PUT", "DELETE"} or not requested <= {"content-type", AUTH_HEADER.lower(), "authorization"}:
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
        if not self._authorize():
            return
        path = urlsplit(self.path).path
        if urlsplit(self.path).query or urlsplit(self.path).fragment:
            raise ValueError("Query parameters are not accepted")
        route = path[4:] if path.startswith("/api/") else path
        state = self.server.state
        if self.command == "GET":
            if route in ("/status", "/health"):
                self._send(200, {**state.status(), "instanceId": self.server.instance_id})
            elif route == "/catalog":
                self._send(200, {"catalog": state.catalog()})
            elif route == "/token":
                self._send(200, state.tokens.status())
            elif route == "/v1/models":
                self._send(200, state.runtime.models(state.catalog()))
            else:
                self._send(404, {"error": "Unknown bridge endpoint"})
            return
        if self.command == "DELETE" and route == "/token":
            self._send(200, state.tokens.clear())
            return
        body = self._body(2 * 1024 * 1024 if route == "/v1/chat/completions" else 256 * 1024)
        if body is None:
            return
        if self.command == "PUT" and route == "/token":
            self._send(200, state.tokens.save(body.get("token"), state.client))
        elif self.command != "POST":
            self._send(405, {"error": "Method not allowed"})
        elif route == "/plan":
            self._send(200, state.plan(body.get("repository")))
        elif route == "/catalog/add":
            self._send(200, state.add(body.get("planId"), body.get("variantIds")))
        elif route == "/catalog/profile":
            self._send(200, state.profile(body))
        elif route == "/install":
            self._send(202, state.start(body.get("id"), "download"))
        elif route == "/prepare":
            self._send(202, state.start(body.get("id"), "prepare"))
        elif route == "/pause":
            self._send(200, state.pause(body.get("jobId")))
        elif route == "/stop":
            self._send(200, state.stop())
        elif route == "/uninstall":
            self._send(200,state.uninstall(body.get('id')))
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
        deadline = time.monotonic() + 8
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
            deadline = time.monotonic() + 8
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
    state = BridgeState(root, resources)
    secret = secrets.token_urlsafe(48)
    server = BridgeServer(("127.0.0.1", 0), state, secret)
    directory = managed_path(Path(root).resolve(), "runtime-setup", "hub-bridge")
    descriptor = managed_path(directory, "service.json")
    current = process_info(os.getpid())
    atomic_json(descriptor, {"schema": 1, "root": str(Path(root).resolve()), "resources": str(Path(resources).resolve()),
        "owner": owner, "process": {k: current[k] for k in ("pid", "path", "created")}, "secret": secret,
        "url": "http://127.0.0.1:" + str(server.server_port), "instanceId": server.instance_id})
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
