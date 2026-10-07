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

fn opencode_export(folder: &Path) -> Value {
    json!({"info":{"id":"oc-session","title":"OpenCode work","directory":folder,"projectID":"project-1",
        "parentID":"parent-session","time":{"created":1760000000000_i64},"permission":[{"action":"allow","pattern":"*"}]},
        "project":{"id":"project-1","name":"Original project name","worktree":folder,"custom":{"kept":42}},
        "messages":[
            {"info":{"id":"user-1","sessionID":"oc-session","role":"user","time":{"created":1760000001000_i64},"model":{"modelID":"original","providerID":"source"}},
                "parts":[{"id":"text-1","type":"text","text":"Read the existing file"}]},
            {"info":{"id":"assistant-1","sessionID":"oc-session","role":"assistant","time":{"created":1760000002000_i64},"tokens":{"input":23,"output":7}},
                "parts":[{"id":"thinking-1","type":"reasoning","text":"Check it first"},
                    {"id":"call-1","type":"tool","callID":"source-call","tool":"read_file","state":{"status":"completed",
                        "input":{"path":"source.txt"},"output":"Original file","time":{"start":1760000002000_i64,"end":1760000003000_i64}}},
                    {"id":"answer-1","type":"text","text":"Read successfully"}]}
        ]})
}

#[test]
fn opencode_native_export_imports_real_project_and_inert_tools_without_changing_sources() {
    let fixture = Fixture::new();
    let folder = fixture.root.join("original-project");
    fs::create_dir(&folder).unwrap(); fs::write(folder.join("source.txt"), "Original file").unwrap();
    let export = opencode_export(&folder);
    let path = fixture.json("opencode.json", &export);
    let source = fs::read(&path).unwrap();
    let preview = preview_file(&path, "auto").unwrap();
    assert_eq!(preview.source_format, "opencode");
    assert_eq!(preview.samples[0].folder_status, "available");
    let report = import_file(fixture.store(), &path, "auto").unwrap();
    assert_eq!(report.imported, 1); assert_eq!(report.conversations[0].folder_status, "linked");
    let project = fixture.store().list_projects().unwrap().remove(0);
    assert_eq!(project.name, "Original project name");
    assert_eq!(fs::canonicalize(project.folder_path.unwrap()).unwrap(), fs::canonicalize(&folder).unwrap());
    assert_eq!(report.conversations[0].project_id.as_deref(), Some(project.id.as_str()));
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows.len(), 5);
    assert!(rows.iter().all(|row| row.source == "Imported OpenCode" && row.metadata["portableImport"]["inert"] == true));
    assert!(rows.iter().any(|row| row.kind == "tool_call" && row.title == "read_file"));
    assert!(rows.iter().any(|row| row.kind == "tool_result" && row.content == "Original file"));
    assert_eq!(rows[0].metadata["portableImport"]["sourceConversation"]["sourceProject"]["custom"]["kept"], 42);
    assert_eq!(rows[0].metadata["sourceMessage"]["model"]["modelID"], "original");
    assert_eq!(rows[0].metadata["portableImport"]["sourceConversation"]["parentID"], "parent-session");
    assert_eq!(import_file(fixture.store(), &path, "opencode").unwrap().skipped, 1);
    assert_eq!(fs::read(&path).unwrap(), source);
    assert_eq!(fs::read_to_string(folder.join("source.txt")).unwrap(), "Original file");
    assert_eq!(fs::read_dir(&folder).unwrap().count(), 1);
}

