"""Local cache ownership cleanup; no inference or production data writes."""
import json
import io
import sys
import threading
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import echo_server


class KvInvalidationTests(unittest.TestCase):
    def lease_state(self):
        state = echo_server.EchoState.__new__(echo_server.EchoState)
        state._backend_lock = threading.RLock()
        state._backend_auxiliary_lock = threading.Lock()
        state._backend_lease_guard = threading.Lock()
        state._backend_lease_token = None
        state._backend_lease_conversation = None
        state._backend_lease_timer = None
        state._backend_conversation = "chat"
        state._backend_needs_reset = False
        state._backend_metrics_lock = threading.Lock()
        state._backend_metrics_key = None
        state._backend_metrics_value = {}
        state._backend_metrics_time = 0
        return state

    def test_external_echo_generation_waits_until_auxiliary_lease_releases(self):
        state = self.lease_state()
        receipt = state.acquire_backend_lease("chat")
        self.assertTrue(receipt["acquired"])
        self.assertFalse(state.acquire_backend_lease("other")["acquired"])
        entered = threading.Event()
        handler = object.__new__(echo_server.Handler)
        handler.state = state
        handler._controlled_context_serial = lambda *args: entered.set()
        worker = threading.Thread(target=lambda: handler._controlled_context({}, "external"))
        worker.start()
        self.assertFalse(entered.wait(0.05))
        self.assertFalse(state.release_backend_lease("wrong-nonce", False)["released"])
        self.assertFalse(entered.is_set())
        self.assertTrue(state.release_backend_lease(receipt["nonce"], False)["released"])
        worker.join(timeout=1)
        self.assertTrue(entered.is_set())

    def test_expired_lease_invalidates_before_releasing_generation_gate(self):
        state = self.lease_state()
        with patch.object(echo_server.threading, "Timer") as timer:
            receipt = state.acquire_backend_lease("chat")
            seconds, expire = timer.call_args.args
            self.assertEqual(seconds, 180)
            expire()
        self.assertIsNone(state._backend_conversation)
        self.assertTrue(state._backend_needs_reset)
        self.assertFalse(state._backend_auxiliary_lock.locked())
        self.assertFalse(state.release_backend_lease(receipt["nonce"], False)["released"])

    def test_legacy_summary_and_passthrough_model_paths_share_the_same_lease_gate(self):
        for path, question, method in [("/v1/chat/completions", "hello", "_legacy_chat"), ("/v1/chat/completions", "/summarize", "_summarize"), ("/v1/chat/completions", "/trim", "_compact"), ("/v1/completions", "hello", "_passthrough")]:
            state = self.lease_state()
            state.autonomous_context = False
            state.reasoning = "off"
            state.log = lambda value: None
            lease = state.acquire_backend_lease("chat")
            handler = object.__new__(echo_server.Handler)
            handler.state = state
            handler.path = path
            raw = json.dumps({"messages": [{"role": "user", "content": question}]}).encode()
            handler.headers = {"Content-Length": str(len(raw))}
            handler.rfile = io.BytesIO(raw)
            handler._begin_generation_log = lambda *args: None
            entered = threading.Event()
            setattr(handler, method, lambda *args: entered.set())
            worker = threading.Thread(target=handler.do_POST)
            worker.start()
            self.assertFalse(entered.wait(0.05))
            state.release_backend_lease(lease["nonce"], False)
            worker.join(timeout=1)
            self.assertTrue(entered.is_set(), method)

    def test_lease_http_control_rejects_origin_remote_peer_and_oversized_body(self):
        for headers, peer in [({"Origin": "https://example.test"}, "127.0.0.1"), ({}, "192.0.2.1"), ({"Content-Length": "4097"}, "127.0.0.1"), ({"Content-Length": "-1"}, "127.0.0.1")]:
            handler = object.__new__(echo_server.Handler)
            handler.path = "/echo/kv/lease"
            handler.headers = headers
            handler.client_address = (peer, 1234)
            handler.rfile = io.BytesIO(b"{}")
            handler.state = self.lease_state()
            seen = []
            handler._send_json = lambda code, value: seen.append((code, value))
            handler.do_POST()
            self.assertEqual(seen[0][0], 403)
            self.assertFalse(handler.state._backend_auxiliary_lock.locked())

    def test_failed_auxiliary_restore_forces_full_transcript_reset_without_archiving(self):
        state = echo_server.EchoState.__new__(echo_server.EchoState)
        state._backend_lock = threading.RLock()
        state._backend_metrics_lock = threading.Lock()
        state._backend_conversation = "chat"
        state._backend_needs_reset = False
        state._backend_metrics_key = "old-session"
        state._backend_metrics_value = {"modelSessionTokens": 123}
        state._backend_metrics_time = 42.0
        archive = b"exact original user and assistant messages"
        state.archives = types.SimpleNamespace(raw=archive)
        receipt = state.invalidate_backend_session("chat")
        self.assertTrue(receipt["invalidated"])
        self.assertIsNone(state._backend_conversation)
        self.assertTrue(state._backend_needs_reset)
        self.assertIsNone(state._backend_metrics_key)
        self.assertEqual(state._backend_metrics_value, {})
        self.assertEqual(state.archives.raw, archive)

    def test_next_request_rehydrates_complete_live_history_instead_of_suffix(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = echo_server.ArchiveSet(Path(folder), 0)
            try:
                state = echo_server.EchoState(archives, "http://127.0.0.1:1", 0, 4, False)
                state._ctx_size = 32768
                state.count_tokens = lambda text: max(1, len(text) // 4)
                state.backend_session_metrics = lambda conversation: {"modelSessionTokens": 100}
                handler = object.__new__(echo_server.Handler)
                handler.state = state
                handler._send_json = lambda code, result: result
                requests = []
                def generate(body, phase):
                    requests.append(json.loads(json.dumps(body)))
                    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "The project uses Godot."}}]}
                handler._generate_live = generate
                handler._controlled_context({"messages": [{"role": "user", "content": "The project uses Godot."}], "max_tokens": 1024}, "chat")
                before = [(page.content_hash, page.text) for page in archives.get("chat").recent_pages("chat", 20)]
                state.invalidate_backend_session("chat")
                self.assertEqual([(page.content_hash, page.text) for page in archives.get("chat").recent_pages("chat", 20)], before)
                handler._controlled_context({"messages": [{"role": "user", "content": "Which engine did we choose?"}], "max_tokens": 1024}, "chat")
                self.assertFalse(requests[1]["echo_append"])
                self.assertTrue(requests[1]["echo_reset"])
                self.assertEqual([message["role"] for message in requests[1]["messages"]], ["system", "user", "assistant", "user"])
                self.assertIn("The project uses Godot.", json.dumps(requests[1]["messages"]))
                self.assertIn("Which engine did we choose?", json.dumps(requests[1]["messages"]))
            finally:
                archives.close()

    def test_invalidation_cannot_clear_another_active_conversation_owner(self):
        state = echo_server.EchoState.__new__(echo_server.EchoState)
        state._backend_lock = threading.RLock()
        state._backend_conversation = "other-chat"
        state._backend_needs_reset = False
        receipt = state.invalidate_backend_session("chat")
        self.assertFalse(receipt["invalidated"])
        self.assertEqual(state._backend_conversation, "other-chat")
        self.assertFalse(state._backend_needs_reset)


if __name__ == "__main__":
    unittest.main()
