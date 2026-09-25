mod claude_harness;
mod artifacts;
mod chat_stream;
mod speech;
mod dev_tool;
mod archive_view;
mod browser_bridge;
mod child_guard;
mod compat;
mod computer_ops;
#[cfg(windows)]
mod desktop_capture;
mod connector_config;
mod gateway;
mod history;
mod models;
mod native_browser;
mod project_paths;
mod project_memory;
mod redaction;
mod reflex;
mod runtime;
mod store;
mod tooling;
mod vision;
#[cfg(windows)]
mod desktop_activity;
mod windows_control;

use crate::gateway::GatewayState;
use crate::models::{AppSnapshot, ApprovalMode, ChatSendRequest, ChatSendResult, ConnectorInput, ConnectorStatus, ExportResult, OperationRecord, ProjectSummary, StartProfileRequest, TimelineEntry};
use crate::redaction::redact_json;
use crate::runtime::RuntimeManager;
use crate::store::{EventStore, ProjectAssignment};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use tokio_util::sync::CancellationToken;
use tauri::{Emitter, Manager};

pub struct AppCore {
    speech: speech::SpeechManager,
    store: Arc<EventStore>,
    runtime: Arc<RuntimeManager>,
    active_chats: Mutex<HashMap<String, CancellationToken>>,
    pending_approvals: Mutex<HashMap<String, (String, tokio::sync::oneshot::Sender<bool>)>>,
    browser: Arc<browser_bridge::BrowserBridge>,
    reflex: Arc<reflex::ReflexManager>,
    vision: Arc<vision::VisionManager>,
}

struct LiveGenerationGuard { app: tauri::AppHandle, conversation: String, run: String }
impl Drop for LiveGenerationGuard {
    fn drop(&mut self) {
        let _ = self.app.emit("opencore-generation", json!({"conversationId":self.conversation,
            "runId":self.run,"done":true}));
    }
}

#[tauri::command]
fn browser_bridge_status(core: tauri::State<'_, Arc<AppCore>>, app: tauri::AppHandle) -> serde_json::Value {
    let mut status = core.browser.status();
    if let Ok(dir) = app.path().resource_dir() {
        status["extensionPath"] = json!(dir.join("chrome-extension").to_string_lossy().to_string());
    }
    status
}

#[tauri::command]
async fn browser_command(core: tauri::State<'_, Arc<AppCore>>, action: String, args: serde_json::Value) -> Result<serde_json::Value, String> {
    core.browser.command(&action, args).await
}

#[tauri::command]
async fn native_browser_command(app: tauri::AppHandle, action: String, args: serde_json::Value) -> Result<serde_json::Value, String> {
    tokio::time::timeout(std::time::Duration::from_secs(15), tokio::task::spawn_blocking(move || {
        native_browser::command(&app, &action, &args)
    })).await.map_err(|_| "OpenCore Browser did not respond within 15 seconds".to_string())?
        .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn desktop_command(app: tauri::AppHandle, action: String, args: serde_json::Value) -> Result<serde_json::Value, String> {
    desktop_action(&app, action, args).await
}


static KEEP_USER_WINDOW_IN_FRONT: AtomicBool = AtomicBool::new(false);

#[tauri::command]
fn set_computer_focus_mode(keep_user_window_in_front: bool) {
    KEEP_USER_WINDOW_IN_FRONT.store(keep_user_window_in_front, Ordering::SeqCst);
}

/// The selected window as Reflex Vision sees it. The compositor capture works while
/// other windows cover it, so looking never moves focus.
#[cfg(windows)]
async fn vision_frame(window_id: i64) -> Result<vision::Frame, String> {
    tokio::task::spawn_blocking(move || {
        let frame = desktop_capture::frame_for_window(window_id as isize)?;
        let origin = desktop_capture::frame_origin(window_id as isize);
        Ok(vision::Frame { data_url: frame.data_url, width: frame.width, height: frame.height, origin })
    }).await.map_err(|error| error.to_string())?
}

#[cfg(not(windows))]
async fn vision_frame(_window_id: i64) -> Result<vision::Frame, String> {
    Err("Reflex Vision needs Windows window capture".into())
}

/// Fast Reflex decisions from accessibility rows, or Reflex Vision looking at the window.
async fn reflex_action(app: &tauri::AppHandle, core: &Arc<AppCore>, action: &str, args: serde_json::Value) -> Result<serde_json::Value, String> {
    let window_id = args.get("windowId").and_then(|v| v.as_i64()).ok_or("windowId is required; use desktop_use action=list first")?;
    let goal = args.get("goal").and_then(|v| v.as_str()).map(str::trim).filter(|goal| !goal.is_empty());
    match action {
        "ground" | "ground_click" | "see" => {
            let goal = goal.ok_or(if action == "see" { "goal is required: the question to answer about the window" }
                                  else { "goal is required: describe one visible target" })?;
            #[cfg(windows)]
            let _activity = desktop_activity::begin(app, window_id, &args);
            let frame = vision_frame(window_id).await?;
            let mut result = if action == "see" { core.vision.ask(&frame, goal).await? }
                             else { core.vision.locate(&frame, goal).await? };
            result["windowId"] = json!(window_id);
            result["imageSize"] = json!({"width":frame.width,"height":frame.height});
            result["origin"] = json!({"x":frame.origin.0,"y":frame.origin.1});
            if action == "see" {
                result["dataUrl"] = json!(frame.data_url);
                result["nextAction"] = json!("For a named control, use reflex_use ground with the exact target; its result is window-relative. Do not invent coordinates from this text description.");
            }
            if action != "see" { result["coordinate_space"] = json!("window_relative"); }
            if action == "ground_click" && result.get("found").and_then(Value::as_bool) == Some(true) {
                let clicked = desktop_action(app, "click".into(),
                    json!({"windowId":window_id,"x":result["x"],"y":result["y"]})).await?;
                result["click"] = clicked;
                result["clicked"] = json!(true);
            }
            Ok(result)
        }
        "pick" => {
            core.reflex.ensure_running().await?;
            let goal = goal.ok_or("goal is required")?;
            let inspected = desktop_action(app, "inspect".into(), json!({"windowId":window_id})).await?;
            let elements = inspected.get("elements").and_then(|v| v.as_array()).cloned().unwrap_or_default();
            let mut picked = core.reflex.post("/desktop/pick", json!({"goal":goal,
                "title":inspected.get("title").cloned().unwrap_or(json!("")), "elements":elements}),
                std::time::Duration::from_secs(30)).await?;
            if let Some(element_id) = picked.get("elementId").and_then(|v| v.as_i64()) {
                if let Some((x, y)) = reflex::relative_center(&elements, element_id) {
                    picked["x"] = json!(x);
                    picked["y"] = json!(y);
                }
            }
            picked["windowId"] = json!(window_id);
            Ok(picked)
        }
        "play_snake" => {
            core.reflex.ensure_running().await?;
            let seconds = args.get("seconds").and_then(|v| v.as_f64()).unwrap_or(90.0).clamp(5.0, 600.0);
            #[cfg(windows)]
            let _activity = desktop_activity::begin(app, window_id, &args);
            child_guard::allow_foreground_handoff();
            let result = core.reflex.post("/snake/play", json!({"hwnd":window_id,"seconds":seconds}),
                std::time::Duration::from_secs(seconds as u64 + 60)).await;
            result
        }
        _ => Err("reflex_use action must be pick, ground, ground_click, see, or play_snake".into()),
    }
}

async fn desktop_action(app: &tauri::AppHandle, action: String, mut args: serde_json::Value) -> Result<serde_json::Value, String> {
    if KEEP_USER_WINDOW_IN_FRONT.load(Ordering::SeqCst) && matches!(action.as_str(), "move" | "click" | "drag" | "type" | "key" | "scroll" | "commit_text") {
        return Err("Keep my window in front is on. Use inspect with invoke or set_value for controls that support background automation, or switch to foreground control for pointer and keyboard actions".into());
    }
    if action == "interact" { args["allowForegroundFallback"] = json!(!KEEP_USER_WINDOW_IN_FRONT.load(Ordering::SeqCst)); }
    #[cfg(windows)]
    let _activity = desktop_activity::begin(app, args["windowId"].as_i64().unwrap_or(0), &args);
    #[cfg(not(windows))]
    let _ = app;
    let result = windows_control::command(action, args).await;
    // Keep brief pointer actions visible long enough to identify their target.
    tokio::time::sleep(std::time::Duration::from_millis(180)).await;
    result
}

struct PendingApprovalGuard<'a> {
    core: &'a AppCore,
    request_id: String,
}

impl Drop for PendingApprovalGuard<'_> {
    fn drop(&mut self) {
        if let Ok(mut pending) = self.core.pending_approvals.lock() { pending.remove(&self.request_id); }
    }
}

async fn ask_tool_approval(
    app: &tauri::AppHandle,
    core: &AppCore,
    conversation_id: &str,
    name: &str,
    arguments: &str,
    token: &CancellationToken,
) -> Result<bool, String> {
    let request_id = uuid::Uuid::new_v4().to_string();
    let (sender, receiver) = tokio::sync::oneshot::channel();
    core.pending_approvals.lock().map_err(|error| error.to_string())?
        .insert(request_id.clone(), (conversation_id.to_string(), sender));
    let _guard = PendingApprovalGuard { core, request_id: request_id.clone() };
    app.emit("opencore-tool-approval-request", json!({
        "requestId":request_id,"conversationId":conversation_id,"name":name,"arguments":arguments
    })).map_err(|error| error.to_string())?;
    let result = tokio::select! {
        _ = token.cancelled() => false,
        answer = tokio::time::timeout(std::time::Duration::from_secs(600), receiver) => {
            matches!(answer, Ok(Ok(true)))
        }
    };
    Ok(result)
}

