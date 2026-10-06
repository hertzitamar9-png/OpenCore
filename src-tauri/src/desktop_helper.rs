//! One bounded desktop operation in a headless mode of the installed executable.
//! No startup, app data, WebView, model initialization or foreground handoff.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::{BufRead, BufReader, Read, Write};
use std::sync::{atomic::{AtomicBool, Ordering}, Mutex, OnceLock};

const HELPER_ARGUMENT: &str = "--desktop-helper";
const WIRE_PREFIX: &[u8] = b"OPENCORE_DESKTOP_HELPER ";
const MAX_REQUEST_BYTES: usize = 64 * 1024;
const MAX_RESPONSE_BYTES: usize = 18 * 1024 * 1024;
#[cfg(windows)]
const OPERATION_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(12);
#[cfg(windows)]
const DISPATCH_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(2);

static IN_HELPER: AtomicBool = AtomicBool::new(false);
static INPUT: OnceLock<Mutex<BufReader<std::io::Stdin>>> = OnceLock::new();

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Request {
    version: u8,
    action: String,
    args: Value,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "event", rename_all = "snake_case", deny_unknown_fields)]
enum Event {
    DispatchReady,
    DispatchFinished,
    Result { outcome: Result<Value, String> },
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Approval { allow: bool }

pub(crate) fn in_helper() -> bool { IN_HELPER.load(Ordering::SeqCst) }

fn limited_line(reader: &mut impl BufRead, maximum: usize) -> Result<Vec<u8>, String> {
    let mut line = Vec::new();
    Read::take(reader, maximum as u64 + 1).read_until(b'\n', &mut line)
        .map_err(|error| error.to_string())?;
    if line.len() > maximum { return Err("Desktop helper input exceeds its size limit".into()); }
    if !line.ends_with(b"\n") { return Err("Desktop helper input is incomplete".into()); }
    Ok(line)
}

fn input_line(maximum: usize) -> Result<Vec<u8>, String> {
    let mut reader = INPUT.get_or_init(|| Mutex::new(BufReader::new(std::io::stdin())))
        .lock().map_err(|error| error.to_string())?;
    limited_line(&mut *reader, maximum)
}

fn emit(event: &Event) -> Result<(), String> {
    let data = serde_json::to_vec(event).map_err(|error| error.to_string())?;
    if data.len() + WIRE_PREFIX.len() + 1 > MAX_RESPONSE_BYTES {
        return Err("Desktop helper result exceeds its size limit".into());
    }
    let mut output = std::io::stdout().lock();
    // A library diagnostic can end without a newline; begin our record on its
    // own line so it cannot be mistaken for that diagnostic (or test output).
    output.write_all(b"\n").and_then(|_| output.write_all(WIRE_PREFIX)).and_then(|_| output.write_all(&data))
        .and_then(|_| output.write_all(b"\n")).and_then(|_| output.flush())
        .map_err(|error| error.to_string())
}

/// The parent protects its own foreground only while this mutation runs. A
/// blocked provider can then be terminated without retaining an in-process COM
/// thread or leaving the foreground lock held indefinitely.
#[cfg(windows)]
pub(crate) fn dispatch_with_parent<T>(dispatch: impl FnOnce() -> Result<T, String>) -> Result<T, String> {
    emit(&Event::DispatchReady)?;
    let approval: Approval = serde_json::from_slice(&input_line(128)?)
        .map_err(|_| "The desktop helper did not receive foreground protection".to_string())?;
    if !approval.allow { return Err("The parent refused the background dispatch; no input was sent".into()); }
    let outcome = dispatch();
    emit(&Event::DispatchFinished)?;
    outcome
}

fn serve(execute: impl FnOnce(&str, &Value) -> Result<Value, String>) -> i32 {
    IN_HELPER.store(true, Ordering::SeqCst);
    let outcome = (|| {
        let mut request: Request = serde_json::from_slice(&input_line(MAX_REQUEST_BYTES)?)
            .map_err(|error| format!("Invalid desktop helper request: {error}"))?;
        if request.version != 1 { return Err("Unsupported desktop helper protocol version".into()); }
        prepare(&request.action, &mut request.args)?;
        execute(&request.action, &request.args)
    })();
    if emit(&Event::Result { outcome }).is_ok() { 0 } else { 1 }
}

fn prepare(action: &str, args: &mut Value) -> Result<(), String> {
    if args["manualControl"].as_bool() != Some(true) {
        return Err("The desktop helper only accepts manual background operations".into());
    }
    // Enforce the policy both before spawn and inside the helper. Request flags
    // cannot enable a foreground fallback or raw pointer/keyboard input.
    crate::desktop_policy::apply(action, args, false)?;
    crate::windows_control::validate_action(action, args)
}

/// Call before ordinary startup so this process never relaunches or opens UI.
pub fn run_if_requested() -> Option<i32> {
    let mut arguments = std::env::args_os().skip(1);
    if arguments.next().as_deref() != Some(std::ffi::OsStr::new(HELPER_ARGUMENT)) { return None; }
    if arguments.next().is_some() { return Some(64); }
    #[cfg(windows)]
    { Some(serve(crate::windows_control::run_in_helper)) }
    #[cfg(not(windows))]
    { Some(serve(|_, _| Err("Desktop control is available on Windows".into()))) }
}

#[cfg(windows)]
pub(crate) fn helper_command() -> Result<tokio::process::Command, String> {
    let mut command = tokio::process::Command::new(std::env::current_exe().map_err(|error| error.to_string())?);
    #[cfg(not(test))]
    command.arg(HELPER_ARGUMENT);
    #[cfg(test)]
    command.args(["--exact", "desktop_helper::tests::process_fixture", "--ignored", "--nocapture"])
        .env("OPENCORE_DESKTOP_HELPER_TEST_MODE", "native");
    Ok(command)
}

#[cfg(windows)]
async fn output_line(reader: &mut (impl tokio::io::AsyncBufRead + Unpin)) -> Result<Vec<u8>, String> {
    use tokio::io::AsyncBufReadExt;
    let mut line = Vec::new();
    loop {
        let available = reader.fill_buf().await.map_err(|error| error.to_string())?;
        if available.is_empty() { return Err("Desktop helper exited before reporting its result. Verify the target before retrying.".into()); }
        let count = available.iter().position(|byte| *byte == b'\n').map_or(available.len(), |index| index + 1);
        if line.len() + count > MAX_RESPONSE_BYTES { return Err("Desktop helper output exceeds its size limit".into()); }
        line.extend_from_slice(&available[..count]);
        reader.consume(count);
        if line.ends_with(b"\n") { return Ok(line); }
    }
}

#[cfg(windows)]
async fn protocol(
    reader: &mut tokio::io::BufReader<tokio::process::ChildStdout>,
    input: &mut tokio::process::ChildStdin,
    args: &Value,
    foreground: &mut Option<crate::windows_control::ManualForegroundGuard>,
    dispatched: &mut bool,
) -> Result<Value, String> {
    use tokio::io::AsyncWriteExt;
    let mut received = 0;
    let mut finished = false;
    let mut dispatch_deadline = None;
    let mut original_desktop = None;
    loop {
        let line = if let Some(deadline) = dispatch_deadline {
            tokio::time::timeout_at(deadline, output_line(reader)).await
                .map_err(|_| "The background action exceeded its dispatch deadline. It may have completed; verify the target before retrying.".to_string())??
        } else { output_line(reader).await? };
        received += line.len();
        if received > MAX_RESPONSE_BYTES + MAX_REQUEST_BYTES { return Err("Desktop helper output exceeds its total size limit".into()); }
        // Native libraries and the disposable Cargo subprocess fixture can
        // print diagnostics. Only our framed records enter the protocol.
        let Some(data) = line.strip_prefix(WIRE_PREFIX) else { continue; };
        let event: Event = serde_json::from_slice(data).map_err(|error| format!("Invalid desktop helper response: {error}"))?;
        match event {
            Event::DispatchReady => {
                if args["manualControl"].as_bool() != Some(true) || *dispatched {
                    return Err("Unexpected desktop helper dispatch request".into());
                }
                let cursor = crate::windows_control::cursor_position()
                    .ok_or("Cannot verify the desktop cursor before background dispatch; no input was sent")?;
                let window = unsafe { windows::Win32::UI::WindowsAndMessaging::GetForegroundWindow() }.0 as isize;
                *foreground = crate::windows_control::ManualForegroundGuard::acquire(args)?;
                original_desktop = Some((window, cursor));
                *dispatched = true;
                dispatch_deadline = Some(tokio::time::Instant::now() + DISPATCH_TIMEOUT);
                input.write_all(b"{\"allow\":true}\n").await.map_err(|error| error.to_string())?;
                input.flush().await.map_err(|error| error.to_string())?;
            }
            Event::DispatchFinished => {
                if !*dispatched || finished { return Err("Unexpected desktop helper dispatch completion".into()); }
                // The helper cannot perform another mutation: the protocol
                // permits exactly one dispatch in an operation.
                foreground.take();
                dispatch_deadline = None;
                finished = true;
            }
            Event::Result { outcome } => {
                if *dispatched && !finished { return Err("The desktop action did not confirm completion. Verify the target before retrying.".into()); }
                if let Some((window, cursor)) = original_desktop {
                    let current = unsafe { windows::Win32::UI::WindowsAndMessaging::GetForegroundWindow() }.0 as isize;
                    return crate::windows_control::annotate_background_result(outcome, current != window,
                        crate::windows_control::cursor_position() != Some(cursor));
                }
                return outcome;
            }
        }
    }
}

/// A canceled command must terminate its helper before releasing foreground
/// protection too. Make the teardown order explicit so later refactors cannot
/// release the foreground while the helper still owns a blocked dispatch.
#[cfg(windows)]
struct HelperLease {
    job: Option<crate::child_guard::ProcessJob>,
    foreground: Option<crate::windows_control::ManualForegroundGuard>,
}

#[cfg(windows)]
impl Drop for HelperLease {
    fn drop(&mut self) {
        self.job.take();
        self.foreground.take();
    }
}

#[cfg(windows)]
pub(crate) async fn execute(action: String, mut args: Value, mut command: tokio::process::Command) -> Result<Value, String> {
    use tokio::io::AsyncWriteExt;
    prepare(&action, &mut args)?;
    let request = serde_json::to_vec(&Request { version: 1, action, args: args.clone() }).map_err(|error| error.to_string())?;
    if request.len() + 1 > MAX_REQUEST_BYTES { return Err("Desktop helper request exceeds its size limit".into()); }
    command.stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null()).creation_flags(0x0800_0000).kill_on_drop(true);
    let mut child = command.spawn().map_err(|error| format!("Cannot start the headless desktop helper: {error}"))?;
    let mut lease = HelperLease {
        job: Some(crate::child_guard::ProcessJob::for_async_child(&child)?),
        foreground: None,
    };
    let mut input = child.stdin.take().ok_or("Desktop helper input is unavailable")?;
    let mut reader = tokio::io::BufReader::new(child.stdout.take().ok_or("Desktop helper output is unavailable")?);
    let mut dispatched = false;
    let outcome = tokio::time::timeout(OPERATION_TIMEOUT, async {
        input.write_all(&request).await.map_err(|error| error.to_string())?;
        input.write_all(b"\n").await.map_err(|error| error.to_string())?;
        input.flush().await.map_err(|error| error.to_string())?;
        protocol(&mut reader, &mut input, &args, &mut lease.foreground, &mut dispatched).await
    }).await.unwrap_or_else(|_| Err(if dispatched {
        "The desktop operation exceeded its deadline. It may have completed; verify the target before retrying.".into()
    } else { "The selected app did not respond within the desktop operation deadline. No foreground fallback was used.".into() }));
    // Kill the complete owned process tree before releasing a protection lease
    // after timeout/transport failure. A hung COM call cannot survive in OpenCore.
    lease.job.take();
    let _ = tokio::time::timeout(std::time::Duration::from_secs(2), child.kill()).await;
    lease.foreground.take();
    outcome
}

