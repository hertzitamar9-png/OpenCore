import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import echo_server


class ReasoningPolicyTests(unittest.TestCase):
    def test_adaptive_effort_has_no_forced_thinking_bounds(self):
        signals = []
        for name, level in echo_server.REASONING_POLICY["levels"].items():
            self.assertEqual(level["minimumTokens"], 0)
            self.assertEqual(level["maximumTokens"], 0 if name == "off" else -1)
            if name != "off":
                signals.append(level["guidance"])
        self.assertEqual(len(set(signals)), 6)

    def test_adaptive_guidance_preserves_sources_and_is_idempotent(self):
        source = [{"role": "system", "content": "Exact source π  "},
                  {"role": "user", "content": [{"type": "text", "text": "Hi"}]},
                  {"role": "tool", "content": "Exact tool result", "tool_call_id": "one"}]
        for effort in echo_server.REASONING_POLICY["levels"]:
            payload = {"messages": copy.deepcopy(source), "reasoning_effort": effort}
            _, settings = echo_server.apply_reasoning(payload, {}, "off")
            self.assertEqual([m for m in payload["messages"] if not m.get("opencore_effort_control")], source)
            self.assertEqual(payload["reasoning_min_tokens"], 0)
            self.assertEqual(payload["reasoning_budget_tokens"], 0 if effort == "off" else -1)
            self.assertEqual(settings["passes"], 1)
            self.assertEqual(payload["chat_template_kwargs"]["enable_thinking"], effort != "off")
            self.assertEqual(payload["reasoning_control"], effort != "off")
            self.assertNotIn("draft 1", payload["messages"][0]["content"].lower())
            once = copy.deepcopy(payload)
            echo_server.apply_reasoning(payload, {}, "off")
            self.assertEqual(payload, once)

    def test_explicit_native_probe_bounds_are_retained_and_clamped_to_answer_room(self):
        payload = {"messages": [{"role": "user", "content": "Hi"}], "reasoning_effort": "opencore",
                   "reasoning_min_tokens": 128, "reasoning_budget_tokens": 384, "max_tokens": 1024}
        echo_server.apply_reasoning(payload, {}, "off")
        self.assertEqual((payload["reasoning_min_tokens"], payload["reasoning_budget_tokens"]), (128, 384))
        payload = {"messages": [], "reasoning_effort": "opencore", "max_tokens": 100}
        echo_server.apply_reasoning(payload, {}, "off")
        self.assertEqual((payload["reasoning_min_tokens"], payload["reasoning_budget_tokens"]), (0, -1))

    def test_opencore_effort_replaces_a_stale_positive_budget_with_unlimited_thinking(self):
        payload = {
            "messages": [{"role": "user", "content": "Continue after the tool result."}],
            "opencore_reasoning_effort": "low",
            "reasoning_budget_tokens": 512,
            "reasoning_min_tokens": 0,
            "max_tokens": 8192,
        }
        echo_server.apply_reasoning(payload, {}, "off")
        self.assertEqual(payload["reasoning_budget_tokens"], -1)
        self.assertEqual(payload["reasoning_min_tokens"], 0)

    def test_policy_has_no_prescribed_outline_and_load_failure_is_actionable(self):
        self.assertFalse(any("draft 1" in value["guidance"].lower() for value in echo_server.REASONING_POLICY["levels"].values()))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reasoning-policy.json"
            with self.assertRaisesRegex(RuntimeError, "Keep echo_server.py and reasoning-policy.json"):
                echo_server.load_reasoning_policy(path)

    def test_minimum_requires_real_backend_capability_and_off_skips_probe(self):
        with patch("echo_server.urllib.request.urlopen", return_value=io.BytesIO(b'{"reasoning_min_tokens":true}')) as opened:
            echo_server.ensure_reasoning_capability("http://127.0.0.1:1", {}, {"reasoning_min_tokens": 128})
            self.assertEqual(opened.call_args.args[0].full_url, "http://127.0.0.1:1/props")
        with patch("echo_server.urllib.request.urlopen", return_value=io.BytesIO(b'{}')):
            with self.assertRaisesRegex(ValueError, "does not support.*reasoning_min_tokens"):
                echo_server.ensure_reasoning_capability("http://127.0.0.1:1", {}, {"reasoning_min_tokens": 128})
        with patch("echo_server.urllib.request.urlopen") as opened:
            echo_server.ensure_reasoning_capability("http://127.0.0.1:1", {}, {"reasoning_min_tokens": 0})
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
