"""Independent review regressions; fixtures never use installed weights or state.

Run: python -B -m unittest test_hf_backend -v
Live metadata only: set OPENCORE_REVIEW_LIVE_METADATA=1 before that command.
"""
from contextlib import closing
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request

import hf_bridge as bridge
import hf_catalog as catalog
import hf_download as transfer
import hf_runtime as runtime
from hf_security import TokenStore


DATA = b"independent-review-fixture\x00" * 8192
COMMIT = "e" * 40
MODEL_ID = "hf-" + "f" * 24


def fixture_entry():
    pin = {"repository": "review/model", "revision": COMMIT,
           "filename": "model-Q4_K_M.gguf", "bytes": len(DATA), "role": "model",
           "sha256": hashlib.sha256(DATA).hexdigest(), "gitBlobSha1": None, "pinned": True}
    return {"id": MODEL_ID, "repository": "review/model", "revision": COMMIT,
            "files": [pin], "bytes": len(DATA), "installable": True, "format": "gguf",
            "quantization": "Q4_K_M", "subdirectory": ""}


class HubFixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        state = self.server
        state.requests.append((self.path, self.headers.get("Authorization"), self.headers.get("Range")))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", state.redirect)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/api/models/"):
            if state.private and self.headers.get("Authorization") != "Bearer hf_review_fixture":
                self.send_error(401)
                return
            body = json.dumps({"id": "review/model", "sha": COMMIT,
                               "siblings": [], "private": state.private}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        offset = int((self.headers.get("Range") or "bytes=0-").split("=")[1].split("-")[0])
        if state.ignore_range:
            offset = 0
        content = b"x" * len(DATA) if state.corrupt else DATA
        self.send_response(206 if offset else 200)
        self.send_header("Content-Length", str(len(content) - offset))
        if offset:
            self.send_header("Content-Range", f"bytes {offset + int(state.bad_range)}-{len(content)-1}/{len(content)}")
        self.end_headers()
        try:
            self.wfile.write(content[offset:])
        except (BrokenPipeError, ConnectionError):
            pass


class RuntimeFixture:
    def __init__(self):
        self.stopped = threading.Event()

    def stop(self):
        self.stopped.set()
        return {"stopped": True}

    def models(self, _catalog):
        return {"object": "list", "data": []}


class LocalFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="hf-backend-review-")))
        self.hub = self.server(HubFixture)
        self.client = transfer.HubClient(lambda: "hf_review_fixture", endpoint=self.url(self.hub), allow_test_http=True)

    def server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.requests = []
        server.private = server.ignore_range = server.bad_range = server.corrupt = False
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    @staticmethod
    def url(server):
        return "http://127.0.0.1:" + str(server.server_port)

    def state(self, adapter=None):
        state = bridge.BridgeState(self.root, self.root / "resources", client=self.client,
                                   runtime=adapter or RuntimeFixture())
        self.addCleanup(state.close)
        return state

    def install_fixture(self):
        entry = fixture_entry()
        receipt = transfer.Downloader(self.root, self.client).install(entry, threading.Event(), lambda _: None)
        return {**entry, "status": "installed", "localDirectory": receipt["localDirectory"],
                "receiptPath": receipt["receiptPath"]}

    def test_anonymous_first_metadata_uses_no_global_hf_token(self):
        with patch.dict(os.environ, {"HF_TOKEN": "hf_external_must_not_be_used"}):
            self.client.metadata("review/model")
        self.assertEqual([item[1] for item in self.hub.requests], [None])
        self.hub.private = True
        self.client.metadata("review/model")
        self.assertEqual([item[1] for item in self.hub.requests[-2:]], [None, "Bearer hf_review_fixture"])

    def test_redirect_strips_token_when_host_or_port_changes(self):
        target = self.server(HubFixture)
        self.hub.redirect = self.url(target) + "/download"
        with self.client.open("/redirect", token="hf_review_fixture", anonymous_first=False) as response:
            self.assertEqual(response.read(), DATA)
        self.assertEqual(self.hub.requests[0][1], "Bearer hf_review_fixture")
        self.assertIsNone(target.requests[0][1])

    def test_redirect_rejects_untrusted_hosts_and_https_downgrades(self):
        redirect = transfer.SafeRedirect()
        for url in ("https://huggingface.co.evil.invalid/weight", "https://evil.invalid/weight", "http://huggingface.co/weight"):
            with self.subTest(url=url), self.assertRaises(transfer.HubError):
                redirect.redirect_request(Request("https://huggingface.co/review/model"), None, 302, "Found", {}, url)

    def test_pause_then_range_resume_publishes_only_verified_receipt(self):
        entry, pause = fixture_entry(), threading.Event()
        downloader = transfer.Downloader(self.root, self.client)
        def report(value):
            if value["stage"] == "downloading" and value["bytesDone"] >= 65536:
                pause.set()
        with self.assertRaises(transfer.Paused):
            downloader.install(entry, pause, report)
        directory = downloader.directory(entry)
        partial = directory / (entry["files"][0]["filename"] + ".partial")
        self.assertEqual(partial.stat().st_size, 65536)
        self.assertFalse((directory / "install-receipt.json").exists())
        pause.clear()
        receipt = downloader.install(entry, pause, lambda _: None)
        self.assertEqual(self.hub.requests[-1][2], "bytes=65536-")
        self.assertEqual(Path(receipt["files"][0]["localPath"]).read_bytes(), DATA)
        self.assertEqual(receipt["files"][0]["actualSha256"], hashlib.sha256(DATA).hexdigest())

    def test_bad_range_never_appends_to_pinned_partial(self):
        entry = fixture_entry()
        downloader = transfer.Downloader(self.root, self.client)
        directory = downloader.directory(entry)
        directory.mkdir(parents=True)
        partial = directory / (entry["files"][0]["filename"] + ".partial")
        partial.write_bytes(DATA[:32])
        transfer.atomic_json(partial.with_name(partial.name + ".json"), entry["files"][0])
        self.hub.bad_range = True
        with self.assertRaises(transfer.HubError):
            downloader.install(entry, threading.Event(), lambda _: None)
        self.assertEqual(partial.read_bytes(), DATA[:32])
        self.assertFalse((directory / "install-receipt.json").exists())

    def test_checksum_failure_is_not_installed_or_executable(self):
        self.hub.corrupt = True
        entry = fixture_entry()
        downloader = transfer.Downloader(self.root, self.client)
        with self.assertRaisesRegex(ValueError, "checksum"):
            downloader.install(entry, threading.Event(), lambda _: None)
        directory = downloader.directory(entry)
        self.assertFalse((directory / entry["files"][0]["filename"]).exists())
        self.assertFalse((directory / "install-receipt.json").exists())
        with self.assertRaises(ValueError):
            runtime.checked_model_files(entry, self.root)

    def test_traversal_and_windows_alias_filenames_are_rejected(self):
        for name in ("../outside.gguf", "x/../../outside.gguf", "C:/outside.gguf", "x:stream.gguf",
                     "CON.gguf", "x/COM1.txt", "x./model.gguf", "x /model.gguf", "x\\model.gguf"):
            entry = fixture_entry()
            entry["files"][0]["filename"] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                transfer.Downloader(self.root, self.client).install(entry, threading.Event(), lambda _: None)
        self.assertEqual(self.hub.requests, [])

    def test_http_auth_origin_host_and_preflight_are_enforced(self):
        state = self.state()
        server = bridge.BridgeServer(("127.0.0.1", 0), state, "review-secret")
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def request(headers, method="GET"):
            with closing(http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)) as connection:
                connection.request(method, "/health", headers=headers)
                response = connection.getresponse()
                data = response.read()
                return response.status, dict(response.headers), data
        self.assertEqual(request({})[0], 401)
        self.assertEqual(request({"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(request({bridge.AUTH_HEADER: "review-secret", "Origin": "https://evil.invalid"})[0], 403)
        self.assertEqual(request({bridge.AUTH_HEADER: "review-secret", "Host": "evil.invalid"})[0], 403)
        status, headers, body = request({"Authorization": "Bearer review-secret", "Origin": "https://tauri.localhost"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://tauri.localhost")
        self.assertNotIn(b"review-secret", body)
        self.assertEqual(request({"Origin": "http://tauri.localhost", "Access-Control-Request-Method": "POST",
                                  "Access-Control-Request-Headers": "Content-Type,X-OpenCore-Hub-Secret"}, "OPTIONS")[0], 204)
        self.assertEqual(request({"Origin": "http://tauri.localhost", "Access-Control-Request-Method": "POST",
                                  "Access-Control-Request-Headers": "X-Unsafe"}, "OPTIONS")[0], 403)

    @unittest.skipUnless(os.name == "nt", "Actual user-bound Windows DPAPI")
    def test_dpapi_roundtrip_is_managed_and_does_not_modify_external_hf_state(self):
        global_hf = self.root / "external-hf"
        global_hf.mkdir()
        external = global_hf / "token"
        external.write_text("external-token-sentinel")
        token = "hf_review_fixture"
        class Validator:
            def validate_token(self, value):
                if value != token:
                    raise ValueError("Unexpected test token")
        first, second = TokenStore(self.root / "instance-a"), TokenStore(self.root / "instance-b")
        with patch.dict(os.environ, {"HF_HOME": str(global_hf), "HF_TOKEN": "hf_external_must_not_be_used"}):
            self.assertIsNone(first.get())
            self.assertEqual(first.save(token, Validator()), {"configured": True})
            self.assertEqual(first.get(), token)
            self.assertNotIn(token.encode(), first.path.read_bytes())
            self.assertIsNone(second.get())
            self.assertEqual(first.clear(), {"configured": False})
        self.assertEqual(external.read_text(), "external-token-sentinel")

    def test_corrupt_optional_token_does_not_disable_public_bridge_health(self):
        state = self.state()
        state.tokens.path.parent.mkdir(parents=True)
        state.tokens.path.write_bytes(b"not-a-dpapi-blob")
        self.assertTrue(state.status()["healthy"])

    def test_pause_preparation_signals_runtime_stop_without_waiting_for_load(self):
        class SlowRuntime(RuntimeFixture):
            def __init__(self):
                super().__init__()
                self.entered = threading.Event()
                self.release = threading.Event()
            def prepare(self, entry):
                self.entered.set()
                self.release.wait(2)
                return {"runtimeReady": True}
        adapter = SlowRuntime()
        state = self.state(adapter)
        self.addCleanup(adapter.release.set)
        entry = self.install_fixture()
        state.entries[entry["id"]] = entry
        state.start(entry["id"], "prepare")
        self.assertTrue(adapter.entered.wait(1))
        state.pause()
        self.assertTrue(adapter.stopped.wait(.15), "Pause must stop the loading runtime promptly")

    def test_catalog_subdirectory_cannot_escape_pinned_model_directory(self):
        entry = self.install_fixture()
        outside = self.root / "outside-checkpoint"
        outside.mkdir()
        (outside / "config.json").write_text('{"model_type":"llama"}')
        entry.update(format="safetensors", quantization="BF16", subdirectory=str(outside))
        with self.assertRaises(ValueError):
            transfer.validate_entry(entry)
            runtime.runtime_plan(entry, self.root, self.root / "resources")


class LifecycleTests(unittest.TestCase):
    def test_ensure_service_refuses_unowned_start_without_creating_files(self):
        with tempfile.TemporaryDirectory(prefix="hf-service-review-") as directory:
            root = Path(directory)
            with patch.object(bridge, "find_owner", return_value=None), patch.object(bridge.subprocess, "Popen") as spawn:
                self.assertFalse(bridge.ensure_service(root, root / "resources")["available"])
                spawn.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Actual Windows process API")
    def test_process_identity_is_real_and_dead_process_is_not_reused(self):
        current = bridge.process_info(os.getpid())
        self.assertEqual(current["pid"], os.getpid())
        self.assertEqual(Path(current["path"]).resolve(), Path(sys.executable).resolve())
        self.assertTrue(bridge.owner_alive(current))
        self.assertFalse(bridge.owner_alive({**current, "created": current["created"] + "0"}))
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", "import time;time.sleep(10)"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            owned = bridge.process_info(child.pid)
            self.assertEqual(owned["parentPid"], os.getpid())
            self.assertTrue(bridge.owner_alive(owned))
        finally:
            child.terminate()
            child.wait(timeout=3)
        self.assertFalse(bridge.owner_alive(owned))

    def test_isolated_bridge_entry_imports_only_its_adjacent_modules(self):
        script = Path(bridge.__file__).resolve()
        result = subprocess.run([sys.executable, "-I", "-B", str(script), "--help"], capture_output=True,
                                text=True, timeout=10, cwd=tempfile.gettempdir())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--serve", result.stdout)


class RuntimeIntegrationRegressions(unittest.TestCase):
    def test_completed_dependency_helper_does_not_reload_a_ready_model_on_chat(self):
        with tempfile.TemporaryDirectory(prefix="hf-helper-review-") as directory:
            root = Path(directory)
            resources = root / "resources"
            helper = resources / "runtime-setup/setup_manager.py"
            helper.parent.mkdir(parents=True)
            helper.write_text("# Fixture helper; never executed")
            manager = runtime.RuntimeManager(root, resources)
            class Child:
                def __init__(self, status):
                    self.returncode = status
                def poll(self):
                    return self.returncode
            completed = Child(0)
            def spawn(*_):
                manager._children.append(completed)
                return completed
            with patch.object(manager, "_spawn", side_effect=spawn):
                manager._ensure_transformers({"id": MODEL_ID}, root / "runtime.log")
            self.assertNotIn(completed, manager._children)
            manager._children = [Child(None), Child(None)]
            manager._active = {"id": MODEL_ID, "url": "http://127.0.0.1:1", "key": "fixture-key"}
            response = io.BytesIO(b'{"choices":[{"message":{"content":"hello"}}]}')
            response.headers = {"Content-Type": "application/json"}
            with patch.object(manager, "prepare", side_effect=AssertionError("Already ready runtime was prepared again")), \
                 patch.object(manager, "_request", return_value=response):
                result = manager.chat({"id": MODEL_ID}, {"messages": [{"role": "user", "content": "hello"}]})
                self.assertIsNotNone(result)


@unittest.skipUnless(os.environ.get("OPENCORE_REVIEW_LIVE_METADATA") == "1", "Set OPENCORE_REVIEW_LIVE_METADATA=1 for anonymous metadata only")
class OfficialMetadataTests(unittest.TestCase):
    def test_public_official_metadata_never_requests_weights_or_credentials(self):
        requested = []
        def no_token():
            raise AssertionError("Public metadata requested credentials")
        client = transfer.HubClient(no_token)
        original = client._request
        def audited(path, **kwargs):
            self.assertTrue(path.startswith("/api/models/"), path)
            self.assertIsNone(kwargs.get("token"))
            requested.append(path)
            return original(path, **kwargs)
        client._request = audited
        for repository in ("hf-internal-testing/tiny-random-LlamaForCausalLM", "rockmatrix-ai/EdgeRock"):
            with self.subTest(repository=repository):
                plan = catalog.build_plan(client.metadata(repository))
                self.assertEqual(plan["repository"], repository)
                self.assertRegex(plan["revision"], r"^[0-9a-f]{40}$")
                self.assertTrue(plan["variants"])
                for entry in plan["variants"]:
                    if entry["installable"]:
                        transfer.validate_entry(entry)
        self.assertEqual(len(requested), 2)


if __name__ == "__main__":
    unittest.main()
