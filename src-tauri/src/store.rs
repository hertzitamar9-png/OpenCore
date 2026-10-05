use crate::models::{
    ConnectorInput, ConnectorStatus, ConversationSummary, LogEntry, OperationRecord, ProjectSummary, TimelineEntry,
};
use crate::redaction::{redact_json, redact_text};
use crate::project_paths::canonical_existing_directory;
use chrono::Utc;
use serde::{Deserialize, Serialize};
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::Path;
use std::sync::Mutex;
use sysinfo::{Pid, ProcessesToUpdate, System};

pub struct EventStore {
    connection: Mutex<Connection>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProjectAssignment { Legacy, Automatic, Manual }

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CodexThreadMapping {
    pub conversation_id: String,
    pub workspace_identity: String,
    pub thread_id: String,
    pub provider_id: String,
    pub model_id: String,
    pub runtime_version: String,
    pub schema_hash: String,
    pub migration_state: String,
    pub legacy_sdk_thread_id: Option<String>,
}

fn codex_thread_mapping_key(conversation_id: &str, workspace_identity: &str) -> String {
    let digest = Sha256::digest(format!("{conversation_id}\0{workspace_identity}").as_bytes());
    format!("codex_app_server_thread_v1_{digest:x}")
}

impl ProjectAssignment {
    fn as_str(self) -> &'static str {
        match self { Self::Legacy => "legacy", Self::Automatic => "automatic", Self::Manual => "manual" }
    }
}

