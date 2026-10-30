"""Recovery contract tests. Only temporary databases and a local fixture server."""
import copy
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from hf_memory_repair import MemoryRepair


SOURCE = "I played professional \u26bd a year ago"
CHAT = "opencore:football"
SESSION = hashlib.sha256(CHAT.encode()).hexdigest()
GOOD = {"memories": [{"key": "personal.professional-football", "kind": "fact", "scope": "global", "quote": SOURCE}]}


class MemoryRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.control = self.data / "control-center.sqlite3"
        self.platform = self.data / "agent-platform.sqlite3"
        with closing(sqlite3.connect(self.control)) as db, db:
            db.executescript("""
                CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE conversations(id TEXT PRIMARY KEY,status TEXT,project_id TEXT);
                CREATE TABLE timeline(id INTEGER PRIMARY KEY,conversation_id TEXT,kind TEXT,role TEXT,source TEXT,content TEXT,metadata TEXT);
            """)
            db.execute("INSERT INTO settings VALUES(?,?)", ("agent_platform_configuration_v1", '{"memoryEnabled":true}'))
            db.execute("INSERT INTO conversations VALUES(?,'idle',NULL)", (CHAT,))
            db.execute("INSERT INTO timeline VALUES(7,?,'message','user','OpenCore',?,'{}')", (CHAT, SOURCE))
            db.execute("INSERT INTO timeline VALUES(8,?,'message','assistant','OpenCore',?,'{}')", (CHAT, "You played esports football."))
        with closing(sqlite3.connect(self.platform)) as db, db:
            db.executescript("""
                CREATE TABLE memories(id TEXT,key TEXT,kind TEXT,scope TEXT,content TEXT,source TEXT,evidence TEXT,status TEXT);
                CREATE TABLE memory_tombstones(scope TEXT,kind TEXT,key TEXT,content_hash TEXT,deleted_at TEXT);
                CREATE TABLE memory_source_tombstones(source_hash TEXT,scope TEXT,deleted_at TEXT);
                CREATE TABLE memory_auto_captures(conversation_id TEXT,user_entry_id INTEGER,status TEXT,saved_count INTEGER,error TEXT);
            """)
            db.execute("INSERT INTO memory_auto_captures VALUES(?,7,'failed',0,?)", (CHAT, "Invalid automatic memory JSON: expected value at line 1 column 1"))
        self.server.posts = []
        self.server.responses = [copy.deepcopy(GOOD)]
        self.server.on_completion = None
        self.server.restored = True
        self.server.processing = False
        self.server.branch_supported = True
        self.server.http_error = None
        self.server.redirect = None
        self.server.branch = None
        self.server.slot_session = SESSION
        self.server.slot_session_tokens = 4750
        self.server.slot_prompt_tokens = 4737
        self.server.invalidation_verified = True
        self.port = self.server.server_port
        self.repair = MemoryRepair(self.data, allowed_ports={self.port})
        self.request = {"conversationId": CHAT, "userEntryId": 7, "temporaryChat": False,
                        "runtime": {"status": "running", "backendPort": self.port, "echoPort": self.port, "profile": "echo"}}

    def mutate(self, database, sql, args=()):
        with closing(sqlite3.connect(database)) as db, db:
            db.execute(sql, args)

    def test_exact_human_fact_returns_native_record_arguments_after_verified_cache_restore(self):
        before = self.platform.read_bytes(), self.control.read_bytes()
        result = self.repair.recover(self.request)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["sourceSha256"], hashlib.sha256(SOURCE.encode()).hexdigest())
        args = result["candidates"][0]["recordArgs"]
        self.assertEqual({k: v for k, v in args.items() if k != "evidence"}, {
            "action": "record", "key": "chat.fact." + hashlib.sha256(("chat:opencore:football:user-entry:7\0" + SOURCE).encode()).hexdigest()[:32], "kind": "fact", "scope": "global",
            "content": SOURCE, "source": "chat:opencore:football:user-entry:7"})
        self.assertEqual(json.loads(args["evidence"]), {"automatic": True, "conversationId": CHAT, "userEntryId": 7,
            "quote": SOURCE, "recovery": "local-constrained-json-v1", "sourceSha256": hashlib.sha256(SOURCE.encode()).hexdigest()})
        self.assertTrue(result["diagnostics"]["cacheRestored"])
        self.assertTrue(result["diagnostics"]["leaseReleased"])
        self.assertEqual((self.platform.read_bytes(), self.control.read_bytes()), before)
        completion = next(body for path, body in self.server.posts if path == "/v1/chat/completions")
        self.assertEqual(completion["response_format"]["type"], "json_object")
        schema = completion["response_format"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["memories"]["maxItems"], 5)
        self.assertEqual(completion["tools"], [])
        self.assertEqual(completion["reasoning_budget_tokens"], 0)
        self.assertTrue(completion["echo_append"])
        self.assertFalse(completion["echo_cached_tool_header"])
        self.assertNotIn("You played esports football", json.dumps(completion))

    def test_endpoints_are_derived_from_snapshot_and_remote_or_unapproved_ports_never_contacted(self):
        cases = [{"baseUrl": "http://example.com:8811"}, {"baseUrl": "http://localhost:8811"},
                 {"baseUrl": "http://127.0.0.1:1"}, {"backendPort": 1}, {"backendPort": True},
                 {"baseUrl": f"http://user:password@127.0.0.1:{self.port}"},
                 {"baseUrl": f"http://127.0.0.1:{self.port}/elsewhere"}]
        for extra in cases:
            with self.subTest(extra=extra):
                request = copy.deepcopy(self.request)
                request["runtime"].update(extra)
                result = self.repair.recover(request)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["candidates"], [])
        self.assertEqual(self.server.posts, [])

    def test_disabled_memory_and_temporary_chat_fail_closed_before_inference(self):
        for request in [{**self.request, "temporaryChat": True}, {k: v for k, v in self.request.items() if k != "temporaryChat"}]:
            self.assertEqual(self.repair.recover(request)["candidates"], [])
        self.mutate(self.control, "UPDATE settings SET value=?", ('{"memoryEnabled":false}',))
        self.assertEqual(self.repair.recover(self.request)["code"], "memory_disabled")
        self.assertEqual(self.server.posts, [])

    def test_only_failed_unsaved_native_capture_of_an_actual_user_entry_is_recoverable(self):
        self.mutate(self.platform, "UPDATE memory_auto_captures SET status='complete'")
        self.assertEqual(self.repair.recover(self.request)["code"], "capture_not_failed")
        self.mutate(self.platform, "UPDATE memory_auto_captures SET status='failed'")
        self.mutate(self.control, "UPDATE timeline SET role='assistant' WHERE id=7")
        self.assertEqual(self.repair.recover(self.request)["code"], "human_source_unavailable")
        self.assertEqual(self.server.posts, [])

    def test_skipped_and_deferred_unsaved_captures_are_reviewed_again_from_exact_human_source(self):
        for status in ('unchanged','deferred'):
            self.mutate(self.platform,'UPDATE memory_auto_captures SET status=?',(status,))
            result=self.repair.recover(self.request)
            self.assertEqual(result['status'],'ready')
            self.assertEqual(result['candidates'][0]['recordArgs']['content'],SOURCE)

    def test_assistant_inventions_and_paraphrases_are_rejected_even_with_valid_json(self):
        for quote in ["You played esports football.", "I played professional football a year ago"]:
            with self.subTest(quote=quote):
                response = copy.deepcopy(GOOD)
                response["memories"][0]["quote"] = quote
                self.server.responses = [response, response]
                result = self.repair.recover(self.request)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["code"], "invalid_candidate")
                self.assertEqual(result["candidates"], [])

    def test_deleted_key_and_deleted_content_cannot_be_recreated_with_an_alias(self):
        for key, content_hash in [("personal.professional-football", "unrelated"),
                                  ("deleted.alias", hashlib.sha256(SOURCE.encode()).hexdigest())]:
            with self.subTest(key=key):
                self.mutate(self.platform, "DELETE FROM memory_tombstones")
                self.mutate(self.platform, "INSERT INTO memory_tombstones VALUES('global','fact',?,?,'now')", (key, content_hash))
                result = self.repair.recover(self.request)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["candidates"], [])

    def test_unrelated_source_tombstone_does_not_disable_all_global_memory_recovery(self):
        self.mutate(self.platform, "INSERT INTO memory_source_tombstones VALUES(?,'global','now')",
                    (hashlib.sha256(b"opencore:other\x00123").hexdigest(),))
        self.assertEqual(self.repair.recover(self.request)["status"], "ready")

    def test_exact_native_conversation_and_entry_hash_blocks_deleted_source_before_inference(self):
        self.mutate(self.platform, "INSERT INTO memory_source_tombstones VALUES(?,'global','now')",
                    (hashlib.sha256(b"opencore:football\x007").hexdigest(),))
        result = self.repair.recover(self.request)
        self.assertEqual(result["code"], "source_tombstoned")
        self.assertEqual(self.server.posts, [])

    def test_receipts_require_active_native_records_with_unchanged_exact_human_source(self):
        self.assertEqual(self.repair.receipts(CHAT), {"conversationId": CHAT, "receipts": {}})
        result = self.repair.recover(self.request)
        args = result["candidates"][0]["recordArgs"]
        self.mutate(self.platform, "INSERT INTO memories VALUES(?,?,?,?,?,?,?,'active')",
                    ("saved-id", args["key"], args["kind"], args["scope"], args["content"], args["source"], args["evidence"]))
        receipt = self.repair.receipts(CHAT)["receipts"]["7"]
        self.assertEqual(receipt["memoryIds"], ["saved-id"])
        self.assertEqual(receipt["savedCount"], 1)
        self.assertEqual(receipt["status"], "saved")
        self.mutate(self.control, "UPDATE timeline SET content='Edited human source' WHERE id=7")
        self.assertEqual(self.repair.receipts(CHAT)["receipts"], {})

    def test_recovery_receipt_cannot_be_fabricated_from_assistant_text_or_wrong_evidence(self):
        self.mutate(self.platform, "INSERT INTO memories VALUES('fake','fake','fact','global',?,'chat:opencore:football:user-entry:7',?,'active')",
                    ("You played esports football.", '{"automatic":true,"conversationId":"opencore:football","userEntryId":7,"quote":"You played esports football.","recovery":"local-constrained-json-v1"}'))
        self.assertEqual(self.repair.receipts(CHAT)["receipts"], {})

    def test_source_deletion_and_settings_changes_during_inference_discard_candidates(self):
        self.server.on_completion = lambda: self.mutate(self.control, "DELETE FROM timeline WHERE id=7")
        result = self.repair.recover(self.request)
        self.assertEqual(result["code"], "human_source_unavailable")
        self.assertEqual(result["candidates"], [])

    def test_source_hash_and_tombstones_are_rechecked_before_native_record(self):
        result = self.repair.recover(self.request)
        self.assertEqual(self.repair.revalidate(result)["status"], "ready")
        self.mutate(self.platform, "INSERT INTO memory_tombstones VALUES('global','fact',?,'other','now')", ("personal.professional-football",))
        self.assertEqual(self.repair.revalidate(result)["candidates"], [])
        self.mutate(self.platform, "DELETE FROM memory_tombstones")
        self.mutate(self.control, "UPDATE timeline SET content=? WHERE id=7", (SOURCE + " changed",))
        self.assertEqual(self.repair.revalidate(result)["code"], "source_changed")

    def test_recovered_facts_keep_existing_records_and_duplicates_are_not_resaved(self):
        self.mutate(self.platform, "INSERT INTO memories VALUES('m','personal.professional-football','fact','global','I played professional football.','chat:older',NULL,'active')")
        self.assertTrue(self.repair.recover(self.request)["candidates"][0]["recordArgs"]["key"].startswith("chat.fact."))
        self.mutate(self.platform, "UPDATE memories SET content=?", (SOURCE,))
        result = self.repair.recover(self.request)
        self.assertEqual(result["status"], "already_recorded")
        self.assertEqual(result["candidates"], [])

    def test_scope_changes_for_an_existing_key_and_project_scope_without_project_are_rejected(self):
        self.mutate(self.platform, "INSERT INTO memories VALUES('m','personal.professional-football','fact','global','older','chat:older',NULL,'active')")
        for scope in ["conversation", "project"]:
            with self.subTest(scope=scope):
                response = copy.deepcopy(GOOD)
                response["memories"][0]["scope"] = scope
                self.server.responses = [response, response]
                self.assertEqual(self.repair.recover(self.request)["code"], "invalid_candidate")

    def test_unrestored_cache_never_returns_candidates_and_invalidates_lease(self):
        self.server.restored = False
        result = self.repair.recover(self.request)
        self.assertEqual(result["code"], "cache_restore_unverified")
        self.assertEqual(result["candidates"], [])
        release = [body for path, body in self.server.posts if path == "/echo/kv/lease" and body.get("action") == "release"]
        self.assertTrue(release[-1]["invalidate"])

    def test_busy_backend_does_not_generate(self):
        self.server.processing=True
        result=self.repair.recover(self.request)
        self.assertEqual(result['code'],'backend_busy')
        self.assertEqual(result['candidates'],[])
        self.assertFalse(any(path == "/v1/chat/completions" for path, _ in self.server.posts))

    def test_runtime_without_cache_branch_uses_exclusive_transcript_reset(self):
        self.server.branch_supported=False
        result=self.repair.recover(self.request)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['diagnostics']['cacheMode'],'transcript-reset')
        self.assertTrue(result['diagnostics']['cacheReset'])
        completion=next(body for path,body in self.server.posts if path=='/v1/chat/completions')
        self.assertFalse(completion['echo_append']);self.assertTrue(completion['echo_reset'])
        self.assertNotIn('echo_memory_branch',completion)
        self.assertNotIn('You played esports football.',json.dumps(completion))
        self.assertTrue(self.server.posts[-1][1]['invalidate'])

    def test_cold_empty_slot_uses_standalone_schema_request_and_requires_next_turn_reset(self):
        self.server.slot_session = ""
        self.server.slot_session_tokens = self.server.slot_prompt_tokens = 0
        self.server.branch_supported = False
        result = self.repair.recover(self.request)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["diagnostics"]["cacheMode"], "cold-start-reset")
        self.assertTrue(result["diagnostics"]["cacheReset"])
        self.assertFalse(result["diagnostics"]["cacheRestored"])
        completion = next(body for path, body in self.server.posts if path == "/v1/chat/completions")
        self.assertFalse(completion["echo_append"])
        self.assertTrue(completion["echo_reset"])
        self.assertNotIn("echo_memory_branch", completion)
        release = [body for path, body in self.server.posts if path == "/echo/kv/lease" and body.get("action") == "release"][-1]
        self.assertTrue(release["invalidate"])

    def test_cold_extraction_never_returns_candidates_without_verified_echo_reset(self):
        self.server.slot_session = ""
        self.server.slot_session_tokens = self.server.slot_prompt_tokens = 0
        self.server.invalidation_verified = False
        result = self.repair.recover(self.request)
        self.assertEqual(result["code"], "cache_reset_unverified")
        self.assertEqual(result["candidates"], [])

    def test_other_chats_occupied_cache_is_never_treated_as_an_empty_slot(self):
        self.server.slot_session = "another-chat"
        result = self.repair.recover(self.request)
        self.assertEqual(result["code"], "cache_owner_mismatch")
        self.assertFalse(any(path == "/v1/chat/completions" for path, _ in self.server.posts))

    def test_bundled_initial_slot_shape_is_cold_but_partial_or_unknown_state_is_not(self):
        initial = {"id": 0, "n_ctx": 32768, "is_processing": False,
                   "echo_memory_branch_supported": True, "echo_memory_branch_version": 1,
                   "echo_memory_branch_receipt": None}
        self.assertTrue(MemoryRepair._cold_slot(initial))
        for change in ({"echo_session_id": "another-chat"}, {"echo_session_tokens": 12},
                       {"n_prompt_tokens": 10}, {"is_processing": True},
                       {"echo_memory_branch_version": 2}, {"echo_memory_branch_receipt": {"restored": True}}):
            self.assertFalse(MemoryRepair._cold_slot({**initial, **change}), change)
        self.assertFalse(MemoryRepair._cold_slot({"is_processing": False}))

    def test_grammar_quotes_the_actual_human_statement_including_emoji(self):
        self.repair.recover(self.request)
        body = next(body for path, body in self.server.posts if path == "/v1/chat/completions")
        quote = body["response_format"]["schema"]["properties"]["memories"]["items"]["properties"]["quote"]
        self.assertEqual(quote, {"type": "string", "enum": [SOURCE]})

    def test_recovery_never_overwrites_an_existing_memory_with_a_misclassified_key(self):
        self.mutate(self.platform, "INSERT INTO memories VALUES('name','user.name','fact','global','My name is Itamar','original','','active')")
        response = copy.deepcopy(GOOD)
        response["memories"][0]["key"] = "user.name"
        self.server.responses = [response]
        result = self.repair.recover(self.request)
        self.assertEqual(result["status"], "ready")
        args = result["candidates"][0]["recordArgs"]
        self.assertTrue(args["key"].startswith("chat.fact."))
        self.assertNotEqual(args["key"], "user.name")
        self.assertEqual(args["content"], SOURCE)

    def test_cold_retry_can_reset_only_its_own_verified_discarded_session(self):
        self.server.slot_session = ""
        self.server.slot_session_tokens = self.server.slot_prompt_tokens = 0
        self.assertEqual(self.repair.recover(self.request)["status"], "ready")
        self.assertEqual(self.repair.recover(self.request)["status"], "ready")
        self.server.slot_session = "another-chat"
        self.assertEqual(self.repair.recover(self.request)["code"], "cache_owner_mismatch")

    def test_malformed_json_retries_once_and_reports_failure_without_fabricated_empty_result(self):
        self.server.responses = ["I think this is useful.", "still not JSON"]
        result = self.repair.recover(self.request)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "invalid_json")
        self.assertEqual(result["diagnostics"]["attempts"], 2)
        self.assertEqual(len([1 for path, _ in self.server.posts if path == "/v1/chat/completions"]), 2)

    def test_schema_rejection_never_falls_back_to_unconstrained_generation(self):
        self.server.http_error = 400
        result = self.repair.recover(self.request)
        self.assertEqual(result["code"], "schema_request_rejected")
        self.assertEqual(len([1 for path, _ in self.server.posts if path == "/v1/chat/completions"]), 1)

    def test_redirects_are_not_followed_and_authorization_is_not_returned(self):
        self.server.redirect = f"http://localhost:{self.port}/elsewhere"
        request = copy.deepcopy(self.request)
        request["runtime"]["authorization"] = "Bearer fixture-secret"
        result = self.repair.recover(request)
        self.assertEqual(result["status"], "error")
        self.assertNotIn("fixture-secret", json.dumps(result))

    def test_empty_model_result_is_explicit_and_has_completion_evidence(self):
        self.server.responses = [{"memories": []}]
        result = self.repair.recover(self.request)
        self.assertEqual(result["status"], "no_facts")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["diagnostics"]["finishReason"], "stop")
        self.assertIn("empty", result["message"].lower())


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, value, code=200):
        raw = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        receipt = None if not self.server.branch else {"id": self.server.branch, "started": True, "restored": self.server.restored}
        self.send([{"id": 0, "echo_memory_branch_supported": self.server.branch_supported,
                    "echo_session_id": self.server.slot_session, "echo_session_tokens": self.server.slot_session_tokens,
                    "n_prompt_tokens": self.server.slot_prompt_tokens,
                    "is_processing": self.server.processing, "echo_memory_branch_receipt": receipt}])

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.posts.append((self.path, body))
        if self.path == "/echo/kv/lease":
            return self.send({"acquired": True, "nonce": "fixture-lease"} if body["action"] == "acquire" else
                {"released": True, "invalidation": {"invalidated": self.server.invalidation_verified, "nextTurn": "full-transcript-reset"}})
        if self.server.redirect:
            self.send_response(302)
            self.send_header("Location", self.server.redirect)
            self.end_headers()
            return
        if self.server.http_error:
            return self.send({"error": "Unsupported schema"}, self.server.http_error)
        self.server.branch = body.get("echo_memory_branch")
        if not self.server.branch:
            self.server.slot_session = body["echo_session_id"]
            self.server.slot_session_tokens = 512
            self.server.slot_prompt_tokens = 500
        if self.server.on_completion:
            self.server.on_completion()
        value = self.server.responses.pop(0) if len(self.server.responses) > 1 else self.server.responses[0]
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        self.send({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content, "reasoning_content": ""}}],
                   "usage": {"completion_tokens": 48, "completion_tokens_details": {"reasoning_tokens": 0}}})


if __name__ == "__main__":
    unittest.main()
