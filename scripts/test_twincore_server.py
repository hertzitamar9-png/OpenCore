"""Focused API tests for the packaged TwinCore server."""

from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock


RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "src-tauri" / "resources" / "doucode"
sys.path.insert(0, str(RESOURCE_ROOT))

import serve_twincore_consensus as twincore_server  # noqa: E402
from twincore.runtime import LlamaBackbone  # noqa: E402
from twincore.spec import BackboneSpec  # noqa: E402


class StreamingEngine:
    def __init__(self):
        self.preview_sent = threading.Event()
        self.release_final = threading.Event()

    def chat_completion(self, payload, on_preview=None):
        if on_preview:
            on_preview({"content": "live token"})
            self.preview_sent.set()
            if not self.release_final.wait(timeout=2):
                raise TimeoutError("test client did not release the final result")
        return {
            "id": "twincore-test",
            "object": "chat.completion",
            "created": 1,
            "model": "doUcode",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "live token"}, "finish_reason": "stop"}],
        }


class FakeStreamResponse:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self.lines)


class TwinCoreServerApiTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), twincore_server.TwinCoreHandler)
        self.server.config = SimpleNamespace(model_id="doUcode", live_window_tokens=262144)
        self.server.engine = StreamingEngine()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_props_reports_the_composite_live_context(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.server.server_port}/props", timeout=2) as response:
            payload = json.load(response)
        self.assertEqual(payload["n_ctx"], 262144)
        self.assertEqual(payload["model"], "doUcode")
        self.assertEqual(payload["default_generation_settings"]["n_ctx"], 262144)

    def test_streaming_route_flushes_provisional_tokens_and_final_completion(self):
        body = json.dumps({"model": "doUcode", "messages": [], "stream": True}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            content_type = response.headers.get("Content-Type", "")
            try:
                first_line = response.readline().decode()
                self.assertTrue(self.server.engine.preview_sent.wait(timeout=1))
                self.assertIn('"echo_preview"', first_line)
            finally:
                self.server.engine.release_final.set()
            stream = first_line + response.read().decode()
        self.assertIn("text/event-stream", content_type)
        self.assertIn('"echo_preview"', stream)
        self.assertIn('"finish_reason": "stop"', stream)
        self.assertIn("data: [DONE]", stream)

    def test_backbone_sse_deltas_are_returned_incrementally_and_reassembled(self):
        spec = BackboneSpec(
            name="K2", repo="model", gguf_repo="model-gguf", gguf_file="model.gguf",
            hidden_size=1, num_hidden_layers=1, num_attention_heads=1,
            num_key_value_heads=1, head_dim=1, vocab_size=1, native_context=32768, port=8831,
        )
        events = [
            b'data: {"id":"part","model":"K2","choices":[{"index":0,"delta":{"content":"Live "},"finish_reason":null}]}\n',
            b'\n',
            b'data: {"choices":[{"index":0,"delta":{"content":"tokens"},"finish_reason":null}]}\n',
            b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n',
            b'data: [DONE]\n',
        ]
        deltas = []
        with mock.patch("twincore.runtime.urllib.request.urlopen", return_value=FakeStreamResponse(events)) as open_url:
            reply = LlamaBackbone(spec, Path("model.gguf")).chat(
                [{"role": "user", "content": "test"}], on_delta=deltas.append,
            )
        sent = json.loads(open_url.call_args.args[0].data.decode())
        self.assertTrue(sent["stream"])
        self.assertEqual(reply.message["content"], "Live tokens")
        self.assertEqual("".join(delta.get("content", "") for delta in deltas), "Live tokens")
        self.assertEqual(reply.raw["choices"][0]["finish_reason"], "stop")


if __name__ == "__main__":
    unittest.main()
