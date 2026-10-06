use crate::store::EventStore;
use serde_json::Value;
use std::fs::{self, File};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

#[path = "agent_connector_history.rs"]
mod agent_connector_history;

pub const SYNC_CANCELLED: &str = "__HISTORY_SYNC_CANCELLED__";

#[derive(Debug, Clone, Default)]
pub struct SyncReport {
    pub current: usize,
    pub total: usize,
    pub imported: usize,
    pub updated: usize,
    pub skipped: usize,
    pub failed: usize,
    pub folders_found: usize,
    pub folders_unresolved: usize,
}

fn record_source_folder(store: &EventStore, id: &str, cwd: Option<&str>, report: &mut SyncReport) {
    match store.record_imported_directory(id, cwd.map(Path::new)) {
        Ok(()) if cwd.is_some() => report.folders_found += 1,
        Ok(()) => report.folders_unresolved += 1,
        Err(error) => {
            report.folders_unresolved += 1;
            store.log("warn", "history", &format!("{id}: folder not linked: {error}"));
        }
    }
}

fn jsonl_files(root: &Path, out: &mut Vec<PathBuf>, cancelled: &dyn Fn() -> bool) -> Result<(), String> {
    if cancelled() { return Err(SYNC_CANCELLED.into()); }
    let Ok(entries) = fs::read_dir(root) else { return Ok(()) };
    for entry in entries.flatten() {
        if cancelled() { return Err(SYNC_CANCELLED.into()); }
        let path = entry.path();
        if path.is_dir() {
            jsonl_files(&path, out, cancelled)?;
        } else if path.extension().and_then(|v| v.to_str()) == Some("jsonl") {
            out.push(path);
        }
    }
    Ok(())
}

fn newest_jsonl(root: &Path, cancelled: &dyn Fn() -> bool) -> Result<Vec<PathBuf>, String> {
    let mut files = Vec::new();
    jsonl_files(root, &mut files, cancelled)?;
    let mut modified = Vec::with_capacity(files.len());
    for path in files {
        if cancelled() { return Err(SYNC_CANCELLED.into()); }
        modified.push((fs::metadata(&path).and_then(|metadata| metadata.modified()).ok(), path));
    }
    modified.sort_by_key(|(stamp, _)| *stamp);
    modified.reverse();
    Ok(modified.into_iter().map(|(_, path)| path).collect())
}

fn is_internal_context(text: &str) -> bool {
    let value = text.trim_start().to_ascii_lowercase();
    [
        "<recommended_plugins>",
        "<environment_context>",
        "<permissions instructions>",
        "<collaboration_mode>",
        "<skills_instructions>",
        "<memories>",
        "<developer_instructions>",
        "<local-command-caveat>",
        "<command-name>",
        "<command-message>",
        "<command-args>",
        "<cos_context:",
    ].iter().any(|prefix| value.starts_with(prefix))
}

fn clean_title(text: &str) -> Option<String> {
    if is_internal_context(text) {
        return None;
    }
    let compact = text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('<'))
        .collect::<Vec<_>>()
        .join(" ");
    let compact = compact.trim_matches(|c: char| c == '#' || c == '*' || c == '-' || c.is_whitespace());
    if compact.is_empty() {
        return None;
    }
    let sentence = compact.split(['.', '!', '?']).next().unwrap_or(compact).trim();
    let mut title = sentence.chars().take(72).collect::<String>();
    if sentence.chars().count() > 72 {
        if let Some(index) = title.rfind(' ') {
            title.truncate(index);
        }
    }
    let title = title.trim_matches(|c: char| c == '"' || c == '\'' || c.is_whitespace()).trim();
    if title.len() < 3 { None } else { Some(title.to_string()) }
}

fn part_text(part: &Value) -> String {
    for key in ["text", "content", "output_text", "input_text"] {
        if let Some(text) = part.get(key).and_then(Value::as_str) {
            return text.to_string();
        }
    }
    String::new()
}

