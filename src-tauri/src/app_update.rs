use crate::AppCore;
use reqwest::Url;
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

fn github_cli_candidates() -> Vec<PathBuf> {
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
    candidates
}

fn parse_manifest_asset_url(release_json: &[u8]) -> Option<Url> {
    let release: serde_json::Value = serde_json::from_slice(release_json).ok()?;
    let assets = release.get("assets")?.as_array()?;
    let mut manifests = assets
        .iter()
        .filter(|asset| asset.get("name").and_then(|name| name.as_str()) == Some("latest.json"));
    let asset = manifests.next()?;
    if manifests.next().is_some() {
        return None;
    }

    let url = Url::parse(asset.get("url")?.as_str()?).ok()?;
    if url.scheme() != "https"
        || url.host_str() != Some("api.github.com")
        || url.port_or_known_default() != Some(443)
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return None;
    }
    let asset_id = url
        .path()
        .strip_prefix("/repos/hertzitamar9-png/OpenCore/releases/assets/")?;
    if asset_id.is_empty() || !asset_id.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    Some(url)
}

/// Resolve the latest private-release manifest through GitHub's authenticated
/// API. GitHub's browser-style `/releases/latest/download/...` route returns
/// 404 for this private repo even when the app supplies a token; the API asset
/// URL is stable for the lifetime of a release and is regenerated each release.
fn github_cli_latest_manifest_url() -> Option<Url> {
    for executable in github_cli_candidates() {
        let mut command = Command::new(executable);
        command.args(["api", "repos/hertzitamar9-png/OpenCore/releases/latest"]);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        let output = match command.output() {
            Ok(output) if output.status.success() => output,
            _ => continue,
        };
        return parse_manifest_asset_url(&output.stdout);
    }
    None
}

fn github_cli_token() -> Result<Option<String>, String> {
    for executable in github_cli_candidates() {
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
    let runtime = core.runtime.snapshot();
    update_allowed(
        &runtime.status,
        runtime.model_pid.is_some() || runtime.echo_pid.is_some(),
        core.active_chats
            .lock()
            .map(|chats| chats.is_empty())
            .unwrap_or(false),
        core.studios.busy()
            || core.studios.continuation_pending()
            || crate::studio_jobs::gpu_reserved(),
    )
}
fn update_allowed(status: &str, owned: bool, chats_idle: bool, jobs_busy: bool) -> bool {
    chats_idle && !jobs_busy && (status == "stopped" || status == "running" && owned)
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
    if !is_idle(&core) || core.speech.is_active().await {
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
    let manifest_url =
        match tauri::async_runtime::spawn_blocking(github_cli_latest_manifest_url).await {
            Ok(Some(url)) => url,
            _ => {
                emit_notice(&app, "failed", None, None, None);
                return Ok(());
            }
        };

    let builder = match app
        .updater_builder()
        .endpoints(vec![manifest_url])
        .and_then(|builder| builder.header("Authorization", format!("Bearer {token}")))
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
        emit_notice(
            &app,
            "up-to-date",
            Some(app.package_info().version.to_string()),
            None,
            None,
        );
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
    if !is_idle(&core) || core.speech.is_active().await || music_has_model().await {
        emit_notice(&app, "waiting", Some(version), None, None);
        return Ok(());
    }

    let _gpu = match crate::studio_jobs::reserve_gpu() {
        Ok(guard) => guard,
        Err(_) => {
            emit_notice(&app, "waiting", Some(version), None, None);
            return Ok(());
        }
    };
    core.speech.release_idle_model().await?;
    let runtime = core.runtime.clone();
    tauri::async_runtime::spawn_blocking(move || runtime.stop())
        .await
        .map_err(|e| e.to_string())??;
    // Do not restart if a studio form queued a new request during model teardown.
    if core.studios.busy() || core.studios.continuation_pending() {
        emit_notice(&app, "waiting", Some(version), None, None);
        return Ok(());
    }

    emit_notice(&app, "installing", Some(version.clone()), None, None);
    // Give the webview a moment to paint the in-app applying state before the
    // Windows updater starts and exits this process.
    tokio::time::sleep(Duration::from_millis(500)).await;
    if update.install(bytes).is_err() {
        emit_notice(&app, "failed", Some(version), None, None);
    } else {
        app.restart();
    }
    Ok(())
}
async fn music_has_model() -> bool {
    let status = crate::music_studio::music_studio_status().await;
    status.model_loaded
        || (status.running
            && crate::music_studio::request("GET", "/api/status", None)
                .await
                .is_ok_and(|s| s["status"] == "running"))
}

#[cfg(test)]
mod tests {
    #[test]
    fn update_defers_background_jobs_and_unowned_or_loading_models() {
        assert!(super::update_allowed("stopped", false, true, false));
        assert!(super::update_allowed("running", true, true, false));
        assert!(!super::update_allowed("running", false, true, false));
        assert!(!super::update_allowed("starting", true, true, false));
        assert!(!super::update_allowed("stopped", false, true, true));
        assert!(!super::update_allowed("running", true, false, false));
    }
    #[test]
    fn github_cli_token_output_is_trimmed_without_logging() {
        assert_eq!(
            super::parse_cli_token(b"gho_example-secret-token\r\n").as_deref(),
            Some("gho_example-secret-token")
        );
        assert_eq!(super::parse_cli_token(b" \r\n"), None);
    }

    #[test]
    fn latest_release_manifest_asset_url_is_selected_from_authenticated_api_json() {
        let release = br#"{
            "assets": [
                {"name": "latest.json.backup", "url": "https://api.github.com/repos/hertzitamar9-png/OpenCore/releases/assets/1"},
                {"name": "latest.json", "url": "https://api.github.com/repos/hertzitamar9-png/OpenCore/releases/assets/589608428"}
            ]
        }"#;

        let url = super::parse_manifest_asset_url(release).unwrap();
        assert_eq!(
            url.as_str(),
            "https://api.github.com/repos/hertzitamar9-png/OpenCore/releases/assets/589608428"
        );
    }

    #[test]
    fn latest_release_manifest_asset_url_rejects_untrusted_or_ambiguous_assets() {
        let untrusted = br#"{"assets":[{"name":"latest.json","url":"https://attacker.example/releases/assets/2"}]}"#;
        let duplicate = br#"{"assets":[
            {"name":"latest.json","url":"https://api.github.com/repos/hertzitamar9-png/OpenCore/releases/assets/2"},
            {"name":"latest.json","url":"https://api.github.com/repos/hertzitamar9-png/OpenCore/releases/assets/3"}
        ]}"#;

        assert!(super::parse_manifest_asset_url(untrusted).is_none());
        assert!(super::parse_manifest_asset_url(duplicate).is_none());
    }
}
