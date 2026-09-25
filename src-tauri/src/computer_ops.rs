use serde_json::{json, Value};
use std::process::Stdio;
#[cfg(windows)]
use std::path::PathBuf;

#[cfg(windows)]
const NO_WINDOW: u32 = 0x0800_0000;

pub(crate) fn with_workspace(mut args: Value, workspace: &std::path::Path) -> Value {
    if args["action"] == "run_command" && args["cwd"].as_str().is_none_or(|cwd| cwd.trim().is_empty()) {
        args["cwd"] = json!(workspace.to_string_lossy());
    }
    args
}

async fn output(mut process: tokio::process::Command) -> Result<String, String> {
    process.stdout(Stdio::piped()).stderr(Stdio::piped());
    process.kill_on_drop(true);
    #[cfg(windows)]
    process.creation_flags(NO_WINDOW);
    let result = tokio::time::timeout(std::time::Duration::from_secs(30), process.output()).await
        .map_err(|_| "Command exceeded 30 seconds".to_string())?
        .map_err(|error| error.to_string())?;
    let stdout = String::from_utf8_lossy(&result.stdout);
    let stderr = String::from_utf8_lossy(&result.stderr);
    if !result.status.success() { return Err(format!("Command exited {}: {}", result.status, stderr.chars().take(4000).collect::<String>())); }
    if result.stdout.len() > 4 * 1024 * 1024 { return Err("Command output exceeded 4 MiB".into()); }
    Ok(stdout.into_owned())
}

#[cfg(windows)]
fn known_install_apps(roots: &[PathBuf]) -> Vec<Value> {
    roots.iter().map(|root| root.join("Google").join("Chrome").join("Application").join("chrome.exe"))
        .filter(|path| path.is_file())
        .map(|path| json!({"Name":"Google Chrome","AppID":path.to_string_lossy()}))
        .collect()
}

#[cfg(windows)]
async fn start_apps() -> Result<Vec<Value>, String> {
    let mut process = tokio::process::Command::new("powershell.exe");
    process.args(["-NoProfile", "-NonInteractive", "-Command", "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"]);
    let raw = output(process).await?;
    let parsed: Value = serde_json::from_str(&raw).map_err(|error| error.to_string())?;
    let mut apps = match parsed { Value::Array(items) => items, Value::Object(_) => vec![parsed], _ => vec![] };
    let roots = ["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"].iter()
        .filter_map(|name| std::env::var_os(name).map(PathBuf::from))
        .collect::<Vec<_>>();
    for app in known_install_apps(&roots) {
        if !apps.iter().any(|item| item["AppID"] == app["AppID"]) { apps.push(app); }
    }
    Ok(apps)
}

