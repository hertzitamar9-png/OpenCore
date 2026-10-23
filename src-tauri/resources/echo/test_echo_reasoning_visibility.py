"""Budget and retained generation contracts; no model inference is performed."""
import io
import base64
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import echo_server


class ReasoningVisibilityTests(unittest.TestCase):
    def test_raw_stream_records_keep_unknown_fields_and_exact_sse_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            handler = echo_server.Handler.__new__(echo_server.Handler)
            handler.state = types.SimpleNamespace(archives=types.SimpleNamespace(directory=Path(temporary)))
            handler._begin_generation_log("stream", "max", 12000)
            chunks = (b'event: custom-provider-event\r\n'
                      b'data: {"vendor_extension":{"exact":"kept"},"choices":[{"delta":{"reasoning_content":"first"}}]}\r\n\r\n'
                      b'data: {"reasoning_budget":{"effective_min_tokens":128,"generated_tokens":128,"minimum_met":true},"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\n'
                      b'data: [DONE]\n\n')
            handler._upstream = lambda *args, **kwargs: io.BytesIO(chunks)
            handler._live_event = lambda value: None
            result = handler._generate_live({"stream": True, "max_tokens": 16384, "reasoning_budget_tokens": 12000}, "working")
            self.assertEqual(result["reasoning_budget"], {"effective_min_tokens":128,"generated_tokens":128,"minimum_met":True})
            records = [json.loads(line) for line in Path(handler._generation_log_status()["generation_file"]).read_text(encoding="utf-8").splitlines()]
            saved_bytes = b''.join(base64.b64decode(record["raw_sse_base64"]) for record in records if "raw_sse_base64" in record)
            self.assertEqual(saved_bytes, chunks)
            self.assertIn(b'"vendor_extension":{"exact":"kept"}', saved_bytes)
            self.assertEqual(result["choices"][0]["message"]["reasoning_content"], "first")
            self.assertEqual(handler._generation_log_status()["generation_count"], 1)

    def test_a_log_directory_error_is_reported_without_preventing_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "raw-generations").write_text("A file already occupies this directory name", encoding="utf-8")
            handler = echo_server.Handler.__new__(echo_server.Handler)
            handler.state = types.SimpleNamespace(archives=types.SimpleNamespace(directory=root), log=lambda value: None)
            handler._begin_generation_log("test", "max", 12000)
            response = {"choices": [{"message": {"content": "Still completed"}, "finish_reason": "stop"}]}
            handler._upstream = lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode())
            self.assertEqual(handler._generate_live({"max_tokens": 16384, "reasoning_budget_tokens": 12000}, "working"), response)
            self.assertTrue(handler._generation_log_status()["generation_log_error"])

    def test_review_request_cannot_spend_all_of_its_reply_allowance_on_reasoning(self):
        with tempfile.TemporaryDirectory() as temporary:
            handler = echo_server.Handler.__new__(echo_server.Handler)
            handler.state = types.SimpleNamespace(archives=types.SimpleNamespace(directory=Path(temporary)), log=lambda value: None,
                                                  archive_turn=lambda *args: None)
            handler._begin_generation_log("review", "ultra", 12000)
            requests = []
            answers = iter(["Draft answer", "NO ISSUES"])
            def upstream(path, payload, stream):
                requests.append(dict(payload))
                return io.BytesIO(json.dumps({"choices": [{"message": {"content": next(answers)}, "finish_reason": "stop"}]}).encode())
            handler._upstream = upstream
            handler.send_response = handler.send_header = handler.end_headers = lambda *args: None
            handler.wfile = io.BytesIO()
            handler._ultra({"messages": [{"role": "user", "content": "Check this"}], "max_completion_tokens": 16384}, "Check this", "review", {"budget": 12000})
            self.assertEqual(requests[0]["reasoning_budget_tokens"], 12000)
            self.assertEqual(requests[1]["max_tokens"], 1500)
            self.assertNotIn("max_completion_tokens", requests[1])
            self.assertEqual(requests[1]["reasoning_budget_tokens"], 1125)

    def test_opencore_finishes_naturally_without_a_thinking_cutoff(self):
        payload = {"reasoning_effort": "opencore"}
        level, settings = echo_server.apply_reasoning(payload, {}, "off")
        self.assertEqual(level, "ultra")
        self.assertEqual(payload["reasoning_budget_tokens"], -1)
        self.assertEqual(settings["passes"], 1)
        self.assertEqual(payload["reasoning_min_tokens"], 0)
        self.assertTrue(payload["chat_template_kwargs"]["enable_thinking"])

    def test_large_window_keeps_requested_reasoning_allowance(self):
        reserve = echo_server.generation_reserve(64000, 58000, 65536, 12000)
        self.assertGreaterEqual(reserve, 14048)
        self.assertEqual(echo_server.reasoning_budget_for_output(12000, reserve), 12000)

    def test_adaptive_output_leaves_room_for_source_memory_without_capping_thinking(self):
        for room, window in ((58000, 65536), (3100, 8192)):
            reserve = echo_server.generation_reserve(64000, room, window, -1)
            self.assertGreater(reserve, 0)
            self.assertLess(reserve, room)
            self.assertEqual(echo_server.reasoning_budget_for_output(-1, reserve), -1)
            self.assertEqual(echo_server.generation_reserve(512, room, window, -1), 512)

    def test_small_window_keeps_space_for_answer_and_never_exceeds_room(self):
        reserve = echo_server.generation_reserve(64000, 3100, 8192, 12000)
        self.assertEqual(reserve, 3100)
        self.assertEqual(echo_server.reasoning_budget_for_output(12000, reserve), 2325)
        self.assertEqual(echo_server.reasoning_budget_for_output(0, reserve), 0)
        self.assertEqual(echo_server.reasoning_budget_for_output(512, reserve), 512)

    def test_generation_log_preserves_every_pass_before_response_is_rewritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            handler = echo_server.Handler.__new__(echo_server.Handler)
            handler.state = types.SimpleNamespace(archives=types.SimpleNamespace(directory=Path(temporary)))
            handler._begin_generation_log("conversation-one", "ultra", 12000)
            draft = {"id": "draft", "choices": [{"message": {
                "reasoning_content": "  exact <think>\nπ\n</think>  ", "content": "Original draft"
            }, "finish_reason": "stop"}], "provider_extension": {"untouched": [1, 2]}}
            review = {"id": "review", "choices": [{"message": {
                "reasoning_content": "Review reasoning", "content": "NO ISSUES"
            }, "finish_reason": "stop"}]}
            responses = iter([draft, review])

            def upstream(*args, **kwargs):
                return io.BytesIO(json.dumps(next(responses), ensure_ascii=False).encode())

            handler._upstream = upstream
            first = handler._generate_live({"max_tokens": 16384, "reasoning_budget_tokens": 12000}, "working")
            first["choices"][0]["message"]["content"] = "Rewritten final"
            handler._generate_live({"max_tokens": 1500, "reasoning_budget_tokens": 512}, "reviewing")
            descriptor = handler._generation_log_status()
            records = [json.loads(line) for line in Path(descriptor["generation_file"]).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(descriptor["generation_count"], 2)
            self.assertEqual(records[0]["response"]["choices"][0]["message"]["content"], "Original draft")
            self.assertEqual(records[0]["response"]["choices"][0]["message"]["reasoning_content"], "  exact <think>\nπ\n</think>  ")
            self.assertEqual(records[0]["response"]["provider_extension"], {"untouched": [1, 2]})
            self.assertEqual(records[0]["request"]["reasoning_budget_tokens"], 12000)
            self.assertEqual(records[1]["phase"], "reviewing")
            self.assertEqual(descriptor["reasoning_effort"], "ultra")


if __name__ == "__main__":
    unittest.main()
