"""Cross-turn archive continuity checks; these do not measure model attention."""

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))
from evoagent.echo_memory import EchoArchive  # noqa: E402
from evoagent.echo_context import LiveTranscript  # noqa: E402
from echo_server import ArchiveSet, EchoState  # noqa: E402


class EchoMemoryContinuityTests(unittest.TestCase):
    def test_completed_turn_does_not_archive_promoted_memory_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = EchoArchive(Path(temporary) / "archive.db")
            self.addCleanup(archive.close)
            page = archive.append("The exact original physics decision.", "project-a")[0]
            live = LiveTranscript(archive, "project-a")
            live.append_memory_pages([page], lambda text: len(text.split()), 256)
            live.start_turn("Explain physics", lambda text: len(text.split()))
            live.append_generated({"role": "assistant", "content": "Explanation"}, lambda text: len(text.split()))
            live.open = False
            live.archive_completed()
            with archive._lock:
                copied = archive.db.execute("SELECT content FROM source_events WHERE content LIKE '%ECHO automatic recall%'").fetchall()
            archive.close()
            self.assertEqual(copied, [])

    def test_each_new_turn_can_promote_exact_old_archive_pages_into_live_transcript(self):
        with tempfile.TemporaryDirectory(prefix="opencore-echo-live-recall-") as temporary:
            root = Path(temporary)
            archives = ArchiveSet(root, idle_seconds=60)
            self.addCleanup(archives.close)
            state = EchoState(archives, "http://127.0.0.1:1", 0, 4, False)
            state.count_tokens = lambda text: max(1, len(text.split()))
            archive = archives.get("project-a")
            fact = "Physics controller uses swept capsule casts to prevent wall tunneling."
            archive.append(fact, "project-a", timestamp=1.0)
            live = LiveTranscript(archive, "project-a")

            result = state.append_automatic_recall(
                live, "Why does the physics controller use capsule casts?", "project-a", 256)

            memory_entries = [entry for entry in live.entries if entry.get("kind") == LiveTranscript.MEMORY]
            self.assertEqual(result["pages"], 1)
            self.assertEqual(len(memory_entries), 1)
            self.assertIn(fact, memory_entries[0]["message"]["content"])
            self.assertEqual(memory_entries[0]["echo_source_hashes"], [archive.retrieve(
                "physics controller capsule casts", conversation_id="project-a").pages[0].content_hash])
            archives.close()

    def test_auto_recall_does_not_duplicate_a_page_already_active(self):
        with tempfile.TemporaryDirectory(prefix="opencore-echo-live-recall-dedupe-") as temporary:
            root = Path(temporary)
            archives = ArchiveSet(root, idle_seconds=60)
            self.addCleanup(archives.close)
            state = EchoState(archives, "http://127.0.0.1:1", 0, 4, False)
            state.count_tokens = lambda text: max(1, len(text.split()))
            archive = archives.get("project-a")
            archive.append("The renderer uses clustered forward lighting.", "project-a", timestamp=1.0)
            live = LiveTranscript(archive, "project-a")

            first = state.append_automatic_recall(live, "Why clustered lighting?", "project-a", 256)
            second = state.append_automatic_recall(live, "Why clustered lighting?", "project-a", 256)

            self.assertEqual(first["pages"], 1)
            self.assertEqual(second["pages"], 0)
            self.assertEqual(sum(entry.get("kind") == LiveTranscript.MEMORY for entry in live.entries), 1)
            archives.close()

    def test_zero_auto_recall_budget_leaves_the_live_context_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="opencore-echo-no-auto-recall-") as temporary:
            archives = ArchiveSet(Path(temporary), idle_seconds=60)
            self.addCleanup(archives.close)
            self.addCleanup(archives.close)
            state = EchoState(archives, "http://127.0.0.1:1", 0, 4, False,
                              automatic_recall_tokens=0)
            state.count_tokens = lambda text: max(1, len(text.split()))
            archive = archives.get("project-a")
            archive.append("A unique automatic recall budget fixture.", "project-a", timestamp=1.0)
            live = LiveTranscript(archive, "project-a")

            result = state.append_automatic_recall(
                live, "Find the unique automatic recall budget fixture", "project-a")

            self.assertEqual(result["pages"], 0)
            self.assertEqual(live.entries, [])
            archives.close()

    def test_compaction_does_not_rearchive_synthetic_recalled_memory(self):
        with tempfile.TemporaryDirectory(prefix="opencore-echo-no-duplicate-") as temporary:
            root = Path(temporary)
            archives = ArchiveSet(root, idle_seconds=60)
            self.addCleanup(archives.close)
            state = EchoState(archives, "http://127.0.0.1:1", 0, 4, False)
            state.count_tokens = lambda text: max(1, len(text.split()))
            archive = archives.get("project-a")
            source = archive.append("The physics subsystem selected swept capsule casts.",
                                    "project-a", timestamp=1.0)[0]
            live = LiveTranscript(archive, "project-a")
            live.append({"role": "user", "content": "Old question about physics"}, state.count_tokens,
                        LiveTranscript.TURN)
            live.append_generated({"role": "assistant", "content": "Old answer"}, state.count_tokens)
            live.append_memory_pages([source], state.count_tokens, 256)
            live.start_turn("Current question", state.count_tokens)

            moved = live.compact(0, state.count_tokens)
            recalled_copy = archive.retrieve("automatic recalled memory evidence",
                                             conversation_id="project-a")

            self.assertGreater(moved, 0)
            self.assertFalse(any("ECHO automatic recall" in page.text for page in recalled_copy.pages))
            self.assertIsNotNone(archive.load(source.page_id))
            archives.close()

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