#[test]
fn opencode_updates_keep_manual_titles_pins_and_project_moves() {
    let fixture = Fixture::new();
    let folder = fixture.root.join("source"); fs::create_dir(&folder).unwrap();
    let mut export = opencode_export(&folder);
    let path = fixture.json("opencode.json", &export);
    let report = import_file(fixture.store(), &path, "opencode").unwrap();
    let id = copied_id(&report);
    let manual_folder = fixture.root.join("chosen"); fs::create_dir(&manual_folder).unwrap();
    let project = fixture.store().create_project("Manually chosen", &manual_folder).unwrap();
    fixture.store().set_project_by_id(id, Some(&project.id), crate::store::ProjectAssignment::Manual).unwrap();
    fixture.store().rename_conversation(id, "My title").unwrap();
    fixture.store().set_conversation_pinned(id, true).unwrap();
    export["messages"].as_array_mut().unwrap().push(json!({"id":"new-message","type":"user","text":"A follow-up", "time":{"created":1760000004000_i64}}));
    fs::write(&path, serde_json::to_vec(&export).unwrap()).unwrap();
    let updated = import_file(fixture.store(), &path, "opencode").unwrap();
    assert_eq!(updated.updated, 1); assert_eq!(updated.conversations[0].folder_status, "available");
    let chat = fixture.store().list_conversations(None).unwrap().into_iter().find(|chat| chat.id == id).unwrap();
    assert_eq!(chat.title, "My title"); assert!(chat.pinned); assert_eq!(chat.project_id.as_deref(), Some(project.id.as_str()));
}

#[test]
fn source_project_roots_link_nested_sessions_without_rewriting_their_exact_cwd() {
    let fixture = Fixture::new();
    let folder = fixture.root.join("source-repository");
    let cwd = folder.join("nested"); fs::create_dir_all(&cwd).unwrap();
    let mut export = opencode_export(&folder);
    export["info"]["directory"] = json!(cwd);
    let opencode = fixture.json("nested-opencode.json", &export);
    let report = import_file(fixture.store(), &opencode, "opencode").unwrap();
    assert_eq!(report.conversations[0].folder_status, "linked");
    let project = fixture.store().list_projects().unwrap().remove(0);
    assert_eq!(fs::canonicalize(project.folder_path.unwrap()).unwrap(), fs::canonicalize(&folder).unwrap());
    let hermes = fixture.json("nested-hermes.json", &json!({"id":"nested-hermes","cwd":cwd,
        "git_repo_root":folder,"started_at":1760000000,"source":"cli","messages":[{"role":"user","content":"A nested task"}]}));
    let hermes_report = import_file(fixture.store(), &hermes, "hermes").unwrap();
    assert_eq!(fixture.store().list_projects().unwrap().len(), 1);
    assert_eq!(report.conversations[0].project_id, hermes_report.conversations[0].project_id);
    let db = Connection::open(fixture.root.join("opencore.sqlite3")).unwrap();
    for id in [copied_id(&report), copied_id(&hermes_report)] {
        let exact: String = db.query_row("SELECT source_cwd FROM conversations WHERE id=?1", [id], |row| row.get(0)).unwrap();
        assert_eq!(exact, cwd.to_string_lossy());
    }
    drop(db);
    let mut other_checkout = json!({"directory":fixture.root.join("other-checkout")});
    projects::attach_opencode_project(&mut other_checkout, &json!({"worktree":folder}));
    assert!(other_checkout.get("sourceProjectFolder").is_none());
    let mut global = json!({"directory":fixture.root});
    projects::attach_opencode_project(&mut global, &json!({"worktree":"/"}));
    assert!(global.get("sourceProjectFolder").is_none());
}

