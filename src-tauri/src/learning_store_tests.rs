use super::*;
use flate2::{write::ZlibEncoder, Compression};
use std::collections::BTreeSet;

struct Fixture {
    store: Arc<LearningStore>,
    root: PathBuf,
    _cleanup: Cleanup,
}

struct Cleanup(PathBuf);
impl Drop for Cleanup {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("opencore-learning-{}", uuid::Uuid::new_v4()));
        let store = LearningStore::new(root.join("learning")).unwrap();
        Self {
            store,
            _cleanup: Cleanup(root.clone()),
            root,
        }
    }

    fn sample(&self, id: &str, conversation: &str, content: &str, evidence: &str) -> Value {
        self.store.ingest(&json!({
            "sourceKind":"receipt", "sourceId":id, "conversationId":conversation,
            "timestamp":"2026-10-07T10:11:12.345Z", "role":"assistant", "kind":"receipt",
            "content":content, "evidenceLabel":evidence,
            "training":{"messages":[{"role":"user","content":"Question"},{"role":"assistant","content":content}]}
        })).unwrap()["record"].clone()
    }
}

#[test]
fn exact_multiline_sources_append_revisions_and_keep_original_timestamps() {
    let fixture = Fixture::new();
    let content = "first,\"quoted\"\r\nשלום\n\tlast\0";
    let raw = "{ \"message\" : \"first\\nlast\" }\r\n";
    let mut source = json!({"sourceKind":"receipt","sourceId":"one","conversationId":"chat",
        "timestamp":"2026-10-07T13:14:15.123+03:00","content":content,"rawText":raw,
        "metadata":{"status":"failed","error":"exact\nproblem"}});
    let first = fixture.store.ingest(&source).unwrap();
    assert_eq!(first["inserted"], true);
    assert_eq!(
        first["record"]["content"].as_str().unwrap().as_bytes(),
        content.as_bytes()
    );
    assert_eq!(first["record"]["rawText"], raw);
    assert_eq!(
        first["record"]["rawSha256"].as_str().unwrap(),
        format!("{:x}", Sha256::digest(raw.as_bytes()))
    );
    assert_eq!(first["record"]["rawHashKind"], "exact-utf8-bytes");
    assert_eq!(first["record"]["timestamp"], "2026-10-07T10:14:15.123Z");
    assert_eq!(
        first["record"]["sourceTimestamp"],
        "2026-10-07T13:14:15.123+03:00"
    );
    assert_eq!(first["record"]["knownFailure"], true);
    assert_eq!(fixture.store.ingest(&source).unwrap()["inserted"], false);
    source["content"] = json!("corrected source\n");
    let second = fixture.store.ingest(&source).unwrap();
    assert_eq!(second["record"]["revision"], 2);
    assert_eq!(second["record"]["previousRecordId"], first["record"]["id"]);
    let original = fixture
        .store
        .query(&json!({"id":first["record"]["id"]}))
        .unwrap();
    assert_eq!(original["record"]["content"], content);
    assert_eq!(
        fixture
            .store
            .query(&json!({"sourceId":"one","latestOnly":false}))
            .unwrap()["total"],
        2
    );
    assert_eq!(
        fixture
            .store
            .query(&json!({"sourceId":"one","latestOnly":true}))
            .unwrap()["total"],
        1
    );
}

#[test]
fn annotations_are_append_only_and_cannot_relabel_a_failed_source_into_positive_sft() {
    let fixture = Fixture::new();
    let record = fixture.store.ingest(&json!({"sourceKind":"receipt","sourceId":"failed","conversationId":"chat",
        "content":"bad answer","metadata":{"exitCode":1},"training":{"prompt":"q","completion":"bad answer"}})).unwrap()["record"].clone();
    fixture.store.annotate(&json!({"id":record["id"],"evidenceLabel":"verified-success","status":"completed","note":"review one"})).unwrap();
    fixture
        .store
        .annotate(&json!({"id":record["id"],"note":"review two"}))
        .unwrap();
    let after = fixture.store.query(&json!({"id":record["id"]})).unwrap();
    assert_eq!(after["record"]["raw"], record["raw"]);
    assert_eq!(after["record"]["knownFailure"], true);
    assert_eq!(after["record"]["annotations"].as_array().unwrap().len(), 2);
    assert_eq!(after["record"]["evidenceLabel"], "verified-success");
    let exported = fixture
        .store
        .export_dataset(&json!({"format":"sft","verifiedOnly":false}))
        .unwrap();
    assert_eq!(exported["counts"]["samples"], 0);
    assert!(exported["exclusions"]
        .as_array()
        .unwrap()
        .iter()
        .any(|row| row["reason"] == "known-failure"));
}

