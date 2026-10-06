"""OpenCore host for the existing YuE installation; no model or environment copy.

Cancellation is acknowledged separately from worker cleanup so the UI responds
immediately without permitting a second model to take the GPU prematurely.
"""
import hashlib
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

HOST_VERSION = 1


class ManagedJob:
    def __init__(self):
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.worker_done = threading.Event()
        self.worker_done.set()
        self._state = {"status": "idle", "worker_active": False}

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        # The publisher handler assigns the new run while holding JOB.lock.
        self._state = dict(value, worker_active=value.get("status") == "running")
        if self._state["worker_active"]:
            self.worker_done.clear()

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self._state))

    def begin(self, **values):
        with self.lock:
            if self._state.get("worker_active"):
                raise RuntimeError("The previous worker is still releasing resources")
            self.cancel.clear()
            self.state = dict(values, status="running")

    def update(self, **values):
        with self.lock:
            self._state.update(values)
            if self.cancel.is_set():
                self._state.update(status="cancelled", stage="Cancelled", error=None)

    def tick(self):
        with self.lock:
            if not self.cancel.is_set():
                self._state["done"] = self._state.get("done", 0) + 1

    def request_cancel(self, expected_run=None):
        with self.lock:
            if expected_run is not None and self._state.get("run") != expected_run:
                raise ValueError("Music Studio switched to a different generation; no cancellation was sent")
            if self._state.get("worker_active"):
                self.cancel.set()
                self._state.update(status="cancelled", stage="Cancelled", error=None)
            return self.snapshot()

    def finish(self, cleanup_error=None):
        with self.lock:
            self._state.update(worker_active=cleanup_error is not None, worker_finished=True)
            if cleanup_error:
                self._state.update(cleanup_error=str(cleanup_error), stage="Resource cleanup needs a retry")
                if not self.cancel.is_set():
                    self._state.update(status="error", error=f"Could not release model resources: {cleanup_error}")
            else:
                self._state.pop("cleanup_error", None)
            if self.cancel.is_set():
                self._state.update(status="cancelled", stage="Cancelled", error=None)
            self.worker_done.set()


def cancellable_sha256(path, event, chunk_bytes=1024 * 1024, checkpoint=None):
    """Retain integrity verification, checking cancellation between disk reads."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            if event.is_set():
                raise InterruptedError("Cancelled during file verification")
            block = stream.read(chunk_bytes)
            if not block:
                break
            digest.update(block)
            if checkpoint:
                checkpoint()
    if event.is_set():
        raise InterruptedError("Cancelled during file verification")
    return digest.hexdigest()


def attach(server, *, install_http=True, storage_modules=None):
    """Adapt the installed publisher server without rewriting its source files."""
    server.JOB = ManagedJob()
    job = server.JOB
    if storage_modules is None:
        from yue2 import storage, pipeline
        storage_modules = [storage, pipeline]
    for module in storage_modules:
        module.sha256_file = lambda path: cancellable_sha256(path, job.cancel)

    original_pipe = server.get_pipe
    def get_pipe(memory):
        if job.cancel.is_set():
            raise InterruptedError("Cancelled before model loading")
        pipe = original_pipe(memory)
        if job.cancel.is_set():
            raise InterruptedError("Cancelled during model loading")
        return pipe
    server.get_pipe = get_pipe

    def managed(worker, song=False):
        def run(*args):
            try:
                worker(*args)
            except Exception as error:
                if not job.cancel.is_set():
                    job.update(status="error", stage="Generation failed", error=f"{type(error).__name__}: {error}")
            finally:
                cleanup_error = None
                try:
                    if song or job.cancel.is_set():
                        try:
                            server.unload_model()
                        except Exception as error:
                            cleanup_error = error
                    if song and job.cancel.is_set():
                        path = args[1] / "studio.json"
                        manifest = server.read_json(path) or {}
                        manifest.update(status="cancelled")
                        manifest.pop("error", None)
                        try:
                            server.write_json(path, manifest)
                        except OSError as error:
                            job.update(manifest_error=str(error))
                finally:
                    job.finish(cleanup_error)
        return run
    server.run_job = managed(server.run_job, song=True)
    server.load_model = managed(server.load_model)

    original_info = server.info
    host_revision = hashlib.sha256(Path(__file__).read_bytes() + Path(__file__).with_name("music_index.html").read_bytes()).hexdigest()
    def info():
        return dict(original_info(), opencore_host_version=HOST_VERSION,
                    opencore_host_revision=host_revision,
                    worker_active=job.snapshot().get("worker_active", False))
    server.info = info
    original_history = server.history
    def history():
        runs = original_history()
        current = job.snapshot()
        if current.get("status") == "cancelled":
            for run in runs:
                if run.get("run") == current.get("run"):
                    run.update(status="cancelled")
                    run.pop("error", None)
        return runs
    server.history = history

    if not install_http:
        return
    original_handler = server.Handler
    start_gate = threading.Lock()
    closing = threading.Event()
    index = Path(__file__).with_name("music_index.html")
    class Handler(original_handler):
        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/cancel":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 <= length <= 4096:
                        raise ValueError("Cancellation request is too large")
                    data = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(data, dict):
                        raise ValueError("Cancellation request must be an object")
                    expected = data.get("expectedRun")
                    if expected is not None and (not isinstance(expected, str) or not expected):
                        raise ValueError("expectedRun must be a nonempty generation ID")
                except (ValueError, TypeError) as error:
                    return self.send_json({"error": str(error)}, 400)
                try:
                    return self.send_json(dict(ok=True, **job.request_cancel(expected)))
                except ValueError as error:
                    return self.send_json({"error": str(error)}, 409)
            if path == "/api/shutdown":
                closing.set()
                job.request_cancel()
                self.send_json({"ok": True})
                def shutdown():
                    with start_gate:
                        # A request admitted just before closing may still be
                        # parsing. Cancel it after admission finishes, then wait.
                        job.request_cancel()
                        job.worker_done.wait()
                        server.HTTPD.shutdown()
                threading.Thread(target=shutdown, daemon=True).start()
                return
            if path in ("/api/generate", "/api/model/load", "/api/model/unload"):
                with start_gate:
                    if closing.is_set():
                        return self.send_json({"error": "Music Studio is shutting down."}, 409)
                    if path == "/api/model/unload" and job.worker_done.is_set():
                        try:
                            server.unload_model()
                        except Exception as error:
                            job.finish(error)
                            return self.send_json({"error": str(error)}, 500)
                        job.finish()
                        return self.send_json({"ok": True})
                    if job.snapshot().get("worker_active"):
                        return self.send_json({"error": "The previous worker is still releasing resources."}, 409)
                    return super().do_POST()
            return super().do_POST()

        def do_GET(self):
            if urlparse(self.path).path in ("/", "/index.html"):
                return self.send_file(index)
            return super().do_GET()
    server.Handler = Handler


def main():
    root = Path(os.environ["OPENCORE_MUSIC_HOME"]).resolve()
    source = root / "studio" / "server.py"
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("opencore_yue_server", source)
    server = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    attach(server)
    server.main()


if __name__ == "__main__":
    main()
