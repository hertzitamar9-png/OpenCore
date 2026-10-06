use crate::AppCore;
use reqwest::Url;
use serde::Serialize;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Arc;
use tauri::{AppHandle, State};
use tauri_plugin_updater::UpdaterExt;

enum UpdateSource {
    Public,
    AuthenticatedPrivate { manifest_url: Url, token: String },
}

fn select_update_source(token: Option<String>, manifest_url: Option<Url>) -> UpdateSource {
    match (token, manifest_url) {
        (Some(token), Some(manifest_url)) => UpdateSource::AuthenticatedPrivate {
            manifest_url,
            token,
        },
        _ => UpdateSource::Public,
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateCheck {
    current_version: String,
    available: bool,
    version: Option<String>,
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
/// API. Public installs use the standard updater endpoint from `tauri.conf.json`
/// and do not need GitHub CLI or an account.
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

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct UpdateStopPlan {
    cancel_chats: bool,
    stop_runtime: bool,
    cancel_studio_jobs: bool,
}

fn update_stop_plan(
    status: &str,
    runtime_owned: bool,
    chats_active: bool,
    studio_jobs_active: bool,
) -> UpdateStopPlan {
    UpdateStopPlan {
        cancel_chats: chats_active,
        stop_runtime: status != "stopped" || runtime_owned || chats_active,
        cancel_studio_jobs: studio_jobs_active,
    }
}

struct UpdateGuard {
    in_progress: Arc<std::sync::atomic::AtomicBool>,
    keep_for_restart: bool,
}

impl UpdateGuard {
    fn acquire(in_progress: Arc<std::sync::atomic::AtomicBool>) -> Result<Self, String> {
        in_progress
            .compare_exchange(
                false,
                true,
                std::sync::atomic::Ordering::AcqRel,
                std::sync::atomic::Ordering::Acquire,
            )
            .map_err(|_| "An OpenCore update is already in progress.".to_string())?;
        Ok(Self {
            in_progress,
            keep_for_restart: false,
        })
    }

    fn keep_for_restart(&mut self) {
        self.keep_for_restart = true;
    }
}

impl Drop for UpdateGuard {
    fn drop(&mut self) {
        if !self.keep_for_restart {
            self.in_progress
                .store(false, std::sync::atomic::Ordering::Release);
        }
    }
}

async fn stop_active_work_for_update(core: &AppCore) -> Result<(), String> {
    let runtime = core.runtime.snapshot();
    let chats_active = !core
        .active_chats
        .lock()
        .map_err(|error| error.to_string())?
        .is_empty();
    let plan = update_stop_plan(
        &runtime.status,
        runtime.model_pid.is_some() || runtime.echo_pid.is_some(),
        chats_active,
        core.studios.busy(),
    );

    if plan.cancel_chats {
        let active = core
            .active_chats
            .lock()
            .map_err(|error| error.to_string())?;
        for token in active.values() {
            token.cancel();
        }
    }
    let studio_error = if plan.cancel_studio_jobs {
        core.studios.cancel_active().await.err()
    } else {
        None
    };
    let background_error=core.background.cancel_active().await.err();

    // Cancel dictation and stop the verified YuE model before replacing
    // the installed files. Keep going if YuE reports an error so the text model
    // and other app-owned workers are still stopped safely.
    let (_, music_result) = tokio::join!(
        core.speech.stop_for_update(),
        crate::music_studio::stop_for_update(),
    );
    core.reflex.stop();
    core.vision.stop();

    if plan.stop_runtime {
        let runtime = core.runtime.clone();
        runtime.request_stop();
        tauri::async_runtime::spawn_blocking(move || runtime.stop())
            .await
            .map_err(|error| error.to_string())??;
    }
    let mut chats_stopped = false;
    for _ in 0..100 {
        if core
            .active_chats
            .lock()
            .map_err(|error| error.to_string())?
            .is_empty()
        {
            chats_stopped = true;
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
    if !chats_stopped {
        return Err("A chat did not stop after cancellation. OpenCore stopped its model, but the update was not installed.".into());
    }
    if let Some(error)=background_error {
        return Err(format!("Background work did not finish stopping: {error}. The update was not installed."));
    }
    if let Some(error) = studio_error {
        return Err(error);
    }
    music_result?;
    Ok(())
}

async fn configured_updater(app: &AppHandle) -> Result<tauri_plugin_updater::Updater, String> {
    let token = tauri::async_runtime::spawn_blocking(github_cli_token)
        .await
        .map_err(|error| error.to_string())?
        .map_err(|error| error)?;
    let private_manifest_url = if token.is_some() {
        tauri::async_runtime::spawn_blocking(github_cli_latest_manifest_url)
            .await
            .ok()
            .flatten()
    } else {
        None
    };
    let source = select_update_source(token, private_manifest_url);
    let builder = match source {
        UpdateSource::Public => Ok(app.updater_builder()),
        UpdateSource::AuthenticatedPrivate {
            manifest_url,
            token,
        } => app
            .updater_builder()
            .endpoints(vec![manifest_url])
            .and_then(|builder| builder.header("Authorization", format!("Bearer {token}")))
            .and_then(|builder| builder.header("Accept", "application/octet-stream")),
    };
    builder
        .map_err(|error| error.to_string())?
        .build()
        .map_err(|error| error.to_string())
}

/// Read-only check of the configured signed update feed. The header checks on
/// launch to expose an available update; installing still requires a user action.
#[tauri::command]
pub async fn check_latest_app_version(app: AppHandle) -> Result<UpdateCheck, String> {
    let current_version = app.package_info().version.to_string();
    if cfg!(debug_assertions) {
        return Ok(UpdateCheck {
            current_version,
            available: false,
            version: None,
        });
    }
    let update = configured_updater(&app)
        .await?
        .check()
        .await
        .map_err(|error| error.to_string())?;
    Ok(UpdateCheck {
        current_version,
        available: update.is_some(),
        version: update.map(|value| value.version),
    })
}

/// Installs only after the user presses Update. Active model work is stopped first.
#[tauri::command]
pub async fn install_latest_app_update(
    app: AppHandle,
    core: State<'_, Arc<AppCore>>,
) -> Result<(), String> {
    if cfg!(debug_assertions) {
        return Err("Updates are available only in an installed OpenCore build.".into());
    }
    let core = Arc::clone(core.inner());
    let updater = configured_updater(&app).await?;
    let update = updater
        .check()
        .await
        .map_err(|error| error.to_string())?
        .ok_or_else(|| {
            "OpenCore is already up to date. Check again for the latest version.".to_string()
        })?;
    let version = update.version.clone();
    let mut update_guard = UpdateGuard::acquire(core.update_in_progress.clone())?;
    stop_active_work_for_update(&core).await?;
    let _gpu = crate::studio_jobs::reserve_gpu()?;
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|error| error.to_string())?;
    update
        .install(bytes)
        .map_err(|error| format!("Could not install OpenCore {version}: {error}"))?;
    drop(_gpu);
    update_guard.keep_for_restart();
    app.restart();
}
#[cfg(test)]
mod tests {
    #[test]
    fn unauthenticated_updates_use_the_public_tauri_release_endpoint() {
        assert!(matches!(
            super::select_update_source(None, None),
            super::UpdateSource::Public
        ));
    }

    #[test]
    fn authenticated_private_updates_keep_the_private_manifest_route() {
        let url = reqwest::Url::parse(
            "https://api.github.com/repos/hertzitamar9-png/OpenCore/releases/assets/42",
        )
        .unwrap();
        assert!(matches!(
            super::select_update_source(Some("token".into()), Some(url)),
            super::UpdateSource::AuthenticatedPrivate { .. }
        ));
    }

    #[test]
    fn update_plan_cancels_active_work_and_stops_loading_models() {
        assert_eq!(
            super::update_stop_plan("running", true, true, true),
            super::UpdateStopPlan {
                cancel_chats: true,
                stop_runtime: true,
                cancel_studio_jobs: true,
            }
        );
        assert_eq!(
            super::update_stop_plan("starting", false, false, false),
            super::UpdateStopPlan {
                stop_runtime: true,
                ..Default::default()
            }
        );
        assert_eq!(
            super::update_stop_plan("stopped", false, false, false),
            super::UpdateStopPlan::default()
        );
    }

    #[test]
    fn update_guard_rejects_parallel_updates_and_reopens_after_failure() {
        let in_progress = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        {
            let _guard = super::UpdateGuard::acquire(in_progress.clone()).unwrap();
            assert!(super::UpdateGuard::acquire(in_progress.clone()).is_err());
            assert!(in_progress.load(std::sync::atomic::Ordering::Acquire));
        }
        assert!(!in_progress.load(std::sync::atomic::Ordering::Acquire));
    }

    #[test]
    fn update_guard_stays_closed_while_restarting_after_install() {
        let in_progress = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let mut guard = super::UpdateGuard::acquire(in_progress.clone()).unwrap();
        guard.keep_for_restart();
        drop(guard);
        assert!(in_progress.load(std::sync::atomic::Ordering::Acquire));
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
