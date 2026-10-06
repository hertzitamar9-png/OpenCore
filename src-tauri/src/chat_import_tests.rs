use super::*;
use serde_json::json;
use std::cell::Cell;

struct Fixture {
    root: PathBuf,
    store: Option<EventStore>,
}
impl Fixture {
    fn new() -> Self {
        let root =
            std::env::temp_dir().join(format!("opencore-import-test-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let store = EventStore::open(&root.join("opencore.sqlite3")).unwrap();
        Self {
            root,
            store: Some(store),
        }
    }
    fn store(&self) -> &EventStore {
        self.store.as_ref().unwrap()
    }
    fn json(&self, name: &str, value: &Value) -> PathBuf {
        let path = self.root.join(name);
        fs::write(&path, serde_json::to_vec(value).unwrap()).unwrap();
        path
    }
    fn lines(&self, name: &str, lines: &[Value]) -> PathBuf {
        let path = self.root.join(name);
        fs::write(
            &path,
            lines
                .iter()
                .map(Value::to_string)
                .collect::<Vec<_>>()
                .join("\n"),
        )
        .unwrap();
        path
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        drop(self.store.take());
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn copied_id(report: &ImportReport) -> &str {
    &report.conversations[0].conversation_id
}

#[test]
fn generic_json_keeps_messages_reasoning_tools_metadata_and_source_bytes() {
    let fixture = Fixture::new();
    let source = json!({"id":"source-chat", "title":"Portable chat", "model":"test-model", "messages":[
        {"id":"u", "role":"user", "content":"Read a file", "timestamp":"2026-01-01T10:00:00+02:00", "metadata":{"custom":42}},
        {"id":"a", "role":"assistant", "content":"Done", "timestamp":1767254401.5, "reasoning":"Check the file first",
         "tool_calls":[{"id":"call-1","type":"function","function":{"name":"read_file","arguments":"{\"path\":\"readme.md\"}"}}]},
        {"id":"t", "role":"tool", "content":"File contents", "tool_call_id":"call-1", "name":"read_file", "timestamp":1767254402.0}
    ]});
    let path = fixture.json("generic.json", &source);
    let bytes = fs::read(&path).unwrap();
    let report = import_file(fixture.store(), &path, "auto").unwrap();
    assert_eq!(report.source_format, "generic");
    assert_eq!(report.imported, 1);
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows.len(), 5);
    assert!(rows
        .iter()
        .any(|row| row.kind == "thinking" && row.content == "Check the file first"));
    assert!(rows
        .iter()
        .any(|row| row.kind == "tool_call" && row.title == "read_file"));
    assert!(rows
        .iter()
        .any(|row| row.kind == "tool_result"
            && row.metadata["sourceRecord"]["tool_call_id"] == "call-1"));
    assert_eq!(rows[0].metadata["sourceRecord"]["metadata"]["custom"], 42);
    assert_eq!(
        rows[0].metadata["portableImport"]["originalTimestamp"],
        "2026-01-01T10:00:00+02:00"
    );
    assert_eq!(fs::read(&path).unwrap(), bytes);
}

#[test]
fn opencore_legacy_and_versioned_exports_preserve_fields_without_overwriting_native_id() {
    let fixture = Fixture::new();
    fixture
        .store()
        .ensure_conversation("native-chat", "OpenCore", "echo", "Native title")
        .unwrap();
    fixture
        .store()
        .add_timeline(
            "native-chat",
            "message",
            "assistant",
            "OpenCore",
            "Assistant",
            "Keep native",
            &json!({}),
        )
        .unwrap();
    let source = json!({"conversation_id":"native-chat", "format":"opencore-chat", "version":1,
        "conversation":{"id":"native-chat","title":"Export title","client":"OpenCore","createdAt":"2025-12-01T00:00:00Z"},
        "entries":[{"id":42,"conversationId":"native-chat","timestamp":"2026-01-02T01:02:03Z","kind":"message","role":"user",
                    "source":"OpenCore","title":"Question","content":"Preserved content","metadata":{"custom":[1,2],"safe":true}}]});
    let path = fixture.json("opencore.json", &source);
    let report = import_file(fixture.store(), &path, "opencore").unwrap();
    assert_ne!(copied_id(&report), "native-chat");
    assert_eq!(
        fixture.store().conversation("native-chat").unwrap()[0].content,
        "Keep native"
    );
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows[0].source, "Imported OpenCore");
    assert_eq!(rows[0].title, "Question");
    assert_eq!(rows[0].metadata["custom"], json!([1, 2]));
    assert_eq!(
        rows[0].metadata["portableImport"]["originalSource"],
        "OpenCore"
    );
    assert_eq!(
        rows[0].metadata["portableImport"]["sourceConversation"]["createdAt"],
        "2025-12-01T00:00:00Z"
    );
    assert_eq!(report.conversations[0].title, "Export title");

    let legacy = fixture.json(
        "legacy.json",
        &json!({"conversation_id":"legacy","entries":source["entries"]}),
    );
    // The entries must belong to the declared conversation; a mismatched export is malformed.
    let rejected = import_file(fixture.store(), &legacy, "opencore").unwrap();
    assert_eq!(rejected.imported, 0);
    assert_eq!(rejected.conversations[0].status, "failed");
}

#[test]
fn unmodified_reimport_is_skipped_and_native_continuations_survive_changed_source() {
    let fixture = Fixture::new();
    let mut source =
        json!({"id":"growing","messages":[{"id":"1","role":"user","content":"Original"}]});
    let path = fixture.json("chat.json", &source);
    let first = import_file(fixture.store(), &path, "generic").unwrap();
    let id = copied_id(&first).to_string();
    let original = fixture.store().conversation(&id).unwrap();
    fixture
        .store()
        .add_timeline(
            &id,
            "message",
            "assistant",
            "OpenCore",
            "Assistant",
            "Native continuation",
            &json!({"native":true}),
        )
        .unwrap();
    let repeated = import_file(fixture.store(), &path, "generic").unwrap();
    assert_eq!(
        (repeated.imported, repeated.updated, repeated.skipped),
        (0, 0, 1)
    );
    assert_eq!(
        fixture.store().conversation(&id).unwrap()[0].id,
        original[0].id
    );
    source["messages"]
        .as_array_mut()
        .unwrap()
        .push(json!({"id":"2","role":"assistant","content":"Added at source"}));
    fs::write(&path, source.to_string()).unwrap();
    let changed = import_file(fixture.store(), &path, "generic").unwrap();
    assert_eq!(
        (changed.imported, changed.updated, changed.skipped),
        (0, 1, 0)
    );
    let rows = fixture.store().conversation(&id).unwrap();
    assert_eq!(rows.len(), 3);
    assert!(rows
        .iter()
        .any(|row| row.content == "Native continuation" && row.metadata["native"] == true));
    let old_event = &original[0].metadata["portableImport"]["eventId"];
    assert!(rows
        .iter()
        .any(|row| &row.metadata["portableImport"]["eventId"] == old_event));
}

#[test]
fn copied_file_path_does_not_change_identity() {
    let fixture = Fixture::new();
    let value = json!([{"role":"user","content":"No explicit session id"}]);
    let one = fixture.json("one.json", &value);
    let two = fixture.json("two.json", &value);
    let first = import_file(fixture.store(), &one, "generic").unwrap();
    let second = import_file(fixture.store(), &two, "generic").unwrap();
    assert_eq!(copied_id(&first), copied_id(&second));
    assert_eq!(second.skipped, 1);
}

#[test]
fn existing_unclaimed_import_namespace_is_never_overwritten() {
    let fixture = Fixture::new();
    let path = fixture.json(
        "chat.json",
        &json!({"id":"occupied","messages":[{"role":"user","content":"Source"}]}),
    );
    let candidates = prepare_file(&path, "generic", &|| false).unwrap();
    let id = candidates.conversations[0].conversation_id.clone();
    fixture
        .store()
        .ensure_conversation(&id, "OpenCore", "echo", "Owned by native user")
        .unwrap();
    fixture
        .store()
        .add_timeline(
            &id,
            "message",
            "user",
            "OpenCore",
            "User",
            "Must survive",
            &json!({}),
        )
        .unwrap();
    let result = import_file(fixture.store(), &path, "generic").unwrap();
    assert_eq!(result.imported, 0);
    assert_eq!((result.failed, result.skipped), (1, 0));
    assert_eq!(result.conversations[0].status, "failed");
    assert_eq!(
        fixture.store().conversation(&id).unwrap()[0].content,
        "Must survive"
    );
    let key = format!("portable_chat_import_v1_{id}");
    fixture
        .store()
        .set_setting(
            &key,
            &digest(&serde_json::to_vec(&candidates.conversations[0].rows).unwrap()),
        )
        .unwrap();
    assert_eq!(
        import_file(fixture.store(), &path, "generic")
            .unwrap()
            .failed,
        1
    );
    assert_eq!(
        fixture.store().conversation(&id).unwrap()[0].content,
        "Must survive"
    );
}

#[test]
fn receiptless_legacy_copy_recovers_and_updates_without_losing_native_continuations() {
    let fixture = Fixture::new();
    let mut source = json!({"id":"recoverable","messages":[{"id":"original","role":"user","content":"Original"}]});
    let path = fixture.json("recoverable.json", &source);
    let first = import_file(fixture.store(), &path, "generic").unwrap();
    let id = copied_id(&first).to_string();
    let key = format!("portable_chat_import_v1_{id}");
    let old_rows = fixture.store().conversation(&id).unwrap();
    let db = Connection::open(fixture.root.join("opencore.sqlite3")).unwrap();
    db.execute("DELETE FROM settings WHERE key=?1", [&key])
        .unwrap();
    // Simulate an older committed copy: its stored event hash/provenance exists,
    // but neither the new copyId field nor the separately written receipt does.
    for row in old_rows {
        let mut metadata = row.metadata;
        metadata["portableImport"]
            .as_object_mut()
            .unwrap()
            .remove("copyId");
        db.execute(
            "UPDATE timeline SET metadata=?1 WHERE id=?2",
            rusqlite::params![metadata.to_string(), row.id],
        )
        .unwrap();
    }
    drop(db);
    fixture
        .store()
        .add_timeline(
            &id,
            "message",
            "assistant",
            "OpenCore",
            "Assistant",
            "Native continuation",
            &json!({"native":true}),
        )
        .unwrap();
    source["messages"]
        .as_array_mut()
        .unwrap()
        .push(json!({"id":"new","role":"assistant","content":"Source continuation"}));
    fs::write(&path, source.to_string()).unwrap();
    let bytes = fs::read(&path).unwrap();
    let recovered = import_file(fixture.store(), &path, "generic").unwrap();
    assert_eq!(
        (
            recovered.imported,
            recovered.updated,
            recovered.failed,
            recovered.skipped
        ),
        (0, 1, 0, 0)
    );
    assert!(fixture.store().get_setting(&key).unwrap().is_some());
    let rows = fixture.store().conversation(&id).unwrap();
    assert_eq!(rows.len(), 3);
    assert!(rows
        .iter()
        .any(|row| row.content == "Native continuation" && row.metadata["native"] == true));
    assert!(rows.iter().any(|row| row.content == "Source continuation"
        && row.metadata["portableImport"]["copyId"] == id));
    assert_eq!(
        import_file(fixture.store(), &path, "generic")
            .unwrap()
            .skipped,
        1
    );
    assert_eq!(fs::read(&path).unwrap(), bytes);
}

#[test]
fn an_import_label_or_receipt_cannot_claim_fake_or_mismatched_copy_provenance() {
    for mismatch in [
        "missing",
        "format",
        "source_id",
        "copy_id",
        "event_id",
        "event_hash",
        "source",
        "extra_row",
    ] {
        let fixture = Fixture::new();
        let path = fixture.json(
            "occupied.json",
            &json!({"id":"fake-copy","messages":[{"id":"1","role":"user","content":"Source"}]}),
        );
        let mut prepared = prepare_file(&path, "generic", &|| false).unwrap();
        let candidate = prepared.conversations.remove(0);
        let id = candidate.conversation_id;
        fixture
            .store()
            .replace_imported_history(&id, "Imported JSON", "Pretend copy", &candidate.rows)
            .unwrap();
        let rows = fixture.store().conversation(&id).unwrap();
        let mut metadata = rows[0].metadata.clone();
        match mismatch {
            "missing" => {
                metadata.as_object_mut().unwrap().remove("portableImport");
            }
            "format" => metadata["portableImport"]["format"] = json!("hermes"),
            "source_id" => {
                metadata["portableImport"]["sourceConversationId"] = json!("different source")
            }
            "copy_id" => {
                metadata["portableImport"]["copyId"] =
                    json!(copy_id("generic", "another namespace"))
            }
            "event_id" => metadata["portableImport"]["eventId"] = json!("invalid"),
            "event_hash" => {
                metadata["opencore_source_event_id"] = json!(digest(b"another namespace"))
            }
            _ => {}
        }
        let db = Connection::open(fixture.root.join("opencore.sqlite3")).unwrap();
        db.execute(
            "UPDATE timeline SET metadata=?1 WHERE id=?2",
            rusqlite::params![metadata.to_string(), rows[0].id],
        )
        .unwrap();
        if mismatch == "source" {
            db.execute(
                "UPDATE timeline SET source='Imported Hermes' WHERE id=?1",
                [rows[0].id],
            )
            .unwrap();
        }
        drop(db);
        if mismatch == "extra_row" {
            fixture
                .store()
                .add_timeline(
                    &id,
                    "message",
                    "user",
                    "Imported JSON",
                    "Unowned",
                    "Must survive",
                    &json!({}),
                )
                .unwrap();
        }
        let before = serde_json::to_value(fixture.store().conversation(&id).unwrap()).unwrap();
        let without_receipt = import_file(fixture.store(), &path, "generic").unwrap();
        assert_eq!(
            (without_receipt.skipped, without_receipt.failed),
            (0, 1),
            "{mismatch}"
        );
        assert_eq!(
            serde_json::to_value(fixture.store().conversation(&id).unwrap()).unwrap(),
            before,
            "{mismatch}"
        );
        // Even a stale receipt matching the incoming fingerprint cannot bypass
        // the ownership checks or convert the failure to an already-copied result.
        let key = format!("portable_chat_import_v1_{id}");
        fixture
            .store()
            .set_setting(&key, &digest(&serde_json::to_vec(&candidate.rows).unwrap()))
            .unwrap();
        let report = import_file(fixture.store(), &path, "generic").unwrap();
        assert_eq!(
            (
                report.imported,
                report.updated,
                report.skipped,
                report.failed
            ),
            (0, 0, 0, 1),
            "{mismatch}"
        );
        assert_eq!(
            serde_json::to_value(fixture.store().conversation(&id).unwrap()).unwrap(),
            before,
            "{mismatch}"
        );
    }
}

#[test]
fn receipt_write_failure_rolls_back_new_copies_and_updates_atomically() {
    let fixture = Fixture::new();
    let mut source =
        json!({"id":"atomic-copy","messages":[{"id":"1","role":"user","content":"Original"}]});
    let path = fixture.json("atomic.json", &source);
    let key = format!(
        "portable_chat_import_v1_{}",
        copy_id("generic", "atomic-copy")
    );
    let db = Connection::open(fixture.root.join("opencore.sqlite3")).unwrap();
    let trigger = "CREATE TRIGGER reject_import_receipt BEFORE INSERT ON settings WHEN NEW.key LIKE 'portable_chat_import_v1_%' BEGIN SELECT RAISE(ABORT,'receipt fixture failure'); END;";
    db.execute_batch(trigger).unwrap();
    let failed = import_file(fixture.store(), &path, "generic").unwrap();
    assert_eq!((failed.imported, failed.failed), (0, 1));
    assert!(failed.conversations[0]
        .error
        .as_deref()
        .unwrap()
        .contains("no history was changed"));
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
    assert!(fixture.store().get_setting(&key).unwrap().is_none());
    db.execute_batch("DROP TRIGGER reject_import_receipt;")
        .unwrap();
    let first = import_file(fixture.store(), &path, "generic").unwrap();
    let id = copied_id(&first);
    fixture
        .store()
        .add_timeline(
            id,
            "message",
            "assistant",
            "OpenCore",
            "Assistant",
            "Native continuation",
            &json!({"native":true}),
        )
        .unwrap();
    let before = serde_json::to_value(fixture.store().conversation(id).unwrap()).unwrap();
    let receipt = fixture.store().get_setting(&key).unwrap();
    source["messages"]
        .as_array_mut()
        .unwrap()
        .push(json!({"id":"2","role":"assistant","content":"New source turn"}));
    fs::write(&path, source.to_string()).unwrap();
    db.execute_batch(trigger).unwrap();
    let failed_update = import_file(fixture.store(), &path, "generic").unwrap();
    assert_eq!((failed_update.updated, failed_update.failed), (0, 1));
    assert_eq!(
        serde_json::to_value(fixture.store().conversation(id).unwrap()).unwrap(),
        before
    );
    assert_eq!(fixture.store().get_setting(&key).unwrap(), receipt);
    db.execute_batch("DROP TRIGGER reject_import_receipt;")
        .unwrap();
    assert_eq!(
        import_file(fixture.store(), &path, "generic")
            .unwrap()
            .updated,
        1
    );
}

#[test]
fn explicit_duplicate_events_deduplicate_but_conflicting_duplicate_aborts_the_conversation() {
    let fixture = Fixture::new();
    let event = json!({"id":"event-1","role":"user","content":"Once"});
    let path = fixture.json(
        "duplicate.json",
        &json!({"id":"duplicates","messages":[event.clone(),event]}),
    );
    let report = import_file(fixture.store(), &path, "generic").unwrap();
    assert_eq!(
        fixture
            .store()
            .conversation(copied_id(&report))
            .unwrap()
            .len(),
        1
    );
    assert!(!report.conversations[0].warnings.is_empty());
    fs::write(&path, json!({"id":"duplicates","messages":[
        {"id":"event-1","role":"user","content":"Once"}, {"id":"event-1","role":"user","content":"Different"}
    ]}).to_string()).unwrap();
    let conflict = import_file(fixture.store(), &path, "generic").unwrap();
    assert_eq!(conflict.conversations[0].status, "failed");
    assert_eq!(
        fixture.store().conversation(copied_id(&report)).unwrap()[0].content,
        "Once"
    );
}

#[test]
fn repeated_text_without_source_ids_remains_a_real_repeated_turn() {
    let fixture = Fixture::new();
    let event = json!({"role":"user","content":"Again"});
    let path = fixture.json("repeated.json", &json!([event.clone(), event]));
    let report = import_file(fixture.store(), &path, "auto").unwrap();
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows.len(), 2);
    assert_ne!(
        rows[0].metadata["portableImport"]["eventId"],
        rows[1].metadata["portableImport"]["eventId"]
    );
}

#[test]
fn malformed_message_is_reported_without_half_importing_a_conversation() {
    let fixture = Fixture::new();
    let path = fixture.json(
        "malformed.json",
        &json!({"messages":[{"role":"user","content":"Valid"},{"content":"Missing role"}]}),
    );
    let report = import_file(fixture.store(), &path, "generic").unwrap();
    assert_eq!(report.imported, 0);
    assert_eq!(report.conversations[0].status, "failed");
    assert!(report.conversations[0]
        .error
        .as_deref()
        .unwrap()
        .contains("role"));
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
}

#[test]
fn codex_rollout_preserves_messages_reasoning_and_both_tool_protocols() {
    let fixture = Fixture::new();
    let path = fixture.lines("codex.jsonl", &[
        json!({"type":"session_meta","payload":{"id":"codex-thread","cwd":"C:/project"}}),
        json!({"type":"response_item","timestamp":"2026-02-01T00:00:00Z","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Please investigate"}]}}),
        json!({"type":"response_item","timestamp":"2026-02-01T00:00:01Z","payload":{"type":"reasoning","summary":[{"type":"summary_text","text":"Check the source"}],"encrypted_content":"opaque-reasoning"}}),
        json!({"type":"response_item","payload":{"type":"function_call","call_id":"c1","name":"exec","arguments":"{\"command\":\"echo hello\"}"}}),
        json!({"type":"response_item","payload":{"type":"function_call_output","call_id":"c1","output":"hello"}}),
        json!({"type":"response_item","payload":{"type":"custom_tool_call","call_id":"c2","name":"apply_patch","input":"a patch"}}),
        json!({"type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"c2","output":"applied"}}),
        json!({"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Answer"}]}}),
        json!({"type":"event_msg","payload":{"type":"agent_message","message":"Answer"}}),
    ]);
    let bytes = fs::read(&path).unwrap();
    let report = import_file(fixture.store(), &path, "auto").unwrap();
    assert_eq!(report.source_format, "codex");
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows.len(), 7);
    assert_eq!(rows.iter().filter(|row| row.kind == "tool_call").count(), 2);
    assert!(rows
        .iter()
        .any(|row| row.kind == "thinking" && row.content == "Check the source"));
    assert!(rows.iter().any(
        |row| row.metadata["sourceRecord"]["payload"]["encrypted_content"] == "opaque-reasoning"
    ));
    assert_eq!(rows.iter().filter(|row| row.content == "Answer").count(), 1);
    assert_eq!(fs::read(path).unwrap(), bytes);
}

