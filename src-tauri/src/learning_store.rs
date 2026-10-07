//! Immutable raw learning sources and append-only review annotations.
//! The ledger never replaces source text with a summary or treats a failed run as positive training.
use crate::store::EventStore;
use base64::Engine;
use chrono::{DateTime, SecondsFormat, Utc};
use flate2::read::ZlibDecoder;
use rusqlite::{
    params, params_from_iter, types::Value as SqlValue, Connection, OpenFlags, OptionalExtension,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

#[cfg(test)]
#[path = "learning_store_tests.rs"]
mod learning_store_tests;

pub struct LearningStore {
    root: PathBuf,
    connection: Mutex<Connection>,
    source_versions: Mutex<HashMap<String, String>>,
}

fn now() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::AutoSi, true)
}
fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn text<'a>(value: &'a Value, key: &str) -> &'a str {
    value[key].as_str().unwrap_or("")
}
fn json_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|error| error.to_string())
}
fn io_error(error: impl std::fmt::Display) -> String {
    error.to_string()
}

fn failure_status(status: &str) -> bool {
    matches!(
        status.to_ascii_lowercase().as_str(),
        "failed"
            | "failure"
            | "verified-failure"
            | "known-failure"
            | "error"
            | "rejected"
            | "cancelled"
            | "canceled"
            | "interrupted"
            | "corrected"
            | "invalid"
            | "timeout"
            | "timed-out"
            | "aborted"
    )
}

fn has_failure(value: &Value) -> bool {
    match value {
        Value::Object(fields) => fields.iter().any(|(key, value)| {
            let key = key.to_ascii_lowercase();
            ((key == "status" || key == "outcome" || key == "evidencelabel")
                && value.as_str().is_some_and(failure_status))
                || ((key == "error" || key == "integrityerror")
                    && !value.is_null()
                    && value.as_bool() != Some(false)
                    && value.as_str() != Some("")
                    && !value.as_object().is_some_and(|fields| fields.is_empty()))
                || ((key == "success" || key == "ok") && value == false)
                || ((key == "exitcode" || key == "exit_code" || key == "returncode")
                    && value.as_i64().is_some_and(|code| code != 0))
                || ((key == "failed" || key == "corrected") && value == true)
                || has_failure(value)
        }),
        Value::Array(items) => items.iter().any(has_failure),
        _ => false,
    }
}

fn incomplete_source(value: &Value) -> bool {
    match value {
        Value::Object(fields) => fields.iter().any(|(key, value)| {
            matches!(key.as_str(), "truncated" | "incomplete") && value == true
                || incomplete_source(value)
        }),
        Value::Array(items) => items.iter().any(incomplete_source),
        _ => false,
    }
}

fn timestamp(value: &Value, fallback: &str) -> (String, &'static str) {
    if let Some(raw) = value.as_str() {
        if let Ok(parsed) = DateTime::parse_from_rfc3339(raw) {
            return (
                parsed
                    .with_timezone(&Utc)
                    .to_rfc3339_opts(SecondsFormat::AutoSi, true),
                "source",
            );
        }
    } else if let Some(epoch) = value.as_f64().filter(|epoch| epoch.is_finite()) {
        let seconds = epoch.floor();
        if seconds >= i64::MIN as f64 && seconds < i64::MAX as f64 {
            let nanos = ((epoch - seconds) * 1_000_000_000.0).round() as u32;
            let (seconds, nanos) = if nanos == 1_000_000_000 {
                (seconds as i64 + 1, 0)
            } else {
                (seconds as i64, nanos)
            };
            if let Some(parsed) = DateTime::<Utc>::from_timestamp(seconds, nanos) {
                return (
                    parsed.to_rfc3339_opts(SecondsFormat::AutoSi, true),
                    "source",
                );
            }
        }
    }
    (
        fallback.to_string(),
        if value.is_null() {
            "ingestion"
        } else {
            "invalid-source-timestamp"
        },
    )
}

fn evidence_label(source: &Value, known_failure: bool) -> String {
    if known_failure {
        return if text(source, "status") == "corrected" {
            "corrected"
        } else {
            "failed"
        }
        .into();
    }
    let explicit = source["evidenceLabel"]
        .as_str()
        .or_else(|| source["metadata"]["evidenceLabel"].as_str());
    if let Some(label) = explicit.filter(|label| !label.is_empty()) {
        return label.to_string();
    }
    if text(source, "sourceKind") == "echo-summary" {
        "derived-summary".into()
    } else if text(source, "role") == "assistant" {
        "unverified-self-distillation".into()
    } else {
        "observed-record".into()
    }
}

impl LearningStore {
    pub fn new(root: PathBuf) -> Result<Arc<Self>, String> {
        std::fs::create_dir_all(&root).map_err(io_error)?;
        let connection = Connection::open(root.join("ledger.sqlite3")).map_err(io_error)?;
        connection
            .busy_timeout(std::time::Duration::from_secs(15))
            .map_err(io_error)?;
        connection.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS learning_records(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,
                source_kind TEXT NOT NULL,source_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
                source_conversation_id TEXT NOT NULL,revision INTEGER NOT NULL,source_sha256 TEXT NOT NULL,
                timestamp TEXT NOT NULL,payload TEXT NOT NULL,search_text TEXT NOT NULL,
                UNIQUE(source_kind,source_id,revision));
            CREATE INDEX IF NOT EXISTS learning_record_source ON learning_records(source_kind,source_id,sequence DESC);
            CREATE INDEX IF NOT EXISTS learning_record_conversation ON learning_records(conversation_id,sequence DESC);
            CREATE INDEX IF NOT EXISTS learning_record_time ON learning_records(timestamp DESC,sequence DESC);
            CREATE TABLE IF NOT EXISTS learning_annotations(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,record_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,payload TEXT NOT NULL,
                FOREIGN KEY(record_id) REFERENCES learning_records(id));
            CREATE INDEX IF NOT EXISTS learning_annotation_record ON learning_annotations(record_id,sequence);
            PRAGMA foreign_keys=ON;").map_err(io_error)?;
        Ok(Arc::new(Self {
            root,
            connection: Mutex::new(connection),
            source_versions: Mutex::new(HashMap::new()),
        }))
    }

