//! Portable, copied chat history. Source files are opened for reading only; source
//! commands, provider settings and approvals are never executed or installed.
use crate::models::TimelineEntry;
use crate::redaction::{redact_json, redact_text};
use crate::store::EventStore;
use base64::Engine;
use chrono::{DateTime, NaiveDateTime, SecondsFormat, Utc};
use rusqlite::{types::ValueRef, Connection, OpenFlags, OptionalExtension, Row};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File, Metadata};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

#[path = "chat_import_projects.rs"]
mod projects;
#[path = "chat_import_opencode.rs"]
mod opencode;
use projects::SourceProject;

const MAX_TEXT_BYTES: u64 = 64 * 1024 * 1024;
const MAX_DATABASE_BYTES: u64 = 512 * 1024 * 1024;
const MAX_ITEM_BYTES: usize = 8 * 1024 * 1024;
const MAX_CONVERSATIONS: usize = 1_000;
const MAX_ENTRIES: usize = 50_000;
const MAX_COLUMNS: usize = 256;
pub const IMPORT_CANCELLED: &str = "__CHAT_IMPORT_CANCELLED__";
const CLOSE_HERMES_DATABASE: &str =
    "Close Hermes/OpenCode before importing its database, or use a JSON/JSONL export.";
type ImportedRow = (String, String, String, String, String, Value);

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportReport {
    pub source_format: String,
    pub source_path: String,
    pub imported: usize,
    pub updated: usize,
    pub skipped: usize,
    pub failed: usize,
    pub current: usize,
    pub total: usize,
    pub cancelled: bool,
    pub warnings: Vec<String>,
    pub conversations: Vec<ImportConversationResult>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportConversationResult {
    pub conversation_id: String,
    pub source_conversation_id: String,
    pub title: String,
    pub status: String,
    pub entries: usize,
    pub warnings: Vec<String>,
    pub error: Option<String>,
    pub source_folder: Option<String>,
    pub folder_status: String,
    pub project_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportPreview {
    pub source_format: String,
    pub source_path: String,
    pub conversations: usize,
    pub entries: usize,
    pub warnings: Vec<String>,
    pub samples: Vec<ImportPreviewConversation>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportPreviewConversation {
    pub source_conversation_id: String,
    pub title: String,
    pub entries: usize,
    pub warnings: Vec<String>,
    pub error: Option<String>,
    pub source_folder: Option<String>,
    pub folder_status: String,
}

#[derive(Debug)]
struct Candidate {
    conversation_id: String,
    source_id: String,
    title: String,
    rows: Vec<ImportedRow>,
    warnings: Vec<String>,
    error: Option<String>,
    project: SourceProject,
}

struct PreparedFile {
    source_format: String,
    conversations: Vec<Candidate>,
}

pub fn preview_file(path: &Path, format: &str) -> Result<ImportPreview, String> {
    let prepared = prepare_file(path, format, &|| false)?;
    let invalid = prepared
        .conversations
        .iter()
        .filter(|item| item.error.is_some())
        .count();
    Ok(ImportPreview {
        source_format: prepared.source_format,
        source_path: redact_text(&path.display().to_string()),
        conversations: prepared.conversations.len(),
        entries: prepared
            .conversations
            .iter()
            .map(|item| item.rows.len())
            .sum(),
        warnings: if invalid == 0 {
            vec![]
        } else {
            vec![format!(
                "{invalid} conversations could not be read. See the details below."
            )]
        },
        samples: prepared
            .conversations
            .into_iter()
            .take(10)
            .map(|item| ImportPreviewConversation {
                source_folder: item.project.display_folder(),
                folder_status: item.project.status().into(),
                source_conversation_id: item.source_id,
                title: item.title,
                entries: item.rows.len(),
                warnings: item.warnings,
                error: item.error,
            })
            .collect(),
    })
}

pub fn import_file(store: &EventStore, path: &Path, format: &str) -> Result<ImportReport, String> {
    import_file_with_cancellation(store, path, format, &|| false, &mut |_| {})
}

pub fn import_file_with_cancellation(
    store: &EventStore,
    path: &Path,
    format: &str,
    cancelled: &dyn Fn() -> bool,
    progress: &mut dyn FnMut(&ImportReport),
) -> Result<ImportReport, String> {
    let mut report = ImportReport {
        source_format: format.to_string(),
        source_path: redact_text(&path.display().to_string()),
        ..Default::default()
    };
    let prepared = match prepare_file(path, format, cancelled) {
        Ok(prepared) => prepared,
        Err(error) if error == IMPORT_CANCELLED => {
            report.cancelled = true;
            progress(&report);
            return Ok(report);
        }
        Err(error) => return Err(error),
    };
    report.source_format = prepared.source_format;
    report.total = prepared.conversations.len();
    progress(&report);
    for candidate in prepared.conversations {
        if cancelled() {
            report.cancelled = true;
            break;
        }
        let mut item = ImportConversationResult {
            source_folder: candidate.project.display_folder(),
            folder_status: candidate.project.status().into(),
            project_id: None,
            conversation_id: candidate.conversation_id.clone(),
            source_conversation_id: candidate.source_id,
            title: candidate.title.clone(),
            status: "failed".into(),
            entries: candidate.rows.len(),
            warnings: candidate.warnings,
            error: candidate.error,
        };
        if item.error.is_none() {
            match persist_candidate(
                store,
                &report.source_format,
                &candidate.conversation_id,
                &candidate.title,
                &candidate.rows,
            ) {
                Ok(status) => {
                    item.status = status;
                    candidate.project.persist(store, &candidate.conversation_id, &mut item);
                }
                Err(error) => item.error = Some(error),
            }
        }
        match item.status.as_str() {
            "imported" => report.imported += 1,
            "updated" => report.updated += 1,
            "skipped" => report.skipped += 1,
            _ => report.failed += 1,
        }
        report.current += 1;
        report.conversations.push(item);
        progress(&report);
    }
    if report
        .conversations
        .iter()
        .any(|item| item.status == "failed")
    {
        report.warnings.push(
            "Some conversations were not imported. Their errors are listed in the results.".into(),
        );
    }
    progress(&report);
    Ok(report)
}

fn import_client(format: &str) -> &'static str {
    match format {
        "opencore" => "Imported OpenCore",
        "hermes" => "Imported Hermes",
        "opencode" => "Imported OpenCode",
        "codex" => "Imported Codex",
        "claude" => "Imported Claude Code",
        _ => "Imported JSON",
    }
}

fn persist_candidate(
    store: &EventStore,
    format: &str,
    id: &str,
    title: &str,
    rows: &[ImportedRow],
) -> Result<String, String> {
    if rows.is_empty() {
        return Err("The conversation has no importable history.".into());
    }
    let key = format!("portable_chat_import_v1_{id}");
    let fingerprint = digest(&serde_json::to_vec(rows).map_err(|error| error.to_string())?);
    store
        .replace_imported_history_with_receipt(
            id,
            import_client(format),
            title,
            rows,
            &key,
            &fingerprint,
        )
        .map(str::to_owned)
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn check_cancelled(cancelled: &dyn Fn() -> bool) -> Result<(), String> {
    if cancelled() {
        Err(IMPORT_CANCELLED.into())
    } else {
        Ok(())
    }
}
fn format_name(format: &str) -> Result<&str, String> {
    match format {
        "auto" | "opencore" | "hermes" | "opencode" | "codex" | "claude" | "generic" => Ok(format),
        _ => Err("Choose Auto, OpenCore, OpenCode, Hermes, Codex, Claude Code or Generic JSON.".into()),
    }
}

fn unchanged(before: &Metadata, after: &Metadata) -> bool {
    before.len() == after.len() && before.modified().ok() == after.modified().ok()
}

fn prepare_file(
    path: &Path,
    requested: &str,
    cancelled: &dyn Fn() -> bool,
) -> Result<PreparedFile, String> {
    format_name(requested)?;
    check_cancelled(cancelled)?;
    let mut source =
        File::open(path).map_err(|error| format!("Cannot read chat history: {error}"))?;
    let before = source.metadata().map_err(|error| error.to_string())?;
    if !before.is_file() {
        return Err("Choose a regular chat history file.".into());
    }
    let mut header = [0_u8; 16];
    let read = source
        .read(&mut header)
        .map_err(|error| error.to_string())?;
    source
        .seek(SeekFrom::Start(0))
        .map_err(|error| error.to_string())?;
    if read == 16 && &header == b"SQLite format 3\0" {
        if !matches!(requested, "auto" | "hermes" | "opencode") {
            return Err("SQLite chat import supports Hermes Agent and OpenCode sessions.".into());
        }
        drop(source);
        return prepare_database(path, requested, cancelled);
    }
    if before.len() > MAX_TEXT_BYTES {
        return Err(
            "Chat history exceeds the 64 MiB text import limit. Export fewer conversations.".into(),
        );
    }
    let mut bytes = Vec::with_capacity(before.len() as usize);
    (&mut source)
        .take(MAX_TEXT_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() as u64 > MAX_TEXT_BYTES {
        return Err("Chat history exceeds the 64 MiB text import limit.".into());
    }
    if !unchanged(
        &before,
        &source.metadata().map_err(|error| error.to_string())?,
    ) || !unchanged(
        &before,
        &fs::metadata(path).map_err(|error| error.to_string())?,
    ) {
        return Err("The source changed while it was being read. Try importing again when the source app is idle.".into());
    }
    let text = std::str::from_utf8(&bytes)
        .map_err(|_| "Chat history must be UTF-8 JSON or JSONL.".to_string())?
        .trim_start_matches('\u{feff}');
    if text.trim().is_empty() {
        return Err("The selected chat history file is empty.".into());
    }
    let is_jsonl = path
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("jsonl"));
    let records = if is_jsonl {
        let mut records = Vec::new();
        for (index, line) in text.lines().enumerate() {
            check_cancelled(cancelled)?;
            if line.trim().is_empty() {
                continue;
            }
            if line.len() > MAX_ITEM_BYTES {
                return Err(format!(
                    "JSONL line {} exceeds the 8 MiB record limit.",
                    index + 1
                ));
            }
            if records.len() >= MAX_ENTRIES {
                return Err("The file exceeds the 50,000 record import limit.".into());
            }
            records.push((
                index + 1,
                serde_json::from_str::<Value>(line)
                    .map_err(|error| format!("Invalid JSON at line {}: {error}", index + 1)),
            ));
        }
        records
    } else {
        vec![(
            1,
            Ok(serde_json::from_str::<Value>(text)
                .map_err(|error| format!("Invalid JSON: {error}"))?),
        )]
    };
    let detected = if requested == "auto" {
        detect_format(&records)?
    } else {
        requested
    };
    let prepared = match detected {
        "codex" | "claude" => prepare_rollout(records, detected, cancelled)?,
        "opencode" => opencode::prepare_documents(records, cancelled)?,
        _ => prepare_documents(records, detected, is_jsonl, cancelled)?,
    };
    validate_prepared(&prepared)?;
    Ok(prepared)
}

fn detect_format(records: &[(usize, Result<Value, String>)]) -> Result<&'static str, String> {
    for (_, record) in records {
        let Ok(value) = record else {
            continue;
        };
        let head = value
            .as_array()
            .and_then(|values| values.first())
            .unwrap_or(value);
        if head.pointer("/info/id").is_some()
            && head.get("messages").and_then(Value::as_array).is_some()
        {
            return Ok("opencode");
        }
        if head.get("entries").is_some()
            || head.get("format").and_then(Value::as_str) == Some("opencore-chat")
        {
            return Ok("opencore");
        }
        if matches!(
            head.get("type").and_then(Value::as_str),
            Some("session_meta" | "response_item" | "event_msg" | "turn_context")
        ) {
            return Ok("codex");
        }
        if head.get("sessionId").is_some()
            || (head.get("message").is_some()
                && matches!(
                    head.get("type").and_then(Value::as_str),
                    Some("user" | "assistant")
                ))
        {
            return Ok("claude");
        }
        if head.get("messages").is_some()
            && (head.get("started_at").is_some()
                || head.get("source").is_some()
                || head.get("session_id").is_some())
        {
            return Ok("hermes");
        }
        if head
            .get("conversations")
            .and_then(Value::as_array)
            .is_some_and(|items| items.first().is_some_and(|item| item.get("from").is_some()))
        {
            return Ok("hermes");
        }
        if head.get("role").is_some()
            || head.get("messages").is_some()
            || head.get("conversations").is_some()
            || head.get("conversation").is_some()
            || value.as_array().is_some_and(Vec::is_empty)
        {
            return Ok("generic");
        }
    }
    Err("This file is not a supported chat export. Choose the source format explicitly or export messages as JSON/JSONL.".into())
}

fn validate_prepared(prepared: &PreparedFile) -> Result<(), String> {
    if prepared.conversations.is_empty() {
        return Err("No conversations were found in this file.".into());
    }
    if prepared.conversations.len() > MAX_CONVERSATIONS {
        return Err("The file exceeds the 1,000 conversation import limit.".into());
    }
    let mut entries = 0usize;
    let mut bytes = 0usize;
    let mut identities = HashMap::new();
    for item in &prepared.conversations {
        entries += item.rows.len();
        for row in &item.rows {
            bytes = bytes.saturating_add(row_cost(row));
        }
        if entries > MAX_ENTRIES || bytes as u64 > MAX_TEXT_BYTES {
            return Err("The normalized history exceeds the import limit (50,000 entries or 64 MiB). Export fewer conversations.".into());
        }
        if item.error.is_none() {
            let fingerprint =
                digest(&serde_json::to_vec(&item.rows).map_err(|error| error.to_string())?);
            if let Some(previous) = identities.insert(&item.conversation_id, fingerprint.clone()) {
                if previous != fingerprint {
                    return Err("The export contains conflicting versions of the same conversation. Import one version at a time.".into());
                }
            }
        }
    }
    Ok(())
}

fn prepare_documents(
    records: Vec<(usize, Result<Value, String>)>,
    format: &str,
    jsonl: bool,
    cancelled: &dyn Fn() -> bool,
) -> Result<PreparedFile, String> {
    let (mut entries, mut bytes) = (0usize, 0usize);
    let raw_messages = jsonl
        && records
            .iter()
            .filter_map(|(_, row)| row.as_ref().ok())
            .any(|row| row.get("role").is_some() || row.get("from").is_some())
        && !records
            .iter()
            .filter_map(|(_, row)| row.as_ref().ok())
            .any(|row| row.get("messages").is_some() || row.get("entries").is_some());
    if raw_messages {
        let mut groups: Vec<(String, Vec<Value>)> = Vec::new();
        for (_, record) in records {
            check_cancelled(cancelled)?;
            let value = record?;
            let group_id = source_id(&value)
                .filter(|_| {
                    value.get("session_id").is_some()
                        || value.get("conversation_id").is_some()
                        || value.get("sessionId").is_some()
                        || value.get("conversationId").is_some()
                })
                .unwrap_or_default();
            if let Some((_, messages)) = groups.iter_mut().find(|(id, _)| id == &group_id) {
                messages.push(value);
            } else {
                groups.push((group_id, vec![value]));
            }
            if groups.len() > MAX_CONVERSATIONS {
                return Err("The file exceeds the 1,000 conversation import limit.".into());
            }
        }
        let mut conversations = Vec::new();
        for (id, messages) in groups {
            let value = if id.is_empty() {
                json!({"messages":messages})
            } else {
                json!({"id":id,"messages":messages})
            };
            let candidate = parse_conversation(value, format, cancelled)?;
            append_candidate(&mut conversations, &mut entries, &mut bytes, candidate)?;
        }
        return Ok(PreparedFile {
            source_format: format.into(),
            conversations,
        });
    }
    let mut conversations = Vec::new();
    for (line, record) in records {
        check_cancelled(cancelled)?;
        match record {
            Err(error) => append_candidate(
                &mut conversations,
                &mut entries,
                &mut bytes,
                failed_candidate(
                    format,
                    &format!("line:{line}"),
                    "Unreadable conversation",
                    error,
                ),
            )?,
            Ok(Value::Array(items)) if !is_message_array(&items) && !items.is_empty() => {
                for item in items {
                    check_cancelled(cancelled)?;
                    append_candidate(
                        &mut conversations,
                        &mut entries,
                        &mut bytes,
                        parse_conversation(item, format, cancelled)?,
                    )?;
                    if conversations.len() > MAX_CONVERSATIONS {
                        return Err("The file exceeds the 1,000 conversation import limit.".into());
                    }
                }
            }
            Ok(value) => {
                let collection = value
                    .get("conversations")
                    .or_else(|| value.get("sessions"))
                    .and_then(Value::as_array)
                    .filter(|items| !is_message_array(items));
                if let Some(items) = collection {
                    for item in items {
                        check_cancelled(cancelled)?;
                        append_candidate(
                            &mut conversations,
                            &mut entries,
                            &mut bytes,
                            parse_conversation(item.clone(), format, cancelled)?,
                        )?;
                        if conversations.len() > MAX_CONVERSATIONS {
                            return Err(
                                "The file exceeds the 1,000 conversation import limit.".into()
                            );
                        }
                    }
                } else {
                    append_candidate(
                        &mut conversations,
                        &mut entries,
                        &mut bytes,
                        parse_conversation(value, format, cancelled)?,
                    )?;
                }
            }
        }
    }
    Ok(PreparedFile {
        source_format: format.into(),
        conversations,
    })
}

fn append_candidate(
    conversations: &mut Vec<Candidate>,
    entries: &mut usize,
    bytes: &mut usize,
    candidate: Candidate,
) -> Result<(), String> {
    *entries = entries.saturating_add(candidate.rows.len());
    for row in &candidate.rows {
        *bytes = bytes.saturating_add(row_cost(row));
    }
    if conversations.len() >= MAX_CONVERSATIONS
        || *entries > MAX_ENTRIES
        || *bytes as u64 > MAX_TEXT_BYTES
    {
        return Err("The file exceeds the import limit (1,000 conversations, 50,000 entries or 64 MiB of copied history). Export fewer conversations.".into());
    }
    conversations.push(candidate);
    Ok(())
}

fn is_message_array(values: &[Value]) -> bool {
    values.first().is_some_and(|item| {
        item.get("role").is_some()
            || item.get("from").is_some()
            || item.get("content").is_some()
            || item.get("value").is_some()
    })
}
fn source_id(value: &Value) -> Option<String> {
    [
        "conversation_id",
        "conversationId",
        "session_id",
        "sessionId",
        "id",
    ]
    .iter()
    .find_map(|key| scalar_id(value.get(*key)))
    .or_else(|| scalar_id(value.pointer("/conversation/id")))
}
fn scalar_id(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::String(value) if !value.is_empty() => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        _ => None,
    }
}
fn copy_id(format: &str, id: &str) -> String {
    format!("import:{format}:{}", digest(id.as_bytes()))
}
fn short_title(text: &str) -> String {
    redact_text(text)
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(96)
        .collect()
}
fn failed_candidate(format: &str, id: &str, title: &str, error: String) -> Candidate {
    Candidate {
        conversation_id: copy_id(format, id),
        source_id: redact_text(id),
        title: short_title(title),
        rows: vec![],
        warnings: vec![],
        error: Some(redact_text(&error)),
        project: SourceProject::default(),
    }
}

fn conversation_header(value: &Value) -> Value {
    if let Some(conversation) = value.get("conversation").filter(|item| item.is_object()) {
        return conversation.clone();
    }
    let mut header = value.as_object().cloned().unwrap_or_default();
    for key in [
        "messages",
        "message",
        "entries",
        "conversations",
        "sessions",
        "conversation",
    ] {
        header.remove(key);
    }
    Value::Object(header)
}

fn safe_value(value: &Value, depth: usize) -> Result<Value, String> {
    if depth > 128 {
        return Err("A history record exceeds the supported nesting limit.".into());
    }
    let decoded = match value {
        Value::String(text) => {
            if text.len() > MAX_ITEM_BYTES {
                return Err("A history field exceeds the 8 MiB record limit.".into());
            }
            if matches!(text.trim_start().as_bytes().first(), Some(b'{' | b'[')) {
                if let Ok(inner) = serde_json::from_str::<Value>(text) {
                    let safe = safe_value(&inner, depth + 1)?;
                    if safe != inner {
                        return Ok(Value::String(safe.to_string()));
                    }
                }
            }
            Value::String(redact_text(text))
        }
        Value::Array(values) => Value::Array(
            values
                .iter()
                .map(|item| safe_value(item, depth + 1))
                .collect::<Result<_, _>>()?,
        ),
        Value::Object(fields) => {
            let mut safe = Map::new();
            for (key, value) in fields {
                // Ask the existing redactor about a key without recursively
                // cloning and redacting the whole subtree at every depth.
                let probe = Value::Object(Map::from_iter([(key.clone(), Value::Null)]));
                let secret =
                    redact_json(&probe).get(key).and_then(Value::as_str) == Some("[REDACTED]");
                safe.insert(
                    key.clone(),
                    if secret {
                        Value::String("[REDACTED]".into())
                    } else {
                        safe_value(value, depth + 1)?
                    },
                );
            }
            Value::Object(safe)
        }
        other => other.clone(),
    };
    Ok(decoded)
}

fn parse_conversation(
    value: Value,
    format: &str,
    cancelled: &dyn Fn() -> bool,
) -> Result<Candidate, String> {
    parse_conversation_with_content_projection(value, format, cancelled, false)
}

fn parse_conversation_with_content_projection(
    value: Value,
    format: &str,
    cancelled: &dyn Fn() -> bool,
    sqlite_content: bool,
) -> Result<Candidate, String> {
    check_cancelled(cancelled)?;
    let id = source_id(&value)
        .unwrap_or_else(|| format!("anonymous:{}", digest(value.to_string().as_bytes())));
    if id.len() > 1024 {
        return Ok(failed_candidate(
            format,
            &digest(id.as_bytes()),
            "Invalid conversation",
            "The source conversation ID is too long.".into(),
        ));
    }
    let title = value
        .pointer("/conversation/title")
        .or_else(|| value.get("title"))
        .or_else(|| value.get("name"))
        .and_then(Value::as_str)
        .map(short_title)
        .unwrap_or_default();
    let mut builder = Builder::new(format, &id, &title, conversation_header(&value));
    if value.get("sourceProjectConflict").is_some() {
        builder.warn("Multiple source projects share this folder. The exact session folder is retained; select the intended project in Projects.");
    }
    if source_id(&value).is_none() {
        builder.warn("This export has no conversation ID. Identical copies are deduplicated; changed exports create another copy.");
    }
    let result = (|| {
        let safe = safe_value(&value, 0)?;
        builder.header = conversation_header(&safe);
        if format == "opencore" {
            if let Some(version) = safe.get("version") {
                if version.as_u64() != Some(1) {
                    return Err("This OpenCore export version is not supported.".into());
                }
            }
            let entries = safe
                .get("entries")
                .and_then(Value::as_array)
                .ok_or("An OpenCore export must contain an entries array.")?;
            for (index, raw) in entries.iter().enumerate() {
                check_cancelled(cancelled)?;
                let entry: TimelineEntry =
                    serde_json::from_value(raw.clone()).map_err(|error| {
                        format!("Timeline entry {} is malformed: {error}", index + 1)
                    })?;
                if entry.conversation_id != id {
                    return Err(format!(
                        "Timeline entry {} belongs to a different conversation.",
                        index + 1
                    ));
                }
                let identity = builder.record_identity(raw, "timeline")?;
                let Some(identity) = identity else {
                    continue;
                };
                let stamp = builder.timestamp(raw.get("timestamp"))?;
                let mut metadata = if entry.metadata.is_object() {
                    entry.metadata.clone()
                } else {
                    json!({"originalMetadata":entry.metadata})
                };
                let fields = metadata.as_object_mut().ok_or("Invalid entry metadata")?;
                fields.insert("sourceRecord".into(), raw.clone());
                fields.insert(
                    "portableImport".into(),
                    builder.provenance(&identity, raw.get("timestamp"), Some(&entry.source)),
                );
                builder.push((
                    stamp,
                    entry.kind,
                    entry.role,
                    entry.title,
                    entry.content,
                    metadata,
                ))?;
            }
        } else {
            let messages = safe
                .as_array()
                .or_else(|| safe.get("messages").and_then(Value::as_array))
                .or_else(|| safe.get("conversation").and_then(Value::as_array))
                .or_else(|| safe.get("conversations").and_then(Value::as_array))
                .ok_or("A conversation must contain a messages array.")?;
            if messages.len() > MAX_ENTRIES {
                return Err("The conversation exceeds the 50,000 message limit.".into());
            }
            for (index, message) in messages.iter().enumerate() {
                check_cancelled(cancelled)?;
                // Keep the original (redacted) SQLite content string in sourceRecord.
                // A separate projection may render a verified encoded block array.
                let projection = if sqlite_content {
                    sqlite_message_projection(message)
                } else {
                    None
                };
                builder
                    .message(
                        projection.as_ref().unwrap_or(message),
                        message,
                        None,
                        message
                            .get("timestamp")
                            .or_else(|| message.get("created_at"))
                            .or_else(|| message.get("createdAt")),
                    )
                    .map_err(|error| format!("Message {}: {error}", index + 1))?;
            }
        }
        Ok::<(), String>(())
    })();
    match result {
        Err(error) if error == IMPORT_CANCELLED => Err(error),
        Err(error) => Ok(builder.failed(error)),
        Ok(()) => Ok(builder.finish()),
    }
}

struct Builder {
    format: String,
    source_id: String,
    title: String,
    header: Value,
    rows: Vec<ImportedRow>,
    warnings: Vec<String>,
    seen: HashMap<String, String>,
    anonymous: HashMap<String, usize>,
    last_time: String,
    bytes: usize,
    project: SourceProject,
}

impl Builder {
    fn new(format: &str, id: &str, title: &str, header: Value) -> Self {
        let project = SourceProject::from_header(&header);
        Self {
            format: format.into(),
            source_id: id.into(),
            title: title.into(),
            header,
            rows: vec![],
            warnings: vec![],
            seen: HashMap::new(),
            anonymous: HashMap::new(),
            last_time: "1970-01-01T00:00:00.000000000Z".into(),
            bytes: 0,
            project,
        }
    }
    fn warn(&mut self, warning: &str) {
        if !self.warnings.iter().any(|existing| existing == warning) {
            self.warnings.push(warning.into());
        }
    }
    fn record_identity(&mut self, raw: &Value, category: &str) -> Result<Option<String>, String> {
        let fingerprint = digest(raw.to_string().as_bytes());
        let explicit = ["uuid", "message_uid", "id", "message_id"]
            .iter()
            .find_map(|key| scalar_id(raw.get(*key)))
            .or_else(|| scalar_id(raw.pointer("/payload/id")))
            .or_else(|| scalar_id(raw.pointer("/payload/call_id")))
            .or_else(|| scalar_id(raw.pointer("/message/id")));
        if let Some(explicit) = explicit {
            let identity = format!("{category}:{explicit}");
            if let Some(previous) = self.seen.get(&identity) {
                if previous != &fingerprint {
                    return Err("Conflicting records have the same source event ID. No part of this conversation was imported.".into());
                }
                self.warn("A repeated source event was ignored.");
                return Ok(None);
            }
            self.seen.insert(identity.clone(), fingerprint);
            Ok(Some(identity))
        } else {
            let occurrence = self.anonymous.entry(fingerprint.clone()).or_default();
            let identity = format!("{category}:{fingerprint}:{}", *occurrence);
            *occurrence += 1;
            Ok(Some(identity))
        }
    }
    fn timestamp(&mut self, value: Option<&Value>) -> Result<String, String> {
        if let Some(value) = value.filter(|value| !value.is_null() && value.as_str() != Some("")) {
            let time = normalized_time(value)?;
            self.last_time = time.clone();
            return Ok(time);
        }
        if self.rows.is_empty() {
            if let Some(value) = ["started_at", "created_at", "createdAt", "timestamp"]
                .iter()
                .find_map(|key| self.header.get(*key))
                .filter(|value| !value.is_null())
            {
                self.last_time = normalized_time(value)?;
            }
        }
        self.warn("Some entries have no timestamp. Source order is preserved with deterministic ordering timestamps.");
        Ok(self.last_time.clone())
    }
    fn provenance(
        &self,
        identity: &str,
        timestamp: Option<&Value>,
        original_source: Option<&str>,
    ) -> Value {
        json!({"version":1,"format":self.format,"sourceConversationId":redact_text(&self.source_id),
            "copyId":copy_id(&self.format,&self.source_id),
            "eventId":digest(format!("{}\0{}\0{identity}",self.format,self.source_id).as_bytes()),
            "originalTimestamp":timestamp.cloned().unwrap_or(Value::Null),"originalSource":original_source,
            "sourceConversation":self.header,"inert":true})
    }
    fn push(&mut self, row: ImportedRow) -> Result<(), String> {
        self.bytes = self.bytes.saturating_add(row_cost(&row));
        if self.rows.len() >= MAX_ENTRIES || self.bytes as u64 > MAX_TEXT_BYTES {
            return Err("The conversation exceeds the normalized history import limit.".into());
        }
        if self.title.is_empty() && row.1 == "message" && row.2 == "user" {
            self.title = short_title(&row.4);
        }
        self.rows.push(row);
        Ok(())
    }
    fn message(
        &mut self,
        message: &Value,
        raw: &Value,
        supplied_role: Option<&str>,
        timestamp: Option<&Value>,
    ) -> Result<(), String> {
        if !message.is_object() {
            return Err("Each message must be an object with a role and content.".into());
        }
        let role = message
            .get("role")
            .or_else(|| message.get("from"))
            .and_then(Value::as_str)
            .or(supplied_role)
            .ok_or("The message has no role.")?;
        let role = match role {
            "human" => "user",
            "gpt" => "assistant",
            "function" | "observation" => "tool",
            "user" | "assistant" | "system" | "developer" | "tool" => role,
            _ => return Err("The message role is not supported.".into()),
        };
        let Some(identity) = self.record_identity(raw, "message")? else {
            return Ok(());
        };
        let stamp = self.timestamp(timestamp)?;
        let parts = message_parts(message, role)?;
        if parts.is_empty() {
            return Err("The message has no content, reasoning or tool activity.".into());
        }
        for (index, part) in parts.into_iter().enumerate() {
            let event = format!("{identity}:part:{index}");
            let metadata =
                json!({"sourceRecord":raw,"portableImport":self.provenance(&event,timestamp,None)});
            self.push((
                stamp.clone(),
                part.kind,
                part.role,
                part.title,
                part.content,
                metadata,
            ))?;
        }
        Ok(())
    }
    fn failed(self, error: String) -> Candidate {
        Candidate {
            conversation_id: copy_id(&self.format, &self.source_id),
            source_id: redact_text(&self.source_id),
            title: if self.title.is_empty() {
                format!("{} conversation", import_client(&self.format))
            } else {
                self.title
            },
            rows: vec![],
            warnings: self.warnings,
            error: Some(redact_text(&error)),
            project: self.project,
        }
    }
    fn finish(mut self) -> Candidate {
        if self.rows.is_empty() {
            return self.failed("The conversation contains no importable history.".into());
        }
        self.rows.sort_by(|left, right| left.0.cmp(&right.0));
        Candidate {
            conversation_id: copy_id(&self.format, &self.source_id),
            source_id: redact_text(&self.source_id),
            title: if self.title.is_empty() {
                format!("{} conversation", import_client(&self.format))
            } else {
                self.title
            },
            rows: self.rows,
            warnings: self.warnings,
            error: None,
            project: self.project,
        }
    }
}

fn row_cost(row: &ImportedRow) -> usize {
    row.0.len() + row.1.len() + row.2.len() + row.3.len() + row.4.len() + row.5.to_string().len()
}

fn normalized_time(value: &Value) -> Result<String, String> {
    let time = if let Some(text) = value.as_str() {
        if let Ok(time) = DateTime::parse_from_rfc3339(text) {
            time.with_timezone(&Utc)
        } else if let Ok(time) = NaiveDateTime::parse_from_str(text, "%Y-%m-%dT%H:%M:%S%.f") {
            time.and_utc()
        } else if let Ok(time) = NaiveDateTime::parse_from_str(text, "%Y-%m-%d %H:%M:%S%.f") {
            time.and_utc()
        } else if let Ok(number) = text.parse::<f64>() {
            epoch_time(number)?
        } else {
            return Err("A timestamp is invalid. Use an ISO 8601 timestamp or Unix time.".into());
        }
    } else if let Some(number) = value.as_f64() {
        epoch_time(number)?
    } else {
        return Err("A timestamp must be an ISO 8601 string or Unix time.".into());
    };
    Ok(time.to_rfc3339_opts(SecondsFormat::Nanos, true))
}
fn epoch_time(mut number: f64) -> Result<DateTime<Utc>, String> {
    if !number.is_finite() {
        return Err("A timestamp is not finite.".into());
    }
    if number.abs() >= 100_000_000_000_000.0 {
        number /= 1_000_000.0;
    } else if number.abs() >= 100_000_000_000.0 {
        number /= 1_000.0;
    }
    let seconds = number.floor();
    if seconds < i64::MIN as f64 || seconds > i64::MAX as f64 {
        return Err("A timestamp is outside the supported range.".into());
    }
    let nanos = ((number - seconds) * 1_000_000_000.0).round() as u32;
    let (seconds, nanos) = if nanos == 1_000_000_000 {
        (
            (seconds as i64)
                .checked_add(1)
                .ok_or("A timestamp is outside the supported range.")?,
            0,
        )
    } else {
        (seconds as i64, nanos)
    };
    DateTime::<Utc>::from_timestamp(seconds, nanos)
        .ok_or_else(|| "A timestamp is outside the supported range.".into())
}

struct Part {
    kind: String,
    role: String,
    title: String,
    content: String,
}
fn part(kind: &str, role: &str, title: &str, content: String) -> Part {
    Part {
        kind: kind.into(),
        role: role.into(),
        title: title.into(),
        content,
    }
}
fn text_value(value: &Value) -> String {
    if let Some(text) = value.as_str() {
        return text.to_string();
    }
    if let Some(parts) = value.as_array() {
        return parts.iter().map(text_value).collect::<Vec<_>>().join("\n");
    }
    for key in ["text", "thinking", "content", "input_text", "output_text"] {
        if let Some(text) = value.get(key).and_then(Value::as_str) {
            return text.into();
        }
    }
    value.to_string()
}
fn message_parts(message: &Value, role: &str) -> Result<Vec<Part>, String> {
    let mut out = Vec::new();
    let mut reasonings = HashSet::new();
    for key in ["reasoning", "reasoning_content"] {
        if let Some(value) = message.get(key).filter(|value| !value.is_null()) {
            let text = text_value(value);
            if !text.is_empty() && reasonings.insert(text.clone()) {
                out.push(part("thinking", role, "Reasoning", text));
            }
        }
    }
    if let Some(content) = message.get("content").or_else(|| message.get("value")) {
        match content {
            Value::String(text) => {
                if !text.is_empty() || (out.is_empty() && message.get("tool_calls").is_none()) {
                    out.push(part(
                        if role == "tool" {
                            "tool_result"
                        } else {
                            "message"
                        },
                        role,
                        if role == "user" {
                            "User"
                        } else if role == "assistant" {
                            "Assistant"
                        } else if role == "tool" {
                            "Tool result"
                        } else {
                            role
                        },
                        text.clone(),
                    ));
                }
            }
            Value::Array(blocks) => {
                for block in blocks {
                    if let Some(text) = block.as_str() {
                        out.push(part("message", role, role, text.into()));
                        continue;
                    }
                    if !block.is_object() {
                        return Err("A content block must be text or an object.".into());
                    }
                    let typ = block.get("type").and_then(Value::as_str).unwrap_or("data");
                    match typ {
                        "text" | "input_text" | "output_text" => {
                            let text = block
                                .get("text")
                                .or_else(|| block.get("content"))
                                .and_then(Value::as_str)
                                .ok_or("A text block has no text.")?;
                            out.push(part(
                                if role == "tool" {
                                    "tool_result"
                                } else {
                                    "message"
                                },
                                role,
                                role,
                                text.into(),
                            ));
                        }
                        "thinking" | "reasoning" | "summary_text" => {
                            out.push(part("thinking", role, "Reasoning", text_value(block)))
                        }
                        "tool_use" | "tool-call" | "function_call" | "custom_tool_call" => {
                            let name = block
                                .get("name")
                                .and_then(Value::as_str)
                                .ok_or("A tool call has no name.")?;
                            let body = block
                                .get("input")
                                .or_else(|| block.get("arguments"))
                                .unwrap_or(&Value::Null);
                            out.push(part("tool_call", "assistant", name, text_value(body)));
                        }
                        "tool_result" | "function_call_output" | "custom_tool_call_output" => out
                            .push(part(
                                "tool_result",
                                "tool",
                                "Tool result",
                                text_value(
                                    block
                                        .get("content")
                                        .or_else(|| block.get("output"))
                                        .unwrap_or(block),
                                ),
                            )),
                        _ => out.push(part("activity", role, typ, block.to_string())),
                    }
                }
            }
            Value::Null => {}
            _ => {
                return Err(
                    "Message content must be text, an array of blocks or null with tool activity."
                        .into(),
                )
            }
        }
    }
    if let Some(calls) = message.get("tool_calls").filter(|value| !value.is_null()) {
        let calls = calls.as_array().ok_or("tool_calls must be an array.")?;
        for call in calls {
            let function = call.get("function").unwrap_or(call);
            let name = function
                .get("name")
                .and_then(Value::as_str)
                .filter(|name| !name.is_empty())
                .ok_or("A tool call has no function name.")?;
            let arguments = function
                .get("arguments")
                .or_else(|| function.get("input"))
                .unwrap_or(&Value::Null);
            out.push(part("tool_call", "assistant", name, text_value(arguments)));
        }
    }
    Ok(out)
}

fn prepare_rollout(
    records: Vec<(usize, Result<Value, String>)>,
    format: &str,
    cancelled: &dyn Fn() -> bool,
) -> Result<PreparedFile, String> {
    let records = records
        .into_iter()
        .map(|(_, value)| value.and_then(|value| safe_value(&value, 0)))
        .collect::<Result<Vec<_>, _>>()?;
    let ids: HashSet<String> = records
        .iter()
        .filter_map(|value| {
            if format == "codex" {
                if value.get("type").and_then(Value::as_str) == Some("session_meta") {
                    scalar_id(value.pointer("/payload/id"))
                } else {
                    None
                }
            } else {
                scalar_id(value.get("sessionId"))
            }
        })
        .collect();
    if ids.len() > 1 {
        return Err(
            "This rollout contains more than one session. Export one session per rollout file."
                .into(),
        );
    }
    let id = ids.into_iter().next().unwrap_or_else(|| {
        format!(
            "anonymous:{}",
            digest(Value::Array(records.clone()).to_string().as_bytes())
        )
    });
    let header = if format == "codex" {
        records
            .iter()
            .find(|value| value.get("type").and_then(Value::as_str) == Some("session_meta"))
            .and_then(|value| value.get("payload"))
            .cloned()
            .unwrap_or_else(|| json!({}))
    } else {
        records
            .first()
            .map(conversation_header)
            .unwrap_or_else(|| json!({}))
    };
    let mut builder = Builder::new(format, &id, "", header);
    let result = (|| {
        for value in &records {
            check_cancelled(cancelled)?;
            let typ = value
                .get("type")
                .and_then(Value::as_str)
                .ok_or("A rollout record has no type.")?;
            let timestamp = value.get("timestamp");
            if format == "claude" {
                match typ {
                    "custom-title" | "ai-title" => {
                        if let Some(title) = value
                            .get("customTitle")
                            .or_else(|| value.get("aiTitle"))
                            .and_then(Value::as_str)
                        {
                            builder.title = short_title(title);
                        }
                    }
                    "user" | "assistant" => builder.message(
                        value.get("message").unwrap_or(value),
                        value,
                        Some(typ),
                        timestamp,
                    )?,
                    _ => builder.warn("Non-message rollout bookkeeping records were omitted."),
                }
                continue;
            }
            if typ == "session_meta" || typ == "turn_context" {
                continue;
            }
            if typ == "event_msg" {
                if matches!(
                    value.pointer("/payload/type").and_then(Value::as_str),
                    Some("agent_message" | "user_message" | "agent_reasoning")
                ) {
                    continue;
                }
            } else if typ != "response_item" {
                builder.warn("Non-message rollout bookkeeping records were omitted.");
                continue;
            }
            let payload = value
                .get("payload")
                .ok_or("A rollout record has no payload.")?;
            let subtype = payload
                .get("type")
                .and_then(Value::as_str)
                .ok_or("A response item has no type.")?;
            if subtype == "message" {
                builder.message(payload, value, None, timestamp)?;
                continue;
            }
            let Some(identity) = builder.record_identity(value, subtype)? else {
                continue;
            };
            let stamp = builder.timestamp(timestamp)?;
            let part = match subtype {
                "reasoning" => part(
                    "thinking",
                    "assistant",
                    "Reasoning",
                    text_value(
                        payload
                            .get("summary")
                            .or_else(|| payload.get("content"))
                            .unwrap_or(payload),
                    ),
                ),
                "function_call" | "custom_tool_call" => part(
                    "tool_call",
                    "assistant",
                    payload
                        .get("name")
                        .and_then(Value::as_str)
                        .ok_or("A tool call has no name.")?,
                    text_value(
                        payload
                            .get("arguments")
                            .or_else(|| payload.get("input"))
                            .unwrap_or(payload),
                    ),
                ),
                "function_call_output" | "custom_tool_call_output" => part(
                    "tool_result",
                    "tool",
                    "Tool result",
                    text_value(payload.get("output").unwrap_or(payload)),
                ),
                _ => part("activity", "system", subtype, payload.to_string()),
            };
            let metadata = json!({"sourceRecord":value,"portableImport":builder.provenance(&identity,timestamp,None)});
            builder.push((
                stamp,
                part.kind,
                part.role,
                part.title,
                part.content,
                metadata,
            ))?;
        }
        Ok::<(), String>(())
    })();
    let conversation = match result {
        Err(error) if error == IMPORT_CANCELLED => return Err(error),
        Err(error) => builder.failed(error),
        Ok(()) => builder.finish(),
    };
    Ok(PreparedFile {
        source_format: format.into(),
        conversations: vec![conversation],
    })
}

// SQLite's read-only mode can still touch a WAL shared-memory sidecar. Copy the
// bounded main file and WAL with read-only handles, and open only that snapshot.
// Hermes' source database, WAL and SHM are never opened by SQLite or modified.
struct DatabaseSnapshot {
    root: PathBuf,
    path: PathBuf,
}
impl Drop for DatabaseSnapshot {
    fn drop(&mut self) {
        if let Ok(entries) = fs::read_dir(&self.root) {
            for entry in entries.flatten() {
                let _ = fs::remove_file(entry.path());
            }
        }
        let _ = fs::remove_dir(&self.root);
    }
}
fn sqlite_sidecar(path: &Path, suffix: &str) -> PathBuf {
    let mut name = path.as_os_str().to_os_string();
    name.push(suffix);
    PathBuf::from(name)
}
#[cfg(windows)]
fn open_database_source(path: &Path) -> std::io::Result<File> {
    use std::os::windows::fs::OpenOptionsExt;
    // FILE_SHARE_READ only: every retained handle denies current/new writers
    // and deletes. Length/mtime checks alone cannot synchronize SQLite WAL reuse.
    fs::OpenOptions::new().read(true).share_mode(1).open(path)
}
#[cfg(windows)]
fn database_source_error(error: std::io::Error) -> String {
    if matches!(error.raw_os_error(), Some(32) | Some(33)) {
        CLOSE_HERMES_DATABASE.into()
    } else {
        format!("Cannot acquire a read-only Hermes database snapshot: {error}")
    }
}
#[cfg(not(windows))]
fn snapshot_database(
    _path: &Path,
    cancelled: &dyn Fn() -> bool,
) -> Result<DatabaseSnapshot, String> {
    check_cancelled(cancelled)?;
    Err("Hermes SQLite import requires exclusive writer protection available on Windows. Use a JSON/JSONL export on this platform.".into())
}
#[cfg(windows)]
fn snapshot_database(
    path: &Path,
    cancelled: &dyn Fn() -> bool,
) -> Result<DatabaseSnapshot, String> {
    check_cancelled(cancelled)?;
    // Hold the main-file guard before discovering/opening the WAL. This blocks
    // normal SQLite writers throughout both copies, including checkpoints.
    let main = open_database_source(path).map_err(database_source_error)?;
    let wal = sqlite_sidecar(path, "-wal");
    let wal_source = match open_database_source(&wal) {
        Ok(source) => Some(source),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(database_source_error(error)),
    };
    let root =
        std::env::temp_dir().join(format!("opencore-chat-snapshot-{}", uuid::Uuid::new_v4()));
    fs::create_dir(&root)
        .map_err(|error| format!("Cannot prepare a read-only database snapshot: {error}"))?;
    let snapshot = DatabaseSnapshot {
        path: root.join("history.sqlite3"),
        root,
    };
    let mut sources = vec![(main, path.to_path_buf(), snapshot.path.clone())];
    if let Some(source) = wal_source {
        sources.push((source, wal.clone(), sqlite_sidecar(&snapshot.path, "-wal")));
    }
    let initial: Vec<_> = sources
        .iter()
        .map(|(source, _, _)| source.metadata().map_err(|error| error.to_string()))
        .collect::<Result<_, _>>()?;
    let total = initial.iter().try_fold(0u64, |total, meta| {
        total
            .checked_add(meta.len())
            .ok_or("Database size is outside the supported range.")
    })?;
    if total > MAX_DATABASE_BYTES {
        return Err("The Hermes database and WAL exceed the 512 MiB snapshot limit. Export sessions as JSONL instead.".into());
    }
    for ((source, _, target), before) in sources.iter_mut().zip(&initial) {
        check_cancelled(cancelled)?;
        if !before.is_file() {
            return Err("The Hermes database and its WAL must be regular files.".into());
        }
        let mut output = File::create_new(target).map_err(|error| error.to_string())?;
        let mut copied = 0u64;
        let mut buffer = [0u8; 64 * 1024];
        loop {
            check_cancelled(cancelled)?;
            let count = source
                .read(&mut buffer)
                .map_err(|error| error.to_string())?;
            if count == 0 {
                break;
            }
            copied += count as u64;
            if copied > before.len() {
                return Err(
                    "The Hermes source changed during the snapshot. Close Hermes and try again."
                        .into(),
                );
            }
            output
                .write_all(&buffer[..count])
                .map_err(|error| error.to_string())?;
        }
        if copied != before.len()
            || !unchanged(
                before,
                &source.metadata().map_err(|error| error.to_string())?,
            )
        {
            return Err(
                "The Hermes source changed during the snapshot. Close Hermes and try again.".into(),
            );
        }
    }
    for ((source, source_path, _), before) in sources.iter().zip(initial) {
        if !unchanged(
            &before,
            &source.metadata().map_err(|error| error.to_string())?,
        ) || !unchanged(
            &before,
            &fs::metadata(source_path).map_err(|error| error.to_string())?,
        ) {
            return Err(
                "The Hermes source changed during the snapshot. Close Hermes and try again.".into(),
            );
        }
    }
    if sources.len() == 1 && wal.exists() {
        return Err("Hermes created a WAL during the snapshot. Close Hermes and try again.".into());
    }
    Ok(snapshot)
}

fn table_columns(db: &Connection, table: &str, required: &[&str]) -> Result<Vec<String>, String> {
    let definition: Option<(String, Option<String>)> = db
        .query_row(
            "SELECT type,sql FROM sqlite_master WHERE name=?1",
            [table],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(|error| format!("Cannot read the Hermes/OpenCode database schema: {error}"))?;
    let Some((kind, sql)) = definition else {
        return Err(format!(
            "This is not a supported Hermes/OpenCode database: missing {table}."
        ));
    };
    if kind != "table"
        || sql
            .unwrap_or_default()
            .to_ascii_uppercase()
            .contains("VIRTUAL TABLE")
    {
        return Err("Hermes/OpenCode import requires ordinary source tables.".into());
    }
    let mut statement = db
        .prepare(&format!("PRAGMA table_info(\"{table}\")"))
        .map_err(|error| error.to_string())?;
    let columns = statement
        .query_map([], |row| row.get::<_, String>(1))
        .map_err(|error| error.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    if columns.len() > MAX_COLUMNS
        || required
            .iter()
            .any(|required| !columns.iter().any(|column| column == required))
    {
        return Err(format!(
            "The Hermes/OpenCode {table} schema is missing required columns or is not supported."
        ));
    }
    Ok(columns)
}

fn sqlite_message_projection(message: &Value) -> Option<Value> {
    let content = message.get("content")?.as_str()?;
    if !content.trim_start().starts_with('[') {
        return None;
    }
    let parsed: Value = serde_json::from_str(content).ok()?;
    let blocks = parsed.as_array()?;
    if blocks.is_empty() || !blocks.iter().all(is_structured_content_block) {
        return None;
    }
    let mut projection = message.clone();
    projection.as_object_mut()?.insert("content".into(), parsed);
    Some(projection)
}

fn is_structured_content_block(block: &Value) -> bool {
    let Some(kind) = block.get("type").and_then(Value::as_str) else {
        return false;
    };
    let text = |key: &str| block.get(key).and_then(Value::as_str).is_some();
    match kind {
        "text" | "input_text" | "output_text" => block
            .get("text")
            .or_else(|| block.get("content"))
            .and_then(Value::as_str)
            .is_some(),
        "thinking" | "reasoning" | "summary_text" => {
            text("thinking") || text("text") || text("content")
        }
        "redacted_thinking" => text("data"),
        "image_url" => {
            text("image_url")
                || block
                    .pointer("/image_url/url")
                    .and_then(Value::as_str)
                    .is_some()
        }
        "input_image" => text("image_url") || text("file_id"),
        "image" => match block.pointer("/source/type").and_then(Value::as_str) {
            Some("url") => block
                .pointer("/source/url")
                .and_then(Value::as_str)
                .is_some(),
            Some("base64") => {
                block
                    .pointer("/source/data")
                    .and_then(Value::as_str)
                    .is_some()
                    && block
                        .pointer("/source/media_type")
                        .and_then(Value::as_str)
                        .is_some()
            }
            _ => false,
        },
        "input_audio" => {
            block
                .pointer("/input_audio/data")
                .and_then(Value::as_str)
                .is_some()
                && block
                    .pointer("/input_audio/format")
                    .and_then(Value::as_str)
                    .is_some()
        }
        "tool_use" | "tool-call" | "function_call" | "custom_tool_call" => {
            text("name") && (block.get("input").is_some() || block.get("arguments").is_some())
        }
        "tool_result" | "function_call_output" | "custom_tool_call_output" => {
            block.get("content").is_some() || block.get("output").is_some()
        }
        _ => false,
    }
}

fn sql_record(row: &Row<'_>, columns: &[String], budget: &mut usize) -> Result<Value, String> {
    let mut value = Map::new();
    for (index, column) in columns.iter().enumerate() {
        let cell = row.get_ref(index).map_err(|error| error.to_string())?;
        let field = match cell {
            ValueRef::Null => Value::Null,
            ValueRef::Integer(value) => json!(value),
            ValueRef::Real(value) => serde_json::Number::from_f64(value)
                .map(Value::Number)
                .ok_or("A Hermes database field contains a non-finite number.")?,
            ValueRef::Text(bytes) => {
                if bytes.len() > MAX_ITEM_BYTES {
                    return Err("A Hermes database field exceeds the 8 MiB record limit.".into());
                }
                let text = std::str::from_utf8(bytes)
                    .map_err(|_| "A Hermes database text field is not UTF-8.")?;
                *budget = budget.saturating_add(bytes.len());
                if matches!(
                    column.as_str(),
                    "tool_calls"
                        | "reasoning_details"
                        | "codex_reasoning_items"
                        | "codex_message_items"
                        | "display_metadata"
                        | "tool_call_uids"
                        | "absorbed_message_uids"
                ) {
                    serde_json::from_str(text).map_err(|error| {
                        format!("The Hermes {column} field contains invalid JSON: {error}")
                    })?
                } else {
                    Value::String(text.into())
                }
            }
            ValueRef::Blob(bytes) => {
                if bytes.len() > MAX_ITEM_BYTES {
                    return Err("A Hermes database field exceeds the 8 MiB record limit.".into());
                }
                *budget = budget.saturating_add(bytes.len() * 2);
                json!({"encoding":"base64","data":base64::engine::general_purpose::STANDARD.encode(bytes)})
            }
        };
        if *budget as u64 > MAX_TEXT_BYTES {
            return Err(
                "Hermes history exceeds the 64 MiB decoded import limit. Export fewer sessions."
                    .into(),
            );
        }
        value.insert(column.clone(), field);
    }
    Ok(Value::Object(value))
}

fn prepare_database(
    path: &Path,
    requested: &str,
    cancelled: &dyn Fn() -> bool,
) -> Result<PreparedFile, String> {
    let snapshot = snapshot_database(path, cancelled)?;
    let db = Connection::open_with_flags(
        &snapshot.path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|error| format!("Cannot read the Hermes database snapshot: {error}"))?;
    db.busy_timeout(std::time::Duration::from_secs(2))
        .map_err(|error| error.to_string())?;
    db.execute_batch("PRAGMA query_only=ON; PRAGMA trusted_schema=OFF;")
        .map_err(|error| error.to_string())?;
    let has_table = |name: &str| -> bool {
        db.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name=?1)", [name], |row| row.get(0)).unwrap_or(false)
    };
    if requested == "auto" && has_table("sessions") && has_table("session") {
        return Err("This database contains multiple source schemas. Choose Hermes or OpenCode explicitly.".into());
    }
    if requested == "opencode" || (requested == "auto" && !has_table("sessions") && (has_table("session") || has_table("session_v2"))) {
        let prepared = opencode::prepare_database(&db, cancelled)?;
        validate_prepared(&prepared)?;
        return Ok(prepared);
    }
    let (project_registry, project_warning) = projects::hermes_registry(path, cancelled)?;
    let session_columns = table_columns(&db, "sessions", &["id", "source", "started_at"])?;
    let message_columns = table_columns(
        &db,
        "messages",
        &["id", "session_id", "role", "content", "timestamp"],
    )?;
    let mut session_statement = db
        .prepare("SELECT * FROM sessions ORDER BY started_at,id LIMIT 1001")
        .map_err(|error| error.to_string())?;
    let mut session_rows = session_statement
        .query([])
        .map_err(|error| error.to_string())?;
    let mut sessions = Vec::new();
    let mut budget = 0usize;
    while let Some(row) = session_rows.next().map_err(|error| error.to_string())? {
        check_cancelled(cancelled)?;
        if sessions.len() >= MAX_CONVERSATIONS {
            return Err("The database exceeds the 1,000 conversation import limit. Export selected sessions as JSONL.".into());
        }
        sessions.push(sql_record(row, &session_columns, &mut budget)?);
    }
    drop(session_rows);
    drop(session_statement);
    let mut statement = db
        .prepare("SELECT * FROM messages WHERE session_id=?1 ORDER BY id LIMIT 50001")
        .map_err(|error| error.to_string())?;
    let mut conversations = Vec::new();
    let (mut message_count, mut entry_count, mut normalized_bytes) = (0usize, 0usize, 0usize);
    for mut session in sessions {
        check_cancelled(cancelled)?;
        projects::attach_hermes_project(&mut session, &project_registry);
        let id = scalar_id(session.get("id")).ok_or("A Hermes session has no ID.")?;
        let mut rows = statement.query([&id]).map_err(|error| error.to_string())?;
        let mut messages = Vec::new();
        let mut error = None;
        while let Some(row) = rows.next().map_err(|error| error.to_string())? {
            check_cancelled(cancelled)?;
            message_count += 1;
            if message_count > MAX_ENTRIES {
                return Err("The database exceeds the 50,000 message import limit. Export selected sessions as JSONL.".into());
            }
            match sql_record(row, &message_columns, &mut budget) {
                Ok(message) => messages.push(message),
                Err(cause) => {
                    error = Some(cause);
                    break;
                }
            }
        }
        if let Some(error) = error {
            append_candidate(
                &mut conversations,
                &mut entry_count,
                &mut normalized_bytes,
                failed_candidate(
                    "hermes",
                    &id,
                    session
                        .get("title")
                        .and_then(Value::as_str)
                        .unwrap_or("Hermes conversation"),
                    error,
                ),
            )?;
        } else {
            session
                .as_object_mut()
                .ok_or("Invalid Hermes session")?
                .insert("messages".into(), Value::Array(messages));
            let mut candidate = parse_conversation_with_content_projection(session, "hermes", cancelled, true)?;
            if let Some(warning) = &project_warning { candidate.warnings.push(warning.clone()); }
            append_candidate(
                &mut conversations,
                &mut entry_count,
                &mut normalized_bytes,
                candidate,
            )?;
        }
    }
    let prepared = PreparedFile {
        source_format: "hermes".into(),
        conversations,
    };
    validate_prepared(&prepared)?;
    Ok(prepared)
}

#[cfg(test)]
#[path = "chat_import_tests.rs"]
mod tests;