#[test]
fn missing_or_relative_source_folders_are_preserved_without_creating_substitutes() {
    let fixture = Fixture::new();
    let missing = fixture.root.join("deleted-original-folder");
    let first = fixture.json("missing.json", &opencode_export(&missing));
    let report = import_file(fixture.store(), &first, "auto").unwrap();
    assert_eq!(report.imported, 1); assert_eq!(report.conversations[0].folder_status, "missing");
    assert!(report.conversations[0].project_id.is_none()); assert!(!missing.exists());
    assert!(!report.conversations[0].warnings.is_empty());
    let relative = fixture.json("relative.json", &json!({"id":"relative-hermes", "cwd":"relative/source",
        "source":"cli","started_at":1760000000,"messages":[{"role":"user","content":"Keep this chat"}]}));
    let report = import_file(fixture.store(), &relative, "hermes").unwrap();
    assert_eq!(report.imported, 1); assert_eq!(report.conversations[0].folder_status, "nonlocal");
    assert_eq!(report.conversations[0].source_folder.as_deref(), Some("relative/source"));
    assert!(fixture.store().list_projects().unwrap().is_empty());
    let db = Connection::open(fixture.root.join("opencore.sqlite3")).unwrap();
    let raw: String = db.query_row("SELECT source_cwd FROM conversations WHERE id=?1", [copied_id(&report)], |row| row.get(0)).unwrap();
    assert_eq!(raw, "relative/source");
}

#[test]
fn opencode_native_typed_messages_preserve_shell_and_compaction_as_history() {
    let fixture = Fixture::new();
    let path = fixture.json("typed.json", &json!({"info":{"id":"new-format","directory":fixture.root,"title":"Typed history"},"messages":[
        {"id":"u","type":"user","text":"Inspect","time":{"created":1760000000000_i64}},
        {"id":"a","type":"assistant","content":[{"id":"r","type":"reasoning","text":"Reasoning"},
            {"id":"t","type":"tool","name":"read","state":{"status":"completed","input":{"path":"file"},"content":[{"type":"text","text":"Output"}],"structured":{},"result":{"ok":true}}}],"time":{"created":1760000001000_i64}},
        {"id":"s","type":"shell","command":"never-execute-imported-command","output":"Historical output","time":{"created":1760000002000_i64,"completed":1760000003000_i64}},
        {"id":"c","type":"compaction","summary":"Historical summary","recent":"Recent", "time":{"created":1760000004000_i64}}
    ]}));
    let report = import_file(fixture.store(), &path, "auto").unwrap();
    assert_eq!(report.imported, 1);
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert!(rows.iter().any(|row| row.kind == "tool_call" && row.content == "never-execute-imported-command"));
    assert!(rows.iter().any(|row| row.kind == "tool_result" && row.content == "Historical output"));
    assert!(rows.iter().any(|row| row.title == "compaction" && row.metadata["sourceRecord"]["summary"] == "Historical summary"));
    assert!(rows.iter().all(|row| row.metadata["portableImport"]["inert"] == true));
}