#[test]
fn claude_rollout_keeps_tool_pairing_and_nontext_blocks_as_data() {
    let fixture = Fixture::new();
    let path = fixture.lines("claude.jsonl", &[
        json!({"type":"user","uuid":"u","sessionId":"claude-session","timestamp":"2026-02-01T00:00:00Z","message":{"role":"user","content":"Read a file"}}),
        json!({"type":"assistant","uuid":"a","sessionId":"claude-session","message":{"role":"assistant","content":[
            {"type":"thinking","thinking":"Plan carefully","signature":"preserved"},
            {"type":"tool_use","id":"t1","name":"Read","input":{"path":"notes.md"}},
            {"type":"text","text":"I will read it"},
            {"type":"image","source":{"type":"url","url":"https://example.invalid/image.png"}}
        ]}}),
        json!({"type":"user","uuid":"t","sessionId":"claude-session","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"Contents"}]}}),
        json!({"type":"custom-title","sessionId":"claude-session","customTitle":"Selected chat"}),
    ]);
    let report = import_file(fixture.store(), &path, "claude").unwrap();
    assert_eq!(report.conversations[0].title, "Selected chat");
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows.len(), 6);
    assert!(rows
        .iter()
        .any(|row| row.kind == "thinking" && row.content == "Plan carefully"));
    assert!(rows.iter().any(|row| row.kind == "tool_result"
        && row.metadata["sourceRecord"]["message"]["content"][0]["tool_use_id"] == "t1"));
    assert!(rows
        .iter()
        .any(|row| row.content.contains("example.invalid")));
}

#[test]
fn malformed_rollout_line_aborts_before_any_persistence() {
    let fixture = Fixture::new();
    let path = fixture.root.join("invalid.jsonl");
    fs::write(
        &path,
        "{\"type\":\"session_meta\",\"payload\":{\"id\":\"s\"}}\n{broken}\n",
    )
    .unwrap();
    let error = import_file(fixture.store(), &path, "codex").unwrap_err();
    assert!(error.contains("line 2"));
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
}

#[test]
fn hermes_jsonl_session_exports_are_individually_atomic_with_clear_failures() {
    let fixture = Fixture::new();
    let path = fixture.root.join("hermes.jsonl");
    fs::write(&path,format!("{}\n{{bad json}}\n{}\n",
        json!({"id":"h1","source":"cli","started_at":1760000000,"title":"Good","messages":[{"role":"user","content":"Hello"}]}),
        json!({"id":"h2","source":"cli","started_at":1760000010,"messages":[{"content":"Missing role"}]}))).unwrap();
    let report = import_file(fixture.store(), &path, "auto").unwrap();
    assert_eq!(report.source_format, "hermes");
    assert_eq!(
        (report.imported, report.skipped, report.failed, report.total),
        (1, 0, 2, 3)
    );
    assert_eq!(
        report
            .conversations
            .iter()
            .filter(|item| item.status == "failed")
            .count(),
        2
    );
    assert_eq!(fixture.store().list_conversations(None).unwrap().len(), 1);
}

#[test]
fn hermes_sharegpt_trajectories_and_raw_role_content_jsonl_are_supported() {
    let fixture = Fixture::new();
    let trajectory = fixture.json(
        "trajectory.json",
        &json!({"model":"hermes-test","timestamp":"2026-01-01T00:00:00",
        "conversations":[{"from":"human","value":"Question"},{"from":"gpt","value":"Answer"}]}),
    );
    let report = import_file(fixture.store(), &trajectory, "hermes").unwrap();
    assert_eq!(
        fixture
            .store()
            .conversation(copied_id(&report))
            .unwrap()
            .len(),
        2
    );
    let raw = fixture.lines(
        "raw.jsonl",
        &[
            json!({"role":"user","content":"Question"}),
            json!({"role":"assistant","content":"Answer"}),
        ],
    );
    assert_eq!(
        import_file(fixture.store(), &raw, "generic")
            .unwrap()
            .imported,
        1
    );
}

#[test]
fn encoded_tool_arguments_and_all_raw_metadata_are_redacted_before_persistence() {
    let fixture = Fixture::new();
    let secret = "sk-import-secret-that-must-not-persist";
    let path = fixture.json("secrets.json",&json!({"id":"secrets","messages":[{"role":"assistant","content":format!("Bearer {secret}"),
        "metadata":{"api_key":"plain-secret-value"},"tool_calls":[{"id":"x","function":{"name":"request","arguments":"{\"api_key\":\"argument-secret-value\"}"}}]}]}));
    let bytes = fs::read(&path).unwrap();
    let report = import_file(fixture.store(), &path, "generic").unwrap();
    let saved =
        serde_json::to_string(&fixture.store().conversation(copied_id(&report)).unwrap()).unwrap();
    for value in [secret, "plain-secret-value", "argument-secret-value"] {
        assert!(!saved.contains(value));
    }
    assert!(saved.contains("REDACTED"));
    assert_eq!(fs::read(path).unwrap(), bytes);
}

#[test]
fn chronological_timestamps_normalize_offsets_and_preserve_source_values() {
    let fixture = Fixture::new();
    let path = fixture.json(
        "times.json",
        &json!({"messages":[
            {"role":"assistant","content":"Later","timestamp":"2026-01-01T09:00:00Z"},
            {"role":"user","content":"Earlier","timestamp":"2026-01-01T10:00:00+02:00"}
        ]}),
    );
    let report = import_file(fixture.store(), &path, "generic").unwrap();
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows[0].content, "Earlier");
    assert_eq!(rows[1].content, "Later");
    assert_eq!(
        rows[0].metadata["portableImport"]["originalTimestamp"],
        "2026-01-01T10:00:00+02:00"
    );
}

