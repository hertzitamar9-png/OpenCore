"""Cross-turn archive continuity checks; these do not measure model attention."""

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))
from evoagent.echo_memory import EchoArchive  # noqa: E402


class EchoMemoryContinuityTests(unittest.TestCase):
    def test_old_exact_fact_survives_many_later_turns_and_restart(self):
        with tempfile.TemporaryDirectory(prefix="opencore-echo-continuity-") as temporary:
            archive_path = Path(temporary) / "conversation.db"
            archive = EchoArchive(archive_path)
            fact = "Project quartz release key: amberwood-7f3c. Keep this exact value."
            original_page = archive.append(fact, "project-a", timestamp=1.0)[0]
            for index in range(600):
                archive.append(
                    f"Status update {index}: completed routine maintenance and review.",
                    "project-a",
                    timestamp=2.0 + index,
                )
            archive.close()

            reopened = EchoArchive(archive_path)
            try:
                exact = reopened.load(original_page.page_id)
                recalled = reopened.retrieve(
                    "What is the exact quartz release key?",
                    conversation_id="project-a",
                )
                isolated = reopened.retrieve(
                    "What is the exact quartz release key?",
                    conversation_id="project-b",
                )

                self.assertIsNotNone(exact)
                self.assertEqual(exact.text, fact)
                self.assertFalse(recalled.uncertain, recalled.reason)
                self.assertTrue(any(fact in page.text for page in recalled.pages))
                self.assertTrue(isolated.uncertain)
                self.assertFalse(any(fact in page.text for page in isolated.pages))
                self.assertEqual(reopened.verify().get("corrupt"), 0)
            finally:
                reopened.close()

    def test_old_fact_survives_hot_window_rollover_into_cold_archive(self):
        with tempfile.TemporaryDirectory(prefix="opencore-echo-cold-tier-") as temporary:
            root = Path(temporary)
            hot_path = root / "hot.db"
            cold_path = root / "cold.db"
            archive = EchoArchive(hot_path)
            fact = "Project quartz release key: amberwood-7f3c. Keep this exact value."
            archive.append(fact, "project-a", timestamp=1.0)
            for index in range(30):
                archive.append(
                    f"Routine status update {index}: review complete.",
                    "project-a",
                    timestamp=2.0 + index,
                )

            rollover = archive.roll_window(
                "project-a", keep_tokens=100, cold_path=cold_path,
                chars_per_token=1.0,
            )
            archive.close()
            cold = EchoArchive(cold_path)
            try:
                recalled = cold.retrieve(
                    "What is the exact quartz release key?",
                    conversation_id="project-a",
                )
                self.assertGreater(rollover["moved"], 0)
                self.assertFalse(recalled.uncertain, recalled.reason)
                self.assertTrue(any(fact in page.text for page in recalled.pages))
                self.assertEqual(cold.verify().get("corrupt"), 0)
            finally:
                cold.close()


if __name__ == "__main__":
    unittest.main()
