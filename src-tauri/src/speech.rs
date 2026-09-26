use base64::Engine;
use serde_json::{json, Value};
use std::{path::PathBuf, process::Stdio, sync::Arc, time::Duration};
use tokio::{io::{AsyncReadExt, AsyncWriteExt}, sync::{Mutex, oneshot, watch}};
use tokio_util::sync::CancellationToken;

type Outcome = Option<Result<Value, String>>;
struct Session {
    id: String,
    audio: PathBuf,
    input: Option<oneshot::Sender<()>>,
    result: watch::Receiver<Outcome>,
    cancel: CancellationToken,
}

pub struct SpeechManager { root: PathBuf, script: PathBuf, session: Mutex<Option<Session>> }

impl SpeechManager {
    pub fn new(root: PathBuf, resources: PathBuf) -> Self {
        Self { root: root.join("speech"), script: resources.join("speech/whisper_worker.py"), session: Mutex::new(None) }
    }

    pub async fn start(&self) -> Result<String, String> {
        crate::model_catalog::require_idle()?;
        let mut guard = self.session.lock().await;
        if guard.is_some() { return Err("A microphone session is already active".into()); }
        let python = self.root.join("venv/Scripts/python.exe");
        let model = self.root.join("large-v3");
        if !python.is_file() || !model.join("model.bin").is_file() || !self.script.is_file() {
            return Err("Whisper large-v3 is not installed. Install it from the Models tab and provide the local speech Python runtime.".into());
        }
        let id = uuid::Uuid::new_v4().to_string();
        let audio = std::env::temp_dir().join(format!("opencore-speech-{id}.audio"));
        let mut command = tokio::process::Command::new(python);
        command.arg(&self.script).arg("--model").arg(&model)
            .env("PYTHONIOENCODING", "utf-8").env("HF_HUB_OFFLINE", "1")
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
        #[cfg(windows)] command.creation_flags(0x0800_0000);
        let mut child = command.spawn().map_err(|e| format!("Could not start Whisper: {e}"))?;
        #[cfg(windows)]
        if let Some(handle) = child.raw_handle() { crate::child_guard::adopt_handle(handle); }
        let mut stdin = child.stdin.take().ok_or("Missing Whisper input")?;
        let stdout = child.stdout.take().ok_or("Missing Whisper output")?;
        let cancel = CancellationToken::new();
        let worker_cancel = cancel.clone();
        let (input, ready) = oneshot::channel();
        let (result_tx, result) = watch::channel(None);
        let file = audio.clone();
        tokio::spawn(async move {
            let output_task = tokio::spawn(async move {
                let mut bytes = Vec::new();
                stdout.take(128 * 1024).read_to_end(&mut bytes).await.map(|_| bytes)
            });
            let outcome: Result<Value, String> = async {
                tokio::select! {
                    _ = worker_cancel.cancelled() => return Err("Recording cancelled".into()),
                    value = tokio::time::timeout(Duration::from_secs(185), ready) => {
                        value.map_err(|_| "Recording timed out")?.map_err(|_| "Recording cancelled")?;
                    }
                }
                stdin.write_all(format!("{}\n", json!({"audio":file})).as_bytes()).await.map_err(|e| e.to_string())?;
                drop(stdin);
                let status = tokio::select! {
                    _ = worker_cancel.cancelled() => return Err("Recording cancelled".into()),
                    value = tokio::time::timeout(Duration::from_secs(180), child.wait()) =>
                        value.map_err(|_| "Whisper transcription timed out")?.map_err(|e| e.to_string())?
                };
                Ok(json!({"success":status.success()}))
            }.await;
            let _ = child.kill().await;
            let _ = child.wait().await;
            let bytes = output_task.await.ok().and_then(Result::ok).unwrap_or_default();
            let last = String::from_utf8_lossy(&bytes).lines().filter_map(|line| serde_json::from_str::<Value>(line).ok()).last();
            let outcome = outcome.and_then(|status| match last {
                Some(value) if status["success"] == true && value["text"].is_string() => Ok(value),
                Some(value) if value["error"].is_string() => Err(format!("Whisper: {}",value["error"].as_str().unwrap())),
                _ => Err("Whisper exited without a transcript. Check available GPU memory.".into()),
            });
            let _ = tokio::fs::remove_file(file).await;
            let _ = result_tx.send(Some(outcome));
        });
        *guard = Some(Session { id: id.clone(), audio, input: Some(input), result, cancel });
        Ok(id)
    }

