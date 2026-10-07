use super::*;

#[test]
fn old_conversation_identity_and_project_survive_sidebar_limit() {
    let root=std::env::temp_dir().join(format!("opencore-old-chat-{}",uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&root).unwrap();
    let store=EventStore::open(&root.join("history.sqlite3")).unwrap();
    let project=store.create_project("Original workspace",&root).unwrap();
    store.ensure_conversation("old","OpenCore","echo","Old chat").unwrap();
    store.set_project_by_id("old",Some(&project.id),ProjectAssignment::Manual).unwrap();
    store.connection.lock().unwrap().execute("UPDATE conversations SET updated_at='2000-01-01' WHERE id='old'",[]).unwrap();
    for index in 0..505 {store.ensure_conversation(&format!("new-{index}"),"OpenCore","echo","New chat").unwrap();}
    assert!(!store.list_conversations(None).unwrap().iter().any(|chat|chat.id=="old"));
    assert!(store.conversation_exists("old").unwrap());
    assert_eq!(store.conversation_project_id("old").unwrap(),Some(project.id));
    store.delete_conversation("old").unwrap();
    assert!(!store.conversation_exists("old").unwrap());
    assert_eq!(store.conversation_project_id("old").unwrap(),None);
    drop(store);std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn side_chat_preserves_all_context_and_isolates_new_messages() {
    let root = std::env::temp_dir().join(format!("opencore-side-chat-{}", uuid::Uuid::new_v4()));
    let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
    store.ensure_conversation("main", "OpenCore", "echo", "Snake game").unwrap();
    for n in 0..180 {
        store.add_timeline("main", "message", if n % 2 == 0 { "user" } else { "assistant" },
            "OpenCore", "Message", &format!("Keep exact message {n}"), &json!({"n": n})).unwrap();
    }
    let info = store.create_side_chat("main", "side", "echo", 262_144).unwrap();
    assert_eq!(info["inheritedEntries"], 180);
    assert_eq!(info["contextTokens"], 262_144);
    assert_eq!(store.workspace_conversation_id("side").unwrap(), "main");
    assert_eq!(store.echo_conversation_scope("side").unwrap(), vec!["side"]);
    store.add_timeline("side", "message", "user", "OpenCore", "You", "Side only", &json!({})).unwrap();
    assert_eq!(store.conversation("main").unwrap().len(), 180);
    let copied = store.conversation("side").unwrap();
    assert_eq!(copied.len(), 181);
    assert_eq!(copied[0].content, "Keep exact message 0");
    assert!(copied[0].metadata["sideChatInherited"].as_bool().unwrap());
    assert!(copied[0].metadata["opencore_source_event_id"].as_str().unwrap().starts_with("side:side:"));
    assert_eq!(copied.last().unwrap().content, "Side only");
    store.add_timeline("main","message","user","OpenCore","Later main","Future parent message",&json!({})).unwrap();
    assert!(!store.conversation("side").unwrap().iter().any(|row|row.content=="Future parent message"));
    assert_eq!(store.echo_conversation_scope("side").unwrap(),vec!["side"]);
    drop(store);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn refreshing_parent_context_keeps_side_turns_settings_workspace_and_pending_delivery() {
    let root=std::env::temp_dir().join(format!("opencore-side-refresh-{}",uuid::Uuid::new_v4()));
    let store=EventStore::open(&root.join("history.sqlite3")).unwrap();
    store.ensure_conversation("main","OpenCore","echo","Main").unwrap();
    store.add_timeline("main","message","user","OpenCore","You","Original decision",&json!({})).unwrap();
    let initial=store.create_side_chat("main","side","echo",262_144).unwrap();
    store.mark_side_chat_context_delivered("side",initial["copiedThrough"].as_i64().unwrap()).unwrap();
    store.add_timeline("side","message","user","OpenCore","You","Independent side question",&json!({})).unwrap();
    store.set_setting("chat_request_side","side-specific-settings").unwrap();
    store.add_timeline("main","message","user","OpenCore","You","Use port 4174 now",&json!({"userChoice":true})).unwrap();
    store.add_timeline("main","message","assistant","OpenCore","Answer","The server is ready",&json!({})).unwrap();
    let refreshed=store.refresh_side_chat_context("side").unwrap();
    assert_eq!(refreshed["updatedEntries"],2);
    assert_eq!(refreshed["inheritedEntries"],3);
    assert_eq!(store.get_setting("chat_request_side").unwrap().as_deref(),Some("side-specific-settings"));
    assert_eq!(store.workspace_conversation_id("side").unwrap(),"main");
    assert_eq!(store.conversation("main").unwrap().len(),3);
    assert!(store.conversation("side").unwrap().iter().any(|entry|entry.content=="Independent side question"));
    let pending=store.side_chat_context_update("side").unwrap().unwrap();
    assert_eq!(pending.entries.iter().map(|entry|entry.content.as_str()).collect::<Vec<_>>(),vec!["Use port 4174 now","The server is ready"]);
    assert!(pending.input_text().contains("Use port 4174 now"));
    assert!(pending.input_text().contains("untrusted"));
    assert_eq!(store.refresh_side_chat_context("side").unwrap()["updatedEntries"],0);
    // Reading or displaying context does not consume it. Failed submissions can retry it.
    assert_eq!(store.side_chat_context_update("side").unwrap().unwrap().entries.len(),2);
    store.mark_side_chat_context_delivered("side",pending.through).unwrap();
    assert!(store.side_chat_context_update("side").unwrap().is_none());
    assert!(store.mark_side_chat_context_delivered("side",pending.through+1).is_err());
    store.mark_side_chat_context_delivered("side",0).unwrap();
    assert!(store.side_chat_context_update("side").unwrap().is_none());
    drop(store);std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn nested_context_refresh_is_incremental_and_survives_parent_deletion() {
    let root=std::env::temp_dir().join(format!("opencore-nested-refresh-{}",uuid::Uuid::new_v4()));
    let store=EventStore::open(&root.join("history.sqlite3")).unwrap();
    store.ensure_conversation("main","OpenCore","echo","Main").unwrap();
    store.add_timeline("main","message","user","OpenCore","You","Original main message",&json!({})).unwrap();
    store.create_side_chat("main","side","echo",262_144).unwrap();
    store.create_side_chat("side","nested","echo",262_144).unwrap();
    store.add_timeline("main","message","user","OpenCore","You","New main message",&json!({})).unwrap();
    store.refresh_side_chat_context("side").unwrap();
    store.add_timeline("side","message","user","OpenCore","You","New side message",&json!({})).unwrap();
    assert_eq!(store.refresh_side_chat_context("nested").unwrap()["updatedEntries"],2);
    assert_eq!(store.refresh_side_chat_context("nested").unwrap()["updatedEntries"],0);
    let nested=store.conversation("nested").unwrap();
    assert_eq!(nested.iter().filter(|entry|entry.content=="Original main message").count(),1);
    assert_eq!(nested.iter().filter(|entry|entry.content=="New main message").count(),1);
    assert_eq!(store.conversation("main").unwrap().len(),2);
    assert_eq!(store.workspace_conversation_id("nested").unwrap(),"main");
    store.delete_conversation("side").unwrap();
    assert!(store.refresh_side_chat_context("nested").unwrap()["contextWarning"].as_str().unwrap().contains("saved context"));
    assert_eq!(store.conversation("nested").unwrap().len(),3);
    assert!(store.side_chat_context_update("main").unwrap().is_none());
    assert!(store.refresh_side_chat_context("main").is_err());
    drop(store);std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn side_chat_shares_project_but_never_reuses_source_harness_mapping() {
    let root = std::env::temp_dir().join(format!("opencore-side-project-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&root).unwrap();
    let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
    let project = store.create_project("Code", &root).unwrap();
    store.ensure_conversation("main", "OpenCore", "echo", "Main").unwrap();
    store.set_project_by_id("main", Some(&project.id), ProjectAssignment::Manual).unwrap();
    let mapping = CodexThreadMapping {
        conversation_id: "main".into(), workspace_identity: "workspace".into(),
        thread_id: "source-thread".into(), provider_id: "opencore-local".into(), model_id: "echo".into(),
        runtime_version: "0.160.0".into(), schema_hash: "schema".into(), migration_state: "native".into(),
        legacy_sdk_thread_id: None,
    };
    store.save_codex_thread_mapping(&mapping).unwrap();
    store.create_side_chat("main", "side", "echo", 262_144).unwrap();
    let branch = store.list_conversations(None).unwrap().into_iter().find(|c| c.id == "side").unwrap();
    assert_eq!(branch.project_id.as_deref(), Some(project.id.as_str()));
    assert!(store.codex_thread_mapping("side", "workspace").unwrap().is_none());
    assert_eq!(store.codex_thread_mapping("main", "workspace").unwrap(), Some(mapping));
    assert_eq!(store.echo_conversation_scope("main").unwrap(),vec!["main"]);
    assert_eq!(store.echo_conversation_scope("side").unwrap(),vec!["side"]);
    store.create_side_chat("side", "nested", "echo", 262_144).unwrap();
    assert_eq!(store.workspace_conversation_id("nested").unwrap(), "main");
    assert_eq!(store.side_chat_info("nested").unwrap().unwrap()["parentId"], "side");
    assert_eq!(store.echo_conversation_scope("main").unwrap(),vec!["main"]);
    drop(store);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn invalid_or_duplicate_branches_do_not_change_existing_history() {
    let root = std::env::temp_dir().join(format!("opencore-side-invalid-{}", uuid::Uuid::new_v4()));
    let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
    store.ensure_conversation("main", "OpenCore", "echo", "Main").unwrap();
    assert!(store.create_side_chat("missing", "side", "echo", 262_144).is_err());
    assert!(store.create_side_chat("main", "main", "echo", 262_144).is_err());
    store.create_side_chat("main", "side", "echo", 262_144).unwrap();
    assert!(store.create_side_chat("main", "side", "echo", 262_144).is_err());
    assert_eq!(store.list_conversations(None).unwrap().len(), 2);
    drop(store);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn deleting_main_keeps_side_history_and_shared_workspace_identity() {
    let root = std::env::temp_dir().join(format!("opencore-side-delete-{}", uuid::Uuid::new_v4()));
    let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
    store.ensure_conversation("main", "OpenCore", "echo", "Main").unwrap();
    store.add_timeline("main", "message", "user", "OpenCore", "You", "Keep this", &json!({})).unwrap();
    store.create_side_chat("main", "side", "echo", 262_144).unwrap();
    store.delete_conversation("main").unwrap();
    assert_eq!(store.conversation("side").unwrap()[0].content, "Keep this");
    assert_eq!(store.workspace_conversation_id("side").unwrap(), "main");
    drop(store);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn spaces_index_uses_all_persisted_outputs_without_duplicating_inherited_artifacts() {
    let root=std::env::temp_dir().join(format!("opencore-space-origins-{}",uuid::Uuid::new_v4()));
    let store=EventStore::open(&root.join("history.sqlite3")).unwrap();
    store.ensure_conversation("main","OpenCore","echo","Images").unwrap();
    for index in 0..140 {
        store.add_timeline("main","file","assistant","OpenCore","Image","artifact link",
            &json!({"id":format!("output-{index}"),"name":"image.png","mime":"image/png"})).unwrap();
    }
    store.create_side_chat("main","side","echo",262_144).unwrap();
    assert_eq!(store.published_artifacts(None).unwrap().len(),140);
    assert_eq!(store.published_artifacts(Some("main")).unwrap().len(),140);
    assert!(store.published_artifacts(Some("side")).unwrap().is_empty());
    drop(store); std::fs::remove_dir_all(root).unwrap();
}