#[test]
fn empty_unsupported_or_oversized_files_fail_explicitly_without_empty_chats() {
    let fixture = Fixture::new();
    let empty = fixture.json("empty.json", &json!({"messages":[]}));
    assert_eq!(
        import_file(fixture.store(), &empty, "generic")
            .unwrap()
            .conversations[0]
            .status,
        "failed"
    );
    let unsupported = fixture.json(
        "settings.json",
        &json!({"api_key":"secret","config":{"execute":"no"}}),
    );
    assert!(import_file(fixture.store(), &unsupported, "auto").is_err());
    let large = fixture.root.join("large.json");
    File::create(&large)
        .unwrap()
        .set_len(MAX_TEXT_BYTES + 1)
        .unwrap();
    assert!(import_file(fixture.store(), &large, "generic")
        .unwrap_err()
        .contains("limit"));
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
}

fn make_hermes_database(path: &Path) {
    let db = Connection::open(path).unwrap();
    db.execute_batch("CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,title TEXT,started_at REAL,model TEXT,parent_session_id TEXT,model_config TEXT);
        CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,tool_calls TEXT,tool_call_id TEXT,tool_name TEXT,timestamp REAL,reasoning TEXT,active INTEGER);
        INSERT INTO sessions VALUES('db-session','cli','Database chat',1760000000.0,'hermes-model',NULL,'{\"api_key\":\"do-not-copy\"}');
        INSERT INTO messages VALUES(1,'db-session','user','Question',NULL,NULL,NULL,1760000001.0,NULL,1);
        INSERT INTO messages VALUES(2,'db-session','assistant','Answer','[{\"id\":\"c\",\"function\":{\"name\":\"read\",\"arguments\":\"{\\\"path\\\":\\\"file.txt\\\"}\"}}]',NULL,NULL,1760000002.0,'Think first',1);
        INSERT INTO messages VALUES(3,'db-session','tool','Contents',NULL,'c','read',1760000003.0,NULL,0);").unwrap();
}