#[test]
fn pagination_freezes_the_source_snapshot_and_searches_full_failure_text() {
    let fixture = Fixture::new();
    for index in 0..123 {
        fixture.store.ingest(&json!({"sourceKind":"receipt","sourceId":format!("source-{index}"),"conversationId":"chat",
            "content":format!("{}needle-{index}", "x".repeat(9000)),"metadata":{"status":"failed"}})).unwrap();
    }
    let first = fixture
        .store
        .query(&json!({"limit":17,"search":"needle","status":"failed"}))
        .unwrap();
    assert_eq!(first["total"], 123);
    fixture.store.ingest(&json!({"sourceKind":"receipt","sourceId":"late","content":"needle-late","status":"failed"})).unwrap();
    let mut ids = BTreeSet::new();
    let mut page = first;
    loop {
        assert_eq!(page["total"], 123);
        for record in page["records"].as_array().unwrap() {
            assert!(ids.insert(record["id"].as_str().unwrap().to_string()));
            assert!(record["content"].as_str().unwrap().len() > 9000);
        }
        let Some(cursor) = page["nextCursor"].as_str() else {
            break;
        };
        page = fixture
            .store
            .query(&json!({"limit":17,"search":"needle","status":"failed","cursor":cursor}))
            .unwrap();
    }
    assert_eq!(ids.len(), 123);
    let counts = fixture
        .store
        .query(&json!({"countOnly":true,"status":"failed"}))
        .unwrap();
    assert_eq!(counts["total"], 124);
    assert!(counts["records"].as_array().unwrap().is_empty());
    assert_eq!(counts["countOnly"], true);
    assert!(fixture.store.query(&json!({"limit":0})).is_err());
}

#[test]
fn pagination_keeps_its_evidence_filter_when_reviews_arrive_between_pages() {
    let fixture = Fixture::new();
    let mut ids = Vec::new();
    for index in 0..5 {
        ids.push(fixture.store.ingest(&json!({"sourceKind":"receipt","sourceId":format!("failed-{index}"),"content":"failure","status":"failed"})).unwrap()["record"]["id"].clone());
    }
    let first = fixture
        .store
        .query(&json!({"limit":2,"status":"failed"}))
        .unwrap();
    for id in ids {
        fixture
            .store
            .annotate(&json!({"id":id,"status":"completed"}))
            .unwrap();
    }
    let mut page = first;
    let mut count = 0;
    loop {
        assert_eq!(page["total"], 5);
        for record in page["records"].as_array().unwrap() {
            assert_eq!(record["status"], "failed");
            count += 1;
        }
        let Some(cursor) = page["nextCursor"].as_str() else {
            break;
        };
        page = fixture
            .store
            .query(&json!({"limit":2,"status":"failed","cursor":cursor}))
            .unwrap();
    }
    assert_eq!(count, 5);
    assert_eq!(
        fixture.store.query(&json!({"status":"completed"})).unwrap()["total"],
        5
    );
}

#[test]
fn dated_activity_orders_by_source_time_and_pages_equal_second_precision() {
    let fixture = Fixture::new();
    for (id, timestamp) in [
        ("newest", "2026-10-07T10:11:12.000000001Z"),
        ("oldest", "2020-01-01T00:00:00Z"),
        ("middle", "2026-10-07T10:11:12Z"),
    ] {
        fixture
            .store
            .ingest(
                &json!({"sourceKind":"receipt","sourceId":id,"timestamp":timestamp,"content":id}),
            )
            .unwrap();
    }
    let first = fixture.store.query(&json!({"limit":1})).unwrap();
    assert_eq!(first["records"][0]["content"], "newest");
    let second = fixture
        .store
        .query(&json!({"limit":1,"cursor":first["nextCursor"]}))
        .unwrap();
    assert_eq!(second["records"][0]["content"], "middle");
    let third = fixture
        .store
        .query(&json!({"limit":1,"cursor":second["nextCursor"]}))
        .unwrap();
    assert_eq!(third["records"][0]["content"], "oldest");
    assert!(third["nextCursor"].is_null());
}