fn message_parts(content: &Value) -> Vec<(String, String, String)> {
    if let Some(text) = content.as_str() {
        return vec![("message".into(), String::new(), text.into())];
    }
    let mut out = Vec::new();
    if let Some(parts) = content.as_array() {
        for part in parts {
            let kind = part.get("type").and_then(Value::as_str).unwrap_or("text");
            match kind {
                "text" | "input_text" | "output_text" => {
                    let text = part_text(part);
                    if !text.is_empty() { out.push(("message".into(), String::new(), text)); }
                }
                "thinking" | "reasoning" => {
                    let text = part_text(part);
                    if !text.is_empty() { out.push(("thinking".into(), "Reasoning".into(), text)); }
                }
                "tool_use" | "tool-call" | "function_call" => {
                    let name = part.get("name").and_then(Value::as_str).unwrap_or("Tool call");
                    let body = part.get("input").or_else(|| part.get("arguments")).unwrap_or(part);
                    out.push(("tool_call".into(), name.into(), body.to_string()));
                }
                "tool_result" | "function_call_output" => {
                    out.push(("tool_result".into(), "Tool result".into(), part.to_string()));
                }
                _ => {}
            }
        }
    }
    out
}

pub fn sync_claude(store: &EventStore, profile: &Path) -> Result<usize, String> {
    let report = sync_claude_with_progress(store, profile, &mut |_| {})?;
    Ok(report.imported + report.updated)
}

fn sync_claude_with_progress(store: &EventStore, profile: &Path,
    progress: &mut impl FnMut(&SyncReport)) -> Result<SyncReport, String> {
    sync_claude_with_cancellation(store, profile, progress, &|| false)
}

fn sync_claude_with_cancellation(store: &EventStore, profile: &Path,
    progress: &mut impl FnMut(&SyncReport), cancelled: &dyn Fn() -> bool) -> Result<SyncReport, String> {
    let root = profile.join(".claude").join("projects");
    if !root.is_dir() { return Err(format!("Claude Code history not found: {}", root.display())); }
    let paths = newest_jsonl(&root, cancelled)?;
    let mut report = SyncReport { total: paths.len(), ..Default::default() };
    progress(&report);
    for (index, path) in paths.into_iter().enumerate() {
        if cancelled() { return Err(SYNC_CANCELLED.into()); }
        report.current = index;
        report.skipped = report.current.saturating_sub(report.imported + report.updated);
        progress(&report);
        let Some(stem) = path.file_stem().and_then(|v| v.to_str()) else { continue };
        let id = format!("claude:{stem}");
        let file = File::open(&path).map_err(|e| e.to_string())?;
        let mut title = String::new();
        let mut project_cwd: Option<String> = None;
        let mut rows: Vec<(String,String,String,String,String,Value)> = Vec::new();
        for line in BufReader::new(file).lines().map_while(Result::ok) {
            if cancelled() { return Err(SYNC_CANCELLED.into()); }
            let Ok(value) = serde_json::from_str::<Value>(&line) else { continue };
            let typ = value.get("type").and_then(Value::as_str).unwrap_or("");
            if project_cwd.is_none() {
                if let Some(cwd) = value.get("cwd").and_then(Value::as_str) {
                    project_cwd = Some(cwd.to_string());
                }
            }
            if matches!(typ, "custom-title" | "ai-title") {
                if let Some(raw) = value.get("customTitle").or_else(|| value.get("aiTitle")).and_then(Value::as_str) {
                    title = clean_title(raw).unwrap_or_else(|| raw.chars().take(72).collect());
                }
                continue;
            }
            if !matches!(typ, "user" | "assistant") { continue; }
            let message = value.get("message").unwrap_or(&value);
            let role = message.get("role").and_then(Value::as_str).unwrap_or(typ);
            let timestamp = value.get("timestamp").and_then(Value::as_str).unwrap_or("").to_string();
            for (kind, part_title, content) in message_parts(message.get("content").unwrap_or(&Value::Null)) {
                if role == "user" && is_internal_context(&content) {
                    continue;
                }
                if title.is_empty() && role == "user" && !content.is_empty() {
                    if let Some(clean) = clean_title(&content) {
                        title = clean;
                    }
                }
                let label = if part_title.is_empty() { if role == "user" { "User" } else { "Assistant" } } else { &part_title };
                rows.push((timestamp.clone(), kind, role.into(), label.into(), content, value.clone()));
            }
        }
        if cancelled() { return Err(SYNC_CANCELLED.into()); }
        if rows.is_empty() { continue; }
        let created = store.replace_imported_history(&id, "Claude Code", if title.is_empty() { "Claude Code session" } else { &title }, &rows)?;
        record_source_folder(store, &id, project_cwd.as_deref(), &mut report);
        if created { report.imported += 1; } else { report.updated += 1; }
    }
    store.reconcile_legacy_projects()?;
    report.current = report.total;
    report.skipped = report.total.saturating_sub(report.imported + report.updated);
    progress(&report);
    Ok(report)
}
pub fn sync_codex(store: &EventStore, profile: &Path) -> Result<usize, String> {
    let report = sync_codex_with_progress(store, profile, &mut |_| {})?;
    Ok(report.imported + report.updated)
}

