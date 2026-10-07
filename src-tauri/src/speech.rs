use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::PathBuf, process::Stdio, sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex as StdMutex}, time::{Duration, Instant}};
use tokio::{io::{AsyncBufReadExt, AsyncWriteExt, BufReader}, process::{Child, ChildStdin}, sync::{Mutex, oneshot, watch}};
use tokio_util::sync::CancellationToken;

type Outcome = Option<Result<Value, String>>;
struct Session {
    id: String, audio: PathBuf, input: Option<oneshot::Sender<()>>,
    result: watch::Receiver<Outcome>, ready: watch::Receiver<Option<Result<(), String>>>, cancel: CancellationToken,
    _gpu: Option<crate::studio_jobs::GpuReservation>,
}
struct Worker { child: Child, input: ChildStdin, output: BufReader<tokio::process::ChildStdout>, model_id: String, runtime_precision: String, cold_start_ms: Option<u64>, wake_ms: Option<u64>, awake: bool }
impl Worker {
    async fn read(&mut self) -> Result<Value, String> {
        let mut line = String::new();
        tokio::time::timeout(Duration::from_secs(190), self.output.read_line(&mut line)).await
            .map_err(|_| "Speech did not respond within 190 seconds".to_string())?
            .map_err(|e| e.to_string())?;
        if line.len() > 128 * 1024 { return Err("Speech returned an oversized result".into()); }
        serde_json::from_str(&line).map_err(|e| format!("Could not read the Speech response: {e}"))
    }
    async fn send(&mut self, value: Value) -> Result<(), String> {
        self.input.write_all(format!("{}\n", value).as_bytes()).await.map_err(|e| e.to_string())?;
        self.input.flush().await.map_err(|e| e.to_string())
    }
    async fn stop(&mut self) {
        let _ = self.send(json!({"action":"shutdown"})).await;
        if tokio::time::timeout(Duration::from_secs(2), self.child.wait()).await.is_err() {
            let _ = self.child.kill().await;
            let _ = self.child.wait().await;
        }
    }
    async fn abort(&mut self) {
        let _ = self.child.kill().await;
        let _ = self.child.wait().await;
    }
}

