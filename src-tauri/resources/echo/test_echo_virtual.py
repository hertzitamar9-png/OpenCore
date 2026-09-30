"""Functional virtual-memory tests, not model-quality benchmarks."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evoagent.echo_memory import EchoArchive, BoundedPageCache
from evoagent.echo_context import LiveTranscript


class VirtualMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = EchoArchive(Path(self.temp.name) / "archive.db", page_cache=BoundedPageCache(32768))
        self.addCleanup(self.archive.close)
        from evoagent.echo_virtual import EchoMemoryController
        from evoagent.echo_adapters import adapter_for
        self.adapter_for = adapter_for
        self.counts = 0
        def count(text):
            self.counts += 1
            return len(text.encode("utf-8"))
        self.adapter = adapter_for({"model": "fixture", "architecture": "qwen", "revision": "1"}, count)
        self.controller = EchoMemoryController(memory_tokens=2048, refresh_tokens=128)
        self.addCleanup(self.controller.close)

    def recall(self, query, conversation="a", live=None, adapter=None, reason="new_turn"):
        live = live or LiveTranscript(self.archive, conversation)
        return self.controller.refresh(live, query, [self.archive], adapter or self.adapter,
                                       capacity=8192, pinned_tokens=256, reserve_tokens=1024, reason=reason)

    def test_unknown_and_recurrent_models_use_valid_fresh_prefill(self):
        for architecture in ("llama", "qwen", "mistral", "gemma", "phi", "moe", "unknown", "lfm2", "mamba"):
            adapter = self.adapter_for({"architecture": architecture}, len)
            capabilities = adapter.inspect_capabilities()
            self.assertFalse(capabilities["supports_direct_kv_reuse"])
            self.assertIn(capabilities["materialization_mode"], ("rematerialization", "textual_reprefill"))

    def test_exact_old_symbol_is_active_and_scope_is_enforced(self):
        fact = "CharacterController.py fixed CUDA_ERROR_OUT_OF_MEMORY with bounded pools."
        self.archive.append(fact, "a", 1)
        self.archive.append("CharacterController.py private secret OTHER_PROJECT", "b", 2)
        live = LiveTranscript(self.archive, "a")
        result = self.recall("Recall CharacterController.py CUDA_ERROR_OUT_OF_MEMORY", live=live)
        active = " ".join(m["content"] for m in live.messages)
        self.assertIn(fact, active)
        self.assertNotIn("OTHER_PROJECT", active)
        self.assertTrue(result["pages"])
        self.assertLessEqual(result["tokens"], 2048)

    def test_explicit_project_allowlist_reuses_related_conversation_only(self):
        self.archive.append("PhysicsController chosen swept capsules RELATED_PROJECT", "related", 1)
        self.archive.append("PhysicsController unrelated PRIVATE_OTHER_PROJECT", "other", 2)
        live = LiveTranscript(self.archive, "a")
        result = self.controller.refresh(live, "Recall PhysicsController", [(self.archive, "a"), (self.archive, "related")],
                                         self.adapter, 8192, 256, 1024)
        text = " ".join(m["content"] for m in live.messages)
        self.assertIn("RELATED_PROJECT", text)
        self.assertNotIn("PRIVATE_OTHER_PROJECT", text)
        self.assertTrue(result["pages"])

    def test_repeated_recall_uses_cache_and_preserves_backend_prefix(self):
        self.archive.append("PhysicsController uses swept capsules.", "a")
        live = LiveTranscript(self.archive, "a")
        first = self.recall("Recall PhysicsController", live=live)
        live.mark_backend_sent()
        before = self.counts
        second = self.recall("Recall PhysicsController", live=live)
        self.assertEqual(first["source_hashes"], second["source_hashes"])
        self.assertFalse(second["layout_changed"])
        self.assertTrue(all(e["backend_sent"] for e in live.entries))
        self.assertGreater(second["telemetry"]["materialization_cache_hits"], 0)
        self.assertLess(self.counts - before, 3)

    def test_exact_query_does_not_fill_attention_with_recent_distractors(self):
        self.archive.append("PhysicsController uses swept capsules.", "a", 1)
        for index in range(40):
            self.archive.append(f"Unrelated menu color note {index}", "a", index + 2)
        result = self.recall("Recall PhysicsController")
        self.assertEqual(result["pages"], 1)

    def test_switching_model_invalidates_cost_cache_but_preserves_archive(self):
        fact = "PhysicsController uses swept capsules."
        self.archive.append(fact, "a")
        self.recall("Recall PhysicsController")
        count_calls = []
        other = self.adapter_for({"model": "other", "revision": "2"}, lambda t: count_calls.append(t) or len(t.split()))
        result = self.recall("Recall PhysicsController", adapter=other)
        self.assertTrue(count_calls)
        self.assertTrue(result["pages"])
        self.assertEqual(self.archive.recent_pages("a", 1)[0].text, fact)

    def test_saved_recent_token_costs_are_rebuilt_once_for_new_model(self):
        live = LiveTranscript(self.archive, "a")
        live.start_turn("Some existing recent text", lambda text: 1)
        live.mark_backend_sent()
        self.recall("hello", live=live)
        self.assertEqual(live.entries[0]["tokens"], len("Some existing recent text".encode()) + 8)
        before = self.counts
        self.recall("hello", live=live)
        self.assertEqual(before, self.counts)
        self.assertFalse(live.entries[0]["backend_sent"])

    def test_memory_budget_never_grows_with_archive_size_or_refreshes(self):
        live = LiveTranscript(self.archive, "a")
        for index in range(50):
            self.archive.append(f"Subsystem{index}.py unique decision " + "detail " * 50, "a")
            result = self.recall(f"Recall Subsystem{index}.py", live=live)
            self.assertLessEqual(live.memory_status()["echoRecalledTokens"], 2048)
            self.assertLessEqual(result["telemetry"]["physical_context_tokens"] + 1024, 8192)
        self.assertGreater(self.archive.stats()["source_bytes"], 12000)

    def test_newest_conflicting_decision_and_causal_neighbors_survive(self):
        old = self.archive.append("ServerConfig port = 8000; backend Redis selected.", "a", 1)[0]
        new = self.archive.append("ServerConfig port = 8765; SQLite replaced Redis due to lock failures.", "a", 2)[0]
        self.controller.link(self.archive, "a", new.page_id, old.page_id, "supersedes")
        live = LiveTranscript(self.archive, "a")
        result = self.recall("Why does ServerConfig use SQLite and which port?", live=live)
        text = " ".join(m["content"] for m in live.messages)
        self.assertIn("8765", text)
        self.assertIn("8000", text)
        self.assertLess(text.index("8000"), text.index("8765"))
        self.assertTrue(result["telemetry"]["relations_loaded"])

    def test_greeting_does_not_retrieve_unrelated_memory(self):
        self.archive.append("PhysicsController confidential evidence", "a")
        result = self.recall("hello")
        self.assertFalse(result["pages"])
        self.assertEqual(result["telemetry"]["candidates_considered"], 0)

    def test_ordinary_anaphoric_recall_can_use_a_scoped_recent_decision(self):
        fact = "PhysicsController uses swept capsules. This was our final project choice."
        self.archive.append(fact, "a")
        for query in ("What did we decide last time?", "Remind me what we agreed on."):
            live = LiveTranscript(self.archive, "a")
            self.recall(query, live=live)
            self.assertIn(fact, " ".join(m["content"] for m in live.messages))

    def test_multiple_distant_needles_and_project_continuity(self):
        facts = ["PhysicsController invariant: capsule sweep must preserve running.",
                 "PhysicsController patch: movement.py uses delta_time for walking.",
                 "PhysicsController regression fixed: test_running_preserved passes."]
        for i, fact in enumerate(facts):
            self.archive.append(fact, "a", i * 100)
            self.archive.append("Menu labels are unrelated.\n" * 100, "a", i * 100 + 1)
        live = LiveTranscript(self.archive, "a")
        result = self.recall("Continue PhysicsController movement.py test_running_preserved", live=live)
        text = " ".join(m["content"] for m in live.messages)
        for fact in facts:
            self.assertIn(fact, text)
        self.assertLessEqual(result["tokens"], 2048)

    def test_deleting_conversation_removes_derived_text_and_saved_working_set(self):
        self.archive.append("PhysicsController confidential history", "a")
        self.recall("Recall PhysicsController")
        self.archive.delete_conversation("a")
        self.assertEqual(LiveTranscript(self.archive, "a").entries, [])
        for table in ("echo_materializations", "echo_virtual_state", "echo_page_links"):
            self.assertEqual(self.archive.db.execute(f"SELECT COUNT(*) FROM {table} WHERE scope='a'").fetchone()[0], 0)

    def test_incremental_history_accounting_survives_reopen(self):
        self.archive.append("exact π source", "a")
        self.archive.append("other scope", "b")
        usage = self.archive.scope_usage("a")
        self.assertEqual(usage["source_bytes"], len("exact π source".encode()))
        self.archive.close()
        self.assertEqual(self.archive.scope_usage("a")["source_bytes"], usage["source_bytes"])

    def test_disk_materialization_cache_is_strictly_bounded(self):
        self.controller.cache.disk_bytes = 100
        self.archive.append("Renderer.py uses forward lighting", "a")
        result = self.recall("Recall Renderer.py")
        self.assertTrue(result["pages"])
        with self.archive._lock:
            size = self.archive.db.execute("SELECT COALESCE(SUM(LENGTH(payload)),0) FROM echo_materializations").fetchone()[0]
        self.assertLessEqual(size, 100)

    def test_derived_cache_write_failure_keeps_canonical_recall(self):
        from unittest.mock import patch
        self.archive.append("Renderer.py uses forward lighting", "a")
        with patch.object(self.controller.cache, "schema", side_effect=OSError("disk full")):
            result = self.recall("Recall Renderer.py")
        self.assertTrue(result["pages"])
        self.assertTrue(result["telemetry"]["diagnostics"])

    def test_tool_boundary_refresh_and_page_fault_are_observable(self):
        self.archive.append("Renderer.py uses forward lighting", "a")
        live = LiveTranscript(self.archive, "a")
        self.recall("Recall Renderer.py", live=live, reason="tool_result")
        result = self.recall("Recall Renderer.py", live=live, reason="page_fault")
        self.assertEqual(result["telemetry"]["page_faults"], 1)
        self.assertEqual(result["telemetry"]["last_refresh_reason"], "page_fault")

    def test_corrupt_candidate_is_reported_and_does_not_replace_valid_context(self):
        page = self.archive.append("Renderer.py uses forward lighting", "a")[0]
        self.archive.page_cache.clear()
        with self.archive._lock:
            self.archive.db.execute("UPDATE pages SET compressed_bytes=? WHERE page_id=?", (b"broken", page.page_id))
            self.archive.db.commit()
        result = self.recall("Recall Renderer.py")
        self.assertTrue(result["telemetry"]["diagnostics"])
        self.assertFalse(result["pages"])


if __name__ == "__main__":
    unittest.main()