    pub fn ingest(&self, source: &Value) -> Result<Value, String> {
        if !source.is_object() {
            return Err("A learning source must be a JSON object".into());
        }
        let source_kind = source["sourceKind"].as_str().unwrap_or("manual");
        let source_id = text(source, "sourceId");
        if source_kind.trim().is_empty() || source_id.trim().is_empty() {
            return Err("Learning sources require non-empty sourceKind and sourceId".into());
        }
        let source_sha = sha256(&json_bytes(source)?);
        let (raw_sha, raw_hash_kind) = if let Some(raw_text) = source["rawText"].as_str() {
            (sha256(raw_text.as_bytes()), "exact-utf8-bytes")
        } else if let Some(encoded) = source["rawBytesBase64"].as_str() {
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(encoded)
                .map_err(|error| format!("Invalid rawBytesBase64: {error}"))?;
            (sha256(&bytes), "exact-binary-bytes")
        } else {
            (
                sha256(&json_bytes(source.get("raw").unwrap_or(source))?),
                "canonical-json",
            )
        };
        let mut connection = self.connection.lock().map_err(io_error)?;
        let transaction = connection.transaction().map_err(io_error)?;
        let previous:Option<(String,i64,String,String)>=transaction.query_row(
            "SELECT id,revision,source_sha256,payload FROM learning_records WHERE source_kind=?1 AND source_id=?2 ORDER BY sequence DESC LIMIT 1",
            params![source_kind,source_id],|row|Ok((row.get(0)?,row.get(1)?,row.get(2)?,row.get(3)?))
        ).optional().map_err(io_error)?;
        if let Some((id, _, hash, _)) = &previous {
            if hash == &source_sha {
                let record = read_record(&transaction, id)?
                    .ok_or("Existing learning record could not be read")?;
                return Ok(json!({"record":record,"inserted":false}));
            }
        }
        let ingested_at = now();
        let source_timestamp = source.get("timestamp").cloned().unwrap_or(Value::Null);
        let (occurred_at, timestamp_origin) = timestamp(&source_timestamp, &ingested_at);
        let timestamp_order = DateTime::parse_from_rfc3339(&occurred_at)
            .map_err(io_error)?
            .with_timezone(&Utc)
            .to_rfc3339_opts(SecondsFormat::Nanos, true);
        let content = source["content"]
            .as_str()
            .map(str::to_string)
            .or_else(|| source["rawText"].as_str().map(str::to_string))
            .unwrap_or_else(|| source.get("raw").unwrap_or(source).to_string());
        let conversation_id = text(source, "conversationId");
        let source_conversation_id = source["sourceConversationId"]
            .as_str()
            .filter(|id| !id.is_empty())
            .unwrap_or(conversation_id);
        let source_conversation_id = if source_conversation_id.is_empty() {
            format!("source:{source_kind}:{source_id}")
        } else {
            source_conversation_id.to_string()
        };
        let revision = previous.as_ref().map_or(1, |previous| previous.1 + 1);
        let previous_id = previous.as_ref().map(|previous| previous.0.clone());
        let id = sha256(format!("{source_kind}\0{source_id}\0{revision}\0{source_sha}").as_bytes());
        let known_failure = has_failure(source)
            || source_content_failed(
                &transaction,
                source_kind,
                source_id,
                &sha256(content.as_bytes()),
                i64::MAX,
            )?;
        let evidence = evidence_label(source, known_failure);
        let source_status = source["status"]
            .as_str()
            .or_else(|| source["metadata"]["status"].as_str())
            .unwrap_or("");
        let status = if known_failure && !failure_status(source_status) {
            "failed"
        } else if source_status.is_empty() {
            "recorded"
        } else {
            source_status
        };
        let record = json!({
            "id":id,"sourceKind":source_kind,"sourceId":source_id,"conversationId":conversation_id,
            "sourceConversationId":source_conversation_id,"revision":revision,"previousRecordId":previous_id,
            "timestamp":occurred_at,"sourceTimestamp":source_timestamp,"timestampOrigin":timestamp_origin,"ingestedAt":ingested_at,
            "kind":source["kind"].as_str().unwrap_or(source_kind),"role":text(source,"role"),
            "source":text(source,"source"),"title":text(source,"title"),"content":content,
            "contentBytes":content.len(),"contentSha256":sha256(content.as_bytes()),"sourceSha256":source_sha,
            "sourceHashKind":"canonical-ingestion-envelope","rawSha256":raw_sha,"rawHashKind":raw_hash_kind,
            "metadata":source.get("metadata").cloned().unwrap_or_else(||json!({})),
            "raw":source.get("raw").cloned().unwrap_or_else(||source.clone()),
            "rawText":source.get("rawText").cloned().unwrap_or(Value::Null),
            "training":source.get("training").cloned().unwrap_or(Value::Null),
            "evidenceLabel":evidence,"status":status,"knownFailure":known_failure,
            "sourceIncomplete":incomplete_source(source),"truncated":false
        });
        let payload = serde_json::to_string(&record).map_err(io_error)?;
        let search_text = format!(
            "{}\n{content}\n{}\n{}\n{payload}",
            text(source, "title"),
            text(source, "rawText"),
            source["metadata"]
        );
        transaction.execute("INSERT INTO learning_records(id,source_kind,source_id,conversation_id,source_conversation_id,revision,source_sha256,timestamp,payload,search_text)
            VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",params![id,source_kind,source_id,conversation_id,source_conversation_id,revision,source_sha,timestamp_order,payload,search_text]).map_err(io_error)?;
        transaction.commit().map_err(io_error)?;
        Ok(json!({"record":record,"inserted":true}))
    }

    pub fn annotate(&self, args: &Value) -> Result<Value, String> {
        let id = args["id"]
            .as_str()
            .or_else(|| args["recordId"].as_str())
            .ok_or("Annotation requires id")?;
        let mut annotation =
            json!({"id":uuid::Uuid::new_v4().to_string(),"recordId":id,"timestamp":now()});
        let fields = [
            "evidenceLabel",
            "status",
            "note",
            "training",
            "reviewer",
            "reason",
        ];
        let mut changed = false;
        for key in fields {
            if let Some(value) = args.get(key) {
                if key != "training" && !value.is_string() {
                    return Err(format!("Annotation {key} must be text"));
                }
                annotation[key] = value.clone();
                changed = true;
            }
        }
        if !changed {
            return Err(
                "Annotation requires an evidence label, status, note or training sample".into(),
            );
        }
        let mut connection = self.connection.lock().map_err(io_error)?;
        let transaction = connection.transaction().map_err(io_error)?;
        let original = read_record(&transaction, id)?.ok_or("Learning record was not found")?;
        check_record_scope(&original, args)?;
        transaction
            .execute(
                "INSERT INTO learning_annotations(record_id,timestamp,payload) VALUES(?1,?2,?3)",
                params![id, text(&annotation, "timestamp"), annotation.to_string()],
            )
            .map_err(io_error)?;
        let record = read_record(&transaction, id)?.ok_or("Learning record was not found")?;
        transaction.commit().map_err(io_error)?;
        Ok(json!({"record":record,"annotation":annotation}))
    }

    pub fn query(&self, args: &Value) -> Result<Value, String> {
        let connection = self.connection.lock().map_err(io_error)?;
        let transaction = connection.unchecked_transaction().map_err(io_error)?;
        if let Some(id) = args["id"].as_str().or_else(|| args["recordId"].as_str()) {
            let record = read_record(&transaction, id)?.ok_or("Learning record was not found")?;
            check_record_scope(&record, args)?;
            return Ok(json!({"record":record}));
        }
        let limit = integer_option(args, "limit", 50, 1, 500)?;
        let snapshot: i64 = transaction
            .query_row(
                "SELECT COALESCE(MAX(sequence),0) FROM learning_records",
                [],
                |row| row.get(0),
            )
            .map_err(io_error)?;
        let annotation_snapshot = annotation_sequence(&transaction)?;
        let selection = Selection::new(args, snapshot, annotation_snapshot, true)?;
        let total: i64 = transaction
            .query_row(
                &format!(
                    "SELECT COUNT(*) FROM learning_records r WHERE {}",
                    selection.where_sql
                ),
                params_from_iter(selection.parameters.iter()),
                |row| row.get(0),
            )
            .map_err(io_error)?;
        if args["countOnly"] == true {
            return Ok(
                json!({"records":[],"total":total,"nextCursor":null,"snapshotSequence":selection.snapshot,
                "annotationSnapshotSequence":selection.annotation_snapshot,"countOnly":true,"completeRaw":false}),
            );
        }
        let mut parameters = selection.parameters.clone();
        parameters.push(SqlValue::Text(selection.before_timestamp.clone()));
        parameters.push(SqlValue::Text(selection.before_timestamp.clone()));
        parameters.push(SqlValue::Text(selection.before_timestamp.clone()));
        parameters.push(SqlValue::Integer(selection.before));
        parameters.push(SqlValue::Integer(limit + 1));
        let mut statement=transaction.prepare(&format!("SELECT r.sequence,r.id,r.timestamp FROM learning_records r WHERE {} AND (?='' OR r.timestamp<? OR (r.timestamp=? AND r.sequence<?)) ORDER BY r.timestamp DESC,r.sequence DESC LIMIT ?",selection.where_sql)).map_err(io_error)?;
        let rows = statement
            .query_map(params_from_iter(parameters.iter()), |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            })
            .map_err(io_error)?;
        let mut records = Vec::new();
        let mut last_sequence = 0;
        let mut last_timestamp = String::new();
        let mut has_more = false;
        for row in rows {
            let (sequence, id, timestamp) = row.map_err(io_error)?;
            if records.len() == limit as usize {
                has_more = true;
                break;
            }
            records.push(
                read_record_at(&transaction, &id, selection.annotation_snapshot)?
                    .ok_or("Learning record disappeared during query")?,
            );
            last_sequence = sequence;
            last_timestamp = timestamp;
        }
        let next_cursor = has_more.then(|| {
            format!(
                "3:{}:{}:{last_sequence}:{}:{}",
                selection.snapshot,
                selection.annotation_snapshot,
                base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(last_timestamp.as_bytes()),
                selection.filter_hash
            )
        });
        Ok(
            json!({"records":records,"total":total,"nextCursor":next_cursor,"snapshotSequence":selection.snapshot,"annotationSnapshotSequence":selection.annotation_snapshot,"pageSize":limit,"completeRaw":true}),
        )
    }

