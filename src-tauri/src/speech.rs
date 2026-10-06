use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::PathBuf, process::Stdio, sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex as StdMutex}, time::Duration};
use tokio::{io::{AsyncBufReadExt, AsyncWriteExt, BufReader}, process::{Child, ChildStdin}, sync::{Mutex, oneshot, watch}};
use tokio_util::sync::CancellationToken;

type Outcome = Option<Result<Value, String>>;
struct Session {
    id: String, audio: PathBuf, input: Option<oneshot::Sender<()>>,
    result: watch::Receiver<Outcome>, ready: watch::Receiver<Option<Result<(), String>>>, cancel: CancellationToken,
    _gpu: Option<crate::studio_jobs::GpuReservation>,
}
struct Worker { child: Child, input: ChildStdin, output: BufReader<tokio::process::ChildStdout>, model_id: String, cold_start_ms: Option<u64>, wake_ms: Option<u64> }
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
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Settings { #[serde(default="default_model_id")] model_id: String, enabled: bool, idle_mode: String, cold_start_ms: Option<u64>, warm_wake_ms: Option<u64> }
fn default_model_id() -> String { "whisper-large-v3-turbo".into() }
impl Default for Settings {
    fn default() -> Self { Self { model_id: default_model_id(), enabled: false, idle_mode: "cold".into(), cold_start_ms: None, warm_wake_ms: None } }
}
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SpeechStatus {
    model_id: String,
    installed: bool, enabled: bool, idle_mode: String, worker_ready: bool,
    cold_start_ms: Option<u64>, warm_wake_ms: Option<u64>, phase: String,
}
pub struct SpeechManager {
    root: PathBuf, resources: PathBuf, control: Arc<Mutex<()>>,
    session: Arc<Mutex<Option<Session>>>, worker: Arc<Mutex<Option<Worker>>>,
    settings: Arc<StdMutex<Settings>>, phase: Arc<StdMutex<String>>,
    update_in_progress: Arc<AtomicBool>,
}
impl Clone for SpeechManager {
    fn clone(&self) -> Self { Self { root:self.root.clone(), resources:self.resources.clone(), control:self.control.clone(),
        session:self.session.clone(), worker:self.worker.clone(), settings:self.settings.clone(), phase:self.phase.clone(), update_in_progress:self.update_in_progress.clone() } }
}
impl SpeechManager {
    pub fn new(root: PathBuf, resources: PathBuf) -> Self {
        Self::new_with_update_gate(root, resources, Arc::new(AtomicBool::new(false)))
    }
    pub fn new_with_update_gate(root: PathBuf, resources: PathBuf, update_in_progress: Arc<AtomicBool>) -> Self {
        let speech_root = root.join("speech");
        let settings = std::fs::read(speech_root.join("settings.json")).ok()
            .and_then(|v| serde_json::from_slice(&v).ok()).unwrap_or_default();
        Self { root:speech_root, resources, control:Arc::new(Mutex::new(())), session:Arc::new(Mutex::new(None)),
            worker:Arc::new(Mutex::new(None)), settings:Arc::new(StdMutex::new(settings)),
            phase:Arc::new(StdMutex::new("off".into())), update_in_progress }
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
    fn status(&self) -> SpeechStatus {
        let cfg=self.settings();
        SpeechStatus { model_id:cfg.model_id.clone(), installed:self.installed(), enabled:cfg.enabled, idle_mode:cfg.idle_mode.clone(),
            worker_ready:self.worker.try_lock().map(|w|w.is_some()).unwrap_or(false),
            cold_start_ms:cfg.cold_start_ms, warm_wake_ms:cfg.warm_wake_ms,
            phase:self.phase.lock().map(|p|p.clone()).unwrap_or_else(|_|"off".into()) }
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
            match self.spawn_worker(&cfg.model_id,"ram",false,&CancellationToken::new()).await {
                Ok(worker)=>{*self.worker.lock().await=Some(worker);self.set_phase("sleeping");Ok(())}
                Err(error)=>{self.set_phase("error");Err(error)}
            }
        } else { self.set_phase("ready"); Ok(()) }
    }
    async fn spawn_worker(&self, model_id:&str, mode:&str, awake:bool, cancel:&CancellationToken) -> Result<Worker,String> {
        let python=crate::model_catalog::speech_python_path(self.root.parent().unwrap_or(&self.root), model_id);
        let model=self.model_dir(model_id)?;
        let mut command=tokio::process::Command::new(python);
        command.arg(self.resources.join("speech").join(if model_id=="phonon-2" {"phonon_worker.py"} else {"whisper_worker.py"})).arg("--model").arg(&model).arg("--idle-mode").arg(mode);
        if awake { command.arg("--awake"); }
        command.env("PYTHONIOENCODING","utf-8").env("HF_HUB_OFFLINE","1")
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
        #[cfg(windows)] command.creation_flags(0x0800_0000);
        let mut child=command.spawn().map_err(|e|format!("Could not start Speech: {e}"))?;
        #[cfg(windows)]
        if let Some(handle)=child.raw_handle(){crate::child_guard::adopt_handle(handle);}
        let mut worker=Worker { input:child.stdin.take().ok_or("Missing Speech input")?,
            output:BufReader::new(child.stdout.take().ok_or("Missing speech output")?), child, model_id:model_id.into(), cold_start_ms:None, wake_ms:None };
        let ready=tokio::select! {
            _=cancel.cancelled()=>{worker.stop().await;return Err("Speech startup was cancelled".into());}
            value=worker.read()=>value?
        };
        if let Some(error)=ready["error"].as_str(){worker.stop().await;return Err(format!("Speech failed to load: {error}"));}
        if ready["ready"]!=true { worker.stop().await;return Err("Speech exited before loading its model".into()); }
        let mut cfg=self.settings();
        worker.cold_start_ms=ready["coldStartMs"].as_u64();
        worker.wake_ms=ready["wakeMs"].as_u64();
        if cfg.model_id==model_id {
            cfg.cold_start_ms=worker.cold_start_ms;
            cfg.warm_wake_ms=worker.wake_ms.or(cfg.warm_wake_ms);
            let _=self.save_settings(cfg);
        }
        Ok(worker)
    }
    pub async fn set_enabled(&self, enabled: bool) -> Result<SpeechStatus,String> {
        let _control=self.control.lock().await;
        if enabled { self.ensure_not_updating()?; }
        let cfg=self.settings();
        if cfg.enabled == enabled { return Ok(self.status()); }
        if enabled {
        if !self.installed(){return Err("Install the selected speech model and its runtime from Models first.".into());}
            if cfg.idle_mode=="ram" {
                self.set_phase("warming");
                let worker=self.spawn_worker(&cfg.model_id,"ram",false,&CancellationToken::new()).await?;
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
        let _control=self.control.lock().await;
        if mode == "ram" { self.ensure_not_updating()?; }
        if !["cold","ram"].contains(&mode){return Err("Choose cold or ram idle mode".into());}
        if self.is_active().await{return Err("Finish the current dictation before changing its sleep mode.".into());}
        let cfg=self.settings();
        if cfg.idle_mode == mode { return Ok(self.status()); }
        if cfg.enabled && mode=="ram" {
            self.set_phase("warming");
            let w=self.spawn_worker(&cfg.model_id,"ram",false,&CancellationToken::new()).await?;
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
            let worker=match self.spawn_worker(id,"ram",false,&CancellationToken::new()).await {
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
        crate::model_catalog::require_idle()?;
        let cfg=self.settings();
        if !cfg.enabled && !explicit_file{return Err("Turn on speech to text in Models before using the microphone.".into());}
        if !self.installed(){return Err("Install the selected speech model and its runtime in Models.".into());}
        let mut session_guard=self.session.lock().await;
        if session_guard.is_some(){return Err("A microphone session is already active".into());}
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
                if existing.as_ref().is_some_and(|worker|worker.model_id!=cfg.model_id) {
                    if let Some(mut worker)=existing.take() { worker.stop().await; }
                }
                active_worker=Some(match existing {
                    Some(worker)=>worker,
                    None=>{
                        manager.set_phase("loading");
                        let mode=manager.settings().idle_mode;
                        manager.spawn_worker(&cfg.model_id,&mode,mode=="cold",&cancel).await?
                    }
                });
                let worker=active_worker.as_mut().unwrap();
                let awake=if worker.child.id().is_some() && manager.settings().idle_mode=="ram" {
                    manager.set_phase("loading");
                    worker.send(json!({"action":"wake"})).await?;
                    let result=tokio::select!{_ = cancel.cancelled()=>return Err("Recording cancelled".into()),v=worker.read()=>v?};
                    if let Some(error)=result["error"].as_str(){return Err(format!("Speech could not move to the GPU or CPU: {error}"));}
                    if result["awake"] != true {return Err("Speech did not enter the recording state.".into());}
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
            // Signal completion only after the process has exited and released
            // CUDA allocations; the next composer waits on this result.
            if let Some(mut worker)=active_worker.take(){worker.stop().await;}
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
        let _control=self.control.lock().await;
        if self.is_active().await {return Err("Finish dictation before switching models".into());}
        if let Some(mut worker)=self.worker.lock().await.take(){worker.stop().await;}
        self.set_phase(if self.settings().enabled {"ready"} else {"off"});
        Ok(())
    }
    pub async fn stop_for_update(&self) {
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
fn speech_setting_reservation(core:&crate::AppCore)->Result<crate::studio_jobs::GpuReservation,String>{
    if core.studios.busy() || !core.active_chats.lock().map_err(|e|e.to_string())?.is_empty(){return Err("Finish the current chat or background job before loading speech.".into());}
    let gpu=crate::studio_jobs::reserve_gpu()?;
    if !core.active_chats.lock().map_err(|e|e.to_string())?.is_empty(){return Err("Finish the current chat before loading speech.".into());}
    Ok(gpu)
}
#[tauri::command]
pub async fn speech_start(core:tauri::State<'_,Arc<crate::AppCore>>,session_id:Option<String>)->Result<String,String>{
    if core.studios.busy() || core.studios.continuation_pending() || !core.active_chats.lock().map_err(|e|e.to_string())?.is_empty(){return Err("Finish the current chat or background job before using the microphone.".into());}
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
