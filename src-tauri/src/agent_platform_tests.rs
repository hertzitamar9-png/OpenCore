use super::*;

struct Fixture {
    root: PathBuf,
    store: EventStore,
}

impl Fixture {
    fn new() -> Self {
        let root =
            std::env::temp_dir().join(format!("opencore-agent-platform-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let store = EventStore::open(&root.join("events.sqlite3")).unwrap();
        Self { root, store }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        // Windows may hold the EventStore file until field drop; never use this
        // helper for production data or remove anything outside this fixture.
        let _ = std::fs::remove_file(self.root.join(LEDGER_FILENAME));
    }
}

fn config(value: Value) -> PlatformConfig {
    serde_json::from_value(value).unwrap()
}

#[test]
fn invalid_settings_do_not_replace_persisted_configuration() {
    let fixture = Fixture::new();
    let good =
        config(json!({"compactAtTokens":123456,"systemPrompt":"Preserve existing game saves."}));
    save_configuration(&fixture.store, good.clone()).unwrap();
    for patch in [
        json!({"compactAtTokens":1023}),
        json!({"verification":"forever"}),
        json!({"repairAttempts":11}),
        json!({"appearance":{"accentColor":"red;display:none"}}),
        json!({"appearance":{"fontSize":9}}),
        json!({"approvalMode":"allow-all"}),
    ] {
        assert!(execute(
            &fixture.store,
            &fixture.root,
            "app_control",
            &json!({"action":"set","settings":patch})
        )
        .is_err());
        assert_eq!(configuration(&fixture.store).unwrap(), good);
    }
    assert!(execute(
        &fixture.store,
        &fixture.root,
        "app_control",
        &json!({
            "action":"set","source":"","settings":{"compactAtTokens":200001}
        })
    )
    .is_err());
    assert_eq!(configuration(&fixture.store).unwrap(), good);
}

#[test]
fn setting_receipt_matches_real_state_after_reopening() {
    let fixture = Fixture::new();
    let receipt = execute(
        &fixture.store,
        &fixture.root,
        "app_control",
        &json!({
            "action":"set","settings":{"compactAtTokens":180000,"verification":"long","appearance":{"fontSize":18}},
            "source":"conversation/settings-request"
        }),
    )
    .unwrap();
    assert_eq!(receipt["persisted"], true);
    assert_eq!(receipt["configuration"]["compactAtTokens"], 180000);
    assert_eq!(receipt["changes"].as_array().unwrap().len(), 3);
    let reopened = EventStore::open(&fixture.root.join("events.sqlite3")).unwrap();
    let current = configuration(&reopened).unwrap();
    assert_eq!(current.compact_at_tokens, 180000);
    assert_eq!(current.verification, "long");
    assert_eq!(current.appearance.font_size, 18);
    assert_eq!(current.appearance.theme, "dark");
    let events = activity(&fixture.root, "settings-request", 10).unwrap();
    assert_eq!(events.len(), 1);
    assert_eq!(
        events[0].details["changes"][0]["field"].as_str().is_some(),
        true
    );
}

#[test]
fn tool_configuration_redacts_env_url_credentials_and_secret_arguments() {
    let fixture = Fixture::new();
    let saved = config(json!({"mcpServers":[
        {"id":"local","name":"local","command":"node","args":["worker.mjs","--token","opaque-credential"],"env":{"PRIVATE_VALUE":"opaque-env-secret"}},
        {"id":"remote","name":"remote","url":"https://example.test/mcp?token=opaque-url-secret","bearerTokenEnvVar":"REMOTE_TOKEN"}
    ]}));
    save_configuration(&fixture.store, saved).unwrap();
    let result = execute(
        &fixture.store,
        &fixture.root,
        "app_control",
        &json!({"action":"get"}),
    )
    .unwrap();
    let text = result.to_string();
    assert!(!text.contains("opaque-credential"));
    assert!(!text.contains("opaque-env-secret"));
    assert!(!text.contains("opaque-url-secret"));
    assert!(text.contains("REMOTE_TOKEN"));
    assert!(text.contains("REDACTED"));
    // Only the model-facing view is redacted; the local editor retains values.
    let local = configuration(&fixture.store).unwrap();
    assert_eq!(
        local.mcp_servers[0].env["PRIVATE_VALUE"],
        "opaque-env-secret"
    );
}

#[test]
fn logged_tool_arguments_hide_credentials_without_changing_execution_input() {
    let args = json!({"action":"set","settings":{"verification":"long","mcpServers":[
        {"id":"local","name":"local","command":"node","env":{"UNRECOGNIZED":"private-env-value"},
         "args":["worker.mjs","--password=private-assigned-value","--token","private-next-value",
           "https://user:private-url-password@example.test/mcp?session=private-query-value"]},
        {"id":"remote","name":"remote","url":"https://example.test/mcp?auth=private-remote-query",
         "bearerTokenEnvVar":"REMOTE_TOKEN"}
    ]}});
    let original = args.clone();
    let logged = redacted_tool_arguments("mcp__opencore__app_control", &args);
    assert_eq!(args, original);
    for secret in [
        "private-env-value",
        "private-assigned-value",
        "private-next-value",
        "private-url-password",
        "private-query-value",
        "private-remote-query",
    ] {
        assert!(!logged.to_string().contains(secret), "leaked {secret}");
    }
    assert_eq!(logged["settings"]["verification"], "long");
    assert_eq!(
        logged["settings"]["mcpServers"][1]["bearerTokenEnvVar"],
        "REMOTE_TOKEN"
    );
}

#[test]
fn question_answer_logging_masks_opaque_secret_fields_and_all_response_content() {
    let answer = json!({"answers":{"generic-field":{"answers":["opaque-answer-secret"]}},
        "response":{"action":"accept","content":{"arbitrary-field":"opaque-content-secret"}},
        "fields":[{"id":"opaque","isSecret":true,"value":"opaque-field-secret","text":"opaque-text-secret"}],
        "promptTokens":123});
    let logged = redacted_tool_arguments("answer_agent_question", &answer);
    for secret in [
        "opaque-answer-secret",
        "opaque-content-secret",
        "opaque-field-secret",
        "opaque-text-secret",
    ] {
        assert!(!logged.to_string().contains(secret));
    }
    assert_eq!(logged["promptTokens"], 123);
    assert_eq!(answer["fields"][0]["value"], "opaque-field-secret");
}

#[test]
fn activity_index_searches_studio_details_scope_and_literal_substrings() {
    let fixture = Fixture::new();
    let mut event = ActivityEvent::new(
        "studio",
        "completed",
        "Generation finished",
        "studio/jobs",
        json!({"jobId":"job-durable-123","modelId":"renderer-abc","prompt":"A harbor with \"gold\" lights",
          "settings":{"seed":"seed-112233","percentage":"100%"},"outputs":["artifacts/harbor-scene.webm"],
          "apiKey":"never-index-this-secret"}),
    );
    event.conversation_id = Some("chat-scope-567".into());
    event.project_id = Some("project-scope-890".into());
    record_activity(&fixture.root, &event).unwrap();
    for query in [
        "durable-123",
        "RENDERER-ABC",
        "with \"gold\"",
        "seed-112233",
        "100%",
        "harbor-scene.webm",
        "chat-scope-567",
        "project-scope-890",
        "%",
    ] {
        let found = activity(&fixture.root, query, 10).unwrap();
        assert_eq!(found.len(), 1, "missing literal {query}");
        assert_eq!(found[0].id, event.id);
    }
    for query in [
        "never-index-this-secret",
        "' OR 1=1 --",
        "renderer OR absent",
        "missing_underscore",
    ] {
        assert!(
            activity(&fixture.root, query, 10).unwrap().is_empty(),
            "nonliteral {query}"
        );
    }
}

#[test]
fn index_deletions_remove_activity_and_superseded_memory_search_entries() {
    let fixture = Fixture::new();
    let event = ActivityEvent::new(
        "studio",
        "completed",
        "Finished",
        "fixture",
        json!({"jobId":"delete-this-search-result"}),
    );
    record_activity(&fixture.root, &event).unwrap();
    assert!(delete_entry(&fixture.root, "activity", &event.id).unwrap());
    assert!(activity(&fixture.root, "delete-this-search-result", 10)
        .unwrap()
        .is_empty());
    let first = record_memory(
        &fixture.root,
        &json!({"key":"release", "content":"old-indexed-marker",
        "source":"fixture/old"}),
    )
    .unwrap();
    let second = record_memory(
        &fixture.root,
        &json!({"key":"release", "content":"current-indexed-marker",
        "source":"fixture/new"}),
    )
    .unwrap();
    assert!(memories(&fixture.root, "old-indexed-marker", 10)
        .unwrap()
        .is_empty());
    assert_eq!(
        memories(&fixture.root, "current-indexed-marker", 10).unwrap()[0].id,
        second.id
    );
    assert!(delete_entry(&fixture.root, "memories", &first.id).unwrap());
    assert!(delete_entry(&fixture.root, "memories", &second.id).unwrap());
    assert!(memories(&fixture.root, "current-indexed-marker", 10)
        .unwrap()
        .is_empty());
    let connection = ledger(&fixture.root).unwrap();
    for table in ["activity_search_fts", "memory_search_fts"] {
        connection
            .execute(
                &format!("INSERT INTO {table}({table},rank) VALUES('integrity-check',1)"),
                [],
            )
            .unwrap();
    }
}

#[test]
fn existing_ledger_is_backfilled_once_without_rewriting_raw_events() {
    let fixture = Fixture::new();
    let raw_echo = b"raw ECHO bytes stay exactly as stored\0";
    std::fs::write(fixture.root.join("raw-echo.opencore"), raw_echo).unwrap();
    let event = ActivityEvent::new(
        "music",
        "completed",
        "Earlier generation",
        "fixture/legacy",
        json!({"modelId":"legacy-music-model","prompt":"old sourced prompt"}),
    );
    let encoded = serde_json::to_string(&event).unwrap();
    {
        let old = Connection::open(fixture.root.join(LEDGER_FILENAME)).unwrap();
        old.execute_batch("CREATE TABLE activity(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,
            category TEXT NOT NULL,action TEXT NOT NULL,summary TEXT NOT NULL,source TEXT NOT NULL,event_json TEXT NOT NULL);
            CREATE TABLE memories(id TEXT PRIMARY KEY,key TEXT NOT NULL,kind TEXT NOT NULL,scope TEXT NOT NULL,
            content TEXT NOT NULL,source TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
            version INTEGER NOT NULL,supersedes TEXT,evidence TEXT,status TEXT NOT NULL);").unwrap();
        old.execute(
            "INSERT INTO activity VALUES(?1,?2,?3,?4,?5,?6,?7)",
            params![
                event.id,
                event.timestamp,
                event.category,
                event.action,
                event.summary,
                event.source,
                encoded
            ],
        )
        .unwrap();
        old.execute(
            "INSERT INTO memories VALUES('legacy-fact','command','fact','global','legacy-memory-marker',
            'fixture/legacy','2026-10-06T10:00:00Z','2026-10-06T10:00:00Z',1,NULL,NULL,'active')",
            [],
        )
        .unwrap();
    }
    for _ in 0..2 {
        assert_eq!(
            activity(&fixture.root, "legacy-music-model", 10).unwrap()[0].id,
            event.id
        );
        assert_eq!(
            memories(&fixture.root, "legacy-memory-marker", 10).unwrap()[0].id,
            "legacy-fact"
        );
    }
    let connection = ledger(&fixture.root).unwrap();
    let saved: String = connection
        .query_row(
            "SELECT event_json FROM activity WHERE id=?1",
            [&event.id],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(saved, encoded);
    assert_eq!(
        std::fs::read(fixture.root.join("raw-echo.opencore")).unwrap(),
        raw_echo
    );
    assert!(delete_entry(&fixture.root, "activity", &event.id).unwrap());
    assert!(activity(&fixture.root, "legacy-music-model", 10)
        .unwrap()
        .is_empty());
}

#[test]
fn redacted_tool_patch_preserves_existing_connection_secrets() {
    let fixture = Fixture::new();
    save_configuration(
        &fixture.store,
        config(json!({"mcpServers":[
            {"id":"local","name":"local","command":"node","env":{"VALUE":"original-secret"}}
        ]})),
    )
    .unwrap();
    execute(
        &fixture.store,
        &fixture.root,
        "app_control",
        &json!({"action":"set","settings":{"mcpServers":[
            {"id":"local","name":"local","enabled":false,"command":"node","env":{"VALUE":"[REDACTED]"}}
        ]}}),
    )
    .unwrap();
    let saved = configuration(&fixture.store).unwrap();
    assert_eq!(saved.mcp_servers[0].env["VALUE"], "original-secret");
    assert!(!saved.mcp_servers[0].enabled);
    assert!(saved.mcp_servers[0].args.is_empty());
    assert!(execute(
        &fixture.store,
        &fixture.root,
        "app_control",
        &json!({"action":"set","settings":{"mcpServers":[
            {"id":"new","name":"new","command":"node","env":{"VALUE":"[REDACTED]"}}
        ]}})
    )
    .is_err());
}

#[test]
fn minimal_mcp_setting_changes_preserve_defaults_for_omitted_fields() {
    let fixture = Fixture::new();
    let result = execute(
        &fixture.store,
        &fixture.root,
        "app_control",
        &json!({"action":"set","settings":{"mcpServers":[
            {"id":"local","name":"local","command":"node"},
            {"id":"remote","name":"remote","url":"https://example.test/mcp"}
        ]}}),
    )
    .unwrap();
    assert_eq!(result["persisted"], true);
    let saved = configuration(&fixture.store).unwrap();
    assert_eq!(saved.mcp_servers.len(), 2);
    for server in &saved.mcp_servers {
        assert!(server.args.is_empty());
        assert!(server.env.is_empty());
        assert_eq!(server.startup_timeout_sec, 30);
    }
    assert_eq!(
        mcp_configuration(&saved).unwrap()["local"]["command"],
        "node"
    );
    assert_eq!(
        mcp_configuration(&saved).unwrap()["remote"]["url"],
        "https://example.test/mcp"
    );
}

#[test]
fn mcp_transports_produce_actual_codex_configuration() {
    let saved = config(json!({"mcpServers":[
        {"id":"local-id","name":"local-tools","command":"node","args":["server.mjs"],"env":{"MODE":"test"},"startupTimeoutSec":15,"toolTimeoutSec":90},
        {"id":"remote-id","name":"remote-tools","url":"https://example.test/mcp","bearerTokenEnvVar":"MCP_AUTH"},
        {"id":"disabled-id","name":"disabled-tools","command":"unused","enabled":false}
    ]}));
    let result = mcp_configuration(&saved).unwrap();
    assert_eq!(result["local-tools"]["command"], "node");
    assert_eq!(result["local-tools"]["args"], json!(["server.mjs"]));
    assert_eq!(result["local-tools"]["env"]["MODE"], "test");
    assert_eq!(result["local-tools"]["startup_timeout_sec"], 15);
    assert_eq!(result["local-tools"]["tool_timeout_sec"], 90);
    assert_eq!(result["remote-tools"]["url"], "https://example.test/mcp");
    assert_eq!(result["remote-tools"]["bearer_token_env_var"], "MCP_AUTH");
    assert!(result["remote-tools"].get("command").is_none());
    assert!(result.get("disabled-tools").is_none());
    assert!(result.get("opencore").is_none());
}

#[test]
fn invalid_mcp_transports_and_reserved_names_are_rejected() {
    let fixture = Fixture::new();
    for servers in [
        json!([{"id":"test","name":"OPENCORE","command":"node"}]),
        json!([{"id":"test","name":"test","command":"node","url":"https://example.test/mcp"}]),
        json!([{"id":"test","name":"test","url":"file:///secret"}]),
        json!([{"id":"test","name":"test","url":"https://user:password@example.test/mcp"}]),
        json!([{"id":"test","name":"test","command":"node","startupTimeoutSec":0}]),
        json!([{"id":"one","name":"same","command":"node"},{"id":"two","name":"SAME","command":"node"}]),
        json!([{"id":"test","name":"test","url":"https://example.test/mcp","env":{"KEY":"ignored"}}]),
    ] {
        assert!(save_configuration(&fixture.store, config(json!({"mcpServers":servers}))).is_err());
    }
}

#[test]
fn facts_supersede_by_scope_and_key_without_overwriting_raw_echo() {
    let fixture = Fixture::new();
    let echo = fixture.root.join("raw-echo.opencore");
    std::fs::write(&echo, b"exact raw conversation bytes\0do not replace").unwrap();
    let first = execute(
        &fixture.store,
        &fixture.root,
        "agent_memory",
        &json!({
            "action":"record","key":"test-command","kind":"fact","scope":"project/game",
            "content":"Use npm test.","source":"package.json@abc123","evidence":"scripts.test"
        }),
    )
    .unwrap();
    let second = execute(
        &fixture.store,
        &fixture.root,
        "agent_memory",
        &json!({
            "action":"record","key":"test-command","kind":"fact","scope":"project/game",
            "content":"Use npm run test:unit.","source":"package.json@def456","evidence":"scripts.test:unit"
        }),
    )
    .unwrap();
    assert_eq!(first["memory"]["version"], 1);
    assert_eq!(second["memory"]["version"], 2);
    assert_eq!(second["memory"]["supersedes"], first["memory"]["id"]);
    let current = memories(&fixture.root, "test-command", 10).unwrap();
    assert_eq!(current.len(), 1);
    assert_eq!(current[0].source, "package.json@def456");
    let history =
        memory_history(&fixture.root, "project/game", "fact", "test-command", 10).unwrap();
    assert_eq!(history.len(), 2);
    assert_eq!(history[1].status, "superseded");
    assert_eq!(
        std::fs::read(echo).unwrap(),
        b"exact raw conversation bytes\0do not replace"
    );
}

#[test]
fn evidence_search_is_literal_and_bounded() {
    let fixture = Fixture::new();
    for (key, content) in [
        ("percent", "CPU 100% measured"),
        ("plain", "A different fact"),
    ] {
        execute(
            &fixture.store,
            &fixture.root,
            "agent_memory",
            &json!({
                "action":"record","key":key,"kind":"lesson","content":content,"source":"test/fixture"
            }),
        )
        .unwrap();
    }
    assert_eq!(memories(&fixture.root, "%", 10).unwrap().len(), 1);
    assert_eq!(memories(&fixture.root, "' OR 1=1 --", 10).unwrap().len(), 0);
    assert_eq!(memories(&fixture.root, "", 1).unwrap().len(), 1);
    assert!(memories(&fixture.root, &"x".repeat(513), 10).is_err());
    assert!(memories(&fixture.root, "", 101).is_err());
    assert!(execute(
        &fixture.store,
        &fixture.root,
        "agent_memory",
        &json!({
            "action":"record","key":"no-source","kind":"fact","content":"An unsupported assertion","source":""
        })
    )
    .is_err());
    assert!(execute(
        &fixture.store,
        &fixture.root,
        "agent_memory",
        &json!({"action":"search","query":42})
    )
    .is_err());
    assert!(execute(
        &fixture.store,
        &fixture.root,
        "app_control",
        &json!({"action":"set","settings":{},"source":false})
    )
    .is_err());
    assert!(execute(
        &fixture.store,
        &fixture.root,
        "skill_library",
        &json!({"action":"read","path":"secret.txt"})
    )
    .is_err());
}

#[test]
fn activity_is_durable_redacted_and_duplicate_ids_cannot_rewrite_history() {
    let fixture = Fixture::new();
    let event = ActivityEvent {
        id: "actual-change".into(),
        timestamp: "2026-10-06T10:00:00Z".into(),
        category: "studio".into(),
        action: "completed".into(),
        summary: "Job produced output.webm".into(),
        source: "studio/jobs/one".into(),
        details: json!({"path":"output.webm","apiKey":"opaque-secret"}),
        conversation_id: Some("chat-one".into()),
        project_id: None,
    };
    record_activity(&fixture.root, &event).unwrap();
    record_activity(&fixture.root, &event).unwrap();
    let reopened = activity(&fixture.root, "output.webm", 10).unwrap();
    assert_eq!(reopened.len(), 1);
    assert_eq!(reopened[0].details["apiKey"], "[REDACTED]");
    let mut changed = event.clone();
    changed.summary = "Pretend this ran a different job".into();
    assert!(record_activity(&fixture.root, &changed).is_err());
}

#[test]
fn disabling_memory_prevents_agent_recall_and_recording() {
    let fixture = Fixture::new();
    save_configuration(&fixture.store, config(json!({"memoryEnabled":false}))).unwrap();
    assert!(execute(
        &fixture.store,
        &fixture.root,
        "agent_memory",
        &json!({"action":"search"})
    )
    .is_err());
    assert!(execute(
        &fixture.store,
        &fixture.root,
        "agent_memory",
        &json!({
            "action":"record","key":"one","content":"A fact","kind":"fact","source":"fixture"
        })
    )
    .is_err());
}

#[test]
fn deleting_derived_memory_leaves_raw_echo_intact_even_when_recall_is_disabled() {
    let fixture = Fixture::new();
    std::fs::write(
        fixture.root.join("raw-echo.opencore"),
        b"unaltered raw history",
    )
    .unwrap();
    let recorded = execute(
        &fixture.store,
        &fixture.root,
        "agent_memory",
        &json!({
            "action":"record","key":"one","kind":"fact","content":"One observed fact","source":"fixture/observation"
        }),
    )
    .unwrap();
    save_configuration(&fixture.store, config(json!({"memoryEnabled":false}))).unwrap();
    let deleted = execute(
        &fixture.store,
        &fixture.root,
        "agent_memory",
        &json!({
            "action":"delete","id":recorded["memory"]["id"]
        }),
    )
    .unwrap();
    assert_eq!(deleted["deleted"], true);
    assert_eq!(deleted["persisted"], true);
    assert!(memories(&fixture.root, "", 10).unwrap().is_empty());
    assert_eq!(
        std::fs::read(fixture.root.join("raw-echo.opencore")).unwrap(),
        b"unaltered raw history"
    );
}

#[test]
fn plugin_mcp_entries_activate_only_for_enabled_valid_plugins() {
    let fixture = Fixture::new();
    let root = fixture.root.join("portable-tools");
    std::fs::create_dir_all(root.join("bin")).unwrap();
    std::fs::write(
        root.join("bin/server.mjs"),
        "// A fixture, never launched during discovery.\n",
    )
    .unwrap();
    std::fs::write(
        root.join("opencore-plugin.json"),
        json!({
            "id":"portable-tools","name":"Portable tools","skills":[],
            "mcpServers":[{"id":"portable-http","name":"portable-http","url":"https://example.test/mcp"},
              {"id":"portable-stdio","name":"portable-stdio","command":"node","args":["plugin-file:bin/server.mjs"]}]
        })
        .to_string(),
    )
    .unwrap();
    let mut saved = config(json!({"pluginDirectories":[root.to_string_lossy()]}));
    let active = mcp_configuration(&saved).unwrap();
    assert_eq!(active["portable-http"]["url"], "https://example.test/mcp");
    assert_eq!(
        PathBuf::from(active["portable-stdio"]["args"][0].as_str().unwrap()),
        std::fs::canonicalize(root.join("bin/server.mjs")).unwrap()
    );
    saved.disabled_plugins.push("portable-tools".into());
    assert!(mcp_configuration(&saved)
        .unwrap()
        .get("portable-http")
        .is_none());
    saved.disabled_plugins.clear();
    saved.mcp_servers = vec![serde_json::from_value(
        json!({"id":"explicit","name":"portable-http","command":"node"}),
    )
    .unwrap()];
    assert!(mcp_configuration(&saved).is_err());
}

#[test]
fn scoped_keys_with_redaction_still_supersede_the_same_saved_memory() {
    let fixture = Fixture::new();
    for content in ["First observation", "Newer observation"] {
        execute(
            &fixture.store,
            &fixture.root,
            "agent_memory",
            &json!({
                "action":"record","key":"hf_tokenlikeexample","kind":"fact","content":content,"source":"fixture"
            }),
        )
        .unwrap();
    }
    let saved = memories(&fixture.root, "", 10).unwrap();
    assert_eq!(saved.len(), 1);
    assert_eq!(saved[0].version, 2);
}

#[test]
fn custom_skill_body_loads_only_by_discovered_identifier() {
    let fixture = Fixture::new();
    let directory = fixture.root.join("skills").join("safe");
    std::fs::create_dir_all(&directory).unwrap();
    std::fs::write(directory.join("SKILL.md"), "---\nname: safe-skill\ndescription: Inspect the actual build before changing it.\n---\n# Safe skill\nSpecific instructions live here.\n").unwrap();
    let saved = config(json!({"skillDirectories":[fixture.root.join("skills").to_string_lossy()]}));
    save_configuration(&fixture.store, saved.clone()).unwrap();
    let list = skills(&saved).unwrap();
    let skill = list
        .iter()
        .find(|skill| skill.name == "safe-skill")
        .unwrap();
    let index = instruction_text(&saved);
    assert!(index.contains("safe-skill"));
    assert!(!index.contains("Specific instructions live here"));
    let read = execute(
        &fixture.store,
        &fixture.root,
        "skill_library",
        &json!({"action":"read","id":skill.id}),
    )
    .unwrap();
    assert!(read["content"]
        .as_str()
        .unwrap()
        .contains("Specific instructions live here"));
    assert!(execute(
        &fixture.store,
        &fixture.root,
        "skill_library",
        &json!({"action":"read","id":"../../outside/SKILL.md"})
    )
    .is_err());
}

#[test]
fn plugin_scan_rejects_traversal_and_never_executes_hooks() {
    let fixture = Fixture::new();
    let plugins_root = fixture.root.join("plugins");
    let bad = plugins_root.join("bad");
    let safe = plugins_root.join("safe");
    let outside = fixture.root.join("outside");
    std::fs::create_dir_all(&bad).unwrap();
    std::fs::create_dir_all(safe.join("skills").join("test")).unwrap();
    std::fs::create_dir_all(&outside).unwrap();
    std::fs::write(
        outside.join("SKILL.md"),
        "---\nname: escaped-skill\ndescription: Never load this.\n---\nOutside body",
    )
    .unwrap();
    std::fs::write(
        bad.join("opencore-plugin.json"),
        json!({
            "id":"bad","name":"Bad plugin","skills":["../../outside"]
        })
        .to_string(),
    )
    .unwrap();
    std::fs::write(
        safe.join("opencore-plugin.json"),
        json!({
            "id":"safe","name":"Safe plugin","skills":["skills"],
            "hooks":{"install":"create-side-effect"}
        })
        .to_string(),
    )
    .unwrap();
    std::fs::write(
        safe.join("skills/test/SKILL.md"),
        "---\nname: plugin-skill\ndescription: A confined plugin skill.\n---\nBody",
    )
    .unwrap();
    let saved = config(json!({"pluginDirectories":[plugins_root.to_string_lossy()]}));
    let list = plugins(&saved).unwrap();
    let rejected = list.iter().find(|plugin| plugin.id == "bad").unwrap();
    assert!(!rejected.enabled);
    assert!(!rejected.warnings.is_empty());
    let found = skills(&saved).unwrap();
    assert!(found.iter().any(|skill| skill.name == "plugin-skill"));
    assert!(!found.iter().any(|skill| skill.name == "escaped-skill"));
    assert!(!fixture.root.join("create-side-effect").exists());
}

#[cfg(unix)]
#[test]
fn custom_skill_symlinks_cannot_escape_the_authorized_directory() {
    let fixture = Fixture::new();
    let root = fixture.root.join("skills");
    let outside = fixture.root.join("outside");
    std::fs::create_dir_all(&root).unwrap();
    std::fs::create_dir_all(&outside).unwrap();
    std::fs::write(
        outside.join("SKILL.md"),
        "---\nname: external\ndescription: External.\n---\nDo not load",
    )
    .unwrap();
    std::os::unix::fs::symlink(&outside, root.join("escape")).unwrap();
    let saved = config(json!({"skillDirectories":[root.to_string_lossy()]}));
    assert!(!skills(&saved)
        .unwrap()
        .iter()
        .any(|skill| skill.name == "external"));
}