    pub fn sync_sources(&self, events: &EventStore, echo_root: &Path) -> Result<Value, String> {
        // Serialize syncs and cache source versions only in memory. Every app restart fully rechecks.
        let mut versions = self.source_versions.lock().map_err(io_error)?;
        let mut inserted = 0_u64;
        let mut unchanged = 0_u64;
        let mut checked = 0_u64;
        let mut errors = Vec::new();
        let version = events.learning_timeline_version()?;
        if versions.get("timeline") != Some(&version) {
            let mut after = 0_i64;
            loop {
                let rows = events.learning_timeline_page(after, 200)?;
                if rows.is_empty() {
                    break;
                }
                for row in rows {
                    after = row["id"]
                        .as_i64()
                        .ok_or("Timeline source has no numeric ID")?;
                    let source = json!({"sourceKind":"timeline","sourceId":format!("timeline:{after}"),
                        "conversationId":row["conversationId"],"sourceConversationId":row["sourceConversationId"],
                        "timestamp":row["timestamp"],"kind":row["kind"],"role":row["role"],"source":row["source"],
                        "title":row["title"],"content":row["content"],"metadata":row["metadata"],"raw":row});
                    count_ingest(
                        self.ingest(&source)?,
                        &mut checked,
                        &mut inserted,
                        &mut unchanged,
                    );
                }
            }
            versions.insert("timeline".into(), version);
        }
        if echo_root.exists() {
            let mut paths = Vec::new();
            collect_sources(echo_root, &mut paths)?;
            paths.sort();
            for path in paths {
                let key = path.to_string_lossy().into_owned();
                let version = file_version(&path)?;
                if versions.get(&key) == Some(&version) {
                    continue;
                }
                let result = if path.extension().is_some_and(|extension| {
                    extension == "db" || extension == "sqlite3" || extension == "sqlite"
                }) {
                    sync_archive(
                        self,
                        &path,
                        echo_root,
                        &mut checked,
                        &mut inserted,
                        &mut unchanged,
                    )
                } else {
                    sync_receipt(
                        self,
                        &path,
                        echo_root,
                        &mut checked,
                        &mut inserted,
                        &mut unchanged,
                    )
                };
                match result {
                    Ok(()) => {
                        versions.insert(key, version);
                    }
                    Err(error) => {
                        let failure = json!({"sourceKind":"source-error","sourceId":format!("source-error:{key}"),"source":"Learning source sync",
                            "title":path.file_name().unwrap_or_default().to_string_lossy(),"content":error,"status":"failed",
                            "metadata":{"path":key,"error":error,"sourceVersion":version}});
                        count_ingest(
                            self.ingest(&failure)?,
                            &mut checked,
                            &mut inserted,
                            &mut unchanged,
                        );
                        errors.push(json!({"path":key,"error":error}));
                    }
                }
            }
        }
        Ok(
            json!({"checked":checked,"inserted":inserted,"unchanged":unchanged,"errors":errors,"syncedAt":now(),"completeRaw":true}),
        )
    }

    pub fn export_dataset(&self, args: &Value) -> Result<Value, String> {
        let format = args["format"].as_str().unwrap_or("sft");
        if !matches!(format, "jsonl" | "csv" | "sft" | "dpo") {
            return Err("Learning export format must be jsonl, csv, sft or dpo".into());
        }
        let parent = args["outputDir"]
            .as_str()
            .map(PathBuf::from)
            .unwrap_or_else(|| self.root.join("exports"));
        std::fs::create_dir_all(&parent).map_err(io_error)?;
        let dataset_id = uuid::Uuid::new_v4().to_string();
        let output = parent.join(format!("learning-{dataset_id}"));
        std::fs::create_dir(&output).map_err(io_error)?;
        let connection = self.connection.lock().map_err(io_error)?;
        let transaction = connection.unchecked_transaction().map_err(io_error)?;
        let snapshot: i64 = transaction
            .query_row(
                "SELECT COALESCE(MAX(sequence),0) FROM learning_records",
                [],
                |row| row.get(0),
            )
            .map_err(io_error)?;
        let selection = Selection::new(args, snapshot, annotation_sequence(&transaction)?, false)?;
        if let Some(ids) = args["recordIds"].as_array() {
            for id in ids {
                let record = read_record(
                    &transaction,
                    id.as_str().ok_or("recordIds must contain text IDs")?,
                )?
                .ok_or("Learning record was not found")?;
                check_record_scope(&record, args)?;
            }
        }
        if matches!(format, "jsonl" | "csv") {
            export_raw(&transaction, &selection, &output, format, &dataset_id)
        } else {
            export_training(&transaction, &selection, &output, format, &dataset_id, args)
        }
    }
}

fn count_ingest(result: Value, checked: &mut u64, inserted: &mut u64, unchanged: &mut u64) {
    *checked += 1;
    if result["inserted"] == true {
        *inserted += 1;
    } else {
        *unchanged += 1;
    }
}

fn read_record(connection: &Connection, id: &str) -> Result<Option<Value>, String> {
    read_record_at(connection, id, i64::MAX)
}

fn check_record_scope(record: &Value, args: &Value) -> Result<(), String> {
    let Some(allowed) = args.get("allowedConversationIds").filter(|v| !v.is_null()) else {
        return Ok(());
    };
    let allowed = allowed
        .as_array()
        .ok_or("Invalid learning conversation scope")?;
    if text(record, "sourceKind") == "music" {
        return Ok(());
    }
    let conversation = text(record, "conversationId");
    let effective = if conversation.is_empty() {
        text(record, "sourceConversationId")
    } else {
        conversation
    };
    if allowed.iter().any(|id| id.as_str() == Some(effective)) {
        Ok(())
    } else {
        Err("Learning record was not found in this conversation or project scope".into())
    }
}

fn annotation_sequence(connection: &Connection) -> Result<i64, String> {
    connection
        .query_row(
            "SELECT COALESCE(MAX(sequence),0) FROM learning_annotations",
            [],
            |row| row.get(0),
        )
        .map_err(io_error)
}

