"""Cancellation tests use tiny local files and a fake worker, never model weights."""
import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from music_host import ManagedJob, cancellable_sha256, attach


class MusicCancellationTests(unittest.TestCase):
    def test_cancel_is_immediate_and_late_failure_cannot_replace_it(self):
        job = ManagedJob()
        job.begin(stage="Verifying model files", run="song")
        job.request_cancel()
        state = job.snapshot()
        self.assertEqual(state["status"], "cancelled")
        self.assertTrue(state["worker_active"])
        job.update(status="error", stage="Load failed", error="Late failure")
        job.update(status="done", stage="Finished")
        self.assertEqual(job.snapshot()["status"], "cancelled")
        self.assertIsNone(job.snapshot().get("error"))
        with self.assertRaises(RuntimeError):
            job.begin(stage="Another song")
        job.finish()
        self.assertFalse(job.snapshot()["worker_active"])
        job.begin(stage="Another song")
        self.assertEqual(job.snapshot()["status"], "running")

    def test_verification_stops_between_chunks_and_preserves_real_hashes(self):
        event = threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "weights.test"
            path.write_bytes(b"small local fixture" * 100)
            self.assertEqual(cancellable_sha256(path, event), hashlib.sha256(path.read_bytes()).hexdigest())
            calls = []
            def checkpoint():
                calls.append(True)
                event.set()
            with self.assertRaises(InterruptedError):
                cancellable_sha256(path, event, chunk_bytes=16, checkpoint=checkpoint)
            self.assertEqual(len(calls), 1)

    def test_worker_exception_after_cancel_releases_resources_and_keeps_cancelled_manifest(self):
        job = ManagedJob()
        released = []
        manifests = []
        def worker(_request, _directory):
            server.JOB.request_cancel()
            server.JOB.update(status="error", error="Late worker failure")
            raise ValueError("Late failure")
        server = SimpleNamespace(JOB=job, run_job=worker, load_model=lambda _memory: None,
            get_pipe=lambda _memory: object(), unload_model=lambda: released.append(True),
            write_json=lambda path, value: manifests.append(value), read_json=lambda *_args: {"status": "error", "error": "Late failure"},
            info=lambda: {"defaults": {}, "model_loaded": False}, history=lambda: [])
        attach(server, install_http=False, storage_modules=[])
        server.JOB.begin(stage="Verifying", run="song")
        server.run_job({}, Path("unused-fixture"))
        self.assertEqual(server.JOB.snapshot()["status"], "cancelled")
        self.assertFalse(server.JOB.snapshot()["worker_active"])
        self.assertEqual(released, [True])
        self.assertEqual(manifests[-1]["status"], "cancelled")
        self.assertNotIn("error", manifests[-1])

    def test_http_cancel_acknowledges_without_waiting_for_worker_and_rejects_overlap(self):
        class PublisherHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def send_json(self, data, status=200):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                self.send_json({"ok": True})
        server = SimpleNamespace(JOB=None,Handler=PublisherHandler,run_job=lambda *_args:None,
            load_model=lambda _memory:None,get_pipe=lambda _memory:object(),unload_model=lambda:None,
            info=lambda:{},history=lambda:[])
        attach(server, storage_modules=[])
        server.JOB.begin(stage="Verifying files", run="fixture")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        def post(path, data=None):
            connection = http.client.HTTPConnection(*httpd.server_address, timeout=2)
            try:
                connection.request("POST", path, body=json.dumps(data or {}).encode())
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        try:
            status, reply = post("/api/cancel", {"expectedRun": "older-generation"})
            self.assertEqual(status, 409)
            self.assertFalse(server.JOB.cancel.is_set())
            self.assertEqual(server.JOB.snapshot()["status"], "running")
            status, reply = post("/api/cancel", {"expectedRun": "fixture"})
            self.assertEqual(status, 200)
            self.assertEqual(reply["status"], "cancelled")
            self.assertTrue(reply["worker_active"])
            self.assertEqual(post("/api/generate")[0], 409)
            server.JOB.finish()
            self.assertEqual(post("/api/generate")[0], 200)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_shutdown_cancels_an_admitted_request_and_waits_for_its_worker(self):
        admitted = threading.Event()
        release_admission = threading.Event()
        shutdown_called = threading.Event()
        shutdown_states = []
        generate_result = []
        generate_errors = []

        class PublisherHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def send_json(self, data, status=200):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                # The publisher can still be parsing an admitted request while
                # shutdown arrives, before it records the new worker in JOB.
                admitted.set()
                if not release_admission.wait(timeout=2):
                    return self.send_json({"error": "Admission fixture timed out"}, 500)
                server.JOB.begin(stage="Starting", run="fixture")
                self.send_json({"ok": True, "run": "fixture"})

        def shutdown():
            shutdown_states.append(server.JOB.snapshot())
            shutdown_called.set()

        server = SimpleNamespace(JOB=None,Handler=PublisherHandler,run_job=lambda *_args:None,
            load_model=lambda _memory:None,get_pipe=lambda _memory:object(),unload_model=lambda:None,
            info=lambda:{},history=lambda:[],HTTPD=SimpleNamespace(shutdown=shutdown))
        attach(server, storage_modules=[])
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def post(path):
            connection = http.client.HTTPConnection(*httpd.server_address, timeout=2)
            try:
                connection.request("POST", path, body=b"{}")
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        def generate():
            try:
                generate_result.append(post("/api/generate"))
            except Exception as error:
                generate_errors.append(error)

        request_thread = threading.Thread(target=generate, daemon=True)
        request_thread.start()
        try:
            self.assertTrue(admitted.wait(timeout=2))
            self.assertEqual(post("/api/shutdown"), (200, {"ok": True}))
            self.assertFalse(shutdown_called.is_set())
            release_admission.set()
            request_thread.join(timeout=2)
            self.assertFalse(request_thread.is_alive())
            self.assertEqual(generate_errors, [])
            self.assertEqual(generate_result, [(200, {"ok": True, "run": "fixture"})])
            self.assertTrue(server.JOB.cancel.wait(timeout=2), "Shutdown must cancel a request admitted before closing")
            self.assertEqual(server.JOB.snapshot()["status"], "cancelled")
            self.assertTrue(server.JOB.snapshot()["worker_active"])
            self.assertFalse(shutdown_called.is_set(), "HTTP shutdown must wait for worker cleanup")
            server.JOB.finish()
            self.assertTrue(shutdown_called.wait(timeout=2))
            self.assertEqual(len(shutdown_states), 1)
            self.assertFalse(shutdown_states[0]["worker_active"])
            self.assertEqual(post("/api/generate")[0], 409)
        finally:
            release_admission.set()
            server.JOB.finish()
            request_thread.join(timeout=2)
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_completed_song_releases_pipeline_before_worker_is_marked_idle(self):
        observed = []
        server = SimpleNamespace(JOB=None,run_job=lambda *_args:None,load_model=lambda _memory:None,
            get_pipe=lambda _memory:object(),unload_model=lambda:observed.append(server.JOB.snapshot()["worker_active"]),
            info=lambda:{},history=lambda:[])
        attach(server, install_http=False, storage_modules=[])
        server.JOB.begin(stage="Generating")
        server.run_job({}, Path("unused-fixture"))
        self.assertEqual(observed, [True])
        self.assertFalse(server.JOB.snapshot()["worker_active"])

    def test_cleanup_failure_keeps_reservation_and_unexpected_worker_error_is_terminal(self):
        def worker(*_args):
            raise OSError("Cannot write the initial manifest")
        def unload():
            raise RuntimeError("Engine has not closed yet")
        server = SimpleNamespace(JOB=None,run_job=worker,load_model=lambda _memory:None,
            get_pipe=lambda _memory:object(),unload_model=unload,info=lambda:{},history=lambda:[])
        attach(server, install_http=False, storage_modules=[])
        server.JOB.begin(stage="Starting")
        server.run_job({}, Path("unused-fixture"))
        state = server.JOB.snapshot()
        self.assertEqual(state["status"], "error")
        self.assertTrue(state["worker_active"])
        self.assertTrue(state["worker_finished"])
        self.assertIn("Engine has not closed yet", state["cleanup_error"])
        with self.assertRaises(RuntimeError):
            server.JOB.begin(stage="Another song")
        server.JOB.finish()
        self.assertFalse(server.JOB.snapshot()["worker_active"])
        self.assertEqual(server.JOB.snapshot()["status"], "error")


if __name__ == "__main__":
    unittest.main()
