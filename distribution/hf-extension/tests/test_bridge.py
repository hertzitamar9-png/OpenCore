import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import hf_bridge as bridge
except ModuleNotFoundError:
    bridge = None
from test_download import DATA, DownloadTests, Fixture
from http.server import ThreadingHTTPServer


class FakeRuntime:
    def __init__(self):
        self.stopped = False
        self.context = None
    def models(self, catalog):
        return {"object": "list", "data": [{"id": x["id"], "object": "model"} for x in catalog if x["status"] == "installed"]}
    def prepare(self, entry):
        return {"runtimeReady": True, "inferenceVerified": True, "receiptRevision": entry["revision"]}
    def chat(self, entry, payload):
        return b'{"choices":[{"message":{"content":"test response"}}]}', "application/json"
    def stop(self):
        self.stopped = True
        return {"stopped": True}
    def set_context(self, model, conversation, request):
        self.context = (model, conversation, request)
        return {"registered": True}
    def release_context(self, request):
        self.context = None
        return {"released": True}


class BridgeTests(unittest.TestCase):
    def test_automatic_runtime_preparation_follows_verified_download(self):
        self.runtime.automatic_preparation=True
        entry=self.seed()
        self.state.entries[entry['id']]['runtime']={'adapter':'gguf'}
        self.assertEqual(self.request('POST','/api/install',{'id':entry['id']})[0],202)
        self.assertEqual(self.wait_job()['status'],'ready')
        self.assertTrue(self.state.catalog()[0]['runtimeReady'])

    def setUp(self):
        self.assertIsNotNone(bridge, "The authenticated bridge is not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hub = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        self.hub.private = self.hub.corrupt = self.hub.ignore_range = self.hub.slow = False
        self.hub.requests = []
        threading.Thread(target=self.hub.serve_forever, daemon=True).start()
        self.addCleanup(self.hub.server_close)
        self.addCleanup(self.hub.shutdown)
        client = bridge.HubClient(lambda: None, endpoint="http://127.0.0.1:" + str(self.hub.server_port), allow_test_http=True)
        self.runtime = FakeRuntime()
        self.state = bridge.BridgeState(self.root, self.root, client=client, runtime=self.runtime)
        self.server = bridge.BridgeServer(("127.0.0.1", 0), self.state, "test-secret")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.state.close)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, value=None, secret="test-secret", origin=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        values = {"Content-Type": "application/json", **(headers or {})}
        if secret:
            values["X-OpenCore-Hub-Secret"] = secret
        if origin:
            values["Origin"] = origin
        body = json.dumps(value) if value is not None else None
        connection.request(method, path, body=body, headers=values)
        response = connection.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), json.loads(payload) if payload else None
        connection.close()
        return result

    def entry(self):
        return DownloadTests.entry(self)

    def seed(self):
        entry = self.entry()
        with self.state.lock:
            self.state.entries[entry["id"]] = entry
            self.state.persist_catalog()
        return entry

    def wait_job(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            job = self.state.status()["progress"]
            if job and job["status"] not in ("running", "pausing"):
                return job
            time.sleep(.01)
        self.fail("Bridge job did not complete")

    def test_secret_and_tauri_origin_are_both_enforced(self):
        self.assertEqual(self.request("GET", "/status", secret=None)[0], 401)
        self.assertEqual(self.request("GET", "/status", secret="wrong")[0], 401)
        self.assertEqual(self.request("GET", "/status", origin="https://evil.example")[0], 403)
        status, headers, result = self.request("GET", "/status", origin="http://tauri.localhost")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://tauri.localhost")
        self.assertNotIn("secret", result)
        self.assertNotIn("token", result)

    def test_preflight_allows_only_tauri_and_approved_headers(self):
        headers = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type,X-OpenCore-Hub-Secret"}
        self.assertEqual(self.request("OPTIONS", "/api/plan", secret=None, origin="http://tauri.localhost", headers=headers)[0], 204)
        self.assertEqual(self.request("OPTIONS", "/api/plan", secret=None, origin="https://evil.example", headers=headers)[0], 403)
        headers["Access-Control-Request-Headers"] = "X-Unapproved"
        self.assertEqual(self.request("OPTIONS", "/api/plan", secret=None, origin="http://tauri.localhost", headers=headers)[0], 403)

    def test_oversized_or_non_json_body_rejected_before_route(self):
        self.assertEqual(self.request("POST", "/api/plan", {"repository": "x" * (256 * 1024)})[0], 413)
        self.assertEqual(self.request("POST", "/api/plan", ["owner/model"])[0], 400)
        self.assertEqual(self.request("POST", "/api/plan", {"repository": "owner/model"}, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/plan", {}, headers={"Host": "evil.example"})[0], 403)

    def test_catalog_persists_and_arbitrary_unreviewed_file_pins_are_rejected(self):
        self.assertEqual(self.request("POST", "/api/catalog/add", {"planId": "made-up", "files": ["arbitrary"]})[0], 400)
        entry = self.seed()
        second = bridge.BridgeState(self.root, self.root, client=self.state.client, runtime=FakeRuntime())
        self.addCleanup(second.close)
        self.assertEqual(second.catalog()[0]["id"], entry["id"])
        status, _, result = self.request("POST", "/api/catalog/profile", {"id": entry["id"], "profileId": "api-profile", "providerId": "api-provider"})
        self.assertEqual(status, 200)
        self.assertEqual(self.state.catalog()[0]["profileId"], "api-profile")

    def test_duplicate_download_is_rejected_and_status_has_shared_progress(self):
        self.hub.slow = True
        entry = self.seed()
        first = self.request("POST", "/api/install", {"id": entry["id"]})
        self.assertEqual(first[0], 202)
        self.assertEqual(self.request("POST", "/api/install", {"id": entry["id"]})[0], 409)
        self.assertEqual(self.request("POST", "/api/prepare", {"id": entry["id"]})[0], 409)
        progress = self.request("GET", "/api/status")[2]["progress"]
        self.assertEqual(progress["jobId"], first[2]["jobId"])
        self.assertEqual(self.wait_job()["status"], "installed")
        self.assertEqual(self.state.catalog()[0]["status"], "installed")
        self.assertFalse(self.state.catalog()[0]["runtimeReady"])

    def test_prepare_chat_and_context_use_runtime_hooks_after_verified_install(self):
        entry = self.seed()
        self.request("POST", "/api/install", {"id": entry["id"]})
        self.wait_job()
        status, _, _ = self.request("POST", "/api/prepare", {"id": entry["id"]})
        self.assertEqual(status, 202)
        self.assertEqual(self.wait_job()["status"], "ready")
        self.assertTrue(self.state.catalog()[0]["runtimeReady"])
        status, _, result = self.request("GET", "/v1/models", secret=None, headers={"Authorization": "Bearer test-secret"})
        self.assertEqual(status, 200)
        self.assertEqual(result["data"][0]["id"], entry["id"])
        self.assertEqual(self.request("POST", "/api/context", {"modelId": entry["id"], "conversationId": "conversation-1", "requestId": "request-1"})[0], 200)
        self.assertEqual(self.runtime.context, (entry["id"], "conversation-1", "request-1"))
        status, _, result = self.request("POST", "/v1/chat/completions", {"model": entry["id"], "messages": [{"role": "user", "content": "test"}]})
        self.assertEqual(status, 200)
        self.assertEqual(result["choices"][0]["message"]["content"], "test response")
        self.request("POST", "/api/context/release", {"requestId": "request-1"})
        self.assertIsNone(self.runtime.context)

    def test_pause_job_and_shutdown_stop_runtime(self):
        self.hub.slow = True
        entry = self.seed()
        self.request("POST", "/api/install", {"id": entry["id"]})
        time.sleep(.025)
        self.assertEqual(self.request("POST", "/api/pause", {})[0], 200)
        self.assertEqual(self.wait_job()["status"], "paused")
        self.state.close()
        self.assertTrue(self.runtime.stopped)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_saved_token_is_dpapi_encrypted_and_never_returned(self):
        token = "hf_private_test"
        self.state.client.token_provider = lambda: None
        status, _, result = self.request("PUT", "/api/token", {"token": token})
        self.assertEqual((status, result), (200, {"configured": True}))
        self.assertEqual(self.state.tokens.get(), token)
        self.assertNotIn(token.encode(), self.state.tokens.path.read_bytes())
        self.assertEqual(self.request("GET", "/api/token")[2], {"configured": True})
        self.assertEqual(self.request("DELETE", "/api/token")[2], {"configured": False})


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(bridge, "The managed lifecycle is not implemented")

    def test_python_stub_chain_selects_only_exact_installed_opencore_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory) / "OpenCore.exe"
            processes = {10: {"pid": 10, "parentPid": 20, "path": "C:/Python/python.exe", "created": "1"},
                         20: {"pid": 20, "parentPid": 30, "path": "C:/Python/Scripts/python.exe", "created": "2"},
                         30: {"pid": 30, "parentPid": 1, "path": str(installed), "created": "3"}}
            owner = bridge.find_owner(directory, directory, start_pid=10, process_info=processes.get)
            self.assertEqual(owner["pid"], 30)
            processes[30]["path"] = str(Path(directory) / "other" / "OpenCore.exe")
            self.assertIsNone(bridge.find_owner(directory, directory, start_pid=10, process_info=processes.get))

    def test_owner_identity_rejects_pid_reuse(self):
        owner = {"pid": 42, "path": "C:/OpenCore/OpenCore.exe", "created": "abc"}
        self.assertTrue(bridge.owner_alive(owner, process_info=lambda _: dict(owner)))
        self.assertFalse(bridge.owner_alive(owner, process_info=lambda _: {**owner, "created": "new-process"}))


if __name__ == "__main__":
    unittest.main()
