import sys
import unittest
from pathlib import Path

ECHO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ECHO_DIR))
import echo_server


class ReasoningOffAliasTests(unittest.TestCase):
    def test_fast_and_speed_legacy_values_resolve_to_off(self):
        for value in ("fast", "speed"):
            with self.subTest(value=value):
                level, settings = echo_server.resolve_reasoning(
                    {"reasoning_effort": value}, {}, "medium"
                )
                self.assertEqual(level, "off")
                self.assertEqual(settings["budget"], 0)

    def test_legacy_fast_request_disables_thinking_in_payload(self):
        payload = {"reasoning_effort": "fast", "chat_template_kwargs": {"other": True}}
        level, settings = echo_server.apply_reasoning(payload, {}, "medium")
        self.assertEqual(level, "off")
        self.assertEqual(settings["budget"], 0)
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(payload["reasoning_budget_tokens"], 0)
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(payload["chat_template_kwargs"]["other"], True)
        self.assertEqual(payload["reasoning_format"], "none")

    def test_low_remains_a_512_token_reasoning_mode(self):
        level, settings = echo_server.resolve_reasoning(
            {"reasoning_effort": "low"}, {}, "medium"
        )
        self.assertEqual(level, "low")
        self.assertEqual(settings["budget"], 512)


if __name__ == "__main__":
    unittest.main()