fn sync_codex_with_progress(store: &EventStore, profile: &Path,
    progress: &mut impl FnMut(&SyncReport)) -> Result<SyncReport, String> {
    sync_codex_with_cancellation(store, profile, progress, &|| false)
}

fn sync_codex_with_cancellation(store: &EventStore, profile: &Path,
    progress: &mut impl FnMut(&SyncReport), cancelled: &dyn Fn() -> bool) -> Result<SyncReport, String> {
    let root = profile.join(".codex").join("sessions");
    if !root.is_dir() { return Err(format!("Codex history not found: {}", root.display())); }
    let paths = newest_jsonl(&root, cancelled)?;
    let mut report = SyncReport { total: paths.len(), ..Default::default() };
    progress(&report);
    for (index, path) in paths.into_iter().enumerate() {
        if cancelled() { return Err(SYNC_CANCELLED.into()); }
        report.current = index;
        report.skipped = report.current.saturating_sub(report.imported + report.updated);
        progress(&report);
        let Some(stem) = path.file_stem().and_then(|v| v.to_str()) else { continue };
        let id = format!("codex:{stem}");
        let file = File::open(&path).map_err(|e| e.to_string())?;
        let mut title = String::new();
        let mut project_cwd: Option<String> = None;
        let mut rows: Vec<(String,String,String,String,String,Value)> = Vec::new();
        for line in BufReader::new(file).lines().map_while(Result::ok) {
            if cancelled() { return Err(SYNC_CANCELLED.into()); }
            let Ok(value) = serde_json::from_str::<Value>(&line) else { continue };
            if value.get("type").and_then(Value::as_str) == Some("session_meta") {
                if project_cwd.is_none() {
                    if let Some(cwd) = value.pointer("/payload/cwd").and_then(Value::as_str) {
                        project_cwd = Some(cwd.to_string());
                    }
                }
                continue;
            }
            if value.get("type").and_then(Value::as_str) != Some("response_item") { continue; }
            let payload = value.get("payload").unwrap_or(&Value::Null);
            let timestamp = value.get("timestamp").and_then(Value::as_str).unwrap_or("").to_string();
            match payload.get("type").and_then(Value::as_str).unwrap_or("") {
                "message" => {
                    let role = payload.get("role").and_then(Value::as_str).unwrap_or("assistant");
                    for (kind, _, content) in message_parts(payload.get("content").unwrap_or(&Value::Null)) {
                        if role == "user" && is_internal_context(&content) {
                            continue;
                        }
                        if title.is_empty() && role == "user" && !content.is_empty() {
                            if let Some(clean) = clean_title(&content) {
                                title = clean;
                            }
                        }
                        let label = if role == "user" { "User" } else { "Assistant" };
                        rows.push((timestamp.clone(), kind, role.into(), label.into(), content, value.clone()));
                    }
                }
                "reasoning" => {
                    let text = payload.get("summary").and_then(Value::as_array)
                        .map(|parts| parts.iter().map(part_text).filter(|v| !v.is_empty()).collect::<Vec<_>>().join("\n"))
                        .unwrap_or_default();
                    if !text.is_empty() { rows.push((timestamp.clone(), "thinking".into(), "assistant".into(), "Reasoning".into(), text, value.clone())); }
                }
                "function_call" | "custom_tool_call" => {
                    let name = payload.get("name").and_then(Value::as_str).unwrap_or("Tool call");
                    rows.push((timestamp.clone(), "tool_call".into(), "assistant".into(), name.into(), payload.to_string(), value.clone()));
                }
                "function_call_output" | "custom_tool_call_output" => {
                    rows.push((timestamp.clone(), "tool_result".into(), "tool".into(), "Tool result".into(), payload.to_string(), value.clone()));
                }
                _ => {}
            }
        }
        if cancelled() { return Err(SYNC_CANCELLED.into()); }
        if rows.is_empty() { continue; }
        let created = store.replace_imported_history(&id, "Codex", if title.is_empty() { "Codex session" } else { &title }, &rows)?;
        record_source_folder(store, &id, project_cwd.as_deref(), &mut report);
        if created { report.imported += 1; } else { report.updated += 1; }
    }
    store.reconcile_legacy_projects()?;
    report.current = report.total;
    report.skipped = report.total.saturating_sub(report.imported + report.updated);
    progress(&report);
    Ok(report)
}