#[derive(Default)]
struct StartupState { cancel: CancellationToken, started: Option<Instant>, interrupts: usize }
struct StartupInterrupt(Arc<StdMutex<StartupState>>);
impl Drop for StartupInterrupt {
    fn drop(&mut self) { if let Ok(mut state)=self.0.lock() { state.interrupts=state.interrupts.saturating_sub(1); } }
}
fn startup_progress(value:&Value) -> Option<&str> {
    value["progress"].as_str().filter(|phase| ["starting-runtime", "verifying-checkpoint", "building-model",
        "expanding-weights", "applying-weights", "preparing-processor", "activating-device",
        "verifying-dense-cache", "loading-dense-cache", "saving-dense-cache",
        "preparing-original-runtime", "loading-packed-weights"].contains(phase))
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Settings { #[serde(default="default_model_id")] model_id: String, #[serde(default="default_runtime_precision")] runtime_precision: String, enabled: bool, idle_mode: String, cold_start_ms: Option<u64>, warm_wake_ms: Option<u64> }
fn default_model_id() -> String { "whisper-large-v3-turbo".into() }
fn default_runtime_precision() -> String { "bf16".into() }
fn decode_settings(bytes:&[u8]) -> Option<Settings> {
    let value:Value=serde_json::from_slice(bytes).ok()?;
    let legacy_precision=value.get("runtimePrecision").is_none();
    let mut settings:Settings=serde_json::from_value(value).ok()?;
    if settings.model_id=="phonon-2" && legacy_precision {
        settings.cold_start_ms=None;settings.warm_wake_ms=None;
    }
    Some(settings)
}
impl Default for Settings {
    fn default() -> Self { Self { model_id: default_model_id(), runtime_precision: default_runtime_precision(), enabled: false, idle_mode: "cold".into(), cold_start_ms: None, warm_wake_ms: None } }
}
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SpeechStatus {
    model_id: String,
    runtime_precision: String, loading_elapsed_ms: Option<u64>,
    installed: bool, enabled: bool, idle_mode: String, worker_ready: bool,
    cold_start_ms: Option<u64>, warm_wake_ms: Option<u64>, phase: String,
    runtime_cache_bytes: u64, runtime_cache_entries: Vec<RuntimeCacheEntry>, dense_cache_hit: Option<bool>,
    prewarmed_for_session: bool,
    runtime_resident_bytes: Option<u64>, runtime_description: Option<String>,
}
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all="camelCase")]
pub struct RuntimeCacheEntry { precision:String, bytes:u64 }
pub struct SpeechManager {
    root: PathBuf, resources: PathBuf, control: Arc<Mutex<()>>,
    session: Arc<Mutex<Option<Session>>>, worker: Arc<Mutex<Option<Worker>>>,
    settings: Arc<StdMutex<Settings>>, phase: Arc<StdMutex<String>>,
    startup: Arc<StdMutex<StartupState>>,
    update_in_progress: Arc<AtomicBool>,
    dense_cache_hit: Arc<StdMutex<Option<bool>>>,
    runtime_observation: Arc<StdMutex<Option<(String,u64,String)>>>,
}
impl Clone for SpeechManager {
    fn clone(&self) -> Self { Self { root:self.root.clone(), resources:self.resources.clone(), control:self.control.clone(),
        session:self.session.clone(), worker:self.worker.clone(), settings:self.settings.clone(), phase:self.phase.clone(), startup:self.startup.clone(), update_in_progress:self.update_in_progress.clone(),dense_cache_hit:self.dense_cache_hit.clone(),runtime_observation:self.runtime_observation.clone() } }
}
impl SpeechManager {
    pub fn new(root: PathBuf, resources: PathBuf) -> Self {
        Self::new_with_update_gate(root, resources, Arc::new(AtomicBool::new(false)))
    }
    pub fn new_with_update_gate(root: PathBuf, resources: PathBuf, update_in_progress: Arc<AtomicBool>) -> Self {
        let speech_root = root.join("speech");
        let settings = std::fs::read(speech_root.join("settings.json")).ok()
            .and_then(|v| decode_settings(&v)).unwrap_or_default();
        Self { root:speech_root, resources, control:Arc::new(Mutex::new(())), session:Arc::new(Mutex::new(None)),
            worker:Arc::new(Mutex::new(None)), settings:Arc::new(StdMutex::new(settings)),
            phase:Arc::new(StdMutex::new("off".into())), startup:Arc::new(StdMutex::new(StartupState::default())), update_in_progress,dense_cache_hit:Arc::new(StdMutex::new(None)),runtime_observation:Arc::new(StdMutex::new(None)) }
    }
    fn ensure_not_updating(&self) -> Result<(), String> {
        if self.update_in_progress.load(Ordering::Acquire) {
            Err("OpenCore is stopping active work to install an update. Please wait for it to restart.".into())
        } else { Ok(()) }
    }
    fn settings(&self) -> Settings { self.settings.lock().map(|v|v.clone()).unwrap_or_default() }
    pub fn selected_model(&self) -> String { self.settings().model_id }
    fn save_settings(&self, next: Settings) -> Result<(), String> {
        std::fs::create_dir_all(&self.root).map_err(|e|e.to_string())?;
        std::fs::write(self.root.join("settings.json"), serde_json::to_vec_pretty(&next).map_err(|e|e.to_string())?)
            .map_err(|e|e.to_string())?;
        *self.settings.lock().map_err(|e|e.to_string())? = next;
        Ok(())
    }
    fn model_dir(&self, id: &str) -> Result<PathBuf,String> {
        crate::model_catalog::speech_model_path(self.root.parent().unwrap_or(&self.root), id).ok_or_else(||"Unknown speech model".into())
    }
    fn installed(&self) -> bool {
        crate::model_catalog::require_installed(self.root.parent().unwrap_or(&self.root), &self.settings().model_id).is_ok()
    }
    fn set_phase(&self, phase: &str) { if let Ok(mut p)=self.phase.lock() { *p=phase.into(); } }
    fn interrupt_startup(&self) -> StartupInterrupt {
        if let Ok(mut state)=self.startup.lock() { state.interrupts+=1; state.cancel.cancel(); }
        StartupInterrupt(self.startup.clone())
    }
    fn status(&self) -> SpeechStatus {
        let cfg=self.settings();
        let entries=self.cache_entries();
        let worker_ready=self.worker.try_lock().map(|w|w.is_some()).unwrap_or(false);
        let observation=self.runtime_observation.lock().ok().and_then(|value|value.clone())
            .filter(|(precision,_,_)|cfg.model_id=="phonon-2" && *precision==cfg.runtime_precision);
        SpeechStatus { model_id:cfg.model_id.clone(), installed:self.installed(), enabled:cfg.enabled, idle_mode:cfg.idle_mode.clone(),
            runtime_precision:cfg.runtime_precision.clone(), loading_elapsed_ms:self.startup.lock().ok().and_then(|s|s.started.map(|t|t.elapsed().as_millis() as u64)),
            worker_ready,
            cold_start_ms:cfg.cold_start_ms, warm_wake_ms:cfg.warm_wake_ms,
            phase:self.phase.lock().map(|p|p.clone()).unwrap_or_else(|_|"off".into()),
            runtime_cache_bytes:entries.iter().map(|entry|entry.bytes).sum(),runtime_cache_entries:entries,
            dense_cache_hit:self.dense_cache_hit.lock().ok().and_then(|value|*value),prewarmed_for_session:cfg.idle_mode=="cold" && worker_ready,
            runtime_resident_bytes:observation.as_ref().map(|(_,bytes,_)|*bytes),runtime_description:observation.map(|(_,_,description)|description) }
    }
    fn cache_entries(&self)->Vec<RuntimeCacheEntry> {
        let directory=self.root.join("runtime-cache/phonon-2");
        ["bf16","fp32"].iter().filter_map(|precision|{
            let metadata=std::fs::symlink_metadata(directory.join(format!("expanded-{precision}.pt"))).ok()?;
            metadata.file_type().is_file().then(||RuntimeCacheEntry{precision:(*precision).into(),bytes:metadata.len()})
        }).collect()
    }
    pub async fn prewarm_session(&self)->Result<SpeechStatus,String> {
        let _control=self.control.lock().await;self.ensure_not_updating()?;
        if self.is_active().await{return Err("Finish dictation before preparing speech.".into());}
        let cfg=self.settings();
        if !cfg.enabled || !self.installed(){return Err("Enable and install the selected speech model before preparing it.".into());}
        if self.worker.lock().await.is_none() {
            // This is a temporary RAM standby action. The saved cold preference
            // still unloads the worker after the next dictation or app restart.
            let worker=self.spawn_worker(&cfg.model_id,&cfg.runtime_precision,"ram",false,&CancellationToken::new()).await?;
            *self.worker.lock().await=Some(worker);
        }
        self.set_phase("sleeping");Ok(self.status())
    }
    pub async fn clear_runtime_cache(&self)->Result<SpeechStatus,String> {
        let _interrupt={let session=self.session.lock().await;if session.is_some(){return Err("Finish dictation before clearing its derived cache.".into());}self.interrupt_startup()};
        let _control=self.control.lock().await;
        if self.is_active().await{return Err("Finish dictation before clearing its derived cache.".into());}
        if let Some(mut worker)=self.worker.lock().await.take(){worker.stop().await;}
        let directory=self.root.join("runtime-cache/phonon-2");
        if directory.exists() {
            let actual=std::fs::canonicalize(&directory).map_err(|error|error.to_string())?;
            let root=std::fs::canonicalize(&self.root).map_err(|error|error.to_string())?;
            if actual!=root.join("runtime-cache/phonon-2"){return Err("The derived speech cache path is redirected; clear it manually after reviewing that path.".into());}
            for entry in std::fs::read_dir(&directory).map_err(|error|error.to_string())? {
                let entry=entry.map_err(|error|error.to_string())?;let name=entry.file_name().to_string_lossy().into_owned();
                let known=["expanded-bf16.pt","expanded-bf16.json","expanded-fp32.pt","expanded-fp32.json"].contains(&name.as_str())
                    || (name.starts_with(".expanded-") && (name.ends_with(".pt.tmp") || name.ends_with(".json.tmp")));
                if known && entry.file_type().map_err(|error|error.to_string())?.is_file(){std::fs::remove_file(entry.path()).map_err(|error|error.to_string())?;}
            }
        }
        if let Ok(mut value)=self.dense_cache_hit.lock(){*value=None;}
        self.set_phase(if self.settings().enabled{"ready"}else{"off"});
        Ok(self.status())
    }
    pub async fn restore_saved_mode(&self) -> Result<(),String> {
        let _control=self.control.lock().await;
        let cfg=self.settings();
        if !cfg.enabled { self.set_phase("off"); return Ok(()); }
        if !self.installed() {
            self.set_phase("error");
            return Err("Speech is enabled in settings, but its checkpoint or speech runtime is missing.".into());
        }
        if cfg.idle_mode=="ram" {
            if self.worker.lock().await.is_some() { self.set_phase("sleeping"); return Ok(()); }
            self.set_phase("warming");
            match self.spawn_worker(&cfg.model_id,&cfg.runtime_precision,"ram",false,&CancellationToken::new()).await {
                Ok(worker)=>{*self.worker.lock().await=Some(worker);self.set_phase("sleeping");Ok(())}
                Err(error)=>{self.set_phase("error");Err(error)}
            }
        } else { self.set_phase("ready"); Ok(()) }
    }
    async fn spawn_worker(&self, model_id:&str, precision:&str, mode:&str, awake:bool, cancel:&CancellationToken) -> Result<Worker,String> {
        self.ensure_not_updating()?;
        let started=Instant::now();
        let startup_cancel={
            let mut state=self.startup.lock().map_err(|e|e.to_string())?;
            if state.interrupts>0 { return Err("Speech startup was cancelled".into()); }
            state.cancel=CancellationToken::new();state.started=Some(started);state.cancel.clone()
        };
        self.set_phase("starting-runtime");
        let result=self.spawn_worker_inner(model_id,precision,mode,awake,cancel,&startup_cancel,started).await;
        if let Ok(mut state)=self.startup.lock() {state.started=None;}
        if result.is_err(){self.set_phase("error");}
        result
    }
    async fn spawn_worker_inner(&self, model_id:&str, precision:&str, mode:&str, awake:bool, cancel:&CancellationToken, startup_cancel:&CancellationToken, started:Instant) -> Result<Worker,String> {
        let python=crate::model_catalog::speech_python_path(self.root.parent().unwrap_or(&self.root), model_id);
        let model=self.model_dir(model_id)?;
        let mut command=tokio::process::Command::new(python);
        command.arg(self.resources.join("speech").join(if model_id=="phonon-2" {"phonon_worker.py"} else {"whisper_worker.py"})).arg("--model").arg(&model).arg("--idle-mode").arg(mode);
        if model_id=="phonon-2" {command.arg("--precision").arg(precision).arg("--cache-dir").arg(self.root.join("runtime-cache/phonon-2"));}
        if awake { command.arg("--awake"); }
        command.env("PYTHONIOENCODING","utf-8").env("HF_HUB_OFFLINE","1")
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
        #[cfg(windows)] command.creation_flags(0x0800_0000);
        let mut child=command.spawn().map_err(|e|format!("Could not start Speech: {e}"))?;
        #[cfg(windows)]
        if let Some(handle)=child.raw_handle(){crate::child_guard::adopt_handle(handle);}
        let mut worker=Worker { input:child.stdin.take().ok_or("Missing Speech input")?,
            output:BufReader::new(child.stdout.take().ok_or("Missing speech output")?), child, model_id:model_id.into(), runtime_precision:precision.into(), cold_start_ms:None, wake_ms:None,awake };
        let ready=self.wait_startup_ready(&mut worker,cancel,startup_cancel).await?;
        if model_id=="phonon-2" && ready["runtimePrecision"].as_str()!=Some(precision) {
            worker.abort().await;return Err("Phonon-2 did not load the selected runtime precision.".into());
        }
        if model_id=="phonon-2" {
            if let (Some(bytes),Some(description))=(ready["runtimeResidentBytes"].as_u64(),ready["runtimeDescription"].as_str()) {
                if let Ok(mut observation)=self.runtime_observation.lock(){*observation=Some((precision.into(),bytes,description.into()));}
            }
        }
        let mut cfg=self.settings();
        worker.cold_start_ms=Some(started.elapsed().as_millis() as u64);
        worker.wake_ms=ready["wakeMs"].as_u64();
        if let Ok(mut value)=self.dense_cache_hit.lock(){*value=ready["denseCache"]["hit"].as_bool();}
        if cfg.model_id==model_id && (model_id!="phonon-2" || cfg.runtime_precision==precision) {
            cfg.cold_start_ms=worker.cold_start_ms;
            cfg.warm_wake_ms=worker.wake_ms.or(cfg.warm_wake_ms);
            let _=self.save_settings(cfg);
        }
        Ok(worker)
    }
    async fn wait_startup_ready(&self, worker:&mut Worker, cancel:&CancellationToken, startup_cancel:&CancellationToken) -> Result<Value,String> {
        let deadline=tokio::time::Instant::now()+Duration::from_secs(190);
        loop {
            let response=tokio::select! {
                biased;
                _=cancel.cancelled()=>Err("Speech startup was cancelled".into()),
                _=startup_cancel.cancelled()=>Err("Speech startup was cancelled".into()),
                value=tokio::time::timeout_at(deadline,worker.read())=>value.unwrap_or_else(|_|Err("Speech did not load within 190 seconds".into())),
            };
            let value=match response {Ok(value)=>value,Err(error)=>{worker.abort().await;return Err(error);}};
            if let Some(error)=value["error"].as_str(){worker.abort().await;return Err(format!("Speech failed to load: {error}"));}
            if value["ready"]==true {return Ok(value);}
            if let Some(phase)=startup_progress(&value) {self.set_phase(phase);continue;}
            worker.abort().await;return Err("Speech exited before loading its model".into());
        }
    }
    pub async fn set_enabled(&self, enabled: bool) -> Result<SpeechStatus,String> {
        let _interrupt=(!enabled).then(||self.interrupt_startup());
        let _control=self.control.lock().await;
        if enabled { self.ensure_not_updating()?; }
        let cfg=self.settings();
        if cfg.enabled == enabled && !(enabled && cfg.idle_mode=="ram" && self.worker.lock().await.is_none()) {
            if !enabled {self.set_phase("off");}
            return Ok(self.status());
        }
        if enabled {
        if !self.installed(){return Err("Install the selected speech model and its runtime from Models first.".into());}
            if cfg.idle_mode=="ram" {
                self.set_phase("warming");
                let worker=self.spawn_worker(&cfg.model_id,&cfg.runtime_precision,"ram",false,&CancellationToken::new()).await?;
                self.save_settings(Settings{enabled:true,..self.settings()})?;
                *self.worker.lock().await=Some(worker);
                self.set_phase("sleeping");
            } else {
                self.save_settings(Settings{enabled:true,..cfg})?;
                self.set_phase("ready");
            }
        } else {
            self.cancel_active().await;
            if let Some(mut w)=self.worker.lock().await.take(){w.stop().await;}
            self.save_settings(Settings{enabled:false,..cfg})?;
            self.set_phase("off");
        }
        Ok(self.status())
    }
    pub async fn set_idle_mode(&self, mode:&str) -> Result<SpeechStatus,String> {
        let _interrupt=if mode=="cold" {
            // Keep admission locked until the synchronous standby interruption.
            let session=self.session.lock().await;
            if session.is_some(){return Err("Finish the current dictation before changing its sleep mode.".into());}
            Some(self.interrupt_startup())
        } else {None};
        let _control=self.control.lock().await;
        if mode == "ram" { self.ensure_not_updating()?; }
        if !["cold","ram"].contains(&mode){return Err("Choose cold or ram idle mode".into());}
        if self.is_active().await{return Err("Finish the current dictation before changing its sleep mode.".into());}
        let cfg=self.settings();
        if cfg.idle_mode == mode && !(cfg.enabled && mode=="ram" && self.worker.lock().await.is_none()) {
            if mode=="cold" {if let Some(mut worker)=self.worker.lock().await.take(){worker.stop().await;}self.set_phase(if cfg.enabled {"ready"}else{"off"});}
            return Ok(self.status());
        }
        if cfg.enabled && mode=="ram" {
            self.set_phase("warming");
            let w=self.spawn_worker(&cfg.model_id,&cfg.runtime_precision,"ram",false,&CancellationToken::new()).await?;
            self.save_settings(Settings{idle_mode:mode.into(),..self.settings()})?;
            *self.worker.lock().await=Some(w);
            self.set_phase("sleeping");
        } else {
            if let Some(mut w)=self.worker.lock().await.take(){w.stop().await;}
            self.save_settings(Settings{idle_mode:mode.into(),..cfg.clone()})?;
            self.set_phase(if cfg.enabled {"ready"}else{"off"});
        }
        Ok(self.status())
    }
    pub async fn set_model(&self, id:&str) -> Result<SpeechStatus,String> {
        let _control=self.control.lock().await;
        self.ensure_not_updating()?;
        if !crate::model_catalog::is_speech_model(id) { return Err("Unknown speech model".into()); }
        if self.is_active().await { return Err("Finish the current dictation before changing its model.".into()); }
        let cfg=self.settings();
        if cfg.model_id==id { return Ok(self.status()); }
        crate::model_catalog::require_installed(self.root.parent().unwrap_or(&self.root), id)?;
        if let Some(mut worker)=self.worker.lock().await.take() { worker.stop().await; }
        let mut next=Settings{model_id:id.into(),cold_start_ms:None,warm_wake_ms:None,..cfg};
        if next.enabled && next.idle_mode=="ram" {
            self.set_phase("warming");
            let worker=match self.spawn_worker(id,&next.runtime_precision,"ram",false,&CancellationToken::new()).await {
                Ok(worker)=>worker,
                Err(error)=>{self.set_phase("error");return Err(error);}
            };
            next.cold_start_ms=worker.cold_start_ms;next.warm_wake_ms=worker.wake_ms;
            self.save_settings(next)?;
            *self.worker.lock().await=Some(worker);
            self.set_phase("sleeping");
        } else {
            let enabled=next.enabled;
            self.save_settings(next)?;
            self.set_phase(if enabled {"ready"} else {"off"});
        }
        Ok(self.status())
    }
    pub async fn set_runtime_precision(&self, precision:&str) -> Result<SpeechStatus,String> {
        if !["original","bf16","fp32"].contains(&precision) {return Err("Choose Original (164 MB), BF16 or FP32 for Phonon-2.".into());}
        let _control=self.control.lock().await;
        self.ensure_not_updating()?;
        if self.is_active().await {return Err("Finish the current dictation before changing its precision.".into());}
        let cfg=self.settings();
        if cfg.model_id!="phonon-2" {return Err("Runtime precision selection is available for Phonon-2.".into());}
        if cfg.runtime_precision==precision && !(cfg.enabled && cfg.idle_mode=="ram" && self.worker.lock().await.is_none()) {
            return Ok(self.status());
        }
        // The old worker exits before a replacement is created, including CPU RAM.
        if let Some(mut worker)=self.worker.lock().await.take() {worker.stop().await;}
        let mut next=Settings{runtime_precision:precision.into(),cold_start_ms:None,warm_wake_ms:None,..cfg};
        if next.enabled && next.idle_mode=="ram" {
            let worker=self.spawn_worker(&next.model_id,precision,"ram",false,&CancellationToken::new()).await?;
            next.cold_start_ms=worker.cold_start_ms;next.warm_wake_ms=worker.wake_ms;
            self.save_settings(next)?;
            *self.worker.lock().await=Some(worker);
            self.set_phase("sleeping");
        } else {
            let enabled=next.enabled;
            self.save_settings(next)?;
            self.set_phase(if enabled {"ready"}else{"off"});
        }
        Ok(self.status())
    }
    async fn cancel_active(&self) {
        let Some(mut session)=self.session.lock().await.take() else{return};
        session.cancel.cancel();
        let _=wait_result(&mut session.result).await;
        let _=tokio::fs::remove_file(session.audio).await;
    }
    pub async fn start(&self) -> Result<String,String> {
        self.start_with_id(uuid::Uuid::new_v4().to_string()).await
    }
    pub async fn start_with_id(&self, id: String) -> Result<String,String> {
        self.start_reserved(id, crate::studio_jobs::reserve_gpu()?).await
    }
    pub async fn start_reserved(&self, id: String, gpu: crate::studio_jobs::GpuReservation) -> Result<String,String> {
        self.start_session(id, false, Some(gpu)).await
    }
    pub async fn start_file(&self) -> Result<String,String> {
        self.start_session(uuid::Uuid::new_v4().to_string(), true, None).await
    }
    async fn start_session(&self, id: String, explicit_file: bool, gpu: Option<crate::studio_jobs::GpuReservation>) -> Result<String,String> {
        uuid::Uuid::parse_str(&id).map_err(|_|"Invalid microphone session ID".to_string())?;
        let control=self.control.lock().await;
        self.ensure_not_updating()?;
        let mut session_guard=self.session.lock().await;
        if session_guard.is_some(){return Err("A microphone session is already active".into());}
        // A standby change can interrupt while this admission waits for the
        // session guard. Reject before publishing a session or spawning a worker.
        if self.startup.lock().map_err(|e|e.to_string())?.interrupts>0 {
            return Err("Speech settings are changing. Wait for the current change, then retry dictation.".into());
        }
        crate::model_catalog::require_idle()?;
        let cfg=self.settings();
        if !cfg.enabled && !explicit_file{return Err("Turn on speech to text in Models before using the microphone.".into());}
        if !self.installed(){return Err("Install the selected speech model and its runtime in Models.".into());}
        let audio=std::env::temp_dir().join(format!("opencore-speech-{id}.audio"));
        let cancel=CancellationToken::new();
        let (input,ready)=oneshot::channel();
        let (ready_tx,ready_rx)=watch::channel(None);
        let (result_tx,result)=watch::channel(None);
        *session_guard=Some(Session{id:id.clone(),audio:audio.clone(),input:Some(input),result,ready:ready_rx.clone(),cancel:cancel.clone(),_gpu:gpu});
        drop(session_guard); drop(control);
        let manager=self.clone();
        tokio::spawn(async move{
            let mut active_worker: Option<Worker> = None;
            let outcome:Result<Value,String>=async{
                if cancel.is_cancelled() { return Err("Recording cancelled".into()); }
                let mut existing=manager.worker.lock().await.take();
                if existing.as_ref().is_some_and(|worker|worker.model_id!=cfg.model_id || (cfg.model_id=="phonon-2" && worker.runtime_precision!=cfg.runtime_precision)) {
                    if let Some(mut worker)=existing.take() { worker.stop().await; }
                }
                active_worker=Some(match existing {
                    Some(worker)=>worker,
                    None=>{
                        manager.set_phase("loading");
                        let mode=manager.settings().idle_mode;
                        manager.spawn_worker(&cfg.model_id,&cfg.runtime_precision,&mode,mode=="cold",&cancel).await?
                    }
                });
                let worker=active_worker.as_mut().unwrap();
                let awake=if worker.child.id().is_some() && !worker.awake {
                    manager.set_phase("activating-device");
                    if let Ok(mut state)=manager.startup.lock(){state.started=Some(Instant::now());}
                    worker.send(json!({"action":"wake"})).await?;
                    let result=tokio::select!{_ = cancel.cancelled()=>Err("Recording cancelled".into()),v=worker.read()=>v};
                    if let Ok(mut state)=manager.startup.lock(){state.started=None;}
                    let result=result?;
                    if let Some(error)=result["error"].as_str(){return Err(format!("Speech could not move to the GPU or CPU: {error}"));}
                    if result["awake"] != true {return Err("Speech did not enter the recording state.".into());}
                    worker.awake=true;
                    result["wakeMs"].as_u64()
                }else{None};
                if let Some(ms)=awake {
                    let cfg=manager.settings();
                    let _=manager.save_settings(Settings{warm_wake_ms:Some(ms),..cfg});
                }
                let _=ready_tx.send(Some(Ok(())));
                manager.set_phase("recording");
                wait_recording(ready, &cancel).await?;
                manager.set_phase("transcribing");
                worker.send(json!({"action":"transcribe","audio":audio})).await?;
                let output=tokio::select!{_ = cancel.cancelled()=>return Err("Recording cancelled".into()),v=worker.read()=>v?};
                if let Some(error)=output["error"].as_str(){return Err(format!("Speech: {error}"));}
                worker.awake=false;
                if manager.settings().idle_mode=="ram" && manager.settings().enabled{
                    *manager.worker.lock().await=active_worker.take();
                    manager.set_phase("sleeping");
                }else{
                    manager.set_phase("unloading");
                    worker.stop().await;
                    manager.set_phase(if manager.settings().enabled{"ready"}else{"off"});
                }
                Ok(output)
            }.await;
            if let Ok(mut state)=manager.startup.lock(){state.started=None;}
            // Signal completion only after the process has exited and released
            // CUDA allocations; the next composer waits on this result.
            if let Some(mut worker)=active_worker.take(){
                if cancel.is_cancelled(){worker.abort().await;}else{worker.stop().await;}
            }
            if let Err(error)=&outcome{
                let _=ready_tx.send(Some(Err(error.clone())));
                if let Some(mut w)=manager.worker.lock().await.take(){w.stop().await;}
                manager.set_phase(if manager.settings().enabled{"ready"}else{"off"});
            }
            let _=tokio::fs::remove_file(audio).await;
            let _=result_tx.send(Some(outcome));
        });
        let mut ready_rx=ready_rx;
        let ready_result=match tokio::time::timeout(Duration::from_secs(180),wait_ready(&mut ready_rx)).await {
            Ok(result)=>result,
            Err(_)=>{self.cancel(&id).await;return Err("The speech model took too long to load. Try RAM standby in Models.".into());}
        };
        if let Err(error)=ready_result { self.cancel(&id).await; return Err(error); }
        Ok(id)
    }
    pub async fn transcribe(&self,id:&str,encoded:&str)->Result<Value,String>{
        self.ensure_not_updating()?;
        let result=async{
            if encoded.len()>24*1024*1024{return Err("Recording is too large".into());}
            let bytes=base64::engine::general_purpose::STANDARD.decode(encoded).map_err(|e|e.to_string())?;
            if bytes.is_empty(){return Err("Recording was empty".into());}
            let mut guard=self.session.lock().await;
            let session=guard.as_mut().filter(|s|s.id==id).ok_or("Microphone session expired")?;
            tokio::fs::write(&session.audio,bytes).await.map_err(|e|e.to_string())?;
            let input=session.input.take().ok_or("Recording already submitted")?;
            input.send(()).map_err(|_|"Speech stopped before transcription")?;
            let mut result=session.result.clone(); drop(guard); wait_result(&mut result).await
        }.await;
        self.cancel(id).await;
        result
    }
    pub async fn cancel(&self,id:&str){
        let _control=self.control.lock().await;
        let mut guard=self.session.lock().await;
        if guard.as_ref().is_some_and(|s|s.id==id){
            let mut session=guard.take().unwrap();session.cancel.cancel();drop(guard);
            let _=wait_result(&mut session.result).await;
        }
    }
    pub async fn is_active(&self)->bool{self.session.lock().await.is_some()}
    pub async fn release_idle_model(&self) -> Result<(),String> {
        let _interrupt={
            let session=self.session.lock().await;
            if session.is_some(){return Err("Finish dictation before switching models".into());}
            self.interrupt_startup()
        };
        let _control=self.control.lock().await;
        if self.is_active().await {return Err("Finish dictation before switching models".into());}
        if let Some(mut worker)=self.worker.lock().await.take(){worker.stop().await;}
        self.set_phase(if self.settings().enabled {"ready"} else {"off"});
        Ok(())
    }
    pub async fn stop_for_update(&self) {
        let _interrupt=self.interrupt_startup();
        let _control = self.control.lock().await;
        self.cancel_active().await;
        if let Some(mut worker) = self.worker.lock().await.take() { worker.stop().await; }
        self.set_phase(if self.settings().enabled { "ready" } else { "off" });
    }
}
async fn wait_ready(receiver:&mut watch::Receiver<Option<Result<(),String>>>)->Result<(),String>{
    loop{if let Some(result)=receiver.borrow().clone(){return result;}receiver.changed().await.map_err(|_|"Speech loading worker disconnected")?;}
}
async fn wait_result(receiver:&mut watch::Receiver<Outcome>)->Result<Value,String>{
    loop{if let Some(result)=receiver.borrow().clone(){return result;}receiver.changed().await.map_err(|_|"Speech worker disconnected")?;}
}
async fn wait_recording(input:oneshot::Receiver<()>, cancel:&CancellationToken)->Result<(),String>{
    tokio::select! {
        biased;
        _=cancel.cancelled()=>Err("Recording cancelled".into()),
        result=tokio::time::timeout(Duration::from_secs(185),input)=>result
            .map_err(|_|"Recording timed out".to_string())?
            .map_err(|_|"Recording cancelled".to_string()),
    }
}

