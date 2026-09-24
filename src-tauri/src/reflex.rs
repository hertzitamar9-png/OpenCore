//! OpenCore Reflex: the fast decision model behind computer use (about 20-40 ms per
//! decision). Its Python server starts on first use, so it holds GPU memory only while
//! Reflex is actually needed, and it stops with the app.

use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const PORT: u16 = 8815;

pub struct ReflexManager {
    child: Mutex<Option<Child>>,
    resource_root: Option<PathBuf>,
    install_root: PathBuf,
}

impl ReflexManager {
    pub fn new(resource_root: Option<PathBuf>, install_root: PathBuf) -> Self {
        Self { child: Mutex::new(None), resource_root, install_root }
    }

    fn script(&self) -> Option<PathBuf> {
        self.resource_root.iter().map(|root| root.join("reflex").join("server.py"))
            .chain(std::iter::once(self.install_root.join("reflex").join("server.py")))
            .find(|path| path.is_file())
    }

    fn model_dir(&self) -> PathBuf {
        std::env::var_os("OPENCORE_REFLEX_MODEL").map(PathBuf::from)
            .unwrap_or_else(|| self.install_root.join("reflex").join("model"))
    }

    /// Reflex needs PyTorch with CUDA; the ECHO interpreter does not have it.
    fn python(&self) -> Option<PathBuf> {
        if let Some(path) = std::env::var_os("OPENCORE_REFLEX_PYTHON").map(PathBuf::from) {
            return path.is_file().then_some(path);
        }
        let profile = PathBuf::from(std::env::var_os("USERPROFILE")?);
        [profile.join(".unsloth").join("studio").join("unsloth_studio").join("Scripts").join("python.exe"),
         self.install_root.join("reflex").join("python").join("python.exe")]
            .into_iter().find(|path| path.is_file())
    }

    fn client(timeout: Duration) -> Result<reqwest::Client, String> {
        reqwest::Client::builder().no_proxy().timeout(timeout).build().map_err(|error| error.to_string())
    }

    async fn healthy() -> bool {
        let Ok(client) = Self::client(Duration::from_secs(2)) else { return false };
        match client.get(format!("http://127.0.0.1:{PORT}/health")).send().await {
            Ok(response) => response.json::<Value>().await.ok()
                .and_then(|value| value.get("ready").and_then(Value::as_bool)).unwrap_or(false),
            Err(_) => false,
        }
    }

    pub async fn ensure_running(&self) -> Result<(), String> {
        if Self::healthy().await { return Ok(()); }
        let script = self.script().ok_or("OpenCore Reflex is not installed with this app")?;
        let model = self.model_dir();
        if !model.join("model.safetensors").is_file() {
            return Err(format!("OpenCore Reflex model not found: {}", model.display()));
        }
        let python = self.python().ok_or("OpenCore Reflex needs Python with PyTorch (CUDA); none was found")?;
        {
            let mut child = self.child.lock().map_err(|error| error.to_string())?;
            let running = child.as_mut().is_some_and(|process| process.try_wait().ok().flatten().is_none());
            if !running {
                let mut command = Command::new(&python);
                command.arg(&script).arg(&model).args(["--port", &PORT.to_string()])
                    .current_dir(script.parent().unwrap_or(&self.install_root))
                    .env("PYTHONIOENCODING", "utf-8")
                    .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
                #[cfg(windows)]
                command.creation_flags(0x0800_0000);
                let process = command.spawn().map_err(|error| format!("Could not start OpenCore Reflex: {error}"))?;
                crate::child_guard::adopt(&process);
                *child = Some(process);
            }
        }
        let deadline = Instant::now() + Duration::from_secs(120);
        while Instant::now() < deadline {
            if Self::healthy().await { return Ok(()); }
            if let Ok(mut child) = self.child.lock() {
                if let Some(Ok(Some(status))) = child.as_mut().map(|process| process.try_wait()) {
                    *child = None;
                    return Err(format!("OpenCore Reflex exited during startup ({status})"));
                }
            }
            tokio::time::sleep(Duration::from_millis(400)).await;
        }
        Err("OpenCore Reflex did not become ready within two minutes".into())
    }

    pub async fn post(&self, path: &str, body: Value, timeout: Duration) -> Result<Value, String> {
        let response = Self::client(timeout)?.post(format!("http://127.0.0.1:{PORT}{path}")).json(&body).send().await
            .map_err(|error| format!("OpenCore Reflex request failed: {error}"))?;
        let status = response.status();
        let value = response.json::<Value>().await.map_err(|error| error.to_string())?;
        if status.is_success() { Ok(value) } else {
            Err(value.get("detail").and_then(Value::as_str).map(str::to_string)
                .unwrap_or_else(|| format!("OpenCore Reflex returned {status}")))
        }
    }

    pub fn stop(&self) {
        if let Ok(mut child) = self.child.lock() {
            if let Some(mut process) = child.take() {
                let _ = process.kill();
                let _ = process.wait();
            }
        }
    }
}

impl Drop for ReflexManager {
    fn drop(&mut self) { self.stop(); }
}

/// Center of an inspected element relative to its window, for desktop_use interact.
pub fn relative_center(elements: &[Value], element_id: i64) -> Option<(i64, i64)> {
    let window = elements.first()?.get("bounds")?;
    let bounds = elements.iter().find(|row| row.get("elementId").and_then(Value::as_i64) == Some(element_id))?.get("bounds")?;
    let field = |value: &Value, key: &str| value.get(key).and_then(Value::as_i64);
    Some((field(bounds, "left")? - field(window, "left")? + field(bounds, "width")? / 2,
          field(bounds, "top")? - field(window, "top")? + field(bounds, "height")? / 2))
}

pub fn tool_spec() -> Value {
    json!({"type":"function","function":{
        "name":"reflex_use",
        "description":"OpenCore Reflex is a persistent, sub-1B computer-use controller. pick chooses a Windows accessibility control (about 25 ms). ground locates a visible target from a screenshot and returns window-relative x,y without clicking. ground_click locates that target and clicks it in one call; it returns whether input was sent, not proof that the task succeeded. Use a short, specific goal and check the screen afterward. Both visual actions require the selected window to be visible and foregroundable. play_snake plays a visible Snake game until game over or the time limit. Use the existing Chrome windowId when the user asked for Chrome.",
        "parameters":{"type":"object","properties":{
            "action":{"type":"string","enum":["pick","ground","ground_click","play_snake"]},
            "windowId":{"type":"integer"},
            "goal":{"type":"string"},
            "seconds":{"type":"number"}
        },"required":["action","windowId"],"additionalProperties":false}
    }})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn centers_are_relative_to_the_window() {
        let rows = vec![
            json!({"elementId":0,"bounds":{"left":100,"top":50,"width":800,"height":600}}),
            json!({"elementId":7,"bounds":{"left":300,"top":90,"width":40,"height":20}}),
        ];
        assert_eq!(relative_center(&rows, 7), Some((220, 50)));
        assert_eq!(relative_center(&rows, 9), None);
    }

}
