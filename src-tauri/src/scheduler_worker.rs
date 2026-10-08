//! Explicit executable/arguments workers with owned process trees and bounded logs.
use crate::child_guard::ProcessJob;
use serde::{Deserialize, Serialize};
use std::{
    fs::OpenOptions,
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::Arc,
    time::Duration,
};
use tokio_util::sync::CancellationToken;

const LOG_LIMIT: u64 = 32 * 1024 * 1024;
const LOG_PREVIEW: u64 = 256 * 1024;
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerConfig {
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    pub cwd: PathBuf,
    #[serde(default)]
    pub uses_gpu: bool,
    #[serde(default)]
    pub long_running: bool,
    #[serde(default = "idle_policy")]
    pub wait_policy: String,
}
fn idle_policy() -> String {
    "when-idle".into()
}
impl WorkerConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.command.trim().is_empty()
            || self.command.len() > 4096
            || self.command.contains('\0')
        {
            return Err("Worker executable is required".into());
        }
        if self.args.len() > 256
            || self
                .args
                .iter()
                .any(|arg| arg.len() > 32768 || arg.contains('\0'))
        {
            return Err("Worker arguments exceed the supported limit".into());
        }
        if !self.cwd.is_absolute() || !self.cwd.is_dir() {
            return Err("Worker working directory must be an existing absolute directory".into());
        }
        if !matches!(self.wait_policy.as_str(), "when-idle" | "allow-during-chat") {
            return Err("Unknown worker wait policy".into());
        }
        if self.uses_gpu && self.wait_policy != "when-idle" {
            return Err("GPU workers must wait until chat, speech and studio work are idle".into());
        }
        Ok(())
    }
}
#[derive(Debug)]
pub struct WorkerResult {
    pub exit_code: Option<i32>,
    pub cancelled: bool,
    pub stdout_truncated: bool,
    pub stderr_truncated: bool,
    pub failure_reason: Option<String>,
    pub output_summary: Option<String>,
}

fn drain(mut input: impl Read, path: PathBuf) -> Result<bool, String> {
    let mut output = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    let mut total = 0u64;
    let mut buffer = [0u8; 8192];
    let mut truncated = false;
    loop {
        let count = input.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        let keep = (LOG_LIMIT.saturating_sub(total) as usize).min(count);
        if keep > 0 {
            output
                .write_all(&buffer[..keep])
                .map_err(|e| e.to_string())?;
        }
        total += count as u64;
        if keep < count {
            truncated = true;
        }
    }
    output.flush().map_err(|e| e.to_string())?;
    Ok(truncated)
}

fn terminate_tree(child: &mut std::process::Child, owned: &mut Option<ProcessJob>) {
    owned.take(); // Windows kill-on-close stops every descendant, including inherited stdout handles.
    #[cfg(unix)]
    {
        let _ = Command::new("kill")
            .args(["-KILL", "--", &format!("-{}", child.id())])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(windows)]
struct WorkerHandle(windows::Win32::Foundation::HANDLE);
#[cfg(windows)]
impl Drop for WorkerHandle {
    fn drop(&mut self) {
        unsafe {
            let _ = windows::Win32::Foundation::CloseHandle(self.0);
        }
    }
}

/// std::process::Child retains the process handle, not CreateProcess's primary
/// thread handle. Before running any worker code, identify its only thread in
/// the suspended process and open an owned handle with the required rights.
#[cfg(windows)]
fn resume_suspended_worker(child: &mut std::process::Child) -> Result<(), String> {
    use windows::Win32::Foundation::ERROR_NO_MORE_FILES;
    use windows::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows::Win32::System::Threading::{
        GetProcessIdOfThread, OpenThread, ResumeThread, THREAD_QUERY_LIMITED_INFORMATION,
        THREAD_SUSPEND_RESUME,
    };
    let snapshot = WorkerHandle(
        unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) }
            .map_err(|error| format!("Could not enumerate the suspended worker thread: {error}"))?,
    );
    let entry_size = std::mem::size_of::<THREADENTRY32>() as u32;
    let mut entry = THREADENTRY32 {
        dwSize: entry_size,
        ..Default::default()
    };
    unsafe { Thread32First(snapshot.0, &mut entry) }
        .map_err(|error| format!("Could not find the suspended worker thread: {error}"))?;
    let mut thread_id = None;
    loop {
        // The owner PID is the fourth DWORD; ToolHelp can return a shorter entry.
        if entry.dwSize < 16 {
            return Err("Worker thread snapshot omitted its owner PID".into());
        }
        if entry.th32OwnerProcessID == child.id() && thread_id.replace(entry.th32ThreadID).is_some()
        {
            return Err("Suspended worker has multiple threads; its primary thread cannot be identified safely".into());
        }
        entry.dwSize = entry_size;
        match unsafe { Thread32Next(snapshot.0, &mut entry) } {
            Ok(()) => {}
            Err(error)
                if error.code() == windows::core::HRESULT::from_win32(ERROR_NO_MORE_FILES.0) =>
            {
                break
            }
            Err(error) => {
                return Err(format!(
                    "Could not finish enumerating the suspended worker thread: {error}"
                ))
            }
        }
    }
    let thread_id = thread_id.ok_or("The suspended worker primary thread is unavailable")?;
    let thread = WorkerHandle(
        unsafe {
            OpenThread(
                THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION,
                false,
                thread_id,
            )
        }
        .map_err(|error| format!("Could not open the suspended worker primary thread: {error}"))?,
    );
    if unsafe { GetProcessIdOfThread(thread.0) } != child.id() {
        return Err("Worker thread ownership changed before activation".into());
    }
    // Checking the retained Child after opening the thread also prevents PID
    // reuse from causing us to resume a thread belonging to another process.
    if child
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_some()
    {
        return Err("Worker exited before activation".into());
    }
    let previous = unsafe { ResumeThread(thread.0) };
    if previous == u32::MAX {
        return Err(format!(
            "Could not resume the owned worker: {}",
            windows::core::Error::from_win32()
        ));
    }
    if previous != 1 {
        return Err(format!(
            "Owned worker had an unexpected primary-thread suspend count: {previous}"
        ));
    }
    Ok(())
}