fn source_content_failed(
    connection: &Connection,
    kind: &str,
    source: &str,
    content_hash: &str,
    annotation_snapshot: i64,
) -> Result<bool, String> {
    let mut statement=connection.prepare("SELECT r.payload FROM learning_records r WHERE r.source_kind=?1 AND r.source_id=?2 AND json_extract(r.payload,'$.contentSha256')=?3 UNION ALL SELECT a.payload FROM learning_annotations a JOIN learning_records r ON r.id=a.record_id WHERE r.source_kind=?1 AND r.source_id=?2 AND json_extract(r.payload,'$.contentSha256')=?3 AND a.sequence<=?4").map_err(io_error)?;
    let rows = statement
        .query_map(
            params![kind, source, content_hash, annotation_snapshot],
            |row| row.get::<_, String>(0),
        )
        .map_err(io_error)?;
    for row in rows {
        let evidence: Value = serde_json::from_str(&row.map_err(io_error)?).map_err(io_error)?;
        if evidence["knownFailure"] == true || has_failure(&evidence) {
            return Ok(true);
        }
    }
    Ok(false)
}

fn read_record_at(
    connection: &Connection,
    id: &str,
    annotation_snapshot: i64,
) -> Result<Option<Value>, String> {
    let payload: Option<String> = connection
        .query_row(
            "SELECT payload FROM learning_records WHERE id=?1",
            [id],
            |row| row.get(0),
        )
        .optional()
        .map_err(io_error)?;
    let Some(payload) = payload else {
        return Ok(None);
    };
    let mut record: Value = serde_json::from_str(&payload).map_err(io_error)?;
    if source_content_failed(
        connection,
        text(&record, "sourceKind"),
        text(&record, "sourceId"),
        text(&record, "contentSha256"),
        annotation_snapshot,
    )? {
        record["knownFailure"] = json!(true);
    }
    let mut statement = connection
        .prepare("SELECT payload FROM learning_annotations WHERE record_id=?1 AND sequence<=?2 ORDER BY sequence")
        .map_err(io_error)?;
    let rows = statement
        .query_map(params![id, annotation_snapshot], |row| {
            row.get::<_, String>(0)
        })
        .map_err(io_error)?;
    let mut annotations = Vec::new();
    for row in rows {
        let annotation: Value = serde_json::from_str(&row.map_err(io_error)?).map_err(io_error)?;
        if has_failure(&annotation) {
            record["knownFailure"] = json!(true);
        }
        for key in ["evidenceLabel", "status", "training"] {
            if let Some(value) = annotation.get(key) {
                record[key] = value.clone();
            }
        }
        record["annotation"] = annotation.clone();
        annotations.push(annotation);
    }
    record["annotations"] = json!(annotations);
    Ok(Some(record))
}

fn integer_option(
    args: &Value,
    key: &str,
    default: i64,
    min: i64,
    max: i64,
) -> Result<i64, String> {
    let value = match args.get(key) {
        Some(value) => value
            .as_i64()
            .ok_or_else(|| format!("{key} must be an integer"))?,
        None => default,
    };
    if !(min..=max).contains(&value) {
        return Err(format!("{key} must be between {min} and {max}"));
    }
    Ok(value)
}

struct Selection {
    where_sql: String,
    parameters: Vec<SqlValue>,
    snapshot: i64,
    annotation_snapshot: i64,
    before: i64,
    before_timestamp: String,
    filter_hash: String,
}
impl Selection {
    fn new(
        args: &Value,
        current_snapshot: i64,
        current_annotation_snapshot: i64,
        paged: bool,
    ) -> Result<Self, String> {
        let latest = args["latestOnly"].as_bool().unwrap_or(false);
        let filter = json!({"sourceKind":args["sourceKind"],"sourceId":args["sourceId"],"conversationId":args["conversationId"],
            "sourceConversationId":args["sourceConversationId"],"search":args["search"],"status":args["status"],"evidenceLabel":args["evidenceLabel"],
            "recordIds":args["recordIds"],"latestOnly":latest,"from":args["from"],"to":args["to"],"allowedConversationIds":args["allowedConversationIds"]});
        let filter_hash = sha256(&json_bytes(&filter)?);
        let mut snapshot = current_snapshot;
        let mut annotation_snapshot = current_annotation_snapshot;
        let mut before = i64::MAX;
        let mut before_timestamp = String::new();
        if paged {
            if let Some(cursor) = args["cursor"].as_str().filter(|cursor| !cursor.is_empty()) {
                let parts = cursor.split(':').collect::<Vec<_>>();
                if parts.len() != 6 || parts[0] != "3" || parts[5] != filter_hash {
                    return Err("Invalid learning cursor or changed query filters".into());
                }
                snapshot = parts[1]
                    .parse::<i64>()
                    .map_err(|_| "Invalid learning cursor snapshot")?;
                annotation_snapshot = parts[2]
                    .parse::<i64>()
                    .map_err(|_| "Invalid learning annotation snapshot")?;
                before = parts[3]
                    .parse::<i64>()
                    .map_err(|_| "Invalid learning cursor position")?;
                before_timestamp = String::from_utf8(
                    base64::engine::general_purpose::URL_SAFE_NO_PAD
                        .decode(parts[4])
                        .map_err(|_| "Invalid learning cursor timestamp")?,
                )
                .map_err(|_| "Invalid learning cursor timestamp")?;
                DateTime::parse_from_rfc3339(&before_timestamp)
                    .map_err(|_| "Invalid learning cursor timestamp")?;
                if snapshot < 0
                    || snapshot > current_snapshot
                    || before < 1
                    || annotation_snapshot < 0
                    || annotation_snapshot > current_annotation_snapshot
                {
                    return Err("Invalid learning cursor range".into());
                }
            }
        }
        let mut predicates = vec!["r.sequence<=?".to_string()];
        let mut parameters = vec![SqlValue::Integer(snapshot)];
        if let Some(allowed) = args.get("allowedConversationIds").filter(|v| !v.is_null()) {
            let allowed = allowed
                .as_array()
                .ok_or("Invalid learning conversation scope")?;
            if allowed.is_empty() {
                predicates.push("r.source_kind='music'".into());
            } else {
                let placeholders = std::iter::repeat("?")
                    .take(allowed.len())
                    .collect::<Vec<_>>()
                    .join(",");
                predicates.push(format!("(r.source_kind='music' OR r.conversation_id IN ({placeholders}) OR (r.conversation_id='' AND r.source_conversation_id IN ({placeholders})))"));
                for _ in 0..2 {
                    for id in allowed {
                        parameters.push(SqlValue::Text(
                            id.as_str()
                                .ok_or("Invalid learning conversation scope ID")?
                                .into(),
                        ));
                    }
                }
            }
        }
        for (key, column) in [
            ("sourceKind", "source_kind"),
            ("sourceId", "source_id"),
            ("conversationId", "conversation_id"),
            ("sourceConversationId", "source_conversation_id"),
        ] {
            if let Some(value) = args[key].as_str().filter(|value| !value.is_empty()) {
                predicates.push(format!("r.{column}=?"));
                parameters.push(SqlValue::Text(value.into()));
            }
        }
        if latest {
            predicates.push("NOT EXISTS(SELECT 1 FROM learning_records newer WHERE newer.source_kind=r.source_kind AND newer.source_id=r.source_id AND newer.sequence>r.sequence AND newer.sequence<=?)".into());
            parameters.push(SqlValue::Integer(snapshot));
        }
        if let Some(search) = args["search"].as_str().filter(|search| !search.is_empty()) {
            predicates.push("(instr(lower(r.search_text),lower(?))>0 OR EXISTS(SELECT 1 FROM learning_annotations a WHERE a.record_id=r.id AND a.sequence<=? AND instr(lower(a.payload),lower(?))>0))".into());
            parameters.push(SqlValue::Text(search.into()));
            parameters.push(SqlValue::Integer(annotation_snapshot));
            parameters.push(SqlValue::Text(search.into()));
        }
        for key in ["status", "evidenceLabel"] {
            if let Some(value) = args[key].as_str().filter(|value| !value.is_empty()) {
                predicates.push(format!("COALESCE((SELECT json_extract(a.payload,'$.{key}') FROM learning_annotations a WHERE a.record_id=r.id AND a.sequence<=? AND json_type(a.payload,'$.{key}') IS NOT NULL ORDER BY a.sequence DESC LIMIT 1),json_extract(r.payload,'$.{key}'))=?"));
                parameters.push(SqlValue::Integer(annotation_snapshot));
                parameters.push(SqlValue::Text(value.into()));
            }
        }
        for (key, operator) in [("from", ">="), ("to", "<=")] {
            if let Some(value) = args[key].as_str() {
                let normalized = DateTime::parse_from_rfc3339(value)
                    .map_err(|error| format!("Invalid {key} timestamp: {error}"))?
                    .with_timezone(&Utc)
                    .to_rfc3339_opts(SecondsFormat::Nanos, true);
                predicates.push(format!("r.timestamp{operator}?"));
                parameters.push(SqlValue::Text(normalized));
            }
        }
        if let Some(ids) = args.get("recordIds").filter(|value| !value.is_null()) {
            let ids = ids.as_array().ok_or("recordIds must be an array")?;
            if ids.is_empty() {
                predicates.push("0".into());
            } else {
                predicates.push(format!(
                    "r.id IN ({})",
                    std::iter::repeat("?")
                        .take(ids.len())
                        .collect::<Vec<_>>()
                        .join(",")
                ));
                for id in ids {
                    parameters.push(SqlValue::Text(
                        id.as_str().ok_or("recordIds must contain text IDs")?.into(),
                    ));
                }
            }
        }
        Ok(Self {
            where_sql: predicates.join(" AND "),
            parameters,
            snapshot,
            annotation_snapshot,
            before,
            before_timestamp,
            filter_hash,
        })
    }
}