fn echo_import_payload_unbounded(conversation_id: &str, rows: &[TimelineEntry]) -> serde_json::Value {
    let messages = rows.iter().map(|row| {
        let stable_id = row.metadata.get("opencore_source_event_id")
            .and_then(|value| value.as_str())
            .map(str::to_string)
            .unwrap_or_else(|| format!("legacy:{}", row.id));
        let assets = if row.source == "OpenCore" && row.role == "user" {
            row.metadata.get("files").and_then(|value| value.as_array()).into_iter()
                .flatten().filter_map(|file| {
                    let path = file.get("path")?.as_str()?;
                    let name = file.get("name")?.as_str()?;
                    let mime = match std::path::Path::new(name).extension()?.to_str()?.to_ascii_lowercase().as_str() {
                        "png" => "image/png", "jpg" | "jpeg" => "image/jpeg",
                        "gif" => "image/gif", "webp" => "image/webp", _ => return None,
                    };
                    let size = std::fs::metadata(path).ok()?.len();
                    if size > 4 * 1024 * 1024 { return None; }
                    let bytes = std::fs::read(path).ok()?;
                    use base64::Engine;
                    Some(json!({"name":name,"data_url":format!("data:{mime};base64,{}", base64::engine::general_purpose::STANDARD.encode(bytes))}))
                }).collect::<Vec<_>>()
        } else { Vec::new() };
        json!({"role":row.role,"content":row.content,"source_event_id":stable_id,
            "timestamp":row.timestamp,"kind":row.kind,"source":row.source,"title":row.title,
            "metadata":row.metadata,"assets":assets})
    }).collect::<Vec<_>>();
    json!({"conversation_id":conversation_id,"messages":messages})
}

fn echo_import_payload(conversation_id: &str, rows: &[TimelineEntry]) -> Result<serde_json::Value, String> {
    let payload = echo_import_payload_unbounded(conversation_id, rows);
    if serde_json::to_vec(&payload).map_err(|error| error.to_string())?.len() > 16 * 1024 * 1024 {
        return Err("An imported turn exceeds ECHO's 16 MiB batch limit; its source remains saved in Conversations".into());
    }
    Ok(payload)
}

async fn hydrate_imported_history(
    core: &AppCore,
    client: &reqwest::Client,
    conversation_id: &str,
    progress_id: i64,
    total: u64,
) -> Result<(), String> {
    let url = format!("{}/echo/import", core.runtime.upstream_url());
    let mut after_id = 0_i64;
    let mut current = 0_u64;
    let mut imported = 0_u64;
    let mut skipped = 0_u64;
    loop {
        let batch = core.store.imported_messages_batch(conversation_id, after_id, 32)?;
        if batch.is_empty() { break; }
        after_id = batch.last().map(|entry| entry.id).unwrap_or(after_id);
        let payload = echo_import_payload(conversation_id, &batch)?;
        let response = client.post(&url).json(&payload).send().await
            .map_err(|error| format!("Could not transfer imported history to ECHO: {error}"))?;
        let status = response.status();
        let raw = response.text().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            let detail = serde_json::from_str::<serde_json::Value>(&raw).ok()
                .and_then(|value| value.pointer("/error/message").and_then(|item| item.as_str()).map(str::to_string))
                .unwrap_or_else(|| raw.chars().take(300).collect());
            return Err(format!("ECHO could not index imported history (HTTP {status}): {detail}"));
        }
        let result: serde_json::Value = serde_json::from_str(&raw)
            .map_err(|error| format!("ECHO returned invalid import progress: {error}"))?;
        imported += result.get("imported").and_then(|value| value.as_u64()).unwrap_or(0);
        skipped += result.get("skipped").and_then(|value| value.as_u64()).unwrap_or(0);
        current += batch.len() as u64;
        core.store.update_timeline(progress_id, &format!("Indexed {current}/{total} source events"),
            &json!({"status":"indexing","current":current,"total":total,"imported":imported,"skipped":skipped}))?;
    }
    core.store.update_timeline(progress_id, &format!("ECHO ready · {total} source events available"),
        &json!({"status":"ready","current":total,"total":total,"imported":imported,"skipped":skipped}))?;
    Ok(())
}

async fn sync_chat_activity(core: &AppCore, conversation_id: &str) -> Result<(), String> {
    let client = reqwest::Client::builder().no_proxy().timeout(std::time::Duration::from_secs(20))
        .build().map_err(|error| error.to_string())?;
    let url = format!("{}/echo/import", core.runtime.upstream_url());
    let cursor_key = format!("echo_sync_cursor_v1_{conversation_id}");
    let mut cursor = core.store.get_setting(&cursor_key)?
        .and_then(|value| value.parse::<i64>().ok())
        .filter(|value| *value >= 0).unwrap_or(0);
    loop {
        let batch = core.store.archive_events_batch(conversation_id, cursor, 8)?;
        if batch.is_empty() { break; }
        cursor = batch.last().map(|item| item.id).unwrap_or(cursor);
        let payload = echo_import_payload(conversation_id, &batch)?;
        let response = client.post(&url).json(&payload).send().await.map_err(|error| error.to_string())?;
        if !response.status().is_success() {
            return Err(format!("ECHO activity import failed (HTTP {})", response.status()));
        }
        core.store.set_setting(&cursor_key, &cursor.to_string())?;
    }
    Ok(())
}

#[cfg(test)]
mod echo_import_tests {
    use super::*;

    #[test]
    fn import_payload_preserves_each_source_event_and_rejects_oversized_batches() {
        let row = TimelineEntry {
            id: 7, conversation_id: "codex:one".into(), timestamp: "2026-09-22T00:00:00Z".into(),
            kind: "message".into(), role: "user".into(), source: "Codex".into(),
            title: "You".into(), content: "hi".into(),
            metadata: json!({"opencore_source_event_id":"stable-7"}),
        };
        let payload = echo_import_payload("codex:one", &[row.clone()]).unwrap();
        assert_eq!(payload["messages"][0]["source_event_id"], "stable-7");
        assert_eq!(payload["messages"][0]["content"], "hi");
        let mut huge = row;
        huge.content = "a".repeat(16 * 1024 * 1024);
        assert!(echo_import_payload("codex:one", &[huge]).unwrap_err().contains("16 MiB"));
    }
}