#[test]
#[cfg(windows)]
fn hermes_database_is_imported_from_a_read_only_snapshot_and_source_bytes_are_unchanged() {
    let fixture = Fixture::new();
    let source = fixture.root.join("hermes.db");
    make_hermes_database(&source);
    let bytes = fs::read(&source).unwrap();
    let report = import_file(fixture.store(), &source, "auto").unwrap();
    assert_eq!(report.source_format, "hermes");
    assert_eq!(report.imported, 1);
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows.len(), 5);
    assert!(rows
        .iter()
        .any(|row| row.kind == "tool_result" && row.metadata["sourceRecord"]["active"] == 0));
    assert!(!serde_json::to_string(&rows)
        .unwrap()
        .contains("do-not-copy"));
    assert_eq!(fs::read(&source).unwrap(), bytes);
    assert!(!source.with_file_name("hermes.db-wal").exists());
    assert!(!source.with_file_name("hermes.db-shm").exists());
    let repeated = import_file(fixture.store(), &source, "hermes").unwrap();
    assert_eq!(repeated.skipped, 1);
}

#[test]
#[cfg(windows)]
fn hermes_database_keeps_bracket_prefixed_plain_text_without_failing_the_session() {
    let fixture = Fixture::new();
    let path = fixture.root.join("bracket-text.db");
    make_hermes_database(&path);
    let contents = [
        "[README](https://example.com)",
        "[memory]",
        "  [unfinished",
        "[]",
    ];
    let writer = Connection::open(&path).unwrap();
    writer.execute("DELETE FROM messages", []).unwrap();
    for (index, content) in contents.iter().enumerate() {
        writer.execute("INSERT INTO messages(id,session_id,role,content,timestamp,active) VALUES(?1,'db-session','user',?2,?3,1)",
            rusqlite::params![index as i64 + 1, content, 1760000000.0 + index as f64]).unwrap();
    }
    drop(writer);
    let bytes = fs::read(&path).unwrap();
    let report = import_file(fixture.store(), &path, "hermes").unwrap();
    assert_eq!((report.imported, report.failed), (1, 0));
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows.len(), contents.len());
    for (row, content) in rows.iter().zip(contents) {
        assert_eq!(row.content, content);
        assert_eq!(row.metadata["sourceRecord"]["content"], content);
    }
    assert_eq!(fs::read(&path).unwrap(), bytes);
}