    pub async fn transcribe(&self, id: &str, encoded: &str) -> Result<Value, String> {
        let result = async {
            if encoded.len() > 24 * 1024 * 1024 { return Err("Recording is too large".into()); }
            let bytes = base64::engine::general_purpose::STANDARD.decode(encoded).map_err(|e| e.to_string())?;
            if bytes.is_empty() { return Err("Recording was empty".into()); }
            let mut guard = self.session.lock().await;
            let session = guard.as_mut().filter(|s| s.id == id).ok_or("Microphone session expired")?;
            tokio::fs::write(&session.audio, bytes).await.map_err(|e| e.to_string())?;
            let input = session.input.take().ok_or("Recording already submitted")?;
            input.send(()).map_err(|_| "Whisper stopped before transcription")?;
            let mut result = session.result.clone();
            drop(guard);
            wait_result(&mut result).await
        }.await;
        self.cancel(id).await;
        result
    }

    pub async fn cancel(&self, id: &str) {
        let mut guard = self.session.lock().await;
        if guard.as_ref().is_some_and(|s| s.id == id) {
            let mut session = guard.take().unwrap();
            session.cancel.cancel();
            drop(guard);
            let _ = wait_result(&mut session.result).await;
        }
    }

    pub async fn is_active(&self) -> bool { self.session.lock().await.is_some() }
}

async fn wait_result(receiver: &mut watch::Receiver<Outcome>) -> Result<Value, String> {
    loop {
        if let Some(result) = receiver.borrow().clone() { return result; }
        receiver.changed().await.map_err(|_| "Whisper worker disconnected")?;
    }
}

#[tauri::command]
pub async fn speech_start(core: tauri::State<'_, Arc<crate::AppCore>>) -> Result<String, String> { core.speech.start().await }
#[tauri::command]
pub async fn speech_transcribe(core: tauri::State<'_, Arc<crate::AppCore>>, session_id: String, audio: String) -> Result<Value, String> {
    core.speech.transcribe(&session_id, &audio).await
}
#[tauri::command]
pub async fn speech_cancel(core: tauri::State<'_, Arc<crate::AppCore>>, session_id: String) -> Result<(), String> { core.speech.cancel(&session_id).await; Ok(()) }

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    #[ignore = "Requires the installed large-v3 CUDA runtime and artifacts/speech-test.wav"]
    async fn installed_whisper_transcribes_and_cleans_up() {
        let source = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let root = PathBuf::from(std::env::var("USERPROFILE").unwrap()).join("OpenCore");
        let manager = SpeechManager::new(root, source.join("resources"));
        let id = manager.start().await.unwrap();
        assert!(manager.start().await.is_err(), "Only one GPU worker may run per app");
        let audio_path = manager.session.lock().await.as_ref().unwrap().audio.clone();
        let bytes = std::fs::read(source.join("../artifacts/speech-test.wav")).unwrap();
        let result = manager.transcribe(&id, &base64::engine::general_purpose::STANDARD.encode(bytes)).await.unwrap();
        let text = result["text"].as_str().unwrap().to_lowercase();
        assert!(text.contains("running") && text.contains("walking"), "{text}");
        assert!(manager.session.lock().await.is_none());
        assert!(!audio_path.exists());
        let id = manager.start().await.unwrap();
        tokio::time::timeout(Duration::from_secs(10), manager.cancel(&id)).await.unwrap();
        assert!(manager.session.lock().await.is_none(), "Cancellation must release the worker");
        let id = manager.start().await.unwrap();
        assert!(manager.transcribe(&id, "invalid base64!").await.is_err());
        assert!(manager.session.lock().await.is_none(), "Invalid audio must also release the worker");
    }
}
