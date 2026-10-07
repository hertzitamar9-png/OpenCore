use chrono::Utc;
use serde_json::{Map, Value};
use std::path::{Path, PathBuf};
use toml_edit::{value, DocumentMut, Item, Table};

#[path = "agent_connector_config.rs"]
mod agent_connector_config;
pub use agent_connector_config::{
    configure_hermes, configure_opencode, hermes_configured, hermes_configured_in, hermes_history_root,
    hermes_launch_guidance, opencode_configured, opencode_history_root,
};

const GATEWAY: &str = "http://127.0.0.1:8812";
const CLAUDE_OPENCORE_SETTINGS: &str = "opencore-settings.json";
const CLAUDE_OPENCORE_ENV: [(&str, &str); 6] = [
    ("ANTHROPIC_BASE_URL", GATEWAY),
    ("ANTHROPIC_AUTH_TOKEN", "opencore-local"),
    ("ANTHROPIC_MODEL", "opencore"),
    ("ANTHROPIC_DEFAULT_OPUS_MODEL", "opencore"),
    ("ANTHROPIC_DEFAULT_SONNET_MODEL", "opencore"),
    ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "opencore"),
];

fn profile_root() -> Result<PathBuf, String> {
    std::env::var_os("USERPROFILE")
        .map(PathBuf::from)
        .ok_or_else(|| "USERPROFILE is unavailable".to_string())
}

fn backup(path: &Path) -> Result<(), String> {
    if !path.is_file() {
        return Ok(());
    }
    let stamp = Utc::now().format("%Y%m%d-%H%M%S-%f");
    let name = path
        .file_name()
        .and_then(|v| v.to_str())
        .unwrap_or("config");
    let backup = path.with_file_name(format!(
        "{name}.opencore-backup-{stamp}-{}", uuid::Uuid::new_v4()
    ));
    let mut destination = std::fs::File::create_new(backup).map_err(|e| e.to_string())?;
    let mut source = std::fs::File::open(path).map_err(|e| e.to_string())?;
    std::io::copy(&mut source, &mut destination).map_err(|e| e.to_string())?;
    destination.sync_all().map_err(|e| e.to_string())?;
    Ok(())
}

fn ensure_parent(path: &Path) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub fn configure_claude_code() -> Result<String, String> {
    configure_claude_code_at(&profile_root()?.join(".claude"))
}

fn configure_claude_code_at(claude_root: &Path) -> Result<String, String> {
    let global_path = claude_root.join("settings.json");
    if global_path.is_file() {
        let mut global = serde_json::from_slice::<Value>(
            &std::fs::read(&global_path).map_err(|e| e.to_string())?,
        )
        .map_err(|e| format!("Claude settings.json is invalid JSON: {e}"))?;
        if remove_legacy_claude_overrides(&mut global)? > 0 {
            backup(&global_path)?;
            std::fs::write(
                &global_path,
                serde_json::to_vec_pretty(&global).map_err(|e| e.to_string())?,
            )
            .map_err(|e| e.to_string())?;
        }
    }

    let path = claude_root.join(CLAUDE_OPENCORE_SETTINGS);
    ensure_parent(&path)?;
    let root = opencore_claude_settings();
    backup(&path)?;
    std::fs::write(
        &path,
        serde_json::to_vec_pretty(&root).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    if !claude_configured_at(&path) {
        return Err(format!("Claude Code profile was written but did not validate: {}", path.display()));
    }
    Ok(format!(
        "OpenCore Local settings installed for Claude Code. Your normal Claude account and global settings were not changed; use claude --settings \"{}\" when you want the local model.",
        path.display()
    ))
}

fn opencore_claude_settings() -> Value {
    let mut env = Map::new();
    for (key, value) in CLAUDE_OPENCORE_ENV {
        env.insert(key.into(), Value::String(value.into()));
    }
    let mut root = Map::new();
    root.insert("env".into(), Value::Object(env));
    Value::Object(root)
}

fn remove_legacy_claude_overrides(root: &mut Value) -> Result<usize, String> {
    let object = root
        .as_object_mut()
        .ok_or("Claude settings.json must contain a JSON object")?;
    let Some(env) = object.get_mut("env") else {
        return Ok(0);
    };
    let env = env
        .as_object_mut()
        .ok_or("Claude settings env must be an object")?;
    let mut removed = 0;
    for (key, managed_value) in CLAUDE_OPENCORE_ENV {
        if env.get(key).and_then(Value::as_str) == Some(managed_value) {
            env.remove(key);
            removed += 1;
        }
    }
    Ok(removed)
}
fn ensure_table<'a>(parent: &'a mut Table, name: &str) -> &'a mut Table {
    if !parent.contains_key(name) || !parent[name].is_table() {
        parent[name] = Item::Table(Table::new());
    }
    parent[name].as_table_mut().expect("table")
}