#[test]
#[cfg(windows)]
fn hermes_database_keeps_literal_json_arrays_as_original_text() {
    let fixture = Fixture::new();
    let path = fixture.root.join("literal-arrays.db");
    make_hermes_database(&path);
    let contents = [
        r#"["hello"]"#,
        "[1,2,3]",
        r#"[{"task":"literal data"}]"#,
        r#"[{"type":"text","missing_text":"literal data"}]"#,
        r#"[{"type":"text","text":"Text"},42]"#,
        r#"[{"type":"text","text":42,"content":"Fallback"}]"#,
    ];
    let writer = Connection::open(&path).unwrap();
    writer.execute("DELETE FROM messages", []).unwrap();
    for (index, content) in contents.iter().enumerate() {
        writer.execute("INSERT INTO messages(id,session_id,role,content,timestamp,active) VALUES(?1,'db-session','user',?2,?3,1)",
            rusqlite::params![index as i64 + 1, content, 1760000000.0 + index as f64]).unwrap();
    }
    drop(writer);
    let bytes = fs::read(&path).unwrap();
    let report = import_file(fixture.store(), &path, "hermes").unwrap();
    assert_eq!((report.imported, report.failed), (1, 0));
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows.len(), contents.len());
    for (row, content) in rows.iter().zip(contents) {
        assert_eq!(row.content, content);
        assert_eq!(row.metadata["sourceRecord"]["content"], content);
    }
    assert_eq!(fs::read(&path).unwrap(), bytes);
}