pub fn run(
    config: WorkerConfig,
    cancel: CancellationToken,
    log_dir: PathBuf,
    webhook_url: String,
    webhook_token: String,
    started: Arc<dyn Fn(u32) -> Result<(), String> + Send + Sync>,
) -> Result<WorkerResult, String> {
    config.validate()?;
    if cancel.is_cancelled() {
        return Ok(WorkerResult {
            exit_code: None,
            cancelled: true,
            stdout_truncated: false,
            stderr_truncated: false,
            failure_reason: None,
        });
    }
    std::fs::create_dir_all(&log_dir).map_err(|e| e.to_string())?;
    let mut command = Command::new(&config.command);
    command
        .args(&config.args)
        .current_dir(&config.cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("OPENCORE_EVENT_URL", webhook_url)
        .env("OPENCORE_EVENT_TOKEN", webhook_token);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        use windows::Win32::System::Threading::{CREATE_NO_WINDOW, CREATE_SUSPENDED};
        command.creation_flags((CREATE_NO_WINDOW | CREATE_SUSPENDED).0);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("Could not start worker {}: {e}", config.command))?;
    crate::child_guard::adopt(&child);
    let mut owned = match ProcessJob::new(&child) {
        Ok(job) => Some(job),
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };
    if let Err(error) = started(child.id()) {
        terminate_tree(&mut child, &mut owned);
        return Err(error);
    }
    if cancel.is_cancelled() {
        terminate_tree(&mut child, &mut owned);
        return Ok(WorkerResult {
            exit_code: None,
            cancelled: true,
            stdout_truncated: false,
            stderr_truncated: false,
            failure_reason: None,
        });
    }
    #[cfg(windows)]
    if let Err(error) = resume_suspended_worker(&mut child) {
        terminate_tree(&mut child, &mut owned);
        return Err(error);
    }
    let stdout = child.stdout.take().ok_or("Worker stdout is unavailable")?;
    let stderr = child.stderr.take().ok_or("Worker stderr is unavailable")?;
    let stdout_path = log_dir.join("stdout.log");
    let stderr_path = log_dir.join("stderr.log");
    let out = std::thread::spawn(move || drain(stdout, stdout_path));
    let err = std::thread::spawn(move || drain(stderr, stderr_path));
    let outcome = loop {
        if cancel.is_cancelled() {
            terminate_tree(&mut child, &mut owned);
            break Ok((None, true));
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                terminate_tree(&mut child, &mut owned);
                break Ok((status.code(), false));
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(100)),
            Err(error) => {
                terminate_tree(&mut child, &mut owned);
                break Err(error.to_string());
            }
        }
    };
    let stdout_truncated = out.join().map_err(|_| "Worker stdout reader failed")??;
    let stderr_truncated = err.join().map_err(|_| "Worker stderr reader failed")??;
    let (exit_code, cancelled) = outcome?;
    let output_summary = failure_reason(&log_dir);
    let failure_reason = if cancelled || exit_code == Some(0) { None } else { output_summary.clone() };
    Ok(WorkerResult {
        exit_code,
        cancelled,
        stdout_truncated,
        stderr_truncated,
        failure_reason,
        output_summary,
    })
}