#[tauri::command]
async fn get_snapshot(core: tauri::State<'_, Arc<AppCore>>) -> Result<AppSnapshot, String> {
    let core = core.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        Ok(AppSnapshot {
            runtime: core.runtime.snapshot(),
            telemetry: core.runtime.telemetry(),
            conversations: core.store.list_conversations(None)?,
            projects: core.store.list_projects()?,
            logs: core.store.logs(200)?,
            connectors: core.runtime.connectors(),
            active_conversation_ids: core.active_chats.lock().map_err(|error| error.to_string())?.keys().cloned().collect(),
        })
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn list_conversations(
    core: tauri::State<'_, Arc<AppCore>>,
    query: Option<String>,
) -> Result<Vec<models::ConversationSummary>, String> {
    core.store.list_conversations(query.as_deref())
}

#[tauri::command]
fn get_conversation(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
) -> Result<Vec<TimelineEntry>, String> {
    core.store.conversation_activity(&id)
}

#[tauri::command]
async fn start_profile(
    core: tauri::State<'_, Arc<AppCore>>,
    request: StartProfileRequest,
) -> Result<models::RuntimeSnapshot, String> {
    let runtime = core.runtime.clone();
    tauri::async_runtime::spawn_blocking(move || runtime.start(&request.profile, request.attach_url))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn stop_runtime(core: tauri::State<'_, Arc<AppCore>>) -> Result<(), String> {
    if let Ok(active) = core.active_chats.lock() {
        for token in active.values() { token.cancel(); }
    }
    let runtime = core.runtime.clone();
    runtime.request_stop();
    tauri::async_runtime::spawn_blocking(move || runtime.stop())
        .await
        .map_err(|e| e.to_string())?
}

struct ActiveChatGuard { core: Arc<AppCore>, id: String, app: tauri::AppHandle }

impl Drop for ActiveChatGuard {
    fn drop(&mut self) {
        if let Ok(mut active) = self.core.active_chats.lock() { active.remove(&self.id); }
        #[cfg(windows)]
        desktop_activity::clear(&self.app);
    }
}

#[tauri::command]
async fn restart_runtime(
    core: tauri::State<'_, Arc<AppCore>>,
) -> Result<models::RuntimeSnapshot, String> {
    let runtime = core.runtime.clone();
    let profile = runtime.profile();
    if profile == "stopped" {
        return Err("No profile is selected".into());
    }
    tauri::async_runtime::spawn_blocking(move || runtime.start(&profile, None))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
fn rename_conversation(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
    title: String,
) -> Result<(), String> {
    core.store.rename_conversation(&id, &title)
}

#[tauri::command]
fn delete_conversation(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
) -> Result<(), String> {
    core.store.delete_conversation(&id)
}

#[tauri::command]
fn set_conversation_pinned(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
    pinned: bool,
) -> Result<(), String> {
    core.store.set_conversation_pinned(&id, pinned)
}

#[tauri::command]
fn move_conversation_to_project(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
    project_id: Option<String>,
) -> Result<(), String> {
    let source = core.store.list_conversations(None)?.into_iter().find(|item| item.id == id)
        .ok_or("Conversation no longer exists")?;
    if imported_client(&source.client) { return Err("Imported Claude Code and Codex projects follow their source folders and cannot be moved".into()); }
    core.store.set_project_by_id(&id, project_id.as_deref(), ProjectAssignment::Manual)
}

fn imported_client(client: &str) -> bool {
    let client = client.to_ascii_lowercase();
    client.contains("claude") || client.contains("codex")
}

fn ensure_project_editable(core: &AppCore, id: &str) -> Result<(), String> {
    if core.store.list_conversations(None)?.iter().any(|item| item.project_id.as_deref() == Some(id) && imported_client(&item.client)) {
        return Err("This project comes from Claude Code or Codex and follows its source folder".into());
    }
    Ok(())
}

#[tauri::command]
fn create_project(
    core: tauri::State<'_, Arc<AppCore>>,
    name: String,
    folder_path: String,
) -> Result<ProjectSummary, String> {
    core.store.create_project(&name, Path::new(&folder_path))
}

#[tauri::command]
fn change_project_folder(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
    folder_path: String,
) -> Result<ProjectSummary, String> {
    ensure_project_editable(&core, &id)?;
    core.store.change_project_folder(&id, Path::new(&folder_path))
}

#[tauri::command]
fn rename_project(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
    name: String,
) -> Result<ProjectSummary, String> {
    ensure_project_editable(&core, &id)?;
    core.store.rename_project(&id, &name)
}

#[tauri::command]
fn delete_project(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
) -> Result<u64, String> {
    ensure_project_editable(&core, &id)?;
    core.store.delete_project(&id)
}

#[tauri::command]
fn export_conversation(
    app: tauri::AppHandle,
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
    format: String,
) -> Result<ExportResult, String> {
    let entries = core.store.conversation(&id)?;
    let export_root = app
        .path()
        .document_dir()
        .map_err(|e| e.to_string())?
        .join("OpenCore Exports");
    std::fs::create_dir_all(&export_root).map_err(|e| e.to_string())?;
    let safe_id: String = id
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_'))
        .take(64)
        .collect();
    let (extension, bytes) = if format == "markdown" {
        let mut text = format!("# OpenCore conversation {id}\n\n");
        for entry in &entries {
            text.push_str(&format!(
                "## {} · {} · {}\n\n{}\n\n",
                entry.timestamp, entry.source, entry.title, entry.content
            ));
        }
        ("md", text.into_bytes())
    } else {
        (
            "json",
            serde_json::to_vec_pretty(&json!({"conversation_id":id,"entries":entries}))
                .map_err(|e| e.to_string())?,
        )
    };
    let path = export_root.join(format!("{}-{}.{}", safe_id, chrono::Utc::now().format("%Y%m%d-%H%M%S"), extension));
    std::fs::write(&path, bytes).map_err(|e| e.to_string())?;
    core.store.log("info", "export", &format!("Exported conversation to {}", path.display()));
    Ok(ExportResult {
        path: path.display().to_string(),
    })
}

#[tauri::command]
fn archive_path(core: tauri::State<'_, Arc<AppCore>>) -> String {
    core.runtime.snapshot().archive_path
}

#[tauri::command]
fn open_local_path(path: String) -> Result<(), String> {
    let target = PathBuf::from(path.trim());
    if !target.exists() {
        return Err(format!("This path does not exist: {}", target.display()));
    }
    #[cfg(windows)]
    {
        let mut explorer = Command::new("explorer.exe");
        if target.is_file() {
            explorer.arg("/select,").arg(&target);
        } else {
            explorer.arg(&target);
        }
        explorer.spawn().map_err(|error| format!("Could not open Windows Explorer for {}: {error}", target.display()))?;
        Ok(())
    }
    #[cfg(not(windows))]
    {
        Err(format!("Windows Explorer is unavailable on this system: {}", target.display()))
    }
}

#[tauri::command]
fn save_connector(
    core: tauri::State<'_, Arc<AppCore>>,
    input: ConnectorInput,
) -> Result<ConnectorStatus, String> {
    core.store.upsert_connector(&input)
}

#[tauri::command]
fn delete_connector(core: tauri::State<'_, Arc<AppCore>>, id: String) -> Result<(), String> {
    core.store.delete_connector(&id)
}


#[tauri::command]
async fn sync_local_history(core: tauri::State<'_, Arc<AppCore>>, id: String) -> Result<String, String> {
    let store = core.store.clone();
    tauri::async_runtime::spawn_blocking(move || history::sync(&store, &id))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
fn list_operations(core: tauri::State<'_, Arc<AppCore>>) -> Result<Vec<OperationRecord>, String> {
    core.store.list_operations()
}

#[tauri::command]
fn start_history_sync(core: tauri::State<'_, Arc<AppCore>>, id: String) -> Result<OperationRecord, String> {
    if !matches!(id.as_str(), "claude-code" | "codex") {
        return Err(format!("History sync is not supported for {id}"));
    }
    let operation = core.store.start_operation("history_sync", &id)?;
    let operation_id = operation.id.clone();
    let store = core.store.clone();
    let runtime = core.runtime.clone();
    tauri::async_runtime::spawn_blocking(move || run_history_sync(store, runtime, id, operation_id));
    Ok(operation)
}

fn index_imported_history_offline(store: &EventStore, runtime: &RuntimeManager,
                                  operation_id: Option<&str>) -> Result<(u64, u64, u64), String> {
    let ids = store.archive_conversation_ids()?;
    if ids.is_empty() { return Ok((0, 0, 0)); }
    let python = runtime.python_path().ok_or("Python runtime not found for ECHO import")?;
    let script = runtime.echo_import_script_path();
    if !script.is_file() { return Err(format!("ECHO import helper is missing: {}", script.display())); }
    let archive_root = runtime.snapshot().archive_path;
    let mut command = Command::new(python);
    #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x0800_0000); }
    let mut child = command.arg(script).arg(archive_root).stdin(Stdio::piped())
        .stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().map_err(|error| error.to_string())?;
    let write_result = (|| -> Result<(), String> {
        let mut stdin = child.stdin.take().ok_or("ECHO importer stdin unavailable")?;
        for (index, conversation_id) in ids.iter().enumerate() {
            let mut after_id = 0;
            loop {
                let batch = store.archive_events_batch(conversation_id, after_id, 8)?;
                if batch.is_empty() { break; }
                after_id = batch.last().map(|entry| entry.id).unwrap_or(after_id);
                let payload = echo_import_payload_unbounded(conversation_id, &batch);
                serde_json::to_writer(&mut stdin, &payload)
                    .map_err(|error| error.to_string())?;
                stdin.write_all(b"\n").map_err(|error| error.to_string())?;
            }
            if let Some(operation_id) = operation_id {
                store.update_operation(operation_id, "Indexing exact history in ECHO",
                    (index + 1) as u64, ids.len() as u64, 0, 0, 0)?;
            }
        }
        Ok(())
    })();
    if let Err(error) = write_result {
        let output = child.wait_with_output().map_err(|wait_error| wait_error.to_string())?;
        let detail = String::from_utf8_lossy(&output.stderr);
        return Err(format!("ECHO import stopped while receiving history: {error}. {}",
            detail.chars().take(600).collect::<String>()));
    }
    let output = child.wait_with_output().map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(format!("ECHO import failed: {}", String::from_utf8_lossy(&output.stderr).chars().take(600).collect::<String>()));
    }
    let result: serde_json::Value = serde_json::from_slice(&output.stdout).map_err(|error| error.to_string())?;
    if let Some(errors) = result["errors"].as_array() {
        for error in errors.iter().filter_map(serde_json::Value::as_str) {
            store.log("warn", "echo-import", error);
        }
    }
    Ok((result["imported"].as_u64().unwrap_or(0), result["skipped"].as_u64().unwrap_or(0), result["failed"].as_u64().unwrap_or(0)))
}

fn run_history_sync(store: Arc<EventStore>, runtime: Arc<RuntimeManager>, id: String, operation_id: String) {
    let _ = store.update_operation(&operation_id, "Scanning transcript folders", 0, 0, 0, 0, 0);
    let mut latest = history::SyncReport::default();
    let result = history::sync_with_progress(&store, &id, |progress| {
        latest = progress.clone();
        if progress.total <= 100 || progress.current == 0 || progress.current == progress.total || progress.current % 10 == 0 {
            let _ = store.update_operation(&operation_id, "Importing transcripts",
                progress.current as u64, progress.total as u64,
                progress.imported as u64, progress.updated as u64, progress.skipped as u64);
        }
    });
    match result {
        Ok(report) => {
            let echo_result = index_imported_history_offline(&store, &runtime, Some(&operation_id));
            let echo_note = match &echo_result {
                Ok((imported, skipped, failed)) => format!(" · ECHO indexed {imported}, already present {skipped}, invalid records {failed}"),
                Err(error) => format!(" · ECHO indexing failed: {error}"),
            };
            let summary = format!("Imported {} · Updated {} · Skipped {} · Source folders found {} · Unresolved {}{}",
                report.imported, report.updated, report.skipped, report.folders_found, report.folders_unresolved, echo_note);
            let _ = store.finish_operation(&operation_id, &summary, echo_result.err().as_deref(),
                report.current as u64, report.total as u64,
                report.imported as u64, report.updated as u64, report.skipped as u64);
            if let Err(error) = store.set_setting(&format!("folder_project_backfill_v1_{id}"), "complete") {
                store.log("warn", "history", &format!("Could not mark {id} folder backfill complete: {error}"));
            }
            store.log("info", "connector", &format!("{id} history sync: {summary}"));
        }
        Err(error) => {
            let _ = store.finish_operation(&operation_id, "History sync failed", Some(&error),
                latest.current as u64, latest.total as u64,
                latest.imported as u64, latest.updated as u64, latest.skipped as u64);
            store.log("error", "connector", &format!("{id} history sync failed: {error}"));
        }
    }
}

#[tauri::command]
async fn index_echo_history(core: tauri::State<'_, Arc<AppCore>>) -> Result<String, String> {
    let store = core.store.clone();
    let runtime = core.runtime.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let (imported, skipped, failed) = index_imported_history_offline(&store, &runtime, None)?;
        Ok(format!("ECHO indexed {imported} source events · {skipped} already present · {failed} invalid records"))
    }).await.map_err(|error| error.to_string())?
}


#[tauri::command]
async fn configure_agent_connector(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
) -> Result<String, String> {
    let store = core.store.clone();
    let runtime = core.runtime.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let result = match id.as_str() {
            "claude-code" => connector_config::configure_claude_code(),
            "codex" => connector_config::configure_codex(),
            _ => Err(format!("Unsupported agent connector: {id}")),
        }?;
        store.log("info", "connector", &result);
        runtime.invalidate_connectors_cache();
        Ok(result)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn test_connector(endpoint: String) -> Result<String, String> {
    let endpoint = endpoint.trim().trim_end_matches('/').to_string();
    if !(endpoint.starts_with("http://") || endpoint.starts_with("https://")) {
        return Err("Endpoint must begin with http:// or https://".into());
    }
    let url = if endpoint.ends_with("/v1") {
        format!("{endpoint}/models")
    } else {
        format!("{endpoint}/v1/models")
    };
    let response = reqwest::Client::builder()
        .no_proxy()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .map_err(|e| e.to_string())?
        .get(url)
        .send()
        .await
        .map_err(|e| format!("Connection failed: {e}"))?;
    if !response.status().is_success() {
        return Err(format!("Provider returned HTTP {}", response.status()));
    }
    Ok("Connected successfully".into())
}

#[tauri::command]
async fn configure_unsloth(
    app: tauri::AppHandle,
    core: tauri::State<'_, Arc<AppCore>>,
) -> Result<String, String> {
    let profile = std::env::var_os("USERPROFILE").map(PathBuf::from).ok_or("USERPROFILE is unavailable")?;
    let python = profile.join(r".unsloth\studio\unsloth_studio\Scripts\python.exe");
    if !python.is_file() {
        return Err(format!("Unsloth Python was not found: {}", python.display()));
    }
    let development_script = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(r"resources\configure_unsloth.py");
    let bundled_script = app
        .path()
        .resource_dir()
        .map_err(|e| e.to_string())?
        .join(r"resources\configure_unsloth.py");
    let script = if development_script.is_file() { development_script } else { bundled_script };
    if !script.is_file() {
        return Err(format!("Unsloth connector helper is missing: {}", script.display()));
    }
    let runtime_path = core.runtime.snapshot().model_path;
    let runtime_dir = PathBuf::from(runtime_path)
        .parent()
        .unwrap_or(core.runtime.install_root())
        .join("runtime");
    let output = tauri::async_runtime::spawn_blocking(move || {
        let mut command = Command::new(python);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
        command
            .arg(script)
            .arg(runtime_dir)
            .arg("http://127.0.0.1:8812/v1")
            .output()
            .map_err(|e| e.to_string())
    })
    .await
    .map_err(|e| e.to_string())??;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    let message = String::from_utf8_lossy(&output.stdout).trim().to_string();
    core.store.log("info", "connector", "Unsloth OpenCore provider configured");
    Ok(message)
}

fn collect_archive_files(root: &Path, out: &mut Vec<PathBuf>, max: usize) {
    if out.len() >= max { return; }
    let Ok(entries) = std::fs::read_dir(root) else { return };
    for entry in entries.flatten() {
        if out.len() >= max { break; }
        let path = entry.path();
        if path.is_dir() {
            collect_archive_files(&path, out, max);
        } else {
            out.push(path);
        }
    }
}

#[tauri::command]
async fn search_archive(
    core: tauri::State<'_, Arc<AppCore>>,
    query: String,
    limit: Option<usize>,
    conversation_ids: Option<Vec<String>>,
) -> Result<Vec<archive_view::ArchiveHit>, String> {
    let root = PathBuf::from(core.runtime.snapshot().archive_path);
    let limit = limit.unwrap_or(50).clamp(1, 200);
    tauri::async_runtime::spawn_blocking(move || archive_view::search(&root, &query, limit, &conversation_ids.unwrap_or_default()))
        .await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn archive_overview(core: tauri::State<'_, Arc<AppCore>>) -> Result<archive_view::ArchiveOverview, String> {
    let root = PathBuf::from(core.runtime.snapshot().archive_path);
    let started = std::time::Instant::now();
    core.store.log("info", "archive", &format!("Reading overview from {}", root.display()));
    let result = tauri::async_runtime::spawn_blocking(move || archive_view::overview(&root)).await.map_err(|e| e.to_string())?;
    match &result {
        Ok(overview) => core.store.log("info", "archive", &format!("Overview ready: {} conversations, {} pages in {} ms", overview.conversations.len(), overview.pages, started.elapsed().as_millis())),
        Err(error) => core.store.log("error", "archive", &format!("Overview failed: {error}")),
    }
    result
}

#[tauri::command]
async fn echo_working_set(core: tauri::State<'_, Arc<AppCore>>, conversation_id: String) -> Result<Value, String> {
    if let Some(value) = core.store.get_setting(&format!("claude_context:{conversation_id}"))? {
        let mut context: Value = serde_json::from_str(&value).map_err(|e| e.to_string())?;
        context["windowTokens"] = json!(core.runtime.snapshot().context_size.max(1));
        return Ok(context);
    }
    let client = reqwest::Client::builder().no_proxy().timeout(std::time::Duration::from_secs(4)).build().map_err(|e| e.to_string())?;
    let response = client.get(format!("http://127.0.0.1:{}/echo/context", core.runtime.snapshot().echo_port))
        .query(&[("conversation", conversation_id)]).send().await.map_err(|e| e.to_string())?;
    response.error_for_status().map_err(|e| e.to_string())?.json().await.map_err(|e| e.to_string())
}

#[tauri::command]
async fn read_archive_page(core: tauri::State<'_, Arc<AppCore>>, archive_file: String, page_id: String) -> Result<String, String> {
    let root = PathBuf::from(core.runtime.snapshot().archive_path);
    tauri::async_runtime::spawn_blocking(move || archive_view::page(&root, &archive_file, &page_id)).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn list_archive_pages(core: tauri::State<'_, Arc<AppCore>>, conversation_id: String, offset: usize, limit: usize) -> Result<Vec<archive_view::ArchivePageRef>, String> {
    let root = PathBuf::from(core.runtime.snapshot().archive_path);
    tauri::async_runtime::spawn_blocking(move || archive_view::pages(&root, &conversation_id, offset, limit))
        .await.map_err(|error| error.to_string())?
}

#[tauri::command]
async fn list_archive_events(core: tauri::State<'_, Arc<AppCore>>, conversation_id: String, offset: usize, limit: usize) -> Result<Vec<archive_view::ArchiveEvent>, String> {
    let root = PathBuf::from(core.runtime.snapshot().archive_path);
    tauri::async_runtime::spawn_blocking(move || archive_view::events(&root, &conversation_id, offset, limit))
        .await.map_err(|error| error.to_string())?
}

#[tauri::command]
async fn read_archive_event(core: tauri::State<'_, Arc<AppCore>>, event_id: String) -> Result<archive_view::ArchiveEvent, String> {
    let root = PathBuf::from(core.runtime.snapshot().archive_path);
    tauri::async_runtime::spawn_blocking(move || archive_view::event(&root, &event_id))
        .await.map_err(|error| error.to_string())?
}

#[tauri::command]
async fn read_archive_asset(core: tauri::State<'_, Arc<AppCore>>, asset_id: String) -> Result<String, String> {
    let root = PathBuf::from(core.runtime.snapshot().archive_path);
    tauri::async_runtime::spawn_blocking(move || archive_view::asset(&root, &asset_id))
        .await.map_err(|error| error.to_string())?
}

#[tauri::command]
async fn echo_memory_action(
    core: tauri::State<'_, Arc<AppCore>>,
    action: String,
    conversation_id: String,
) -> Result<String, String> {
    if !matches!(action.as_str(), "trim" | "compact") {
        return Err("action must be trim or compact".into());
    }
    if conversation_id.trim().is_empty() {
        return Err("Select a conversation first".into());
    }
    let snapshot = core.runtime.snapshot();
    if !snapshot.profile.contains("echo") || snapshot.status != "running" {
        return Err("Start an ECHO profile first".into());
    }
    let command = if action == "trim" { format!("/trim {}", snapshot.context_size.max(1)) } else { "/summarize".into() };
    let body = json!({
        "model": "opencore-echo",
        "conversation_id": conversation_id,
        "messages": [{"role":"user","content":command}],
        "max_tokens": 512,
        "temperature": 0
    });
    let url = format!("http://127.0.0.1:{}/v1/chat/completions", snapshot.echo_port);
    let response = reqwest::Client::new().post(url).json(&body).send().await.map_err(|e| e.to_string())?;
    let status = response.status();
    let value: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(value.to_string());
    }
    Ok(value.pointer("/choices/0/message/content").and_then(|v| v.as_str()).unwrap_or("Done").to_string())
}

#[tauri::command]
fn clear_logs(core: tauri::State<'_, Arc<AppCore>>) -> Result<(), String> {
    core.store.clear_logs()
}

#[tauri::command]
async fn verify_model(core: tauri::State<'_, Arc<AppCore>>) -> Result<String, String> {
    let path = PathBuf::from(core.runtime.snapshot().model_path);
    tauri::async_runtime::spawn_blocking(move || {
        let meta = std::fs::metadata(&path).map_err(|e| e.to_string())?;
        let mut file = File::open(&path).map_err(|e| e.to_string())?;
        let mut hasher = Sha256::new();
        let mut buffer = vec![0u8; 8 * 1024 * 1024];
        loop {
            let read = file.read(&mut buffer).map_err(|e| e.to_string())?;
            if read == 0 { break; }
            hasher.update(&buffer[..read]);
        }
        Ok(format!("{} bytes · SHA-256 {:x}", meta.len(), hasher.finalize()))
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn export_archive_index(
    app: tauri::AppHandle,
    core: tauri::State<'_, Arc<AppCore>>,
) -> Result<String, String> {
    let root = PathBuf::from(core.runtime.snapshot().archive_path);
    let mut files = Vec::new();
    collect_archive_files(&root, &mut files, 20000);
    let rows: Vec<_> = files.into_iter().filter_map(|path| {
        let meta = std::fs::metadata(&path).ok()?;
        Some(json!({"path": path.display().to_string(), "bytes": meta.len()}))
    }).collect();
    let export_root = app.path().document_dir().map_err(|e| e.to_string())?.join("OpenCore Exports");
    std::fs::create_dir_all(&export_root).map_err(|e| e.to_string())?;
    let path = export_root.join(format!("echo-index-{}.json", chrono::Utc::now().format("%Y%m%d-%H%M%S")));
    std::fs::write(&path, serde_json::to_vec_pretty(&rows).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    Ok(path.display().to_string())
}

#[tauri::command]
fn export_diagnostics(
    app: tauri::AppHandle,
    core: tauri::State<'_, Arc<AppCore>>,
) -> Result<String, String> {
    let payload = json!({
        "runtime": core.runtime.snapshot(),
        "telemetry": core.runtime.telemetry(),
        "connectors": core.runtime.connectors(),
        "logs": core.store.logs(1000)?,
        "conversation_count": core.store.list_conversations(None)?.len()
    });
    let export_root = app.path().document_dir().map_err(|e| e.to_string())?.join("OpenCore Exports");
    std::fs::create_dir_all(&export_root).map_err(|e| e.to_string())?;
    let path = export_root.join(format!("diagnostics-{}.json", chrono::Utc::now().format("%Y%m%d-%H%M%S")));
    std::fs::write(&path, serde_json::to_vec_pretty(&payload).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    Ok(path.display().to_string())
}

#[tauri::command]
async fn health_check(core: tauri::State<'_, Arc<AppCore>>) -> Result<String, String> {
    let snapshot = core.runtime.snapshot();
    let model_ok = PathBuf::from(&snapshot.model_path).is_file();
    let gateway = reqwest::Client::new()
        .get(format!("http://127.0.0.1:{}/health", snapshot.gateway_port))
        .timeout(std::time::Duration::from_secs(2))
        .send().await.map(|r| r.status().is_success()).unwrap_or(false);
    Ok(format!(
        "Gateway: {} · Runtime: {} · Model file: {} · Profile: {}",
        if gateway { "healthy" } else { "unreachable" },
        snapshot.status,
        if model_ok { "present" } else { "missing" },
        snapshot.profile
    ))
}


fn fallback_chat_title(seed: &str) -> String {
    let words = seed
        .split_whitespace()
        .filter(|word| !word.starts_with('<') && !word.starts_with('#'))
        .take(7)
        .collect::<Vec<_>>()
        .join(" ");
    let title = words.trim_matches(|c: char| c == '"' || c == '\'' || c.is_whitespace());
    if title.is_empty() { "New OpenCore chat".into() } else { title.chars().take(72).collect() }
}

async fn generate_chat_title(runtime: Arc<RuntimeManager>, seed: String) -> String {
    let fallback = fallback_chat_title(&seed);
    let body = json!({
        "model": "opencore",
        "messages": [
            {
                "role":"system",
                "content":"Create a short, specific conversation title in 3 to 7 words describing the requested task. This is a request, not a result: never claim it succeeded, passed, or completed. Return only the title, with no quotes, prefix, markdown, or punctuation at the end."
            },
            {
                "role":"user",
                "content": seed.chars().take(1200).collect::<String>()
            }
        ],
        "max_tokens": 24,
        "temperature": 0.1,
        "stream": false
    });
    let url = format!("{}/v1/chat/completions", runtime.direct_backend_url());
    let Ok(client) = reqwest::Client::builder()
        .no_proxy()
        .timeout(std::time::Duration::from_secs(30))
        .build()
    else { return fallback };
    let Ok(response) = client.post(url).json(&body).send().await else { return fallback };
    let Ok(value) = response.json::<serde_json::Value>().await else { return fallback };
    let raw = value.pointer("/choices/0/message/content").and_then(|v| v.as_str()).unwrap_or("");
    let line = raw.lines().next().unwrap_or("").trim();
    let clean = line
        .trim_matches(|c: char| matches!(c, '"' | '\'' | '#' | '*' | ':' | '-' | ' '))
        .trim_end_matches(['.', '!', '?', ':']);
    if clean.len() < 3 {
        fallback
    } else {
        clean.chars().take(72).collect()
    }
}

fn read_chat_attachments(paths: &[String], image_store: &Path) -> Result<(String, Vec<serde_json::Value>, Vec<serde_json::Value>), String> {
    const MAX_ATTACHMENT_BYTES: u64 = 1_000_000_000_000;
    const PREVIEW_BYTES: u64 = 1_048_576;
    let mut prompt = String::new();
    let mut metadata = Vec::new();
    let mut images = Vec::new();
    for raw in paths.iter().take(12) {
        let path = PathBuf::from(raw);
        let name = path.file_name().and_then(|v| v.to_str()).unwrap_or("attachment");
        let size = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
        let mut entry = json!({"path":raw,"name":name,"bytes":size});
        if artifacts::is_image_name(name) {
            let image = artifacts::store_attached_image(image_store, &path)
                .map_err(|error| format!("Cannot attach image {name}: {error}"))?;
            let preview = artifacts::preview(image_store, &image.id)?;
            entry["artifactId"] = json!(image.id);
            entry["included"] = json!(true);
            images.push(json!({"type":"image_url","image_url":{"url":preview.data_url}}));
            prompt.push_str(&format!("\n[Attached image: {name}. Original file: {}. Its pixels are included in this message.]", path.display()));
            metadata.push(entry);
            continue;
        }
        if size > MAX_ATTACHMENT_BYTES {
            entry["error"] = serde_json::Value::String("File exceeds the 1 TB attachment limit".into());
            metadata.push(entry);
            continue;
        }
        match File::open(&path) {
            Ok(file) => {
                let mut bytes = Vec::new();
                let mut limited = file.take(PREVIEW_BYTES);
                if limited.read_to_end(&mut bytes).is_ok() {
                    if let Ok(text) = String::from_utf8(bytes) {
                        prompt.push_str(&format!(
                            "\n\n[Attached file: {name}]\n---\n{}\n---",
                            text
                        ));
                        entry["included"] = serde_json::Value::Bool(true);
                        if size > PREVIEW_BYTES {
                            entry["local_reference"] = serde_json::Value::Bool(true);
                            prompt.push_str(&format!("\n[The complete attachment remains available at: {}]", path.display()));
                        }
                    } else {
                        entry["included"] = serde_json::Value::Bool(false);
                        entry["note"] = serde_json::Value::String("Binary file; filename and size are visible, contents were not injected into this text-only model.".into());
                        prompt.push_str(&format!("\n\n[Attached binary file: {name}, {size} bytes]"));
                    }
                }
            }
            Err(error) => {
                entry["error"] = serde_json::Value::String(error.to_string());
            }
        }
        metadata.push(entry);
    }
    Ok((prompt, metadata, images))
}

fn composer_skill_instructions(skills: &[String]) -> Result<String, String> {
    if skills.len() > 3 { return Err("Choose at most three skills".into()); }
    let mut instructions = Vec::new();
    for skill in skills {
        match skill.as_str() {
            "computer-use" => instructions.push("Computer use skill: carry out the user's Windows/terminal task with desktop_use, system_use and reflex_use. Reflex Vision is an on-demand 0.8B model and may only be used because this prompt explicitly enabled /computer-use. Inspect before acting, verify results, and avoid unnecessary vision calls."),
            "browser-use" => instructions.push("OpenCore Browser skill: use browser_use for the isolated in-app browser. Inspect before interacting and verify navigation or page changes."),
            "chrome-control" => instructions.push("Chrome control skill: use chrome_use for the user's paired Chrome tabs. List and inspect tabs before acting, then verify the page result. For an explicit development/debugging request, evaluate may run JavaScript in the selected tab's DevTools Runtime. If Chrome is not paired, explain that connection is needed and do not claim the action happened."),
            _ => return Err(format!("Unknown skill: {skill}")),
        }
    }
    Ok(instructions.join("\n"))
}

fn clean_computer_action(value: &str) -> &str {
    let first = value.split_once('\n').map(|(first, _)| first).unwrap_or(value).trim();
    if matches!(first, "list" | "open" | "navigate" | "navigate_url" | "inspect" | "screenshot" | "read_screen" | "invoke" | "set_value" | "interact" | "set_at" | "commit_enter" | "commit_text" | "move" | "click" | "drag" | "type" | "scroll" | "key" | "back" | "forward" | "reload" | "find_apps" | "launch_app" | "run_command") {
        first
    } else { value }
}

fn browser_surface_error(user_text: &str, tool_name: &str) -> Option<&'static str> {
    let request = user_text.to_ascii_lowercase();
    let chrome_task = ["google chrome", "chrome application", "chrome browser", "open chrome", "in chrome", "using chrome", "chrome extension"]
        .iter().any(|phrase| request.contains(phrase));
    let both_browsers_requested = request.contains("both chrome and opencore browser") || request.contains("also use opencore browser");
    if tool_name == "browser_use" && chrome_task && !both_browsers_requested {
        Some("This request targets the Chrome application. Use desktop_use for its Windows window, or chrome_use if the extension is paired. browser_use controls only OpenCore Browser.")
    } else { None }
}

fn tool_progress(name: &str, args: &Value) -> String {
    let action = args.get("action").and_then(Value::as_str).unwrap_or("");
    let target = args.get("path").or_else(|| args.get("query")).and_then(Value::as_str).unwrap_or("the workspace");
    if name == "dev" {
        match action {
            "read" => return format!("Reading {target} to check the current implementation and version."),
            "edit" | "patch" | "apply_patch" => return format!("Applying the requested changes to {target}; other code stays in place."),
            "write" => return format!("Creating {target} in the project workspace."),
            "checkpoint" => return format!("Saving the completed component '{}' with its source versions and check results.", args["title"].as_str().unwrap_or("component")),
            "recall" => return format!("Retrieving project memory for {target} and checking whether its files changed."),
            _ => {}
        }
    }
    if (name == "dev" && action == "run") || (name == "system_use" && action == "run_command") {
        return format!("Running: {}. I'll inspect its exit status and output before continuing.", args["command"].as_str().unwrap_or("the requested command").chars().take(300).collect::<String>());
    }
    match (name, action) {
        ("dev", "status" | "list" | "search" | "read" | "git_status" | "git_diff" | "git_log") => "I'll inspect the existing files and their exact versions.",
        ("dev", "checkout") => "I'll restore the selected prior file into the coding workspace.",
        ("dev", "write" | "edit" | "patch" | "apply_patch") => "I'll edit the existing coding workspace and preserve its other parts.",
        ("dev", "run") => "I'll run the project's check and read its output.",
        ("dev", "git_commit") => "I'll commit the named files and check the result.",
        ("dev", "git_push") => "I'll push the current branch and check the result.",
        ("dev", "publish") => "I'll publish the checked file version.",
        ("desktop_use", "list") => "I'll check which Windows apps are open.",
        ("desktop_use", "navigate_url") => "I'll open the requested page in the selected Windows browser.",
        ("desktop_use", "inspect" | "read_screen" | "screenshot") => "I'll inspect the selected window before acting.",
        ("desktop_use", "click" | "drag" | "interact" | "invoke" | "key" | "type") => "I'll use the selected window and check what changed.",
        ("system_use", "find_apps") => "I'll look for the installed app.",
        ("system_use", "launch_app") => "I'll open the selected app.",
        ("system_use", "run_command") => "I'll run the command and check its output.",
        ("browser_use", _) => "I'll use OpenCore Browser and check the page.",
        ("chrome_use", _) => "I'll use the paired Chrome tab and check the page.",
        ("reflex_use", "pick") => "I'll find the right control with Reflex.",
        ("reflex_use", "ground" | "ground_click") => "I'll locate the target on the selected window's screen.",
        ("reflex_use", "see") => "I'll look at the selected window.",
        ("reflex_use", "play_snake") => "I'll play the game live with Reflex.",
        _ => "I'll run the next tool and check its result.",
    }.to_string()
}

#[cfg(test)]
mod browser_surface_guard_tests {
    use super::{browser_surface_error, tool_progress};
    use serde_json::json;

    #[test]
    fn explicit_chrome_task_rejects_opencore_browser_tool() {
        assert!(browser_surface_error("Open the Google Chrome application and play Snake", "browser_use").is_some());
        assert!(browser_surface_error("Open Google Chrome. Do not use OpenCore Browser.", "browser_use").is_some());
        assert!(browser_surface_error("Open the Google Chrome application and play Snake", "desktop_use").is_none());
        assert!(browser_surface_error("Use OpenCore Browser to search Google", "browser_use").is_none());
    }

    #[test]
    fn progress_describes_intent_before_the_tool_runs() {
        assert_eq!(tool_progress("desktop_use", &json!({"action":"list"})), "I'll check which Windows apps are open.");
        assert_eq!(tool_progress("system_use", &json!({"action":"launch_app"})), "I'll open the selected app.");
    }
}

fn normalize_computer_args(name: &str, mut args: Value) -> Value {
    let system_tool = name == "system_use" ||
        (name == "computer_use" && args.get("target").and_then(Value::as_str) == Some("system"));
    if system_tool && args.get("command").and_then(Value::as_str).is_some()
        && args.get("action").and_then(Value::as_str).is_some_and(|action| action.trim() == "run_command>") {
        args["action"] = json!("run_command");
    }
    if system_tool && args.get("command").is_none() {
        let embedded = args.get("action").and_then(Value::as_str)
            .and_then(|value| value.strip_prefix("run_command>"))
            .map(str::trim).filter(|command| !command.is_empty() && command.len() <= 16_000)
            .map(str::to_string);
        if let Some(command) = embedded {
            args["action"] = json!("run_command");
            args["command"] = json!(command);
        }
    }
    args
}

fn is_computer_tool(name: &str) -> bool {
    matches!(name, "computer_use" | "desktop_use" | "system_use" | "browser_use" | "chrome_use" | "reflex_use")
}

const TOOL_REPLAY_ROUNDS: usize = 8;
const TOOL_REPLAY_SUMMARY_CHARS: usize = 4_000;
const TOOL_REPLAY_PREFIX: &str = "Earlier tool activity (exact records remain in the conversation archive):\n";

fn compact_tool_history(messages: &mut Vec<serde_json::Value>, base_len: usize) {
    let round_starts = messages.iter().enumerate().skip(base_len)
        .filter(|(_, value)| value.get("role").and_then(|role| role.as_str()) == Some("assistant")
            && value.get("tool_calls").and_then(|calls| calls.as_array()).is_some_and(|calls| !calls.is_empty()))
        .map(|(index, _)| index)
        .collect::<Vec<_>>();
    if round_starts.len() <= TOOL_REPLAY_ROUNDS { return; }
    let cut = round_starts[round_starts.len() - TOOL_REPLAY_ROUNDS];
    let mut lines = Vec::new();
    for entry in &messages[base_len..cut] {
        if let Some(previous) = entry.get("content").and_then(|v| v.as_str()).filter(|v| v.starts_with(TOOL_REPLAY_PREFIX)) {
            lines.push(previous.trim_start_matches(TOOL_REPLAY_PREFIX).to_string());
        }
        if let Some(calls) = entry.get("tool_calls").and_then(|v| v.as_array()) {
            for call in calls {
                let name = call.pointer("/function/name").and_then(|v| v.as_str()).unwrap_or("tool");
                let args = call.pointer("/function/arguments").and_then(|v| v.as_str()).unwrap_or("{}");
                lines.push(format!("Called {name} {}", args.chars().take(180).collect::<String>()));
            }
        } else if entry.get("role").and_then(|v| v.as_str()) == Some("tool") {
            let content = entry.get("content").and_then(|v| v.as_str()).unwrap_or("");
            let parsed = serde_json::from_str::<serde_json::Value>(content).unwrap_or_default();
            let outcome = parsed.get("error").and_then(|v| v.as_str()).map(|v| format!("error: {v}"))
                .or_else(|| parsed.get("url").and_then(|v| v.as_str()).map(|v| format!("url: {v}")))
                .or_else(|| parsed.get("title").and_then(|v| v.as_str()).map(|v| format!("title: {v}")))
                .unwrap_or_else(|| content.chars().take(160).collect());
            lines.push(format!("Result: {}", outcome.chars().take(240).collect::<String>()));
        }
    }
    let joined = lines.join("\n");
    let chars = joined.chars().collect::<Vec<_>>();
    let start = chars.len().saturating_sub(TOOL_REPLAY_SUMMARY_CHARS);
    let summary = chars[start..].iter().collect::<String>();
    messages.drain(base_len..cut);
    messages.insert(base_len, json!({"role":"assistant","content":format!("{TOOL_REPLAY_PREFIX}{summary}")}));
}

#[cfg(test)]
mod tool_context_tests {
    use super::*;

    #[test]
    fn older_tool_rounds_are_bounded_without_orphaning_recent_results() {
        let mut messages = vec![json!({"role":"system","content":"rules"}), json!({"role":"user","content":"play snake"})];
        for step in 0..20 {
            messages.push(json!({"role":"assistant","tool_calls":[{"id":format!("call-{step}"),"function":{"name":"browser_use","arguments":format!("{{\"action\":\"click\",\"step\":{step}}}")}}]}));
            messages.push(json!({"role":"tool","tool_call_id":format!("call-{step}"),"content":format!("{{\"url\":\"https://example.test/{step}\"}}")}));
            compact_tool_history(&mut messages, 2);
        }
        assert_eq!(messages.len(), 2 + 1 + TOOL_REPLAY_ROUNDS * 2);
        assert!(messages[2]["content"].as_str().unwrap().starts_with(TOOL_REPLAY_PREFIX));
        assert!(messages[2]["content"].as_str().unwrap().contains("https://example.test/11"));
        assert_eq!(messages[3]["tool_calls"][0]["id"], "call-12");
        assert_eq!(messages.last().unwrap()["tool_call_id"], "call-19");
    }
}

#[cfg(test)]
mod composer_skill_tests {
    use super::*;

    #[test]
    fn skill_guidance_is_optional_and_bounded() {
        assert!(composer_skill_instructions(&[]).unwrap().is_empty());
        assert!(composer_skill_instructions(&["computer-use".into()]).unwrap().contains("on-demand 0.8B"));
        assert!(composer_skill_instructions(&["browser-use".into()]).unwrap().contains("browser_use"));
        assert!(composer_skill_instructions(&["chrome-control".into()]).unwrap().contains("paired Chrome"));
        assert!(composer_skill_instructions(&["invented".into()]).is_err());
    }

    #[test]
    fn strips_known_action_parameter_leakage() {
        assert_eq!(clean_computer_action("run_command\n<parameter=target>\nsystem"), "run_command");
        assert_eq!(clean_computer_action("unknown\n<parameter=target>"), "unknown\n<parameter=target>");
    }

    #[test]
    fn recovers_an_embedded_system_command_for_review_and_execution() {
        let recovered = normalize_computer_args("system_use", json!({"action":"run_command>\necho ok > test.txt"}));
        assert_eq!(recovered["action"], "run_command");
        assert_eq!(recovered["command"], "echo ok > test.txt");
        let other = normalize_computer_args("desktop_use", json!({"action":"run_command>\necho ok"}));
        assert_eq!(other["action"], "run_command>\necho ok");
        let trailing_delimiter = normalize_computer_args("system_use", json!({"action":"run_command>","command":"echo ok"}));
        assert_eq!(trailing_delimiter["action"], "run_command");
        assert_eq!(trailing_delimiter["command"], "echo ok");
    }
}

#[tauri::command]
async fn send_chat_message(
    core: tauri::State<'_, Arc<AppCore>>,
    app: tauri::AppHandle,
    request: ChatSendRequest,
) -> Result<ChatSendResult, String> {
    let core = core.inner().clone();
    let id = request.conversation_id.trim().to_string();
    if id.is_empty() {
        return Err("Conversation id is required".into());
    }
    let text = request.text.trim().to_string();
    if text.is_empty() && request.files.is_empty() {
        return Err("Message or attachment is required".into());
    }
    let skill_instructions = composer_skill_instructions(&request.skills)?;
    // Clipboard and temporary image files can disappear while the model starts.
    let (attachment_prompt, attachment_meta, attachment_images) = read_chat_attachments(&request.files, &artifact_root(&app)?)?;
    if !attachment_images.is_empty() && !core.runtime.install_root().join("vision/mmproj-BF16.gguf").is_file() {
        return Err("The main model vision projector is missing. Install the matching Qwen3.5-4B projector before sending images.".into());
    }
    let token = CancellationToken::new();
    {
        let mut active = core.active_chats.lock().map_err(|error| error.to_string())?;
        if active.contains_key(&id) { return Err("This conversation is already running. Stop it before retrying.".into()); }
        active.insert(id.clone(), token.clone());
    }
    let _active_guard = ActiveChatGuard { core: core.clone(), id: id.clone(), app: app.clone() };
    let runtime = core.runtime.clone();
    let startup_token = token.clone();
    let runtime_result = tauri::async_runtime::spawn_blocking(move || {
        if startup_token.is_cancelled() { return Err("__INTERRUPTED_BEFORE_SAVE__".into()); }
        runtime.ensure_running()
    })
        .await
        .map_err(|e| e.to_string())?;
    if token.is_cancelled() { return Err("__INTERRUPTED_BEFORE_SAVE__".into()); }
    let runtime_snapshot = runtime_result?;
    if token.is_cancelled() { return Err("__INTERRUPTED_BEFORE_SAVE__".into()); }

    let prior = core.store.conversation_messages(&id)?;
    let is_new = prior.is_empty();
    core.store.ensure_conversation(&id, "OpenCore", &runtime_snapshot.profile, "New conversation")?;
    if !is_new && matches!(runtime_snapshot.profile.as_str(), "echo" | "unsloth-echo") {
        sync_chat_activity(&core, &id).await
            .map_err(|error| format!("ECHO could not restore pending chat activity: {error}"))?;
    }

    let visible_text = if text.is_empty() {
        format!("Attached {} file(s)", request.files.len())
    } else {
        text.clone()
    };
    core.store.add_timeline(
        &id,
        "message",
        "user",
        "OpenCore",
        "You",
        &visible_text,
        &json!({"files":attachment_meta}),
    )?;
    if is_new {
        // A useful title appears as soon as the first message is saved. The small
        // model naming request runs independently of the potentially long agent task.
        let provisional = fallback_chat_title(&visible_text);
        core.store.rename_conversation(&id, &provisional)?;
        let title_core = core.clone();
        let title_id = id.clone();
        let title_seed = visible_text.clone();
        tauri::async_runtime::spawn(async move {
            let generated = generate_chat_title(title_core.runtime.clone(), title_seed).await;
            if let Ok(conversations) = title_core.store.list_conversations(None) {
                if conversations.iter().any(|chat| chat.id == title_id && chat.title == provisional) {
                    let _ = title_core.store.rename_conversation(&title_id, &generated);
                }
            }
        });
    }

    let full_user = format!("{text}{attachment_prompt}");
    let user_content = if attachment_images.is_empty() { json!(full_user) } else {
        let mut parts = vec![json!({"type":"text","text":full_user})];
        parts.extend(attachment_images);
        json!(parts)
    };
    let project_id = core.store.list_conversations(None)?.into_iter()
        .find(|conversation| conversation.id == id)
        .and_then(|conversation| conversation.project_id);
    let project_root = if let Some(project_id) = project_id {
        core.store.list_projects()?.into_iter()
            .find(|project| project.id == project_id && project.folder_available)
            .and_then(|project| project.folder_path)
            .map(PathBuf::from)
    } else { None };
    let data_root = app.path().app_data_dir().map_err(|error| error.to_string())?;
    let workspace_key = dev_tool::sha256(id.as_bytes());
    let workspace_root = project_root.clone().unwrap_or_else(|| data_root.join("code-workspaces").join(&workspace_key[..24]));
    let receipts_root = data_root.join("code-receipts").join(&workspace_key[..24]);
    let mut available_tools = vec![dev_tool::tool_spec(), artifacts::tool_spec(),
        json!({"type":"function","function":{
            "name":"desktop_use","description":"Control a running Windows window. Start with action=list (no windowId) for window IDs. For a Google Chrome window, navigate_url with windowId and an HTTP(S) url uses its address bar; then inspect or read_screen to verify the loaded page. To search for a game, use a Google search URL instead of guessing an unverified game URL. Inspect accessible controls once; if the target text is absent, immediately use read_screen with windowId for Windows OCR text and x,y coordinates on a canvas. Repeating inspect on panes will not reveal canvas text. For a named target, copy the exact x,y center of its matching read_screen line; do not estimate from the layout or nearby targets. Screenshots include actual image content for visual analysis; read_screen adds OCR text coordinates. For icons, images, canvas content or on-screen state, use reflex_use see (ask what is visible) and reflex_use ground (locate a described target). interact activates a control at x,y and falls back to a foreground click. drag draws one line from x,y to toX,toY within the selected window; inspect the canvas after a stroke. Coordinates are physical pixels relative to the selected window. Use inspect element x,y directly; do not copy absolute screenBounds. After an out-of-bounds error, inspect again and choose a fresh in-window target before retrying. Desktop input shares the user's Windows pointer and focus.",
            "parameters":{"type":"object","properties":{
                "action":{"type":"string","enum":["list","inspect","read_screen","invoke","set_value","interact","set_at","commit_enter","commit_text","move","click","drag","type","scroll","key","navigate_url"]},
                "windowId":{"type":"integer"},"elementId":{"type":"integer"},"x":{"type":"number"},"y":{"type":"number"},"toX":{"type":"number"},"toY":{"type":"number"},"text":{"type":"string"},
                "key":{"type":"string"},"direction":{"type":"string","enum":["up","down"]},"url":{"type":"string"}
            },"required":["action"],"additionalProperties":false}
        }}),
        json!({"type":"function","function":{
            "name":"system_use","description":"Use PowerShell or Windows installed apps. find_apps searches installed Start menu apps; it does not list currently running windows. run_command defaults to this conversation workspace; use cwd to choose another directory. For testing workspace code use dev run with verifyPaths. run_command already runs Windows PowerShell 5.1: put native PowerShell text in the separate command field, without a nested powershell -Command prefix. PowerShell 5.1 does not support &&; use separate calls or a semicolon. Quote paths containing spaces and use -LiteralPath for file paths. To write a text file use Set-Content -LiteralPath 'path' -Value 'text'; to check it use Get-Content -LiteralPath 'path'. A successful command returns exitCode 0 even when output is empty. If a command fails, retry system_use with corrected fields; do not switch to a user's terminal window.",
            "parameters":{"type":"object","properties":{
                "action":{"type":"string","enum":["find_apps","launch_app","run_command"]},"query":{"type":"string"},
                "appId":{"type":"string"},"command":{"type":"string"},"cwd":{"type":"string"},"keepUserWindowInFront":{"type":"boolean"}
            },"required":["action"],"additionalProperties":false}
        }}),
        json!({"type":"function","function":{
            "name":"browser_use","description":"Control the single OpenCore Browser web view. It works without the Chrome extension. open or navigate with url opens/navigates even after the panel was closed. inspect or read_screen returns page text plus only controls currently visible in the viewport with clickable x,y coordinates; scroll and inspect again for a control below the fold. Never click coordinates from old page text or from a previous scroll position. Follow navigation instructions in order, activate only requested controls, and confirm the intended URL before submitting a form; unrelated clicks and wrong-page submits are real side effects. For a visible Start game, Play, or other named button, use click_text with its exact text and verify clicked=true and after; do not repeatedly navigate to unrelated sites. type with text,x,y focuses that input and sets its value in one call; click with x,y activates a button and returns the settled page in after. key accepts Enter, arrows, Space, Escape, Tab, PageUp and PageDown (case insensitive); commit_enter also submits a focused form. Use after to verify the result; inspect again only if needed. If an OpenCore Browser action fails, report the error instead of switching to desktop_use or chrome_use for that page.",
            "parameters":{"type":"object","properties":{
                "action":{"type":"string","enum":["open","navigate","inspect","read_screen","click","click_text","type","key","commit_enter","scroll","back","forward","reload"]},
                "url":{"type":"string"},"x":{"type":"number"},"y":{"type":"number"},"text":{"type":"string"},"key":{"type":"string"},"deltaY":{"type":"number"}
            },"required":["action"],"additionalProperties":false}
        }}),
        json!({"type":"function","function":{
            "name":"chrome_use","description":"Control tabs in the user's Chrome profile through the paired OpenCore extension. Requires a connected extension. Use list to get tab IDs, then inspect and interact with each tab.",
            "parameters":{"type":"object","properties":{
                "action":{"type":"string","enum":["list","open","navigate","activate","close","inspect","screenshot","click","type","key","scroll","back","forward","reload","evaluate"]},
                "tabId":{"type":"integer"},"url":{"type":"string"},"x":{"type":"number"},"y":{"type":"number"},"text":{"type":"string"},"key":{"type":"string"},"deltaY":{"type":"number"},"expression":{"type":"string"}
            },"required":["action"],"additionalProperties":false}
        }})];
    available_tools.push(reflex::tool_spec());
    let computer_enabled = request.skills.iter().any(|skill| skill == "computer-use");
    let browser_enabled = request.skills.iter().any(|skill| skill == "browser-use");
    let chrome_enabled = request.skills.iter().any(|skill| skill == "chrome-control");
    available_tools.retain(|spec| {
        let name = spec.pointer("/function/name").and_then(Value::as_str).unwrap_or("");
        match name {
            "desktop_use" | "system_use" | "reflex_use" => computer_enabled,
            "browser_use" => browser_enabled,
            "chrome_use" => chrome_enabled,
            _ => true,
        }
    });
    if project_root.is_some() { available_tools.extend(tooling::read_only_tool_specs()); }
    for spec in &mut available_tools {
        spec["function"]["parameters"]["properties"]["explanation"] = json!({"type":"string", "description":"Explain to the user what you learned and why this exact action is needed, in clear complete sentences. Name the relevant file, behavior or error. Do not use generic filler."});
        if let Some(required) = spec["function"]["parameters"]["required"].as_array_mut() {
            required.push(json!("explanation"));
        }
    }
    // Keep every composer effort on this single Claude Agent SDK / Claude Code
    // preset path. reasoning_effort configures the local model request; it must
    // never select or bypass the agent harness.
    let result = claude_harness::run(core.clone(), app, &request, token, workspace_root, receipts_root,
        user_content, available_tools, skill_instructions).await;
    if computer_enabled {
        core.vision.stop();
        core.reflex.stop();
    }
    result
}

#[tauri::command]
fn resolve_tool_approval(
    core: tauri::State<'_, Arc<AppCore>>,
    request_id: String,
    approved: bool,
) -> Result<(), String> {
    let entry = core.pending_approvals.lock().map_err(|error| error.to_string())?.remove(&request_id)
        .ok_or("Approval request is no longer pending")?;
    entry.1.send(approved).map_err(|_| "Approval request is no longer active".to_string())
}

#[tauri::command]
async fn cancel_chat_message(
    core: tauri::State<'_, Arc<AppCore>>,
    conversation_id: String,
    app: tauri::AppHandle,
) -> Result<bool, String> {
    #[cfg(windows)]
    desktop_activity::clear(&app);
    let sole_active_chat = {
        let active = core.active_chats.lock().map_err(|e| e.to_string())?;
        let Some(token) = active.get(&conversation_id) else { return Ok(false); };
        token.cancel();
        active.len() == 1
    };
    if sole_active_chat || core.runtime.snapshot().status == "starting" {
        let runtime = core.runtime.clone();
        runtime.request_stop();
        tauri::async_runtime::spawn_blocking(move || runtime.stop())
            .await
            .map_err(|error| error.to_string())??;
    }
    Ok(true)
}


fn data_path(app: &tauri::App) -> Result<PathBuf, String> {
    #[cfg(debug_assertions)]
    if let Some(root) = std::env::var_os("OPENCORE_TEST_DATA_DIR") {
        let root = PathBuf::from(root);
        std::fs::create_dir_all(&root).map_err(|error| error.to_string())?;
        return Ok(root.join("control-center.sqlite3"));
    }
    let root = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?;
    // This installation's original WAL is locked/corrupt. Keep it untouched and use the
    // integrity-checked recovery copy when present; other installations retain the usual path.
    let recovered = root.join("control-center.recovered.sqlite3");
    Ok(if recovered.is_file() { recovered } else { root.join("control-center.sqlite3") })
}

fn artifact_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(app.path().app_data_dir().map_err(|e| e.to_string())?.join("artifacts"))
}

#[tauri::command]
fn preview_artifact(app: tauri::AppHandle, id: String) -> Result<artifacts::ArtifactPreview, String> {
    artifacts::preview(&artifact_root(&app)?, &id)
}

#[tauri::command]
fn preview_attachment_image(path: String) -> Result<String, String> {
    artifacts::preview_attached_image(Path::new(&path))
}

#[tauri::command]
fn download_artifact(app: tauri::AppHandle, id: String) -> Result<String, String> {
    let downloads = app.path().download_dir().map_err(|e| e.to_string())?;
    let target = artifacts::download(&artifact_root(&app)?, &downloads, &id)?;
    Ok(target.to_string_lossy().to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            #[cfg(windows)]
            {
                let overlay = tauri::WebviewWindowBuilder::new(app, "desktop-activity", tauri::WebviewUrl::App("index.html?desktop-activity".into()))
                    .title("OpenCore is using your computer")
                    .decorations(false)
                    .transparent(true)
                    .always_on_top(true)
                    .skip_taskbar(true)
                    .focused(false)
                    .focusable(false)
                    .visible(false)
                    .resizable(false)
                    .inner_size(290.0, 54.0)
                    .build()?;
                overlay.set_ignore_cursor_events(true)?;
                if let Ok(hwnd) = overlay.hwnd() {
                    unsafe { let _ = windows::Win32::UI::WindowsAndMessaging::SetWindowDisplayAffinity(windows::Win32::Foundation::HWND(hwnd.0 as _), windows::Win32::UI::WindowsAndMessaging::WDA_EXCLUDEFROMCAPTURE); }
                }
            }
            let database = data_path(app)?;
            let store = Arc::new(EventStore::open(&database)?);
            if database.file_name().and_then(|name| name.to_str()) == Some("control-center.recovered.sqlite3") {
                store.log("warn", "storage", "Using the verified recovery database; original database and WAL retained for diagnosis");
            }
            let runtime = Arc::new(RuntimeManager::new_with_resources(store.clone(), app.path().resource_dir().ok()));
            let core = Arc::new(AppCore {
                speech: speech::SpeechManager::new(runtime.install_root().to_path_buf(), app.path().resource_dir()?),
                store: store.clone(),
                runtime: runtime.clone(),
                active_chats: Mutex::new(HashMap::new()),
                pending_approvals: Mutex::new(HashMap::new()),
                browser: Arc::new(browser_bridge::BrowserBridge::new()),
                reflex: Arc::new(reflex::ReflexManager::new(app.path().resource_dir().ok(), runtime.install_root().to_path_buf())),
                vision: Arc::new(vision::VisionManager::new(runtime.install_root().to_path_buf())),
            });
            let browser_state = core.browser.clone();
            let browser_log = store.clone();
            tauri::async_runtime::spawn(async move {
                if let Err(error) = browser_bridge::serve(browser_state).await {
                    browser_log.log("error", "browser", &format!("Chrome bridge failed: {error}"));
                }
            });
            #[cfg(windows)] {
                let emergency_core = core.clone();
                let emergency_app = app.handle().clone();
                std::thread::spawn(move || {
                    use windows::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_ESCAPE};
                    let mut was_down = false;
                    let mut first_escape: Option<std::time::Instant> = None;
                    loop {
                        let down = unsafe { GetAsyncKeyState(VK_ESCAPE.0 as i32) } as u16 & 0x8000 != 0;
                        if down && !was_down {
                            let now = std::time::Instant::now();
                            if first_escape.is_some_and(|first| now.duration_since(first).as_millis() < 650) {
                                if let Ok(active) = emergency_core.active_chats.lock() {
                                    for token in active.values() { token.cancel(); }
                                }
                                let _ = emergency_app.emit("opencore-computer-use-cancelled", ());
                                first_escape = None;
                            } else { first_escape = Some(now); }
                        }
                        was_down = down;
                        std::thread::sleep(std::time::Duration::from_millis(18));
                    }
                });
            }
            app.manage(core);
            // Backfill folder identity once, off the UI thread, including sessions indexed by
            // earlier name-only releases. Each client reports progress through Operations.
            let history_store = store.clone();
            let history_runtime = runtime.clone();
            tauri::async_runtime::spawn_blocking(move || {
                for id in ["claude-code", "codex"] {
                    match history_store.has_setting(&format!("folder_project_backfill_v1_{id}")) {
                        Ok(true) => continue,
                        Ok(false) => {},
                        Err(error) => { history_store.log("warn", "history", &format!("Could not check {id} backfill: {error}")); continue; }
                    }
                    match history_store.start_operation("history_sync", id) {
                        Ok(operation) => run_history_sync(history_store.clone(), history_runtime.clone(), id.to_string(), operation.id),
                        Err(error) => history_store.log("warn", "history", &format!("Automatic {id} folder backfill skipped: {error}")),
                    }
                }
            });
            let gateway_store = store.clone();
            tauri::async_runtime::spawn(async move {
                if let Err(error) = gateway::serve(GatewayState::new(runtime, gateway_store.clone()), 8812).await {
                    gateway_store.log("error", "gateway", &error);
                }
            });
            let arguments: Vec<String> = std::env::args().collect();
            if let Some(index) = arguments.iter().position(|value| value == "--start-profile") {
                if let Some(profile) = arguments.get(index + 1).cloned() {
                    let startup_runtime = app.state::<Arc<AppCore>>().runtime.clone();
                    let startup_store = app.state::<Arc<AppCore>>().store.clone();
                    tauri::async_runtime::spawn_blocking(move || {
                        if let Err(error) = startup_runtime.start(&profile, None) {
                            startup_store.log("error", "runtime", &format!("Automatic start failed: {error}"));
                        }
                    });
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            speech::speech_start, speech::speech_transcribe, speech::speech_cancel,
            get_snapshot,
            list_conversations,
            get_conversation,
            start_profile,
            stop_runtime,
            restart_runtime,
            rename_conversation,
            delete_conversation,
            set_conversation_pinned,
            move_conversation_to_project,
            create_project,
            change_project_folder,
            rename_project,
            delete_project,
            export_conversation,
            archive_path
            ,open_local_path
            ,save_connector
            ,delete_connector
            ,test_connector
            ,sync_local_history
            ,list_operations
            ,start_history_sync
            ,configure_agent_connector
            ,search_archive
            ,archive_overview
            ,echo_working_set
            ,read_archive_page
            ,list_archive_pages
            ,list_archive_events
            ,read_archive_event
            ,read_archive_asset
            ,index_echo_history
            ,echo_memory_action
            ,clear_logs
            ,verify_model
            ,export_archive_index
            ,export_diagnostics
            ,health_check
            ,send_chat_message
            ,resolve_tool_approval
            ,cancel_chat_message
            ,configure_unsloth
            ,preview_artifact
            ,preview_attachment_image
            ,download_artifact
            ,browser_bridge_status
            ,browser_command
            ,native_browser_command
            ,desktop_command
            ,set_computer_focus_mode
        ])
        .run(tauri::generate_context!())
        .expect("error while running OpenCore");
}

#[cfg(test)]
mod image_attachment_tests {
    use super::*;
    #[test]
    fn attached_pixels_survive_original_file_removal() {
        let root = std::env::temp_dir().join(format!("opencore-image-test-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let source = root.join("picture.png");
        std::fs::write(&source, include_bytes!("../../public/opencore-logo.png")).unwrap();
        let (prompt, meta, parts) = read_chat_attachments(&[source.to_string_lossy().to_string()], &root.join("stored")).unwrap();
        std::fs::remove_file(&source).unwrap();
        assert!(prompt.contains("pixels are included"));
        assert_eq!(meta[0]["included"], true);
        let stored = artifacts::preview(&root.join("stored"), meta[0]["artifactId"].as_str().unwrap()).unwrap();
        assert_eq!(parts[0]["image_url"]["url"], stored.data_url);
        std::fs::remove_dir_all(root).unwrap();
    }
}