#[test]
#[cfg(windows)]
fn hermes_database_decodes_verified_multimodal_blocks_but_retains_the_original_cell() {
    let fixture = Fixture::new();
    let path = fixture.root.join("multimodal.db");
    make_hermes_database(&path);
    let content = r#"[ { "type": "text", "text": "Describe this image" }, { "type": "image_url", "image_url": { "url": "https://example.invalid/image.png", "detail": "low" } } ]"#;
    let writer = Connection::open(&path).unwrap();
    writer.execute("DELETE FROM messages", []).unwrap();
    writer.execute("INSERT INTO messages(id,session_id,role,content,timestamp,active) VALUES(1,'db-session','user',?1,1760000000.0,1)", [content]).unwrap();
    drop(writer);
    let bytes = fs::read(&path).unwrap();
    let report = import_file(fixture.store(), &path, "hermes").unwrap();
    assert_eq!((report.imported, report.failed), (1, 0));
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].kind, "message");
    assert_eq!(rows[0].content, "Describe this image");
    assert_eq!(rows[1].kind, "activity");
    assert!(rows[1]
        .content
        .contains("https://example.invalid/image.png"));
    for row in &rows {
        assert_eq!(row.metadata["sourceRecord"]["content"], content);
    }
    assert_eq!(fs::read(&path).unwrap(), bytes);
    assert_eq!(
        import_file(fixture.store(), &path, "hermes")
            .unwrap()
            .skipped,
        1
    );
}

