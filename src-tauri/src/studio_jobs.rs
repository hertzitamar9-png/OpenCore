//! One durable queue for studio forms and the chat agent. No shell interpolation.
use crate::{model_catalog, music_studio, AppCore};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};
use tauri::Emitter;
use tauri::Manager;
use tokio_util::sync::CancellationToken;
static GPU_RESERVED: AtomicBool = AtomicBool::new(false);
pub fn gpu_reserved() -> bool {
    GPU_RESERVED.load(Ordering::SeqCst)
}
pub(crate) struct GpuReservation;
pub(crate) fn reserve_gpu() -> Result<GpuReservation, String> {
    GPU_RESERVED
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .map(|_| GpuReservation)
        .map_err(|_| "The GPU is reserved by another job or app update".into())
}
impl Drop for GpuReservation {
    fn drop(&mut self) {
        GPU_RESERVED.store(false, Ordering::SeqCst);
    }
}

pub const CATEGORIES: &[&str] = &[
    "music",
    "image",
    "3d",
    "3d-animation",
    "2d-animation",
    "speech",
    "video",
    "tts",
    "voice-cloning",
    "ocr",
    "omni",
    "policy",
];
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StudioRequest {
    pub model_id: String,
    pub prompt: String,
    #[serde(default)]
    pub settings: Value,
    #[serde(default)]
    pub conversation_id: Option<String>,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StudioJob {
    pub id: String,
    pub category: String,
    pub request: StudioRequest,
    pub status: String,
    pub stage: String,
    pub created_at: String,
    pub updated_at: String,
    pub backend_run: Option<String>,
    pub progress: Value,
    pub outputs: Vec<String>,
    pub error: Option<String>,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StudioRuntime {
    pub model_id: String,
    pub python: PathBuf,
    pub source_dir: Option<PathBuf>,
    pub runner: Option<PathBuf>,
}
#[derive(Default)]
struct MusicAdmission {
    request_sent: bool,
    run: Option<String>,
}
fn builtin_service(model_id: &str) -> bool {
    model_id == "yue2" || model_catalog::is_speech_model(model_id)
}
fn builtin_worker(model: &model_catalog::Model) -> bool {
    matches!(model.id.as_str(), "triposr" | "qwen-image-21" | "animation-diffusion-2d")
        || (model.category == "image" && model.backend == "diffusers")
}
fn validate_runtime(model: &model_catalog::Model, runtime: &StudioRuntime) -> Result<(), String> {
    if !CATEGORIES.contains(&model.category.as_str()) || builtin_service(&model.id) {
        return Err("This model uses its built-in service or is not a studio model".into());
    }
    if runtime.model_id != model.id || !runtime.python.is_absolute() || !runtime.python.is_file() {
        return Err("Choose an existing Python interpreter for this model".into());
    }
    if runtime.source_dir.as_ref().is_some_and(|path| !path.is_absolute() || !path.is_dir())
        || runtime.runner.as_ref().is_some_and(|path| !path.is_absolute() || !path.is_file() || path.extension().is_none_or(|extension| extension != "py")) {
        return Err("Choose an existing source folder and Python worker".into());
    }
    if !builtin_worker(model) && runtime.runner.is_none() {
        return Err("This architecture requires an explicit publisher-compatible Python worker".into());
    }
    if (!model.installable || model.id == "triposr") && runtime.source_dir.is_none() {
        return Err("Choose the existing SDK/model source folder for this architecture".into());
    }
    Ok(())
}
fn runtime_from_tool(args:&Value)->Result<StudioRuntime,String>{
    let runtime:StudioRuntime=serde_json::from_value(args["runtime"].clone()).map_err(|e|format!("Provide runtime modelId, python, runner and sourceDir: {e}"))?;
    if args["modelId"].as_str()!=Some(runtime.model_id.as_str()){return Err("runtime.modelId must match modelId".into());}
    Ok(runtime)
}
fn worker_request(root: &Path, job: &StudioJob, runtime: &StudioRuntime) -> Result<Value, String> {
    let mut payload = serde_json::to_value(&job.request).map_err(|error| error.to_string())?;
    let model = model_catalog::model(&job.request.model_id).ok_or("Unknown studio model")?;
    payload["modelRoot"] = json!(root);
    payload["category"] = json!(job.category);
    payload["sourceDir"] = json!(runtime.source_dir);
    payload["protocolVersion"] = json!(1);
    payload["modelPath"] = json!(model.runtime_model_path.as_ref().map(|path| root.join(path)));
    payload["modelDirectory"] = json!(model.runtime_model_path.as_ref().filter(|path| path.starts_with("models/library/"))
        .map(|path| root.join(Path::new(path).components().take(3).collect::<PathBuf>())));
    payload["catalogModel"] = json!({"id":model.id,"category":model.category,"license":model.license,"sourceUrl":model.source_url,"setupUrl":model.setup_url,"artifactIds":model.artifacts});
    Ok(payload)
}
pub struct StudioManager {
    notify: Mutex<Option<Box<dyn Fn(&StudioJob) + Send + Sync>>>,
    save_gate: Mutex<()>,
    db: Mutex<rusqlite::Connection>,
    root: PathBuf,
    running: Mutex<HashMap<String, CancellationToken>>,
    gate: tokio::sync::Mutex<()>,
    closing: AtomicBool,
    watches: Mutex<HashMap<String, crate::process_watch::ProcessWatch>>,
}
impl StudioManager {
    pub fn new(root: PathBuf) -> Result<Arc<Self>, String> {
        std::fs::create_dir_all(&root).map_err(|e| e.to_string())?;
        let db =
            rusqlite::Connection::open(root.join("jobs.sqlite3")).map_err(|e| e.to_string())?;
        db.execute_batch("PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS runtimes(model_id TEXT PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS continuations(id TEXT PRIMARY KEY,payload TEXT NOT NULL,user_entry INTEGER NOT NULL,status TEXT NOT NULL);").map_err(|e|e.to_string())?;
        let this = Arc::new(Self {
            notify: Mutex::new(None),
            save_gate: Mutex::new(()),
            db: Mutex::new(db),
            root,
            running: Mutex::new(HashMap::new()),
            gate: tokio::sync::Mutex::new(()),
            closing: AtomicBool::new(false),
            watches: Mutex::new(HashMap::new()),
        });
        for mut job in this.list()? {
            if active(&job.status) {
                job.status = "interrupted".into();
                job.stage = "Application closed; saved request can be retried".into();
                this.save(&job)?;
            }
        }
        this.db.lock().map_err(|e|e.to_string())?.execute("UPDATE continuations SET status='interrupted' WHERE status IN ('pending','resuming')",[]).map_err(|e|e.to_string())?;
        Ok(this)
    }
    fn save(&self, job: &StudioJob) -> Result<(), String> {
        // Keep event delivery in the same order as durable writes. Otherwise an
        // older running event could arrive after the accepted cancelled event.
        let _save = self.save_gate.lock().map_err(|error| error.to_string())?;
        let mut saved = job.clone();
        {
            let db = self.db.lock().map_err(|error| error.to_string())?;
            let previous: Option<StudioJob> = db.query_row("SELECT payload FROM jobs WHERE id=?1", [&job.id], |row| row.get::<_, String>(0)).ok().and_then(|text| serde_json::from_str(&text).ok());
            // A stale progress/error/completion write must never undo an accepted cancel.
            if let Some(previous) = previous {
                // The backend ID acknowledges a launched worker and cannot be erased
                // by a cancel snapshot captured before that acknowledgement.
                if saved.backend_run.is_none() { saved.backend_run = previous.backend_run; }
                if previous.status == "cancelled" {
                    saved.status = "cancelled".into();
                    saved.error = None;
                    if job.status != "cancelled" || previous.progress["cleanupPending"] == false {
                        saved.stage = previous.stage;
                        saved.progress["cleanupPending"] = previous.progress["cleanupPending"].clone();
                    }
                }
            }
            db.execute("INSERT INTO jobs(id,payload) VALUES(?1,?2) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",rusqlite::params![saved.id,serde_json::to_string(&saved).map_err(|e|e.to_string())?]).map_err(|e|e.to_string())?;
        }
        self.notify(&saved);
        Ok(())
    }
    pub fn attach_app(&self, app: tauri::AppHandle) {
        if let Ok(mut value) = self.notify.lock() {
            *value = Some(Box::new(move |job| {
                let _ = app.emit("opencore-studio-job", job);
                if matches!(job.status.as_str(),"completed"|"failed"|"cancelled") || (job.status=="queued"&&job.created_at==job.updated_at) {
                    if let (Ok(data),Some(core))=(app.path().app_data_dir(),app.try_state::<Arc<AppCore>>()) {
                        if matches!(job.status.as_str(),"completed"|"failed"|"cancelled") {
                            let event=json!({"id":format!("studio:{}:{}",job.id,job.status),"name":format!("studio.{}",job.status),
                                "data":{"jobId":job.id,"category":job.category,"modelId":job.request.model_id,"conversationId":job.request.conversation_id,"status":job.status,"outputs":job.outputs,"error":job.error}});
                            if let Err(error)=core.background.emit(event) { core.store.log("warn","background-events",&error); }
                            if job.status=="completed" && !job.outputs.is_empty() {
                                if let Err(error)=core.runtime_setup.record_inference(job) { core.store.log("warn","runtime-evidence",&error); }
                                let files=core.files.clone(); let log=core.store.clone(); let notice=app.clone();
                                let conversation=job.request.conversation_id.clone().unwrap_or_else(||format!("studio:{}",job.category));
                                let job_id=job.id.clone(); let paths:Vec<PathBuf>=job.outputs.iter().map(PathBuf::from).collect();
                                tauri::async_runtime::spawn_blocking(move || match files.register_outputs(&conversation,&job_id,&paths) {
                                    Ok(records)=>{let _=notice.emit("opencore-file-changes",records);},
                                    Err(error)=>log.log("warn","file-history",&format!("Could not index studio outputs: {error}")),
                                });
                            }
                        }
                        if crate::agent_platform::configuration(&core.store).is_ok_and(|config|config.activity_enabled) {
                            let summary=format!("{} generation {}: {}",job.category,job.status,job.request.prompt.chars().take(220).collect::<String>());
                            let settings=if job.request.settings.to_string().len()<=32768 {job.request.settings.clone()} else {json!({"sha256":crate::dev_tool::sha256(job.request.settings.to_string().as_bytes()),"note":"Full settings are preserved in the studio job record; inspect the job by jobId."})};
                            let progress=if job.progress.to_string().len()<=8192 {job.progress.clone()}else {json!({"note":"Full progress is preserved in the studio job record."})};
                            let details=json!({"jobId":job.id,"category":job.category,"modelId":job.request.model_id,"status":job.status,"prompt":job.request.prompt.chars().take(4096).collect::<String>(),"settings":settings,"progress":progress,"outputs":job.outputs.iter().take(16).map(|path|path.chars().take(1024).collect::<String>()).collect::<Vec<_>>(),"error":job.error.as_ref().map(|text|text.chars().take(1024).collect::<String>()),"createdAt":job.created_at,"updatedAt":job.updated_at,"exactRecord":"studio/jobs.sqlite3"});
                            let mut event=crate::agent_platform::ActivityEvent::new(&job.category,&job.status,&summary,"studio",details);
                            event.id=format!("studio:{}:{}",job.id,job.status);
                            event.timestamp=job.updated_at.clone();event.conversation_id=job.request.conversation_id.clone();
                            if let Err(error)=crate::agent_platform::record_activity(&data,&event){core.store.log("warn","activity",&error);}
                        }
                    }
                }
            }));
        }
    }
    fn notify(&self, job: &StudioJob) {
        if let Ok(notify) = self.notify.lock() {
            if let Some(notify) = notify.as_ref() {
                notify(job);
            }
        }
    }
    pub fn list(&self) -> Result<Vec<StudioJob>, String> {
        let db = self.db.lock().map_err(|e| e.to_string())?;
        let mut stmt = db
            .prepare("SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 200")
            .map_err(|e| e.to_string())?;
        let values = stmt
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(|e| e.to_string())?;
        values
            .map(|v| {
                serde_json::from_str(&v.map_err(|e| e.to_string())?).map_err(|e| e.to_string())
            })
            .collect()
    }
    pub fn get(&self, id: &str) -> Result<StudioJob, String> {
        let db = self.db.lock().map_err(|e| e.to_string())?;
        let data: String = db
            .query_row("SELECT payload FROM jobs WHERE id=?1", [id], |row| {
                row.get(0)
            })
            .map_err(|_| "Studio job not found".to_string())?;
        serde_json::from_str(&data).map_err(|e| e.to_string())
    }
    pub fn busy(&self) -> bool {
        self.running.lock().is_ok_and(|jobs| !jobs.is_empty())
    }
    pub fn continuation_pending(&self) -> bool {
        self.db
            .lock()
            .ok()
            .and_then(|db| {
                db.query_row(
                    "SELECT count(*) FROM continuations WHERE status IN ('pending','resuming')",
                    [],
                    |row| row.get::<_, i64>(0),
                )
                .ok()
            })
            .is_none_or(|n| n > 0)
    }
    pub fn arm_continuation(
        &self,
        core: &AppCore,
        id: &str,
        request: &crate::models::ChatSendRequest,
    ) -> Result<(), String> {
        let job = self.get(id)?;
        if job.request.conversation_id.as_deref() != Some(request.conversation_id.as_str()) {
            return Err("Background job conversation mismatch".into());
        }
        let user_entry = core
            .store
            .latest_user_entry(&request.conversation_id)?
            .ok_or("No originating user turn")?;
        let payload = json!({"request":request,"profile":core.runtime.profile()});
        self.db.lock().map_err(|e|e.to_string())?.execute("INSERT OR IGNORE INTO continuations(id,payload,user_entry,status) VALUES(?1,?2,?3,'pending')",rusqlite::params![id,payload.to_string(),user_entry]).map_err(|e|e.to_string())?;
        Ok(())
    }
    fn claim_continuation(&self, id: &str, latest_user: i64) -> Result<Option<Value>, String> {
        let db = self.db.lock().map_err(|e| e.to_string())?;
        let candidate = db.query_row(
            "SELECT payload,user_entry FROM continuations WHERE id=?1 AND status='pending'",
            [id],
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?)),
        );
        let Ok((payload, origin)) = candidate else {
            return Ok(None);
        };
        let state = if latest_user == origin {
            "resuming"
        } else {
            "superseded"
        };
        if db
            .execute(
                "UPDATE continuations SET status=?2 WHERE id=?1 AND status='pending'",
                rusqlite::params![id, state],
            )
            .map_err(|e| e.to_string())?
            != 1
            || state == "superseded"
        {
            return Ok(None);
        };
        Ok(Some(
            serde_json::from_str(&payload).map_err(|e| e.to_string())?,
        ))
    }
    async fn resume_continuation(&self, core: Arc<AppCore>, app: tauri::AppHandle, job: StudioJob) {
        let Some(conversation) = job.request.conversation_id.as_deref() else {
            return;
        };
        if !matches!(job.status.as_str(), "completed" | "failed") {
            if let Ok(db) = self.db.lock() {
                let _ = db.execute(
                    "UPDATE continuations SET status='cancelled' WHERE id=?1 AND status='pending'",
                    [&job.id],
                );
            }
            return;
        }
        // Only OS/app code runs while waiting. The text model stays unloaded.
        loop {
            if self.closing.load(Ordering::SeqCst) || core.update_in_progress.load(Ordering::Acquire) {
                return;
            }
            let chats = core.active_chats.lock().is_ok_and(|chats| chats.is_empty());
            if !self.busy() && !core.runtime_setup.busy() && !core.claude_bridge.busy() && chats && !gpu_reserved() && !core.speech.is_active().await {
                break;
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
        let latest_user = core
            .store
            .latest_user_entry(conversation)
            .ok()
            .flatten()
            .unwrap_or(-1);
        let payload = match self.claim_continuation(&job.id, latest_user) {
            Ok(Some(value)) => value,
            _ => return,
        };
        let result = async {
            core.ensure_not_updating()?;
            let request =
                serde_json::from_value(payload["request"].clone()).map_err(|e| e.to_string())?;
            core.runtime.select_profile(
                payload["profile"]
                    .as_str()
                    .ok_or("Missing original text model")?,
            )?;
            crate::resume_background_job(core.clone(), app, request, job.clone()).await
        }
        .await;
        if let Ok(db) = self.db.lock() {
            let _ = db.execute(
                "UPDATE continuations SET status=?2 WHERE id=?1",
                rusqlite::params![job.id, if result.is_ok() { "done" } else { "failed" }],
            );
        }
        if let Err(error) = result {
            let _ = core.store.add_timeline(
                conversation,
                "error",
                "system",
                "OpenCore",
                "Background continuation",
                &format!("The job finished, but ECHO could not resume: {error}"),
                &json!({"studioJobId":job.id}),
            );
        }
    }
    pub async fn shutdown(&self) {
        self.closing.store(true, Ordering::SeqCst);
        if let Ok(jobs) = self.running.lock() {
            for token in jobs.values() {
                token.cancel();
            }
        }
        for _ in 0..120 {
            if !self.busy() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
    }
    /// Cancel generation jobs without permanently closing the manager, as an
    /// in-place application update may fail and leave this process running.
    pub async fn cancel_active(&self) -> Result<(), String> {
        {
            let jobs = self.running.lock().map_err(|error| error.to_string())?;
            for token in jobs.values() { token.cancel(); }
        }
        for _ in 0..120 {
            if !self.busy() { return Ok(()); }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        Err("A studio task did not stop within 30 seconds. OpenCore stopped other model work, but the update was not installed.".into())
    }
    fn update(
        &self,
        id: &str,
        status: &str,
        stage: &str,
        progress: Value,
        outputs: Vec<String>,
        error: Option<String>,
    ) -> Result<(), String> {
        let mut job = self.get(id)?;
        job.status = status.into();
        job.stage = stage.into();
        job.progress = progress;
        job.outputs = outputs;
        job.error = error;
        job.updated_at = chrono::Utc::now().to_rfc3339();
        self.save(&job)
    }
    pub fn runtime(&self, id: &str) -> Result<Option<StudioRuntime>, String> {
        let db = self.db.lock().map_err(|e| e.to_string())?;
        let mut stmt = db
            .prepare("SELECT payload FROM runtimes WHERE model_id=?1")
            .map_err(|e| e.to_string())?;
        let mut rows = stmt.query([id]).map_err(|e| e.to_string())?;
        let runtime: Option<StudioRuntime> = rows.next()
            .map_err(|e| e.to_string())?
            .map(|row| {
                row.get::<_, String>(0)
                    .map_err(|e| e.to_string())
                    .and_then(|v| serde_json::from_str(&v).map_err(|e| e.to_string()))
            })
            .transpose()?;
        Ok(runtime.filter(|runtime| model_catalog::model(id)
            .is_some_and(|model| validate_runtime(&model, runtime).is_ok())))
    }
    /// A connection is an explicit user-selected executable and SDK folder,
    /// not a model install receipt. Recheck paths after a runtime is removed.
    pub fn runtime_connected(&self, id: &str) -> bool {
        self.runtime(id).ok().flatten().is_some()
    }
    pub fn configured_models(&self) -> Result<Vec<model_catalog::Model>, String> {
        let ids = {
            let db = self.db.lock().map_err(|error| error.to_string())?;
            let mut statement = db.prepare("SELECT model_id FROM runtimes ORDER BY model_id").map_err(|error| error.to_string())?;
            let rows = statement.query_map([], |row| row.get::<_, String>(0)).map_err(|error| error.to_string())?;
            rows.collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())?
        };
        Ok(ids.into_iter().filter(|id| self.runtime_connected(id)).filter_map(|id| model_catalog::model(&id)).collect())
    }
    pub fn available_models(&self, root: &Path) -> Result<Vec<model_catalog::Model>, String> {
        let mut models = model_catalog::installed_models(root)?;
        for model in self.configured_models()? {
            if !models.iter().any(|installed| installed.id == model.id) { models.push(model); }
        }
        Ok(models.into_iter().filter(|model| CATEGORIES.contains(&model.category.as_str())).collect())
    }
    fn require_available_model(&self, root: &Path, model: &model_catalog::Model) -> Result<(), String> {
        if model_catalog::require_installed(root, &model.id).is_ok() { return Ok(()); }
        if let Some(runtime) = self.runtime(&model.id)? {
            if runtime.runner.is_some() && runtime.source_dir.is_some() { return Ok(()); }
        }
        Err("Install the selected weights or connect an explicit worker and its existing SDK/model folder in the studio".into())
    }
    pub fn configure(&self, runtime: StudioRuntime) -> Result<(), String> {
        if self.busy() {
            return Err("Wait for studio jobs before changing a runtime".into());
        }
        let model = model_catalog::model(&runtime.model_id).ok_or("Unknown studio model")?;
        validate_runtime(&model, &runtime)?;
        self.db.lock().map_err(|e|e.to_string())?.execute("INSERT INTO runtimes(model_id,payload) VALUES(?1,?2) ON CONFLICT(model_id) DO UPDATE SET payload=excluded.payload",rusqlite::params![runtime.model_id,serde_json::to_string(&runtime).map_err(|e|e.to_string())?]).map_err(|e|e.to_string())?;
        Ok(())
    }
    pub fn cancel(&self, id: &str) -> Result<(), String> {
        let token = self.running.lock().map_err(|e| e.to_string())?.get(id).cloned();
        if let Some(token) = token {
            token.cancel();
            let mut job = self.get(id)?;
            if active(&job.status) {
                job.status = "cancelled".into();
                job.stage = "Cancelled · releasing resources".into();
                job.error = None;
                job.progress["cleanupPending"] = json!(true);
                job.updated_at = chrono::Utc::now().to_rfc3339();
                self.save(&job)?;
            }
            Ok(())
        } else {
            Err("This job is no longer active".into())
        }
    }
    pub fn submit(
        self: &Arc<Self>,
        core: Arc<AppCore>,
        app: tauri::AppHandle,
        mut request: StudioRequest,
    ) -> Result<StudioJob, String> {
        core.ensure_not_updating()?;
        let mut process_watch = None;
        let category = if request.model_id == "background-wait" {
            if request.settings["pid"]
                .as_u64()
                .is_none_or(|v| v == 0 || v > u32::MAX as u64)
                || request.settings["created"].as_u64().is_none()
            {
                return Err("Invalid process wait".into());
            }
            let watch = crate::process_watch::ProcessWatch::open(
                request.settings["pid"].as_u64().unwrap() as u32,
            )?;
            if Some(watch.created) != request.settings["created"].as_u64() {
                return Err("Process identity changed before the wait was queued".into());
            }
            process_watch = Some(watch);
            "background".into()
        } else {
            let model = model_catalog::model(&request.model_id).ok_or("Unknown model")?;
            self.require_available_model(core.runtime.install_root(), &model)?;
            validate_request(&model.category, &request)?;
            model.category
        };
        if request.settings.is_null() {
            request.settings = json!({});
        }
        let now = chrono::Utc::now().to_rfc3339();
        let job = StudioJob {
            id: uuid::Uuid::new_v4().to_string(),
            category,
            request,
            status: "queued".into(),
            stage: "Waiting for chat and GPU".into(),
            created_at: now.clone(),
            updated_at: now,
            backend_run: None,
            progress: json!({}),
            outputs: vec![],
            error: None,
        };
        let token = CancellationToken::new();
        {
            let mut running = self.running.lock().map_err(|e| e.to_string())?;
            // Serialize job registration with the updater's cancellation pass.
            // If the update flag was set before this lock, reject; if it is set
            // after this check, the updater will see and cancel this token.
            core.ensure_not_updating()?;
            self.save(&job)?;
            if let Some(watch) = process_watch {
                self.watches
                    .lock()
                    .map_err(|e| e.to_string())?
                    .insert(job.id.clone(), watch);
            }
            running.insert(job.id.clone(), token.clone());
        }
        let this = self.clone();
        let id = job.id.clone();
        tauri::async_runtime::spawn(async move {
            let result = this.run(&core, &app, &id, &token).await;
            let result = if token.is_cancelled() { Err("Generation cancelled".to_string()) } else { result };
            if let Err(error) = result {
                let saved = this.get(&id).ok();
                let mut progress = saved.as_ref().map(|job| job.progress.clone()).unwrap_or(json!({}));
                progress["cleanupPending"] = json!(false);
                let _ = this.update(
                    &id,
                    if token.is_cancelled() {
                        "cancelled"
                    } else {
                        "failed"
                    },
                    if token.is_cancelled() {
                        "Cancelled"
                    } else {
                        "Generation failed"
                    },
                    progress,
                    saved.map(|j| j.outputs).unwrap_or_default(),
                    (!token.is_cancelled()).then_some(error),
                );
            }
            if let Ok(mut running) = this.running.lock() {
                running.remove(&id);
            }
            if let Ok(mut watches) = this.watches.lock() {
                watches.remove(&id);
            }
            if let Ok(job) = this.get(&id) {
                if let Some(conversation) = &job.request.conversation_id {
                    let studio = if job.category == "music" {
                        "Music Studio"
                    } else if job.category == "background" {
                        "Background jobs"
                    } else if matches!(job.category.as_str(),"video"|"tts"|"voice-cloning"|"ocr"|"omni"|"policy") {
                        "Media Studio"
                    } else {
                        "Game Dev Studio"
                    };
                    let text = if job.status == "completed" {
                        format!(
                            "Generation complete. Open {studio} to view the output. Job: {}",
                            job.id
                        )
                    } else {
                        format!(
                            "Generation {}. Open {studio} for details. {}",
                            job.status,
                            job.error.as_deref().unwrap_or("")
                        )
                    };
                    let _ = core.store.add_timeline(
                        conversation,
                        "message",
                        "assistant",
                        "OpenCore",
                        studio,
                        &text,
                        &json!({"studioJobId":job.id,"studioReceipt":true,"status":job.status}),
                    );
                    this.notify(&job);
                }
                this.resume_continuation(core.clone(), app.clone(), job)
                    .await;
            }
        });
        Ok(job)
    }
    async fn run(
        &self,
        core: &Arc<AppCore>,
        app: &tauri::AppHandle,
        id: &str,
        token: &CancellationToken,
    ) -> Result<(), String> {
        let _gate = tokio::select! {guard=self.gate.lock()=>guard,_=token.cancelled()=>return Err("Cancelled before generation".into())};
        loop {
            let chats_active = !core
                .active_chats
                .lock()
                .map_err(|e| e.to_string())?
                .is_empty()
                || !core
                    .live_generation_runs
                    .lock()
                    .map_err(|e| e.to_string())?
                    .is_empty();
            if !chats_active && !core.runtime_setup.busy() && !core.claude_bridge.busy() && !gpu_reserved() && !core.speech.is_active().await {
                break;
            }
            tokio::select! {_=token.cancelled()=>return Err("Cancelled before generation".into()),_=tokio::time::sleep(Duration::from_millis(200))=>{}}
        }
        let job = self.get(id)?;
        if job.category != "background" {
            let model = model_catalog::model(&job.request.model_id).ok_or("Unknown studio model")?;
            self.require_available_model(core.runtime.install_root(), &model)?;
        }
        // Preflight before releasing text weights. Never stop an unrelated backend.
        let runtime = if job.category == "background" || builtin_service(&job.request.model_id) {
            None
        } else {
            Some(
                self.runtime(&job.request.model_id)?
                    .ok_or("Connect this model's publisher runtime in the studio before generating")?,
            )
        };
        if let Some(input) = job.request.settings["inputPath"].as_str() {
            if !Path::new(input).is_absolute() || !Path::new(input).is_file() {
                return Err("The selected studio input file is missing or is not an absolute file path".into());
            }
        }
        let music = music_studio::music_studio_status().await;
        if music.running
            && (music.model_loaded
                || music_studio::request("GET", "/api/status", None)
                    .await
                    .is_ok_and(|s| music_studio::worker_active(&s)))
        {
            return Err("Music Studio has an active or loaded model. Finish it or unload it before this job.".into());
        }
        let snapshot = core.runtime.snapshot();
        if snapshot.status == "running"
            && snapshot.model_pid.is_none()
            && snapshot.echo_pid.is_none()
        {
            return Err("An externally managed text runtime is active. Stop it before starting a studio job.".into());
        }
        self.update(
            id,
            "starting",
            "Preparing generation",
            json!({}),
            vec![],
            None,
        )?;
        let _reservation = reserve_gpu()?;
        let text_runtime = core.runtime.clone();
        tauri::async_runtime::spawn_blocking(move || text_runtime.stop())
            .await
            .map_err(|e| e.to_string())??;
        if token.is_cancelled() {
            return Err("Cancelled".into());
        }
        core.vision.stop();
        core.reflex.stop();
        core.speech.release_idle_model().await?;
        if job.category == "background" {
            let watch = self
                .watches
                .lock()
                .map_err(|e| e.to_string())?
                .remove(id)
                .ok_or("Original process handle is unavailable")?;
            if Some(watch.created) != job.request.settings["created"].as_u64() {
                return Err(
                    "The original process exited; that PID now belongs to another process".into(),
                );
            }
            self.update(
                id,
                "running",
                "Waiting without a loaded text model",
                json!({"pid":job.request.settings["pid"]}),
                vec![],
                None,
            )?;
            loop {
                if token.is_cancelled() {
                    return Err("Wait cancelled; the observed process was left running".into());
                }
                if let Some(code) = watch.exit_code()? {
                    self.update(
                        id,
                        if code == 0 { "completed" } else { "failed" },
                        "Observed process exited",
                        json!({"pid":job.request.settings["pid"],"exitCode":code}),
                        vec![],
                        (code != 0).then(|| format!("Observed process exited with code {code}")),
                    )?;
                    return Ok(());
                }
                tokio::select! {_=token.cancelled()=>{},_=tokio::time::sleep(Duration::from_millis(500))=>{}}
            }
        } else if job.request.model_id == "yue2" {
            let mut admission = MusicAdmission::default();
            let result = self.run_music(&job, token, &mut admission).await;
            if result.is_err() && admission.request_sent {
                // Do not release the GPU lease on a timer or an unknown HTTP result.
                // Only cancel the run created here, then verify worker and model cleanup.
                // A lost response or failed DB write does not prove the worker
                // never started. Keep the lease even without an acknowledged ID;
                // observe unknown runs without cancelling someone else's work.
                self.wait_music_cleanup(id, admission.run.as_deref(), token).await;
            }
            result
        } else if model_catalog::is_speech_model(&job.request.model_id) {
            let result = self.run_speech(core, &job, token).await;
            core.speech.release_idle_model().await?;
            result
        } else {
            self.run_asset(core, app, &job, runtime.unwrap(), token)
                .await
        }
    }
    async fn wait_music_cleanup(&self, id: &str, run: Option<&str>, token: &CancellationToken) {
        let mut cancellation_sent = false;
        let mut cleanup_notice = false;
        loop {
            let cleaned = match music_studio::request("GET", "/api/status", None).await {
                Ok(status) if music_studio::worker_active(&status) && status["worker_finished"] != true => {
                    if run.is_some_and(|run| status["run"].as_str() == Some(run)) && !cancellation_sent {
                        cancellation_sent = music_studio::request("POST", "/api/cancel", Some(&json!({"expectedRun":run}))).await.is_ok();
                    }
                    false
                }
                Ok(_) => music_studio::request("POST", "/api/model/unload", Some(&json!({}))).await.is_ok()
                    && music_studio::request("GET", "/api/info", None).await.is_ok_and(|info| info["model_loaded"] == false && info["worker_active"] != true),
                Err(_) => false,
            };
            if cleaned { return; }
            if !cleanup_notice {
                if let Ok(job) = self.get(id) {
                    let mut progress = job.progress;
                    progress["cleanupPending"] = json!(true);
                    let _ = self.update(id, if token.is_cancelled() {"cancelled"} else {"running"},
                        if token.is_cancelled() {"Cancelled · waiting for resources to be released"} else {"Waiting for Music Studio resources to be released"}, progress, job.outputs, None);
                }
                cleanup_notice = true;
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    }
    async fn run_music(&self, job: &StudioJob, token: &CancellationToken, admission: &mut MusicAdmission) -> Result<(), String> {
        if token.is_cancelled() { return Err("Cancelled before Music Studio started".into()); }
        music_studio::start_music_studio_unchecked().await?;
        if token.is_cancelled() { return Err("Cancelled before Music Studio started".into()); }
        let current = music_studio::request("GET", "/api/status", None).await?;
        if music_studio::worker_active(&current) {
            return Err("Music Studio already has an active generation".into());
        }
        admission.request_sent = true;
        let result =
            music_studio::request("POST", "/api/generate", Some(&job.request.settings)).await?;
        let run = result["run"]
            .as_str()
            .ok_or("Music Studio did not return a generation ID")?
            .to_string();
        if run.is_empty() || run.contains(['/', '\\', ':']) || run == "." || run == ".." {
            return Err("Music Studio returned an unsafe generation folder".into());
        }
        admission.run = Some(run.clone());
        let mut saved = self.get(&job.id)?;
        saved.backend_run = Some(run.clone());
        self.save(&saved)?;
        let mut cancellation_sent = false;
        loop {
            if token.is_cancelled() && !cancellation_sent {
                music_studio::request("POST", "/api/cancel", Some(&json!({"expectedRun":run}))).await?;
                cancellation_sent = true;
            }
            let status = music_studio::request("GET", "/api/status", None).await?;
            if status["run"].as_str() != Some(&run) {
                return Err("Music Studio switched to a different generation".into());
            }
            let state = status["status"].as_str().unwrap_or("unknown");
            // Cancelling inside the embedded studio must cancel its OpenCore job too.
            if state == "cancelled" { token.cancel(); }
            let stage = status["stage"].as_str().unwrap_or("Generating music");
            let dir = music_studio::root().join("studio-output").join(&run);
            let outputs = output_files(&dir)?;
            match state {
                "done" => {
                    if music_studio::worker_active(&status) {
                        self.update(&job.id, "running", "Finishing music and releasing resources", status.clone(), outputs, None)?;
                        tokio::time::sleep(Duration::from_millis(250)).await;
                        continue;
                    }
                    if outputs.is_empty() {
                        return Err("Music Studio finished without output files".into());
                    }
                    music_studio::request("POST", "/api/model/unload", Some(&json!({}))).await?;
                    self.update(&job.id, "completed", stage, status.clone(), outputs, None)?;
                    return Ok(());
                }
                "error" | "cancelled" => {
                    if music_studio::worker_active(&status) && status["worker_finished"] != true {
                        self.update(&job.id, if token.is_cancelled() {"cancelled"} else {"running"},
                            if token.is_cancelled() {"Cancelled · releasing resources"} else {"Releasing resources after a Music Studio error"}, json!({"cleanupPending":true}), outputs, None)?;
                        tokio::time::sleep(Duration::from_millis(250)).await;
                        continue;
                    }
                    let error = status["error"].as_str().unwrap_or(stage).to_string();
                    let _ =
                        music_studio::request("POST", "/api/model/unload", Some(&json!({}))).await;
                    return Err(error);
                }
                "running" => {
                    self.update(&job.id, "running", stage, status.clone(), outputs, None)?;
                }
                _ => return Err(format!("Unexpected Music Studio state: {state}")),
            }
            if cancellation_sent {
                tokio::time::sleep(Duration::from_millis(250)).await;
            } else {
                tokio::select! { _=token.cancelled()=>{}, _=tokio::time::sleep(Duration::from_secs(1))=>{} }
            }
        }
    }
    async fn run_speech(
        &self,
        core: &Arc<AppCore>,
        job: &StudioJob,
        token: &CancellationToken,
    ) -> Result<(), String> {
        let path = PathBuf::from(
            job.request.settings["inputPath"]
                .as_str()
                .ok_or("Select an audio file")?,
        );
        if tokio::fs::metadata(&path)
            .await
            .map_err(|e| e.to_string())?
            .len()
            > 18 * 1024 * 1024
        {
            return Err("Audio attachment exceeds 18 MiB".into());
        }
        let bytes = tokio::fs::read(&path).await.map_err(|e| e.to_string())?;
        if bytes.len() > 18 * 1024 * 1024 {
            return Err("Audio attachment exceeds 18 MiB".into());
        }
        core.speech.set_model(&job.request.model_id).await?;
        let session = core.speech.start_file().await?;
        use base64::Engine;
        let encoded = base64::engine::general_purpose::STANDARD.encode(bytes);
        let result = tokio::select! {r=core.speech.transcribe(&session,&encoded)=>r,_=token.cancelled()=>{core.speech.cancel(&session).await;return Err("Cancelled".into())}}?;
        self.update(
            &job.id,
            "completed",
            "Audio transcribed",
            result,
            vec![],
            None,
        )
    }
    async fn run_asset(
        &self,
        core: &Arc<AppCore>,
        app: &tauri::AppHandle,
        job: &StudioJob,
        runtime: StudioRuntime,
        token: &CancellationToken,
    ) -> Result<(), String> {
        let dir = self.root.join(&job.id);
        std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        let payload = worker_request(core.runtime.install_root(), job, &runtime)?;
        let request_file = dir.join("request.json");
        std::fs::write(&request_file, serde_json::to_vec(&payload).unwrap())
            .map_err(|e| e.to_string())?;
        let worker = runtime.runner.unwrap_or(
            app.path()
                .resource_dir()
                .map_err(|e| e.to_string())?
                .join("studio/asset_worker.py"),
        );
        if !worker.is_file() {
            return Err("Studio worker is missing; reinstall the app".into());
        }
        let log = std::fs::File::create(dir.join("generation.log")).map_err(|e| e.to_string())?;
        let mut command = tokio::process::Command::new(runtime.python);
        command
            .args(["-u"])
            .arg(worker)
            .arg("--request")
            .arg(request_file)
            .arg("--output")
            .arg(&dir)
            .env("HF_HUB_OFFLINE", "1")
            .env("TRANSFORMERS_OFFLINE", "1")
            .stdin(std::process::Stdio::null())
            .stdout(log.try_clone().map_err(|e| e.to_string())?)
            .stderr(log)
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(0x0800_0000);
        let mut child = command.spawn().map_err(|e| e.to_string())?;
        #[cfg(windows)]
        if let Some(handle) = child.raw_handle() {
            crate::child_guard::adopt_handle(handle);
        }
        self.update(
            &job.id,
            "running",
            "Running studio model",
            json!({"pid":child.id()}),
            vec![],
            None,
        )?;
        let status = loop {
            tokio::select! {
                s=child.wait()=>break s.map_err(|e|e.to_string())?,
                _=token.cancelled()=>{let _=child.kill().await;return Err("Generation cancelled; partial files retained".into())},
                _=tokio::time::sleep(Duration::from_secs(1))=>{
                    let progress_file=dir.join("progress.json");
                    if progress_file.metadata().is_ok_and(|m|m.len()<64*1024) {
                        if let Ok(data)=std::fs::read(&progress_file) {
                            if let Ok(value)=serde_json::from_slice::<Value>(&data) {
                                let stage=value["stage"].as_str().unwrap_or("Running studio model");
                                self.update(&job.id,"running",stage,value.clone(),output_files(&dir)?,None)?;
                            }
                        }
                    }
                }
            }
        };
        if !status.success() {
            let detail = std::fs::read_to_string(dir.join("generation.log"))
                .unwrap_or_default()
                .chars()
                .rev()
                .take(2000)
                .collect::<String>()
                .chars()
                .rev()
                .collect::<String>();
            return Err(format!("Studio runtime failed: {detail}"));
        }
        let outputs = output_files(&dir)?;
        if outputs.is_empty() {
            return Err("The studio runtime returned no generated files".into());
        }
        self.update(
            &job.id,
            "completed",
            "Generation complete",
            json!({}),
            outputs,
            None,
        )
    }
}
fn active(status: &str) -> bool {
    matches!(status, "queued" | "starting" | "running")
}
pub fn validate_request(category: &str, request: &StudioRequest) -> Result<(), String> {
    if !CATEGORIES.contains(&category) {
        return Err("This model is not a studio generator".into());
    }
    if request.prompt.trim().is_empty() || request.prompt.len() > 64 * 1024 {
        return Err("Enter a prompt of at most 64 KiB".into());
    }
    if !request.settings.is_null() && !request.settings.is_object() {
        return Err("Settings must be a JSON object".into());
    }
    if request.settings.to_string().len() > 128 * 1024 {
        return Err("Generation settings are too large".into());
    }
    if category == "music" && request.model_id == "yue2" {
        for field in ["style", "lyrics"] {
            if request.settings[field]
                .as_str()
                .is_none_or(|s| s.trim().is_empty())
            {
                return Err(format!("Music generation requires {field} as actual nonempty text. Compose the content, not a JSON schema. Example settings: {{\"title\":\"Tomorrow\",\"style\":\"Upbeat synth pop with clear vocals\",\"lyrics\":\"[Verse] We build tomorrow, one bright idea at a time\",\"cot\":\"full\",\"takes\":1,\"memory\":{{\"quantization\":\"none\",\"offload_ar\":true}}}}"));
            }
        }
        if request.settings["memory"]["quantization"]
            .as_str()
            .is_some_and(|v| v != "none")
        {
            return Err(
                "Studio generation preserves model precision; quantization is not enabled".into(),
            );
        }
    }
    validate_media_settings(category, &request.settings)?;
    Ok(())
}
fn validate_media_settings(category: &str, settings: &Value) -> Result<(), String> {
    let needs_input = matches!(category, "speech" | "voice-cloning" | "ocr" | "omni" | "policy")
        || (category == "video" && settings["mode"] == "image-to-video");
    if needs_input && settings["inputPath"].as_str().is_none_or(|path| path.trim().is_empty()) {
        return Err(format!("{category} requires inputPath as a nonempty file path"));
    }
    let numeric: &[(&str, f64, f64, bool)] = &[
        ("seed", 0.0, u32::MAX as f64, true), ("fps", 1.0, 120.0, true), ("frameCount", 1.0, 2400.0, true),
        ("width", 128.0, 4096.0, true), ("height", 128.0, 4096.0, true), ("steps", 1.0, 256.0, true),
        ("guidanceScale", 0.0, 30.0, false), ("speed", 0.25, 4.0, false), ("sampleRate", 8000.0, 192000.0, true),
        ("pageStart", 1.0, 100000.0, true), ("pageEnd", 0.0, 100000.0, true), ("maxTokens", 1.0, 32768.0, true),
        ("temperature", 0.0, 2.0, false), ("actionHorizon", 1.0, 1024.0, true), ("controlRateHz", 1.0, 500.0, false),
    ];
    for (name, min, max, integer) in numeric {
        if let Some(value) = settings.get(*name) {
            if value.as_f64().is_none_or(|number| !number.is_finite() || number < *min || number > *max || (*integer && number.fract() != 0.0)) {
                return Err(format!("Invalid {name}: expected {} from {min} to {max}", if *integer {"integer"} else {"number"}));
            }
        }
    }
    for name in ["language", "voice", "referenceText", "embodiment", "normalizationKey", "negativePrompt"] {
        if settings.get(name).is_some_and(|value| value.as_str().is_none()) { return Err(format!("{name} must be actual text")); }
    }
    if settings.get("preserveLayout").is_some_and(|value| !value.is_boolean()) { return Err("preserveLayout must be a boolean".into()); }
    if category == "ocr" && settings["pageEnd"].as_u64().is_some_and(|last| last > 0 && last < settings["pageStart"].as_u64().unwrap_or(1)) {
        return Err("Last page must be at or after first page".into());
    }
    let formats: &[&str] = match category {
        "video" => &["mp4", "webm", "gif"], "tts" | "voice-cloning" => &["wav", "flac", "mp3"],
        "ocr" => &["txt", "md", "json"], "omni" => &["txt", "json", "wav"], "policy" => &["json", "npz"], _ => &[],
    };
    if !formats.is_empty() && settings.get("outputFormat").is_some_and(|value| value.as_str().is_none_or(|format| !formats.contains(&format))) {
        return Err(format!("Unsupported {category} output format"));
    }
    if category == "video" && settings.get("mode").is_some_and(|value| value.as_str().is_none_or(|mode| !["text-to-video", "image-to-video"].contains(&mode))) {
        return Err("Video mode must be text-to-video or image-to-video".into());
    }
    if category == "omni" && settings.get("responseMode").is_some_and(|value| value.as_str().is_none_or(|mode| !["text", "speech", "text-and-speech"].contains(&mode))) {
        return Err("Unsupported multimodal response mode".into());
    }
    Ok(())
}
fn output_files(dir: &Path) -> Result<Vec<String>, String> {
    let mut out = vec![];
    if !dir.exists() {
        return Ok(out);
    }
    let canonical = dir.canonicalize().map_err(|e| e.to_string())?;
    let mut stack = vec![dir.to_path_buf()];
    let mut visited = std::collections::HashSet::new();
    while let Some(folder) = stack.pop() {
        for item in std::fs::read_dir(folder).map_err(|e| e.to_string())? {
            let item = item.map_err(|e| e.to_string())?;
            let path = item.path();
            if path == dir.join("request.json") || path == dir.join("progress.json") { continue; }
            let kind = item.file_type().map_err(|e| e.to_string())?;
            if kind.is_symlink() {
                continue;
            }
            if !path
                .canonicalize()
                .map_err(|e| e.to_string())?
                .starts_with(&canonical)
            {
                return Err("Output escaped the generation folder".into());
            }
            if kind.is_dir() {
                if stack.len() < 128
                    && visited.insert(path.canonicalize().map_err(|e| e.to_string())?)
                {
                    stack.push(path);
                }
                continue;
            }
            if kind.is_file()
                && path.extension().is_some_and(|ext| {
                    matches!(
                        ext.to_string_lossy().to_ascii_lowercase().as_str(),
                        "png"
                            | "jpg"
                            | "jpeg"
                            | "webp"
                            | "gif"
                            | "flac"
                            | "wav"
                            | "mp3"
                            | "ogg"
                            | "opus"
                            | "mp4"
                            | "webm"
                            | "glb"
                            | "gltf"
                            | "obj"
                            | "bvh"
                            | "fbx"
                            | "abc"
                            | "npz"
                            | "npy"
                            | "ply"
                            | "txt"
                            | "md"
                            | "json"
                            | "jsonl"
                            | "csv"
                            | "srt"
                            | "vtt"
                    )
                })
            {
                out.push(path.to_string_lossy().into_owned());
            }
            if out.len() >= 256 {
                return Ok(out);
            }
        }
    }
    Ok(out)
}
pub fn tool_spec() -> Value {
    let mut spec = json!({"type":"function","function":{"name":"studio_use","description":"Control OpenCore studios. list_models lists verified installed models and explicitly connected publisher runtimes for enabled categories, with installedWeights and runtimeConnected kept separate. generate queues a real job with exact prompt/settings; generation starts after this chat response finishes. status/list inspect conversation jobs. cancel stops an owned job. For YuE2 music compose actual title, style and lyrics strings in settings. Video, speech synthesis, reference voices, OCR, multimodal and offline policy jobs use connected publisher workers. Policy results are prediction files for review. Never claim a queued job is completed. Results, exact requests and errors appear in Music Studio, Game Dev Studio or Media Studio.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["list_models","generate","status","list","cancel"]},"category":{"type":"string","enum":CATEGORIES},"modelId":{"type":"string"},"prompt":{"type":"string"},"settings":{"type":"object","properties":{
        "title":{"type":"string","description":"Actual song title"},"style":{"type":"string","description":"Actual genre, instruments, mood and vocal description"},"lyrics":{"type":"string","description":"Actual original lyrics composed for the user, with verse/chorus section markers"},
        "cot":{"type":"string","enum":["full","melody","off"]},"mode":{"type":"string","enum":["song","plan"]},"takes":{"type":"integer","minimum":1,"maximum":8},"seed":{"type":"integer"},"ode_steps":{"type":"integer","minimum":1,"maximum":256},
        "memory":{"type":"object","properties":{"quantization":{"type":"string","enum":["none"]},"offload_ar":{"type":"boolean"}}},
        "semantic_sampling":{"type":"object","properties":{"max_tokens":{"type":"integer","minimum":1},"min_tokens":{"type":"integer","minimum":1}}},
        "abc_sampling":{"type":"object","properties":{"max_tokens":{"type":"integer","minimum":1},"min_tokens":{"type":"integer","minimum":1}}},
        "inputPath":{"type":"string","description":"Exact attached input file path or prior generated output"},
        "width":{"type":"integer","minimum":128,"maximum":2048},"height":{"type":"integer","minimum":128,"maximum":2048},"steps":{"type":"integer","minimum":1,"maximum":100},
        "negativePrompt":{"type":"string"},"guidanceScale":{"type":"number","minimum":0,"maximum":30},"numImages":{"type":"integer","minimum":1,"maximum":8},
        "resolution":{"type":"integer","minimum":32,"maximum":512},"chunkSize":{"type":"integer","minimum":256,"maximum":32768},
        "motionPrompt":{"type":"string"},"durationSeconds":{"type":"number","minimum":1,"maximum":60},"duration":{"type":"number"},
        "frameCount":{"type":"integer","minimum":1,"maximum":2400},"fps":{"type":"integer","minimum":1,"maximum":120},"loop":{"type":"boolean"},
        "outputFormat":{"type":"string","enum":["png","webp","jpeg","glb","fbx","bvh","gif","mp4","obj","ply"]}
    },"additionalProperties":true},"jobId":{"type":"string"}},"required":["action"]}}});
    spec["function"]["parameters"]["properties"]["settings"]["properties"].as_object_mut().unwrap().extend(json!({
        "mode":{"type":"string","enum":["song","plan","text-to-video","image-to-video"]},
        "width":{"type":"integer","minimum":128,"maximum":4096},"height":{"type":"integer","minimum":128,"maximum":4096},"steps":{"type":"integer","minimum":1,"maximum":256},
        "voice":{"type":"string","description":"Publisher runtime voice or speaker ID"},"language":{"type":"string","description":"Language code supported by the selected model, or auto"},
        "referenceText":{"type":"string","description":"Actual transcript of the supplied inputPath reference audio"},
        "speed":{"type":"number","minimum":0.25,"maximum":4},"sampleRate":{"type":"integer","minimum":8000,"maximum":192000},
        "pageStart":{"type":"integer","minimum":1,"maximum":100000},"pageEnd":{"type":"integer","minimum":0,"maximum":100000,"description":"Zero includes all remaining pages"},"preserveLayout":{"type":"boolean"},
        "responseMode":{"type":"string","enum":["text","speech","text-and-speech"]},"maxTokens":{"type":"integer","minimum":1,"maximum":32768},"temperature":{"type":"number","minimum":0,"maximum":2},
        "embodiment":{"type":"string"},"normalizationKey":{"type":"string"},"actionHorizon":{"type":"integer","minimum":1,"maximum":1024},"controlRateHz":{"type":"number","minimum":1,"maximum":500},
        "outputFormat":{"type":"string","enum":["png","webp","jpeg","glb","fbx","bvh","gif","mp4","webm","obj","ply","wav","flac","mp3","txt","md","json","npz","csv"]}
    }).as_object().unwrap().clone());
    spec["function"]["parameters"]["properties"]["action"]["enum"].as_array_mut().unwrap().extend([json!("catalog"),json!("runtime"),json!("configure_runtime")]);
    spec["function"]["parameters"]["properties"]["query"]=json!({"type":"string","description":"A publisher/model name filter for catalog discovery"});
    spec["function"]["parameters"]["properties"]["limit"]=json!({"type":"integer","minimum":1,"maximum":50});
    spec["function"]["parameters"]["properties"]["runtime"]=json!({"type":"object","properties":{"modelId":{"type":"string"},"python":{"type":"string","description":"Existing absolute Python interpreter"},"runner":{"type":"string","description":"Existing absolute publisher-compatible Python worker"},"sourceDir":{"type":"string","description":"Existing absolute SDK/model directory"}},"required":["modelId","python"]});
    let text=spec["function"]["description"].as_str().unwrap_or("").to_owned();
    spec["function"]["description"]=json!(format!("{text} catalog discovers optional models before installation. runtime inspects a worker connection; configure_runtime connects an existing validated Python worker/SDK, subject to approval. Set up the actual publisher SDK and worker with native terminal tools only when requested; do not imply a saved connection proves generation works."));
    spec
}
pub fn music_tool_spec() -> Value {
    json!({"type":"function","function":{"name":"music_generate","description":"Start one real YuE2 Music Studio song. Compose actual original title, style description and lyrics from the user's request. Fill plain text strings with the content, never JSON schemas. The queued job starts after this chat response. Its settings, progress and output appear in Music Studio.","parameters":{"type":"object","properties":{
        "prompt":{"type":"string","description":"The user's music request"},"title":{"type":"string"},"style":{"type":"string","description":"Actual genre, instruments, mood and vocals"},"lyrics":{"type":"string","description":"Actual original song lyrics with verse/chorus markers"},"cot":{"type":"string","enum":["full","melody","off"]},"mode":{"type":"string","enum":["song","plan"]},"takes":{"type":"integer","minimum":1,"maximum":8},"seed":{"type":"integer"},"ode_steps":{"type":"integer","minimum":1,"maximum":256},"semantic_sampling":{"type":"object","properties":{"max_tokens":{"type":"integer","minimum":1},"min_tokens":{"type":"integer","minimum":1}}},"abc_sampling":{"type":"object","properties":{"max_tokens":{"type":"integer","minimum":1},"min_tokens":{"type":"integer","minimum":1}}}
    },"required":["prompt","title","style","lyrics"]}}})
}
pub fn wait_tool_spec() -> Value {
    json!({"type":"function","function":{"name":"background_wait","description":"Wait for an existing local training or other process without leaving ECHO loaded. Provide its real Windows PID and the user's task. This stops this inference turn, releases the text model, and uses a read-only OS process watcher. The app resumes this conversation after the same process exits. It never stops or modifies the observed process; a nonzero exit is reported as failure.","parameters":{"type":"object","properties":{"pid":{"type":"integer","minimum":1},"prompt":{"type":"string","description":"The requested task to continue after this process exits"}},"required":["pid","prompt"]}}})
}
pub fn submit_wait(
    core: Arc<AppCore>,
    app: tauri::AppHandle,
    conversation: &str,
    args: &Value,
) -> Result<Value, String> {
    let pid = args["pid"]
        .as_u64()
        .filter(|v| *v > 0 && *v <= u32::MAX as u64)
        .ok_or("Supply a real process ID")? as u32;
    let watcher = crate::process_watch::ProcessWatch::open(pid)?;
    let prompt = args["prompt"]
        .as_str()
        .filter(|v| !v.trim().is_empty() && v.len() < 32768)
        .ok_or("Describe the authorized task to continue")?;
    let job = core.studios.submit(
        core.clone(),
        app,
        StudioRequest {
            model_id: "background-wait".into(),
            prompt: prompt.into(),
            settings: json!({"pid":pid,"created":watcher.created}),
            conversation_id: Some(conversation.into()),
        },
    )?;
    Ok(json!(job))
}
pub async fn generate_music(
    core: Arc<AppCore>,
    app: tauri::AppHandle,
    conversation: &str,
    skills: &[String],
    args: &Value,
) -> Result<Value, String> {
    let mut settings = args.clone();
    if let Some(settings) = settings.as_object_mut() {
        settings.remove("prompt");
    }
    settings["memory"] = json!({"quantization":"none","offload_ar":true});
    execute(
        core,
        app,
        conversation,
        skills,
        &json!({"action":"generate","modelId":"yue2","prompt":args["prompt"],"settings":settings}),
    )
    .await
}
pub async fn execute(
    core: Arc<AppCore>,
    app: tauri::AppHandle,
    conversation: &str,
    skills: &[String],
    args: &Value,
) -> Result<Value, String> {
    let action = args["action"].as_str().unwrap_or("");
    let permitted = |category: &str| skills.iter().any(|s| s == category);
    match action {
        "catalog"=>{
            let category=args["category"].as_str().unwrap_or("");let query=args["query"].as_str().unwrap_or("").to_lowercase();
            if query.len()>512{return Err("Catalog queries are limited to 512 bytes".into());}
            let limit=args["limit"].as_u64().unwrap_or(20).clamp(1,50) as usize;
            let library=serde_json::to_value(model_catalog::list(core.runtime.install_root())?).map_err(|e|e.to_string())?;
            let models=library["models"].as_array().ok_or("Invalid catalog")?.iter().filter(|model| {
                let c=model["category"].as_str().unwrap_or("");
                CATEGORIES.contains(&c)&&(category.is_empty()||c==category)&&(query.is_empty()||format!("{} {}",model["id"].as_str().unwrap_or(""),model["label"].as_str().unwrap_or("")).to_lowercase().contains(&query))
            }).take(limit).cloned().collect::<Vec<_>>();
            Ok(json!({"models":models,"limit":limit,"note":"Catalog availability is distinct from installed weights and a working connected runtime."}))
        },
        "runtime"=>Ok(json!({"modelId":args["modelId"],"runtime":core.studios.runtime(args["modelId"].as_str().unwrap_or(""))?})),
        "configure_runtime"=>{
            core.ensure_not_updating()?;
            let runtime=runtime_from_tool(args)?;let before=core.studios.runtime(&runtime.model_id)?;
            let changed=serde_json::to_value(&before).map_err(|e|e.to_string())?!=serde_json::to_value(Some(&runtime)).map_err(|e|e.to_string())?;
            core.studios.configure(runtime.clone())?;
            if changed {crate::record_platform_activity(&core,&app,"runtime","configure","Studio runtime connected","agent",json!({"runtime":runtime}));}
            Ok(json!({"modelId":runtime.model_id,"runtime":runtime,"changed":changed,"runtimeConnected":true,"generationVerified":false}))
        },
        "list_models"=>Ok(json!(core.studios.available_models(core.runtime.install_root())?.into_iter().filter(|m|permitted(&m.category)).map(|m|json!({"id":m.id,"label":m.label,"category":m.category,"installedWeights":model_catalog::require_installed(core.runtime.install_root(),&m.id).is_ok(),"runtimeConnected":(m.id=="yue2"&&music_studio::runtime_available())||model_catalog::is_speech_model(&m.id)||core.studios.runtime_connected(&m.id),"sourceUrl":m.source_url,"setupUrl":m.setup_url})).collect::<Vec<_>>())),
        "generate"=>{
            let model=model_catalog::model(args["modelId"].as_str().unwrap_or("")).ok_or("Unknown model")?;
            if !permitted(&model.category){return Err(format!("Enable /{} for this prompt before generating",model.category));}
            // Input files must originate from this conversation's approved attachments or generated outputs.
            if let Some(path)=args["settings"]["inputPath"].as_str(){
                let entries=core.store.conversation(conversation)?;
                let allowed=entries.iter().any(|entry|entry.metadata["files"].as_array().is_some_and(|files|files.iter().any(|file|file["path"].as_str()==Some(path)))) || core.studios.list()?.iter().filter(|j|j.request.conversation_id.as_deref()==Some(conversation)).any(|j|j.outputs.iter().any(|p|p==path));
                if !allowed{return Err("Input must be an attachment or output from this conversation".into());}
            }
            let request=StudioRequest{model_id:model.id,prompt:args["prompt"].as_str().unwrap_or("").into(),settings:args["settings"].clone(),conversation_id:Some(conversation.into())};
            let job=core.studios.submit(core.clone(),app,request)?;Ok(json!(job))
        },
        "list"=>Ok(json!(core.studios.list()?.into_iter().filter(|job|job.request.conversation_id.as_deref()==Some(conversation)&&permitted(&job.category)).collect::<Vec<_>>())),
        "status"|"cancel"=>{let job=core.studios.get(args["jobId"].as_str().unwrap_or(""))?;authorize_job(&job,conversation,skills)?;if action=="cancel" {core.studios.cancel(&job.id)?;}Ok(json!(job))},
        _=>Err("Unknown Studio action".into())
    }
}
fn authorize_job(job: &StudioJob, conversation: &str, skills: &[String]) -> Result<(), String> {
    if job.request.conversation_id.as_deref() != Some(conversation)
        || !skills.contains(&job.category)
    {
        Err("This job belongs to another conversation or a disabled skill".into())
    } else {
        Ok(())
    }
}
pub fn submit_cli(app: &tauri::AppHandle, args: &[String]) -> Result<(), String> {
    let Some(index) = args.iter().position(|s| s == "--studio-request") else {
        return Ok(());
    };
    let path = PathBuf::from(
        args.get(index + 1)
            .ok_or("--studio-request requires a request JSON file")?,
    );
    if path.metadata().map_err(|e| e.to_string())?.len() > 200 * 1024 {
        return Err("Studio request file is too large".into());
    }
    let mut request: StudioRequest =
        serde_json::from_slice(&std::fs::read(path).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    request.conversation_id = None;
    let core = app.state::<Arc<AppCore>>();
    let job = core
        .studios
        .submit(core.inner().clone(), app.clone(), request)?;
    core.store.log(
        "info",
        "studio",
        &format!("CLI generation queued: {}", job.id),
    );
    Ok(())
}
#[tauri::command]
pub fn list_studio_jobs(core: tauri::State<'_, Arc<AppCore>>) -> Result<Vec<StudioJob>, String> {
    core.studios.list()
}
#[tauri::command]
pub fn submit_studio_job(
    core: tauri::State<'_, Arc<AppCore>>,
    app: tauri::AppHandle,
    request: StudioRequest,
) -> Result<StudioJob, String> {
    core.studios.submit(core.inner().clone(), app, request)
}
#[tauri::command]
pub fn cancel_studio_job(core: tauri::State<'_, Arc<AppCore>>, id: String) -> Result<(), String> {
    core.studios.cancel(&id)
}
#[tauri::command]
pub fn configure_studio_runtime(
    core: tauri::State<'_, Arc<AppCore>>,
    app: tauri::AppHandle,
    runtime: StudioRuntime,
) -> Result<(), String> {
    let changed=serde_json::to_value(core.studios.runtime(&runtime.model_id)?).map_err(|e|e.to_string())?!=serde_json::to_value(Some(&runtime)).map_err(|e|e.to_string())?;
    core.studios.configure(runtime.clone())?;
    if changed {crate::record_platform_activity(&core,&app,"runtime","configure","Studio runtime connected","studio-ui",json!({"runtime":runtime}));}
    Ok(())
}
#[tauri::command]
pub fn studio_runtime(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
) -> Result<Option<StudioRuntime>, String> {
    core.studios.runtime(&id)
}
#[tauri::command]
pub fn open_studio_output(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
    path: String,
) -> Result<(), String> {
    core.studios.validate_output(&id, &path)?;
    #[cfg(windows)]
    {
        std::process::Command::new("explorer.exe")
            .arg(Path::new(&path).parent().ok_or("Output folder missing")?)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}
impl StudioManager {
    fn validate_output(&self, id: &str, path: &str) -> Result<PathBuf, String> {
        let job = self.get(id)?;
        if !job.outputs.iter().any(|p| p == path) {
            return Err("Not a generated output of this job".into());
        }
        let root = if job.request.model_id == "yue2" {
            music_studio::root()
                .join("studio-output")
                .join(job.backend_run.ok_or("Music generation folder missing")?)
        } else {
            self.root.join(&job.id)
        };
        let root = root.canonicalize().map_err(|e| e.to_string())?;
        let path = Path::new(path).canonicalize().map_err(|e| e.to_string())?;
        if !path.starts_with(root) || !path.is_file() {
            return Err("Output is no longer inside its generation folder".into());
        }
        Ok(path)
    }
}
#[tauri::command]
pub async fn studio_output_preview(
    core: tauri::State<'_, Arc<AppCore>>,
    id: String,
    path: String,
) -> Result<Value, String> {
    let path = core.studios.validate_output(&id, &path)?;
    if path.metadata().map_err(|e| e.to_string())?.len() > 32 * 1024 * 1024 {
        return Err("This output exceeds the inline preview limit. Open its folder to play the complete file.".into());
    }
    let mime = match path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "flac" => "audio/flac",
        "wav" => "audio/wav",
        "mp3" => "audio/mpeg",
        "ogg" | "opus" => "audio/ogg",
        "mp4" => "video/mp4",
        "webm" => "video/webm",
        "txt" | "srt" | "vtt" | "jsonl" => "text/plain",
        "md" => "text/markdown",
        "csv" => "text/csv",
        "json" => "application/json",
        _ => return Err("This file has no inline preview".into()),
    };
    let bytes = tokio::fs::read(path).await.map_err(|e| e.to_string())?;
    use base64::Engine;
    Ok(
        json!({"mime":mime,"dataUrl":format!("data:{mime};base64,{}",base64::engine::general_purpose::STANDARD.encode(bytes))}),
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn agent_runtime_configuration_cannot_switch_to_a_different_model() {
        let args=json!({"modelId":"tts-f5-tts","runtime":{"modelId":"ocr-other","python":"C:/runtime/python.exe"}});
        assert!(runtime_from_tool(&args).err().unwrap().contains("must match"));
        let args=json!({"modelId":"tts-f5-tts","runtime":{"modelId":"tts-f5-tts","python":"C:/runtime/python.exe","runner":"C:/runtime/worker.py","sourceDir":"C:/runtime/sdk"}});
        assert_eq!(runtime_from_tool(&args).unwrap().model_id,"tts-f5-tts");
        let actions=tool_spec()["function"]["parameters"]["properties"]["action"]["enum"].as_array().unwrap().clone();
        assert!(actions.contains(&json!("configure_runtime")));assert!(actions.contains(&json!("catalog")));
    }

    #[test]
    fn media_requests_require_inputs_and_reject_invalid_controls() {
        let mut request = StudioRequest { model_id: "external".into(), prompt: "Inspect the supplied input".into(), settings: json!({}), conversation_id: None };
        for category in ["voice-cloning", "ocr", "omni", "policy", "speech"] {
            assert!(validate_request(category, &request).unwrap_err().contains("inputPath"), "{category}");
            request.settings = json!({"inputPath":"C:/input/observation.json"});
            assert!(validate_request(category, &request).is_ok(), "{category}");
            request.settings = json!({});
        }
        request.settings = json!({"mode":"image-to-video"});
        assert!(validate_request("video", &request).is_err());
        request.settings = json!({"mode":"text-to-video", "fps":24, "frameCount":81});
        assert!(validate_request("video", &request).is_ok());
        for bad in [json!(-1), json!(121), json!(2.5), json!("24")] {
            request.settings["fps"] = bad;
            assert!(validate_request("video", &request).is_err());
        }
        request.settings = json!({"inputPath":"invoice.png", "pageStart":5, "pageEnd":3});
        assert!(validate_request("ocr", &request).is_err());
        request.settings = json!({"speed":0});
        assert!(validate_request("tts", &request).is_err());
    }

    #[test]
    fn setup_only_models_need_an_explicit_worker_and_source_folder() {
        let root = std::env::temp_dir().join(format!("studio-runtime-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let python = root.join("python.exe");
        let runner = root.join("worker.py");
        std::fs::write(&python, "fixture; never executed").unwrap();
        std::fs::write(&runner, "fixture; never executed").unwrap();
        let manager = StudioManager::new(root.join("jobs")).unwrap();
        let mut runtime = StudioRuntime {model_id:"wan2-2-t2v-a14b".into(), python, runner:None, source_dir:Some(root.clone())};
        assert!(manager.configure(runtime.clone()).is_err());
        runtime.runner = Some(runner.clone()); runtime.source_dir = None;
        assert!(manager.configure(runtime.clone()).is_err());
        runtime.source_dir = Some(root.clone());
        manager.configure(runtime).unwrap();
        assert!(manager.runtime_connected("wan2-2-t2v-a14b"));
        assert_eq!(manager.configured_models().unwrap()[0].category, "video");
        std::fs::remove_file(runner).unwrap();
        assert!(!manager.runtime_connected("wan2-2-t2v-a14b"));
        assert!(manager.configured_models().unwrap().is_empty());
        drop(manager);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn output_discovery_ignores_request_metadata_and_preserves_document_and_action_files() {
        let root = std::env::temp_dir().join(format!("studio-output-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("request.json"), "{}").unwrap();
        std::fs::write(root.join("progress.json"), "{}").unwrap();
        std::fs::write(root.join("generation.log"), "finished").unwrap();
        assert!(output_files(&root).unwrap().is_empty(), "metadata cannot turn a zero-output job into success");
        for name in ["document.md", "actions.json", "actions.npz", "clip.gif"] { std::fs::write(root.join(name), "generated").unwrap(); }
        let outputs = output_files(&root).unwrap();
        assert_eq!(outputs.len(), 4);
        assert!(outputs.iter().any(|file| file.ends_with("actions.json")));
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn only_supported_native_model_ids_bypass_generic_workers() {
        for id in ["yue2", "whisper-large-v3-turbo", "whisper-large-v3", "phonon-2"] { assert!(builtin_service(id)); }
        for id in ["musicgen-small", "whisper-small", "tts-f5-tts", "wan2-2-ti2v-5b"] { assert!(!builtin_service(id)); }
        let spec = tool_spec();
        let settings = &spec["function"]["parameters"]["properties"]["settings"]["properties"];
        assert_eq!(settings["actionHorizon"]["minimum"], 1);
        assert_eq!(settings["referenceText"]["type"], "string");
        assert!(spec["function"]["parameters"]["properties"]["category"]["enum"].as_array().unwrap().contains(&json!("ocr")));
    }

    #[test]
    fn worker_protocol_preserves_request_inputs_and_pinned_model_identity() {
        let job = StudioJob { id:"job".into(), category:"video".into(), request:StudioRequest {model_id:"wan2-2-ti2v-5b".into(), prompt:"A paper boat on a lake".into(), settings:json!({"fps":12,"frameCount":49,"inputPath":"C:/clips/reference.png"}), conversation_id:Some("chat".into())}, status:"queued".into(), stage:"Waiting".into(), created_at:"now".into(), updated_at:"now".into(), backend_run:None, progress:json!({}), outputs:vec![], error:None };
        let runtime = StudioRuntime {model_id:job.request.model_id.clone(),python:PathBuf::from("C:/runtime/python.exe"),runner:Some(PathBuf::from("C:/runtime/worker.py")),source_dir:Some(PathBuf::from("C:/runtime/sdk"))};
        let payload = worker_request(Path::new("C:/OpenCore"), &job, &runtime).unwrap();
        assert_eq!(payload["protocolVersion"], 1);
        assert_eq!(payload["prompt"], "A paper boat on a lake");
        assert_eq!(payload["settings"], json!({"fps":12,"frameCount":49,"inputPath":"C:/clips/reference.png"}));
        assert_eq!(payload["conversationId"], "chat");
        assert_eq!(payload["sourceDir"], "C:/runtime/sdk");
        assert!(payload["catalogModel"]["sourceUrl"].as_str().unwrap().ends_with("921dbaf3f1674a56f47e83fb80a34bac8a8f203e"));
        assert!(payload["modelDirectory"].as_str().unwrap().ends_with("wan2-2-ti2v-5b"));
    }

    #[tokio::test]
    async fn update_cancellation_drains_running_jobs_without_closing_the_manager() {
        let root = std::env::temp_dir().join(format!("studio-update-{}", uuid::Uuid::new_v4()));
        let manager = StudioManager::new(root.clone()).unwrap();
        let token = CancellationToken::new();
        manager.running.lock().unwrap().insert("active".into(), token.clone());
        let worker_manager = manager.clone();
        let worker = tokio::spawn(async move {
            token.cancelled().await;
            worker_manager.running.lock().unwrap().remove("active");
        });
        tokio::time::timeout(Duration::from_secs(1), manager.cancel_active()).await.unwrap().unwrap();
        worker.await.unwrap();
        assert!(!manager.busy());
        assert!(manager.cancel_active().await.is_ok(), "the manager remains available if installation fails");
        drop(manager);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn chat_tool_schema_exposes_game_dev_generation_customization() {
        let spec = tool_spec();
        let properties = spec["function"]["parameters"]["properties"]["settings"]["properties"].as_object().unwrap();
        for name in ["inputPath", "negativePrompt", "guidanceScale", "numImages", "resolution", "chunkSize",
                     "motionPrompt", "durationSeconds", "frameCount", "fps", "loop", "outputFormat"] {
            assert!(properties.contains_key(name), "missing studio setting {name}");
        }
    }
    #[test]
    fn continuation_is_claimed_once_and_new_user_turn_supersedes_it() {
        let root =
            std::env::temp_dir().join(format!("studio-continuation-{}", uuid::Uuid::new_v4()));
        let manager = StudioManager::new(root.clone()).unwrap();
        for id in ["ready", "stale"] {
            manager
                .db
                .lock()
                .unwrap()
                .execute(
                    "INSERT INTO continuations VALUES(?1,?2,42,'pending')",
                    rusqlite::params![
                        id,
                        r#"{"profile":"echo","request":{"text":"original task"}}"#
                    ],
                )
                .unwrap();
        }
        assert!(manager.continuation_pending());
        assert_eq!(
            manager.claim_continuation("ready", 42).unwrap().unwrap()["request"]["text"],
            "original task"
        );
        assert!(manager.claim_continuation("ready", 42).unwrap().is_none());
        assert!(manager.claim_continuation("stale", 43).unwrap().is_none());
        drop(manager);
        let reopened = StudioManager::new(root.clone()).unwrap();
        assert!(!reopened.continuation_pending());
        drop(reopened);
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn music_requires_real_lyrics_and_preserves_precision() {
        let mut r = StudioRequest {
            model_id: "yue2".into(),
            prompt: "AI song".into(),
            settings: json!({}),
            conversation_id: None,
        };
        assert!(validate_request("music", &r).is_err());
        r.settings = json!({"style":"Electronic","lyrics":"We build tomorrow"});
        assert!(validate_request("music", &r).is_ok());
        r.settings["style"] = json!({"type":"string"});
        assert!(validate_request("music", &r)
            .unwrap_err()
            .contains("actual nonempty text"));
        r.settings["style"] = json!("Electronic");
        r.settings["memory"] = json!({"quantization":"fp8"});
        assert!(validate_request("music", &r).is_err());
        assert!(validate_request("text", &r).is_err());
    }
    #[test]
    fn cancel_is_saved_immediately_and_late_worker_writes_do_not_revert_it() {
        let root = std::env::temp_dir().join(format!("studio-cancel-{}", uuid::Uuid::new_v4()));
        let manager = StudioManager::new(root.clone()).unwrap();
        let job = StudioJob { id:"cancel-now".into(), category:"music".into(), request:StudioRequest {model_id:"yue2".into(),prompt:"A song".into(),settings:json!({}),conversation_id:None},status:"running".into(),stage:"Verifying files".into(),created_at:"today".into(),updated_at:"today".into(),backend_run:None,progress:json!({}),outputs:vec![],error:None };
        manager.save(&job).unwrap();
        let token = CancellationToken::new();
        manager.running.lock().unwrap().insert(job.id.clone(), token.clone());
        manager.cancel(&job.id).unwrap();
        assert!(token.is_cancelled());
        let cancelled = manager.get(&job.id).unwrap();
        assert_eq!(cancelled.status, "cancelled");
        assert_eq!(cancelled.progress["cleanupPending"], true);
        assert!(manager.busy(), "GPU lease remains occupied until the worker finishes cleanup");
        manager.update(&job.id, "failed", "Generation failed", json!({}), vec![], Some("Late failure".into())).unwrap();
        let mut stale = job.clone(); stale.backend_run = Some("accepted-run".into());
        manager.save(&stale).unwrap();
        let saved = manager.get(&job.id).unwrap();
        assert_eq!(saved.status, "cancelled");
        assert!(saved.error.is_none());
        assert_eq!(saved.backend_run.as_deref(), Some("accepted-run"));
        manager.update(&job.id, "cancelled", "Cancelled", json!({"cleanupPending":false}), vec![], None).unwrap();
        let mut stale_cancel = cancelled.clone();
        stale_cancel.backend_run = None;
        manager.save(&stale_cancel).unwrap();
        assert_eq!(manager.get(&job.id).unwrap().progress["cleanupPending"], false);
        assert_eq!(manager.get(&job.id).unwrap().backend_run.as_deref(), Some("accepted-run"));
        drop(manager);
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn interrupted_jobs_keep_the_original_request_on_reopen() {
        let root = std::env::temp_dir().join(format!("studio-test-{}", uuid::Uuid::new_v4()));
        let manager = StudioManager::new(root.clone()).unwrap();
        let job = StudioJob {
            id: "saved".into(),
            category: "music".into(),
            request: StudioRequest {
                model_id: "yue2".into(),
                prompt: "AI song".into(),
                settings: json!({"lyrics":"Original lyrics"}),
                conversation_id: Some("chat-a".into()),
            },
            status: "running".into(),
            stage: "Song".into(),
            created_at: "today".into(),
            updated_at: "today".into(),
            backend_run: Some("original-run".into()),
            progress: json!({}),
            outputs: vec![],
            error: None,
        };
        assert!(authorize_job(&job, "chat-b", &["music".into()]).is_err());
        assert!(authorize_job(&job, "chat-a", &[]).is_err());
        assert!(authorize_job(&job, "chat-a", &["music".into()]).is_ok());
        let observed = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let count = observed.clone();
        let weak = Arc::downgrade(&manager);
        *manager.notify.lock().unwrap() = Some(Box::new(move |job| {
            assert_eq!(
                weak.upgrade().unwrap().get(&job.id).unwrap().status,
                job.status
            );
            count.fetch_add(1, Ordering::SeqCst);
        }));
        manager.save(&job).unwrap();
        assert_eq!(observed.load(Ordering::SeqCst), 1);
        drop(manager);
        let manager = StudioManager::new(root.clone()).unwrap();
        let recovered = manager.get("saved").unwrap();
        assert_eq!(recovered.status, "interrupted");
        assert_eq!(recovered.request.settings, job.request.settings);
        assert_eq!(recovered.backend_run, job.backend_run);
        drop(manager);
        std::fs::remove_dir_all(root).unwrap();
    }
    #[tokio::test]
    #[ignore = "Explicit local YuE inference smoke; no network model download"]
    async fn real_music_server_generates_a_plan_and_releases_its_model() {
        assert_eq!(std::env::var("OPENCORE_TEST_MUSIC").as_deref(), Ok("1"));
        let root = PathBuf::from(std::env::var("OPENCORE_HOME").unwrap());
        let external = crate::music_weights::external_dir().expect("Existing music weights");
        crate::music_weights::register(&root, &external).unwrap();
        let manager = StudioManager::new(root.join("studio-smoke")).unwrap();
        let request = StudioRequest {
            model_id: "yue2".into(),
            prompt: "Integration smoke: short AI music score".into(),
            settings: json!({"title":"OpenCore-integration-smoke","style":"Electronic pop, piano, clear vocals","lyrics":"[Verse]\nWe build tomorrow, one bright idea at a time.","mode":"plan","takes":1,"seed":831001,"abc_sampling":{"max_tokens":32,"min_tokens":1},"memory":{"quantization":"none","offload_ar":true}}),
            conversation_id: None,
        };
        let now = chrono::Utc::now().to_rfc3339();
        let job = StudioJob {
            id: uuid::Uuid::new_v4().to_string(),
            category: "music".into(),
            request,
            status: "queued".into(),
            stage: "Smoke test".into(),
            created_at: now.clone(),
            updated_at: now,
            backend_run: None,
            progress: json!({}),
            outputs: vec![],
            error: None,
        };
        manager.save(&job).unwrap();
        manager
            .run_music(&job, &CancellationToken::new(), &mut MusicAdmission::default())
            .await
            .unwrap();
        let result = manager.get(&job.id).unwrap();
        assert_eq!(result.status, "completed");
        assert!(!result.outputs.is_empty());
        assert!(!music_studio::music_studio_status().await.model_loaded);
        println!("{}", serde_json::to_string_pretty(&result).unwrap());
    }
}
