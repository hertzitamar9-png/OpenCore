use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::PathBuf, process::Stdio, sync::{Arc, Mutex as StdMutex}, time::Duration};
use tokio::{io::{AsyncBufReadExt, AsyncWriteExt, BufReader}, process::{Child, ChildStdin}, sync::{Mutex, oneshot, watch}};
use tokio_util::sync::CancellationToken;

type Outcome = Option<Result<Value, String>>;
struct Session {
    id: String, audio: PathBuf, input: Option<oneshot::Sender<()>>,
    result: watch::Receiver<Outcome>, ready: watch::Receiver<Option<Result<(), String>>>, cancel: CancellationToken,
}
struct Worker { child: Child, input: ChildStdin, output: BufReader<tokio::process::ChildStdout> }
impl Worker {
    async fn read(&mut self) -> Result<Value, String> {
        let mut line = String::new();
        tokio::time::timeout(Duration::from_secs(190), self.output.read_line(&mut line)).await
            .map_err(|_| "Whisper did not respond within 190 seconds".to_string())?
            .map_err(|e| e.to_string())?;
        if line.len() > 128 * 1024 { return Err("Whisper returned an oversized result".into()); }
        serde_json::from_str(&line).map_err(|e| format!("Could not read the Whisper response: {e}"))
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
struct Settings { enabled: bool, idle_mode: String, cold_start_ms: Option<u64>, warm_wake_ms: Option<u64> }
impl Default for Settings {
    fn default() -> Self { Self { enabled: false, idle_mode: "cold".into(), cold_start_ms: None, warm_wake_ms: None } }
}
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SpeechStatus {
    installed: bool, enabled: bool, idle_mode: String, worker_ready: bool,
    cold_start_ms: Option<u64>, warm_wake_ms: Option<u64>, phase: String,
}
pub struct SpeechManager {
    root: PathBuf, script: PathBuf, setup_script: PathBuf,
    session: Arc<Mutex<Option<Session>>>, worker: Arc<Mutex<Option<Worker>>>,
    settings: Arc<StdMutex<Settings>>, phase: Arc<StdMutex<String>>,
}
impl Clone for SpeechManager {
    fn clone(&self) -> Self { Self { root:self.root.clone(), script:self.script.clone(), setup_script:self.setup_script.clone(),
        session:self.session.clone(), worker:self.worker.clone(), settings:self.settings.clone(), phase:self.phase.clone() } }
}
impl SpeechManager {
    pub fn new(root: PathBuf, resources: PathBuf) -> Self {
        let speech_root = root.join("speech");
        let settings = std::fs::read(speech_root.join("settings.json")).ok()
            .and_then(|v| serde_json::from_slice(&v).ok()).unwrap_or_default();
        Self { root:speech_root, script:resources.join("speech/whisper_worker.py"),
            setup_script:resources.join("speech/prepare_runtime.py"), session:Arc::new(Mutex::new(None)),
            worker:Arc::new(Mutex::new(None)), settings:Arc::new(StdMutex::new(settings)),
            phase:Arc::new(StdMutex::new("off".into())) }
    }
    fn settings(&self) -> Settings { self.settings.lock().map(|v|v.clone()).unwrap_or_default() }
    fn save_settings(&self, next: Settings) -> Result<(), String> {
        std::fs::create_dir_all(&self.root).map_err(|e|e.to_string())?;
        std::fs::write(self.root.join("settings.json"), serde_json::to_vec_pretty(&next).map_err(|e|e.to_string())?)
            .map_err(|e|e.to_string())?;
        *self.settings.lock().map_err(|e|e.to_string())? = next;
        Ok(())
    }
    fn model_dir(&self) -> PathBuf {
        crate::model_catalog::whisper_model_path(self.root.parent().unwrap_or(&self.root))
    }
    fn installed(&self) -> bool {
        crate::model_catalog::whisper_model_available(self.root.parent().unwrap_or(&self.root)) &&
            self.root.join("runtime.json").is_file() && self.root.join("venv/Scripts/python.exe").is_file()
    }
    fn set_phase(&self, phase: &str) { if let Ok(mut p)=self.phase.lock() { *p=phase.into(); } }
    fn status(&self) -> SpeechStatus {
        let cfg=self.settings();
        SpeechStatus { installed:self.installed(), enabled:cfg.enabled, idle_mode:cfg.idle_mode.clone(),
            worker_ready:self.worker.try_lock().map(|w|w.is_some()).unwrap_or(false),
            cold_start_ms:cfg.cold_start_ms, warm_wake_ms:cfg.warm_wake_ms,
            phase:self.phase.lock().map(|p|p.clone()).unwrap_or_else(|_|"off".into()) }
    }
    pub async fn restore_saved_mode(&self) -> Result<(),String> {
        let cfg=self.settings();
        if !cfg.enabled { self.set_phase("off"); return Ok(()); }
        if !self.installed() {
            self.set_phase("error");
            return Err("Whisper is enabled in settings, but its checkpoint or speech runtime is missing.".into());
        }
        if cfg.idle_mode=="ram" {
            if self.worker.lock().await.is_some() { self.set_phase("sleeping"); return Ok(()); }
            self.set_phase("warming");
            match self.spawn_worker("ram",false,&CancellationToken::new()).await {
                Ok(worker)=>{*self.worker.lock().await=Some(worker);self.set_phase("sleeping");Ok(())}
                Err(error)=>{self.set_phase("error");Err(error)}
            }
        } else { self.set_phase("ready"); Ok(()) }
    }
    async fn spawn_worker(&self, mode:&str, awake:bool, cancel:&CancellationToken) -> Result<Worker,String> {
        let python=self.root.join("venv/Scripts/python.exe");
        let model=self.model_dir();
        let mut command=tokio::process::Command::new(python);
        command.arg(&self.script).arg("--model").arg(&model).arg("--idle-mode").arg(mode);
        if awake { command.arg("--awake"); }
        command.env("PYTHONIOENCODING","utf-8").env("HF_HUB_OFFLINE","1")
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
        #[cfg(windows)] command.creation_flags(0x0800_0000);
        let mut child=command.spawn().map_err(|e|format!("Could not start Whisper: {e}"))?;
        #[cfg(windows)]
        if let Some(handle)=child.raw_handle(){crate::child_guard::adopt_handle(handle);}
        let mut worker=Worker { input:child.stdin.take().ok_or("Missing Whisper input")?,
            output:BufReader::new(child.stdout.take().ok_or("Missing Whisper output")?), child };
        let ready=tokio::select! {
            _=cancel.cancelled()=>{worker.stop().await;return Err("Whisper startup was cancelled".into());}
            value=worker.read()=>value?
        };
        if let Some(error)=ready["error"].as_str(){worker.stop().await;return Err(format!("Whisper failed to load: {error}"));}
        if ready["ready"]!=true { worker.stop().await;return Err("Whisper exited before loading its model".into()); }
        let mut cfg=self.settings();
        cfg.cold_start_ms=ready["coldStartMs"].as_u64();
        cfg.warm_wake_ms=ready["wakeMs"].as_u64().or(cfg.warm_wake_ms);
        let _=self.save_settings(cfg);
        Ok(worker)
    }
    async fn prepare_sleeping_worker(&self) -> Result<(),String> {
        if !self.installed(){return Err("Install Whisper Large V3 Turbo from Models before enabling RAM sleep.".into());}
        let cancel=CancellationToken::new();
        self.set_phase("warming");
        let worker=self.spawn_worker("ram",false,&cancel).await?;
        *self.worker.lock().await=Some(worker);
        self.set_phase("sleeping");
        let cfg=self.settings();
        self.save_settings(Settings{enabled:cfg.enabled,idle_mode:"ram".into(),cold_start_ms:cfg.cold_start_ms,warm_wake_ms:cfg.warm_wake_ms})
    }
    pub async fn set_enabled(&self, enabled: bool) -> Result<SpeechStatus,String> {
        let cfg=self.settings();
        if cfg.enabled == enabled { return Ok(self.status()); }
        if enabled {
        if !self.installed(){return Err("Install Whisper Large V3 Turbo and its speech runtime from the Models tab first.".into());}
            if cfg.idle_mode=="ram" {
                self.set_phase("warming");
                let worker=self.spawn_worker("ram",false,&CancellationToken::new()).await?;
                self.save_settings(Settings{enabled:true,..cfg})?;
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
        if !["cold","ram"].contains(&mode){return Err("Choose cold or ram idle mode".into());}
        if self.is_active().await{return Err("Finish the current dictation before changing its sleep mode.".into());}
        let cfg=self.settings();
        if cfg.idle_mode == mode { return Ok(self.status()); }
        if cfg.enabled && mode=="ram" {
            self.set_phase("warming");
            let w=self.spawn_worker("ram",false,&CancellationToken::new()).await?;
            self.save_settings(Settings{idle_mode:mode.into(),..cfg})?;
            *self.worker.lock().await=Some(w);
            self.set_phase("sleeping");
        } else {
            if let Some(mut w)=self.worker.lock().await.take(){w.stop().await;}
            self.save_settings(Settings{idle_mode:mode.into(),..cfg.clone()})?;
            self.set_phase(if cfg.enabled {"ready"}else{"off"});
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
        crate::model_catalog::require_idle()?;
        let cfg=self.settings();
        if !cfg.enabled{return Err("Turn on Whisper in the Models tab before using the microphone.".into());}
        if !self.installed(){return Err("Install Whisper Large V3 Turbo and its speech runtime in the Models tab.".into());}
        let mut session_guard=self.session.lock().await;
        if session_guard.is_some(){return Err("A microphone session is already active".into());}
        let id=uuid::Uuid::new_v4().to_string();
        let audio=std::env::temp_dir().join(format!("opencore-speech-{id}.audio"));
        let cancel=CancellationToken::new();
        let (input,ready)=oneshot::channel();
        let (ready_tx,ready_rx)=watch::channel(None);
        let (result_tx,result)=watch::channel(None);
        *session_guard=Some(Session{id:id.clone(),audio:audio.clone(),input:Some(input),result,ready:ready_rx.clone(),cancel:cancel.clone()});
        drop(session_guard);
        let manager=self.clone();
        tokio::spawn(async move{
            let outcome:Result<Value,String>=async{
                let existing=manager.worker.lock().await.take();
                let mut worker=match existing {
                    Some(worker)=>worker,
                    None=>{
                        manager.set_phase("loading");
                        let mode=manager.settings().idle_mode;
                        manager.spawn_worker(&mode,mode=="cold",&cancel).await?
                    }
                };
                let awake=if worker.child.id().is_some() && manager.settings().idle_mode=="ram" {
                    manager.set_phase("loading");
                    worker.send(json!({"action":"wake"})).await?;
                    let result=tokio::select!{_ = cancel.cancelled()=>return Err("Recording cancelled".into()),v=worker.read()=>v?};
                    if let Some(error)=result["error"].as_str(){return Err(format!("Whisper could not move to the GPU or CPU: {error}"));}
                    if result["awake"] != true {return Err("Whisper did not enter the recording state.".into());}
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
                let id=manager.session.lock().await.as_ref().map(|s|s.id.clone()).ok_or("Microphone session expired")?;
                let file=manager.session.lock().await.as_ref().map(|s|s.audio.clone()).ok_or("Microphone session expired")?;
                let _=id;
                worker.send(json!({"action":"transcribe","audio":file})).await?;
                let output=tokio::select!{_ = cancel.cancelled()=>return Err("Recording cancelled".into()),v=worker.read()=>v?};
                if let Some(error)=output["error"].as_str(){return Err(format!("Whisper: {error}"));}
                if manager.settings().idle_mode=="ram" && manager.settings().enabled{
                    *manager.worker.lock().await=Some(worker);
                    manager.set_phase("sleeping");
                }else{
                    manager.set_phase("unloading");
                    worker.stop().await;
                    manager.set_phase(if manager.settings().enabled{"ready"}else{"off"});
                }
                Ok(output)
            }.await;
            if let Err(error)=&outcome{
                let _=ready_tx.send(Some(Err(error.clone())));
                if let Some(mut w)=manager.worker.lock().await.take(){w.stop().await;}
                manager.set_phase(if manager.settings().enabled{"ready"}else{"off"});
            }
            let _=tokio::fs::remove_file(audio).await;
            let _=result_tx.send(Some(outcome));
        });
        let mut ready_rx=self.session.lock().await.as_ref().unwrap().ready.clone();
        let ready_result=tokio::time::timeout(Duration::from_secs(180),wait_ready(&mut ready_rx)).await
            .map_err(|_|"Whisper took too long to load. Try the RAM sleep option in Models.".to_string())?;
        if let Err(error)=ready_result { self.cancel(&id).await; return Err(error); }
        Ok(id)
    }
    pub async fn transcribe(&self,id:&str,encoded:&str)->Result<Value,String>{
        let result=async{
            if encoded.len()>24*1024*1024{return Err("Recording is too large".into());}
            let bytes=base64::engine::general_purpose::STANDARD.decode(encoded).map_err(|e|e.to_string())?;
            if bytes.is_empty(){return Err("Recording was empty".into());}
            let mut guard=self.session.lock().await;
            let session=guard.as_mut().filter(|s|s.id==id).ok_or("Microphone session expired")?;
            tokio::fs::write(&session.audio,bytes).await.map_err(|e|e.to_string())?;
            let input=session.input.take().ok_or("Recording already submitted")?;
            input.send(()).map_err(|_|"Whisper stopped before transcription")?;
            let mut result=session.result.clone(); drop(guard); wait_result(&mut result).await
        }.await;
        self.cancel(id).await;
        result
    }
    pub async fn cancel(&self,id:&str){
        let mut guard=self.session.lock().await;
        if guard.as_ref().is_some_and(|s|s.id==id){
            let mut session=guard.take().unwrap();session.cancel.cancel();drop(guard);
            let _=wait_result(&mut session.result).await;
        }
    }
    pub async fn is_active(&self)->bool{self.session.lock().await.is_some()}
}
async fn wait_ready(receiver:&mut watch::Receiver<Option<Result<(),String>>>)->Result<(),String>{
    loop{if let Some(result)=receiver.borrow().clone(){return result;}receiver.changed().await.map_err(|_|"Whisper loading worker disconnected")?;}
}
async fn wait_result(receiver:&mut watch::Receiver<Outcome>)->Result<Value,String>{
    loop{if let Some(result)=receiver.borrow().clone(){return result;}receiver.changed().await.map_err(|_|"Whisper worker disconnected")?;}
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
pub async fn speech_set_enabled(core:tauri::State<'_,Arc<crate::AppCore>>,enabled:bool)->Result<SpeechStatus,String>{core.speech.set_enabled(enabled).await}
#[tauri::command]
pub async fn speech_set_idle_mode(core:tauri::State<'_,Arc<crate::AppCore>>,mode:String)->Result<SpeechStatus,String>{core.speech.set_idle_mode(&mode).await}
#[tauri::command]
pub async fn speech_start(core:tauri::State<'_,Arc<crate::AppCore>>)->Result<String,String>{core.speech.start().await}
#[tauri::command]
pub async fn speech_transcribe(core:tauri::State<'_,Arc<crate::AppCore>>,session_id:String,audio:String)->Result<Value,String>{core.speech.transcribe(&session_id,&audio).await}
#[tauri::command]
pub async fn speech_cancel(core:tauri::State<'_,Arc<crate::AppCore>>,session_id:String)->Result<(),String>{core.speech.cancel(&session_id).await;Ok(())}

#[cfg(test)]
mod tests {
    use super::*;
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
                input:Some(input),result,ready,cancel:cancel.clone()});
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