fn operation_from_row(row: &Row<'_>) -> rusqlite::Result<OperationRecord> {
    Ok(OperationRecord {
        id: row.get(0)?, kind: row.get(1)?, target: row.get(2)?,
        phase: row.get(3)?, status: row.get(4)?,
        current: row.get::<_, i64>(5)? as u64, total: row.get::<_, i64>(6)? as u64,
        imported: row.get::<_, i64>(7)? as u64, updated: row.get::<_, i64>(8)? as u64,
        skipped: row.get::<_, i64>(9)? as u64, summary: row.get(10)?,
        error: row.get(11)?, started_at: row.get(12)?, finished_at: row.get(13)?,
        last_progress_at: row.get(14)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_project_migration_keeps_id_and_timeline() {
        let root = std::env::temp_dir().join(format!("opencore-legacy-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let path = root.join("history.sqlite3");
        {
            let connection = Connection::open(&path).unwrap();
            connection.execute_batch("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE COLLATE NOCASE,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT NOT NULL,client TEXT NOT NULL,profile TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,project TEXT NOT NULL DEFAULT '',project_id TEXT,pinned INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE timeline(id INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id TEXT NOT NULL,timestamp TEXT NOT NULL,kind TEXT NOT NULL,role TEXT NOT NULL,source TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,metadata TEXT NOT NULL);
                INSERT INTO projects VALUES('old-id','Old project','2026','2026');
                INSERT INTO conversations VALUES('legacy-chat','Keep','Codex','history','imported','2026','2026','Old project','old-id',1);
                INSERT INTO timeline(conversation_id,timestamp,kind,role,source,title,content,metadata) VALUES('legacy-chat','2026','message','user','Codex','User','kept message','{}');").unwrap();
        }
        let store = EventStore::open(&path).unwrap();
        assert_eq!(store.list_projects().unwrap()[0].id, "old-id");
        assert_eq!(store.conversation("legacy-chat").unwrap()[0].content, "kept message");
        assert_eq!(store.list_conversations(None).unwrap()[0].project_id.as_deref(), Some("old-id"));
        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn echo_search_scope_includes_only_conversations_in_the_active_project() {
        let root = std::env::temp_dir().join(format!("opencore-echo-scope-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
        let project = store.create_project("Project A", &root).unwrap();
        for id in ["project-chat-a", "project-chat-b", "other-chat", "unassigned-chat"] {
            store.ensure_conversation(id, "OpenCore", "history", id).unwrap();
        }
        store.set_project_by_id("project-chat-a", Some(&project.id), ProjectAssignment::Manual).unwrap();
        store.set_project_by_id("project-chat-b", Some(&project.id), ProjectAssignment::Manual).unwrap();

        assert_eq!(store.echo_conversation_scope("project-chat-a").unwrap(), vec!["project-chat-a", "project-chat-b"]);
        assert_eq!(store.echo_conversation_scope("other-chat").unwrap(), vec!["other-chat"]);
        assert_eq!(store.echo_conversation_scope("missing-chat").unwrap(), vec!["missing-chat"]);

        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn folder_projects_distinguish_same_names_and_manual_moves_survive_import() {
        let root = std::env::temp_dir().join(format!("opencore-folder-store-{}", uuid::Uuid::new_v4()));
        let left = root.join("left").join("app");
        let right = root.join("right").join("app");
        std::fs::create_dir_all(&left).unwrap();
        std::fs::create_dir_all(&right).unwrap();
        let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
        store.ensure_conversation("chat", "Codex", "history", "Keep").unwrap();
        let a = store.create_project("App", &left).unwrap();
        let b = store.create_project("App", &right).unwrap();
        assert_ne!(a.id, b.id);
        assert!(store.create_project("Duplicate", &left).is_err());
        store.set_project_by_id("chat", Some(&a.id), ProjectAssignment::Manual).unwrap();
        store.record_imported_directory("chat", Some(&right)).unwrap();
        assert_eq!(store.list_conversations(None).unwrap()[0].project_id.as_deref(), Some(a.id.as_str()));
        let renamed = store.rename_project(&a.id, "My app").unwrap();
        assert_eq!(renamed.folder_path, a.folder_path);
        assert_eq!(store.delete_project(&a.id).unwrap(), 1);
        assert!(left.is_dir());
        assert!(right.is_dir());
        assert_eq!(store.list_conversations(None).unwrap()[0].project_id, None);
        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn echo_sync_cursor_survives_restart() {
        let path = std::env::temp_dir().join(format!("opencore-cursor-{}.sqlite3", uuid::Uuid::new_v4()));
        let store = EventStore::open(&path).unwrap();
        assert_eq!(store.get_setting("echo_sync_cursor_v1_chat").unwrap(), None);
        store.set_setting("echo_sync_cursor_v1_chat", "42").unwrap();
        drop(store);
        let reopened = EventStore::open(&path).unwrap();
        assert_eq!(reopened.get_setting("echo_sync_cursor_v1_chat").unwrap(), Some("42".into()));
        drop(reopened);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn saved_app_server_thread_resumes_after_app_restart_without_mutating_legacy_sdk_state() {
        let root = std::env::temp_dir().join(format!("opencore-thread-map-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let path = root.join("history.sqlite3");
        let mapping = CodexThreadMapping {
            conversation_id: "conversation-a".into(),
            workspace_identity: "c:/work/project".into(),
            thread_id: "app-server-thread-1".into(),
            provider_id: "opencore-local".into(),
            model_id: "echo-local".into(),
            runtime_version: "0.160.0".into(),
            schema_hash: "schema-sha256".into(),
            migration_state: "legacy_sdk_unimported".into(),
            legacy_sdk_thread_id: Some("sdk-thread-preserved".into()),
        };
        {
            let store = EventStore::open(&path).unwrap();
            store.set_setting("codex_session:conversation-a:workspace", "sdk-thread-preserved").unwrap();
            store.save_codex_thread_mapping(&mapping).unwrap();
            assert_eq!(store.get_setting("codex_session:conversation-a:workspace").unwrap().as_deref(), Some("sdk-thread-preserved"));
        }
        let reopened = EventStore::open(&path).unwrap();
        assert_eq!(reopened.codex_thread_mapping("conversation-a", "c:/work/project").unwrap(), Some(mapping));
        assert_eq!(reopened.codex_thread_mapping("conversation-a", "c:/work/other").unwrap(), None);
        assert_eq!(reopened.get_setting("codex_session:conversation-a:workspace").unwrap().as_deref(), Some("sdk-thread-preserved"));
        drop(reopened);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn repeated_app_server_migration_preserves_original_files_timeline_ids_and_legacy_link() {
        let root = std::env::temp_dir().join(format!("opencore-thread-migrate-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join("codex")).unwrap();
        let legacy_rollout = root.join("codex").join("legacy-session.jsonl");
        std::fs::write(&legacy_rollout, b"{\"type\":\"assistant\",\"text\":\"keep me\"}\n").unwrap();
        let original_rollout_hash = crate::dev_tool::sha256(&std::fs::read(&legacy_rollout).unwrap());
        let path = root.join("history.sqlite3");
        let store = EventStore::open(&path).unwrap();
        store.ensure_conversation("conversation-b", "Codex", "echo", "Keep history").unwrap();
        store.add_timeline("conversation-b", "message", "assistant", "Codex SDK", "Assistant", "original reply", &json!({"source":"legacy"})).unwrap();
        let original_ids = store.conversation("conversation-b").unwrap().into_iter().map(|entry| entry.id).collect::<Vec<_>>();
        let mut mapping = CodexThreadMapping {
            conversation_id: "conversation-b".into(),
            workspace_identity: "c:/work/project".into(),
            thread_id: "native-thread".into(),
            provider_id: "opencore-local".into(),
            model_id: "echo-local".into(),
            runtime_version: "0.160.0".into(),
            schema_hash: "schema-sha256".into(),
            migration_state: "legacy_sdk_unimported".into(),
            legacy_sdk_thread_id: Some("legacy-sdk-thread".into()),
        };
        store.save_codex_thread_mapping(&mapping).unwrap();
        mapping.migration_state = "legacy_history_linked".into();
        store.save_codex_thread_mapping(&mapping).unwrap();
        let entries = store.conversation("conversation-b").unwrap();
        assert_eq!(entries.iter().map(|entry| entry.id).collect::<Vec<_>>(), original_ids);
        assert_eq!(entries[0].content, "original reply");
        assert_eq!(store.codex_thread_mapping("conversation-b", "c:/work/project").unwrap().unwrap().migration_state, "legacy_history_linked");
        assert!(legacy_rollout.is_file());
        assert_eq!(crate::dev_tool::sha256(&std::fs::read(&legacy_rollout).unwrap()), original_rollout_hash);
        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn changing_project_folder_and_concurrent_manual_move_survive_source_resync() {
        let root = std::env::temp_dir().join(format!("opencore-relink-{}", uuid::Uuid::new_v4()));
        let a = root.join("a");
        let b = root.join("b");
        std::fs::create_dir_all(&a).unwrap();
        std::fs::create_dir_all(&b).unwrap();
        let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
        store.ensure_conversation("chat", "Codex", "history", "Chat").unwrap();
        store.record_imported_directory("chat", Some(&a)).unwrap();
        let original = store.list_conversations(None).unwrap()[0].project_id.clone().unwrap();
        store.change_project_folder(&original, &b).unwrap();
        store.record_imported_directory("chat", Some(&a)).unwrap();
        assert_eq!(store.list_conversations(None).unwrap()[0].project_id.as_deref(), Some(original.as_str()));
        let other = store.create_project("Other", &a).unwrap();
        store.set_project_by_id("chat", Some(&other.id), ProjectAssignment::Manual).unwrap();
        assert!(!store.assign_imported_project_if_automatic("chat", &original).unwrap());
        assert_eq!(store.list_conversations(None).unwrap()[0].project_id.as_deref(), Some(other.id.as_str()));
        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn missing_linked_folder_is_reported_unavailable() {
        let root = std::env::temp_dir().join(format!("opencore-missing-linked-{}", uuid::Uuid::new_v4()));
        let folder = root.join("project");
        std::fs::create_dir_all(&folder).unwrap();
        let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
        let project = store.create_project("Project", &folder).unwrap();
        assert!(project.folder_available);
        std::fs::remove_dir(&folder).unwrap();
        let missing = store.list_projects().unwrap().into_iter().find(|p| p.id == project.id).unwrap();
        assert!(!missing.folder_available);
        assert!(!missing.needs_folder);
        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn ambiguous_legacy_project_stays_visible_unlinked() {
        let root = std::env::temp_dir().join(format!("opencore-legacy-ambiguous-{}", uuid::Uuid::new_v4()));
        let left = root.join("left").join("app");
        let right = root.join("right").join("app");
        std::fs::create_dir_all(&left).unwrap();
        std::fs::create_dir_all(&right).unwrap();
        let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
        for id in ["one", "two"] {
            store.ensure_conversation(id, "Codex", "history", id).unwrap();
            store.set_project(id, "app").unwrap();
        }
        store.record_imported_directory("one", Some(&left)).unwrap();
        store.record_imported_directory("two", Some(&right)).unwrap();
        store.reconcile_legacy_projects().unwrap();
        let project = store.list_projects().unwrap().into_iter().find(|p| p.name == "app").unwrap();
        assert!(project.needs_folder);
        assert_eq!(project.conversation_count, 2);
        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn folder_backfill_marker_is_persistent() {
        let root = std::env::temp_dir().join(format!("opencore-backfill-{}", uuid::Uuid::new_v4()));
        let path = root.join("history.sqlite3");
        let store = EventStore::open(&path).unwrap();
        assert!(!store.has_setting("folder_project_backfill_v1_codex").unwrap());
        store.set_setting("folder_project_backfill_v1_codex", "complete").unwrap();
        drop(store);
        let reopened = EventStore::open(&path).unwrap();
        assert!(reopened.has_setting("folder_project_backfill_v1_codex").unwrap());
        drop(reopened);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn custom_connector_is_persistent_testable_and_attributed() {
        let path =
            std::env::temp_dir().join(format!("opencore-store-{}.sqlite3", uuid::Uuid::new_v4()));
        let store = EventStore::open(&path).unwrap();
        let saved = store
            .upsert_connector(&ConnectorInput {
                id: None,
                name: "My Provider".into(),
                kind: "openai".into(),
                endpoint: "http://127.0.0.1:9000/v1".into(),
                match_pattern: "my-provider".into(),
            })
            .unwrap();
        assert!(saved.custom);
        assert!(!saved.observable);
        store.observe_client("My-Provider/2.0");
        let observed = store
            .connectors()
            .unwrap()
            .into_iter()
            .find(|item| item.id == saved.id)
            .unwrap();
        assert!(observed.observable);
        store.delete_connector(&saved.id).unwrap();
        assert!(!store
            .connectors()
            .unwrap()
            .into_iter()
            .any(|item| item.id == saved.id));
        drop(store);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn conversation_timeline_is_bounded_and_contains_event_types() {
        let path = std::env::temp_dir().join(format!(
            "opencore-timeline-{}.sqlite3",
            uuid::Uuid::new_v4()
        ));
        let store = EventStore::open(&path).unwrap();
        store
            .ensure_conversation("conversation", "test", "echo", "Performance")
            .unwrap();
        for index in 0..600 {
            let kind = if index % 2 == 0 {
                "thinking"
            } else {
                "tool_call"
            };
            store
                .add_timeline(
                    "conversation",
                    kind,
                    "assistant",
                    "test",
                    "event",
                    &format!("event-{index}"),
                    &json!({}),
                )
                .unwrap();
        }

        let conversation = store.conversation("conversation").unwrap();
        assert_eq!(conversation.len(), 500);
        assert_eq!(conversation.first().unwrap().content, "event-100");
        assert_eq!(conversation.last().unwrap().content, "event-599");

        assert!(conversation.iter().any(|entry| entry.kind == "thinking"));
        assert!(conversation.iter().any(|entry| entry.kind == "tool_call"));

        drop(store);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn imported_history_batches_exclude_native_continuations_and_progress_updates_in_place() {
        let path = std::env::temp_dir().join(format!("opencore-import-{}.sqlite3", uuid::Uuid::new_v4()));
        let store = EventStore::open(&path).unwrap();
        store.replace_imported_history("codex:one", "Codex", "Imported", &[
            ("2026-09-22T00:00:00Z".into(), "message".into(), "user".into(), "You".into(), "hi".into(), json!({})),
            ("2026-09-22T00:00:01Z".into(), "message".into(), "assistant".into(), "OpenCore".into(), "hello".into(), json!({})),
        ]).unwrap();
        store.add_timeline("codex:one", "message", "user", "OpenCore", "You", "continue", &json!({})).unwrap();
        assert_eq!(store.imported_message_count("codex:one").unwrap(), 2);
        let first = store.imported_messages_batch("codex:one", 0, 1).unwrap();
        assert_eq!(first.len(), 1);
        let source_id = first[0].metadata["opencore_source_event_id"].as_str().unwrap().to_string();
        let second = store.imported_messages_batch("codex:one", first[0].id, 1).unwrap();
        assert_eq!(second[0].content, "hello");
        assert!(store.imported_messages_batch("codex:one", second[0].id, 1).unwrap().is_empty());
        let progress = store.add_timeline("codex:one", "echo_import", "system", "OpenCore", "Preparing ECHO", "0/2", &json!({"current":0,"total":2})).unwrap();
        store.update_timeline(progress, "2/2", &json!({"current":2,"total":2,"status":"ready"})).unwrap();
        assert_eq!(store.conversation("codex:one").unwrap().last().unwrap().metadata["status"], "ready");
        store.replace_imported_history("codex:one", "Codex", "Imported", &[
            ("2026-09-22T00:00:00Z".into(), "message".into(), "user".into(), "You".into(), "hi".into(), json!({})),
            ("2026-09-22T00:00:01Z".into(), "message".into(), "assistant".into(), "OpenCore".into(), "hello".into(), json!({})),
        ]).unwrap();
        assert_eq!(store.imported_messages_batch("codex:one", 0, 1).unwrap()[0].metadata["opencore_source_event_id"], source_id);
        drop(store);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn imported_history_can_be_filtered_and_cleared_by_source_and_sync_cancelled() {
        let path = std::env::temp_dir().join(format!("opencore-import-cleanup-{}.sqlite3", uuid::Uuid::new_v4()));
        let store = EventStore::open(&path).unwrap();
        let row = |text: &str| vec![("2026-09-22T00:00:00Z".into(), "message".into(), "user".into(), "User".into(), text.into(), json!({}))];
        store.replace_imported_history("codex:one", "Codex", "Codex session", &row("codex transcript")).unwrap();
        store.replace_imported_history("claude:one", "Claude Code", "Claude session", &row("claude transcript")).unwrap();
        store.add_timeline("codex:one", "message", "user", "OpenCore", "User", "local continuation", &json!({})).unwrap();
        assert_eq!(store.imported_conversation_ids_for_client("codex").unwrap(), vec!["codex:one"]);
        assert_eq!(store.imported_conversation_ids_for_client("claude-code").unwrap(), vec!["claude:one"]);
        assert_eq!(store.clear_imported_history("codex").unwrap(), vec!["codex:one"]);
        assert!(store.imported_conversation_ids_for_client("codex").unwrap().is_empty());
        assert!(store.conversation("codex:one").unwrap().is_empty());
        assert_eq!(store.conversation("claude:one").unwrap().len(), 1);

        let operation = store.start_operation("history_sync", "codex").unwrap();
        store.request_operation_cancel(&operation.id).unwrap();
        store.finish_cancelled_operation(&operation.id, 1, 4, 1, 0, 0).unwrap();
        let finished = store.list_operations().unwrap().into_iter().find(|item| item.id == operation.id).unwrap();
        assert_eq!(finished.status, "cancelled");
        assert_eq!((finished.current, finished.total), (1, 4));
        drop(store);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn projects_and_pins_are_persistent_and_ordered() {
        let path = std::env::temp_dir().join(format!(
            "opencore-projects-{}.sqlite3",
            uuid::Uuid::new_v4()
        ));
        let store = EventStore::open(&path).unwrap();
        store
            .ensure_conversation("older", "Codex", "history", "Older pinned")
            .unwrap();
        store
            .ensure_conversation("newer", "OpenCore", "echo", "Newer")
            .unwrap();
        let project = store.create_project("Agent work", path.parent().unwrap()).unwrap();
        store.set_project("older", &project.name).unwrap();
        store.set_conversation_pinned("older", true).unwrap();

        let conversations = store.list_conversations(None).unwrap();
        assert_eq!(conversations[0].id, "older");
        assert!(conversations[0].pinned);
        assert_eq!(conversations[0].project, "Agent work");
        assert_eq!(conversations[0].project_id.as_deref(), Some(project.id.as_str()));
        assert_eq!(store.list_projects().unwrap()[0].conversation_count, 1);

        drop(store);
        let reopened = EventStore::open(&path).unwrap();
        let persisted = reopened.list_conversations(None).unwrap();
        assert!(persisted[0].pinned);
        assert_eq!(persisted[0].project, "Agent work");
        drop(reopened);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn project_rename_and_delete_keep_conversations_and_pins() {
        let path = std::env::temp_dir().join(format!(
            "opencore-project-lifecycle-{}.sqlite3", uuid::Uuid::new_v4()
        ));
        let store = EventStore::open(&path).unwrap();
        store.ensure_conversation("chat", "OpenCore", "echo", "Keep me").unwrap();
        store.add_timeline("chat", "message", "user", "OpenCore", "User", "Hello", &json!({})).unwrap();
        let project = store.create_project("Original", path.parent().unwrap()).unwrap();
        store.set_project("chat", &project.name).unwrap();
        store.set_conversation_pinned("chat", true).unwrap();
        let renamed = store.rename_project(&project.id, "Renamed").unwrap();
        assert_eq!(renamed.id, project.id);
        assert_eq!(renamed.conversation_count, 1);
        assert_eq!(store.list_conversations(None).unwrap()[0].project, "Renamed");
        assert_eq!(store.delete_project(&project.id).unwrap(), 1);
        assert!(store.list_projects().unwrap().is_empty());
        drop(store);
        let reopened = EventStore::open(&path).unwrap();
        let conversation = &reopened.list_conversations(None).unwrap()[0];
        assert_eq!(conversation.title, "Keep me");
        assert_eq!(conversation.project, "");
        assert!(conversation.project_id.is_none());
        assert!(conversation.pinned);
        assert_eq!(reopened.conversation("chat").unwrap().len(), 1);
        drop(reopened);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn operations_keep_progress_results_and_interrupted_state_across_restart() {
        let path = std::env::temp_dir().join(format!(
            "opencore-operations-{}.sqlite3", uuid::Uuid::new_v4()
        ));
        let store = EventStore::open(&path).unwrap();
        let codex = store.start_operation("history_sync", "codex").unwrap();
        assert!(store.start_operation("history_sync", "codex").is_err());
        let claude = store.start_operation("history_sync", "claude-code").unwrap();
        store.update_operation(&codex.id, "Importing transcripts", 3, 10, 1, 1, 1).unwrap();
        store.finish_operation(&codex.id, "Imported 1 · Updated 1 · Skipped 8", None, 10, 10, 1, 1, 8).unwrap();
        store.connection.lock().unwrap().execute(
            "UPDATE operations SET owner_pid=999999 WHERE id=?1", [&claude.id]
        ).unwrap();
        drop(store);
        let reopened = EventStore::open(&path).unwrap();
        let records = reopened.list_operations().unwrap();
        let completed = records.iter().find(|record| record.id == codex.id).unwrap();
        assert_eq!(completed.status, "completed");
        assert_eq!((completed.current, completed.total, completed.skipped), (10, 10, 8));
        assert!(completed.finished_at.is_some());
        assert_eq!(completed.last_progress_at, completed.finished_at.clone().unwrap());
        assert!(completed.last_progress_at >= completed.started_at);
        let interrupted = records.iter().find(|record| record.id == claude.id).unwrap();
        assert_eq!(interrupted.status, "failed");
        assert!(interrupted.error.as_deref().unwrap().contains("app closed"));
        assert!(reopened.start_operation("history_sync", "claude-code").is_ok());
        drop(reopened);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn older_operations_schema_migrates_progress_timestamp_from_start_time() {
        let path = std::env::temp_dir().join(format!("opencore-operation-progress-{}.sqlite3", uuid::Uuid::new_v4()));
        {
            let connection = Connection::open(&path).unwrap();
            connection.execute_batch("CREATE TABLE operations (
                id TEXT PRIMARY KEY,kind TEXT NOT NULL,target TEXT NOT NULL,phase TEXT NOT NULL,status TEXT NOT NULL,
                current INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,imported INTEGER NOT NULL DEFAULT 0,
                updated INTEGER NOT NULL DEFAULT 0,skipped INTEGER NOT NULL DEFAULT 0,summary TEXT NOT NULL DEFAULT '',
                error TEXT,started_at TEXT NOT NULL,finished_at TEXT,owner_pid INTEGER NOT NULL DEFAULT 0);
                INSERT INTO operations(id,kind,target,phase,status,started_at,owner_pid)
                VALUES('legacy','history_sync','codex','Indexing exact history in ECHO','completed','2026-10-01T00:00:00Z',0);").unwrap();
        }
        let store = EventStore::open(&path).unwrap();
        let legacy = store.list_operations().unwrap().into_iter().find(|item| item.id == "legacy").unwrap();
        assert_eq!(legacy.last_progress_at, "2026-10-01T00:00:00Z");
        drop(store);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn code_artifacts_only_lists_files_from_the_selected_conversation() {
        let path = std::env::temp_dir().join(format!("opencore-code-history-{}.sqlite3", uuid::Uuid::new_v4()));
        let store = EventStore::open(&path).unwrap();
        for id in ["first", "second"] { store.ensure_conversation(id, "OpenCore", "echo", id).unwrap(); }
        store.add_timeline("first", "file", "assistant", "OpenCore", "game.py", "artifact://one",
            &json!({"id":"one","name":"game.py","size":100})).unwrap();
        store.add_timeline("second", "file", "assistant", "OpenCore", "other.py", "artifact://two",
            &json!({"id":"two","name":"other.py","size":20})).unwrap();
        assert_eq!(store.code_artifacts("first").unwrap(), vec![json!({"id":"one","name":"game.py","size":100})]);
        drop(store);
        let _ = std::fs::remove_file(path);
    }
}

impl EventStore {
    pub fn get_setting(&self, key: &str) -> Result<Option<String>, String> {
        self.connection.lock().map_err(|e| e.to_string())?.query_row(
            "SELECT value FROM settings WHERE key=?1", [key], |row| row.get(0)
        ).optional().map_err(|e| e.to_string())
    }

    pub fn has_setting(&self, key: &str) -> Result<bool, String> {
        self.connection.lock().map_err(|e| e.to_string())?.query_row(
            "SELECT EXISTS(SELECT 1 FROM settings WHERE key=?1)", [key], |row| row.get(0)
        ).map_err(|e| e.to_string())
    }

    pub fn set_setting(&self, key: &str, value: &str) -> Result<(), String> {
        self.connection.lock().map_err(|e| e.to_string())?.execute(
            "INSERT INTO settings(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            params![key, value],
        ).map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn codex_thread_mapping(
        &self,
        conversation_id: &str,
        workspace_identity: &str,
    ) -> Result<Option<CodexThreadMapping>, String> {
        let key = codex_thread_mapping_key(conversation_id, workspace_identity);
        let Some(value) = self.get_setting(&key)? else { return Ok(None); };
        let mapping: CodexThreadMapping = serde_json::from_str(&value)
            .map_err(|error| format!("Saved Codex app-server thread mapping is invalid: {error}"))?;
        if mapping.conversation_id != conversation_id || mapping.workspace_identity != workspace_identity {
            return Err("Saved Codex app-server thread mapping does not match its conversation/workspace key".into());
        }
        Ok(Some(mapping))
    }

    pub fn save_codex_thread_mapping(&self, mapping: &CodexThreadMapping) -> Result<(), String> {
        for (label, value) in [
            ("conversation ID", mapping.conversation_id.as_str()),
            ("workspace identity", mapping.workspace_identity.as_str()),
            ("thread ID", mapping.thread_id.as_str()),
            ("provider ID", mapping.provider_id.as_str()),
            ("model ID", mapping.model_id.as_str()),
            ("runtime version", mapping.runtime_version.as_str()),
            ("schema hash", mapping.schema_hash.as_str()),
            ("migration state", mapping.migration_state.as_str()),
        ] {
            if value.trim().is_empty() || value.len() > 32_768 || value.chars().any(char::is_control) {
                return Err(format!("Codex thread mapping has an invalid {label}"));
            }
        }
        let key = codex_thread_mapping_key(&mapping.conversation_id, &mapping.workspace_identity);
        let value = serde_json::to_string(mapping).map_err(|error| error.to_string())?;
        let mut connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        transaction.execute(
            "INSERT INTO settings(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            params![key, value],
        ).map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(())
    }

    pub fn get_or_create_pairing_token(&self, key: &str) -> Result<String, String> {
        let mut connection = self.connection.lock().map_err(|e| e.to_string())?;
        let transaction = connection.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
            .map_err(|e| e.to_string())?;
        let saved: Option<String> = transaction.query_row(
            "SELECT value FROM settings WHERE key=?1", [key], |row| row.get(0)
        ).optional().map_err(|e| e.to_string())?;
        let token = match saved {
            Some(value) if uuid::Uuid::parse_str(&value).is_ok_and(|id|
                id.get_version_num() == 4 && id.get_variant() == uuid::Variant::RFC4122) => value,
            _ => {
                let value = uuid::Uuid::new_v4().to_string();
                transaction.execute(
                    "INSERT INTO settings(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    params![key, value],
                ).map_err(|e| e.to_string())?;
                value
            }
        };
        transaction.commit().map_err(|e| e.to_string())?;
        Ok(token)
    }

    pub fn open(path: &Path) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let mut connection = Connection::open(path).map_err(|e| e.to_string())?;
        connection
            .execute_batch(
                "PRAGMA journal_mode=WAL;
                 PRAGMA synchronous=NORMAL;
                 CREATE TABLE IF NOT EXISTS conversations (
                   id TEXT PRIMARY KEY,
                   title TEXT NOT NULL,
                   client TEXT NOT NULL,
                   profile TEXT NOT NULL,
                   status TEXT NOT NULL,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL
                 );
                 CREATE TABLE IF NOT EXISTS timeline (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   conversation_id TEXT NOT NULL,
                   timestamp TEXT NOT NULL,
                   kind TEXT NOT NULL,
                   role TEXT NOT NULL,
                   source TEXT NOT NULL,
                   title TEXT NOT NULL,
                   content TEXT NOT NULL,
                   metadata TEXT NOT NULL
                 );
                 CREATE INDEX IF NOT EXISTS timeline_conversation_time
                   ON timeline(conversation_id, timestamp, id);
                 CREATE INDEX IF NOT EXISTS timeline_conversation_kind_id
                   ON timeline(conversation_id, kind, id);
                 CREATE TABLE IF NOT EXISTS logs (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   timestamp TEXT NOT NULL,
                   level TEXT NOT NULL,
                   source TEXT NOT NULL,
                   message TEXT NOT NULL
                 );
                 CREATE INDEX IF NOT EXISTS logs_time ON logs(timestamp, id);
                 CREATE TABLE IF NOT EXISTS settings (
                   key TEXT PRIMARY KEY,
                   value TEXT NOT NULL
                 );
                 CREATE TABLE IF NOT EXISTS connectors (
                   id TEXT PRIMARY KEY,
                   name TEXT NOT NULL,
                   kind TEXT NOT NULL,
                   endpoint TEXT NOT NULL,
                   match_pattern TEXT NOT NULL,
                   custom INTEGER NOT NULL DEFAULT 1,
                   last_seen TEXT
                 );
                 CREATE TABLE IF NOT EXISTS projects (
                   id TEXT PRIMARY KEY,
                   name TEXT NOT NULL,
                   folder_path TEXT,
                   folder_key TEXT,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL
                 );
                 CREATE TABLE IF NOT EXISTS operations (
                   id TEXT PRIMARY KEY,
                   kind TEXT NOT NULL,
                   target TEXT NOT NULL,
                   phase TEXT NOT NULL,
                   status TEXT NOT NULL,
                   current INTEGER NOT NULL DEFAULT 0,
                   total INTEGER NOT NULL DEFAULT 0,
                   imported INTEGER NOT NULL DEFAULT 0,
                   updated INTEGER NOT NULL DEFAULT 0,
                   skipped INTEGER NOT NULL DEFAULT 0,
                   summary TEXT NOT NULL DEFAULT '',
                   error TEXT,
                   started_at TEXT NOT NULL,
                   finished_at TEXT,
                   last_progress_at TEXT NOT NULL DEFAULT '',
                   owner_pid INTEGER NOT NULL DEFAULT 0
                 );
                 CREATE INDEX IF NOT EXISTS operations_target_started
                   ON operations(kind,target,started_at DESC);
                 CREATE UNIQUE INDEX IF NOT EXISTS operations_one_active_target
                   ON operations(kind,target) WHERE status IN ('queued','running');
                 INSERT OR IGNORE INTO connectors(id,name,kind,endpoint,match_pattern,custom)
                   VALUES
                   ('unsloth','Unsloth','unsloth','http://127.0.0.1:8888','unsloth',0),
                   ('lmstudio','LM Studio','lmstudio','http://127.0.0.1:1234','lm studio',0),
                   ('ollama','Ollama','ollama','http://127.0.0.1:11434','ollama',0),
                   ('llamacpp','llama.cpp','openai','http://127.0.0.1:8080/v1','llama',0),
                   ('vllm','vLLM','openai','http://127.0.0.1:8000/v1','vllm',0),
                   ('localai','LocalAI','openai','http://127.0.0.1:8080/v1','localai',0),
                   ('claude-code','Claude Code','history','local://claude-code','claude',0),
                   ('codex','Codex','history','local://codex','codex',0);",
            )
            .map_err(|e| e.to_string())?;
        let project_columns = {
            let mut statement = connection.prepare("PRAGMA table_info(projects)").map_err(|e| e.to_string())?;
            let rows = statement.query_map([], |row| row.get::<_, String>(1)).map_err(|e| e.to_string())?;
            rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?
        };
        if !project_columns.iter().any(|column| column == "folder_key") {
            let transaction = connection.transaction().map_err(|e| e.to_string())?;
            transaction.execute_batch(
                "CREATE TABLE projects_migrated (
                   id TEXT PRIMARY KEY, name TEXT NOT NULL, folder_path TEXT, folder_key TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                 );
                 INSERT INTO projects_migrated(id,name,created_at,updated_at)
                   SELECT id,name,created_at,updated_at FROM projects;
                 DROP TABLE projects;
                 ALTER TABLE projects_migrated RENAME TO projects;"
            ).map_err(|e| e.to_string())?;
            transaction.commit().map_err(|e| e.to_string())?;
        }
        connection.execute_batch(
            "CREATE UNIQUE INDEX IF NOT EXISTS projects_folder_key_unique
               ON projects(folder_key) WHERE folder_key IS NOT NULL;"
        ).map_err(|e| e.to_string())?;
        let has_project = {
            let mut statement = connection.prepare("PRAGMA table_info(conversations)").map_err(|e| e.to_string())?;
            let columns = statement.query_map([], |row| row.get::<_, String>(1)).map_err(|e| e.to_string())?;
            let found = columns.flatten().any(|name| name == "project");
            found
        };
        if !has_project {
            connection.execute("ALTER TABLE conversations ADD COLUMN project TEXT NOT NULL DEFAULT ''", [])
                .map_err(|e| e.to_string())?;
        }
        let conversation_columns = {
            let mut statement = connection
                .prepare("PRAGMA table_info(conversations)")
                .map_err(|e| e.to_string())?;
            let columns = statement
                .query_map([], |row| row.get::<_, String>(1))
                .map_err(|e| e.to_string())?
                .flatten()
                .collect::<Vec<_>>();
            columns
        };
        if !conversation_columns.iter().any(|name| name == "project_id") {
            connection
                .execute("ALTER TABLE conversations ADD COLUMN project_id TEXT", [])
                .map_err(|e| e.to_string())?;
        }
        if !conversation_columns.iter().any(|name| name == "pinned") {
            connection
                .execute(
                    "ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
                    [],
                )
                .map_err(|e| e.to_string())?;
        }
        if !conversation_columns.iter().any(|name| name == "source_cwd") {
            connection.execute("ALTER TABLE conversations ADD COLUMN source_cwd TEXT", [])
                .map_err(|e| e.to_string())?;
        }
        if !conversation_columns.iter().any(|name| name == "project_assignment") {
            connection.execute("ALTER TABLE conversations ADD COLUMN project_assignment TEXT NOT NULL DEFAULT 'legacy'", [])
                .map_err(|e| e.to_string())?;
        }
        let operation_columns = {
            let mut statement = connection.prepare("PRAGMA table_info(operations)").map_err(|e| e.to_string())?;
            let rows = statement.query_map([], |row| row.get::<_, String>(1))
                .map_err(|e| e.to_string())?;
            rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?
        };
        if !operation_columns.iter().any(|name| name == "owner_pid") {
            connection.execute("ALTER TABLE operations ADD COLUMN owner_pid INTEGER NOT NULL DEFAULT 0", [])
                .map_err(|e| e.to_string())?;
        }
        if !operation_columns.iter().any(|name| name == "last_progress_at") {
            connection.execute("ALTER TABLE operations ADD COLUMN last_progress_at TEXT NOT NULL DEFAULT ''", [])
                .map_err(|e| e.to_string())?;
            connection.execute("UPDATE operations SET last_progress_at=started_at WHERE last_progress_at=''", [])
                .map_err(|e| e.to_string())?;
        }
        let now = Utc::now().to_rfc3339();
        connection
            .execute(
                "INSERT OR IGNORE INTO projects(id,name,created_at,updated_at)
                 SELECT 'legacy-' || lower(hex(c.project)), c.project, ?1, ?1
                 FROM conversations c
                 WHERE trim(c.project) <> ''
                   AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.name=c.project COLLATE NOCASE)
                 GROUP BY lower(c.project)",
                [&now],
            )
            .map_err(|e| e.to_string())?;
        connection
            .execute(
                "UPDATE conversations
                 SET project_id=(SELECT id FROM projects WHERE projects.name=conversations.project)
                 WHERE project_id IS NULL AND trim(project) <> ''",
                [],
            )
            .map_err(|e| e.to_string())?;
        let active_owners = {
            let mut statement = connection.prepare(
                "SELECT id,owner_pid FROM operations WHERE status IN ('queued','running')"
            ).map_err(|e| e.to_string())?;
            let rows = statement.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, u32>(1)?)))
                .map_err(|e| e.to_string())?;
            rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?
        };
        if !active_owners.is_empty() {
            let mut processes = System::new();
            processes.refresh_processes(ProcessesToUpdate::All, true);
            for (id, owner_pid) in active_owners {
                if owner_pid == 0 || processes.process(Pid::from_u32(owner_pid)).is_none() {
                    connection.execute(
                        "UPDATE operations SET status='failed',phase='Interrupted',
                         error='The app closed before this operation finished',finished_at=?2 WHERE id=?1",
                        params![id, now],
                    ).map_err(|e| e.to_string())?;
                }
            }
        }
        let store = Self { connection: Mutex::new(connection) };
        store.cleanup_internal_context()?;
        Ok(store)
    }


    fn cleanup_internal_context(&self) -> Result<(), String> {
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        connection.execute(
            "DELETE FROM timeline
             WHERE kind='message' AND role='user' AND (
               content LIKE '<recommended_plugins>%' OR
               content LIKE '<environment_context>%' OR
               content LIKE '<permissions instructions>%' OR
               content LIKE '<collaboration_mode>%' OR
               content LIKE '<skills_instructions>%' OR
               content LIKE '<local-command-caveat>%' OR
               content LIKE '<command-name>%' OR
               content LIKE '<command-message>%' OR
               content LIKE '<command-args>%' OR
               content LIKE '<COS_CONTEXT:%'
             )",
            [],
        ).map_err(|e| e.to_string())?;
        connection.execute(
            "UPDATE conversations
             SET title=COALESCE(
               NULLIF(substr((
                 SELECT t.content FROM timeline t
                 WHERE t.conversation_id=conversations.id
                   AND t.kind='message' AND t.role='user'
                   AND trim(t.content) <> '' AND t.content NOT LIKE '<%'
                 ORDER BY t.id LIMIT 1
               ),1,72),''),
               'Imported conversation'
             )
             WHERE title='Imported conversation'
                OR title LIKE '<recommended_plugins>%'
                OR title LIKE '<environment_context>%'
                OR title LIKE '<permissions instructions>%'
                OR title LIKE '<collaboration_mode>%'
                OR title LIKE '<skills_instructions>%'
                OR title LIKE '<local-command-caveat>%'
                OR title LIKE '<command-name>%'
                OR title LIKE '<command-message>%'
                OR title LIKE '<command-args>%'
                OR title LIKE '<COS_CONTEXT:%'",
            [],
        ).map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn set_project(&self, id: &str, project: &str) -> Result<(), String> {
        let project = redact_text(project).trim().to_string();
        let mut connection = self.connection.lock().map_err(|e| e.to_string())?;
        let transaction = connection.transaction().map_err(|e| e.to_string())?;
        let project_id = if project.is_empty() {
            None
        } else {
            let existing = transaction
                .query_row(
                    "SELECT id FROM projects WHERE name=?1 COLLATE NOCASE",
                    [&project],
                    |row| row.get::<_, String>(0),
                )
                .ok();
            let project_id = existing.unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
            transaction
                .execute(
                    "INSERT OR IGNORE INTO projects(id,name,created_at,updated_at) VALUES(?1,?2,?3,?3)",
                    params![project_id, project, Utc::now().to_rfc3339()],
                )
                .map_err(|e| e.to_string())?;
            Some(project_id)
        };
        transaction
            .execute(
                "UPDATE conversations SET project=?2,project_id=?3,updated_at=?4 WHERE id=?1",
                params![id, project, project_id, Utc::now().to_rfc3339()],
            )
            .map_err(|e| e.to_string())?;
        transaction.commit().map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn set_project_by_id(&self, id: &str, project_id: Option<&str>, assignment: ProjectAssignment) -> Result<(), String> {
        let mut connection = self.connection.lock().map_err(|e| e.to_string())?;
        let transaction = connection.transaction().map_err(|e| e.to_string())?;
        let project_name = match project_id {
            Some(project_id) => Some(transaction.query_row(
                "SELECT name FROM projects WHERE id=?1", [project_id], |row| row.get::<_, String>(0)
            ).optional().map_err(|e| e.to_string())?
                .ok_or_else(|| "Project no longer exists".to_string())?),
            None => None,
        };
        let changed = transaction.execute(
            "UPDATE conversations SET project=?2,project_id=?3,project_assignment=?4,updated_at=?5 WHERE id=?1",
            params![id, project_name.unwrap_or_default(), project_id, assignment.as_str(), Utc::now().to_rfc3339()],
        ).map_err(|e| e.to_string())?;
        if changed == 0 { return Err("Conversation no longer exists".into()); }
        transaction.commit().map_err(|e| e.to_string())?;
        Ok(())
    }

    /// Import updates may race with a user move. Only provenance-controlled chats may be relinked.
    pub fn assign_imported_project_if_automatic(&self, id: &str, project_id: &str) -> Result<bool, String> {
        let mut connection = self.connection.lock().map_err(|e| e.to_string())?;
        let transaction = connection.transaction().map_err(|e| e.to_string())?;
        let name: String = transaction.query_row("SELECT name FROM projects WHERE id=?1", [project_id], |row| row.get(0))
            .map_err(|e| e.to_string())?;
        let changed = transaction.execute(
            "UPDATE conversations SET project=?2,project_id=?3,project_assignment='automatic',updated_at=?4
             WHERE id=?1 AND (project_assignment='automatic' OR (project_assignment='legacy' AND project_id IS NULL))",
            params![id, name, project_id, Utc::now().to_rfc3339()],
        ).map_err(|e| e.to_string())?;
        transaction.commit().map_err(|e| e.to_string())?;
        Ok(changed > 0)
    }

    pub fn ensure_project_for_directory(&self, folder: &Path) -> Result<ProjectSummary, String> {
        let (folder_path, folder_key) = canonical_existing_directory(folder)?;
        let name = Path::new(&folder_path).file_name().and_then(|part| part.to_str())
            .filter(|part| !part.is_empty()).unwrap_or(&folder_path).to_string();
        let id = uuid::Uuid::new_v4().to_string();
        let now = Utc::now().to_rfc3339();
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        connection.execute(
            "INSERT OR IGNORE INTO projects(id,name,folder_path,folder_key,created_at,updated_at)
             VALUES(?1,?2,?3,?4,?5,?5)",
            params![id, name, folder_path, folder_key, now],
        ).map_err(|e| e.to_string())?;
        let project_id: String = connection.query_row(
            "SELECT id FROM projects WHERE folder_key=?1", [&folder_key], |row| row.get(0)
        ).map_err(|e| e.to_string())?;
        drop(connection);
        self.list_projects()?.into_iter().find(|project| project.id == project_id)
            .ok_or_else(|| "Linked project could not be read".into())
    }

    pub fn record_imported_directory(&self, conversation_id: &str, cwd: Option<&Path>) -> Result<(), String> {
        let raw = cwd.map(|path| path.to_string_lossy().to_string());
        let (assignment, current_project): (String, Option<String>) = {
            let connection = self.connection.lock().map_err(|e| e.to_string())?;
            let state = connection.query_row(
                "SELECT project_assignment,project_id FROM conversations WHERE id=?1", [conversation_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            ).map_err(|e| e.to_string())?;
            connection.execute("UPDATE conversations SET source_cwd=?2 WHERE id=?1", params![conversation_id, raw])
                .map_err(|e| e.to_string())?;
            state
        };
        let Some(folder) = cwd else { return Ok(()); };
        // Validate even for a manually assigned chat so sync can report a stale source folder.
        canonical_existing_directory(folder)?;
        if assignment == "manual" || (assignment == "legacy" && current_project.is_some()) {
            return Ok(());
        }
        let project = self.ensure_project_for_directory(folder)?;
        self.assign_imported_project_if_automatic(conversation_id, &project.id).map(|_| ())
    }

    pub fn reconcile_legacy_projects(&self) -> Result<(), String> {
        let legacy_ids = {
            let connection = self.connection.lock().map_err(|e| e.to_string())?;
            let mut statement = connection.prepare("SELECT id,name FROM projects WHERE folder_key IS NULL")
                .map_err(|e| e.to_string())?;
            let rows = statement.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
                .map_err(|e| e.to_string())?;
            rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?
        };
        for (legacy_id, legacy_name) in legacy_ids {
            let members = {
                let connection = self.connection.lock().map_err(|e| e.to_string())?;
                let mut statement = connection.prepare(
                    "SELECT id,source_cwd,project_assignment FROM conversations WHERE project_id=?1 ORDER BY id"
                ).map_err(|e| e.to_string())?;
                let rows = statement.query_map([&legacy_id], |row| Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?, row.get::<_, String>(2)?)))
                    .map_err(|e| e.to_string())?;
                rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?
            };
            if members.is_empty() || members.iter().any(|(_, cwd, assignment)| cwd.is_none() || assignment != "legacy") {
                continue;
            }
            let canonical = members.iter().map(|(_, cwd, _)| {
                canonical_existing_directory(Path::new(cwd.as_deref().unwrap_or_default()))
            }).collect::<Result<Vec<_>, _>>();
            let Ok(canonical) = canonical else { continue; };
            let (folder_path, folder_key) = &canonical[0];
            if canonical.iter().any(|(_, key)| key != folder_key) { continue; }
            let basename = Path::new(folder_path).file_name().and_then(|part| part.to_str()).unwrap_or("");
            if !legacy_name.eq_ignore_ascii_case(basename) { continue; }
            let mut connection = self.connection.lock().map_err(|e| e.to_string())?;
            let transaction = connection.transaction().map_err(|e| e.to_string())?;
            let still_legacy: bool = transaction.query_row(
                "SELECT EXISTS(SELECT 1 FROM projects WHERE id=?1 AND folder_key IS NULL)",
                [&legacy_id], |row| row.get(0),
            ).map_err(|e| e.to_string())?;
            if !still_legacy { continue; }
            let current_members = {
                let mut statement = transaction.prepare(
                    "SELECT id,source_cwd,project_assignment FROM conversations WHERE project_id=?1 ORDER BY id"
                ).map_err(|e| e.to_string())?;
                let rows = statement.query_map([&legacy_id], |row| Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?, row.get::<_, String>(2)?)))
                    .map_err(|e| e.to_string())?;
                rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())?
            };
            if current_members != members { continue; }
            let existing: Option<String> = transaction.query_row(
                "SELECT id FROM projects WHERE folder_key=?1", [folder_key], |row| row.get(0)
            ).optional().map_err(|e| e.to_string())?;
            if let Some(linked_id) = existing {
                let linked_name: String = transaction.query_row("SELECT name FROM projects WHERE id=?1", [&linked_id], |row| row.get(0))
                    .map_err(|e| e.to_string())?;
                transaction.execute("UPDATE conversations SET project_id=?2,project=?3,project_assignment='automatic' WHERE project_id=?1 AND project_assignment='legacy'",
                    params![legacy_id, linked_id, linked_name]).map_err(|e| e.to_string())?;
                transaction.execute("DELETE FROM projects WHERE id=?1", [&legacy_id]).map_err(|e| e.to_string())?;
            } else {
                transaction.execute("UPDATE projects SET folder_path=?2,folder_key=?3 WHERE id=?1",
                    params![legacy_id, folder_path, folder_key]).map_err(|e| e.to_string())?;
                transaction.execute("UPDATE conversations SET project_assignment='automatic' WHERE project_id=?1 AND project_assignment='legacy'",
                    [&legacy_id]).map_err(|e| e.to_string())?;
            }
            transaction.commit().map_err(|e| e.to_string())?;
        }
        Ok(())
    }

    pub fn change_project_folder(&self, id: &str, folder: &Path) -> Result<ProjectSummary, String> {
        let (folder_path, folder_key) = canonical_existing_directory(folder)?;
        let mut connection = self.connection.lock().map_err(|e| e.to_string())?;
        let transaction = connection.transaction().map_err(|e| e.to_string())?;
        let changed = transaction.execute(
            "UPDATE projects SET folder_path=?2,folder_key=?3,updated_at=?4 WHERE id=?1",
            params![id, folder_path, folder_key, Utc::now().to_rfc3339()],
        ).map_err(|e| if e.to_string().contains("UNIQUE") {
            format!("This folder is already linked to another project: {folder_path}")
        } else { e.to_string() })?;
        if changed == 0 { return Err("Project no longer exists".into()); }
        transaction.execute(
            "UPDATE conversations SET project_assignment='manual' WHERE project_id=?1",
            [id],
        ).map_err(|e| e.to_string())?;
        transaction.commit().map_err(|e| e.to_string())?;
        drop(connection);
        self.list_projects()?.into_iter().find(|project| project.id == id)
            .ok_or_else(|| "Updated project could not be read".into())
    }

    pub fn set_conversation_pinned(&self, id: &str, pinned: bool) -> Result<(), String> {
        self.connection
            .lock()
            .map_err(|e| e.to_string())?
            .execute(
                "UPDATE conversations SET pinned=?2,updated_at=?3 WHERE id=?1",
                params![id, pinned as i64, Utc::now().to_rfc3339()],
            )
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn create_project(&self, name: &str, folder: &Path) -> Result<ProjectSummary, String> {
        let name = redact_text(name).trim().to_string();
        if name.is_empty() {
            return Err("Project name cannot be empty".into());
        }
        let (folder_path, folder_key) = canonical_existing_directory(folder)?;
        let id = uuid::Uuid::new_v4().to_string();
        let now = Utc::now().to_rfc3339();
        self.connection
            .lock()
            .map_err(|e| e.to_string())?
            .execute(
                "INSERT INTO projects(id,name,folder_path,folder_key,created_at,updated_at) VALUES(?1,?2,?3,?4,?5,?5)",
                params![id, name, folder_path, folder_key, now],
            )
            .map_err(|e| {
                if e.to_string().contains("UNIQUE") {
                    format!("This folder is already linked to an OpenCore project: {folder_path}")
                } else {
                    e.to_string()
                }
            })?;
        Ok(ProjectSummary {
            id,
            name,
            folder_path: Some(folder_path),
            needs_folder: false,
            folder_available: true,
            created_at: now.clone(),
            updated_at: now,
            conversation_count: 0,
        })
    }

    pub fn rename_project(&self, id: &str, name: &str) -> Result<ProjectSummary, String> {
        let name = redact_text(name).trim().to_string();
        if name.is_empty() {
            return Err("Project name cannot be empty".into());
        }
        let now = Utc::now().to_rfc3339();
        let mut connection = self.connection.lock().map_err(|e| e.to_string())?;
        let transaction = connection.transaction().map_err(|e| e.to_string())?;
        let changed = transaction.execute(
            "UPDATE projects SET name=?2,updated_at=?3 WHERE id=?1",
            params![id, name, now],
        ).map_err(|e| e.to_string())?;
        if changed == 0 { return Err("Project no longer exists".into()); }
        transaction.execute(
            "UPDATE conversations SET project=?2 WHERE project_id=?1",
            params![id, name],
        ).map_err(|e| e.to_string())?;
        transaction.commit().map_err(|e| e.to_string())?;
        drop(connection);
        self.list_projects()?.into_iter().find(|project| project.id == id)
            .ok_or_else(|| "Renamed project could not be read".into())
    }

    /// Remove only the folder. Its conversations and timeline remain intact and unfiled.
    pub fn delete_project(&self, id: &str) -> Result<u64, String> {
        let now = Utc::now().to_rfc3339();
        let mut connection = self.connection.lock().map_err(|e| e.to_string())?;
        let transaction = connection.transaction().map_err(|e| e.to_string())?;
        let exists: bool = transaction.query_row(
            "SELECT EXISTS(SELECT 1 FROM projects WHERE id=?1)", [id], |row| row.get(0),
        ).map_err(|e| e.to_string())?;
        if !exists { return Err("Project no longer exists".into()); }
        let unfiled = transaction.execute(
            "UPDATE conversations SET project='',project_id=NULL,project_assignment='manual',updated_at=?2 WHERE project_id=?1",
            params![id, now],
        ).map_err(|e| e.to_string())?;
        transaction.execute("DELETE FROM projects WHERE id=?1", [id])
            .map_err(|e| e.to_string())?;
        transaction.commit().map_err(|e| e.to_string())?;
        Ok(unfiled as u64)
    }

    pub fn list_projects(&self) -> Result<Vec<ProjectSummary>, String> {
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        let mut statement = connection
            .prepare(
                "SELECT p.id,p.name,p.folder_path,p.created_at,p.updated_at,COUNT(c.id)
                 FROM projects p LEFT JOIN conversations c ON c.project_id=p.id
                 GROUP BY p.id,p.name,p.folder_path,p.created_at,p.updated_at
                 ORDER BY lower(p.name)",
            )
            .map_err(|e| e.to_string())?;
        let rows = statement
            .query_map([], |row| {
                let folder_path: Option<String> = row.get(2)?;
                Ok(ProjectSummary {
                    id: row.get(0)?,
                    name: row.get(1)?,
                    needs_folder: folder_path.is_none(),
                    folder_available: folder_path.as_ref().is_some_and(|path| Path::new(path).is_dir()),
                    folder_path,
                    created_at: row.get(3)?,
                    updated_at: row.get(4)?,
                    conversation_count: row.get::<_, i64>(5)? as u64,
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())
    }

    pub fn start_operation(&self, kind: &str, target: &str) -> Result<OperationRecord, String> {
        let id = uuid::Uuid::new_v4().to_string();
        let now = Utc::now().to_rfc3339();
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        let active: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM operations WHERE kind=?1 AND target=?2 AND status IN ('queued','running'))",
            params![kind, target], |row| row.get(0),
        ).map_err(|e| e.to_string())?;
        if active { return Err(format!("{target} already has an operation in progress")); }
        connection.execute(
            "INSERT INTO operations(id,kind,target,phase,status,started_at,last_progress_at,owner_pid) VALUES(?1,?2,?3,'Queued','queued',?4,?4,?5)",
            params![id, kind, target, now, std::process::id()],
        ).map_err(|e| e.to_string())?;
        Ok(OperationRecord {
            id, kind: kind.into(), target: target.into(), phase: "Queued".into(),
            status: "queued".into(), current: 0, total: 0, imported: 0, updated: 0,
            skipped: 0, summary: String::new(), error: None, started_at: now.clone(), last_progress_at: now, finished_at: None,
        })
    }

    pub fn update_operation(&self, id: &str, phase: &str, current: u64, total: u64,
        imported: u64, updated: u64, skipped: u64) -> Result<(), String> {
        let changed = self.connection.lock().map_err(|e| e.to_string())?.execute(
            "UPDATE operations SET phase=?2,status='running',current=?3,total=?4,last_progress_at=?8,
             imported=?5,updated=?6,skipped=?7 WHERE id=?1 AND status IN ('queued','running')",
            params![id, phase, current, total, imported, updated, skipped, Utc::now().to_rfc3339()],
        ).map_err(|e| e.to_string())?;
        if changed == 0 { return Err("Operation is no longer active".into()); }
        Ok(())
    }

    pub fn finish_operation(&self, id: &str, summary: &str, error: Option<&str>,
        current: u64, total: u64, imported: u64, updated: u64, skipped: u64) -> Result<(), String> {
        let status = if error.is_some() { "failed" } else { "completed" };
        let phase = if error.is_some() { "Failed" } else { "Completed" };
        self.connection.lock().map_err(|e| e.to_string())?.execute(
            "UPDATE operations SET phase=?2,status=?3,current=?4,total=?5,
             imported=?6,updated=?7,skipped=?8,summary=?9,error=?10,finished_at=?11,last_progress_at=?11 WHERE id=?1",
            params![id, phase, status, current, total, imported, updated, skipped,
                redact_text(summary), error.map(redact_text), Utc::now().to_rfc3339()],
        ).map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn request_operation_cancel(&self, id: &str) -> Result<(), String> {
        let changed = self.connection.lock().map_err(|e| e.to_string())?.execute(
            "UPDATE operations SET phase='Cancellation requested',last_progress_at=?2 WHERE id=?1 AND status IN ('queued','running')",
            params![id, Utc::now().to_rfc3339()],
        ).map_err(|e| e.to_string())?;
        if changed == 0 { return Err("Import is no longer active".into()); }
        Ok(())
    }

    pub fn finish_cancelled_operation(&self, id: &str, current: u64, total: u64,
        imported: u64, updated: u64, skipped: u64) -> Result<(), String> {
        self.connection.lock().map_err(|e| e.to_string())?.execute(
            "UPDATE operations SET phase='Cancelled',status='cancelled',current=?2,total=?3,
             imported=?4,updated=?5,skipped=?6,summary='Import cancelled. Imported sessions remain until cleared.',
             error=NULL,finished_at=?7,last_progress_at=?7 WHERE id=?1 AND status IN ('queued','running')",
            params![id, current, total, imported, updated, skipped, Utc::now().to_rfc3339()],
        ).map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn has_active_operation(&self, kind: &str, target: &str) -> Result<bool, String> {
        self.connection.lock().map_err(|e| e.to_string())?.query_row(
            "SELECT EXISTS(SELECT 1 FROM operations WHERE kind=?1 AND target=?2 AND status IN ('queued','running'))",
            params![kind, target], |row| row.get(0),
        ).map_err(|e| e.to_string())
    }

    pub fn list_operations(&self) -> Result<Vec<OperationRecord>, String> {
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        let mut statement = connection.prepare(
            "SELECT id,kind,target,phase,status,current,total,imported,updated,skipped,
             summary,error,started_at,finished_at,last_progress_at FROM operations ORDER BY started_at DESC,id DESC LIMIT 50",
        ).map_err(|e| e.to_string())?;
        let rows = statement.query_map([], operation_from_row).map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())
    }

    pub fn has_client_conversations(&self, client: &str) -> bool {
        self.connection
            .lock()
            .ok()
            .and_then(|connection| {
                connection
                    .query_row(
                        "SELECT EXISTS(SELECT 1 FROM conversations WHERE client=?1 LIMIT 1)",
                        [client],
                        |row| row.get::<_, bool>(0),
                    )
                    .ok()
            })
            .unwrap_or(false)
    }

    pub fn log(&self, level: &str, source: &str, message: &str) {
        let timestamp = Utc::now().to_rfc3339();
        if let Ok(connection) = self.connection.lock() {
            let _ = connection.execute(
                "INSERT INTO logs(timestamp, level, source, message) VALUES (?1, ?2, ?3, ?4)",
                params![timestamp, level, source, redact_text(message)],
            );
            let _ = connection.execute(
                "DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 100000)",
                [],
            );
        }
    }

    pub fn ensure_conversation(
        &self,
        id: &str,
        client: &str,
        profile: &str,
        title: &str,
    ) -> Result<(), String> {
        let timestamp = Utc::now().to_rfc3339();
        self.connection
            .lock()
            .map_err(|e| e.to_string())?
            .execute(
                "INSERT INTO conversations(id,title,client,profile,status,created_at,updated_at)
                 VALUES (?1,?2,?3,?4,'active',?5,?5)
                 ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,
                   profile=excluded.profile",
                params![id, redact_text(title), client, profile, timestamp],
            )
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    /// Replace imported transcript events atomically while preserving the user's title,
    /// project assignment and pin on an existing conversation.
    pub fn replace_imported_history(
        &self,
        id: &str,
        client: &str,
        title: &str,
        rows: &[(String, String, String, String, String, Value)],
    ) -> Result<bool, String> {
        let now = Utc::now().to_rfc3339();
        let latest = rows.iter().rev().find(|row| !row.0.is_empty())
            .map(|row| row.0.as_str()).unwrap_or(&now);
        let mut connection = self.connection.lock().map_err(|e| e.to_string())?;
        let transaction = connection.transaction().map_err(|e| e.to_string())?;
        let exists: bool = transaction.query_row(
            "SELECT EXISTS(SELECT 1 FROM conversations WHERE id=?1)", [id], |row| row.get(0),
        ).map_err(|e| e.to_string())?;
        transaction.execute(
            "INSERT INTO conversations(id,title,client,profile,status,created_at,updated_at)
             VALUES(?1,?2,?3,'history','imported',?4,?5)
             ON CONFLICT(id) DO UPDATE SET
               updated_at=CASE WHEN conversations.updated_at > excluded.updated_at
                 THEN conversations.updated_at ELSE excluded.updated_at END",
            params![id, redact_text(title), client, now, latest],
        ).map_err(|e| e.to_string())?;
        // Native/API continuations have their own source and must survive transcript refresh.
        transaction.execute("DELETE FROM timeline WHERE conversation_id=?1 AND source=?2", params![id, client])
            .map_err(|e| e.to_string())?;
        {
            let mut insert = transaction.prepare(
                "INSERT INTO timeline(conversation_id,timestamp,kind,role,source,title,content,metadata)
                 VALUES(?1,?2,?3,?4,?5,?6,?7,?8)",
            ).map_err(|e| e.to_string())?;
            for (ordinal, (timestamp, kind, role, row_title, content, metadata)) in rows.iter().enumerate() {
                let event_time = if timestamp.is_empty() { now.as_str() } else { timestamp.as_str() };
                let source_identity = format!("{id}\0{client}\0{ordinal}\0{event_time}\0{kind}\0{role}\0{content}");
                let stable_id = format!("{:x}", Sha256::digest(source_identity.as_bytes()));
                let mut enriched = redact_json(metadata);
                if let Some(fields) = enriched.as_object_mut() {
                    fields.insert("opencore_source_event_id".into(), json!(stable_id));
                }
                insert.execute(params![id, event_time, kind, role, client,
                    redact_text(row_title), redact_text(content), enriched.to_string()])
                    .map_err(|e| e.to_string())?;
            }
        }
        transaction.commit().map_err(|e| e.to_string())?;
        Ok(!exists)
    }

    pub fn add_timeline(
        &self,
        conversation_id: &str,
        kind: &str,
        role: &str,
        source: &str,
        title: &str,
        content: &str,
        metadata: &Value,
    ) -> Result<i64, String> {
        let timestamp = Utc::now().to_rfc3339();
        let metadata = redact_json(metadata).to_string();
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        connection
            .execute(
                "INSERT INTO timeline(conversation_id,timestamp,kind,role,source,title,content,metadata)
                 VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
                params![
                    conversation_id,
                    timestamp,
                    kind,
                    role,
                    source,
                    redact_text(title),
                    redact_text(content),
                    metadata
                ],
            )
            .map_err(|e| e.to_string())?;
        connection
            .execute(
                "UPDATE conversations SET updated_at=?2 WHERE id=?1",
                params![conversation_id, timestamp],
            )
            .map_err(|e| e.to_string())?;
        Ok(connection.last_insert_rowid())
    }

    pub fn add_bridge_timeline(&self, conversation: &str, event: &str, kind: &str,
        role: &str, title: &str, content: &str, metadata: &Value) -> Result<i64, String> {
        let mut connection=self.connection.lock().map_err(|e|e.to_string())?;
        connection.execute_batch("CREATE TABLE IF NOT EXISTS claude_bridge_events (
            conversation_id TEXT NOT NULL, event_id TEXT NOT NULL, entry_id INTEGER NOT NULL,
            PRIMARY KEY(conversation_id,event_id),
            FOREIGN KEY(entry_id) REFERENCES timeline(id) ON DELETE CASCADE)").map_err(|e|e.to_string())?;
        let tx=connection.transaction().map_err(|e|e.to_string())?;
        if let Some(id)=tx.query_row("SELECT entry_id FROM claude_bridge_events WHERE conversation_id=?1 AND event_id=?2",
            params![conversation,event],|row|row.get::<_,i64>(0)).optional().map_err(|e|e.to_string())? {return Ok(id);}
        let now=Utc::now().to_rfc3339();
        tx.execute("INSERT INTO timeline(conversation_id,timestamp,kind,role,source,title,content,metadata)
            VALUES(?1,?2,?3,?4,'Claude Code Mods',?5,?6,?7)",params![conversation,now,kind,role,
            redact_text(title),redact_text(content),redact_json(metadata).to_string()]).map_err(|e|e.to_string())?;
        let id=tx.last_insert_rowid();
        tx.execute("INSERT INTO claude_bridge_events VALUES(?1,?2,?3)",params![conversation,event,id]).map_err(|e|e.to_string())?;
        tx.execute("UPDATE conversations SET updated_at=?2 WHERE id=?1",params![conversation,now]).map_err(|e|e.to_string())?;
        tx.commit().map_err(|e|e.to_string())?;Ok(id)
    }

    pub fn update_timeline(&self, entry_id: i64, content: &str, metadata: &Value) -> Result<(), String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let changed = connection.execute(
            "UPDATE timeline SET content=?2,metadata=?3 WHERE id=?1 AND kind='echo_import'",
            params![entry_id, redact_text(content), redact_json(metadata).to_string()],
        ).map_err(|error| error.to_string())?;
        if changed != 1 { return Err("ECHO import progress entry no longer exists".into()); }
        Ok(())
    }

    pub fn imported_message_count(&self, conversation_id: &str) -> Result<u64, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        connection.query_row(
            "SELECT COUNT(*) FROM timeline WHERE conversation_id=?1 AND kind<>'echo_import' \
             AND source IN ('Codex','Claude Code')",
            [conversation_id], |row| row.get::<_, i64>(0),
        ).map(|count| count as u64).map_err(|error| error.to_string())
    }

    pub fn imported_conversation_ids(&self) -> Result<Vec<String>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let mut statement = connection.prepare(
            "SELECT DISTINCT conversation_id FROM timeline WHERE kind<>'echo_import' AND source IN ('Codex','Claude Code') ORDER BY conversation_id"
        ).map_err(|error| error.to_string())?;
        let ids = statement.query_map([], |row| row.get::<_, String>(0))
            .map_err(|error| error.to_string())?
            .collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())?;
        Ok(ids)
    }

    pub fn imported_conversation_ids_for_client(&self, client: &str) -> Result<Vec<String>, String> {
        let (client_name, prefix) = match client {
            "codex" => ("Codex", "codex:%"),
            "claude-code" => ("Claude Code", "claude:%"),
            _ => return Err(format!("Unsupported imported history source: {client}")),
        };
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let mut statement = connection.prepare(
            "SELECT DISTINCT c.id FROM conversations c WHERE c.client=?1 AND c.id LIKE ?2
             AND EXISTS(SELECT 1 FROM timeline t WHERE t.conversation_id=c.id AND t.kind<>'echo_import' AND t.source=?1)
             ORDER BY c.id",
        ).map_err(|error| error.to_string())?;
        let ids = statement.query_map(params![client_name, prefix], |row| row.get::<_, String>(0))
            .map_err(|error| error.to_string())?
            .collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())?;
        Ok(ids)
    }

    pub fn clear_imported_history(&self, client: &str) -> Result<Vec<String>, String> {
        let (client_name, prefix) = match client {
            "codex" => ("Codex", "codex:%"),
            "claude-code" => ("Claude Code", "claude:%"),
            _ => return Err(format!("Unsupported imported history source: {client}")),
        };
        let mut connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection.transaction().map_err(|error| error.to_string())?;
        let ids = {
            let mut statement = transaction.prepare(
                "SELECT DISTINCT c.id FROM conversations c WHERE c.client=?1 AND c.id LIKE ?2
                 AND EXISTS(SELECT 1 FROM timeline t WHERE t.conversation_id=c.id AND t.kind<>'echo_import' AND t.source=?1)
                 ORDER BY c.id",
            ).map_err(|error| error.to_string())?;
            let ids = statement.query_map(params![client_name, prefix], |row| row.get::<_, String>(0))
                .map_err(|error| error.to_string())?
                .collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())?;
            ids
        };
        for id in &ids {
            transaction.execute("DELETE FROM timeline WHERE conversation_id=?1", [id])
                .map_err(|error| error.to_string())?;
            transaction.execute("DELETE FROM conversations WHERE id=?1", [id])
                .map_err(|error| error.to_string())?;
        }
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(ids)
    }

    pub fn imported_messages_batch(&self, conversation_id: &str, after_id: i64, limit: u32) -> Result<Vec<TimelineEntry>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let mut statement = connection.prepare(
            "SELECT id,conversation_id,timestamp,kind,role,source,title,content,metadata FROM timeline \
             WHERE conversation_id=?1 AND id>?2 AND kind<>'echo_import' \
             AND source IN ('Codex','Claude Code') \
             ORDER BY id LIMIT ?3",
        ).map_err(|error| error.to_string())?;
        let rows = statement.query_map(params![conversation_id, after_id, limit.min(128)], |row| {
            let raw: String = row.get(8)?;
            Ok(TimelineEntry {
                id: row.get(0)?, conversation_id: row.get(1)?, timestamp: row.get(2)?,
                kind: row.get(3)?, role: row.get(4)?, source: row.get(5)?,
                title: row.get(6)?, content: row.get(7)?,
                metadata: serde_json::from_str(&raw).unwrap_or_else(|_| json!({})),
            })
        }).map_err(|error| error.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())
    }

    pub fn archive_conversation_ids(&self) -> Result<Vec<String>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let mut statement = connection.prepare(
            "SELECT DISTINCT conversation_id FROM timeline WHERE kind<>'echo_import' ORDER BY conversation_id"
        ).map_err(|error| error.to_string())?;
        let ids = statement.query_map([], |row| row.get::<_, String>(0))
            .map_err(|error| error.to_string())?
            .collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())?;
        Ok(ids)
    }

    pub fn archive_events_batch(&self, conversation_id: &str, after_id: i64, limit: u32) -> Result<Vec<TimelineEntry>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let mut statement = connection.prepare(
            "SELECT id,conversation_id,timestamp,kind,role,source,title,content,metadata FROM timeline \
             WHERE conversation_id=?1 AND id>?2 AND kind<>'echo_import' ORDER BY id LIMIT ?3"
        ).map_err(|error| error.to_string())?;
        let rows = statement.query_map(params![conversation_id, after_id, limit.min(32)], |row| {
            let raw: String = row.get(8)?;
            Ok(TimelineEntry { id: row.get(0)?, conversation_id: row.get(1)?, timestamp: row.get(2)?,
                kind: row.get(3)?, role: row.get(4)?, source: row.get(5)?, title: row.get(6)?,
                content: row.get(7)?, metadata: serde_json::from_str(&raw).unwrap_or_else(|_| json!({})), })
        }).map_err(|error| error.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())
    }

    pub fn finish_conversation(&self, id: &str, status: &str) {
        if let Ok(connection) = self.connection.lock() {
            let _ = connection.execute(
                "UPDATE conversations SET status=?2,updated_at=?3 WHERE id=?1",
                params![id, status, Utc::now().to_rfc3339()],
            );
        }
    }

    pub fn list_conversations(
        &self,
        query: Option<&str>,
    ) -> Result<Vec<ConversationSummary>, String> {
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        let pattern = format!("%{}%", query.unwrap_or_default());
        let mut statement = connection
            .prepare(
                "SELECT c.id,c.title,c.client,c.created_at,c.updated_at,c.profile,c.status,
                   (SELECT COUNT(*) FROM timeline t WHERE t.conversation_id=c.id AND t.kind='message') AS message_count,
                   c.project,c.project_id,c.pinned
                 FROM conversations c
                 WHERE c.title LIKE ?1 OR c.client LIKE ?1 OR c.id LIKE ?1 OR c.project LIKE ?1
                 ORDER BY c.pinned DESC,c.updated_at DESC LIMIT 500",
            )
            .map_err(|e| e.to_string())?;
        let rows = statement
            .query_map([pattern], |row| {
                Ok(ConversationSummary {
                    id: row.get(0)?,
                    title: row.get(1)?,
                    client: row.get(2)?,
                    created_at: row.get(3)?,
                    updated_at: row.get(4)?,
                    profile: row.get(5)?,
                    status: row.get(6)?,
                    message_count: row.get::<_, i64>(7)? as u64,
                    project: row.get(8)?,
                    project_id: row.get(9)?,
                    pinned: row.get::<_, i64>(10)? != 0,
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())
    }

    pub fn echo_conversation_scope(&self, conversation_id: &str) -> Result<Vec<String>, String> {
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        let project_id: Option<String> = connection.query_row(
            "SELECT project_id FROM conversations WHERE id=?1", [conversation_id], |row| row.get(0),
        ).optional().map_err(|error| error.to_string())?.flatten();
        let Some(project_id) = project_id else { return Ok(vec![conversation_id.to_string()]); };
        let mut statement = connection.prepare("SELECT id FROM conversations WHERE project_id=?1 ORDER BY id")
            .map_err(|error| error.to_string())?;
        let rows = statement.query_map([project_id], |row| row.get::<_, String>(0))
            .map_err(|error| error.to_string())?;
        let mut ids = rows.collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())?;
        if !ids.iter().any(|id| id == conversation_id) { ids.push(conversation_id.to_string()); }
        Ok(ids)
    }

    pub fn conversation(&self, id: &str) -> Result<Vec<TimelineEntry>, String> {
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        let mut statement = connection
            .prepare(
                "SELECT id,conversation_id,timestamp,kind,role,source,title,content,metadata
                 FROM (
                   SELECT id,conversation_id,timestamp,kind,role,source,title,content,metadata
                   FROM timeline WHERE conversation_id=?1 ORDER BY timestamp DESC,id DESC LIMIT 500
                 ) ORDER BY timestamp,id",
            )
            .map_err(|e| e.to_string())?;
        let rows = statement
            .query_map([id], |row| {
                let raw: String = row.get(8)?;
                Ok(TimelineEntry {
                    id: row.get(0)?,
                    conversation_id: row.get(1)?,
                    timestamp: row.get(2)?,
                    kind: row.get(3)?,
                    role: row.get(4)?,
                    source: row.get(5)?,
                    title: row.get(6)?,
                    content: row.get(7)?,
                    metadata: serde_json::from_str(&raw).unwrap_or_else(|_| json!({})),
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())
    }

    pub fn latest_user_entry(&self,id:&str)->Result<Option<i64>,String> {
        self.connection.lock().map_err(|e|e.to_string())?.query_row("SELECT max(id) FROM timeline WHERE conversation_id=?1 AND role='user' AND kind='message'",[id],|row|row.get(0)).map_err(|e|e.to_string())
    }

    pub fn code_artifacts(&self, id: &str) -> Result<Vec<Value>, String> {
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        let mut statement = connection.prepare(
            "SELECT metadata FROM timeline WHERE conversation_id=?1 AND kind='file' \
             AND role='assistant' AND source='OpenCore' ORDER BY id DESC LIMIT 100"
        ).map_err(|e| e.to_string())?;
        let rows = statement.query_map([id], |row| row.get::<_, String>(0))
            .map_err(|e| e.to_string())?;
        let mut files = Vec::new();
        for row in rows {
            let metadata = serde_json::from_str::<Value>(&row.map_err(|e| e.to_string())?)
                .unwrap_or_default();
            if metadata.get("id").and_then(Value::as_str).is_some()
                && metadata.get("name").and_then(Value::as_str).is_some() {
                files.push(json!({"id":metadata["id"],"name":metadata["name"],
                    "size":metadata.get("size").cloned().unwrap_or(Value::Null)}));
            }
        }
        Ok(files)
    }

    pub fn conversation_activity(&self, id: &str) -> Result<Vec<TimelineEntry>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let mut statement = connection.prepare(
            "SELECT id,conversation_id,timestamp,kind,role,source,title,\
             CASE WHEN length(content)>65536 THEN substr(content,1,65536)||char(10)||'[Large event preview. Open ECHO Memory for the exact source.]' ELSE content END,\
             CASE WHEN length(metadata)>32768 THEN '{}' ELSE metadata END \
             FROM (SELECT id,conversation_id,timestamp,kind,role,source,title,content,metadata \
                   FROM timeline WHERE conversation_id=?1 ORDER BY id DESC LIMIT 300) ORDER BY id"
        ).map_err(|error| error.to_string())?;
        let rows = statement.query_map([id], |row| {
            let raw: String = row.get(8)?;
            Ok(TimelineEntry { id: row.get(0)?, conversation_id: row.get(1)?, timestamp: row.get(2)?,
                kind: row.get(3)?, role: row.get(4)?, source: row.get(5)?, title: row.get(6)?,
                content: row.get(7)?, metadata: serde_json::from_str(&raw).unwrap_or_else(|_| json!({})), })
        }).map_err(|error| error.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())
    }

    pub fn conversation_messages(&self, id: &str) -> Result<Vec<TimelineEntry>, String> {
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        let mut statement = connection
            .prepare(
                "SELECT id,conversation_id,timestamp,kind,role,source,title,content,metadata
                 FROM (
                   SELECT id,conversation_id,timestamp,kind,role,source,title,content,metadata
                   FROM timeline
                   WHERE conversation_id=?1 AND kind='message' AND role IN ('user','assistant')
                   ORDER BY timestamp DESC,id DESC LIMIT 160
                 ) ORDER BY timestamp,id",
            )
            .map_err(|e| e.to_string())?;
        let rows = statement
            .query_map([id], |row| {
                let raw: String = row.get(8)?;
                Ok(TimelineEntry {
                    id: row.get(0)?,
                    conversation_id: row.get(1)?,
                    timestamp: row.get(2)?,
                    kind: row.get(3)?,
                    role: row.get(4)?,
                    source: row.get(5)?,
                    title: row.get(6)?,
                    content: row.get(7)?,
                    metadata: serde_json::from_str(&raw).unwrap_or_else(|_| json!({})),
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())
    }

    pub fn logs(&self, limit: usize) -> Result<Vec<LogEntry>, String> {
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        let mut statement = connection
            .prepare(
                "SELECT id,timestamp,level,source,message FROM
                   (SELECT id,timestamp,level,source,message FROM logs ORDER BY id DESC LIMIT ?1)
                 ORDER BY id",
            )
            .map_err(|e| e.to_string())?;
        let rows = statement
            .query_map([limit as i64], |row| {
                Ok(LogEntry {
                    id: row.get(0)?,
                    timestamp: row.get(1)?,
                    level: row.get(2)?,
                    source: row.get(3)?,
                    message: row.get(4)?,
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())
    }

    pub fn clear_logs(&self) -> Result<(), String> {
        self.connection.lock().map_err(|e| e.to_string())?
            .execute("DELETE FROM logs", [])
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn rename_conversation(&self, id: &str, title: &str) -> Result<(), String> {
        self.connection
            .lock()
            .map_err(|e| e.to_string())?
            .execute(
                "UPDATE conversations SET title=?2,updated_at=?3 WHERE id=?1",
                params![id, redact_text(title), Utc::now().to_rfc3339()],
            )
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn delete_conversation(&self, id: &str) -> Result<(), String> {
        let mut connection = self.connection.lock().map_err(|e| e.to_string())?;
        let transaction = connection.transaction().map_err(|e| e.to_string())?;
        transaction
            .execute("DELETE FROM timeline WHERE conversation_id=?1", [id])
            .map_err(|e| e.to_string())?;
        transaction
            .execute("DELETE FROM conversations WHERE id=?1", [id])
            .map_err(|e| e.to_string())?;
        transaction.commit().map_err(|e| e.to_string())
    }

    pub fn connectors(&self) -> Result<Vec<ConnectorStatus>, String> {
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        let mut statement = connection
            .prepare("SELECT id,name,kind,endpoint,custom,last_seen FROM connectors ORDER BY custom,name")
            .map_err(|e| e.to_string())?;
        let rows = statement
            .query_map([], |row| {
                let last_seen: Option<String> = row.get(5)?;
                Ok(ConnectorStatus {
                    id: row.get(0)?,
                    name: row.get(1)?,
                    kind: row.get(2)?,
                    status: if last_seen.is_some() {
                        "observed".into()
                    } else {
                        "configured".into()
                    },
                    endpoint: row.get(3)?,
                    observable: last_seen.is_some(),
                    details: last_seen
                        .map(|value| format!("Last routed request: {value}"))
                        .unwrap_or_else(|| "Configured; no routed request observed yet".into()),
                    custom: row.get::<_, i64>(4)? != 0,
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())
    }

    pub fn upsert_connector(&self, input: &ConnectorInput) -> Result<ConnectorStatus, String> {
        let id = input
            .id
            .clone()
            .unwrap_or_else(|| uuid::Uuid::new_v4().simple().to_string());
        let name = input.name.trim();
        let endpoint = input.endpoint.trim().trim_end_matches('/');
        let pattern = input.match_pattern.trim().to_ascii_lowercase();
        if name.is_empty() || endpoint.is_empty() || pattern.is_empty() {
            return Err("Name, endpoint and client match pattern are required".into());
        }
        if !(endpoint.starts_with("http://") || endpoint.starts_with("https://")) {
            return Err("Endpoint must begin with http:// or https://".into());
        }
        self.connection
            .lock()
            .map_err(|e| e.to_string())?
            .execute(
                "INSERT INTO connectors(id,name,kind,endpoint,match_pattern,custom,last_seen)
                 VALUES (?1,?2,?3,?4,?5,1,NULL)
                 ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,
                   endpoint=excluded.endpoint,match_pattern=excluded.match_pattern",
                params![id, name, input.kind, endpoint, pattern],
            )
            .map_err(|e| e.to_string())?;
        self.connectors()?
            .into_iter()
            .find(|item| item.id == id)
            .ok_or_else(|| "Connector save failed".into())
    }

    pub fn delete_connector(&self, id: &str) -> Result<(), String> {
        let connection = self.connection.lock().map_err(|e| e.to_string())?;
        let custom: Option<i64> = connection
            .query_row("SELECT custom FROM connectors WHERE id=?1", [id], |row| {
                row.get(0)
            })
            .ok();
        if custom != Some(1) {
            return Err("Built-in connectors cannot be deleted".into());
        }
        connection
            .execute("DELETE FROM connectors WHERE id=?1", [id])
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn observe_client(&self, client: &str) {
        let lowered = client.to_ascii_lowercase();
        if let Ok(connection) = self.connection.lock() {
            if let Ok(mut statement) = connection.prepare("SELECT id,match_pattern FROM connectors")
            {
                if let Ok(rows) = statement.query_map([], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
                }) {
                    for row in rows.flatten() {
                        if lowered.contains(&row.1) {
                            let _ = connection.execute(
                                "UPDATE connectors SET last_seen=?2 WHERE id=?1",
                                params![row.0, Utc::now().to_rfc3339()],
                            );
                        }
                    }
                }
            }
        }
    }
}