fn collect_sources(root: &Path, paths: &mut Vec<PathBuf>) -> Result<(), String> {
    for entry in std::fs::read_dir(root).map_err(io_error)? {
        let entry = entry.map_err(io_error)?;
        let file_type = entry.file_type().map_err(io_error)?;
        if file_type.is_symlink() {
            continue;
        }
        let path = entry.path();
        if file_type.is_dir() {
            collect_sources(&path, paths)?;
        } else if file_type.is_file() {
            let extension = path
                .extension()
                .unwrap_or_default()
                .to_string_lossy()
                .to_ascii_lowercase();
            let name = path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .to_ascii_lowercase();
            let in_receipts = path.components().any(|component| {
                component
                    .as_os_str()
                    .to_string_lossy()
                    .eq_ignore_ascii_case("receipts")
            });
            if matches!(extension.as_str(), "db" | "sqlite3" | "sqlite")
                || matches!(extension.as_str(), "json" | "jsonl")
                    && (name.contains("receipt") || in_receipts)
            {
                paths.push(path);
            }
        }
    }
    Ok(())
}

fn file_version(path: &Path) -> Result<String, String> {
    let mut signature = String::new();
    for source in [
        path.to_path_buf(),
        PathBuf::from(format!("{}-wal", path.to_string_lossy())),
    ] {
        match std::fs::metadata(&source) {
            Ok(metadata) => {
                let nanos = metadata
                    .modified()
                    .map_err(io_error)?
                    .duration_since(std::time::UNIX_EPOCH)
                    .map_err(io_error)?
                    .as_nanos();
                signature.push_str(&format!("{}:{nanos};", metadata.len()));
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                signature.push_str("missing;")
            }
            Err(error) => return Err(error.to_string()),
        }
    }
    Ok(signature)
}

fn relative_source(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

fn table_exists(connection: &Connection, table: &str) -> Result<bool, String> {
    connection
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1)",
            [table],
            |row| row.get(0),
        )
        .map_err(io_error)
}

/// Generic database values preserve all columns, including future source schema additions.
fn table_rows(
    connection: &Connection,
    table: &str,
    mut consume: impl FnMut(Value) -> Result<(), String>,
) -> Result<(), String> {
    let with_rowid = format!("SELECT rowid AS ledger_rowid,* FROM {table} ORDER BY rowid");
    let mut statement = match connection.prepare(&with_rowid) {
        Ok(statement) => statement,
        Err(_) => connection
            .prepare(&format!("SELECT * FROM {table}"))
            .map_err(io_error)?,
    };
    let mut rows = statement.query([]).map_err(io_error)?;
    while let Some(row) = rows.next().map_err(io_error)? {
        let mut value = serde_json::Map::new();
        for index in 0..row.as_ref().column_count() {
            let key = row
                .as_ref()
                .column_name(index)
                .map_err(io_error)?
                .to_string();
            let cell = match row.get_ref(index).map_err(io_error)? {
                rusqlite::types::ValueRef::Null => Value::Null,
                rusqlite::types::ValueRef::Integer(integer) => json!(integer),
                rusqlite::types::ValueRef::Real(real) => {
                    if real.is_finite() {
                        json!(real)
                    } else {
                        json!({"sqliteReal":real.to_string()})
                    }
                }
                rusqlite::types::ValueRef::Text(bytes) => match std::str::from_utf8(bytes) {
                    Ok(value) => json!(value),
                    Err(_) => {
                        json!({"textBytesBase64":base64::engine::general_purpose::STANDARD.encode(bytes)})
                    }
                },
                rusqlite::types::ValueRef::Blob(bytes) => {
                    json!({"bytesBase64":base64::engine::general_purpose::STANDARD.encode(bytes)})
                }
            };
            value.insert(key, cell);
        }
        consume(Value::Object(value))?;
    }
    Ok(())
}

