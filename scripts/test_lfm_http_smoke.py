import unittest
import json
import tempfile
from pathlib import Path

import lfm_http_smoke


class SpeedQualificationTests(unittest.TestCase):
    def test_runtime_command_configures_server_side_reasoning_budget(self):
        command = lfm_http_smoke.build_runtime_command(
            "dualcore-kv", Path("model.gguf"), 8870, thinking_budget_tokens=512
        )

        self.assertEqual("--reasoning-budget-tokens", command[-2])
        self.assertEqual("512", command[-1])

    def test_loads_real_humaneval_speed_prompt_by_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inputs.json"
            path.write_text(json.dumps({"rows": [
                {"id": "HumanEval/1", "prompt": "other"},
                {"id": "HumanEval/32", "prompt": "representative code task"},
            ]}))

            self.assertEqual("representative code task",
                             lfm_http_smoke.load_speed_prompt(path, "HumanEval/32"))

    def test_missing_speed_prompt_id_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inputs.json"
            path.write_text(json.dumps({"rows": []}))

            with self.assertRaisesRegex(ValueError, "HumanEval/32"):
                lfm_http_smoke.load_speed_prompt(path, "HumanEval/32")

    def test_speed_probe_bounds_echo_to_one_backend_call(self):
        payload = lfm_http_smoke.build_speed_payload(
            "representative task", "fusioncore-echo", "speed-chat", 512
        )

        self.assertEqual(4, payload["echo_max_calls"])
        self.assertEqual("speed-chat", payload["conversation_id"])
        self.assertEqual(512, payload["max_tokens"])
        self.assertEqual(0, payload["temperature"])

    def test_speed_probe_carries_requested_thinking_budget(self):
        payload = lfm_http_smoke.build_speed_payload(
            "representative task", "dualcore-kv", "speed-chat", 512, 256
        )

        self.assertEqual(256, payload["thinking_budget_tokens"])

    def test_speed_probe_does_not_add_echo_controls_to_native_profile(self):
        payload = lfm_http_smoke.build_speed_payload(
            "representative task", "fusioncore-kv", "speed-chat", 512
        )

        self.assertNotIn("echo_max_calls", payload)

    def test_extracts_text_from_content_part_deltas(self):
        events = [
            {"choices": [{"delta": {"content": {"type": "text", "text": "hello "}}}]},
            {"choices": [{"delta": {"reasoning_content": "thought "}}]},
            {"choices": [{"delta": {"content": "world"}}]},
        ]

        self.assertEqual("hello world", lfm_http_smoke.extract_stream_text(events))

    def test_echo_readiness_rejects_a_direct_backend_response(self):
        direct_backend_props = {
            "default_generation_settings": {"params": {"n_ctx": 32768}}
        }

        with self.assertRaisesRegex(RuntimeError, "persistent ECHO proxy"):
            lfm_http_smoke.validate_echo_readiness(direct_backend_props, 32768)

    def test_rejects_fast_total_decode_when_reasoning_reduces_answer_wall_rate(self):
        probe = {
            "streamed_draft_tokens": 80,
            "streamed_draft_tokens_per_second": 40.0,
            "selected_answer_tokens": 80,
            "selected_answer_wall_tokens_per_second": 4.0,
            "all_brain_tokens": 180,
            "aggregate_model_tokens_per_second": 31.0,
            "selected_finish_reason": "stop",
            "minimum_streamed_draft_tokens_per_second": 20,
        }

        self.assertIn("user-visible answer rate", lfm_http_smoke.speed_probe_failure(probe))

    def test_rejects_slow_total_decode_even_if_visible_answer_is_fast(self):
        probe = {
            "streamed_draft_tokens": 80,
            "streamed_draft_tokens_per_second": 40.0,
            "selected_answer_tokens": 80,
            "selected_answer_wall_tokens_per_second": 40.0,
            "all_brain_tokens": 180,
            "aggregate_model_tokens_per_second": 4.0,
            "selected_finish_reason": "stop",
            "minimum_streamed_draft_tokens_per_second": 20,
        }

        self.assertIn("aggregate model", lfm_http_smoke.speed_probe_failure(probe))

    def test_accepts_draft_and_selected_answer_at_target_rate(self):
        probe = {
            "streamed_draft_tokens": 80,
            "streamed_draft_tokens_per_second": 40.0,
            "selected_answer_tokens": 80,
            "selected_answer_wall_tokens_per_second": 24.0,
            "aggregate_model_tokens_per_second": 24.0,
            "selected_finish_reason": "stop",
            "minimum_streamed_draft_tokens_per_second": 20,
        }

        self.assertEqual("", lfm_http_smoke.speed_probe_failure(probe))

    def test_rejects_speed_probe_that_hits_output_token_cap(self):
        probe = {
            "streamed_draft_tokens": 80,
            "streamed_draft_tokens_per_second": 40.0,
            "selected_answer_tokens": 512,
            "selected_answer_wall_tokens_per_second": 59.0,
            "selected_finish_reason": "length",
            "minimum_streamed_draft_tokens_per_second": 20,
        }

        self.assertIn("did not stop", lfm_http_smoke.speed_probe_failure(probe))


if __name__ == "__main__":
    unittest.main()