pub fn sync_with_progress(store: &EventStore, id: &str,
    mut progress: impl FnMut(&SyncReport)) -> Result<SyncReport, String> {
    sync_with_cancellation(store, id, &mut progress, &|| false)
}

pub fn sync_with_cancellation(store: &EventStore, id: &str,
    mut progress: impl FnMut(&SyncReport), cancelled: &dyn Fn() -> bool) -> Result<SyncReport, String> {
    if cancelled() { return Err(SYNC_CANCELLED.into()); }
    if matches!(id, "opencode" | "hermes") {
        return agent_connector_history::sync_with_cancellation(store, id, &mut progress, cancelled);
    }
    let profile = history_profile_root()?;
    match id {
        "claude-code" => sync_claude_with_cancellation(store, &profile, &mut progress, cancelled),
        "codex" => sync_codex_with_cancellation(store, &profile, &mut progress, cancelled),
        _ => Err(format!("History sync is not supported for {id}")),
    }
}
pub fn sync(store: &EventStore, id: &str) -> Result<String, String> {
    if matches!(id, "opencode" | "hermes") {
        let report = sync_with_cancellation(store, id, |_| {}, &|| false)?;
        return Ok(format!("Imported {} · Updated {} · Skipped {} · Failed {} · Linked {} folders · {} folders unavailable or unrecorded",
            report.imported, report.updated, report.skipped, report.failed, report.folders_found, report.folders_unresolved));
    }
    let profile = history_profile_root()?;
    let count = match id {
        "claude-code" => sync_claude(store, &profile)?,
        "codex" => sync_codex(store, &profile)?,
        _ => return Err(format!("History sync is not supported for {id}")),
    };
    store.log("info", "connector", &format!("Synced {count} {id} sessions"));
    Ok(format!("Imported {count} recent sessions"))
}

