//! Install the bundled bridge through Claude's supported local marketplace API.
//! Runs off the UI thread, without inference, authentication or provider changes.
use crate::{claude_bridge, store::EventStore};
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    process::Stdio,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tauri::Manager;

const PLUGIN: &str = "opencore-bridge@opencore-local";
pub const READY_KEY: &str = "claude_bridge_registered_v1";
pub const ERROR_KEY: &str = "claude_bridge_setup_error_v1";
pub const ENABLED_KEY: &str = "claude_bridge_enabled_v1";
static SETUP_ACTIVE: AtomicBool = AtomicBool::new(false);
struct SetupGuard;
impl Drop for SetupGuard {
    fn drop(&mut self) {
        SETUP_ACTIVE.store(false, Ordering::Release);
    }
}

fn native_cli(resources: &Path) -> PathBuf {
    let package = if cfg!(windows) {
        "claude-agent-sdk-win32-x64"
    } else if cfg!(target_os = "macos") && cfg!(target_arch = "aarch64") {
        "claude-agent-sdk-darwin-arm64"
    } else if cfg!(target_os = "macos") {
        "claude-agent-sdk-darwin-x64"
    } else if cfg!(target_arch = "aarch64") {
        "claude-agent-sdk-linux-arm64"
    } else {
        "claude-agent-sdk-linux-x64"
    };
    resources
        .join("node_modules/@anthropic-ai")
        .join(package)
        .join(if cfg!(windows) {
            "claude.exe"
        } else {
            "claude"
        })
}

async fn cli(executable: &Path, args: &[&str], config: Option<&Path>) -> Result<String, String> {
    let mut command = tokio::process::Command::new(executable);
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    if let Some(config) = config {
        command.env("CLAUDE_CONFIG_DIR", config);
    }
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    let child = command
        .spawn()
        .map_err(|e| format!("Could not configure the bundled Claude bridge: {e}"))?;
    #[cfg(windows)]
    if let Some(handle) = child.raw_handle() {
        crate::child_guard::adopt_handle(handle);
    }
    let output = tokio::time::timeout(Duration::from_secs(30), child.wait_with_output())
        .await
        .map_err(|_| {
            "Claude bridge configuration timed out; OpenCore will retry automatically".to_string()
        })?
        .map_err(|e| e.to_string())?;
    if !output.status.success() {
        // Management commands may explain a managed-policy restriction. Never bypass it.
        let message = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        return Err(format!(
            "Claude bridge configuration failed: {}",
            crate::redaction::redact_text(&message)
                .chars()
                .take(2000)
                .collect::<String>()
        ));
    }
    String::from_utf8(output.stdout).map_err(|e| e.to_string())
}

fn installed<'a>(list: &'a Value, version: Option<&str>) -> Option<&'a Value> {
    list.as_array()?.iter().find(|p| {
        p["id"] == PLUGIN && p["scope"] == "user" && version.is_none_or(|v| p["version"] == v)
    })
}

/// Verify the cache really contains this paired build, not merely a stale install receipt.
fn cache_matches(plugin: &Value, source: &Path) -> bool {
    let Some(path) = plugin["installPath"].as_str() else {
        return false;
    };
    [
        ".claude-plugin/plugin.json",
        "hooks/local-config.mjs",
        "hooks/register.js",
        "hooks/bridge.mjs",
        "hooks/hooks.json",
        "commands/music.md",
        "commands/assets.md",
    ]
    .iter()
    .all(|name| {
        let expected = std::fs::read(source.join(name));
        let actual = std::fs::read(Path::new(path).join(name));
        matches!((expected, actual), (Ok(a), Ok(b)) if a == b)
    })
}

