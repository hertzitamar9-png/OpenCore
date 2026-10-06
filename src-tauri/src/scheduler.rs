//! Durable app-lifetime schedules, exact-once event admission and real workers.
//! No inference runtime is loaded by this timer. Only claimed prompt runs wake it.
use crate::{models::ChatSendRequest, scheduler_cron::CronSchedule, scheduler_worker::{self, WorkerConfig}, AppCore};
use chrono::{DateTime, Utc};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{collections::HashMap, path::PathBuf, sync::{Arc, Mutex, Weak, atomic::{AtomicBool, AtomicU16, Ordering}}, time::Duration};
use tauri::Emitter;
use tokio::sync::Notify;
use tokio_util::sync::CancellationToken;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BackgroundContext { pub request: ChatSendRequest, pub model_profile: String, pub workspace: PathBuf }

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Schedule {
    Once { at: String },
    Interval { #[serde(rename = "everySeconds")] every_seconds: u64, #[serde(default, rename = "startAt")] start_at: Option<String> },
    Cron { expression: String, #[serde(default = "utc_zone")] timezone: String },
    Event { name: String, #[serde(default = "empty_object")] filters: Value, #[serde(default, rename = "stepModulo")] step_modulo: Option<u64>, #[serde(default = "step_field", rename = "stepField")] step_field: String },
}
fn utc_zone() -> String { "utc".into() }
fn step_field() -> String { "step".into() }
fn empty_object() -> Value { json!({}) }
fn now() -> String { Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true) }
fn date_ms(value: &str) -> Result<i64, String> { DateTime::parse_from_rfc3339(value).map(|date| date.timestamp_millis()).map_err(|_| "Use a valid ISO time including its UTC offset".into()) }
fn timestamp(value: i64) -> String { DateTime::<Utc>::from_timestamp_millis(value).map(|time| time.to_rfc3339_opts(chrono::SecondsFormat::Millis, true)).unwrap_or_default() }

impl Schedule {
    fn validate(&self) -> Result<(), String> {
        match self {
            Self::Once { at } => { date_ms(at)?; },
            Self::Interval { every_seconds, start_at } => {
                if !(1..=31_536_000).contains(every_seconds) { return Err("Interval must be between 1 second and 365 days".into()); }
                if let Some(value) = start_at { date_ms(value)?; }
            },
            Self::Cron { expression, timezone } => { CronSchedule::parse(expression, timezone)?.next_after(Utc::now().timestamp_millis())?; },
            Self::Event { name, filters, step_modulo, step_field } => {
                if name.trim().is_empty() || name.len() > 200 { return Err("Event name is required and limited to 200 characters".into()); }
                if !filters.is_object() || filters.to_string().len() > 16_384 { return Err("Event filters must be a JSON object up to 16 KiB".into()); }
                if step_modulo == &Some(0) { return Err("Every N steps must be a positive integer".into()); }
                if step_field.is_empty() || step_field.len() > 200 { return Err("Step field is required".into()); }
            }
        }
        Ok(())
    }
    fn first_due(&self, at: i64) -> Result<Option<i64>, String> {
        Ok(match self {
            Self::Once { at } => Some(date_ms(at)?),
            Self::Interval { every_seconds, start_at } => Some(match start_at { Some(value) => date_ms(value)?, None => at.checked_add((*every_seconds * 1000) as i64).ok_or("Schedule time is out of range")? }),
            Self::Cron { expression, timezone } => Some(CronSchedule::parse(expression, timezone)?.next_after(at)?),
            Self::Event { .. } => None,
        })
    }
    fn after_due(&self, due: i64, at: i64) -> Result<(Option<i64>, u64), String> {
        Ok(match self {
            Self::Once { .. } => (None, 1),
            Self::Interval { every_seconds, .. } => {
                let period = (*every_seconds * 1000) as i64;
                let missed = (at.saturating_sub(due).max(0) / period) as u64 + 1;
                (Some(due.checked_add(period.checked_mul(missed as i64).ok_or("Schedule time is out of range")?).ok_or("Schedule time is out of range")?), missed)
            },
            Self::Cron { expression, timezone } => (Some(CronSchedule::parse(expression, timezone)?.next_after(at)?), 1),
            Self::Event { .. } => (None, 0),
        })
    }
    fn matches(&self, event: &Value) -> bool {
        let Self::Event { name, filters, step_modulo, step_field } = self else { return false; };
        if event["name"].as_str() != Some(name.as_str()) { return false; }
        if !filters.as_object().is_some_and(|filters| filters.iter().all(|(key, expected)| field(event, key).is_some_and(|actual| subset(actual, expected)))) { return false; }
        if let Some(modulo) = step_modulo { return field(event, step_field).and_then(Value::as_u64).is_some_and(|step| step > 0 && step % modulo == 0); }
        true
    }
}
fn field<'a>(event: &'a Value, path: &str) -> Option<&'a Value> {
    fn descend<'a>(value: &'a Value, path: &str) -> Option<&'a Value> { path.split('.').try_fold(value, |value, key| value.get(key)) }
    descend(event, path).or_else(|| event.get("data").and_then(|data| descend(data, path)))
}
fn subset(actual: &Value, expected: &Value) -> bool {
    if let Some(fields) = expected.as_object() { fields.iter().all(|(key, value)| actual.get(key).is_some_and(|actual| subset(actual, value))) } else { actual == expected }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum BackgroundAction { Prompt { prompt: String }, Worker { worker: WorkerConfig } }
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BackgroundTask {
    pub id: String, pub name: String, pub conversation_id: Option<String>, pub schedule: Schedule,
    pub task_action: BackgroundAction, pub context: Option<BackgroundContext>, pub paused: bool,
    pub next_due: Option<i64>, pub revision: u64, pub created_at: String, pub updated_at: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BackgroundRun {
    pub id: String, pub task_id: String, pub task_name: String, pub conversation_id: Option<String>,
    pub occurrence: String, pub status: String, pub scheduled_at: Option<String>, pub queued_at: String,
    pub started_at: Option<String>, pub finished_at: Option<String>, pub error: Option<String>,
    pub exit_code: Option<i32>, pub pid: Option<u32>, pub evidence: Value,
}
struct RunControl { token: CancellationToken, prompt: bool, uses_gpu: bool }

pub struct BackgroundManager {
    root: PathBuf, db: Mutex<Connection>, app: Mutex<Option<tauri::AppHandle>>, running: Mutex<HashMap<String, RunControl>>,
    attached: AtomicBool, closing: AtomicBool, stopping: AtomicBool, wake: Notify, stop: CancellationToken, gateway_port: AtomicU16,
    scheduler: Mutex<Option<tauri::async_runtime::JoinHandle<()>>>,
}
impl BackgroundManager {
    pub fn new(root: PathBuf) -> Result<Arc<Self>, String> {
        std::fs::create_dir_all(root.join("logs")).map_err(|error| error.to_string())?;
        let db = Connection::open(root.join("background.sqlite3")).map_err(|error| error.to_string())?;
        db.busy_timeout(Duration::from_secs(5)).map_err(|error| error.to_string())?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,payload TEXT NOT NULL,paused INTEGER NOT NULL,next_due INTEGER,deleted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,occurrence TEXT NOT NULL,payload TEXT NOT NULL,task_payload TEXT NOT NULL,status TEXT NOT NULL,queued_at TEXT NOT NULL,UNIQUE(task_id,occurrence));
            CREATE INDEX IF NOT EXISTS run_state ON runs(status,queued_at);
            CREATE INDEX IF NOT EXISTS run_task_state ON runs(task_id,status,queued_at);
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,name TEXT NOT NULL,payload TEXT NOT NULL,received_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);").map_err(|error| error.to_string())?;
        let token = format!("{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple());
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('webhook_token',?1)", [token]).map_err(|error| error.to_string())?;
        let this = Arc::new(Self { root, db: Mutex::new(db), app: Mutex::new(None), running: Mutex::new(HashMap::new()), attached: AtomicBool::new(false), closing: AtomicBool::new(false), stopping: AtomicBool::new(false), wake: Notify::new(), stop: CancellationToken::new(), gateway_port: AtomicU16::new(0), scheduler: Mutex::new(None) });
        this.recover()?;
        Ok(this)
    }
    fn recover(&self) -> Result<(), String> {
        let mut db = self.db.lock().map_err(|error| error.to_string())?;
        let transaction = db.transaction_with_behavior(TransactionBehavior::Immediate).map_err(|error| error.to_string())?;
        let rows = {
            let mut statement = transaction.prepare("SELECT payload FROM runs WHERE status='running'").map_err(|error| error.to_string())?;
            let values = statement.query_map([], |row| row.get::<_, String>(0)).map_err(|error| error.to_string())?;
            values.collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())?
        };
        for payload in rows {
            let mut run: BackgroundRun = serde_json::from_str(&payload).map_err(|error| error.to_string())?;
            run.status = "interrupted".into(); run.finished_at = Some(now()); run.error = Some("OpenCore closed during this run. It was not automatically repeated.".into());
            save_run(&transaction, &run)?;
        }
        transaction.commit().map_err(|error| error.to_string())
    }
    pub fn attach_app(self: &Arc<Self>, core: Arc<AppCore>, app: tauri::AppHandle) {
        if let Ok(mut stored) = self.app.lock() { *stored = Some(app.clone()); }
        self.gateway_port.store(core.runtime.snapshot().gateway_port, Ordering::Release);
        if self.attached.swap(true, Ordering::AcqRel) { return; }
        let manager = self.clone(); let weak: Weak<AppCore> = Arc::downgrade(&core);
        let handle = tauri::async_runtime::spawn(async move {
            loop {
                if manager.closing.load(Ordering::Acquire) { break; }
                if let Some(core) = weak.upgrade() {
                    if !core.update_in_progress.load(Ordering::Acquire) {
                        if let Err(error) = manager.enqueue_due(Utc::now().timestamp_millis()) { core.store.log("error", "background", &error); }
                        if let Err(error) = manager.dispatch(core.clone(), app.clone()).await { core.store.log("error", "background", &error); }
                    }
                } else { break; }
                tokio::select! { _ = manager.stop.cancelled() => break, _ = manager.wake.notified() => {}, _ = tokio::time::sleep(Duration::from_secs(1)) => {} }
            }
        });
        if let Ok(mut slot) = self.scheduler.lock() { *slot = Some(handle); }
    }
    fn changed(&self) {
        if let Ok(app) = self.app.lock() { if let Some(app) = app.as_ref() { let _ = app.emit("opencore-background-changed", json!({"timestamp":now()})); } }
        self.wake.notify_one();
    }
    pub fn webhook_token(&self) -> String {
        self.db.lock().ok().and_then(|db| db.query_row("SELECT value FROM settings WHERE key='webhook_token'", [], |row| row.get(0)).ok()).unwrap_or_default()
    }
    pub fn authenticate_webhook(&self, token: &str) -> bool {
        let expected = self.webhook_token();
        // Avoid prefix-dependent comparisons of the bearer secret.
        let mut difference = expected.len() ^ token.len();
        for (index, byte) in expected.bytes().enumerate() { difference |= (byte ^ token.as_bytes().get(index).copied().unwrap_or(0)) as usize; }
        !expected.is_empty() && difference == 0
    }
    fn webhook_url(&self) -> String { format!("http://127.0.0.1:{}/background/events", self.gateway_port.load(Ordering::Acquire)) }
    pub fn busy_gpu(&self) -> bool { self.running.lock().map(|runs| runs.values().any(|run| run.uses_gpu)).unwrap_or(true) }
    pub fn busy(&self) -> bool { self.running.lock().map(|runs| !runs.is_empty()).unwrap_or(true) }
    /// The harness adapter connects this token only after it owns its chat slot.
    /// A background cancellation must never stop an unrelated foreground turn.
    pub fn run_token(&self, id: &str) -> Option<CancellationToken> { self.running.lock().ok().and_then(|runs| runs.get(id).map(|run| run.token.clone())) }
    pub async fn cancel_active(&self) -> Result<(), String> {
        self.stopping.store(true, Ordering::Release);
        struct ResumeDispatch<'a>(&'a BackgroundManager);
        impl Drop for ResumeDispatch<'_> { fn drop(&mut self) { self.0.stopping.store(false, Ordering::Release); self.0.wake.notify_one(); } }
        let _resume = ResumeDispatch(self);
        { let runs = self.running.lock().map_err(|error| error.to_string())?; for run in runs.values() { run.token.cancel(); } }
        for _ in 0..120 { if !self.busy() { return Ok(()); } tokio::time::sleep(Duration::from_millis(100)).await; }
        Err("Owned background work is still stopping".into())
    }
    pub async fn shutdown(&self) {
        self.closing.store(true, Ordering::Release); self.stop.cancel();
        let _ = self.cancel_active().await;
        let handle = self.scheduler.lock().ok().and_then(|mut handle| handle.take());
        if let Some(handle) = handle { let _ = handle.await; }
    }
    fn tasks(&self) -> Result<Vec<BackgroundTask>, String> {
        let db = self.db.lock().map_err(|error| error.to_string())?;
        let mut statement = db.prepare("SELECT payload FROM tasks WHERE deleted=0 ORDER BY rowid DESC").map_err(|error| error.to_string())?;
        let rows = statement.query_map([], |row| row.get::<_, String>(0)).map_err(|error| error.to_string())?;
        rows.map(|row| serde_json::from_str(&row.map_err(|error| error.to_string())?).map_err(|error| error.to_string())).collect()
    }
    fn runs(&self, conversation: Option<&str>, limit: usize) -> Result<Vec<BackgroundRun>, String> {
        let db = self.db.lock().map_err(|error| error.to_string())?;
        let mut statement = db.prepare("SELECT payload FROM runs WHERE (?1 IS NULL OR json_extract(payload,'$.conversationId')=?1) ORDER BY queued_at DESC,rowid DESC LIMIT ?2").map_err(|error| error.to_string())?;
        let rows = statement.query_map(params![conversation,limit.min(1000) as i64], |row| row.get::<_, String>(0)).map_err(|error| error.to_string())?;
        let runs: Vec<BackgroundRun> = rows.map(|row| serde_json::from_str(&row.map_err(|error| error.to_string())?).map_err(|error| error.to_string())).collect::<Result<_, String>>()?;
        Ok(runs.into_iter().filter(|run| conversation.is_none_or(|id| run.conversation_id.as_deref() == Some(id))).collect())
    }
    fn task(&self, id: &str) -> Result<BackgroundTask, String> {
        let payload: String = self.db.lock().map_err(|error| error.to_string())?.query_row("SELECT payload FROM tasks WHERE id=?1 AND deleted=0", [id], |row| row.get(0)).map_err(|_| "Background job not found")?;
        serde_json::from_str(&payload).map_err(|error| error.to_string())
    }
    fn run(&self, id: &str) -> Result<BackgroundRun, String> {
        let payload: String = self.db.lock().map_err(|error| error.to_string())?.query_row("SELECT payload FROM runs WHERE id=?1", [id], |row| row.get(0)).map_err(|_| "Background run not found")?;
        serde_json::from_str(&payload).map_err(|error| error.to_string())
    }
    pub fn emit(&self, event: Value) -> Result<Value, String> {
        self.emit_scoped(event,None)
    }
    fn emit_scoped(&self, mut event: Value, conversation: Option<&str>) -> Result<Value, String> {
        if let Some(conversation)=conversation {
            if !event.is_object() { return Err("Event must be a JSON object".into()); }
            check_event_conversation(&event,conversation)?;
            let name=event["name"].as_str().unwrap_or(""); let id=event["id"].as_str().unwrap_or("");
            if ["generation.","studio.","background."].iter().any(|prefix|name.starts_with(*prefix)) || ["generation:","studio:","background:"].iter().any(|prefix|id.starts_with(*prefix)) {
                return Err("Runtime completion events are emitted by OpenCore; use a custom event name and id".into());
            }
            event["conversationId"]=json!(conversation);
        }
        if event.to_string().len() > 65_536 { return Err("Event body exceeds 64 KiB".into()); }
        let id = event["id"].as_str().filter(|id| !id.trim().is_empty() && id.len() <= 256).ok_or("A stable event id up to 256 characters is required")?;
        let name = event["name"].as_str().filter(|name| !name.trim().is_empty() && name.len() <= 200).ok_or("Event name is required")?;
        let mut db = self.db.lock().map_err(|error| error.to_string())?;
        let transaction = db.transaction_with_behavior(TransactionBehavior::Immediate).map_err(|error| error.to_string())?;
        if transaction.execute("INSERT OR IGNORE INTO events(id,name,payload,received_at) VALUES(?1,?2,?3,?4)", params![id,name,event.to_string(),now()]).map_err(|error| error.to_string())? == 0 {
            return Ok(json!({"accepted":false,"duplicate":true,"eventId":id,"queued":0}));
        }
        let tasks = {
            let mut statement = transaction.prepare("SELECT payload FROM tasks WHERE deleted=0 AND paused=0").map_err(|error| error.to_string())?;
            let rows = statement.query_map([], |row| row.get::<_, String>(0)).map_err(|error| error.to_string())?;
            rows.collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())?
        };
        let mut queued = 0;
        for payload in tasks {
            let task: BackgroundTask = serde_json::from_str(&payload).map_err(|error| error.to_string())?;
            if conversation.is_none_or(|id|task.conversation_id.as_deref()==Some(id)) && task.schedule.matches(&event) && queue(&transaction, &task, format!("event:{id}"), None, json!({"event":event}))? { queued += 1; }
        }
        transaction.commit().map_err(|error| error.to_string())?;
        drop(db); self.changed();
        Ok(json!({"accepted":true,"duplicate":false,"eventId":id,"queued":queued}))
    }
    fn enqueue_due(&self, at: i64) -> Result<usize, String> {
        let mut db = self.db.lock().map_err(|error| error.to_string())?;
        let transaction = db.transaction_with_behavior(TransactionBehavior::Immediate).map_err(|error| error.to_string())?;
        let tasks = {
            let mut statement = transaction.prepare("SELECT payload FROM tasks WHERE deleted=0 AND paused=0 AND next_due<=?1").map_err(|error| error.to_string())?;
            let rows = statement.query_map([at], |row| row.get::<_, String>(0)).map_err(|error| error.to_string())?;
            rows.collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())?
        };
        let mut count = 0;
        for payload in tasks {
            let mut task: BackgroundTask = serde_json::from_str(&payload).map_err(|error| error.to_string())?;
            let due = task.next_due.ok_or("Missing due time")?;
            let (next, missed) = task.schedule.after_due(due, at)?;
            let pending: i64 = transaction.query_row("SELECT count(*) FROM runs WHERE task_id=?1 AND status='queued' AND occurrence LIKE 'schedule:%'", [&task.id], |row| row.get(0)).map_err(|error| error.to_string())?;
            let missed_count = if matches!(&task.schedule, Schedule::Cron { .. }) { Value::Null } else { json!(missed) };
            if pending == 0 && queue(&transaction, &task, format!("schedule:{}:{due}", task.revision), Some(timestamp(due)), json!({"missedOccurrences":missed_count,"coalesced":at > due,"observedAt":timestamp(at)}))? { count += 1; }
            task.next_due = next; task.updated_at = now(); save_task(&transaction, &task)?;
        }
        transaction.commit().map_err(|error| error.to_string())?;
        drop(db); if count > 0 { self.changed(); }
        Ok(count)
    }
    fn claim(&self, id: &str) -> Result<Option<(BackgroundRun, BackgroundTask)>, String> {
        let mut db = self.db.lock().map_err(|error| error.to_string())?;
        let transaction = db.transaction_with_behavior(TransactionBehavior::Immediate).map_err(|error| error.to_string())?;
        let candidate: Option<(String, String)> = transaction.query_row("SELECT runs.payload,runs.task_payload FROM runs JOIN tasks ON tasks.id=runs.task_id WHERE runs.id=?1 AND runs.status='queued' AND tasks.deleted=0 AND (tasks.paused=0 OR runs.occurrence LIKE 'manual:%') AND NOT EXISTS (SELECT 1 FROM runs AS active WHERE active.task_id=runs.task_id AND active.status='running')", [id], |row| Ok((row.get(0)?, row.get(1)?))).optional().map_err(|error| error.to_string())?;
        let Some((run_payload, task_payload)) = candidate else { return Ok(None); };
        let mut run: BackgroundRun = serde_json::from_str(&run_payload).map_err(|error| error.to_string())?;
        let task = serde_json::from_str(&task_payload).map_err(|error| error.to_string())?;
        run.status = "running".into(); run.started_at = Some(now());
        save_run(&transaction, &run)?; transaction.commit().map_err(|error| error.to_string())?;
        Ok(Some((run, task)))
    }
    async fn idle(core: &AppCore) -> bool {
        core.active_chats.lock().is_ok_and(|chats| chats.is_empty()) && !core.studios.busy() && !core.studios.continuation_pending() && !core.claude_bridge.busy() && !crate::studio_jobs::gpu_reserved() && !core.speech.is_active().await
    }
    fn pending(&self) -> Result<Vec<(BackgroundRun, BackgroundTask)>, String> {
        let db = self.db.lock().map_err(|error| error.to_string())?;
        // One eligible head per task. Reading only the latest history would strand
        // older pending events when a long-running worker builds a large backlog.
        let mut statement = db.prepare("SELECT run.payload,run.task_payload FROM runs AS run JOIN tasks ON tasks.id=run.task_id
            WHERE run.status='queued' AND tasks.deleted=0 AND (tasks.paused=0 OR run.occurrence LIKE 'manual:%')
            AND NOT EXISTS(SELECT 1 FROM runs AS active WHERE active.task_id=run.task_id AND active.status='running')
            AND NOT EXISTS(SELECT 1 FROM runs AS older WHERE older.task_id=run.task_id AND older.status='queued'
                AND (tasks.paused=0 OR older.occurrence LIKE 'manual:%') AND older.rowid<run.rowid)
            ORDER BY run.queued_at,run.rowid").map_err(|error| error.to_string())?;
        let rows = statement.query_map([], |row| Ok((row.get::<_,String>(0)?,row.get::<_,String>(1)?))).map_err(|error| error.to_string())?;
        rows.map(|row| { let (run,task)=row.map_err(|error|error.to_string())?; Ok((serde_json::from_str(&run).map_err(|error|error.to_string())?,serde_json::from_str(&task).map_err(|error|error.to_string())?)) }).collect()
    }
    async fn dispatch(self: &Arc<Self>, core: Arc<AppCore>, app: tauri::AppHandle) -> Result<(), String> {
        for (run, task) in self.pending()? {
            if self.closing.load(Ordering::Acquire) || self.stopping.load(Ordering::Acquire) || core.update_in_progress.load(Ordering::Acquire) { break; }
            let (prompt, uses_gpu, needs_idle) = match &task.task_action { BackgroundAction::Prompt { .. } => (true, false, true), BackgroundAction::Worker { worker } => (false, worker.uses_gpu, worker.wait_policy == "when-idle") };
            let capacity = { let running = self.running.lock().map_err(|error| error.to_string())?; running.len() < 4 && !(prompt && running.values().any(|control| control.prompt)) };
            if !capacity || (prompt && self.busy_gpu()) || ((needs_idle || prompt || uses_gpu) && !Self::idle(&core).await) { continue; }
            let gpu = if uses_gpu { match crate::studio_jobs::reserve_gpu() { Ok(gpu) => Some(gpu), Err(_) => continue } } else { None };
            if uses_gpu {
                // Foreground chat admission checks gpu_reserved under this same
                // lock. Reserving first, then rechecking closes both race orders.
                let chats = core.active_chats.lock().map_err(|error| error.to_string())?;
                if !chats.is_empty() { continue; }
            }
            let Some((run, task)) = self.claim(&run.id)? else { continue; };
            let token = CancellationToken::new();
            {
                // Same lock order as cancel(): a cancellation between the SQL claim
                // and process registration cannot lose the cancellation token.
                let mut running = self.running.lock().map_err(|error| error.to_string())?;
                let current = self.run(&run.id)?;
                if current.evidence["cancelRequested"] == true || self.stopping.load(Ordering::Acquire) || self.closing.load(Ordering::Acquire) { token.cancel(); }
                running.insert(run.id.clone(), RunControl { token: token.clone(), prompt, uses_gpu });
            }
            self.changed(); let manager = self.clone(); let core = core.clone(); let app = app.clone();
            tauri::async_runtime::spawn(async move {
                let outcome = manager.perform(core.clone(), app, &run, &task, token.clone()).await;
                let closing = manager.closing.load(Ordering::Acquire) || core.update_in_progress.load(Ordering::Acquire);
                if let Err(error) = manager.finish(&run.id, outcome, token.is_cancelled(), closing) {
                    core.store.log("error", "background", &format!("Could not persist background run {}: {error}",run.id));
                }
                drop(gpu);
                if let Ok(mut running) = manager.running.lock() { running.remove(&run.id); }
                manager.changed();
            });
        }
        Ok(())
    }
    async fn perform(self: &Arc<Self>, core: Arc<AppCore>, app: tauri::AppHandle, run: &BackgroundRun, task: &BackgroundTask, token: CancellationToken) -> Result<Value, String> {
        core.ensure_not_updating()?;
        if token.is_cancelled() { return Err("Background run was cancelled before execution".into()); }
        match &task.task_action {
            BackgroundAction::Prompt { prompt } => {
                let context = task.context.clone().ok_or("The original chat settings are missing")?;
                let mut request = context.request; request.text = prompt.clone(); request.files.clear(); request.submission_id = Some(format!("background:{}", run.id));
                let evidence = json!({"taskId":task.id,"runId":run.id,"taskName":task.name,"modelProfile":context.model_profile,"workspace":context.workspace,"approvalMode":request.approval_mode.as_str(),"trigger":run.evidence,"scheduledAt":run.scheduled_at,"occurrence":run.occurrence});
                // The root adapter owns the actual harness, model selection, race-safe chat gate and runtime release.
                // Await cancellation without dropping the harness future, so partial file evidence can finish.
                let future = crate::resume_scheduled_job(core.clone(), app, request, run.id.clone(), evidence);
                tokio::pin!(future);
                let result = tokio::select! {
                    result = &mut future => result,
                    _ = token.cancelled() => {
                        // Root connects this exact run's token to its owned inference.
                        future.await
                    }
                };
                result.and_then(|result| serde_json::to_value(result).map_err(|error| error.to_string()))
            },
            BackgroundAction::Worker { worker } => {
                if worker.uses_gpu {
                    core.speech.release_idle_model().await?;
                    let runtime = core.runtime.clone();
                    tokio::task::spawn_blocking(move || runtime.stop()).await.map_err(|error| error.to_string())??;
                }
                let config = worker.clone(); let dir = self.root.join("logs").join(&run.id); let url = self.webhook_url(); let secret = self.webhook_token();
                let manager = self.clone(); let id = run.id.clone();
                let started = Arc::new(move |pid| manager.set_pid(&id, pid));
                let result = tokio::task::spawn_blocking(move || scheduler_worker::run(config, token, dir, url, secret, started)).await.map_err(|error| error.to_string())??;
                Ok(json!({"exitCode":result.exit_code,"cancelled":result.cancelled,"stdoutTruncated":result.stdout_truncated,"stderrTruncated":result.stderr_truncated}))
            }
        }
    }
    fn set_pid(&self, id: &str, pid: u32) -> Result<(), String> {
        let mut run = self.run(id)?; run.pid = Some(pid);
        save_run(&*self.db.lock().map_err(|error| error.to_string())?, &run)?; self.changed(); Ok(())
    }
    fn finish(&self, id: &str, outcome: Result<Value, String>, cancelled: bool, interrupted: bool) -> Result<(), String> {
        let mut run = self.run(id)?;
        if !cancelled && !interrupted && outcome.as_ref().err().is_some_and(|error|error==crate::SCHEDULED_ADMISSION_BUSY) {
            // Foreground work won the chat admission race. No inference or
            // timeline write happened; retain the same durable occurrence.
            run.status="queued".into(); run.started_at=None; run.finished_at=None; run.error=None;
            let waits=run.evidence["admissionWaits"].as_u64().unwrap_or(0);
            run.evidence["admissionWaits"]=json!(waits.saturating_add(1));
            run.evidence["lastAdmissionWaitAt"]=json!(now());
            save_run(&*self.db.lock().map_err(|error|error.to_string())?,&run)?;
            self.changed(); return Ok(());
        }
        run.finished_at = Some(now());
        match outcome {
            Ok(value) => {
                run.exit_code = value["exitCode"].as_i64().map(|code| code as i32);
                let worker_failed = value.get("exitCode").is_some() && run.exit_code != Some(0) && !cancelled;
                run.status = if cancelled { if interrupted { "interrupted" } else { "cancelled" } } else if worker_failed { "failed" } else { "completed" }.into();
                if worker_failed { run.error = Some(format!("Worker exited with code {}", run.exit_code.map(|code| code.to_string()).unwrap_or_else(|| "unavailable (terminated by signal)".into()))); }
                run.evidence["result"] = value;
            },
            Err(error) => { run.status = if cancelled { if interrupted { "interrupted" } else { "cancelled" } } else { "failed" }.into(); run.error = Some(error); }
        }
        save_run(&*self.db.lock().map_err(|error| error.to_string())?, &run)?;
        let _ = self.emit(json!({"id":format!("background:{}:{}",run.id,run.status),"name":format!("background.{}",run.status),"runId":run.id,"taskId":run.task_id,"status":run.status,"exitCode":run.exit_code,"conversationId":run.conversation_id}));
        self.changed(); Ok(())
    }
    fn create_or_update(&self, args: &Value, context: Option<BackgroundContext>, update: bool) -> Result<BackgroundTask, String> {
        command_conversation(args,context.as_ref())?;
        let input = args.get("task").ok_or("Task definition is required")?;
        let existing = if update { Some(self.task(args["taskId"].as_str().ok_or("taskId is required")?)?) } else { None };
        // Authenticate the caller against the origin before replacing its
        // execution settings with the immutable task's saved settings.
        if let Some(existing)=existing.as_ref() { check_task_context(existing,context.as_ref())?; }
        let name = input["name"].as_str().map(str::trim).filter(|name| !name.is_empty() && name.len() <= 200).ok_or("Job name is required, up to 200 characters")?.to_string();
        let schedule: Schedule = serde_json::from_value(input["schedule"].clone()).map_err(|error| format!("Invalid schedule: {error}"))?;
        schedule.validate()?;
        let task_action: BackgroundAction = serde_json::from_value(input["taskAction"].clone()).map_err(|error| format!("Invalid action: {error}"))?;
        let context = if let Some(existing) = existing.as_ref() { existing.context.clone() } else { context };
        let conversation_id = if let Some(existing) = existing.as_ref() { existing.conversation_id.clone() }
            else { input["conversationId"].as_str().or(args["conversationId"].as_str()).map(str::to_string).or_else(|| context.as_ref().map(|context| context.request.conversation_id.clone())) };
        if let Some(context) = context.as_ref() {
            if conversation_id.as_deref() != Some(context.request.conversation_id.as_str()) { return Err("Originating chat settings do not match this job".into()); }
        }
        match &task_action {
            BackgroundAction::Prompt { prompt } => {
                if prompt.trim().is_empty() || prompt.len() > 65_536 { return Err("Agent prompt must contain 1 to 65536 characters".into()); }
                let context = context.as_ref().ok_or("Send a message in this chat first so its exact model and approval settings can be saved")?;
                if conversation_id.as_deref() != Some(context.request.conversation_id.as_str()) { return Err("Originating chat settings do not match this job".into()); }
                if context.model_profile.is_empty() || !context.workspace.is_absolute() || !context.workspace.is_dir() { return Err("Original model profile and existing workspace are required".into()); }
            },
            BackgroundAction::Worker { worker } => worker.validate()?,
        }
        let time = now(); let next_due = schedule.first_due(Utc::now().timestamp_millis())?;
        let task = BackgroundTask { id: existing.as_ref().map(|task| task.id.clone()).unwrap_or_else(|| uuid::Uuid::new_v4().to_string()), name, conversation_id, schedule, task_action, context,
            paused: existing.as_ref().is_some_and(|task| task.paused), next_due, revision: existing.as_ref().map_or(1, |task| task.revision + 1), created_at: existing.as_ref().map(|task| task.created_at.clone()).unwrap_or_else(|| time.clone()), updated_at: time };
        let mut db = self.db.lock().map_err(|error| error.to_string())?;
        let transaction = db.transaction_with_behavior(TransactionBehavior::Immediate).map_err(|error| error.to_string())?;
        save_task(&transaction, &task)?;
        if update { cancel_queued(&transaction, &task.id, "Job definition was edited")?; }
        transaction.commit().map_err(|error| error.to_string())?; drop(db); self.changed(); Ok(task)
    }
    fn pause(&self, id: &str, paused: bool) -> Result<BackgroundTask, String> {
        let mut task = self.task(id)?; task.paused = paused; task.updated_at = now();
        // Keep the previous due instant on resume so missed occurrences coalesce once.
        save_task(&*self.db.lock().map_err(|error| error.to_string())?, &task)?; self.changed(); Ok(task)
    }
    fn delete(&self, id: &str) -> Result<(), String> {
        let _ = self.task(id)?;
        let mut db = self.db.lock().map_err(|error| error.to_string())?;
        let transaction = db.transaction_with_behavior(TransactionBehavior::Immediate).map_err(|error| error.to_string())?;
        transaction.execute("UPDATE tasks SET deleted=1 WHERE id=?1", [id]).map_err(|error| error.to_string())?;
        cancel_queued(&transaction, id, "Job was deleted")?;
        transaction.commit().map_err(|error| error.to_string())?; drop(db);
        if let Ok(runs) = self.running.lock() { for (run_id, control) in runs.iter() { if self.run(run_id).is_ok_and(|run| run.task_id == id) { control.token.cancel(); } } }
        self.changed(); Ok(())
    }
    fn run_now(&self, id: &str) -> Result<BackgroundRun, String> {
        let task = self.task(id)?;
        let db = self.db.lock().map_err(|error| error.to_string())?;
        let occurrence = format!("manual:{}", uuid::Uuid::new_v4());
        queue(&db, &task, occurrence.clone(), None, json!({"manual":true}))?;
        let payload: String = db.query_row("SELECT payload FROM runs WHERE task_id=?1 AND occurrence=?2", params![id,occurrence], |row| row.get(0)).map_err(|error| error.to_string())?;
        let run = serde_json::from_str(&payload).map_err(|error| error.to_string())?; drop(db); self.changed(); Ok(run)
    }
    fn cancel(&self, id: &str) -> Result<BackgroundRun, String> {
        let controls = self.running.lock().map_err(|error| error.to_string())?;
        let mut db = self.db.lock().map_err(|error| error.to_string())?;
        let transaction = db.transaction_with_behavior(TransactionBehavior::Immediate).map_err(|error| error.to_string())?;
        let payload: String = transaction.query_row("SELECT payload FROM runs WHERE id=?1",[id],|row|row.get(0)).map_err(|_| "Background run not found")?;
        let mut run: BackgroundRun = serde_json::from_str(&payload).map_err(|error| error.to_string())?;
        if run.status == "running" { run.evidence["cancelRequested"] = json!(true); if let Some(control) = controls.get(id) { control.token.cancel(); } }
        else if run.status == "queued" { run.status = "cancelled".into(); run.finished_at = Some(now()); }
        save_run(&transaction, &run)?;
        transaction.commit().map_err(|error| error.to_string())?;
        drop(db); drop(controls);
        self.changed(); Ok(run)
    }
    fn list(&self, conversation: Option<&str>, reveal_token: bool) -> Result<Value, String> {
        let tasks: Vec<_> = self.tasks()?.into_iter().filter(|task| conversation.is_none_or(|id| task.conversation_id.as_deref() == Some(id))).collect();
        Ok(json!({"tasks":tasks,"runs":self.runs(conversation,500)?,"webhook":{"url":self.webhook_url(),"token":if reveal_token { Some(self.webhook_token()) } else { None }},"execution":{"appMustBeOpen":true,"noPermanentService":true,"gpuWorkersHoldReservationUntilExit":true}}))
    }
    fn command(&self,args:&Value,context:Option<BackgroundContext>)->Result<Value,String> {
        let conversation=command_conversation(args,context.as_ref())?;
        if context.is_some() {
            if let Some(id)=args.get("taskId").filter(|value|!value.is_null()) {
                let task=self.task(id.as_str().ok_or("taskId must be a string")?)?;
                check_task_context(&task,context.as_ref())?;
            }
            if let Some(id)=args.get("runId").filter(|value|!value.is_null()) {
                let run=self.run(id.as_str().ok_or("runId must be a string")?)?;
                check_run_context(&run,context.as_ref())?;
            }
        }
        let action=args["action"].as_str().unwrap_or("list");
        let id=||args["taskId"].as_str().ok_or_else(||"taskId is required".to_string());
        match action {
            "list"|"status"=>self.list(conversation.as_deref(),context.is_none()),
            "context"=>context.map(|context|json!(context)).ok_or_else(||"Send a chat message first to capture its model and approval settings".into()),
            "create"=>self.create_or_update(args,context,false).map(|task|json!(task)),
            "update"=>self.create_or_update(args,context,true).map(|task|json!(task)),
            "pause"=>self.pause(id()?,true).map(|task|json!(task)),
            "resume"=>self.pause(id()?,false).map(|task|json!(task)),
            "delete"=>{self.delete(id()?)?;Ok(json!({"deleted":true}))},
            "run_now"=>self.run_now(id()?).map(|run|json!(run)),
            "cancel"=>self.cancel(args["runId"].as_str().ok_or("runId is required")?).map(|run|json!(run)),
            "logs"=>{let run=self.run(args["runId"].as_str().ok_or("runId is required")?)?;scheduler_worker::read_logs(&self.root.join("logs").join(run.id))},
            "emit"=>self.emit_scoped(args.get("event").cloned().ok_or("event is required")?,context.as_ref().map(|context|context.request.conversation_id.as_str())),
            "webhook_setup"=>Ok(json!({"url":self.webhook_url(),"token":self.webhook_token()})),
            "rotate_token"=>{
                if context.is_some() {return Err("Webhook token management is available in the native Jobs screen".into());}
                let token=format!("{}{}",uuid::Uuid::new_v4().simple(),uuid::Uuid::new_v4().simple());
                self.db.lock().map_err(|error|error.to_string())?.execute("UPDATE settings SET value=?1 WHERE key='webhook_token'",[&token]).map_err(|error|error.to_string())?;
                self.changed();Ok(json!({"url":self.webhook_url(),"token":token}))
            },
            _=>Err("Unknown background action".into()),
        }
    }
}
fn command_conversation(args:&Value,context:Option<&BackgroundContext>)->Result<Option<String>,String> {
    let Some(context)=context else {return Ok(args["conversationId"].as_str().map(str::to_string));};
    let caller=context.request.conversation_id.as_str();
    if caller.trim().is_empty() {return Err("Originating chat context is required".into());}
    for supplied in [args.get("conversationId"),args.get("task").and_then(|task|task.get("conversationId"))].into_iter().flatten() {
        if !supplied.is_null() && supplied.as_str()!=Some(caller) {return Err("Background jobs are scoped to the originating chat".into());}
    }
    Ok(Some(caller.to_string()))
}
fn check_task_context(task:&BackgroundTask,context:Option<&BackgroundContext>)->Result<(),String> {
    if let Some(context)=context {
        let caller=context.request.conversation_id.as_str();
        if task.conversation_id.as_deref()!=Some(caller) || task.context.as_ref().is_some_and(|saved|saved.request.conversation_id!=caller) {
            return Err("Background job not found in this chat".into());
        }
    }
    Ok(())
}
fn check_run_context(run:&BackgroundRun,context:Option<&BackgroundContext>)->Result<(),String> {
    if context.is_some_and(|context|run.conversation_id.as_deref()!=Some(context.request.conversation_id.as_str())) {return Err("Background run not found in this chat".into());}
    Ok(())
}
fn check_event_conversation(event:&Value,conversation:&str)->Result<(),String> {
    match event {
        Value::Object(fields)=>{
            for (key,value) in fields {
                if matches!(key.as_str(),"conversationId"|"conversation_id") && !value.is_null() && value.as_str()!=Some(conversation) {return Err("An agent event cannot impersonate another chat".into());}
                check_event_conversation(value,conversation)?;
            }
        },
        Value::Array(values)=>{for value in values {check_event_conversation(value,conversation)?;}},
        _=>{},
    }
    Ok(())
}
fn save_task(db: &Connection, task: &BackgroundTask) -> Result<(), String> {
    db.execute("INSERT INTO tasks(id,payload,paused,next_due,deleted) VALUES(?1,?2,?3,?4,0) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,paused=excluded.paused,next_due=excluded.next_due", params![task.id,serde_json::to_string(task).map_err(|error| error.to_string())?,task.paused,task.next_due]).map_err(|error| error.to_string())?; Ok(())
}
fn save_run(db: &Connection, run: &BackgroundRun) -> Result<(), String> {
    db.execute("UPDATE runs SET payload=?2,status=?3 WHERE id=?1", params![run.id,serde_json::to_string(run).map_err(|error| error.to_string())?,run.status]).map_err(|error| error.to_string())?; Ok(())
}
fn queue(db: &Connection, task: &BackgroundTask, occurrence: String, scheduled_at: Option<String>, evidence: Value) -> Result<bool, String> {
    let run = BackgroundRun { id: uuid::Uuid::new_v4().to_string(), task_id: task.id.clone(), task_name: task.name.clone(), conversation_id: task.conversation_id.clone(), occurrence, status: "queued".into(), scheduled_at, queued_at: now(), started_at: None, finished_at: None, error: None, exit_code: None, pid: None, evidence };
    Ok(db.execute("INSERT OR IGNORE INTO runs(id,task_id,occurrence,payload,task_payload,status,queued_at) VALUES(?1,?2,?3,?4,?5,'queued',?6)", params![run.id,task.id,run.occurrence,serde_json::to_string(&run).map_err(|error| error.to_string())?,serde_json::to_string(task).map_err(|error| error.to_string())?,run.queued_at]).map_err(|error| error.to_string())? == 1)
}
fn cancel_queued(db: &Connection, task_id: &str, reason: &str) -> Result<(), String> {
    let payloads = {
        let mut statement = db.prepare("SELECT payload FROM runs WHERE task_id=?1 AND status='queued'").map_err(|error| error.to_string())?;
        let rows = statement.query_map([task_id], |row| row.get::<_, String>(0)).map_err(|error| error.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())?
    };
    for payload in payloads { let mut run: BackgroundRun = serde_json::from_str(&payload).map_err(|error| error.to_string())?; run.status = "cancelled".into(); run.finished_at = Some(now()); run.error = Some(reason.into()); save_run(db, &run)?; }
    Ok(())
}

