"""Authenticated Hub memory routes; no model, production DB, or credential reads."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

import hf_bridge as bridge


class FakeRepair:
    instances = []

    def __init__(self, data, *, allowed_ports):
        self.data, self.allowed_ports, self.calls = data, allowed_ports, []
        self.instances.append(self)

    def recover(self, body):
        self.calls.append(("recover", body))
        return {"status": "ready", "proof": "fixture"}

    def revalidate(self, proof):
        self.calls.append(("revalidate", proof))
        return {"status": "ready"}

    def receipts(self, conversation):
        self.calls.append(("receipts", conversation))
        return {"conversationId": conversation, "receipts": {}}


class MemoryBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = bridge.BridgeState.__new__(bridge.BridgeState)
        self.state.root = Path(self.tmp.name)
        self.state.owner, self.state.closed = None, False
        self.state.memory_repair = None
        self.state._memory_lock = threading.RLock()
        FakeRepair.instances = []
        patched = patch.object(bridge, "MemoryRepair", FakeRepair)
        patched.start()
        self.addCleanup(patched.stop)
        self.server = bridge.BridgeServer(("127.0.0.1", 0), self.state, "test-secret")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body, secret="test-secret"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if secret:
            headers[bridge.AUTH_HEADER] = secret
        try:
            connection.request("POST", path, json.dumps(body), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def descriptor(self):
        return {"status": "running", "backendPort": 19001, "echoPort": 19003, "authorization": "Bearer caller-secret"}

    def test_authenticated_routes_keep_one_helper_and_never_forward_ui_authorization(self):
        status, _ = self.request("/api/memory/recover", {"conversationId": "chat", "userEntryId": 7,
            "temporaryChat": False, "runtime": self.descriptor()})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/memory/revalidate", {"proof": {"proof": "signed"}, "runtime": self.descriptor()})[1]["status"], "ready")
        self.assertEqual(self.request("/api/memory/receipts", {"conversationId": "chat"})[1], {"conversationId": "chat", "receipts": {}})
        self.assertEqual(len(FakeRepair.instances), 1)
        helper = FakeRepair.instances[0]
        self.assertEqual([call[0] for call in helper.calls], ["recover", "revalidate", "receipts"])
        self.assertEqual(helper.calls[1][1], {"proof": "signed"})
        self.assertNotIn("authorization", helper.calls[0][1]["runtime"])
        self.assertTrue({19001, 19003} <= set(helper.allowed_ports))

    def test_missing_secret_cannot_initialize_or_access_memory_helper(self):
        status, _ = self.request("/api/memory/receipts", {"conversationId": "chat"}, secret=None)
        self.assertEqual(status, 401)
        self.assertEqual(FakeRepair.instances, [])

    def test_invalid_ports_and_nonloopback_descriptors_never_reach_recovery(self):
        for extra in [{"backendPort": True}, {"echoPort": 0}, {"baseUrl": "http://example.com:19001"}, {"baseUrl": 42}]:
            with self.subTest(extra=extra):
                self.assertEqual(self.request("/api/memory/recover", {"runtime": {**self.descriptor(), **extra}})[0], 400)
        self.assertEqual(FakeRepair.instances[0].calls, [])

    def test_receipts_need_no_runtime_or_database_at_service_initialization(self):
        self.assertIsNone(self.state.memory_repair)
        self.assertEqual(self.request("/api/memory/receipts", {"conversationId": "chat"})[0], 200)
        self.assertEqual(FakeRepair.instances[0].calls, [("receipts", "chat")])


class Denied(Exception):
    pass


class Gone(Exception):
    pass


class Zombie(Exception):
    pass


class FakeProcess:
    def __init__(self, pid=900):
        self.pid = pid

    def username(self):
        return "same-user"

    def children(self, recursive=False):
        return []

    def parents(self):
        return [FakeProcess(444)]

    def net_connections(self, kind):
        return [types.SimpleNamespace(status="LISTEN", laddr=types.SimpleNamespace(
            port=19001 if self.pid == 555 else 19003, ip="127.0.0.1"))]

    def cmdline(self):
        return [r"C:\OpenCore\python.exe", r"C:\OpenCore\echo\echo_server.py"]

    def environ(self):
        return {"ECHO_UPSTREAM_API_KEY": "private-fixture-key"}


class MemoryOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = bridge.BridgeState.__new__(bridge.BridgeState)
        self.state.root = Path(self.tmp.name)
        self.state.owner = {"pid": 444}
        self.state.memory_repair = None
        self.state._memory_lock = threading.RLock()
        fake = types.SimpleNamespace(Process=FakeProcess, CONN_LISTEN="LISTEN", AccessDenied=Denied,
            NoSuchProcess=Gone, ZombieProcess=Zombie)
        patched = patch.dict(sys.modules, {"psutil": fake})
        patched.start()
        self.addCleanup(patched.stop)
        helper = patch.object(bridge, "MemoryRepair", FakeRepair)
        helper.start()
        self.addCleanup(helper.stop)
        self.runtime = {"status": "running", "backendPort": 19001, "echoPort": 19003,
            "modelPid": 555, "echoPid": 666, "authorization": "Bearer caller-supplied"}

    def test_owned_echo_secret_is_attached_internally_and_never_returned(self):
        result = self.state.memory_action("recover", {"runtime": self.runtime})
        forwarded = self.state.memory_repair.calls[-1][1]
        self.assertEqual(forwarded["runtime"]["authorization"], "Bearer private-fixture-key")
        self.assertNotIn("private-fixture-key", json.dumps(result))
        self.assertNotIn("caller-supplied", json.dumps(forwarded))

    def test_unowned_listener_is_rejected_before_recovery(self):
        with patch.object(FakeProcess, "parents", return_value=[FakeProcess(445)]):
            with self.assertRaisesRegex(ValueError, "not owned"):
                self.state.memory_action("recover", {"runtime": self.runtime})
        self.assertEqual(self.state.memory_repair.calls, [])


if __name__ == "__main__":
    unittest.main()
