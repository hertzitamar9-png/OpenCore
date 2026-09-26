use crate::AppCore;
use serde::Serialize;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, State};
use tauri_plugin_updater::UpdaterExt;

static UPDATE_CHECK_ACTIVE: AtomicBool = AtomicBool::new(false);

struct UpdateCheckGuard;
impl Drop for UpdateCheckGuard {
    fn drop(&mut self) {
        UPDATE_CHECK_ACTIVE.store(false, Ordering::Release);
    }
}

#[derive(Clone, Serialize)]
struct UpdateNotice {
    state: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    downloaded: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    total: Option<u64>,
}

fn emit_notice(
    app: &AppHandle,
    state: &'static str,
    version: Option<String>,
    downloaded: Option<u64>,
    total: Option<u64>,
) {
    let _ = app.emit(
        "opencore-auto-update",
        UpdateNotice {
            state,
            version,
            downloaded,
            total,
        },
    );
}

fn parse_cli_token(stdout: &[u8]) -> Option<String> {
    let token = String::from_utf8_lossy(stdout).trim().to_string();
    (!token.is_empty()).then_some(token)
}

fn github_cli_token() -> Result<Option<String>, String> {
    let mut candidates = vec![PathBuf::from("gh.exe"), PathBuf::from("gh")];
    if let Some(program_files) = std::env::var_os("ProgramFiles") {
        candidates.push(
            PathBuf::from(program_files)
                .join("GitHub CLI")
                .join("gh.exe"),
        );
    }
    if let Some(local_app_data) = std::env::var_os("LOCALAPPDATA") {
        candidates.push(
            PathBuf::from(local_app_data)
                .join("Programs")
                .join("GitHub CLI")
                .join("gh.exe"),
        );
    }

    for executable in candidates {
        let mut command = Command::new(&executable);
        command.args(["auth", "token", "--hostname", "github.com"]);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW: never flash a console during startup.
        }
        let output = match command.output() {
            Ok(output) => output,
            Err(_) => continue,
        };
        if output.status.success() {
            if let Some(token) = parse_cli_token(&output.stdout) {
                return Ok(Some(token));
            }
        }
    }
    Ok(None)
}

fn is_idle(core: &AppCore) -> bool {
    core.runtime.snapshot().status == "stopped"
        && core
            .active_chats
            .lock()
            .map(|chats| chats.is_empty())
            .unwrap_or(false)
}

/// Check the private GitHub Releases feed and install a signed update when the
/// app is idle. The credential stays in Rust memory and is never sent to JS or logs.
#[tauri::command]
pub async fn auto_update(app: AppHandle, core: State<'_, Arc<AppCore>>) -> Result<(), String> {
    if cfg!(debug_assertions) {
        return Ok(());
    }
    if UPDATE_CHECK_ACTIVE
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return Ok(());
    }
    let _guard = UpdateCheckGuard;
    let core = Arc::clone(core.inner());

    // Never close OpenCore while a model or a chat turn is active. The periodic
    // check will retry once the runtime is stopped, or on the next app launch.
    if !is_idle(&core) {
        return Ok(());
    }

    let token = match tauri::async_runtime::spawn_blocking(github_cli_token).await {
        Ok(Ok(token)) => token,
        _ => {
            emit_notice(&app, "failed", None, None, None);
            return Ok(());
        }
    };
    let Some(token) = token else {
        emit_notice(&app, "auth-required", None, None, None);
        return Ok(());
    };

    let builder = match app
        .updater_builder()
        .header("Authorization", format!("Bearer {token}"))
        .and_then(|builder| builder.header("Accept", "application/octet-stream"))
    {
        Ok(builder) => builder,
        Err(_) => {
            emit_notice(&app, "failed", None, None, None);
            return Ok(());
        }
    };
    let updater = match builder.build() {
        Ok(updater) => updater,
        Err(_) => {
            emit_notice(&app, "failed", None, None, None);
            return Ok(());
        }
    };
    let update = match updater.check().await {
        Ok(update) => update,
        Err(_) => {
            emit_notice(&app, "failed", None, None, None);
            return Ok(());
        }
    };
    let Some(update) = update else {
        return Ok(());
    };
    let version = update.version.clone();
    emit_notice(&app, "downloading", Some(version.clone()), Some(0), None);

    let mut downloaded = 0_u64;
    let mut last_reported = 0_u64;
    let mut last_report = Instant::now();
    let bytes = match update
        .download(
            |chunk_length, content_length| {
                downloaded = downloaded.saturating_add(chunk_length as u64);
                if downloaded.saturating_sub(last_reported) >= 1_048_576
                    || last_report.elapsed() >= Duration::from_secs(1)
                {
                    emit_notice(
                        &app,
                        "downloading",
                        Some(version.clone()),
                        Some(downloaded),
                        content_length,
                    );
                    last_reported = downloaded;
                    last_report = Instant::now();
                }
            },
            || {},
        )
        .await
    {
        Ok(bytes) => bytes,
        Err(_) => {
            emit_notice(&app, "failed", Some(version), None, None);
            return Ok(());
        }
    };

    // A chat or model may have started while the signed package downloaded.
    // Defer installation until an idle check to avoid restarting mid-session.
    if !is_idle(&core) {
        emit_notice(&app, "waiting", Some(version), None, None);
        return Ok(());
    }

    emit_notice(&app, "restarting", Some(version), None, None);
    if update.install(bytes).is_err() {
        emit_notice(&app, "failed", None, None, None);
    } else {
        app.restart();
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn github_cli_token_output_is_trimmed_without_logging() {
        assert_eq!(
            super::parse_cli_token(b"gho_example-secret-token\r\n").as_deref(),
            Some("gho_example-secret-token")
        );
        assert_eq!(super::parse_cli_token(b" \r\n"), None);
    }
}