async fn register(
    executable: &Path,
    market: &Path,
    source: &Path,
    version: &str,
    config: Option<&Path>,
) -> Result<bool, String> {
    let list: Value =
        serde_json::from_str(&cli(executable, &["plugin", "list", "--json"], config).await?)
            .map_err(|e| format!("Invalid Claude plugin inventory: {e}"))?;
    if let Some(plugin) = installed(&list, Some(version)).filter(|p| cache_matches(p, source)) {
        // Preserve a user's explicit disable choice on later app launches.
        return Ok(plugin["enabled"].as_bool().unwrap_or(false));
    }
    let markets: Value = serde_json::from_str(
        &cli(
            executable,
            &["plugin", "marketplace", "list", "--json"],
            config,
        )
        .await?,
    )
    .map_err(|e| e.to_string())?;
    let registered = markets
        .as_array()
        .and_then(|items| items.iter().find(|p| p["name"] == "opencore-local"));
    if let Some(existing) = registered {
        let location = existing["path"]
            .as_str()
            .or_else(|| existing["installLocation"].as_str());
        if location.and_then(|p| std::fs::canonicalize(p).ok())
            != std::fs::canonicalize(market).ok()
        {
            return Err(
                "A different Claude marketplace already uses the name opencore-local".into(),
            );
        }
        cli(
            executable,
            &["plugin", "marketplace", "update", "opencore-local"],
            config,
        )
        .await?;
    } else {
        cli(
            executable,
            &[
                "plugin",
                "marketplace",
                "add",
                &market.to_string_lossy(),
                "--scope",
                "user",
            ],
            config,
        )
        .await?;
    }
    let action = if installed(&list, None).is_some() {
        "update"
    } else {
        "install"
    };
    cli(
        executable,
        &["plugin", action, PLUGIN, "--scope", "user", "--json"],
        config,
    )
    .await?;
    let verified: Value =
        serde_json::from_str(&cli(executable, &["plugin", "list", "--json"], config).await?)
            .map_err(|e| e.to_string())?;
    let plugin = installed(&verified, Some(version))
        .filter(|p| cache_matches(p, source))
        .ok_or("Claude did not confirm installation of the current paired bridge")?;
    Ok(plugin["enabled"].as_bool().unwrap_or(false))
}