#[test]
fn source_sync_reads_complete_timeline_echo_pages_events_summaries_and_receipts() {
    let fixture = Fixture::new();
    let history = EventStore::open(&fixture.root.join("history.sqlite3")).unwrap();
    history
        .ensure_conversation("chat", "OpenCore", "echo", "Chat")
        .unwrap();
    for index in 0..1101 {
        history
            .add_timeline(
                "chat",
                "message",
                "user",
                "OpenCore",
                "User",
                &format!("timeline-{index}"),
                &json!({}),
            )
            .unwrap();
    }
    let progress = history
        .add_timeline(
            "chat",
            "echo_import",
            "system",
            "OpenCore",
            "Import",
            "0/1",
            &json!({"current":0}),
        )
        .unwrap();
    let echo = fixture.root.join("echo");
    std::fs::create_dir_all(&echo).unwrap();
    let db = Connection::open(echo.join("one.db")).unwrap();
    db.execute_batch("CREATE TABLE pages(page_id TEXT PRIMARY KEY,conversation_id TEXT,offset_start INTEGER,offset_end INTEGER,timestamp REAL,content_hash TEXT,compressed_bytes BLOB);
        CREATE TABLE source_events(event_id TEXT PRIMARY KEY,conversation_id TEXT,timestamp REAL,kind TEXT,role TEXT,source TEXT,title TEXT,content TEXT,metadata TEXT);
        CREATE TABLE derived_summaries(conversation_id TEXT,generated_at REAL,source_pages INTEGER,model_calls INTEGER,truncated INTEGER,incomplete INTEGER,content TEXT);").unwrap();
    let text = format!("{}\r\nשלום\n", "exact page,".repeat(4000));
    let mut compressor = ZlibEncoder::new(Vec::new(), Compression::default());
    compressor.write_all(text.as_bytes()).unwrap();
    let compressed = compressor.finish().unwrap();
    db.execute(
        "INSERT INTO pages VALUES(?1,'chat',0,?2,1791367872.5,?3,?4)",
        params![
            "a".repeat(64),
            text.len() as i64,
            format!("{:x}", Sha256::digest(text.as_bytes())),
            compressed
        ],
    )
    .unwrap();
    let metadata_text = format!("{{ \"raw\": \"{}\" }}", "m".repeat(70000));
    db.execute("INSERT INTO source_events VALUES(?1,'chat',1791367872.5,'tool_result','tool','ECHO','Result',?2,?3)",params!["b".repeat(64),text,metadata_text]).unwrap();
    db.execute(
        "INSERT INTO derived_summaries VALUES('chat',1791367872.5,1,1,0,0,'derived\nsummary')",
        [],
    )
    .unwrap();
    let receipt_text = "{ \"id\":\"receipt-one\", \"conversationId\":\"chat\", \"timestamp\":\"2026-10-07T10:11:12Z\", \"status\":\"failed\", \"error\":\"precise\\nerror\" }\r\n";
    std::fs::write(echo.join("receipt.json"), receipt_text).unwrap();
    let synced = fixture.store.sync_sources(&history, &echo).unwrap();
    assert_eq!(synced["inserted"], 1106);
    let page = fixture
        .store
        .query(&json!({"sourceKind":"echo-page"}))
        .unwrap();
    assert_eq!(page["records"][0]["content"], text);
    let event = fixture
        .store
        .query(&json!({"sourceKind":"echo-event"}))
        .unwrap();
    assert_eq!(event["records"][0]["raw"]["metadataText"], metadata_text);
    assert_eq!(event["records"][0]["content"], text);
    let receipt = fixture
        .store
        .query(&json!({"sourceKind":"receipt"}))
        .unwrap();
    assert_eq!(receipt["records"][0]["rawText"], receipt_text);
    assert_eq!(receipt["records"][0]["knownFailure"], true);
    assert_eq!(
        fixture.store.sync_sources(&history, &echo).unwrap()["inserted"],
        0
    );
    history
        .update_timeline(progress, "1/1", &json!({"current":1}))
        .unwrap();
    assert_eq!(
        fixture.store.sync_sources(&history, &echo).unwrap()["inserted"],
        1
    );
}

#[test]
fn corrupt_archive_pages_are_retained_with_their_raw_bytes_and_exact_failure() {
    let fixture = Fixture::new();
    let history = EventStore::open(&fixture.root.join("history.sqlite3")).unwrap();
    let echo = fixture.root.join("echo");
    std::fs::create_dir_all(&echo).unwrap();
    let db = Connection::open(echo.join("corrupt.db")).unwrap();
    db.execute_batch("CREATE TABLE pages(page_id TEXT PRIMARY KEY,conversation_id TEXT,offset_start INTEGER,offset_end INTEGER,timestamp REAL,content_hash TEXT,compressed_bytes BLOB)").unwrap();
    db.execute(
        "INSERT INTO pages VALUES('bad','chat',0,3,1791367872.5,'wrong',?1)",
        [vec![0_u8, 1, 2, 3]],
    )
    .unwrap();
    assert_eq!(
        fixture.store.sync_sources(&history, &echo).unwrap()["inserted"],
        1
    );
    let page = fixture.store.query(&json!({"status":"failed"})).unwrap();
    assert_eq!(page["total"], 1);
    assert_eq!(
        page["records"][0]["raw"]["compressedBytesBase64"],
        "AAECAw=="
    );
    assert!(!page["records"][0]["metadata"]["integrityError"]
        .as_str()
        .unwrap()
        .is_empty());
}

#[test]
fn malformed_timeline_metadata_is_retained_and_excluded_from_training() {
    let fixture = Fixture::new();
    let history_path = fixture.root.join("history.sqlite3");
    let history = EventStore::open(&history_path).unwrap();
    history
        .ensure_conversation("chat", "OpenCore", "echo", "Chat")
        .unwrap();
    history
        .add_timeline(
            "chat",
            "message",
            "user",
            "OpenCore",
            "User",
            "question",
            &json!({}),
        )
        .unwrap();
    let id = history
        .add_timeline(
            "chat",
            "message",
            "assistant",
            "OpenCore",
            "Assistant",
            "untrustworthy",
            &json!({}),
        )
        .unwrap();
    let raw_metadata = "{ \"status\": \"failed\", broken\r\n";
    let db = Connection::open(&history_path).unwrap();
    db.execute(
        "UPDATE timeline SET metadata=?1 WHERE id=?2",
        params![raw_metadata, id],
    )
    .unwrap();
    fixture
        .store
        .sync_sources(&history, &fixture.root.join("missing-echo"))
        .unwrap();
    let page = fixture.store.query(&json!({"status":"failed"})).unwrap();
    assert_eq!(page["total"], 1);
    assert_eq!(page["records"][0]["raw"]["metadataText"], raw_metadata);
    assert_eq!(page["records"][0]["knownFailure"], true);
    let export = fixture
        .store
        .export_dataset(&json!({"format":"sft","verifiedOnly":false}))
        .unwrap();
    assert_eq!(export["counts"]["samples"], 0);
    assert!(export["exclusions"]
        .as_array()
        .unwrap()
        .iter()
        .any(|row| row["reason"] == "known-failure"));
}

#[test]
fn raw_exports_stream_every_record_and_csv_preserves_quotes_and_newlines() {
    let fixture = Fixture::new();
    let content = "comma,\"quote\"\r\nnew line\nשלום";
    for index in 0..507 {
        fixture.store.ingest(&json!({"sourceKind":"receipt","sourceId":format!("row-{index}"),"content":content})).unwrap();
    }
    let export = fixture
        .store
        .export_dataset(&json!({"format":"jsonl"}))
        .unwrap();
    let lines = std::fs::read_to_string(export["path"].as_str().unwrap()).unwrap();
    let rows = lines
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(rows.len(), 507);
    assert!(rows.iter().all(|row| row["content"] == content));
    let csv = fixture
        .store
        .export_dataset(&json!({"format":"csv"}))
        .unwrap();
    let text = std::fs::read_to_string(csv["path"].as_str().unwrap()).unwrap();
    assert!(text.contains("\"comma,\"\"quote\"\"\r\nnew line\nשלום\""));
    let parsed = parse_csv(&text);
    assert_eq!(parsed.len(), 508);
    let content_column = parsed[0]
        .iter()
        .position(|column| column == "content")
        .unwrap();
    let raw_column = parsed[0]
        .iter()
        .position(|column| column == "recordJson")
        .unwrap();
    for row in parsed.iter().skip(1) {
        assert_eq!(row[content_column], content);
        let raw: Value = serde_json::from_str(&row[raw_column]).unwrap();
        assert_eq!(raw["content"], content);
    }
    assert_eq!(csv["counts"]["records"], 507);
    assert_eq!(
        export["sha256"].as_str().unwrap(),
        format!("{:x}", Sha256::digest(lines.as_bytes()))
    );
}

// Deliberately independent CSV reader used to check complete records, not string fragments.
fn parse_csv(source: &str) -> Vec<Vec<String>> {
    let mut rows = Vec::new();
    let mut row = Vec::new();
    let mut field = String::new();
    let mut quoted = false;
    let mut chars = source.chars().peekable();
    while let Some(character) = chars.next() {
        match character {
            '"' if quoted && chars.peek() == Some(&'"') => {
                chars.next();
                field.push('"');
            }
            '"' => quoted = !quoted,
            ',' if !quoted => {
                row.push(std::mem::take(&mut field));
            }
            '\r' if !quoted && chars.peek() == Some(&'\n') => {
                chars.next();
                row.push(std::mem::take(&mut field));
                rows.push(std::mem::take(&mut row));
            }
            '\n' if !quoted => {
                row.push(std::mem::take(&mut field));
                rows.push(std::mem::take(&mut row));
            }
            character => field.push(character),
        }
    }
    assert!(!quoted);
    if !row.is_empty() || !field.is_empty() {
        row.push(field);
        rows.push(row);
    }
    rows
}

#[test]
fn dpo_requires_distinct_preferences_and_keeps_grouped_provenance() {
    let fixture = Fixture::new();
    for index in 0..4 {
        fixture.store.ingest(&json!({"sourceKind":"receipt","sourceId":format!("preference-{index}"),"conversationId":format!("chat-{index}"),
            "content":"reviewed preference","evidenceLabel":"verified-success",
            "training":{"prompt":format!("q-{index}"),"chosen":format!("good-{index}"),"rejected":format!("bad-{index}")}})).unwrap();
    }
    fixture.store.ingest(&json!({"sourceKind":"receipt","sourceId":"invalid-preference","evidenceLabel":"verified-success",
        "training":{"prompt":"q","chosen":"same","rejected":"same"}})).unwrap();
    let export = fixture
        .store
        .export_dataset(&json!({"format":"dpo"}))
        .unwrap();
    assert_eq!(export["counts"]["samples"], 4);
    assert_eq!(export["counts"]["excluded"], 1);
    let manifest: Value =
        serde_json::from_slice(&std::fs::read(export["manifestPath"].as_str().unwrap()).unwrap())
            .unwrap();
    let train = manifest["train"]["sourceIds"].as_array().unwrap();
    let validation = manifest["validation"]["sourceIds"].as_array().unwrap();
    assert!(!validation.is_empty());
    assert!(train.iter().all(|source| !validation.contains(source)));
}

#[test]
fn optional_null_record_selection_exports_all_eligible_sources() {
    let fixture = Fixture::new();
    fixture.sample("selected", "chat", "answer", "verified-success");
    let export = fixture
        .store
        .export_dataset(&json!({"format":"sft","recordIds":null}))
        .unwrap();
    assert_eq!(export["counts"]["samples"], 1);
    assert_eq!(
        fixture.store.query(&json!({"recordIds":null})).unwrap()["total"],
        1
    );
    let empty = fixture
        .store
        .export_dataset(&json!({"format":"sft","recordIds":[]}))
        .unwrap();
    assert_eq!(empty["counts"]["records"], 0);
}

#[test]
fn frozen_training_split_groups_source_conversations_and_records_exclusions() {
    let fixture = Fixture::new();
    for conversation in 0..8 {
        for index in 0..3 {
            fixture.sample(
                &format!("sample-{conversation}-{index}"),
                &format!("chat-{conversation}"),
                &format!("answer-{conversation}-{index}"),
                "unverified-self-distillation",
            );
        }
    }
    fixture.store.ingest(&json!({"sourceKind":"receipt","sourceId":"bad","conversationId":"bad-chat","content":"failed","status":"failed","training":{"prompt":"q","completion":"bad"}})).unwrap();
    fixture.store.ingest(&json!({"sourceKind":"echo-summary","sourceId":"summary","conversationId":"chat-0","content":"summary"})).unwrap();
    fixture.sample(
        "too-long",
        "long-chat",
        &"z".repeat(500),
        "verified-success",
    );
    let export = fixture.store.export_dataset(&json!({"format":"sft","verifiedOnly":false,"validationFraction":0.25,"seed":42,"maxCharacters":100})).unwrap();
    let read = |key: &str| {
        std::fs::read_to_string(export[key].as_str().unwrap())
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>()
    };
    let train = read("trainPath");
    let validation = read("validationPath");
    assert_eq!(train.len() + validation.len(), 24);
    assert!(!train.is_empty() && !validation.is_empty());
    let groups = |rows: &Vec<Value>| {
        rows.iter()
            .map(|row| row["sourceConversationId"].as_str().unwrap().to_string())
            .collect::<BTreeSet<_>>()
    };
    assert!(groups(&train).is_disjoint(&groups(&validation)));
    assert!(train
        .iter()
        .chain(validation.iter())
        .all(|row| row["evidenceLabel"] == "unverified-self-distillation"));
    let reasons = export["exclusions"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| row["reason"].as_str().unwrap())
        .collect::<BTreeSet<_>>();
    assert!(reasons.contains("known-failure"));
    assert!(reasons.contains("no-trainable-sample"));
    assert!(reasons.contains("character-limit"));
    let manifest_bytes = std::fs::read(export["manifestPath"].as_str().unwrap()).unwrap();
    let manifest: Value = serde_json::from_slice(&manifest_bytes).unwrap();
    assert_eq!(
        manifest["files"]["train"]["sha256"].as_str().unwrap(),
        format!(
            "{:x}",
            Sha256::digest(std::fs::read(export["trainPath"].as_str().unwrap()).unwrap())
        )
    );
    assert_eq!(manifest["sourceRecords"].as_array().unwrap().len(), 27);
    fixture.sample("later", "chat-later", "later answer", "verified-success");
    assert_eq!(
        std::fs::read(export["manifestPath"].as_str().unwrap()).unwrap(),
        manifest_bytes
    );
    let verified = fixture
        .store
        .export_dataset(&json!({"format":"sft","verifiedOnly":true}))
        .unwrap();
    assert_eq!(verified["counts"]["samples"], 2);
}

#[test]
fn timeline_pairs_use_real_user_context_and_superseded_revisions_are_excluded() {
    let fixture = Fixture::new();
    fixture.store.ingest(&json!({"sourceKind":"timeline","sourceId":"user","conversationId":"chat","timestamp":"2026-10-07T10:00:00Z","kind":"message","role":"user","content":"actual user"})).unwrap();
    let assistant = json!({"sourceKind":"timeline","sourceId":"assistant","conversationId":"chat","timestamp":"2026-10-07T10:00:01Z","kind":"message","role":"assistant","content":"old answer"});
    let old = fixture.store.ingest(&assistant).unwrap()["record"].clone();
    let mut corrected = assistant;
    corrected["content"] = json!("new answer");
    fixture.store.ingest(&corrected).unwrap();
    let export = fixture
        .store
        .export_dataset(&json!({"format":"sft","verifiedOnly":false}))
        .unwrap();
    let line = std::fs::read_to_string(export["trainPath"].as_str().unwrap()).unwrap();
    let sample: Value = serde_json::from_str(line.trim()).unwrap();
    assert_eq!(
        sample["messages"],
        json!([{"role":"user","content":"actual user"},{"role":"assistant","content":"new answer"}])
    );
    assert!(!sample["sourceRecordIds"]
        .as_array()
        .unwrap()
        .contains(&old["id"]));
    let selected_old = fixture
        .store
        .export_dataset(&json!({"format":"sft","verifiedOnly":false,"recordIds":[old["id"]]}))
        .unwrap();
    assert_eq!(selected_old["counts"]["samples"], 0);
    assert_eq!(
        selected_old["exclusions"][0]["reason"],
        "superseded-source-revision"
    );
}

#[test]
fn conversation_scope_covers_queries_ids_annotations_and_exports_without_exposing_forks() {
    let fixture = Fixture::new();
    let own = fixture.sample("own", "project-a", "own answer", "verified-success");
    let sibling = fixture.sample(
        "sibling",
        "project-b",
        "shared project answer",
        "verified-success",
    );
    let foreign = fixture.sample(
        "foreign",
        "other-project",
        "private answer",
        "verified-success",
    );
    let branch=fixture.store.ingest(&json!({"sourceKind":"timeline","sourceId":"branch","conversationId":"private-fork","sourceConversationId":"project-a","content":"fork after cutoff"})).unwrap()["record"].clone();
    let music = fixture
        .store
        .ingest(
            &json!({"sourceKind":"music","sourceId":"song","content":"cross-studio song activity"}),
        )
        .unwrap()["record"]
        .clone();
    let allowed = json!(["project-a", "project-b"]);
    let page = fixture
        .store
        .query(&json!({"allowedConversationIds":allowed,"limit":100}))
        .unwrap();
    assert_eq!(page["total"], 3);
    let ids = page["records"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["id"].clone())
        .collect::<Vec<_>>();
    assert!(ids.contains(&own["id"]) && ids.contains(&sibling["id"]) && ids.contains(&music["id"]));
    assert!(!ids.contains(&foreign["id"]) && !ids.contains(&branch["id"]));
    for record in [foreign, branch] {
        assert!(fixture
            .store
            .query(&json!({"id":record["id"],"allowedConversationIds":allowed}))
            .is_err());
        assert!(fixture
            .store
            .annotate(
                &json!({"id":record["id"],"note":"not authorized","allowedConversationIds":allowed})
            )
            .is_err());
        assert!(fixture.store.export_dataset(&json!({"format":"jsonl","recordIds":[record["id"]],"allowedConversationIds":allowed})).is_err());
    }
    let export = fixture
        .store
        .export_dataset(&json!({"format":"jsonl","allowedConversationIds":allowed}))
        .unwrap();
    assert_eq!(export["counts"]["records"], 3);
    assert_eq!(
        fixture.store.query(&json!({"countOnly":true})).unwrap()["total"],
        5
    );
}

#[test]
fn a_failure_review_stays_excluded_after_a_later_success_annotation() {
    let fixture = Fixture::new();
    let record = fixture.sample(
        "initially-unverified",
        "conversation",
        "the same wrong answer",
        "unverified-self-distillation",
    );
    fixture
        .store
        .annotate(&json!({"id":record["id"],"evidenceLabel":"known-failure","status":"failed"}))
        .unwrap();
    fixture
        .store
        .annotate(
            &json!({"id":record["id"],"evidenceLabel":"verified-success","status":"completed"}),
        )
        .unwrap();
    let reviewed = fixture.store.query(&json!({"id":record["id"]})).unwrap();
    assert_eq!(reviewed["record"]["knownFailure"], true);
    assert_eq!(
        reviewed["record"]["annotations"].as_array().unwrap().len(),
        2
    );
    let exported = fixture
        .store
        .export_dataset(&json!({"format":"sft","verifiedOnly":false}))
        .unwrap();
    assert_eq!(exported["counts"]["samples"], 0);
    assert_eq!(exported["exclusions"][0]["reason"], "known-failure");
    let revised=fixture.store.ingest(&json!({"sourceKind":"receipt","sourceId":"initially-unverified","conversationId":"conversation","content":"the same wrong answer","metadata":{"profile":"benign metadata change"},"evidenceLabel":"verified-success","training":record["training"]})).unwrap()["record"].clone();
    assert_eq!(revised["knownFailure"], true);
    let repeated = fixture
        .store
        .export_dataset(&json!({"format":"sft","verifiedOnly":false}))
        .unwrap();
    assert_eq!(repeated["counts"]["samples"], 0);
    let corrected=fixture.store.ingest(&json!({"sourceKind":"receipt","sourceId":"initially-unverified","conversationId":"conversation","content":"new corrected bytes","evidenceLabel":"verified-success","training":{"prompt":"Question","completion":"new corrected bytes"}})).unwrap()["record"].clone();
    assert_eq!(corrected["knownFailure"], false);
}