pub fn configure_codex() -> Result<String, String> {
    let path = profile_root()?.join(".codex").join("config.toml");
    configure_codex_at(&path)
}

fn configure_codex_at(path: &Path) -> Result<String, String> {
    ensure_parent(&path)?;
    backup(&path)?;
    let source = if path.is_file() {
        std::fs::read_to_string(&path).map_err(|e| e.to_string())?
    } else {
        String::new()
    };
    let mut doc = source
        .parse::<DocumentMut>()
        .map_err(|e| format!("Codex config.toml is invalid TOML: {e}"))?;

    install_codex_profile(&mut doc);

    std::fs::write(&path, doc.to_string()).map_err(|e| e.to_string())?;
    if !codex_configured_at(path) {
        return Err(format!("Codex profile was written but did not validate: {}", path.display()));
    }
    Ok(format!("OpenCore Local profile installed for Codex at {}. Your default provider and login were not changed; use codex --profile opencore when you want the local model.", path.display()))
}

fn install_codex_profile(doc: &mut DocumentMut) {
    let providers = ensure_table(doc.as_table_mut(), "model_providers");
    let provider = ensure_table(providers, "opencore");
    provider["name"] = value("OpenCore Local");
    provider["base_url"] = value("http://127.0.0.1:8812/v1");
    provider["wire_api"] = value("responses");
    provider["experimental_bearer_token"] = value("opencore-local");
    provider["request_max_retries"] = value(2);
    provider["stream_max_retries"] = value(2);
    provider["stream_idle_timeout_ms"] = value(300000);

    let profiles = ensure_table(doc.as_table_mut(), "profiles");
    let profile = ensure_table(profiles, "opencore");
    profile["model"] = value("opencore");
    profile["model_provider"] = value("opencore");
}

pub fn claude_configured() -> bool {
    let Ok(root) = profile_root() else {
        return false;
    };
    claude_configured_at(&root.join(".claude").join(CLAUDE_OPENCORE_SETTINGS))
}

fn claude_configured_at(path: &Path) -> bool {
    let Ok(bytes) = std::fs::read(path) else {
        return false;
    };
    let Ok(value) = serde_json::from_slice::<Value>(&bytes) else {
        return false;
    };
    CLAUDE_OPENCORE_ENV.iter().all(|(key, expected)| value.pointer(&format!("/env/{key}")).and_then(Value::as_str)==Some(*expected))
}

pub fn codex_configured() -> bool {
    let Ok(root) = profile_root() else {
        return false;
    };
    codex_configured_at(&root.join(".codex").join("config.toml"))
}