pub async fn execute(core: Arc<AppCore>, _app: tauri::AppHandle, args: &Value, context: Option<BackgroundContext>) -> Result<Value, String> {
    core.ensure_not_updating()?;
    core.background.command(args,context)
}
pub fn tool_spec() -> Value {
    json!({"type":"function","function":{"name":"background_use","description":"Create durable schedules and named-event triggers while OpenCore is open. Prompt jobs save this chat's exact model, workspace and approval settings and invoke the real agent when GPU work is idle. Workers run an explicitly approved executable/argument array in a saved directory, hidden, with logs, exit codes and process-tree cancellation. Use event triggers for training checkpoints; stepModulo 500 matches positive steps 500,1000,1500. Missed schedules coalesce after reopen; interrupted runs are evidence and are not silently repeated. GPU workers hold the GPU until exit, so agent checkpoints wait. No inference polling or permanent OS service. webhook_setup reveals the loopback bearer token only when explicitly requested. Never fabricate worker results or completion.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["list","status","create","update","pause","resume","delete","run_now","cancel","logs","emit","webhook_setup"]},"taskId":{"type":"string"},"runId":{"type":"string"},"conversationId":{"type":"string"},"task":{"type":"object","description":"name, schedule and taskAction. schedule kinds: once {at ISO}, interval {everySeconds,startAt?}, cron {expression,timezone utc|local}, event {name,filters?,stepModulo?,stepField?}. taskAction: prompt {prompt}, worker {worker:{command,args,cwd,usesGpu,longRunning,waitPolicy when-idle|allow-during-chat}}. Saved permissions cannot be raised by task JSON."},"event":{"type":"object","description":"Stable id, name and structured fields/data."}},"required":["action"]}}})
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (PathBuf, Arc<BackgroundManager>) { let root = std::env::temp_dir().join(format!("background-scheduler-{}",uuid::Uuid::new_v4())); let manager = BackgroundManager::new(root.clone()).unwrap(); (root, manager) }
    fn context(mode: &str) -> BackgroundContext {
        BackgroundContext { request: serde_json::from_value(json!({"conversationId":"chat","text":"source task","approvalMode":mode,"reasoningEffort":"high","skills":["web-dev"],"subagentsEnabled":true,"maxSubagents":3,"projectSkillsEnabled":true,"compactAtTokens":200000})).unwrap(), model_profile:"echo-3t".into(), workspace:std::env::temp_dir() }
    }
    #[test]
    fn foreground_admission_race_preserves_the_same_occurrence_for_retry() {
        let (root,manager)=fixture();
        create(&manager,json!({"kind":"once","at":"2026-01-01T00:00:00Z"}));
        manager.enqueue_due(date_ms("2026-10-06T00:00:00Z").unwrap()).unwrap();
        let run=manager.runs(None,10).unwrap().remove(0);
        manager.claim(&run.id).unwrap().unwrap();
        manager.finish(&run.id,Err(crate::SCHEDULED_ADMISSION_BUSY.into()),false,false).unwrap();
        let queued=manager.run(&run.id).unwrap();
        assert_eq!(queued.status,"queued"); assert_eq!(queued.occurrence,run.occurrence);
        assert!(queued.started_at.is_none()); assert!(queued.finished_at.is_none());
        assert_eq!(queued.evidence["admissionWaits"],1);
        assert_eq!(manager.runs(None,10).unwrap().len(),1);
        manager.claim(&run.id).unwrap().unwrap();
        manager.finish(&run.id,Err(crate::SCHEDULED_ADMISSION_BUSY.into()),true,false).unwrap();
        assert_eq!(manager.run(&run.id).unwrap().status,"cancelled");
        drop(manager); let _=std::fs::remove_dir_all(root);
    }
    fn create(manager: &BackgroundManager, schedule: Value) -> BackgroundTask { manager.create_or_update(&json!({"task":{"name":"Scheduled review","conversationId":"chat","schedule":schedule,"taskAction":{"kind":"prompt","prompt":"Review existing evidence"}}}),Some(context("ask-every-time")),false).unwrap() }
    #[test]
    fn interval_boundaries_coalesce_missed_occurrences_and_are_durable() {
        let (root, manager) = fixture(); let task = create(&manager,json!({"kind":"interval","everySeconds":60,"startAt":"2026-01-01T00:00:00Z"}));
        let due = date_ms("2026-01-01T00:00:00Z").unwrap();
        assert_eq!(manager.enqueue_due(due-1).unwrap(),0); assert_eq!(manager.enqueue_due(due+10*60_000).unwrap(),1);
        let runs = manager.runs(None,20).unwrap(); assert_eq!(runs.len(),1); assert_eq!(runs[0].evidence["missedOccurrences"],11);
        assert_eq!(manager.task(&task.id).unwrap().next_due,Some(due+11*60_000));
        assert_eq!(manager.enqueue_due(due+100*60_000).unwrap(),0); drop(manager);
        let manager = BackgroundManager::new(root.clone()).unwrap(); assert_eq!(manager.runs(None,20).unwrap().len(),1);
        drop(manager); let _ = std::fs::remove_dir_all(root);
    }
    #[test]
    fn stable_event_ids_and_step_filters_admit_each_trigger_once() {
        let (root, manager) = fixture(); create(&manager,json!({"kind":"event","name":"training.checkpoint","filters":{"runId":"training-1","metrics.status":"saved"},"stepModulo":500}));
        let event = |id:&str,step:i64| json!({"id":id,"name":"training.checkpoint","data":{"runId":"training-1","step":step,"metrics":{"status":"saved","loss":0.3}}});
        assert_eq!(manager.emit(event("step499",499)).unwrap()["queued"],0);
        assert_eq!(manager.emit(event("step0",0)).unwrap()["queued"],0);
        assert_eq!(manager.emit(event("step500",500)).unwrap()["queued"],1);
        assert_eq!(manager.emit(event("step500",500)).unwrap()["duplicate"],true);
        assert_eq!(manager.emit(event("step1000",1000)).unwrap()["queued"],1);
        assert_eq!(manager.runs(None,20).unwrap().len(),2); drop(manager); let _ = std::fs::remove_dir_all(root);
    }
    #[test]
    fn claims_are_atomic_and_restart_preserves_interrupted_evidence() {
        let (root, manager) = fixture(); let task = create(&manager,json!({"kind":"once","at":"2026-01-01T00:00:00Z"}));
        manager.enqueue_due(date_ms("2026-10-06T00:00:00Z").unwrap()).unwrap(); let run = manager.runs(None,20).unwrap().remove(0);
        assert!(manager.claim(&run.id).unwrap().is_some()); assert!(manager.claim(&run.id).unwrap().is_none());
        assert_eq!(manager.task(&task.id).unwrap().next_due,None); drop(manager);
        let manager = BackgroundManager::new(root.clone()).unwrap(); let recovered = manager.run(&run.id).unwrap();
        assert_eq!(recovered.status,"interrupted"); assert!(recovered.finished_at.is_some()); assert!(manager.claim(&run.id).unwrap().is_none());
        drop(manager); let _ = std::fs::remove_dir_all(root);
    }
    #[test]
    fn editing_cannot_raise_saved_approval_permissions() {
        let (root, manager) = fixture(); let task = create(&manager,json!({"kind":"event","name":"checkpoint"}));
        let edited = manager.create_or_update(&json!({"taskId":task.id,"task":{"name":"Edited","schedule":{"kind":"event","name":"checkpoint"},"taskAction":{"kind":"prompt","prompt":"Continue review"},"context":{"approvalMode":"allow-all"}}}),Some(context("allow-all")),true).unwrap();
        let saved = edited.context.unwrap(); assert_eq!(saved.request.approval_mode.as_str(),"ask-every-time"); assert_eq!(saved.request.skills,vec!["web-dev"]); assert!(saved.request.subagents_enabled);
        assert_eq!(saved.model_profile,"echo-3t"); drop(manager); let _ = std::fs::remove_dir_all(root);
    }
    #[test]
    fn duplicated_events_from_concurrent_deliveries_do_not_duplicate_runs() {
        let (root, manager) = fixture(); create(&manager,json!({"kind":"event","name":"checkpoint"}));
        let handles = (0..8).map(|_| { let manager = manager.clone(); std::thread::spawn(move || manager.emit(json!({"id":"same-event","name":"checkpoint"})).unwrap()) }).collect::<Vec<_>>();
        let accepted = handles.into_iter().map(|handle| handle.join().unwrap()).filter(|result| result["accepted"]==true).count();
        assert_eq!(accepted,1); assert_eq!(manager.runs(None,20).unwrap().len(),1); drop(manager); let _ = std::fs::remove_dir_all(root);
    }
    #[test]
    fn missing_origin_and_invalid_schedules_are_rejected() {
        let (root, manager) = fixture();
        assert!(manager.create_or_update(&json!({"task":{"name":"Unsafe","schedule":{"kind":"interval","everySeconds":60},"taskAction":{"kind":"prompt","prompt":"Do work"}}}),None,false).is_err());
        for schedule in [json!({"kind":"interval","everySeconds":0}),json!({"kind":"event","name":"checkpoint","stepModulo":0}),json!({"kind":"once","at":"tomorrow"}),json!({"kind":"cron","expression":"* * * *"})] { assert!(serde_json::from_value::<Schedule>(schedule).unwrap().validate().is_err()); }
        let token = manager.webhook_token(); assert!(manager.authenticate_webhook(&token)); assert!(!manager.authenticate_webhook("")); assert!(!manager.authenticate_webhook(&format!("{token}x"))); drop(manager); let _ = std::fs::remove_dir_all(root);
    }
    #[test]
    fn queued_cancel_prevents_claim_and_running_cancel_is_durable_before_registration() {
        let (root,manager)=fixture(); let task=create(&manager,json!({"kind":"event","name":"checkpoint"}));
        let queued=manager.run_now(&task.id).unwrap(); manager.cancel(&queued.id).unwrap();
        assert!(manager.claim(&queued.id).unwrap().is_none()); assert_eq!(manager.run(&queued.id).unwrap().status,"cancelled");
        let running=manager.run_now(&task.id).unwrap(); manager.claim(&running.id).unwrap().unwrap();
        manager.cancel(&running.id).unwrap(); assert_eq!(manager.run(&running.id).unwrap().evidence["cancelRequested"],true);
        drop(manager); let _=std::fs::remove_dir_all(root);
    }
    #[test]
    fn paused_jobs_stop_automatic_claims_but_allow_explicit_manual_run() {
        let (root,manager)=fixture(); let task=create(&manager,json!({"kind":"event","name":"checkpoint"}));
        manager.pause(&task.id,true).unwrap(); assert_eq!(manager.emit(json!({"id":"paused-event","name":"checkpoint"})).unwrap()["queued"],0);
        let manual=manager.run_now(&task.id).unwrap(); assert!(manager.claim(&manual.id).unwrap().is_some());
        drop(manager); let _=std::fs::remove_dir_all(root);
    }
    #[test]
    fn pending_heads_are_not_lost_outside_the_visible_history_limit() {
        let (root,manager)=fixture(); create(&manager,json!({"kind":"event","name":"checkpoint"}));
        for number in 0..1002 { manager.emit(json!({"id":format!("checkpoint-{number}"),"name":"checkpoint"})).unwrap(); }
        assert_eq!(manager.runs(None,500).unwrap().len(),500);
        let heads=manager.pending().unwrap(); assert_eq!(heads.len(),1); assert_eq!(heads[0].0.occurrence,"event:checkpoint-0");
        drop(manager); let _=std::fs::remove_dir_all(root);
    }
    #[tokio::test]
    async fn cancel_active_releases_owned_run_and_is_reusable() {
        let (root,manager)=fixture(); let task=create(&manager,json!({"kind":"event","name":"checkpoint"})); let run=manager.run_now(&task.id).unwrap();
        let token=CancellationToken::new(); manager.running.lock().unwrap().insert(run.id.clone(),RunControl{token:token.clone(),prompt:false,uses_gpu:true});
        let cleanup=manager.clone(); let id=run.id.clone();
        let wait=tokio::spawn(async move { token.cancelled().await; cleanup.running.lock().unwrap().remove(&id); });
        assert!(manager.busy_gpu()); manager.cancel_active().await.unwrap(); wait.await.unwrap();
        assert!(!manager.busy_gpu()); assert!(!manager.stopping.load(Ordering::Acquire)); assert!(!manager.closing.load(Ordering::Acquire));
        assert!(manager.run_now(&task.id).is_ok()); drop(manager); let _=std::fs::remove_dir_all(root);
    }
    fn chat_context(id:&str, mode:&str)->BackgroundContext {
        let mut saved=context(mode); saved.request.conversation_id=id.into(); saved
    }
    fn chat_task(manager:&BackgroundManager,id:&str)->BackgroundTask {
        manager.create_or_update(&json!({"task":{"name":format!("{id} private task"),"schedule":{"kind":"event","name":"training.checkpoint"},"taskAction":{"kind":"prompt","prompt":format!("{id} private prompt")}}}),Some(chat_context(id,"ask-every-time")),false).unwrap()
    }
    #[test]
    fn agent_lists_are_isolated_by_chat_even_when_parent_and_side_share_workspace() {
        let (root,manager)=fixture(); let main=chat_task(&manager,"main"); let side=chat_task(&manager,"side");
        manager.run_now(&main.id).unwrap(); manager.run_now(&side.id).unwrap();
        for conversation in ["main","side"] {
            let result=manager.command(&json!({"action":"list"}),Some(chat_context(conversation,"ask-every-time"))).unwrap();
            assert_eq!(result["tasks"].as_array().unwrap().len(),1); assert_eq!(result["runs"].as_array().unwrap().len(),1);
            assert_eq!(result["tasks"][0]["conversationId"],conversation); assert_eq!(result["runs"][0]["conversationId"],conversation);
            assert!(result["webhook"]["token"].is_null());
            let other=if conversation=="main" {"side"} else {"main"};
            for action in ["list","status"] { assert!(manager.command(&json!({"action":action,"conversationId":other}),Some(chat_context(conversation,"allow-all"))).is_err()); }
        }
        let native=manager.command(&json!({"action":"list"}),None).unwrap();
        assert_eq!(native["tasks"].as_array().unwrap().len(),2); assert_eq!(native["runs"].as_array().unwrap().len(),2); assert!(native["webhook"]["token"].is_string());
        drop(manager); let _=std::fs::remove_dir_all(root);
    }
    #[test]
    fn cross_chat_task_ids_cannot_read_mutate_or_swap_to_the_targets_saved_context() {
        let (root,manager)=fixture(); let main=chat_task(&manager,"main"); let side=chat_task(&manager,"side");
        for (caller,target) in [("main",&side),("side",&main)] {
            for action in ["status","pause","resume","delete","run_now","update"] {
                let args=json!({"action":action,"conversationId":caller,"taskId":target.id,"task":{"name":"Injected update","schedule":{"kind":"event","name":"training.checkpoint"},"taskAction":{"kind":"prompt","prompt":"Read another chat"},"context":{"request":{"conversationId":caller,"approvalMode":"allow-all"}}}});
                assert!(manager.command(&args,Some(chat_context(caller,"allow-all"))).is_err(),"{caller} {action}");
            }
            // Direct update also enforces ownership before loading saved execution settings.
            assert!(manager.create_or_update(&json!({"taskId":target.id,"task":{"name":"Direct override","schedule":{"kind":"event","name":"training.checkpoint"},"taskAction":{"kind":"prompt","prompt":"Changed"}}}),Some(chat_context(caller,"allow-all")),true).is_err());
            let preserved=manager.task(&target.id).unwrap(); assert_eq!(preserved.name,target.name); assert!(!preserved.paused); assert_eq!(preserved.context.unwrap().request.approval_mode.as_str(),"ask-every-time");
        }
        assert!(manager.runs(None,10).unwrap().is_empty());
        assert!(manager.command(&json!({"action":"pause","taskId":side.id}),None).is_ok());
        assert!(manager.task(&side.id).unwrap().paused);
        drop(manager); let _=std::fs::remove_dir_all(root);
    }
    #[test]
    fn cross_chat_run_ids_cannot_expose_logs_or_cancel_private_work() {
        let (root,manager)=fixture(); let main=chat_task(&manager,"main"); let side=chat_task(&manager,"side");
        let main_run=manager.run_now(&main.id).unwrap(); let side_run=manager.run_now(&side.id).unwrap();
        for run in [&main_run,&side_run] {
            let directory=root.join("logs").join(&run.id); std::fs::create_dir_all(&directory).unwrap();
            std::fs::write(directory.join("stdout.log"),format!("{} private stdout",run.conversation_id.as_deref().unwrap())).unwrap();
        }
        for (caller,target,own) in [("main",&side_run,&main_run),("side",&main_run,&side_run)] {
            for action in ["status","logs","cancel"] { assert!(manager.command(&json!({"action":action,"runId":target.id}),Some(chat_context(caller,"allow-all"))).is_err(),"{caller} {action}"); }
            assert_eq!(manager.run(&target.id).unwrap().status,"queued");
            let own_logs=manager.command(&json!({"action":"logs","runId":own.id}),Some(chat_context(caller,"ask-every-time"))).unwrap();
            assert_eq!(own_logs["stdout"],format!("{caller} private stdout"));
        }
        let native=manager.command(&json!({"action":"logs","runId":side_run.id}),None).unwrap(); assert_eq!(native["stdout"],"side private stdout");
        drop(manager); let _=std::fs::remove_dir_all(root);
    }
    #[test]
    fn agent_creation_rejects_conflicting_metadata_and_updates_keep_original_policy() {
        let (root,manager)=fixture();
        for top_level in [true,false] {
            let mut args=json!({"action":"create","task":{"name":"Wrong chat","schedule":{"kind":"event","name":"training.checkpoint"},"taskAction":{"kind":"prompt","prompt":"Review"}}});
            if top_level {args["conversationId"]=json!("side");} else {args["task"]["conversationId"]=json!("side");}
            assert!(manager.command(&args,Some(chat_context("main","ask-every-time"))).is_err());
        }
        let task=chat_task(&manager,"main");
        let result=manager.command(&json!({"action":"update","taskId":task.id,"task":{"name":"Updated own task","schedule":{"kind":"event","name":"training.checkpoint"},"taskAction":{"kind":"prompt","prompt":"Continue"},"context":{"request":{"conversationId":"main","approvalMode":"allow-all"}}}}),Some(chat_context("main","allow-all"))).unwrap();
        assert_eq!(result["context"]["request"]["approvalMode"],"ask-every-time"); assert_eq!(result["conversationId"],"main");
        drop(manager); let _=std::fs::remove_dir_all(root);
    }
    #[test]
    fn agent_events_cannot_impersonate_other_chats_or_runtime_events() {
        let (root,manager)=fixture(); chat_task(&manager,"main"); chat_task(&manager,"side");
        for event in [json!({"id":"wrong-root","name":"training.checkpoint","conversationId":"side"}),json!({"id":"wrong-data","name":"training.checkpoint","data":{"conversationId":"side"}}),json!({"id":"generation:reserved:completed","name":"training.checkpoint"}),json!({"id":"reserved-name","name":"background.completed"})] {
            assert!(manager.command(&json!({"action":"emit","event":event}),Some(chat_context("main","allow-all"))).is_err());
        }
        let custom=json!({"action":"emit","event":{"id":"custom-checkpoint","name":"training.checkpoint","data":{"runId":"external-training-1","step":500}}});
        assert_eq!(manager.command(&custom,Some(chat_context("main","ask-every-time"))).unwrap()["queued"],1);
        let own=manager.runs(Some("main"),10).unwrap(); assert_eq!(own.len(),1); assert_eq!(own[0].evidence["event"]["conversationId"],"main");
        assert!(manager.runs(Some("side"),10).unwrap().is_empty());
        assert_eq!(manager.command(&custom,Some(chat_context("main","ask-every-time"))).unwrap()["duplicate"],true);
        // Authenticated external workers keep intentionally shared named workflows.
        assert_eq!(manager.emit(json!({"id":"external-checkpoint","name":"training.checkpoint","data":{"runId":"external-training-1","step":1000}})).unwrap()["queued"],2);
        drop(manager); let _=std::fs::remove_dir_all(root);
    }
}
