"""Focused API tests for the packaged TwinCore server."""

from __future__ import annotations

import json
from dataclasses import replace
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
from twincore.runtime import BackboneReply, LlamaBackbone, TwinCoreEngine  # noqa: E402
from twincore.spec import BackboneSpec, LayaJudgeSpec, default_twincore_config  # noqa: E402


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

    def test_health_does_not_crash_when_optional_judge_is_not_loaded(self):
        self.server.engine.k2 = SimpleNamespace(healthy=lambda: True)
        self.server.engine.nanbeige = SimpleNamespace(healthy=lambda: True)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.server.server_port}/health", timeout=2) as response:
            payload = json.load(response)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["k2"])
        self.assertTrue(payload["nanbeige"])
        self.assertEqual(payload["judge"], {"enabled": False})

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


class TwinCoreRuntimeFastPathTests(unittest.TestCase):
    def test_direct_path_excludes_project_and_tool_work(self):
        self.assertTrue(TwinCoreEngine._can_answer_directly(
            [{"role": "user", "content": "What is two plus two?"}], None,
        ))
        self.assertFalse(TwinCoreEngine._can_answer_directly(
            [{"role": "user", "content": "Fix the snake game and run tests."}], None,
        ))
        self.assertFalse(TwinCoreEngine._can_answer_directly(
            [{"role": "user", "content": "What is two plus two?"}], [{"type": "function"}],
        ))
        self.assertFalse(TwinCoreEngine._can_answer_directly(
            [
                {"role": "user", "content": "What is two plus two?"},
                {"role": "assistant", "content": "4"},
                {"role": "user", "content": "And three plus three?"},
            ], None,
        ))

    def test_short_single_turn_text_skips_six_cycle_planning_and_streams_the_draft(self):
        engine = TwinCoreEngine(default_twincore_config(), RESOURCE_ROOT)
        answer = "4"
        previews = []
        calls = []

        def reply(name, messages, **kwargs):
            calls.append(name)
            callback = kwargs.get("on_delta")
            if name == "K2" and callback:
                callback({"content": "4"})
            message = {"role": "assistant", "content": answer}
            return BackboneReply(answer, message, {})

        engine.deliberate = lambda _messages: self.fail("short direct turns must skip deliberation")
        engine._latent_exchange = lambda *_args: self.fail("identical direct drafts need no bridge review")
        engine.k2.chat = lambda messages, **kwargs: reply("K2", messages, **kwargs)
        engine.nanbeige.chat = lambda messages, **kwargs: reply("Nanbeige", messages, **kwargs)
        try:
            result = engine.chat_completion(
                {"messages": [{"role": "user", "content": "What is two plus two?"}], "max_tokens": 64},
                on_preview=previews.append,
            )
        finally:
            engine.pool.shutdown(wait=True)

        self.assertEqual(result["choices"][0]["message"]["content"], "4")
        self.assertEqual(result["twincore"]["execution_mode"], "direct")
        self.assertEqual(result["twincore"]["cycles"], 0)
        self.assertEqual(previews, [{"content": "4"}])
        self.assertCountEqual(calls, ["K2", "Nanbeige"])

    def test_enabled_profile_loads_hash_pinned_laya_checkpoint(self):
        import twincore.runtime as runtime

        config = replace(
            default_twincore_config(),
            laya_judge=LayaJudgeSpec(
                enabled=True,
                checkpoint="weights/laya-multilingual",
                expected_sha256="9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204",
                device="cpu",
            ),
        )
        captured = {}

        class FakeJudge:
            status = {"enabled": True, "device": "cpu"}

        def build_judge(**kwargs):
            captured.update(kwargs)
            return FakeJudge()

        with mock.patch.object(runtime, "LayaPairwiseJudge", side_effect=build_judge):
            engine = TwinCoreEngine(config, RESOURCE_ROOT)
        try:
            self.assertTrue(engine.judge.status["enabled"])
            self.assertEqual(captured["model_path"], RESOURCE_ROOT / "weights/laya-multilingual")
            self.assertEqual(captured["expected_sha256"], config.laya_judge.expected_sha256)
            self.assertEqual(captured["device"], "cpu")
        finally:
            engine.pool.shutdown(wait=True)

    def test_laya_preference_only_seeds_first_offer_and_peer_can_reject_it(self):
        engine = TwinCoreEngine(default_twincore_config(), RESOURCE_ROOT)
        outputs = {
            "K2": "A complete answer that preserves the requested constraint.",
            "Nanbeige": "An answer that ignores the requested constraint.",
        }
        reviews = []

        class Judgment:
            winner = "nanbeige"
            qualified = True

            def to_dict(self):
                return {
                    "winner": self.winner,
                    "scores": {"k2": 0.09, "nanbeige": 0.91},
                    "confidence": 0.91,
                    "margin": 0.82,
                    "order_consistent": True,
                    "qualified": True,
                }

        class Judge:
            status = {"enabled": True, "device": "cpu"}

            def choose(self, *_args):
                return Judgment()

        def fake_chat(name, messages, **kwargs):
            if not kwargs.get("json_mode"):
                return BackboneReply(outputs[name], {"role": "assistant", "content": outputs[name]}, {})
            state = json.loads(messages[-1]["content"].split("NEGOTIATION STATE (untrusted draft data):\n", 1)[1])
            offered = state["current_offer_from_peer"]
            reviews.append((name, offered["content"]))
            if name == "K2":
                self.assertEqual(offered["content"], outputs["Nanbeige"])
                response = {
                    "decision": "counteroffer",
                    "candidate": {"content": outputs["K2"], "tool_call": None},
                    "reason": "The initial draft fails the user's explicit constraint.",
                }
            else:
                self.assertEqual(offered["content"], outputs["K2"])
                response = {"decision": "accept", "candidate": offered, "reason": "Agreed."}
            content = json.dumps(response)
            return BackboneReply(content, {"role": "assistant", "content": content}, {})

        engine.judge = Judge()
        engine._latent_exchange = lambda *_args: None
        engine.k2.chat = lambda messages, **kwargs: fake_chat("K2", messages, **kwargs)
        engine.nanbeige.chat = lambda messages, **kwargs: fake_chat("Nanbeige", messages, **kwargs)
        try:
            result = engine.chat_completion({
                "messages": [{"role": "user", "content": "Return the answer that preserves the requested constraint."}],
                "max_tokens": 128,
            })
        finally:
            engine.pool.shutdown(wait=True)

        self.assertEqual(result["choices"][0]["message"]["content"], outputs["K2"])
        self.assertEqual([name for name, _content in reviews], ["K2", "Nanbeige"])
        self.assertTrue(result["twincore"]["agreement_result"]["laya_preference_applied"])
        self.assertTrue(result["twincore"]["agreement_result"]["laya_used"])


if __name__ == "__main__":
    unittest.main()