#[test]
#[cfg(windows)]
fn hermes_wal_rows_are_included_without_changing_database_or_sidecars() {
    let fixture = Fixture::new();
    let creator = fixture.root.join("creator.db");
    make_hermes_database(&creator);
    let writer = Connection::open(&creator).unwrap();
    writer.execute_batch("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;
        INSERT INTO messages VALUES(4,'db-session','user','Pending WAL message',NULL,NULL,NULL,1760000004.0,NULL,1);").unwrap();
    // Only this test owns the creator. After its committed, quiescent write,
    // copy the known coherent pair and close the writer before importing it.
    let before = [
        fs::read(&creator).unwrap(),
        fs::read(sqlite_sidecar(&creator, "-wal")).unwrap(),
    ];
    let source = fixture.root.join("wal.db");
    let wal = sqlite_sidecar(&source, "-wal");
    let shm = sqlite_sidecar(&source, "-shm");
    fs::write(&source, &before[0]).unwrap();
    fs::write(&wal, &before[1]).unwrap();
    drop(writer);
    let report = import_file(fixture.store(), &source, "hermes").unwrap();
    assert!(fixture
        .store()
        .conversation(copied_id(&report))
        .unwrap()
        .iter()
        .any(|row| row.content == "Pending WAL message"));
    assert_eq!(
        before,
        [fs::read(&source).unwrap(), fs::read(&wal).unwrap()]
    );
    assert!(!shm.exists());
}

#[test]
#[cfg(windows)]
fn a_live_hermes_writer_is_rejected_without_touching_database_or_sidecars() {
    let fixture = Fixture::new();
    let source = fixture.root.join("active.db");
    make_hermes_database(&source);
    let writer = Connection::open(&source).unwrap();
    writer.execute_batch("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;
        INSERT INTO messages VALUES(4,'db-session','user','Pending WAL message',NULL,NULL,NULL,1760000004.0,NULL,1);").unwrap();
    let wal = sqlite_sidecar(&source, "-wal");
    let shm = sqlite_sidecar(&source, "-shm");
    let before = [
        fs::read(&source).unwrap(),
        fs::read(&wal).unwrap(),
        fs::read(&shm).unwrap(),
    ];
    let error = import_file(fixture.store(), &source, "hermes").unwrap_err();
    assert_eq!(error, CLOSE_HERMES_DATABASE);
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
    assert_eq!(
        before,
        [
            fs::read(&source).unwrap(),
            fs::read(&wal).unwrap(),
            fs::read(&shm).unwrap()
        ]
    );
    drop(writer);
}

#[test]
#[cfg(windows)]
fn live_writer_and_same_size_wal_checkpoint_reuse_cannot_import_mixed_generations() {
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc,
    };
    let fixture = Fixture::new();
    let source = fixture.root.join("checkpoint.db");
    make_hermes_database(&source);
    let worker_path = source.clone();
    let stop = Arc::new(AtomicBool::new(false));
    let worker_stop = stop.clone();
    let (ready, started) = mpsc::channel();
    let worker = std::thread::spawn(move || {
        let writer = Connection::open(worker_path).unwrap();
        writer
            .execute_batch(
                "PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;
            BEGIN IMMEDIATE; UPDATE sessions SET title='Generation A';
            UPDATE messages SET content='Generation A'; COMMIT;
            PRAGMA wal_checkpoint(RESTART);",
            )
            .unwrap();
        ready.send(()).unwrap();
        let mut generation_a = false;
        while !worker_stop.load(Ordering::SeqCst) {
            let value = if generation_a {
                "Generation A"
            } else {
                "Generation B"
            };
            writer.execute_batch("BEGIN IMMEDIATE;").unwrap();
            writer
                .execute("UPDATE sessions SET title=?1", [value])
                .unwrap();
            writer
                .execute("UPDATE messages SET content=?1", [value])
                .unwrap();
            writer
                .execute_batch("COMMIT; PRAGMA wal_checkpoint(RESTART);")
                .unwrap();
            generation_a = !generation_a;
        }
    });
    started.recv().unwrap();
    let result = import_file(fixture.store(), &source, "hermes");
    stop.store(true, Ordering::SeqCst);
    worker.join().unwrap();
    // The writer's read/write handle spans the entire import attempt. No main/WAL
    // generation is copied; file timestamps and equal lengths cannot bypass it.
    assert_eq!(result.unwrap_err(), CLOSE_HERMES_DATABASE);
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
}

#[test]
#[cfg(not(windows))]
fn sqlite_import_without_supported_writer_exclusion_rejects_and_leaves_source_unchanged() {
    let fixture = Fixture::new();
    let source = fixture.root.join("unsupported.db");
    make_hermes_database(&source);
    let before = fs::read(&source).unwrap();
    assert!(import_file(fixture.store(), &source, "hermes")
        .unwrap_err()
        .contains("JSON/JSONL export on this platform"));
    assert_eq!(fs::read(&source).unwrap(), before);
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
}

#[test]
#[cfg(windows)]
fn non_hermes_databases_and_virtual_or_missing_schema_are_rejected() {
    let fixture = Fixture::new();
    let path = fixture.root.join("other.sqlite3");
    let db = Connection::open(&path).unwrap();
    db.execute_batch("CREATE TABLE unrelated(data TEXT);")
        .unwrap();
    drop(db);
    assert!(import_file(fixture.store(), &path, "hermes")
        .unwrap_err()
        .contains("Hermes"));
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
}

#[test]
fn bulk_cancellation_keeps_completed_conversations_atomic_and_reports_progress() {
    let fixture = Fixture::new();
    let path = fixture.json(
        "batch.json",
        &json!({"conversations":[
            {"id":"first","messages":[{"role":"user","content":"First"}]},
            {"id":"second","messages":[{"role":"user","content":"Second"}]}
        ]}),
    );
    let stop = Cell::new(false);
    let report = import_file_with_cancellation(
        fixture.store(),
        &path,
        "generic",
        &|| stop.get(),
        &mut |progress| {
            if progress.imported == 1 {
                stop.set(true);
            }
        },
    )
    .unwrap();
    assert!(report.cancelled);
    assert_eq!(report.imported, 1);
    assert_eq!(fixture.store().list_conversations(None).unwrap().len(), 1);
}

#[test]
fn preview_does_not_persist_and_reports_invalid_conversations() {
    let fixture = Fixture::new();
    let path = fixture.json(
        "preview.json",
        &json!({"conversations":[
            {"id":"ok","messages":[{"role":"user","content":"Valid"}]},
            {"id":"bad","messages":[{"content":"Missing role"}]}
        ]}),
    );
    let preview = preview_file(&path, "generic").unwrap();
    assert_eq!(preview.conversations, 2);
    assert_eq!(preview.entries, 1);
    assert!(preview.samples[1].error.is_some());
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
}

#[test]
fn normalized_budget_is_enforced_while_collecting_conversations() {
    let mut candidates = Vec::new();
    let (mut entries, mut bytes) = (MAX_ENTRIES, 0usize);
    let mut candidate = failed_candidate("generic", "limited", "Limited", "unused".into());
    candidate.error = None;
    candidate.rows.push((
        "1970-01-01T00:00:00Z".into(),
        "message".into(),
        "user".into(),
        "User".into(),
        "Text".into(),
        json!({}),
    ));
    assert!(
        append_candidate(&mut candidates, &mut entries, &mut bytes, candidate)
            .unwrap_err()
            .contains("limit")
    );
    assert!(candidates.is_empty());
}