fn history_profile_root() -> Result<PathBuf, String> {
    #[cfg(debug_assertions)]
    if let Some(root) = std::env::var_os("OPENCORE_TEST_PROFILE_ROOT") {
        return Ok(PathBuf::from(root));
    }
    std::env::var_os("USERPROFILE").map(PathBuf::from)
        .ok_or_else(|| "USERPROFILE is unavailable".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::EventStore;
    use serde_json::json;

    #[test]
    fn imports_both_clients_into_one_folder_project_without_overwriting_manual_moves() {
        let root = std::env::temp_dir().join(format!("opencore-linked-history-{}", uuid::Uuid::new_v4()));
        let folder = root.join("workspace").join("app");
        let other = root.join("other").join("app");
        let claude_dir = root.join(".claude").join("projects").join("fixture");
        let codex_dir = root.join(".codex").join("sessions").join("2026");
        for path in [&folder, &other, &claude_dir, &codex_dir] { std::fs::create_dir_all(path).unwrap(); }
        let claude_lines = [
            json!({"type":"user","cwd":folder,"timestamp":"2026-09-22T00:00:00Z","message":{"role":"user","content":"Claude hello"}}),
            json!({"type":"assistant","timestamp":"2026-09-22T00:00:01Z","message":{"role":"assistant","content":"Claude response"}}),
        ];
        let codex_lines = [
            json!({"type":"session_meta","payload":{"cwd":folder}}),
            json!({"type":"response_item","timestamp":"2026-09-22T00:00:00Z","payload":{"type":"message","role":"user","content":"Codex hello"}}),
        ];
        std::fs::write(claude_dir.join("claude.jsonl"), claude_lines.iter().map(Value::to_string).collect::<Vec<_>>().join("\n")).unwrap();
        std::fs::write(codex_dir.join("codex.jsonl"), codex_lines.iter().map(Value::to_string).collect::<Vec<_>>().join("\n")).unwrap();
        let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
        let claude_report = sync_claude_with_progress(&store, &root, &mut |_| {}).unwrap();
        let codex_report = sync_codex_with_progress(&store, &root, &mut |_| {}).unwrap();
        assert_eq!((claude_report.folders_found, claude_report.folders_unresolved), (1, 0));
        assert_eq!((codex_report.folders_found, codex_report.folders_unresolved), (1, 0));
        let linked = store.list_projects().unwrap().into_iter().filter(|item| !item.needs_folder).collect::<Vec<_>>();
        assert_eq!(linked.len(), 1);
        assert_eq!(linked[0].conversation_count, 2);
        let manual = store.create_project("Other app", &other).unwrap();
        store.set_project_by_id("codex:codex", Some(&manual.id), crate::store::ProjectAssignment::Manual).unwrap();
        sync_codex(&store, &root).unwrap();
        assert_eq!(store.list_conversations(None).unwrap().into_iter().find(|item| item.id == "codex:codex").unwrap().project_id.as_deref(), Some(manual.id.as_str()));
        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn imports_claude_and_codex_transcripts() {
        let root = std::env::temp_dir().join(format!("opencore-history-{}", uuid::Uuid::new_v4()));
        let claude_dir = root.join(".claude").join("projects").join("demo");
        let codex_dir = root.join(".codex").join("sessions").join("2026").join("09").join("22");
        std::fs::create_dir_all(&claude_dir).unwrap();
        std::fs::create_dir_all(&codex_dir).unwrap();

        std::fs::write(
            claude_dir.join("claude-session.jsonl"),
            concat!(
                r#"{"type":"user","timestamp":"2026-09-22T00:00:00Z","message":{"role":"user","content":"hello claude"}}"#, "\n",
                r#"{"type":"assistant","timestamp":"2026-09-22T00:00:01Z","message":{"role":"assistant","content":[{"type":"text","text":"hello back"},{"type":"thinking","text":"reasoning"}]}}"#, "\n"
            ),
        ).unwrap();

        std::fs::write(
            codex_dir.join("rollout.jsonl"),
            concat!(
                r#"{"timestamp":"2026-09-22T00:00:00Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"hello codex"}]}}"#, "\n",
                r#"{"timestamp":"2026-09-22T00:00:01Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"hello back"}]}}"#, "\n"
            ),
        ).unwrap();

        let db = root.join("history.sqlite3");
        let store = EventStore::open(&db).unwrap();
        assert_eq!(sync_claude(&store, &root).unwrap(), 1);
        assert_eq!(sync_codex(&store, &root).unwrap(), 1);

        let conversations = store.list_conversations(None).unwrap();
        assert!(conversations.iter().any(|c| c.client == "Claude Code"));
        assert!(conversations.iter().any(|c| c.client == "Codex"));
        assert!(store.conversation("claude:claude-session").unwrap().iter().any(|e| e.kind == "thinking"));
        assert!(store.conversation("codex:rollout").unwrap().iter().any(|e| e.content.contains("hello back")));

        let custom = store.create_project("My research", &root).unwrap();
        store.set_project_by_id("codex:rollout", Some(&custom.id), crate::store::ProjectAssignment::Manual).unwrap();
        store.set_conversation_pinned("codex:rollout", true).unwrap();
        store.rename_conversation("codex:rollout", "My own title").unwrap();
        assert_eq!(sync_codex(&store, &root).unwrap(), 1);
        let restored = store.list_conversations(None).unwrap().into_iter()
            .find(|item| item.id == "codex:rollout").unwrap();
        assert_eq!(restored.title, "My own title");
        assert_eq!(restored.project, "My research");
        assert!(restored.pinned);
        assert_eq!(store.conversation("codex:rollout").unwrap().len(), 2);
        assert_eq!(store.conversation("codex:rollout").unwrap()[0].timestamp, "2026-09-22T00:00:00Z");
        store.ensure_conversation("codex:rollout", "OpenCore", "echo", "New conversation").unwrap();
        store.add_timeline("codex:rollout", "message", "user", "OpenCore", "You", "Continue in the app", &json!({})).unwrap();
        let mut progress = Vec::new();
        let report = sync_codex_with_progress(&store, &root, &mut |item| progress.push(item.clone())).unwrap();
        assert_eq!((report.current, report.total, report.imported, report.updated, report.skipped), (1, 1, 0, 1, 0));
        assert_eq!((report.folders_found, report.folders_unresolved), (0, 1));
        assert!(progress.iter().any(|item| item.current == 0 && item.total == 1));
        let continued = store.list_conversations(None).unwrap().into_iter()
            .find(|item| item.id == "codex:rollout").unwrap();
        assert_eq!(continued.client, "Codex");
        assert_eq!(continued.project, "My research");
        assert!(continued.pinned);
        assert_eq!(continued.title, "My own title");
        let entries = store.conversation("codex:rollout").unwrap();
        assert_eq!(entries.len(), 3);
        assert_eq!(entries[2].content, "Continue in the app");
        assert_eq!(entries[2].source, "OpenCore");

        drop(store);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn cancellation_during_transcript_read_does_not_save_a_partial_session() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let root = std::env::temp_dir().join(format!("opencore-history-cancel-{}", uuid::Uuid::new_v4()));
        let sessions = root.join(".codex").join("sessions");
        std::fs::create_dir_all(&sessions).unwrap();
        std::fs::write(sessions.join("session.jsonl"), concat!(
            r#"{"type":"response_item","payload":{"type":"message","role":"user","content":"first"}}"#, "\n",
            r#"{"type":"response_item","payload":{"type":"message","role":"assistant","content":"second"}}"#, "\n"
        )).unwrap();
        let store = EventStore::open(&root.join("history.sqlite3")).unwrap();
        let checks = AtomicUsize::new(0);
        let result = sync_codex_with_cancellation(&store, &root, &mut |_| {}, &|| {
            checks.fetch_add(1, Ordering::SeqCst) >= 5
        });
        assert_eq!(result.unwrap_err(), SYNC_CANCELLED);
        assert!(store.conversation("codex:session").unwrap().is_empty());
        drop(store);
        let _ = std::fs::remove_dir_all(root);
    }
}