#[test]
fn opencode_conflicting_parts_are_atomic_and_cancellation_is_not_a_failed_chat() {
    let fixture = Fixture::new();
    let mut export = opencode_export(&fixture.root);
    export["messages"][0]["parts"].as_array_mut().unwrap().push(json!({"id":"text-1","type":"text","text":"Conflicting content"}));
    let path = fixture.json("conflict.json", &export);
    let report = import_file(fixture.store(), &path, "opencode").unwrap();
    assert_eq!(report.failed, 1); assert!(fixture.store().list_conversations(None).unwrap().is_empty());
    let calls = Cell::new(0);
    let cancelled = opencode::prepare_documents(vec![(1,Ok(opencode_export(&fixture.root)))], &|| {
        let count = calls.get()+1; calls.set(count); count >= 5
    });
    assert_eq!(cancelled.err().as_deref(), Some(IMPORT_CANCELLED));
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
fn hermes_project_registry_links_original_parent_folder_and_keeps_exact_session_cwd() {
    let fixture = Fixture::new();
    let folder = fixture.root.join("original-repo");
    let cwd = folder.join("nested"); fs::create_dir_all(&cwd).unwrap();
    fs::write(folder.join("existing.txt"), "Original content").unwrap();
    let path = fixture.root.join("state.db"); make_hermes_database(&path);
    let db = Connection::open(&path).unwrap();
    db.execute_batch("ALTER TABLE sessions ADD COLUMN cwd TEXT; ALTER TABLE sessions ADD COLUMN git_repo_root TEXT;").unwrap();
    db.execute("UPDATE sessions SET cwd=?1,git_repo_root=?2", rusqlite::params![cwd.to_string_lossy(),folder.to_string_lossy()]).unwrap();
    drop(db);
    let registry_path = fixture.root.join("projects.db");
    let registry = Connection::open(&registry_path).unwrap();
    registry.execute_batch("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,primary_path TEXT,description TEXT,archived INTEGER);
        CREATE TABLE project_folders(project_id TEXT,path TEXT,label TEXT,is_primary INTEGER);").unwrap();
    registry.execute("INSERT INTO projects VALUES('hermes-project','Original Hermes project',?1,'Keep project metadata',0)", [folder.to_string_lossy().as_ref()]).unwrap();
    registry.execute("INSERT INTO project_folders VALUES('hermes-project',?1,'Repository',1)", [folder.to_string_lossy().as_ref()]).unwrap();
    drop(registry);
    let before = fs::read(&path).unwrap(); let registry_before = fs::read(&registry_path).unwrap();
    let report = import_file(fixture.store(), &path, "auto").unwrap();
    assert_eq!(report.imported, 1); assert_eq!(report.conversations[0].folder_status, "linked");
    let project = fixture.store().list_projects().unwrap().remove(0);
    assert_eq!(project.name, "Original Hermes project");
    assert_eq!(fs::canonicalize(project.folder_path.unwrap()).unwrap(), fs::canonicalize(&folder).unwrap());
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    let header = &rows[0].metadata["portableImport"]["sourceConversation"];
    assert_eq!(header["cwd"], cwd.to_string_lossy().as_ref());
    assert_eq!(header["sourceProject"]["description"], "Keep project metadata");
    assert_eq!(header["sourceProject"]["folders"][0]["label"], "Repository");
    let app_db = Connection::open(fixture.root.join("opencore.sqlite3")).unwrap();
    let exact: String = app_db.query_row("SELECT source_cwd FROM conversations WHERE id=?1", [copied_id(&report)], |row| row.get(0)).unwrap();
    assert_eq!(exact, cwd.to_string_lossy()); drop(app_db);
    assert_eq!(fs::read(&path).unwrap(), before); assert_eq!(fs::read(&registry_path).unwrap(), registry_before);
    assert_eq!(fs::read_to_string(folder.join("existing.txt")).unwrap(), "Original content");
}

#[test]
#[cfg(windows)]
fn opencode_database_combines_message_families_deduplicates_migrated_ids_and_preserves_project() {
    let fixture = Fixture::new();
    let folder = fixture.root.join("original-open-code"); fs::create_dir(&folder).unwrap();
    let path = fixture.root.join("opencode.db");
    let db = Connection::open(&path).unwrap();
    db.execute_batch("CREATE TABLE session(id TEXT PRIMARY KEY,project_id TEXT,title TEXT,directory TEXT,time_created INTEGER,time_updated INTEGER,parent_id TEXT);
        CREATE TABLE project(id TEXT PRIMARY KEY,worktree TEXT,name TEXT,commands TEXT);
        CREATE TABLE message(id TEXT PRIMARY KEY,session_id TEXT,time_created INTEGER,time_updated INTEGER,data TEXT);
        CREATE TABLE part(id TEXT PRIMARY KEY,message_id TEXT,session_id TEXT,time_created INTEGER,time_updated INTEGER,data TEXT);
        CREATE TABLE session_message(id TEXT PRIMARY KEY,session_id TEXT,type TEXT,seq INTEGER,time_created INTEGER,time_updated INTEGER,data TEXT);").unwrap();
    db.execute("INSERT INTO project VALUES('p',?1,'Real OpenCode project','{\"start\":\"do-not-execute\"}')", [folder.to_string_lossy().as_ref()]).unwrap();
    db.execute("INSERT INTO session VALUES('session','p','Mixed source',?1,1760000000000,1760000004000,'parent')", [folder.to_string_lossy().as_ref()]).unwrap();
    db.execute("INSERT INTO message VALUES('u','session',1760000001000,1760000001000,?1)", [json!({"role":"user","time":{"created":1760000001000_i64}}).to_string()]).unwrap();
    db.execute("INSERT INTO part VALUES('up','u','session',1760000001000,1760000001000,?1)", [json!({"type":"text","text":"Legacy question"}).to_string()]).unwrap();
    db.execute("INSERT INTO message VALUES('a','session',1760000002000,1760000002000,?1)", [json!({"role":"assistant","time":{"created":1760000002000_i64}}).to_string()]).unwrap();
    db.execute("INSERT INTO part VALUES('ap','a','session',1760000002000,1760000002000,?1)", [json!({"type":"text","text":"Older projection"}).to_string()]).unwrap();
    db.execute("INSERT INTO session_message VALUES('a','session','assistant',1,1760000002000,1760000002000,?1)", [json!({"content":[{"id":"modern","type":"text","text":"Current answer"}],"time":{"created":1760000002000_i64}}).to_string()]).unwrap();
    db.execute("INSERT INTO session_message VALUES('follow','session','user',2,1760000003000,1760000003000,?1)", [json!({"text":"Modern follow-up","time":{"created":1760000003000_i64}}).to_string()]).unwrap();
    drop(db);
    let before = fs::read(&path).unwrap();
    let report = import_file(fixture.store(), &path, "auto").unwrap();
    assert_eq!(report.source_format, "opencode"); assert_eq!(report.imported, 1);
    let rows = fixture.store().conversation(copied_id(&report)).unwrap();
    assert_eq!(rows.len(), 3);
    assert_eq!(rows.iter().map(|row|row.content.as_str()).collect::<Vec<_>>(), vec!["Legacy question","Current answer","Modern follow-up"]);
    assert_eq!(rows[0].metadata["sourceRecord"]["sourceDatabaseMetadata"]["time_created"], 1760000001000_i64);
    assert_eq!(rows[0].metadata["portableImport"]["sourceConversation"]["sourceProject"]["name"], "Real OpenCode project");
    assert_eq!(rows[1].metadata["sourceMessage"]["sourceDatabaseMetadata"]["seq"], 1);
    assert_eq!(fixture.store().list_projects().unwrap()[0].name, "Real OpenCode project");
    assert_eq!(fixture.store().imported_conversation_ids_for_client("opencode").unwrap(), vec![copied_id(&report)]);
    assert_eq!(fixture.store().imported_message_count(copied_id(&report)).unwrap(), 3);
    assert_eq!(fixture.store().imported_messages_batch(copied_id(&report), 0, 100).unwrap().len(), 3);
    assert_eq!(import_file(fixture.store(), &path, "opencode").unwrap().skipped, 1);
    assert_eq!(fs::read(&path).unwrap(), before);
}

#[test]
fn first_class_agent_connectors_are_observable_and_copy_cleanup_does_not_remove_sources() {
    let fixture = Fixture::new();
    let connectors = fixture.store().connectors().unwrap();
    for id in ["opencode", "hermes"] {
        let connector = connectors.iter().find(|connector| connector.id == id).unwrap();
        assert!(!connector.custom); assert_eq!(connector.kind, "history");
    }
    fixture.store().observe_client("Hermes Agent"); fixture.store().observe_client("OpenCode");
    assert!(fixture.store().connectors().unwrap().iter().filter(|connector| matches!(connector.id.as_str(), "hermes"|"opencode")).all(|connector|connector.observable));
    let folder = fixture.root.join("project"); fs::create_dir(&folder).unwrap();
    fs::write(folder.join("keep.txt"), "source").unwrap();
    let opencode = fixture.json("oc.json", &opencode_export(&folder));
    let hermes = fixture.json("hermes.json", &json!({"id":"hermes","source":"cli","cwd":folder,"started_at":1760000000,"messages":[{"role":"user","content":"Keep Hermes"}]}));
    let oc = import_file(fixture.store(), &opencode, "opencode").unwrap();
    let hermes_report = import_file(fixture.store(), &hermes, "hermes").unwrap();
    assert_eq!(fixture.store().list_projects().unwrap().len(), 1);
    let oc_bytes = fs::read(&opencode).unwrap(); let hermes_bytes = fs::read(&hermes).unwrap();
    assert_eq!(fixture.store().clear_imported_history("opencode").unwrap(), vec![copied_id(&oc)]);
    assert!(fixture.store().conversation(copied_id(&hermes_report)).unwrap().iter().any(|row| row.content == "Keep Hermes"));
    assert_eq!(fs::read(opencode).unwrap(), oc_bytes); assert_eq!(fs::read(hermes).unwrap(), hermes_bytes);
    assert_eq!(fs::read_to_string(folder.join("keep.txt")).unwrap(), "source");
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
fn an_idle_live_hermes_connection_imports_committed_wal_without_touching_sources() {
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
    let report = import_file(fixture.store(), &source, "hermes").unwrap();
    assert_eq!(report.imported, 1);
    assert!(fixture.store().conversation(copied_id(&report)).unwrap().iter()
        .any(|row| row.content == "Pending WAL message"));
    assert_eq!(
        before,
        [
            fs::read(&source).unwrap(),
            fs::read(&wal).unwrap(),
            fs::read(&shm).unwrap()
        ]
    );
    // All source locks must have been released before returning to the caller.
    writer.execute("UPDATE sessions SET title='Still writable'", []).unwrap();
}

#[test]
#[cfg(windows)]
fn idle_live_rollback_connection_imports_without_creating_sidecars() {
    let fixture = Fixture::new();
    let source = fixture.root.join("rollback.db");
    make_hermes_database(&source);
    let writer = Connection::open(&source).unwrap();
    let before = fs::read(&source).unwrap();
    let report = import_file(fixture.store(), &source, "hermes").unwrap();
    assert_eq!(report.imported, 1);
    assert_eq!(fs::read(&source).unwrap(), before);
    for suffix in ["-wal", "-shm", "-journal"] {
        assert!(!sqlite_sidecar(&source, suffix).exists());
    }
    writer.execute("UPDATE sessions SET title='Still writable'", []).unwrap();
}

#[test]
#[cfg(windows)]
fn an_unfinished_live_transaction_is_busy_and_source_bytes_are_preserved() {
    let fixture = Fixture::new();
    let source = fixture.root.join("writing.db");
    make_hermes_database(&source);
    let writer = Connection::open(&source).unwrap();
    writer.execute_batch("PRAGMA journal_mode=WAL; BEGIN IMMEDIATE;
        UPDATE messages SET content='Not committed';").unwrap();
    let paths = [source.clone(), sqlite_sidecar(&source, "-wal"), sqlite_sidecar(&source, "-shm")];
    let read_sources = || paths.iter().map(|path| {
        if path == &paths[2] {
            // An active Windows WAL writer exclusively locks unused SHM bytes
            // 120..127, so ReadFile cannot cross them. Compare every content byte
            // around those lock slots without mapping the source in this test.
            let mut file = File::open(path).unwrap();
            let mut bytes = vec![0; 128];
            file.read_exact(&mut bytes[..120]).unwrap();
            file.seek(SeekFrom::Start(128)).unwrap();
            file.read_to_end(&mut bytes).unwrap();
            bytes
        } else {
            fs::read(path).unwrap()
        }
    }).collect::<Vec<_>>();
    let before = read_sources();
    let error = import_file(fixture.store(), &source, "hermes").unwrap_err();
    assert!(error.contains(BUSY_CHAT_DATABASE), "{error}");
    assert!(error.contains(&source.display().to_string()), "{error}");
    assert!(error.contains("os error 33") || error.contains("os error 32"), "{error}");
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
    assert_eq!(before, read_sources());
    writer.execute_batch("ROLLBACK;").unwrap();
    assert_eq!(import_file(fixture.store(), &source, "hermes").unwrap().imported, 1);
}

#[test]
#[cfg(windows)]
fn cancelled_snapshot_releases_locks_and_does_not_import_partial_history() {
    let fixture = Fixture::new();
    let source = fixture.root.join("cancelled.db");
    make_hermes_database(&source);
    let writer = Connection::open(&source).unwrap();
    writer.execute_batch("PRAGMA journal_mode=WAL;
        INSERT INTO messages VALUES(4,'db-session','user','Committed before cancellation',NULL,NULL,NULL,1760000004.0,NULL,1);").unwrap();
    let checks = Cell::new(0);
    let report = import_file_with_cancellation(fixture.store(), &source, "hermes", &|| {
        checks.set(checks.get() + 1);
        checks.get() >= 6
    }, &mut |_| {}).unwrap();
    assert!(report.cancelled);
    assert_eq!(report.imported, 0);
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
    writer.execute("UPDATE sessions SET title='Still writable'", []).unwrap();
}

#[test]
#[cfg(windows)]
fn a_new_source_reader_can_connect_while_snapshot_locks_are_held() {
    let fixture = Fixture::new();
    let source = fixture.root.join("reading.db");
    make_hermes_database(&source);
    let writer = Connection::open(&source).unwrap();
    writer.execute_batch("PRAGMA journal_mode=WAL;
        INSERT INTO messages VALUES(4,'db-session','user','Committed before reading',NULL,NULL,NULL,1760000004.0,NULL,1);").unwrap();
    let checks = Cell::new(0);
    let observed = Cell::new(false);
    let snapshot = snapshot_database(&source, &|| {
        checks.set(checks.get() + 1);
        if checks.get() == 3 {
            // The first copy cancellation check runs after main + SHM locks.
            let reader = Connection::open_with_flags(&source, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
            let count: i64 = reader.query_row("SELECT count(*) FROM messages", [], |row| row.get(0)).unwrap();
            assert_eq!(count, 4);
            observed.set(true);
        }
        false
    }).unwrap();
    assert!(observed.get());
    drop(snapshot);
    writer.execute("UPDATE sessions SET title='Still writable'", []).unwrap();
}

#[test]
#[cfg(windows)]
fn a_hot_rollback_journal_is_rejected_without_recovering_the_source() {
    let fixture = Fixture::new();
    let source = fixture.root.join("recover.db");
    make_hermes_database(&source);
    let journal = sqlite_sidecar(&source, "-journal");
    let mut bytes = vec![0; 1024];
    bytes[..8].copy_from_slice(&[0xd9, 0xd5, 0x05, 0xf9, 0x20, 0xa1, 0x63, 0xd7]);
    fs::write(&journal, &bytes).unwrap();
    let before = fs::read(&source).unwrap();
    assert!(import_file(fixture.store(), &source, "hermes").unwrap_err().contains("unfinished rollback transaction"));
    assert_eq!(fs::read(&source).unwrap(), before);
    assert_eq!(fs::read(&journal).unwrap(), bytes);
    assert!(fixture.store().list_conversations(None).unwrap().is_empty());
}

#[test]
#[cfg(windows)]
fn live_writer_and_same_size_wal_checkpoint_reuse_never_imports_mixed_generations() {
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
        writer.busy_timeout(std::time::Duration::from_secs(3)).unwrap();
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
    match result {
        Ok(report) => {
            assert_eq!(report.imported, 1);
            let item = &report.conversations[0];
            assert!(matches!(item.title.as_str(), "Generation A" | "Generation B"));
            let rows = fixture.store().conversation(copied_id(&report)).unwrap();
            assert!(rows.iter().filter(|row| row.kind == "message")
                .all(|row| row.content == item.title));
        }
        Err(error) => {
            assert!(error.contains(BUSY_CHAT_DATABASE), "{error}");
            assert!(fixture.store().list_conversations(None).unwrap().is_empty());
        }
    }
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
