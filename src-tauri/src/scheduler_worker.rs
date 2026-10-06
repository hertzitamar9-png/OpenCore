//! Explicit executable/arguments workers with owned process trees and bounded logs.
use crate::child_guard::ProcessJob;
use serde::{Deserialize, Serialize};
use std::{fs::OpenOptions, io::{Read, Seek, SeekFrom, Write}, path::{Path, PathBuf}, process::{Command, Stdio}, sync::Arc, time::Duration};
use tokio_util::sync::CancellationToken;

const LOG_LIMIT: u64 = 32 * 1024 * 1024;
const LOG_PREVIEW: u64 = 256 * 1024;
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerConfig {
    pub command: String,
    #[serde(default)] pub args: Vec<String>,
    pub cwd: PathBuf,
    #[serde(default)] pub uses_gpu: bool,
    #[serde(default)] pub long_running: bool,
    #[serde(default = "idle_policy")] pub wait_policy: String,
}
fn idle_policy() -> String { "when-idle".into() }
impl WorkerConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.command.trim().is_empty() || self.command.len() > 4096 || self.command.contains('\0') { return Err("Worker executable is required".into()); }
        if self.args.len() > 256 || self.args.iter().any(|arg| arg.len() > 32768 || arg.contains('\0')) { return Err("Worker arguments exceed the supported limit".into()); }
        if !self.cwd.is_absolute() || !self.cwd.is_dir() { return Err("Worker working directory must be an existing absolute directory".into()); }
        if !matches!(self.wait_policy.as_str(), "when-idle" | "allow-during-chat") { return Err("Unknown worker wait policy".into()); }
        if self.uses_gpu && self.wait_policy != "when-idle" { return Err("GPU workers must wait until chat, speech and studio work are idle".into()); }
        Ok(())
    }
}
#[derive(Debug)]
pub struct WorkerResult { pub exit_code: Option<i32>, pub cancelled: bool, pub stdout_truncated: bool, pub stderr_truncated: bool }

fn drain(mut input: impl Read, path: PathBuf) -> Result<bool, String> {
    let mut output = OpenOptions::new().create(true).truncate(true).write(true).open(path).map_err(|e| e.to_string())?;
    let mut total = 0u64; let mut buffer = [0u8; 8192]; let mut truncated = false;
    loop {
        let count = input.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 { break; }
        let keep = (LOG_LIMIT.saturating_sub(total) as usize).min(count);
        if keep > 0 { output.write_all(&buffer[..keep]).map_err(|e| e.to_string())?; }
        total += count as u64;
        if keep < count { truncated = true; }
    }
    output.flush().map_err(|e| e.to_string())?;
    Ok(truncated)
}

fn terminate_tree(child: &mut std::process::Child, owned: &mut Option<ProcessJob>) {
    owned.take(); // Windows kill-on-close stops every descendant, including inherited stdout handles.
    #[cfg(unix)] {
        let _ = Command::new("kill").args(["-KILL", "--", &format!("-{}", child.id())]).stdout(Stdio::null()).stderr(Stdio::null()).status();
    }
    let _ = child.kill(); let _ = child.wait();
}

pub fn run(config: WorkerConfig, cancel: CancellationToken, log_dir: PathBuf, webhook_url: String, webhook_token: String,
    started: Arc<dyn Fn(u32) -> Result<(), String> + Send + Sync>) -> Result<WorkerResult, String> {
    config.validate()?;
    if cancel.is_cancelled() { return Ok(WorkerResult { exit_code: None, cancelled: true, stdout_truncated: false, stderr_truncated: false }); }
    std::fs::create_dir_all(&log_dir).map_err(|e| e.to_string())?;
    let mut command = Command::new(&config.command);
    command.args(&config.args).current_dir(&config.cwd).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped())
        .env("OPENCORE_EVENT_URL", webhook_url).env("OPENCORE_EVENT_TOKEN", webhook_token);
    #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
    #[cfg(unix)] { use std::os::unix::process::CommandExt; command.process_group(0); }
    let mut child = command.spawn().map_err(|e| format!("Could not start worker {}: {e}", config.command))?;
    crate::child_guard::adopt(&child);
    let mut owned = match ProcessJob::new(&child) { Ok(job) => Some(job), Err(error) => { let _ = child.kill(); let _ = child.wait(); return Err(error); } };
    if let Err(error) = started(child.id()) { terminate_tree(&mut child, &mut owned); return Err(error); }
    let stdout = child.stdout.take().ok_or("Worker stdout is unavailable")?;
    let stderr = child.stderr.take().ok_or("Worker stderr is unavailable")?;
    let stdout_path = log_dir.join("stdout.log"); let stderr_path = log_dir.join("stderr.log");
    let out = std::thread::spawn(move || drain(stdout, stdout_path));
    let err = std::thread::spawn(move || drain(stderr, stderr_path));
    let outcome = loop {
        if cancel.is_cancelled() { terminate_tree(&mut child, &mut owned); break Ok((None, true)); }
        match child.try_wait() {
            Ok(Some(status)) => { terminate_tree(&mut child, &mut owned); break Ok((status.code(), false)); }
            Ok(None) => std::thread::sleep(Duration::from_millis(100)),
            Err(error) => { terminate_tree(&mut child, &mut owned); break Err(error.to_string()); }
        }
    };
    let stdout_truncated = out.join().map_err(|_| "Worker stdout reader failed")??;
    let stderr_truncated = err.join().map_err(|_| "Worker stderr reader failed")??;
    let (exit_code, cancelled) = outcome?;
    Ok(WorkerResult { exit_code, cancelled, stdout_truncated, stderr_truncated })
}