fn failure_reason(log_dir: &Path) -> Option<String> {
    let logs = read_logs(log_dir).ok()?;
    let lines: Vec<String> = ["stderr", "stdout"].iter().filter_map(|stream| {
        let line = logs[*stream].as_str()?.lines().rev().find(|line| !line.trim().is_empty())?;
        let mut excerpt = line.chars().take(2048).collect::<String>();
        if line.chars().nth(2048).is_some() { excerpt.push_str("… (full output in Logs)"); }
        Some(format!("{stream}: {excerpt}"))
    }).collect();
    (!lines.is_empty()).then(|| lines.join("\n"))
}

pub fn read_logs(log_dir: &Path) -> Result<serde_json::Value, String> {
    fn tail(path: PathBuf) -> Result<(String, bool), String> {
        if !path.exists() {
            return Ok((String::new(), false));
        }
        let mut file = std::fs::File::open(path).map_err(|e| e.to_string())?;
        let size = file.metadata().map_err(|e| e.to_string())?.len();
        file.seek(SeekFrom::Start(size.saturating_sub(LOG_PREVIEW)))
            .map_err(|e| e.to_string())?;
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
        Ok((
            String::from_utf8_lossy(&bytes).into_owned(),
            size > LOG_PREVIEW,
        ))
    }
    let (stdout, stdout_truncated) = tail(log_dir.join("stdout.log"))?;
    let (stderr, stderr_truncated) = tail(log_dir.join("stderr.log"))?;
    Ok(
        serde_json::json!({"stdout":stdout,"stderr":stderr,"stdoutTruncated":stdout_truncated,"stderrTruncated":stderr_truncated,"limitBytesPerStream":LOG_LIMIT}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    fn config() -> WorkerConfig {
        #[cfg(windows)]
        let (command, args) = (
            "cmd.exe",
            vec![
                "/D".into(),
                "/C".into(),
                "echo real-output & echo real-error 1>&2 & exit /b 7".into(),
            ],
        );
        #[cfg(not(windows))]
        let (command, args) = (
            "sh",
            vec![
                "-c".into(),
                "printf real-output; printf real-error >&2; exit 7".into(),
            ],
        );
        WorkerConfig {
            command: command.into(),
            args,
            cwd: std::env::temp_dir(),
            uses_gpu: false,
            long_running: false,
            wait_policy: idle_policy(),
        }
    }
    #[test]
    fn failure_excerpt_is_bounded_and_preserves_unicode() {
        let dir = std::env::temp_dir().join(format!("background-worker-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("stderr.log"), format!("Traceback\n{}\n", "שגיאה".repeat(2000))).unwrap();
        let detail = failure_reason(&dir).unwrap();
        assert!(detail.starts_with("stderr: שגיאה"));
        assert!(detail.ends_with("… (full output in Logs)"));
        assert!(detail.chars().count() < 2100);
        let _ = std::fs::remove_dir_all(dir);
    }
    #[test]
    fn retains_stdout_stderr_and_actual_failure_code() {
        let dir = std::env::temp_dir().join(format!("background-worker-{}", uuid::Uuid::new_v4()));
        let result = run(
            config(),
            CancellationToken::new(),
            dir.clone(),
            String::new(),
            String::new(),
            Arc::new(|_| Ok(())),
        )
        .unwrap();
        assert_eq!(result.exit_code, Some(7));
        assert!(!result.cancelled);
        let reason = result.failure_reason.as_deref().unwrap();
        assert!(reason.contains("real-error"));
        assert!(reason.contains("real-output"));
        let logs = read_logs(&dir).unwrap();
        assert!(logs["stdout"].as_str().unwrap().contains("real-output"));
        assert!(logs["stderr"].as_str().unwrap().contains("real-error"));
        let _ = std::fs::remove_dir_all(dir);
    }
    #[test]
    fn a_short_successful_worker_retains_output_in_its_result() {
        let dir=std::env::temp_dir().join(format!("worker-success-{}",uuid::Uuid::new_v4()));
        let mut worker=config();
        #[cfg(windows)] {worker.args=vec!["/D".into(),"/C".into(),"echo verification-passed".into()];}
        #[cfg(not(windows))] {worker.args=vec!["-c".into(),"echo verification-passed".into()];}
        let result=run(worker,CancellationToken::new(),dir.clone(),String::new(),String::new(),Arc::new(|_|Ok(()))).unwrap();
        assert_eq!(result.exit_code,Some(0));
        assert!(result.failure_reason.is_none());
        assert!(result.output_summary.unwrap().contains("verification-passed"));
        let _=std::fs::remove_dir_all(dir);
    }
    #[test]
    fn cancellation_stops_an_owned_worker_without_a_success_code() {
        let dir = std::env::temp_dir().join(format!("background-worker-{}", uuid::Uuid::new_v4()));
        let mut worker = config();
        #[cfg(windows)]
        {
            worker.args = vec![
                "/D".into(),
                "/C".into(),
                "ping -n 120 127.0.0.1 >nul".into(),
            ];
        }
        #[cfg(not(windows))]
        {
            worker.args = vec!["-c".into(), "sleep 120".into()];
        }
        let token = CancellationToken::new();
        let stop = token.clone();
        let start = std::time::Instant::now();
        // Give the shell time to spawn its descendant. Both inherit pipes, so
        // joining the log readers also proves that cancellation ends the tree.
        let result = run(
            worker,
            token,
            dir.clone(),
            String::new(),
            String::new(),
            Arc::new(move |_| {
                let stop = stop.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(Duration::from_millis(250));
                    stop.cancel();
                });
                Ok(())
            }),
        )
        .unwrap();
        assert!(result.cancelled);
        assert_eq!(result.exit_code, None);
        assert!(start.elapsed() < Duration::from_secs(10));
        let _ = std::fs::remove_dir_all(dir);
    }
    #[cfg(windows)]
    #[test]
    fn windows_worker_cannot_execute_before_ownership_callback_finishes() {
        let dir = std::env::temp_dir().join(format!(
            "background-worker-startup-{}",
            uuid::Uuid::new_v4()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let mut worker = config();
        worker.cwd = dir.clone();
        // The executable's first action is observable. It also requires the
        // callback's ownership receipt, rather than merely finishing eventually.
        worker.args = vec![
            "/D".into(),
            "/C".into(),
            "echo executed>executed.marker & if not exist adopted.marker (exit /b 91) & exit /b 0"
                .into(),
        ];
        let during_adoption = dir.clone();
        let result = run(
            worker,
            CancellationToken::new(),
            dir.join("logs"),
            String::new(),
            String::new(),
            Arc::new(move |_| {
                std::thread::sleep(Duration::from_millis(250));
                if during_adoption.join("executed.marker").exists() {
                    return Err("Worker executed before ownership callback finished".into());
                }
                std::fs::write(during_adoption.join("adopted.marker"), b"adoption complete")
                    .map_err(|error| error.to_string())
            }),
        )
        .unwrap();
        assert_eq!(result.exit_code, Some(0));
        assert!(dir.join("executed.marker").is_file());
        assert!(dir.join("adopted.marker").is_file());
        let _ = std::fs::remove_dir_all(dir);
    }
    #[cfg(windows)]
    #[test]
    fn windows_failed_ownership_callback_never_activates_worker_code() {
        let dir = std::env::temp_dir().join(format!(
            "background-worker-rejected-{}",
            uuid::Uuid::new_v4()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let mut worker = config();
        worker.cwd = dir.clone();
        worker.args = vec![
            "/D".into(),
            "/C".into(),
            "echo executed>executed.marker".into(),
        ];
        let result = run(
            worker,
            CancellationToken::new(),
            dir.join("logs"),
            String::new(),
            String::new(),
            Arc::new(|_| {
                std::thread::sleep(Duration::from_millis(250));
                Err("ownership callback rejected startup".into())
            }),
        );
        assert!(result.unwrap_err().contains("ownership callback rejected"));
        assert!(!dir.join("executed.marker").exists());
        let _ = std::fs::remove_dir_all(dir);
    }
    #[cfg(windows)]
    #[test]
    fn windows_cancellation_during_adoption_never_activates_worker_code() {
        let dir = std::env::temp_dir().join(format!(
            "background-worker-prestart-cancel-{}",
            uuid::Uuid::new_v4()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let mut worker = config();
        worker.cwd = dir.clone();
        worker.args = vec![
            "/D".into(),
            "/C".into(),
            "echo executed>executed.marker".into(),
        ];
        let token = CancellationToken::new();
        let stop = token.clone();
        let result = run(
            worker,
            token,
            dir.join("logs"),
            String::new(),
            String::new(),
            Arc::new(move |_| {
                std::thread::sleep(Duration::from_millis(250));
                stop.cancel();
                Ok(())
            }),
        )
        .unwrap();
        assert!(result.cancelled);
        assert_eq!(result.exit_code, None);
        assert!(!dir.join("executed.marker").exists());
        let _ = std::fs::remove_dir_all(dir);
    }
    #[test]
    fn gpu_workers_cannot_bypass_idle_waiting() {
        let mut worker = config();
        worker.uses_gpu = true;
        worker.wait_policy = "allow-during-chat".into();
        assert!(worker.validate().unwrap_err().contains("must wait"));
    }
}