fn sync_archive(
    store: &LearningStore,
    path: &Path,
    root: &Path,
    checked: &mut u64,
    inserted: &mut u64,
    unchanged: &mut u64,
) -> Result<(), String> {
    let connection = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|error| format!("{}: {error}", path.display()))?;
    connection
        .busy_timeout(std::time::Duration::from_secs(5))
        .map_err(io_error)?;
    let transaction = connection.unchecked_transaction().map_err(io_error)?;
    let archive_file = relative_source(root, path);
    if table_exists(&transaction, "pages")? {
        table_rows(&transaction, "pages", |mut raw| {
            let compressed = base64::engine::general_purpose::STANDARD
                .decode(text(&raw["compressed_bytes"], "bytesBase64"))
                .map_err(io_error)?;
            let mut decoded = Vec::new();
            let result = ZlibDecoder::new(compressed.as_slice()).read_to_end(&mut decoded);
            let mut integrity_error = result
                .err()
                .map(|error| format!("ECHO page decompression failed: {error}"));
            if integrity_error.is_none() && sha256(&decoded) != text(&raw, "content_hash") {
                integrity_error = Some(format!(
                    "ECHO page source hash mismatch: expected {}, observed {}",
                    text(&raw, "content_hash"),
                    sha256(&decoded)
                ));
            }
            let content = match String::from_utf8(decoded.clone()) {
                Ok(content) => content,
                Err(error) => {
                    if integrity_error.is_none() {
                        integrity_error = Some(format!("ECHO page is not UTF-8: {error}"));
                    }
                    String::new()
                }
            };
            raw["compressedBytesBase64"] =
                json!(base64::engine::general_purpose::STANDARD.encode(&compressed));
            if integrity_error.is_some() {
                raw["decodedBytesBase64"] =
                    json!(base64::engine::general_purpose::STANDARD.encode(&decoded));
            }
            let source = json!({"sourceKind":"echo-page","sourceId":format!("{archive_file}#page:{}",text(&raw,"page_id")),
                "conversationId":raw["conversation_id"],"timestamp":raw["timestamp"],"kind":"archive-page","source":"ECHO",
                "title":format!("ECHO page {}",text(&raw,"page_id")),"content":content,
                "status":if integrity_error.is_some(){"failed"}else{"recorded"},
                "metadata":{"archiveFile":archive_file,"archivePath":path,"integrityError":integrity_error,
                    "offsetStart":raw["offset_start"],"offsetEnd":raw["offset_end"],"expectedContentSha256":raw["content_hash"]},"raw":raw});
            count_ingest(store.ingest(&source)?, checked, inserted, unchanged);
            Ok(())
        })?;
    }
    if table_exists(&transaction, "source_events")? {
        table_rows(&transaction, "source_events", |mut raw| {
            let metadata_text = text(&raw, "metadata").to_string();
            let mut metadata: Value = serde_json::from_str(&metadata_text).unwrap_or_else(
                |error| json!({"integrityError":format!("Source event metadata JSON parse failed: {error}")}),
            );
            if !metadata.is_object() {
                metadata = json!({"sourceMetadata":metadata});
            }
            metadata["archiveFile"] = json!(archive_file);
            metadata["archivePath"] = json!(path);
            raw["metadataText"] = json!(metadata_text);
            if table_exists(&transaction, "source_event_assets")? {
                let mut assets = transaction
                    .prepare("SELECT * FROM source_event_assets WHERE event_id=?1")
                    .map_err(io_error)?;
                let mut assets_rows = assets.query([text(&raw, "event_id")]).map_err(io_error)?;
                let mut links = Vec::new();
                while let Some(asset) = assets_rows.next().map_err(io_error)? {
                    let mut link = serde_json::Map::new();
                    for index in 0..asset.as_ref().column_count() {
                        let column = asset.as_ref().column_name(index).map_err(io_error)?;
                        let cell: Option<String> = asset.get(index).map_err(io_error)?;
                        link.insert(column.into(), json!(cell));
                    }
                    links.push(Value::Object(link));
                }
                raw["assetLinks"] = json!(links);
            }
            let source = json!({"sourceKind":"echo-event","sourceId":format!("{archive_file}#event:{}",text(&raw,"event_id")),
                "conversationId":raw["conversation_id"],"timestamp":raw["timestamp"],"kind":raw["kind"],
                "role":raw["role"],"source":raw["source"],"title":raw["title"],"content":raw["content"],"metadata":metadata,"raw":raw});
            count_ingest(store.ingest(&source)?, checked, inserted, unchanged);
            Ok(())
        })?;
    }
    if table_exists(&transaction, "derived_summaries")? {
        table_rows(&transaction, "derived_summaries", |raw| {
            let row_id = raw
                .get("ledger_rowid")
                .map(Value::to_string)
                .unwrap_or_else(|| {
                    format!("{}:{}", text(&raw, "conversation_id"), raw["generated_at"])
                });
            let source = json!({"sourceKind":"echo-summary","sourceId":format!("{archive_file}#summary:{row_id}"),
                "conversationId":raw["conversation_id"],"timestamp":raw["generated_at"],"kind":"derived-summary","source":"ECHO",
                "title":"ECHO derived summary","content":raw["content"],"evidenceLabel":"derived-summary",
                "metadata":{"archiveFile":archive_file,"archivePath":path,"sourcePages":raw["source_pages"],"modelCalls":raw["model_calls"],
                    "truncated":raw["truncated"].as_i64().is_some_and(|value|value!=0),"incomplete":raw["incomplete"].as_i64().is_some_and(|value|value!=0)},"raw":raw});
            count_ingest(store.ingest(&source)?, checked, inserted, unchanged);
            Ok(())
        })?;
    }
    Ok(())
}

fn sync_receipt(
    store: &LearningStore,
    path: &Path,
    root: &Path,
    checked: &mut u64,
    inserted: &mut u64,
    unchanged: &mut u64,
) -> Result<(), String> {
    let bytes = std::fs::read(path).map_err(io_error)?;
    let source_file = relative_source(root, path);
    let is_jsonl = path
        .extension()
        .is_some_and(|extension| extension == "jsonl");
    let chunks: Vec<&[u8]> = if is_jsonl {
        bytes.split_inclusive(|byte| *byte == b'\n').collect()
    } else {
        vec![bytes.as_slice()]
    };
    for (index, chunk) in chunks.into_iter().enumerate() {
        if chunk.iter().all(u8::is_ascii_whitespace) {
            continue;
        }
        let raw_text = std::str::from_utf8(chunk);
        let parsed = serde_json::from_slice::<Value>(chunk);
        let (raw, error) = match parsed {
            Ok(raw) => (raw, None),
            Err(error) => (
                json!({"bytesBase64":base64::engine::general_purpose::STANDARD.encode(chunk)}),
                Some(format!("Receipt JSON parse failed: {error}")),
            ),
        };
        let mut metadata = json!({"sourceFile":source_file,"sourcePath":path,"line":if is_jsonl{Some(index+1)}else{None},"integrityError":error});
        metadata["receipt"] = raw.clone();
        let content = raw
            .get("content")
            .and_then(Value::as_str)
            .map(str::to_string)
            .or_else(|| raw.get("error").and_then(Value::as_str).map(str::to_string))
            .unwrap_or_else(|| raw_text.unwrap_or("").to_string());
        let source = json!({"sourceKind":"receipt","sourceId":format!("{source_file}#{}",if is_jsonl{index+1}else{0}),
            "conversationId":raw["conversationId"].as_str().or_else(||raw["conversation_id"].as_str()).or_else(||raw["request"]["conversationId"].as_str()).unwrap_or(""),
            "timestamp":raw.get("timestamp").or_else(||raw.get("completedAt")).or_else(||raw.get("updatedAt")).or_else(||raw.get("createdAt")).cloned().unwrap_or(Value::Null),
            "kind":"receipt","source":"Studio receipt","title":raw["title"].as_str().unwrap_or(&source_file),
            "status":raw["status"].as_str().unwrap_or(if error.is_some(){"failed"}else{"recorded"}),
            "content":content,"metadata":metadata,"raw":raw,"rawText":raw_text.ok(),
            "rawBytesBase64":base64::engine::general_purpose::STANDARD.encode(chunk)});
        count_ingest(store.ingest(&source)?, checked, inserted, unchanged);
    }
    Ok(())
}

struct HashWriter {
    writer: BufWriter<File>,
    hash: Sha256,
    bytes: u64,
}
impl HashWriter {
    fn new(path: &Path) -> Result<Self, String> {
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .map_err(io_error)?;
        Ok(Self {
            writer: BufWriter::new(file),
            hash: Sha256::new(),
            bytes: 0,
        })
    }
    fn finish(mut self) -> Result<(String, u64), String> {
        self.writer.flush().map_err(io_error)?;
        self.writer.get_ref().sync_all().map_err(io_error)?;
        Ok((format!("{:x}", self.hash.finalize()), self.bytes))
    }
}
impl Write for HashWriter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        let written = self.writer.write(bytes)?;
        self.hash.update(&bytes[..written]);
        self.bytes += written as u64;
        Ok(written)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.writer.flush()
    }
}
fn write_json_line(writer: &mut impl Write, value: &Value) -> Result<(), String> {
    serde_json::to_writer(&mut *writer, value).map_err(io_error)?;
    writer.write_all(b"\n").map_err(io_error)
}
fn csv_cell(value: &str) -> String {
    format!("\"{}\"", value.replace('"', "\"\""))
}

