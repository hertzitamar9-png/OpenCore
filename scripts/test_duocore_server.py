"""Focused API tests for the packaged DuoCore server."""

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

import serve_duocore as duocore_server  # noqa: E402
from duocore.runtime import BackboneReply, LlamaBackbone, DuoCoreEngine  # noqa: E402
from duocore import gpu_budget  # noqa: E402
from duocore.selection import parse_review  # noqa: E402
from duocore.gpu_budget import required_host_ram_mib  # noqa: E402
from duocore.spec import BackboneSpec, default_duocore_config  # noqa: E402


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
            "id": "duocore-test",
            "object": "chat.completion",
            "created": 1,
            "model": "DuoCore",
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


class DuoCoreServerApiTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), duocore_server.DuoCoreHandler)
        self.server.config = SimpleNamespace(model_id="DuoCore", live_window_tokens=262144)
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
        self.assertEqual(payload["model"], "DuoCore")
        self.assertEqual(payload["default_generation_settings"]["n_ctx"], 262144)

    def test_health_reports_pairwise_selection_without_a_third_model(self):
        self.server.engine.k2 = SimpleNamespace(healthy=lambda: True)
        self.server.engine.nanbeige = SimpleNamespace(healthy=lambda: True)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.server.server_port}/health", timeout=2) as response:
            payload = json.load(response)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["ready"])
        self.assertTrue(payload["k2"])
        self.assertTrue(payload["nanbeige"])
        self.assertEqual(payload["selection"]["strategy"], "pairwise_candidate_selection")
        self.assertFalse(payload["selection"]["third_party_judge"])
        self.assertFalse(payload["selection"]["latent_bridge"])

    def test_health_fails_readiness_when_either_backbone_is_unavailable(self):
        self.server.engine.k2 = SimpleNamespace(healthy=lambda: True)
        self.server.engine.nanbeige = SimpleNamespace(healthy=lambda: False)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"http://127.0.0.1:{self.server.server_port}/health", timeout=2)
        self.assertEqual(caught.exception.code, 503)
        payload = json.load(caught.exception)
        self.assertEqual(payload["status"], "unavailable")
        self.assertFalse(payload["ready"])
        self.assertTrue(payload["k2"])
        self.assertFalse(payload["nanbeige"])

    def test_streaming_route_flushes_provisional_tokens_and_final_completion(self):
        body = json.dumps({"model": "DuoCore", "messages": [], "stream": True}).encode()
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
            b'data: {"usage":{"prompt_tokens":15,"completion_tokens":2,"total_tokens":17},"choices":[]}\n',
            b'data: [DONE]\n',
        ]
        deltas = []
        with mock.patch("duocore.runtime.urllib.request.urlopen", return_value=FakeStreamResponse(events)) as open_url:
            reply = LlamaBackbone(spec, Path("model.gguf")).chat(
                [{"role": "user", "content": "test"}], on_delta=deltas.append,
            )
        sent = json.loads(open_url.call_args.args[0].data.decode())
        self.assertTrue(sent["stream"])
        self.assertEqual(sent["stream_options"], {"include_usage": True})
        self.assertEqual(reply.message["content"], "Live tokens")
        self.assertEqual("".join(delta.get("content", "") for delta in deltas), "Live tokens")
        self.assertEqual(reply.raw["choices"][0]["finish_reason"], "stop")
        self.assertEqual(reply.raw["usage"]["total_tokens"], 17)


class DuoCoreRuntimeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.engine = DuoCoreEngine(default_duocore_config(), RESOURCE_ROOT)

    def tearDown(self):
        self.engine.pool.shutdown(wait=True)

    @staticmethod
    def _reply(content, *, tool_call=None, usage=None):
        message = {"role": "assistant", "content": content}
        if tool_call is not None:
            message["tool_calls"] = [{
                "id": "draft-call",
                "type": "function",
                "function": {
                    "name": tool_call["name"],
                    "arguments": json.dumps(tool_call["arguments"]),
                },
            }]
        return BackboneReply(content, message, {"usage": usage or {}})

    def test_both_models_generate_one_candidate_and_mutually_score_the_winner(self):
        candidates = {
            "K2": "Replace the whole game with a simpler version.",
            "Nanbeige": "Keep the original game behavior and fix only the reported bug.",
        }
        calls = []
        reviews = []
        previews = []

        def fake_chat(name, messages, **kwargs):
            calls.append((name, bool(kwargs.get("json_mode"))))
            if kwargs.get("json_mode"):
                data = json.loads(messages[-1]["content"].split("\n", 1)[1])
                reviews.append((name, data))
                score = lambda candidate: 95 if "Keep the original game" in candidate["content"] else 20
                response = json.dumps({
                    "score_a": score(data["candidate_a"]),
                    "score_b": score(data["candidate_b"]),
                    "confidence": 0.83,
                    "reason": "The preferred draft follows the preservation requirement.",
                })
                return self._reply(response, usage={"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18})
            callback = kwargs.get("on_delta")
            if name == "K2" and callback:
                callback({"content": "K2 provisional"})
            return self._reply(candidates[name], usage={"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18})

        self.engine.k2.chat = lambda messages, **kwargs: fake_chat("K2", messages, **kwargs)
        self.engine.nanbeige.chat = lambda messages, **kwargs: fake_chat("Nanbeige", messages, **kwargs)
        result = self.engine.chat_completion({
            "messages": [{"role": "user", "content": "Keep the original game and fix its bug."}],
            "max_tokens": 128,
        }, on_preview=previews.append)

        self.assertEqual(result["choices"][0]["message"]["content"], candidates["Nanbeige"])
        self.assertEqual(result["duocore"]["execution_mode"], "pairwise_candidate_selection")
        self.assertEqual(result["duocore"]["selection_method"], "mean_of_independent_blind_pairwise_scores")
        self.assertEqual(len(reviews), 2)
        self.assertEqual(result["usage"], {"prompt_tokens": 40, "completion_tokens": 32, "total_tokens": 72})
        self.assertCountEqual(calls, [
            ("K2", False), ("Nanbeige", False), ("K2", True), ("Nanbeige", True),
        ])
        self.assertEqual(previews, [{"content": "K2 provisional"}])

    def test_review_confidence_percent_is_normalized(self):
        review = parse_review('{"score_a":90,"score_b":25,"confidence":95,"reason":"A is more complete."}')
        self.assertIsNotNone(review)
        self.assertEqual(review.confidence, 0.95)

    def test_failed_joint_review_does_not_silently_choose_k2(self):
        def fake_chat(name, messages, **kwargs):
            if kwargs.get("json_mode"):
                return self._reply("not valid JSON")
            return self._reply(f"Distinct answer from {name}.")

        self.engine.k2.chat = lambda messages, **kwargs: fake_chat("K2", messages, **kwargs)
        self.engine.nanbeige.chat = lambda messages, **kwargs: fake_chat("Nanbeige", messages, **kwargs)
        result = self.engine.chat_completion({
            "messages": [{"role": "user", "content": "Give a concise answer."}],
        })

        self.assertEqual(result["duocore"]["status"], "failed")
        self.assertIsNone(result["duocore"]["selected_model"])
        self.assertEqual(result["duocore"]["selection_method"], "joint_review_failed_no_selection")
        self.assertNotIn("Distinct answer from K2", result["choices"][0]["message"]["content"])

    def test_host_ram_budget_grows_with_context_and_keeps_desktop_reserve(self):
        config = default_duocore_config()
        one_gib = 1024**3
        budget_64k = required_host_ram_mib(config, 3 * one_gib, 2 * one_gib)
        budget_128k = required_host_ram_mib(replace(config, live_window_tokens=131072), 3 * one_gib, 2 * one_gib)

        self.assertGreaterEqual(budget_64k, 5 * 1024)
        self.assertGreater(budget_128k, budget_64k)

    def test_host_ram_budget_counts_weights_moved_to_gpu(self):
        config = default_duocore_config()
        one_gib = 1024**3
        cpu_only = required_host_ram_mib(config, 6 * one_gib, 4 * one_gib)
        half_on_gpu = required_host_ram_mib(config, 6 * one_gib, 4 * one_gib, 18, 11)

        self.assertGreater(cpu_only, half_on_gpu)
        self.assertGreaterEqual(half_on_gpu, 5 * 1024)

    def test_context_auto_fits_available_ram_at_the_largest_safe_1k_boundary(self):
        config = default_duocore_config()
        k2_bytes = 4_161_403_264
        nanbeige_bytes = 3_595_603_104
        available_mib = 16_734
        fit = getattr(gpu_budget, "largest_context_that_fits", None)
        self.assertTrue(callable(fit), "DuoCore needs an adaptive context preflight")

        context = fit(config, k2_bytes, nanbeige_bytes, 0, 0, available_mib)

        self.assertLess(context, config.live_window_tokens)
        self.assertGreaterEqual(context, 8_192)
        self.assertEqual(context % 1_024, 0)
        self.assertLessEqual(required_host_ram_mib(config, k2_bytes, nanbeige_bytes, 0, 0, context), available_mib)
        self.assertGreater(required_host_ram_mib(config, k2_bytes, nanbeige_bytes, 0, 0, context + 1_024), available_mib)

    def test_context_stays_configured_when_full_window_fits(self):
        config = default_duocore_config()
        fit = getattr(gpu_budget, "largest_context_that_fits", None)
        self.assertTrue(callable(fit), "DuoCore needs an adaptive context preflight")
        self.assertEqual(fit(config, 3_000_000_000, 2_000_000_000, 0, 0, 32_000), config.live_window_tokens)

    def test_context_preflight_refuses_to_fall_below_minimum_safe_window(self):
        config = default_duocore_config()
        fit = getattr(gpu_budget, "largest_context_that_fits", None)
        self.assertTrue(callable(fit), "DuoCore needs an adaptive context preflight")
        with self.assertRaisesRegex(RuntimeError, "8,192"):
            fit(config, 4_161_403_264, 3_595_603_104, 0, 0, 10_000)

    def test_identical_candidates_skip_review_work(self):
        calls = []
        answer = "Both backbones independently agree on this complete answer."

        def fake_chat(name, messages, **kwargs):
            calls.append((name, bool(kwargs.get("json_mode"))))
            return self._reply(answer)

        self.engine.k2.chat = lambda messages, **kwargs: fake_chat("K2", messages, **kwargs)
        self.engine.nanbeige.chat = lambda messages, **kwargs: fake_chat("Nanbeige", messages, **kwargs)
        result = self.engine.chat_completion({
            "messages": [{"role": "user", "content": "Explain this briefly."}],
        })

        self.assertEqual(result["choices"][0]["message"]["content"], answer)
        self.assertEqual(result["duocore"]["selection_method"], "both_models_returned_the_same_valid_candidate")
        self.assertEqual(calls, [("K2", False), ("Nanbeige", False)])

    def test_per_pass_output_uses_available_model_context_without_a_12k_cap(self):
        requested = 40000
        received = []
        answer = "The requested output allowance reached both model backends."

        def fake_chat(name, messages, **kwargs):
            received.append((name, kwargs["max_tokens"]))
            return self._reply(answer)

        self.engine.k2.chat = lambda messages, **kwargs: fake_chat("K2", messages, **kwargs)
        self.engine.nanbeige.chat = lambda messages, **kwargs: fake_chat("Nanbeige", messages, **kwargs)
        self.engine.chat_completion({
            "messages": [{"role": "user", "content": "Answer fully."}],
            "max_tokens": requested,
        })

        self.assertCountEqual(received, [("K2", requested), ("Nanbeige", requested)])

    def test_only_candidate_with_an_available_tool_name_can_be_selected(self):
        calls = []
        tools = [{
            "type": "function",
            "function": {"name": "read_file", "parameters": {"type": "object"}},
        }]

        def fake_chat(name, messages, **kwargs):
            calls.append((name, bool(kwargs.get("json_mode"))))
            if name == "K2":
                return self._reply("", tool_call={"name": "delete_everything", "arguments": {}})
            return self._reply("", tool_call={"name": "read_file", "arguments": {"path": "main.py"}})

        self.engine.k2.chat = lambda messages, **kwargs: fake_chat("K2", messages, **kwargs)
        self.engine.nanbeige.chat = lambda messages, **kwargs: fake_chat("Nanbeige", messages, **kwargs)
        result = self.engine.chat_completion({
            "messages": [{"role": "user", "content": "Read main.py."}],
            "tools": tools,
        })

        message = result["choices"][0]["message"]
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(result["duocore"]["selection_method"], "only_valid_candidate")
        self.assertEqual(calls, [("K2", False), ("Nanbeige", False)])

    def test_unreviewed_tool_candidates_are_never_returned_for_execution(self):
        tools = [{
            "type": "function",
            "function": {"name": "read_file", "parameters": {"type": "object"}},
        }]

        def fake_chat(name, messages, **kwargs):
            if kwargs.get("json_mode"):
                raise RuntimeError("review unavailable")
            return self._reply("", tool_call={"name": "read_file", "arguments": {"path": f"{name}.py"}})

        self.engine.k2.chat = lambda messages, **kwargs: fake_chat("K2", messages, **kwargs)
        self.engine.nanbeige.chat = lambda messages, **kwargs: fake_chat("Nanbeige", messages, **kwargs)
        result = self.engine.chat_completion({
            "messages": [{"role": "user", "content": "Read a source file."}],
            "tools": tools,
        })

        message = result["choices"][0]["message"]
        self.assertNotIn("tool_calls", message)
        self.assertEqual(result["duocore"]["status"], "failed")
        self.assertEqual(result["duocore"]["selection_method"], "tool_candidates_unreviewed_no_action")
        self.assertIn("joint review failed", message["content"])


if __name__ == "__main__":
    unittest.main()
