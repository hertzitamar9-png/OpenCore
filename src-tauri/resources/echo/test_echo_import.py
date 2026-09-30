import json
import tempfile
import unittest
from pathlib import Path

import echo_import


class EchoImportDeleteTests(unittest.TestCase):
    def test_delete_clears_imported_pages_events_and_allows_reimport(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conversation_id = "codex:session-one"
            event = {
                "source_event_id": "stable-event-one",
                "source": "Codex",
                "role": "user",
                "kind": "message",
                "content": "A unique imported transcript line.",
            }
            batch = json.dumps({"conversation_id": conversation_id, "messages": [event]}) + "\n"
            self.assertEqual(echo_import.import_stream(root, [batch])["imported"], 1)
            path = echo_import.path_for(root, conversation_id)
            archive = echo_import.EchoArchive(path)
            self.assertEqual(archive.db.execute("SELECT COUNT(*) FROM source_events").fetchone()[0], 1)
            self.assertEqual(archive.db.execute("SELECT COUNT(*) FROM pages").fetchone()[0], 1)
            archive.close()

            deleted = echo_import.delete_stream(root, [json.dumps({"conversation_id": conversation_id})])
            self.assertEqual((deleted["conversations"], deleted["records"], deleted["failed"]), (1, 2, 0))
            archive = echo_import.EchoArchive(path)
            self.assertEqual(archive.db.execute("SELECT COUNT(*) FROM source_events").fetchone()[0], 0)
            self.assertEqual(archive.db.execute("SELECT COUNT(*) FROM pages").fetchone()[0], 0)
            archive.close()

            self.assertEqual(echo_import.import_stream(root, [batch])["imported"], 1)

    def test_delete_handles_archive_without_legacy_import_fingerprints(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conversation_id = "codex:empty-fingerprint-table"
            archive = echo_import.EchoArchive(echo_import.path_for(root, conversation_id))
            archive.append("Archive text without imported-message fingerprints.", conversation_id)
            archive.close()
            deleted = echo_import.delete_stream(root, [json.dumps({"conversation_id": conversation_id})])
            self.assertEqual((deleted["conversations"], deleted["records"], deleted["failed"]), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