fn export_raw(
    connection: &Connection,
    selection: &Selection,
    output: &Path,
    format: &str,
    dataset_id: &str,
) -> Result<Value, String> {
    let path = output.join(format!("records.{format}"));
    let mut writer = HashWriter::new(&path)?;
    let columns = [
        "id",
        "sourceKind",
        "sourceId",
        "conversationId",
        "sourceConversationId",
        "revision",
        "previousRecordId",
        "timestamp",
        "sourceTimestamp",
        "timestampOrigin",
        "ingestedAt",
        "kind",
        "role",
        "source",
        "title",
        "content",
        "contentBytes",
        "contentSha256",
        "sourceSha256",
        "sourceHashKind",
        "rawSha256",
        "rawHashKind",
        "status",
        "evidenceLabel",
        "knownFailure",
        "sourceIncomplete",
        "truncated",
        "metadata",
        "raw",
        "rawText",
        "training",
        "annotation",
        "annotations",
        "recordJson",
    ];
    if format == "csv" {
        writer
            .write_all(columns.join(",").as_bytes())
            .map_err(io_error)?;
        writer.write_all(b"\r\n").map_err(io_error)?;
    }
    let mut statement = connection
        .prepare(&format!(
            "SELECT r.id FROM learning_records r WHERE {} ORDER BY r.sequence",
            selection.where_sql
        ))
        .map_err(io_error)?;
    let rows = statement
        .query_map(params_from_iter(selection.parameters.iter()), |row| {
            row.get::<_, String>(0)
        })
        .map_err(io_error)?;
    let mut count = 0_u64;
    for row in rows {
        let record = read_record(connection, &row.map_err(io_error)?)?
            .ok_or("Learning record disappeared during export")?;
        if format == "jsonl" {
            write_json_line(&mut writer, &record)?;
        } else {
            for (index, column) in columns.iter().enumerate() {
                if index > 0 {
                    writer.write_all(b",").map_err(io_error)?;
                }
                let value = if *column == "recordJson" {
                    record.to_string()
                } else {
                    record[*column]
                        .as_str()
                        .map(str::to_string)
                        .unwrap_or_else(|| record[*column].to_string())
                };
                writer
                    .write_all(csv_cell(&value).as_bytes())
                    .map_err(io_error)?;
            }
            writer.write_all(b"\r\n").map_err(io_error)?;
        }
        count += 1;
    }
    let (hash, bytes) = writer.finish()?;
    let manifest_path = output.join("manifest.json");
    let manifest = json!({"schemaVersion":1,"datasetId":dataset_id,"createdAt":now(),"format":format,"snapshotSequence":selection.snapshot,
        "completeRaw":true,"counts":{"records":count},"files":{"records":{"path":path,"sha256":hash,"bytes":bytes,"records":count}},"exclusions":[]});
    save_manifest(&manifest_path, &manifest)?;
    Ok(
        json!({"datasetId":dataset_id,"format":format,"path":path,"manifestPath":manifest_path,"sha256":hash,"bytes":bytes,
        "counts":{"records":count},"exclusions":[],"completeRaw":true,"snapshotSequence":selection.snapshot}),
    )
}

fn save_manifest(path: &Path, manifest: &Value) -> Result<(), String> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(io_error)?;
    serde_json::to_writer_pretty(&mut file, manifest).map_err(io_error)?;
    file.write_all(b"\n").map_err(io_error)?;
    file.sync_all().map_err(io_error)
}

fn valid_messages(value: &Value) -> Option<Value> {
    let messages = value.as_array()?;
    if messages.len() < 2 {
        return None;
    }
    let mut normalized = Vec::new();
    let mut has_user = false;
    let mut has_assistant = false;
    for message in messages {
        let role = message["role"].as_str()?;
        let content = message["content"].as_str()?;
        if !matches!(role, "system" | "user" | "assistant" | "tool") || content.is_empty() {
            return None;
        }
        if role == "user" {
            has_user = true;
        }
        if role == "assistant" {
            has_assistant = true;
        }
        let mut normalized_message = json!({"role":role,"content":content});
        // Tool references are preserved when an explicitly reviewed sample supplies them.
        for key in ["name", "tool_call_id", "tool_calls"] {
            if let Some(value) = message.get(key) {
                normalized_message[key] = value.clone();
            }
        }
        normalized.push(normalized_message);
    }
    if !has_user || !has_assistant || messages.last()?["role"] != "assistant" {
        return None;
    }
    Some(json!(normalized))
}

fn explicit_sample(record: &Value, format: &str) -> Option<Value> {
    let training = &record["training"];
    if format == "dpo" {
        let prompt = training["prompt"].as_str()?;
        let chosen = training["chosen"].as_str()?;
        let rejected = training["rejected"].as_str()?;
        if prompt.is_empty() || chosen.is_empty() || rejected.is_empty() || chosen == rejected {
            return None;
        }
        return Some(json!({"prompt":prompt,"chosen":chosen,"rejected":rejected}));
    }
    if let Some(messages) = valid_messages(&training["messages"]) {
        return Some(json!({"messages":messages}));
    }
    let prompt = training["prompt"].as_str()?;
    let completion = training["completion"].as_str()?;
    if prompt.is_empty() || completion.is_empty() {
        return None;
    }
    Some(
        json!({"messages":[{"role":"user","content":prompt},{"role":"assistant","content":completion}]}),
    )
}

fn sample_characters(sample: &Value) -> usize {
    if let Some(messages) = sample["messages"].as_array() {
        messages
            .iter()
            .map(|message| text(message, "content").chars().count())
            .sum()
    } else {
        ["prompt", "chosen", "rejected"]
            .iter()
            .map(|key| text(sample, key).chars().count())
            .sum()
    }
}

fn source_manifest(record: &Value) -> Value {
    let mut value = json!({});
    for key in [
        "id",
        "sourceKind",
        "sourceId",
        "conversationId",
        "sourceConversationId",
        "revision",
        "sourceSha256",
        "sourceHashKind",
        "rawSha256",
        "rawHashKind",
        "contentSha256",
        "timestamp",
        "sourceTimestamp",
        "evidenceLabel",
        "status",
        "knownFailure",
    ] {
        value[key] = record[key].clone();
    }
    value["annotationIds"] = json!(record["annotations"]
        .as_array()
        .map(|annotations| annotations
            .iter()
            .map(|annotation| annotation["id"].clone())
            .collect::<Vec<_>>())
        .unwrap_or_default());
    value
}

fn exclusion(record: &Value, reason: &str, details: Value) -> Value {
    json!({"recordId":record["id"],"sourceKind":record["sourceKind"],"sourceId":record["sourceId"],"sourceConversationId":record["sourceConversationId"],"reason":reason,"details":details})
}