pub async fn ensure(
    store: &EventStore,
    data: &Path,
    executable: &Path,
    config: Option<&Path>,
) -> Result<(), String> {
    let token = store
        .get_setting(claude_bridge::TOKEN_KEY)?
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("{}{}", uuid::Uuid::new_v4(), uuid::Uuid::new_v4()));
    // Keep the original bridge directory compatible with earlier explicit launches.
    let original = data.join("claude-bridge");
    claude_bridge::install_files(&original, &token)?;
    store.set_setting(claude_bridge::TOKEN_KEY, &token)?;
    let market = data.join("claude-marketplace");
    let source = market.join("plugins/opencore-bridge");
    claude_bridge::install_files(&source, &token)?;
    let version = serde_json::from_slice::<Value>(
        &std::fs::read(source.join(".claude-plugin/plugin.json")).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?["version"]
        .as_str()
        .ok_or("Missing bundled bridge version")?
        .to_string();
    std::fs::create_dir_all(market.join(".claude-plugin")).map_err(|e| e.to_string())?;
    std::fs::write(market.join(".claude-plugin/marketplace.json"), serde_json::to_vec_pretty(&json!({
        "name":"opencore-local", "owner":{"name":"OpenCore"},
        "plugins":[{"name":"opencore-bridge", "source":"./plugins/opencore-bridge", "version":version}]
    })).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    let enabled = register(executable, &market, &source, &version, config).await?;
    store.set_setting(READY_KEY, &version)?;
    store.set_setting(ENABLED_KEY, if enabled { "true" } else { "false" })?;
    store.set_setting(ERROR_KEY, "")?;
    Ok(())
}

pub fn start(store: Arc<EventStore>, app: &tauri::AppHandle) {
    let Ok(data) = app.path().app_data_dir() else {
        return;
    };
    let packaged = app.path().resource_dir().ok().map(|p| p.join("claude"));
    let resources = packaged
        .filter(|p| native_cli(p).is_file())
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/claude"));
    let executable = native_cli(&resources);
    if SETUP_ACTIVE.swap(true, Ordering::AcqRel) {
        return;
    }
    tauri::async_runtime::spawn(async move {
        let _guard = SetupGuard;
        for attempt in 0..3 {
            match ensure(&store, &data, &executable, None).await {
                Ok(()) => return,
                Err(error) => {
                    let _ = store.set_setting(ERROR_KEY, &error);
                    store.log("warn", "claude-bridge", &error);
                    if attempt < 2 {
                        tokio::time::sleep(Duration::from_secs(30)).await;
                    }
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inventory_must_match_scope_revision_and_actual_paired_files() {
        let list = json!([
            {"id":PLUGIN,"version":"old","scope":"project"},
            {"id":PLUGIN,"version":"old","scope":"user","enabled":true}
        ]);
        assert!(installed(&list, Some("current")).is_none());
        assert!(installed(&list, Some("old")).is_some());
        assert!(!cache_matches(
            installed(&list, None).unwrap(),
            Path::new("missing")
        ));
    }

    #[tokio::test]
    #[ignore = "Requires the bundled native Claude CLI; uses only an isolated temporary config"]
    async fn native_zero_setup_installs_updates_and_preserves_user_configuration() {
        let root =
            std::env::temp_dir().join(format!("opencore-auto-bridge-{}", uuid::Uuid::new_v4()));
        let config = root.join("config");
        std::fs::create_dir_all(&config).unwrap();
        std::fs::write(
            config.join("settings.json"),
            r#"{"env":{"TEST_PRESERVED":"yes"}}"#,
        )
        .unwrap();
        let store = EventStore::open(&root.join("app.db")).unwrap();
        let executable =
            native_cli(&PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/claude"));
        ensure(&store, &root.join("data"), &executable, Some(&config))
            .await
            .unwrap();
        let token = store
            .get_setting(claude_bridge::TOKEN_KEY)
            .unwrap()
            .unwrap();
        let old_version = store.get_setting(READY_KEY).unwrap();
        // Pairing rotation invalidates the cached build and must update its installed copy.
        store
            .set_setting(claude_bridge::TOKEN_KEY, "rotated-test-pairing")
            .unwrap();
        ensure(&store, &root.join("data"), &executable, Some(&config))
            .await
            .unwrap();
        assert_ne!(old_version, store.get_setting(READY_KEY).unwrap());
        assert_ne!(token, "rotated-test-pairing");
        let settings: Value =
            serde_json::from_slice(&std::fs::read(config.join("settings.json")).unwrap()).unwrap();
        assert_eq!(settings["env"]["TEST_PRESERVED"], "yes");
        assert_eq!(settings["enabledPlugins"][PLUGIN], true);
        cli(
            &executable,
            &["plugin", "disable", PLUGIN, "--scope", "user"],
            Some(&config),
        )
        .await
        .unwrap();
        ensure(&store, &root.join("data"), &executable, Some(&config))
            .await
            .unwrap();
        assert_eq!(
            store.get_setting(ENABLED_KEY).unwrap().as_deref(),
            Some("false")
        );
        store
            .set_setting(claude_bridge::TOKEN_KEY, "rotated-again-while-disabled")
            .unwrap();
        ensure(&store, &root.join("data"), &executable, Some(&config))
            .await
            .unwrap();
        assert_eq!(
            store.get_setting(ENABLED_KEY).unwrap().as_deref(),
            Some("false")
        );
        // No model calls, no --plugin-dir, and no writes to the real Claude config.
        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn failed_native_setup_retains_pairing_without_claiming_registration() {
        let root =
            std::env::temp_dir().join(format!("opencore-auto-failure-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let store = EventStore::open(&root.join("app.db")).unwrap();
        let error = ensure(
            &store,
            &root,
            &root.join("missing-native-cli"),
            Some(&root.join("config")),
        )
        .await
        .unwrap_err();
        assert!(error.contains("Could not configure"));
        let token = store
            .get_setting(claude_bridge::TOKEN_KEY)
            .unwrap()
            .unwrap();
        assert!(store.get_setting(READY_KEY).unwrap().is_none());
        assert!(ensure(
            &store,
            &root,
            &root.join("missing-native-cli"),
            Some(&root.join("config"))
        )
        .await
        .is_err());
        assert_eq!(
            store
                .get_setting(claude_bridge::TOKEN_KEY)
                .unwrap()
                .as_deref(),
            Some(token.as_str())
        );
        drop(store);
        std::fs::remove_dir_all(root).unwrap();
    }
}