pub fn read_logs(log_dir: &Path) -> Result<serde_json::Value, String> {
    fn tail(path: PathBuf) -> Result<(String, bool), String> {
        if !path.exists() { return Ok((String::new(), false)); }
        let mut file = std::fs::File::open(path).map_err(|e| e.to_string())?;
        let size = file.metadata().map_err(|e| e.to_string())?.len();
        file.seek(SeekFrom::Start(size.saturating_sub(LOG_PREVIEW))).map_err(|e| e.to_string())?;
        let mut bytes = Vec::new(); file.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
        Ok((String::from_utf8_lossy(&bytes).into_owned(), size > LOG_PREVIEW))
    }
    let (stdout, stdout_truncated) = tail(log_dir.join("stdout.log"))?;
    let (stderr, stderr_truncated) = tail(log_dir.join("stderr.log"))?;
    Ok(serde_json::json!({"stdout":stdout,"stderr":stderr,"stdoutTruncated":stdout_truncated,"stderrTruncated":stderr_truncated,"limitBytesPerStream":LOG_LIMIT}))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn config() -> WorkerConfig {
        #[cfg(windows)] let (command, args) = ("cmd.exe", vec!["/D".into(), "/C".into(), "echo real-output & echo real-error 1>&2 & exit /b 7".into()]);
        #[cfg(not(windows))] let (command, args) = ("sh", vec!["-c".into(), "printf real-output; printf real-error >&2; exit 7".into()]);
        WorkerConfig { command: command.into(), args, cwd: std::env::temp_dir(), uses_gpu: false, long_running: false, wait_policy: idle_policy() }
    }
    #[test]
    fn retains_stdout_stderr_and_actual_failure_code() {
        let dir = std::env::temp_dir().join(format!("background-worker-{}", uuid::Uuid::new_v4()));
        let result = run(config(), CancellationToken::new(), dir.clone(), String::new(), String::new(), Arc::new(|_| Ok(()))).unwrap();
        assert_eq!(result.exit_code, Some(7)); assert!(!result.cancelled);
        let logs = read_logs(&dir).unwrap(); assert!(logs["stdout"].as_str().unwrap().contains("real-output")); assert!(logs["stderr"].as_str().unwrap().contains("real-error"));
        let _ = std::fs::remove_dir_all(dir);
    }
    #[test]
    fn cancellation_stops_an_owned_worker_without_a_success_code() {
        let dir = std::env::temp_dir().join(format!("background-worker-{}", uuid::Uuid::new_v4()));
        let mut worker = config();
        #[cfg(windows)] { worker.args = vec!["/D".into(), "/C".into(), "ping -n 120 127.0.0.1 >nul".into()]; }
        #[cfg(not(windows))] { worker.args = vec!["-c".into(), "sleep 120".into()]; }
        let token = CancellationToken::new(); let stop = token.clone();
        let start = std::time::Instant::now();
        // Give the shell time to spawn its descendant. Both inherit pipes, so
        // joining the log readers also proves that cancellation ends the tree.
        let result = run(worker, token, dir.clone(), String::new(), String::new(), Arc::new(move |_| {
            let stop=stop.clone(); std::thread::spawn(move || { std::thread::sleep(Duration::from_millis(250)); stop.cancel(); }); Ok(())
        })).unwrap();
        assert!(result.cancelled); assert_eq!(result.exit_code, None); assert!(start.elapsed() < Duration::from_secs(10));
        let _ = std::fs::remove_dir_all(dir);
    }
    #[test]
    fn gpu_workers_cannot_bypass_idle_waiting() {
        let mut worker = config(); worker.uses_gpu = true; worker.wait_policy = "allow-during-chat".into();
        assert!(worker.validate().unwrap_err().contains("must wait"));
    }
}