fn export_training(
    connection: &Connection,
    selection: &Selection,
    output: &Path,
    format: &str,
    dataset_id: &str,
    args: &Value,
) -> Result<Value, String> {
    let verified_only = match args.get("verifiedOnly") {
        Some(value) => value
            .as_bool()
            .ok_or("verifiedOnly must be true or false")?,
        None => true,
    };
    let validation_fraction = match args.get("validationFraction") {
        Some(value) => value
            .as_f64()
            .ok_or("validationFraction must be a finite number")?,
        None => 0.2,
    };
    if !validation_fraction.is_finite() || validation_fraction <= 0.0 || validation_fraction >= 1.0
    {
        return Err("validationFraction must be greater than 0 and less than 1".into());
    }
    let seed = integer_option(args, "seed", 42, 0, i64::MAX)?;
    let max_characters = match args.get("maxCharacters") {
        Some(_) => Some(integer_option(args, "maxCharacters", 0, 1, i64::MAX)? as usize),
        None => None,
    };
    let max_seq_length = match args.get("maxSeqLength") {
        Some(_) => Some(integer_option(args, "maxSeqLength", 4096, 128, 1_048_576)?),
        None => None,
    };
    let mut statement=connection.prepare(&format!("SELECT r.id,NOT EXISTS(SELECT 1 FROM learning_records newer WHERE newer.source_kind=r.source_kind AND newer.source_id=r.source_id AND newer.sequence>r.sequence AND newer.sequence<=?)
        FROM learning_records r WHERE {} ORDER BY r.timestamp,r.sequence",selection.where_sql)).map_err(io_error)?;
    let mut parameters = vec![SqlValue::Integer(selection.snapshot)];
    parameters.extend(selection.parameters.clone());
    let rows = statement
        .query_map(params_from_iter(parameters.iter()), |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, bool>(1)?))
        })
        .map_err(io_error)?;
    let mut source_records = Vec::new();
    let mut exclusions = Vec::new();
    let mut samples = Vec::new();
    let mut last_user: HashMap<String, Value> = HashMap::new();
    let mut seen_payloads: HashMap<String, String> = HashMap::new();
    for row in rows {
        let (id, is_latest) = row.map_err(io_error)?;
        let record = read_record(connection, &id)?
            .ok_or("Learning record disappeared during dataset export")?;
        source_records.push(source_manifest(&record));
        if !is_latest {
            exclusions.push(exclusion(&record, "superseded-source-revision", json!({})));
            continue;
        }
        let conversation = text(&record, "conversationId").to_string();
        let is_timeline_message =
            text(&record, "sourceKind") == "timeline" && text(&record, "kind") == "message";
        if is_timeline_message && text(&record, "role") == "user" {
            if record["knownFailure"] != true && record["sourceIncomplete"] != true {
                last_user.insert(conversation.clone(), record.clone());
            } else {
                last_user.remove(&conversation);
            }
        }
        if record["knownFailure"] == true
            || failure_status(text(&record, "status"))
            || failure_status(text(&record, "evidenceLabel"))
        {
            exclusions.push(exclusion(
                &record,
                "known-failure",
                json!({"status":record["status"],"evidenceLabel":record["evidenceLabel"]}),
            ));
            continue;
        }
        if record["sourceIncomplete"] == true {
            exclusions.push(exclusion(
                &record,
                "incomplete-source",
                json!({"message":"The source reports truncation or incomplete content"}),
            ));
            continue;
        }
        let mut provenance = vec![record["id"].clone()];
        let mut sample = explicit_sample(&record, format);
        if sample.is_none()
            && format == "sft"
            && is_timeline_message
            && text(&record, "role") == "assistant"
            && !text(&record, "content").is_empty()
        {
            if let Some(user) = last_user.get(&conversation) {
                if text(user, "sourceConversationId") == text(&record, "sourceConversationId") {
                    sample = Some(
                        json!({"messages":[{"role":"user","content":user["content"]},{"role":"assistant","content":record["content"]}]}),
                    );
                    provenance.insert(0, user["id"].clone());
                }
            }
        }
        let Some(mut sample) = sample else {
            exclusions.push(exclusion(&record,"no-trainable-sample",json!({"format":format,"message":"Provide a reviewed messages or prompt/completion sample for SFT, or prompt/chosen/rejected for DPO"})));
            continue;
        };
        let verified = text(&record, "evidenceLabel") == "verified-success";
        if verified_only && !verified {
            exclusions.push(exclusion(
                &record,
                "unverified-evidence",
                json!({"evidenceLabel":record["evidenceLabel"]}),
            ));
            continue;
        }
        let characters = sample_characters(&sample);
        if max_characters.is_some_and(|limit| characters > limit) {
            exclusions.push(exclusion(
                &record,
                "character-limit",
                json!({"characters":characters,"maximum":max_characters,"truncated":false}),
            ));
            continue;
        }
        let hash = sha256(&json_bytes(&sample)?);
        if let Some(first) = seen_payloads.get(&hash) {
            exclusions.push(exclusion(
                &record,
                "duplicate-sample",
                json!({"firstRecordId":first,"sampleSha256":hash}),
            ));
            continue;
        }
        seen_payloads.insert(hash.clone(), id);
        sample["sourceConversationId"] = record["sourceConversationId"].clone();
        sample["sourceRecordIds"] = json!(provenance);
        sample["evidenceLabel"] = json!(if verified {
            "verified-success"
        } else {
            "unverified-self-distillation"
        });
        sample["sampleSha256"] = json!(hash);
        sample["sourceTimestamp"] = record["timestamp"].clone();
        samples.push(sample);
    }
    let groups = samples
        .iter()
        .map(|sample| text(sample, "sourceConversationId").to_string())
        .collect::<BTreeSet<_>>();
    let mut ranked = groups
        .iter()
        .map(|group| (sha256(format!("{seed}\0{group}").as_bytes()), group.clone()))
        .collect::<Vec<_>>();
    ranked.sort();
    let validation_count = if groups.len() < 2 {
        0
    } else {
        ((groups.len() as f64 * validation_fraction).round() as usize).clamp(1, groups.len() - 1)
    };
    let validation_groups = ranked
        .iter()
        .take(validation_count)
        .map(|(_, group)| group.clone())
        .collect::<BTreeSet<_>>();
    let train_groups = groups
        .difference(&validation_groups)
        .cloned()
        .collect::<BTreeSet<_>>();
    let train_path = output.join("train.jsonl");
    let validation_path = output.join("validation.jsonl");
    let mut train_writer = HashWriter::new(&train_path)?;
    let mut validation_writer = HashWriter::new(&validation_path)?;
    let mut train_count = 0_u64;
    let mut heldout_count = 0_u64;
    for sample in &samples {
        if validation_groups.contains(text(sample, "sourceConversationId")) {
            write_json_line(&mut validation_writer, sample)?;
            heldout_count += 1;
        } else {
            write_json_line(&mut train_writer, sample)?;
            train_count += 1;
        }
    }
    let (train_hash, train_bytes) = train_writer.finish()?;
    let (validation_hash, validation_bytes) = validation_writer.finish()?;
    let mut reason_counts = BTreeMap::<String, u64>::new();
    for excluded in &exclusions {
        *reason_counts
            .entry(text(excluded, "reason").to_string())
            .or_default() += 1;
    }
    let counts = json!({"records":source_records.len(),"samples":samples.len(),"train":train_count,"validation":heldout_count,
        "trainConversations":train_groups.len(),"validationConversations":validation_groups.len(),"excluded":exclusions.len(),"exclusionReasons":reason_counts});
    let train = json!({"path":train_path,"sha256":train_hash,"bytes":train_bytes,"samples":train_count,"sourceIds":train_groups});
    let validation = json!({"path":validation_path,"sha256":validation_hash,"bytes":validation_bytes,"samples":heldout_count,"sourceIds":validation_groups});
    let manifest_path = output.join("manifest.json");
    let manifest = json!({"schemaVersion":1,"datasetId":dataset_id,"createdAt":now(),"format":format,"snapshotSequence":selection.snapshot,
        "options":{"verifiedOnly":verified_only,"validationFraction":validation_fraction,"seed":seed,"maxCharacters":max_characters,"maxSeqLength":max_seq_length},
        "train":train,"validation":validation,"files":{"train":train,"validation":validation},"sourceRecords":source_records,
        "counts":counts,"exclusions":exclusions,"sourceConversationSplit":true,"immutableSourceHashes":true,
        "tokenization":{"status":"requires-model-tokenizer","maxSeqLength":max_seq_length,"truncationAllowed":false,
            "message":"No tokenizer estimate is substituted for actual tokens. The training worker must enforce its context limit and report over-length samples without truncation."},
        "warnings":if heldout_count==0{json!(["Insufficient source conversations for a held-out evaluation split; candidate acceptance must remain blocked"])}else{json!([])}});
    save_manifest(&manifest_path, &manifest)?;
    Ok(
        json!({"datasetId":dataset_id,"format":format,"manifestPath":manifest_path,"trainPath":train_path,"validationPath":validation_path,
        "trainSha256":train_hash,"validationSha256":validation_hash,"counts":counts,"exclusions":exclusions,
        "snapshotSequence":selection.snapshot,"warnings":manifest["warnings"],"tokenization":manifest["tokenization"]}),
    )
}