#[tauri::command]
pub async fn speech_status(core:tauri::State<'_,Arc<crate::AppCore>>)->Result<SpeechStatus,String>{Ok(core.speech.status())}
#[tauri::command]
pub async fn speech_set_enabled(core:tauri::State<'_,Arc<crate::AppCore>>,enabled:bool)->Result<SpeechStatus,String>{
    let _gpu=if enabled {Some(speech_setting_reservation(&core)?)} else {None};
    core.speech.set_enabled(enabled).await
}
#[tauri::command]
pub async fn speech_set_idle_mode(core:tauri::State<'_,Arc<crate::AppCore>>,mode:String)->Result<SpeechStatus,String>{
    let _gpu=if mode=="ram" {Some(speech_setting_reservation(&core)?)} else {None};
    core.speech.set_idle_mode(&mode).await
}
#[tauri::command]
pub async fn speech_set_model(core:tauri::State<'_,Arc<crate::AppCore>>,model_id:String)->Result<SpeechStatus,String>{
    let _gpu=speech_setting_reservation(&core)?;
    core.speech.set_model(&model_id).await
}
#[tauri::command]
pub async fn speech_set_runtime_precision(core:tauri::State<'_,Arc<crate::AppCore>>,precision:String)->Result<SpeechStatus,String>{
    let _gpu=speech_setting_reservation(&core)?;
    core.speech.set_runtime_precision(&precision).await
}
#[tauri::command]
pub async fn speech_prewarm_session(core:tauri::State<'_,Arc<crate::AppCore>>,webview:tauri::Webview)->Result<SpeechStatus,String>{
    crate::computer_access::require_settings_surface(webview.label())?;
    let _gpu=speech_setting_reservation(&core)?;core.speech.prewarm_session().await
}
#[tauri::command]
pub async fn speech_cancel_prewarm(core:tauri::State<'_,Arc<crate::AppCore>>,webview:tauri::Webview)->Result<SpeechStatus,String>{
    crate::computer_access::require_settings_surface(webview.label())?;
    core.speech.release_idle_model().await?;Ok(core.speech.status())
}
#[tauri::command]
pub async fn speech_clear_runtime_cache(core:tauri::State<'_,Arc<crate::AppCore>>,webview:tauri::Webview)->Result<SpeechStatus,String>{
    crate::computer_access::require_settings_surface(webview.label())?;
    core.speech.clear_runtime_cache().await
}
fn speech_setting_reservation(core:&crate::AppCore)->Result<crate::studio_jobs::GpuReservation,String>{
    if core.runtime_setup.busy() || core.background.busy_gpu() || core.studios.busy() || !core.active_chats.lock().map_err(|e|e.to_string())?.is_empty(){return Err("Finish the current chat, setup or background job before loading speech.".into());}
    let gpu=crate::studio_jobs::reserve_gpu()?;
    if !core.active_chats.lock().map_err(|e|e.to_string())?.is_empty(){return Err("Finish the current chat before loading speech.".into());}
    Ok(gpu)
}
#[tauri::command]
pub async fn speech_start(core:tauri::State<'_,Arc<crate::AppCore>>,session_id:Option<String>)->Result<String,String>{
    if core.runtime_setup.busy() || core.background.busy_gpu() || core.studios.busy() || core.studios.continuation_pending() || !core.active_chats.lock().map_err(|e|e.to_string())?.is_empty(){return Err("Finish the current chat, setup or background job before using the microphone.".into());}
    crate::music_studio::require_idle_gpu().await?;
    let gpu=crate::studio_jobs::reserve_gpu()?;
    if !core.active_chats.lock().map_err(|e|e.to_string())?.is_empty(){return Err("Finish the current chat before using the microphone.".into());}
    let runtime=core.runtime.clone();
    tauri::async_runtime::spawn_blocking(move||runtime.stop()).await.map_err(|e|e.to_string())??;
    core.vision.stop();core.reflex.stop();
    core.speech.start_reserved(session_id.unwrap_or_else(||uuid::Uuid::new_v4().to_string()),gpu).await
}
#[tauri::command]
pub async fn speech_transcribe(core:tauri::State<'_,Arc<crate::AppCore>>,session_id:String,audio:String)->Result<Value,String>{core.speech.transcribe(&session_id,&audio).await}
#[tauri::command]
pub async fn speech_cancel(core:tauri::State<'_,Arc<crate::AppCore>>,session_id:String)->Result<(),String>{core.speech.cancel(&session_id).await;Ok(())}

#[cfg(test)]
mod tests {
    use super::*;
    async fn protocol_fixture(source:&str) -> Worker {
        let mut child=tokio::process::Command::new("python").args(["-u","-c",source])
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true).spawn().unwrap();
        Worker{input:child.stdin.take().unwrap(),output:BufReader::new(child.stdout.take().unwrap()),child,
            model_id:"phonon-2".into(),runtime_precision:"bf16".into(),cold_start_ms:None,wake_ms:None,awake:false}
    }
    #[tokio::test]
    async fn loading_protocol_reads_progress_before_ready_and_kills_cancelled_startup() {
        let manager=SpeechManager::new(PathBuf::from("unused"),PathBuf::from("unused"));
        let mut worker=protocol_fixture("import json; print(json.dumps({'progress':'expanding-weights'})); print(json.dumps({'ready':True,'runtimePrecision':'bf16'}))").await;
        let ready=manager.wait_startup_ready(&mut worker,&CancellationToken::new(),&CancellationToken::new()).await.unwrap();
        assert_eq!(ready["runtimePrecision"],"bf16");assert_eq!(manager.status().phase,"expanding-weights");
        worker.child.wait().await.unwrap();
        let mut worker=protocol_fixture("import json,time; print(json.dumps({'progress':'expanding-weights'})); time.sleep(120)").await;
        let cancel=CancellationToken::new();let signal=cancel.clone();
        tokio::spawn(async move {tokio::time::sleep(Duration::from_millis(30)).await;signal.cancel();});
        let error=tokio::time::timeout(Duration::from_secs(2),manager.wait_startup_ready(&mut worker,&cancel,&CancellationToken::new())).await.unwrap().unwrap_err();
        assert!(error.contains("cancelled"));assert!(worker.child.try_wait().unwrap().is_some());
    }
    #[test]
    fn phonon_precision_migration_preserves_cold_and_explicit_fp32() {
        for mode in ["cold","ram"] {
            let legacy=decode_settings(&serde_json::to_vec(&json!({"modelId":"phonon-2","enabled":true,"idleMode":mode,"coldStartMs":25890,"warmWakeMs":797})).unwrap()).unwrap();
            assert_eq!(legacy.runtime_precision,"bf16");assert_eq!(legacy.idle_mode,mode);assert!(legacy.enabled);
            assert_eq!(legacy.cold_start_ms,None);assert_eq!(legacy.warm_wake_ms,None);
        }
        let explicit=decode_settings(br#"{"modelId":"phonon-2","runtimePrecision":"fp32","enabled":true,"idleMode":"ram","coldStartMs":25890,"warmWakeMs":797}"#).unwrap();
        assert_eq!(explicit.runtime_precision,"fp32");assert_eq!(explicit.idle_mode,"ram");
        assert_eq!(explicit.cold_start_ms,Some(25890));assert_eq!(explicit.warm_wake_ms,Some(797));
        let whisper=decode_settings(br#"{"enabled":true,"idleMode":"ram","coldStartMs":3339,"warmWakeMs":820}"#).unwrap();
        assert_eq!(whisper.cold_start_ms,Some(3339));assert_eq!(whisper.warm_wake_ms,Some(820));
    }
    #[test]
    fn loading_protocol_accepts_known_stages_and_rejects_unknown_payloads() {
        assert_eq!(startup_progress(&json!({"progress":"expanding-weights"})),Some("expanding-weights"));
        assert_eq!(startup_progress(&json!({"progress":"made-up-percent"})),None);
        assert_eq!(startup_progress(&json!({"ready":true})),None);
        assert_eq!(startup_progress(&json!({"progress":"loading-dense-cache"})),Some("loading-dense-cache"));
    }
    #[tokio::test]
    async fn clearing_derived_cache_preserves_checkpoint_preferences_and_unrelated_files() {
        let root=std::env::temp_dir().join(format!("speech-cache-clear-{}",uuid::Uuid::new_v4()));
        let cache=root.join("speech/runtime-cache/phonon-2");std::fs::create_dir_all(&cache).unwrap();
        std::fs::write(cache.join("expanded-bf16.pt"),b"derived").unwrap();std::fs::write(cache.join("expanded-bf16.json"),b"{}").unwrap();
        std::fs::write(cache.join("keep.txt"),b"unrelated").unwrap();std::fs::create_dir_all(root.join("speech/phonon-2")).unwrap();
        let checkpoint=root.join("speech/phonon-2/model.fermion");std::fs::write(&checkpoint,b"checkpoint").unwrap();
        let manager=SpeechManager::new(root,PathBuf::new());manager.save_settings(Settings{model_id:"phonon-2".into(),enabled:true,idle_mode:"cold".into(),..Settings::default()}).unwrap();
        assert_eq!(manager.status().runtime_cache_bytes,7);
        assert_eq!(manager.clear_runtime_cache().await.unwrap().runtime_cache_bytes,0);
        assert_eq!(std::fs::read(checkpoint).unwrap(),b"checkpoint");assert!(cache.join("keep.txt").is_file());
        assert_eq!(manager.settings().idle_mode,"cold");assert!(manager.settings().enabled);
    }
    #[tokio::test]
    async fn standby_stops_interrupt_startup_before_waiting_for_control() {
        for operation in ["update","off","cold","release"] {
            let root=std::env::temp_dir().join(format!("speech-standby-stop-{}",uuid::Uuid::new_v4()));
            let manager=SpeechManager::new(root.clone(),root.clone());
            let lock=manager.control.lock().await;
            let token={let mut state=manager.startup.lock().unwrap();state.started=Some(Instant::now());state.cancel.clone()};
            assert!(manager.status().loading_elapsed_ms.is_some());
            let stopping=manager.clone();
            let task=tokio::spawn(async move {match operation {
                "update"=>stopping.stop_for_update().await,
                "off"=>{stopping.set_enabled(false).await.unwrap();},
                "cold"=>{stopping.set_idle_mode("cold").await.unwrap();},
                _=>stopping.release_idle_model().await.unwrap(),
            }});
            tokio::time::timeout(Duration::from_secs(1),token.cancelled()).await.unwrap();
            assert!(!task.is_finished(),"Control must remain sequential after cancellation is signalled");
            drop(lock);
            task.await.unwrap();
            assert_eq!(manager.status().phase,"off");
            assert!(!root.exists());
        }
    }
    #[tokio::test]
    async fn rejected_idle_changes_preserve_active_dictation_and_its_startup_token() {
        for release in [false,true] {
            let root=std::env::temp_dir().join(format!("speech-active-settings-{}",uuid::Uuid::new_v4()));
            let manager=SpeechManager::new(root.clone(),root.clone());
            let session_cancel=CancellationToken::new();
            let (input,_audio)=oneshot::channel();
            let (_result_tx,result)=watch::channel(None);
            let (_ready_tx,ready)=watch::channel(None);
            *manager.session.lock().await=Some(Session{id:"active".into(),audio:root.join("pending.audio"),
                input:Some(input),result,ready,cancel:session_cancel.clone(),_gpu:None});
            let startup_cancel={let mut state=manager.startup.lock().unwrap();state.started=Some(Instant::now());state.cancel.clone()};
            manager.set_phase("expanding-weights");
            let control=manager.control.lock().await;
            let rejected=tokio::time::timeout(Duration::from_secs(1),async {
                if release {manager.release_idle_model().await} else {manager.set_idle_mode("cold").await.map(|_|())}
            }).await;
            assert!(!startup_cancel.is_cancelled(),"A rejected idle change must not cancel dictation startup");
            assert!(!session_cancel.is_cancelled());
            assert!(rejected.unwrap().unwrap_err().contains("dictation"));
            let session=manager.session.lock().await;
            assert_eq!(session.as_ref().unwrap().id,"active");
            assert!(session.as_ref().unwrap().input.is_some());
            let startup=manager.startup.lock().unwrap();
            assert!(startup.started.is_some());assert_eq!(startup.interrupts,0);
            assert_eq!(*manager.phase.lock().unwrap(),"expanding-weights");
            assert!(!root.exists());
            drop(control);
        }
    }
    #[tokio::test]
    async fn pending_idle_change_rejects_dictation_before_publishing_a_session() {
        let root=std::env::temp_dir().join(format!("speech-settings-transition-{}",uuid::Uuid::new_v4()));
        let manager=SpeechManager::new(root.clone(),root.clone());
        let _change=manager.interrupt_startup();
        let error=manager.start_file().await.unwrap_err();
        assert!(error.contains("Speech settings are changing"),"Reject at admission, before checking/loading models: {error}");
        assert!(!manager.is_active().await);
        assert!(!root.exists());
    }
    #[tokio::test]
    async fn cold_precision_choice_is_persisted_without_starting_or_downloading_a_model() {
        let root=std::env::temp_dir().join(format!("speech-precision-{}",uuid::Uuid::new_v4()));
        let manager=SpeechManager::new(root.clone(),root.clone());
        manager.save_settings(Settings{model_id:"phonon-2".into(),enabled:true,..Settings::default()}).unwrap();
        for precision in ["original","bf16"] {
            let status=manager.set_runtime_precision(precision).await.unwrap();
            assert_eq!(status.runtime_precision,precision);assert!(!status.worker_ready);
            assert_eq!(SpeechManager::new(root.clone(),root.clone()).settings().runtime_precision,precision);
        }
        let status=manager.set_runtime_precision("fp32").await.unwrap();
        assert_eq!(status.runtime_precision,"fp32");assert_eq!(status.idle_mode,"cold");assert!(!status.worker_ready);
        assert!(manager.set_runtime_precision("nf4").await.is_err());
        let restored=SpeechManager::new(root.clone(),root.clone());
        assert_eq!(restored.settings().runtime_precision,"fp32");
        assert!(!root.join("speech/phonon-2").exists());
        std::fs::remove_dir_all(root).unwrap();
    }
    #[tokio::test]
    async fn update_gate_rejects_new_microphone_work() {
        let root=std::env::temp_dir().join(format!("speech-update-gate-{}",uuid::Uuid::new_v4()));
        let update=Arc::new(AtomicBool::new(true));
        let manager=SpeechManager::new_with_update_gate(root.clone(),root.clone(),update);
        let error=manager.start_file().await.unwrap_err();
        assert!(error.contains("install an update"));
        assert!(!manager.is_active().await);
        assert!(!root.exists());
    }
    #[tokio::test]
    async fn update_stop_cancels_a_live_dictation_before_returning() {
        let root=std::env::temp_dir().join(format!("speech-update-stop-{}",uuid::Uuid::new_v4()));
        let manager=SpeechManager::new(root.clone(),root.clone());
        let cancel=CancellationToken::new();
        let (input,audio)=oneshot::channel();
        let (result_tx,result)=watch::channel(None);
        let (_ready_tx,ready)=watch::channel(None);
        *manager.session.lock().await=Some(Session{id:"recording".into(),audio:root.join("pending.audio"),input:Some(input),
            result,ready,cancel:cancel.clone(),_gpu:None});
        tokio::spawn(async move {
            let outcome=wait_recording(audio,&cancel).await.map(|_|json!({"text":"unexpected"}));
            let _=result_tx.send(Some(outcome));
        });
        tokio::time::timeout(Duration::from_secs(1),manager.stop_for_update()).await.unwrap();
        assert!(!manager.is_active().await);
        assert_eq!(manager.status().phase,"off");
        if root.exists(){std::fs::remove_dir_all(root).unwrap();}
    }
    #[tokio::test]
    async fn microphone_cannot_start_while_a_background_job_owns_gpu(){
        let root=std::env::temp_dir().join(format!("speech-gpu-{}",uuid::Uuid::new_v4()));
        let manager=SpeechManager::new(root.clone(),root);
        let _job=crate::studio_jobs::reserve_gpu().unwrap();
        let error=manager.start_with_id(uuid::Uuid::new_v4().to_string()).await.unwrap_err();
        assert!(error.contains("GPU is reserved"),"Reject before any speech worker starts: {error}");
        assert!(!manager.is_active().await);
    }
    #[tokio::test]
    async fn cancelling_recording_does_not_wait_for_the_unsent_audio(){
        for disable in [false,true] {
            let root=std::env::temp_dir().join(format!("opencore-speech-cancel-{}",uuid::Uuid::new_v4()));
            let manager=SpeechManager::new(root.clone(),root.clone());
            if disable {
                manager.save_settings(Settings{enabled:true,..Settings::default()}).unwrap();
            }
            let cancel=CancellationToken::new();
            let (input,audio)=oneshot::channel();
            let (_ready_tx,ready)=watch::channel(None);
            let (result_tx,result)=watch::channel(None);
            *manager.session.lock().await=Some(Session{id:"recording".into(),audio:root.join("pending.audio"),
                input:Some(input),result,ready,cancel:cancel.clone(),_gpu:None});
            let task=tokio::spawn(async move{
                let result=wait_recording(audio,&cancel).await.map(|_|json!({"text":"unexpected"}));
                let _=result_tx.send(Some(result));
            });
            let cancelled=tokio::time::timeout(Duration::from_secs(1),async{
                if disable {manager.set_enabled(false).await.unwrap();}
                else {manager.cancel("recording").await;}
            }).await;
            assert!(cancelled.is_ok(),"Cancelling must release the recording wait before its 185-second timeout");
            task.await.unwrap();
            assert!(!manager.is_active().await);
            if root.exists(){std::fs::remove_dir_all(root).unwrap();}
        }
    }
    #[test]
    fn whisper_defaults_to_cold_start_and_persists_the_ram_preference(){
        let cfg=Settings::default();
        assert!(!cfg.enabled);
        assert_eq!(cfg.idle_mode,"cold");
    }
    #[tokio::test]
    async fn microphone_handoff_waits_for_worker_exit_and_old_cancel_cannot_stop_new_session(){
        let manager=SpeechManager::new(PathBuf::from("unused"),PathBuf::from("unused"));
        let (input,_audio)=oneshot::channel();let (tx,result)=watch::channel(None);let (_rtx,ready)=watch::channel(None);
        *manager.session.lock().await=Some(Session{id:"old".into(),audio:PathBuf::from("unused.audio"),input:Some(input),result,ready,cancel:CancellationToken::new(),_gpu:None});
        let cancelling=manager.clone();
        let release=tokio::spawn(async move {cancelling.cancel("old").await;});
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert!(manager.control.try_lock().is_err(),"A new start must wait for the old worker to exit");
        tx.send(Some(Err("Recording cancelled".into()))).unwrap();release.await.unwrap();
        let (input,_audio)=oneshot::channel();let (_tx,result)=watch::channel(None);let (_rtx,ready)=watch::channel(None);
        *manager.session.lock().await=Some(Session{id:"new".into(),audio:PathBuf::from("unused.audio"),input:Some(input),result,ready,cancel:CancellationToken::new(),_gpu:None});
        manager.cancel("old").await;
        assert_eq!(manager.session.lock().await.as_ref().unwrap().id,"new");
    }
    #[test]
    fn legacy_speech_settings_keep_the_actual_turbo_checkpoint(){
        let cfg:Settings=serde_json::from_str(r#"{"enabled":true,"idleMode":"ram","coldStartMs":3339,"warmWakeMs":820}"#).unwrap();
        assert_eq!(cfg.model_id,"whisper-large-v3-turbo");
        assert!(cfg.enabled);assert_eq!(cfg.idle_mode,"ram");
    }
    #[tokio::test]
    async fn failed_model_selection_preserves_settings_and_does_not_download(){
        let root=std::env::temp_dir().join(format!("opencore-speech-model-{}",uuid::Uuid::new_v4()));
        let manager=SpeechManager::new(root.clone(),root.clone());
        assert!(manager.set_model("echo").await.unwrap_err().contains("Unknown speech"));
        assert!(manager.set_model("phonon-2").await.unwrap_err().contains("not installed"));
        assert_eq!(manager.selected_model(),"whisper-large-v3-turbo");
        assert!(!root.exists());
    }
    #[tokio::test]
    async fn model_changes_are_rejected_during_dictation(){
        let root=std::env::temp_dir().join(format!("opencore-speech-active-{}",uuid::Uuid::new_v4()));
        let manager=SpeechManager::new(root.clone(),root.clone());
        let (input,_audio)=oneshot::channel();let (_tx,result)=watch::channel(None);let (_ready_tx,ready)=watch::channel(None);
        *manager.session.lock().await=Some(Session{id:"recording".into(),audio:root.join("audio"),input:Some(input),result,ready,cancel:CancellationToken::new(),_gpu:None});
        assert!(manager.set_model("phonon-2").await.unwrap_err().contains("Finish the current dictation"));
        assert_eq!(manager.selected_model(),"whisper-large-v3-turbo");
    }
    #[tokio::test]
    #[ignore="Requires the installed large-v3 CUDA runtime and artifacts/speech-test.wav"]
    async fn installed_whisper_transcribes_and_cleans_up(){
        let source=PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let root=PathBuf::from(std::env::var("USERPROFILE").unwrap()).join("OpenCore");
        let manager=SpeechManager::new(root,source.join("resources"));
        manager.set_enabled(true).await.unwrap();
        let id=manager.start().await.unwrap();
        assert!(manager.start().await.is_err(),"Only one GPU worker may run per app");
        let audio_path=manager.session.lock().await.as_ref().unwrap().audio.clone();
        let bytes=std::fs::read(source.join("../artifacts/speech-test.wav")).unwrap();
        let result=manager.transcribe(&id,&base64::engine::general_purpose::STANDARD.encode(bytes)).await.unwrap();
        assert!(result["text"].as_str().unwrap().to_lowercase().contains("running"));
        assert!(manager.session.lock().await.is_none());
        assert!(!audio_path.exists());
    }
}