pub(crate) async fn command(action: &str, args: &Value) -> Result<Value, String> {
    #[cfg(not(windows))]
    { let _ = (action, args); return Err("Computer apps and terminal actions require Windows".into()); }
    #[cfg(windows)]
    match action {
        "find_apps" => {
            let query = args.get("query").and_then(Value::as_str).unwrap_or("").trim().to_lowercase();
            if query.len() > 100 { return Err("App search is too long".into()); }
            let apps = start_apps().await?.into_iter().filter(|app| query.is_empty()
                || app["Name"].as_str().unwrap_or("").to_lowercase().contains(&query))
                .take(50).collect::<Vec<_>>();
            Ok(json!({"apps": apps}))
        }
        "launch_app" => {
            let app_id = args.get("appId").and_then(Value::as_str).ok_or("Select an appId returned by find_apps")?;
            if app_id.len() > 1000 { return Err("App ID is too long".into()); }
            let app = start_apps().await?.into_iter().find(|item| item["AppID"].as_str() == Some(app_id))
                .ok_or("App ID was not found in the current Windows app list")?;
            let quiet = args.get("keepUserWindowInFront").and_then(Value::as_bool).unwrap_or(false);
            if app_id.to_ascii_lowercase().ends_with(".exe") && std::path::Path::new(app_id).is_file() {
                if quiet {
                    let mut process = tokio::process::Command::new("powershell.exe");
                    process.args(["-NoProfile", "-NonInteractive", "-Command", "Start-Process -FilePath $env:OPENCORE_LAUNCH_PATH -WindowStyle Minimized"])
                        .env("OPENCORE_LAUNCH_PATH", app_id);
                    output(process).await?;
                } else { std::process::Command::new(app_id).spawn().map_err(|error| error.to_string())?; }
            } else {
                let mut process = std::process::Command::new("explorer.exe");
                process.arg(format!("shell:AppsFolder\\{app_id}"));
                process.spawn().map_err(|error| error.to_string())?;
            }
            Ok(json!({"launched":app["Name"], "appId":app_id,
                "focus":"Windows may bring the launched app to the foreground even when keepUserWindowInFront is enabled",
                "requestedBackground":quiet}))
        }
        "run_command" => {
            let script = args.get("command").and_then(Value::as_str).ok_or("A command is required")?;
            if script.len() > 16000 { return Err("Command is too long".into()); }
            let trimmed = script.trim();
            if trimmed.len() >= 2 && ((trimmed.starts_with('"') && trimmed.ends_with('"'))
                || (trimmed.starts_with('\'') && trimmed.ends_with('\''))) {
                let inner = &trimmed[1..trimmed.len() - 1];
                if ["Set-Content", "Get-Content", "Test-Path", "Write-Output", "cmd /c "]
                    .iter().any(|verb| inner.to_ascii_lowercase().starts_with(&verb.to_ascii_lowercase())) {
                    return Err("The entire PowerShell command was quoted as a string. Remove the outer quotes and retry.".into());
                }
            }
            let mut process = tokio::process::Command::new("powershell.exe");
            process.args(["-NoProfile", "-NonInteractive", "-Command", script]);
            if let Some(cwd) = args.get("cwd").and_then(Value::as_str) {
                if !std::path::Path::new(cwd).is_dir() { return Err("Working directory does not exist".into()); }
                process.current_dir(cwd);
            }
            Ok(json!({"exitCode":0,"output":output(process).await?.chars().take(16000).collect::<String>()}))
        }
        _ => Err("Unsupported computer action".into()),
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    #[tokio::test]
    async fn workspace_commands_read_the_conversation_file_and_preserve_explicit_cwd() {
        let root = std::env::temp_dir().join(format!("opencore-cwd-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("marker.txt"), "conversation workspace").unwrap();
        let args = with_workspace(json!({"action":"run_command", "command":"Get-Content -LiteralPath marker.txt"}), &root);
        let result = command("run_command", &args).await.unwrap();
        assert_eq!(result["output"].as_str().unwrap().trim(), "conversation workspace");
        let explicit = with_workspace(json!({"action":"run_command", "cwd":"C:/explicit"}), &root);
        assert_eq!(explicit["cwd"], "C:/explicit");
        std::fs::remove_file(root.join("marker.txt")).unwrap();
        std::fs::remove_dir(root).unwrap();
    }
    #[test]
    fn chrome_executable_outside_start_apps_is_discoverable() {
        let root = std::env::temp_dir().join(format!("opencore-chrome-test-{}", uuid::Uuid::new_v4()));
        let path = root.join("Google").join("Chrome").join("Application").join("chrome.exe");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, b"").unwrap();
        let apps = known_install_apps(&[root.clone()]);
        assert_eq!(apps[0]["Name"], "Google Chrome");
        assert_eq!(apps[0]["AppID"], path.to_string_lossy().as_ref());
        std::fs::remove_file(&path).unwrap();
        std::fs::remove_dir(path.parent().unwrap()).unwrap();
        std::fs::remove_dir(path.parent().unwrap().parent().unwrap()).unwrap();
        std::fs::remove_dir(path.parent().unwrap().parent().unwrap().parent().unwrap()).unwrap();
        std::fs::remove_dir(root).unwrap();
    }
    #[tokio::test]
    async fn can_find_installed_apps_without_launching_them() {
        let result = command("find_apps", &json!({"query":"powershell"})).await.unwrap();
        assert!(result["apps"].as_array().is_some());
    }
    #[tokio::test]
    async fn structured_app_output_is_not_cut_off_before_json_parsing() {
        let mut process = tokio::process::Command::new("powershell.exe");
        process.args(["-NoProfile", "-NonInteractive", "-Command", "'{\"Name\":\"' + ('A' * 20000) + '\"}'"]);
        let raw = output(process).await.unwrap();
        let parsed: Value = serde_json::from_str(&raw).unwrap();
        assert_eq!(parsed["Name"].as_str().unwrap().len(), 20000);
    }
    #[tokio::test]
    async fn silent_powershell_success_is_explicit() {
        let result = command("run_command", &json!({"command":"$null"})).await.unwrap();
        assert_eq!(result["exitCode"], 0);
        assert_eq!(result["output"], "");
    }
    #[tokio::test]
    async fn quoted_command_is_not_reported_as_success() {
        let error = command("run_command", &json!({"command":"\"Set-Content -LiteralPath 'x' -Value 'y'\""})).await.unwrap_err();
        assert!(error.contains("outer quotes"));
    }
}