fn codex_configured_at(path: &Path) -> bool {
    let Ok(source) = std::fs::read_to_string(path) else {
        return false;
    };
    let Ok(doc) = source.parse::<DocumentMut>() else {
        return false;
    };
    let text_at=|section: &str,field: &str|doc.get(section).and_then(Item::as_table)
        .and_then(|table|table.get("opencore")).and_then(Item::as_table)
        .and_then(|table|table.get(field)).and_then(Item::as_value).and_then(|value|value.as_str());
    text_at("model_providers","base_url")==Some("http://127.0.0.1:8812/v1")
        && text_at("model_providers","wire_api")==Some("responses")
        && text_at("model_providers","experimental_bearer_token")==Some("opencore-local")
        && text_at("profiles","model_provider")==Some("opencore")
        && text_at("profiles","model")==Some("opencore")
}
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn configured_codex_profile_is_verified_at_reported_path_and_preserves_defaults() {
        let root=std::env::temp_dir().join(format!("opencore-configured-codex-{}",uuid::Uuid::new_v4()));
        let path=root.join(".codex/config.toml");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path,"model = \"user-model\"\nmodel_provider = \"user-provider\"\n").unwrap();
        let result=configure_codex_at(&path).unwrap();
        assert!(result.contains(path.to_string_lossy().as_ref()));
        assert!(codex_configured_at(&path));
        let installed=std::fs::read_to_string(&path).unwrap().parse::<DocumentMut>().unwrap();
        assert_eq!(installed["model"].as_str(),Some("user-model"));
        assert_eq!(installed["model_provider"].as_str(),Some("user-provider"));
        drop(installed);
        std::fs::write(&path,"[model_providers.opencore]\nbase_url=\"http://127.0.0.1:8812/v1\"\n[profiles.opencore]\nmodel_provider=\"opencore\"\n").unwrap();
        assert!(!codex_configured_at(&path),"a missing Responses transport or model is not a verified connector");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn configured_claude_profile_is_verified_and_preserves_user_account_settings() {
        let root=std::env::temp_dir().join(format!("opencore-configured-claude-{}",uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let global=root.join("settings.json");
        std::fs::write(&global,serde_json::to_vec(&json!({"env":{"ANTHROPIC_BASE_URL":GATEWAY,"ANTHROPIC_MODEL":"user-choice","UNRELATED":"keep"},"enableWorkflows":true})).unwrap()).unwrap();
        let result=configure_claude_code_at(&root).unwrap();
        let managed=root.join(CLAUDE_OPENCORE_SETTINGS);
        assert!(result.contains(managed.to_string_lossy().as_ref())); assert!(claude_configured_at(&managed));
        let preserved:Value=serde_json::from_slice(&std::fs::read(&global).unwrap()).unwrap();
        assert_eq!(preserved["env"]["ANTHROPIC_MODEL"],"user-choice");
        assert_eq!(preserved["env"]["UNRELATED"],"keep"); assert_eq!(preserved["enableWorkflows"],true);
        std::fs::write(&managed,serde_json::to_vec(&json!({"env":{"ANTHROPIC_BASE_URL":GATEWAY,"ANTHROPIC_MODEL":"opencore"}})).unwrap()).unwrap();
        assert!(!claude_configured_at(&managed),"missing managed auth/model fields are not a verified connector");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn toml_provider_shape_is_valid() {
        let mut doc = "".parse::<DocumentMut>().unwrap();
        install_codex_profile(&mut doc);
        let parsed = doc.to_string().parse::<DocumentMut>().unwrap();
        assert!(parsed.get("model").is_none());
        assert!(parsed.get("model_provider").is_none());
        assert_eq!(
            parsed["model_providers"]["opencore"]["wire_api"].as_str(),
            Some("responses")
        );
        assert_eq!(
            parsed["profiles"]["opencore"]["model_provider"].as_str(),
            Some("opencore")
        );
    }

    #[test]
    fn installing_codex_profile_preserves_existing_default_and_account_provider() {
        let source = r#"
model = "gpt-5.6-sol"
model_provider = "openai"
personality = "pragmatic"
"#;
        let mut doc = source.parse::<DocumentMut>().unwrap();

        install_codex_profile(&mut doc);

        assert_eq!(doc["model"].as_str(), Some("gpt-5.6-sol"));
        assert_eq!(doc["model_provider"].as_str(), Some("openai"));
        assert_eq!(doc["personality"].as_str(), Some("pragmatic"));
        assert_eq!(
            doc["profiles"]["opencore"]["model_provider"].as_str(),
            Some("opencore")
        );
    }

    #[test]
    fn claude_opt_in_settings_are_complete_without_global_configuration() {
        let settings = opencore_claude_settings();
        for (key, expected) in CLAUDE_OPENCORE_ENV {
            assert_eq!(
                settings
                    .pointer(&format!("/env/{key}"))
                    .and_then(Value::as_str),
                Some(expected)
            );
        }
        assert_eq!(settings.as_object().unwrap().len(), 1);
    }

    #[test]
    fn repairing_legacy_claude_overrides_preserves_unrelated_and_user_values() {
        let mut settings = serde_json::json!({
            "env": {
                "ANTHROPIC_BASE_URL": GATEWAY,
                "ANTHROPIC_AUTH_TOKEN": "opencore-local",
                "ANTHROPIC_MODEL": "claude-user-selected",
                "UNRELATED": "keep-me"
            },
            "enableWorkflows": true
        });

        let removed = remove_legacy_claude_overrides(&mut settings).unwrap();

        assert_eq!(removed, 2);
        assert!(settings.pointer("/env/ANTHROPIC_BASE_URL").is_none());
        assert!(settings.pointer("/env/ANTHROPIC_AUTH_TOKEN").is_none());
        assert_eq!(
            settings
                .pointer("/env/ANTHROPIC_MODEL")
                .and_then(Value::as_str),
            Some("claude-user-selected")
        );
        assert_eq!(
            settings.pointer("/env/UNRELATED").and_then(Value::as_str),
            Some("keep-me")
        );
        assert_eq!(
            settings.get("enableWorkflows").and_then(Value::as_bool),
            Some(true)
        );
    }
}
