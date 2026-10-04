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

/// Checks the configured signed update feed only when the user asks.
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

/// Installs only after the user presses Update. A busy session is left alone.
#[tauri::command]
pub async fn install_latest_app_update(
    app: AppHandle,
    core: State<'_, Arc<AppCore>>,
) -> Result<(), String> {
    if cfg!(debug_assertions) {
        return Err("Updates are available only in an installed OpenCore build.".into());
    }
    let core = Arc::clone(core.inner());
    if !is_idle(&core) || core.speech.is_active().await || music_has_model().await {
        return Err(
            "Finish active chats, model work, or Music Studio generation before updating.".into(),
        );
    }
    let updater = configured_updater(&app).await?;
    let update = updater
        .check()
        .await
        .map_err(|error| error.to_string())?
        .ok_or_else(|| {
            "OpenCore is already up to date. Check again for the latest version.".to_string()
        })?;
    let version = update.version.clone();
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|error| error.to_string())?;

    if !is_idle(&core) || core.speech.is_active().await || music_has_model().await {
        return Err("A model or generation started during the download. Finish it, then press Update again.".into());
    }
    let _gpu = crate::studio_jobs::reserve_gpu()?;
    core.speech.release_idle_model().await?;
    let runtime = core.runtime.clone();
    tauri::async_runtime::spawn_blocking(move || runtime.stop())
        .await
        .map_err(|e| e.to_string())??;
    if core.studios.busy() || core.studios.continuation_pending() {
        return Err(
            "A studio request started during shutdown. Finish it, then press Update again.".into(),
        );
    }
    update
        .install(bytes)
        .map_err(|error| format!("Could not install OpenCore {version}: {error}"))?;
    app.restart();
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