#[cfg(windows)]
pub(crate) async fn command(action: String, args: Value) -> Result<Value, String> {
    execute(action, args, helper_command()?).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helper_input_is_bounded_before_parsing_or_execution() {
        assert_eq!(limited_line(&mut std::io::Cursor::new(b"{}\n"), 3).unwrap(), b"{}\n");
        assert!(limited_line(&mut std::io::Cursor::new(b"{}\n"), 2).is_err());
        assert!(limited_line(&mut std::io::Cursor::new(b"{}"), 8).is_err());
        assert!(limited_line(&mut std::io::Cursor::new(vec![b'x'; 65_537]), 65_536).is_err());
        assert!(serde_json::from_slice::<Request>(br#"{"version":1,"action":"list","args":{},"foreground":true}"#).is_err());
        assert!(serde_json::from_slice::<Approval>(br#"{"allow":true,"foreground":true}"#).is_err());
    }

    #[test]
    fn helper_and_parent_refuse_policy_downgrades() {
        for args in [serde_json::json!({}), serde_json::json!({"manualControl":false}),
            serde_json::json!({"manualControl":"true"})] {
            assert!(prepare("list", &mut args.clone()).is_err());
        }
        let policy = serde_json::json!({"manualControl":true,"backgroundOnly":false,
            "allowForegroundFallback":true,"windowId":1,"x":40,"y":20,
            "toX":90,"toY":80,"text":"hello","key":"Enter","direction":"down",
            "url":"https://example.com"});
        for action in ["move", "click", "drag", "type", "key", "scroll", "navigate_url"] {
            assert!(prepare(action, &mut policy.clone()).is_err(), "{action} must remain background only");
        }
        let mut args = policy;
        prepare("interact", &mut args).unwrap();
        assert_eq!(args["backgroundOnly"], true);
        assert_eq!(args["allowForegroundFallback"], false);
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "Headless subprocess entry used only by the GitHub Actions disposable fixture"]
    fn process_fixture() {
        assert_eq!(std::env::var("GITHUB_ACTIONS").as_deref(), Ok("true"));
        let mode = std::env::var("OPENCORE_DESKTOP_HELPER_TEST_MODE").unwrap();
        let code = serve(|action, args| {
            if mode == "hang_dispatch" {
                return dispatch_with_parent(|| {
                    if let Some(path) = args["processReceipt"].as_str() {
                        // The parent has granted dispatch and holds its lease
                        // before this receipt confirms the fixture is blocked.
                        std::fs::write(path, std::process::id().to_string()).map_err(|error| error.to_string())?;
                    }
                    std::thread::sleep(std::time::Duration::from_secs(30));
                    Ok(serde_json::json!({"activated":true}))
                });
            }
            crate::windows_control::run_in_helper(action, args)
        });
        assert_eq!(code, 0);
    }
}
