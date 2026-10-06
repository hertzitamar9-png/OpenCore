//! Provider configuration only. Inspection never starts an agent or reads its credentials.
use super::{backup, ensure_parent, profile_root};
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};

const API_BASE: &str = "http://127.0.0.1:8812/v1";
const MAX_CONFIG_BYTES: u64 = 8 * 1024 * 1024;
const HERMES_MIN_CONTEXT: u64 = 65_536;

fn env_path(name: &str) -> Option<PathBuf> {
    std::env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

fn opencode_config_path() -> Result<PathBuf, String> {
    if let Some(path) = env_path("OPENCODE_CONFIG") {
        return Ok(path);
    }
    let root = env_path("XDG_CONFIG_HOME")
        .unwrap_or(profile_root()?.join(".config"))
        .join("opencode");
    // OpenCode loads both forms, with JSONC after JSON. Edit the effective file.
    let commented = root.join("opencode.jsonc");
    Ok(if commented.is_file() {
        commented
    } else {
        root.join("opencode.json")
    })
}

pub fn opencode_history_root() -> Result<PathBuf, String> {
    Ok(env_path("XDG_DATA_HOME")
        .unwrap_or(profile_root()?.join(".local").join("share"))
        .join("opencode"))
}

fn default_hermes_root() -> Result<PathBuf, String> {
    let suffix = std::env::var("HERMES_DATA_DIR_SUFFIX").unwrap_or_default();
    // Installed Hermes' Windows hermes_constants.py uses LOCALAPPDATA, not ~/.hermes.
    #[cfg(windows)]
    let root = env_path("LOCALAPPDATA")
        .unwrap_or(profile_root()?.join("AppData").join("Local"))
        .join(format!("hermes{suffix}"));
    #[cfg(not(windows))]
    let root = profile_root()?.join(format!(".hermes{suffix}"));
    Ok(root)
}

fn hermes_root_for(home: &Path) -> PathBuf {
    if home
        .parent()
        .and_then(Path::file_name)
        .and_then(|name| name.to_str())
        == Some("profiles")
    {
        return home
            .parent()
            .and_then(Path::parent)
            .unwrap_or(home)
            .to_path_buf();
    }
    home.to_path_buf()
}

/// The CLI resolves named profiles under its process's Hermes home, which may be custom.
pub fn hermes_launch_guidance(source_profile: &Path) -> String {
    format!(
        "Launch hermes --profile opencore with HERMES_HOME set to \"{}\" for that process.",
        hermes_root_for(source_profile).display()
    )
}

/// Resolve the actual current profile without importing Hermes or initializing its home.
pub fn hermes_profile_root() -> Result<PathBuf, String> {
    let home = env_path("HERMES_HOME").unwrap_or(default_hermes_root()?);
    if home
        .parent()
        .and_then(Path::file_name)
        .and_then(|name| name.to_str())
        == Some("profiles")
    {
        return Ok(home);
    }
    let active = home.join("active_profile");
    if active.is_file() {
        let name = read_config_text(&active)?
            .trim_start_matches('\u{feff}')
            .trim()
            .to_owned();
        if !name.is_empty() && name != "default" {
            if !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
            {
                return Err(
                    "Hermes' saved profile name is invalid. Select an existing profile folder."
                        .into(),
                );
            }
            let path = home.join("profiles").join(name);
            if !path.is_dir() {
                return Err(
                    "Hermes' saved profile folder is missing. Select an existing profile folder."
                        .into(),
                );
            }
            return Ok(path);
        }
    }
    Ok(home)
}

pub fn hermes_history_root() -> Result<PathBuf, String> {
    hermes_profile_root()
}

fn read_config_text(path: &Path) -> Result<String, String> {
    let metadata = std::fs::metadata(path)
        .map_err(|error| format!("Cannot read the connector config: {error}"))?;
    if !metadata.is_file() || metadata.len() > MAX_CONFIG_BYTES {
        return Err("The connector config must be a regular file smaller than 8 MiB.".into());
    }
    std::fs::read_to_string(path).map_err(|_| "The connector config must be readable UTF-8.".into())
}

/// JSONC comments and trailing commas are accepted; string contents are never rewritten.
fn parse_jsonc(text: &str) -> Result<Value, String> {
    let bytes = text.trim_start_matches('\u{feff}').as_bytes();
    let mut clean = bytes.to_vec();
    let (mut index, mut quoted, mut escaped) = (0, false, false);
    while index < bytes.len() {
        let byte = bytes[index];
        if quoted {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                quoted = false;
            }
            index += 1;
            continue;
        }
        if byte == b'"' {
            quoted = true;
            index += 1;
            continue;
        }
        if byte == b'/' && bytes.get(index + 1) == Some(&b'/') {
            while index < bytes.len() && !matches!(bytes[index], b'\n' | b'\r') {
                clean[index] = b' ';
                index += 1;
            }
            continue;
        }
        if byte == b'/' && bytes.get(index + 1) == Some(&b'*') {
            clean[index] = b' ';
            clean[index + 1] = b' ';
            index += 2;
            let mut closed = false;
            while index < bytes.len() {
                if bytes[index] == b'*' && bytes.get(index + 1) == Some(&b'/') {
                    clean[index] = b' ';
                    clean[index + 1] = b' ';
                    index += 2;
                    closed = true;
                    break;
                }
                if !matches!(bytes[index], b'\n' | b'\r') {
                    clean[index] = b' ';
                }
                index += 1;
            }
            if !closed {
                return Err(
                    "OpenCode config contains an unfinished comment. No settings were changed."
                        .into(),
                );
            }
            continue;
        }
        index += 1;
    }
    quoted = false;
    escaped = false;
    for index in 0..clean.len() {
        let byte = clean[index];
        if quoted {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                quoted = false;
            }
        } else if byte == b'"' {
            quoted = true;
        } else if byte == b',' {
            if clean[index + 1..]
                .iter()
                .find(|byte| !byte.is_ascii_whitespace())
                .is_some_and(|byte| matches!(byte, b'}' | b']'))
            {
                clean[index] = b' ';
            }
        }
    }
    serde_json::from_slice(&clean)
        // Parser excerpts can contain credentials. Show no source excerpts.
        .map_err(|_| "OpenCode config is invalid JSON/JSONC. No settings were changed.".into())
}

fn object_field<'a>(
    object: &'a mut Map<String, Value>,
    key: &str,
) -> Result<&'a mut Map<String, Value>, String> {
    object
        .entry(key)
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or_else(|| {
            format!("Connector config field {key} must be an object. No settings were changed.")
        })
}

fn install_opencode_provider(root: &mut Value, context: u64) -> Result<(), String> {
    if context < 4_096 {
        return Err("Select an OpenCore model before configuring OpenCode.".into());
    }
    let object = root
        .as_object_mut()
        .ok_or("OpenCode config must be an object. No settings were changed.")?;
    let provider = object_field(object_field(object, "provider")?, "opencore")?;
    if provider
        .get("options")
        .and_then(|value| value.get("baseURL"))
        .and_then(Value::as_str)
        .is_some_and(|url| url != API_BASE)
    {
        return Err("OpenCode already has another provider named opencore. Rename that provider before connecting OpenCore.".into());
    }
    provider.insert("npm".into(), json!("@ai-sdk/openai-compatible"));
    provider.insert("name".into(), json!("OpenCore Local"));
    let options = object_field(provider, "options")?;
    options.insert("baseURL".into(), json!(API_BASE));
    options.insert("apiKey".into(), json!("opencore-local"));
    object_field(options, "headers")?.insert("x-opencore-client".into(), json!("OpenCode"));
    let model = object_field(object_field(provider, "models")?, "opencore")?;
    model.insert("name".into(), json!("OpenCore (loaded model)"));
    model.insert("tool_call".into(), json!(true));
    let limit = object_field(model, "limit")?;
    limit.insert("context".into(), json!(context));
    limit.insert("output".into(), json!((context / 4).min(16_384)));
    // Existing allowlists must include the newly installed provider; other choices stay intact.
    if let Some(enabled) = object.get_mut("enabled_providers") {
        let enabled = enabled
            .as_array_mut()
            .ok_or("OpenCode enabled_providers must be an array. No settings were changed.")?;
        if !enabled
            .iter()
            .any(|value| value.as_str() == Some("opencore"))
        {
            enabled.push(json!("opencore"));
        }
    }
    if let Some(disabled) = object.get_mut("disabled_providers") {
        disabled
            .as_array_mut()
            .ok_or("OpenCode disabled_providers must be an array. No settings were changed.")?
            .retain(|value| value.as_str() != Some("opencore"));
    }
    Ok(())
}

fn write_config(path: &Path, bytes: &[u8]) -> Result<(), String> {
    ensure_parent(path)?;
    backup(path)?;
    std::fs::write(path, bytes)
        .map_err(|error| format!("Cannot save the connector config: {error}"))
}

pub fn configure_opencode(context_tokens: u64) -> Result<String, String> {
    let path = opencode_config_path()?;
    let mut config = if path.is_file() {
        parse_jsonc(&read_config_text(&path)?)?
    } else {
        json!({})
    };
    install_opencode_provider(&mut config, context_tokens)?;
    let bytes = serde_json::to_vec_pretty(&config)
        .map_err(|_| "Cannot encode the OpenCode config".to_string())?;
    write_config(&path, &bytes)?;
    Ok(format!("OpenCode's OpenCore provider is installed at {}. Select OpenCore in /models, or launch opencode --model opencore/opencore. Existing provider settings are preserved; an existing config is backed up.", path.display()))
}

fn parse_yaml(source: &str) -> Result<serde_yaml::Value, String> {
    let value = serde_yaml::from_str::<serde_yaml::Value>(source.trim_start_matches('\u{feff}'))
        .map_err(|_| "Hermes config.yaml is invalid YAML. No settings were changed.".to_string())?;
    if !value.is_mapping() {
        return Err("Hermes config.yaml must be a mapping. No settings were changed.".into());
    }
    Ok(value)
}

fn merge_yaml_settings(
    target: &mut serde_yaml::Mapping,
    settings: &serde_yaml::Mapping,
) -> Result<(), String> {
    for (key, value) in settings {
        if key.as_str() == Some("extra_headers") {
            let headers = target
                .entry(key.clone())
                .or_insert_with(|| serde_yaml::Value::Mapping(Default::default()))
                .as_mapping_mut()
                .ok_or("Hermes extra_headers must be a mapping. No settings were changed.")?;
            for (key, value) in value.as_mapping().ok_or("Invalid Hermes header settings")? {
                headers.insert(key.clone(), value.clone());
            }
        } else {
            target.insert(key.clone(), value.clone());
        }
    }
    Ok(())
}

fn install_hermes_provider(config: &mut serde_yaml::Value, context: u64) -> Result<(), String> {
    if context < HERMES_MIN_CONTEXT {
        return Err("Hermes requires a model with at least 65,536 context tokens. Select a supported OpenCore model first.".into());
    }
    let root = config
        .as_mapping_mut()
        .ok_or("Hermes config.yaml must be a mapping")?;
    let provider_key = serde_yaml::Value::String("providers".into());
    let providers = root
        .entry(provider_key)
        .or_insert_with(|| serde_yaml::Value::Mapping(Default::default()))
        .as_mapping_mut()
        .ok_or("Hermes providers must be a mapping. No settings were changed.")?;
    let opencore_key = serde_yaml::Value::String("opencore".into());
    if providers
        .get(&opencore_key)
        .is_some_and(|provider| provider["base_url"].as_str() != Some(API_BASE))
    {
        return Err("Hermes already has another provider named opencore. Rename that provider before connecting OpenCore.".into());
    }
    let entry = serde_yaml::to_value(json!({
        "name":"OpenCore Local", "base_url":API_BASE, "api_key":"opencore-local",
        "model":"opencore", "context_length":context, "api_mode":"chat_completions",
        "extra_headers":{"x-opencore-client":"Hermes Agent"}
    }))
    .map_err(|_| "Cannot encode Hermes provider settings")?;
    // Retain additional settings on an existing OpenCore provider.
    let provider = providers
        .entry(opencore_key)
        .or_insert_with(|| serde_yaml::Value::Mapping(Default::default()))
        .as_mapping_mut()
        .ok_or("Hermes OpenCore provider must be a mapping")?;
    merge_yaml_settings(
        provider,
        entry
            .as_mapping()
            .ok_or("Invalid Hermes provider settings")?,
    )?;
    // This is a separate profile. The user's active/default model config stays in its original file.
    let model_settings = serde_yaml::to_value(json!({
        "default":"opencore", "provider":"custom", "base_url":API_BASE,
        "api_key":"opencore-local", "api_mode":"chat_completions", "context_length":context
    }))
    .map_err(|_| "Cannot encode Hermes model settings")?;
    let model = root
        .entry(serde_yaml::Value::String("model".into()))
        .or_insert_with(|| serde_yaml::Value::Mapping(Default::default()));
    if matches!(&*model, serde_yaml::Value::String(_)) {
        *model = serde_yaml::Value::Mapping(Default::default());
    }
    let model = model
        .as_mapping_mut()
        .ok_or("Hermes model must be a mapping or model name. No settings were changed.")?;
    merge_yaml_settings(
        model,
        model_settings
            .as_mapping()
            .ok_or("Invalid Hermes model settings")?,
    )?;
    Ok(())
}

/// Install a separately selectable profile, copying only config.yaml and no agent data/folders.
pub fn configure_hermes(
    context_tokens: u64,
    source_profile: Option<&Path>,
) -> Result<String, String> {
    if context_tokens < HERMES_MIN_CONTEXT {
        return Err("Hermes requires a model with at least 65,536 context tokens. Select a supported OpenCore model first.".into());
    }
    let base = source_profile
        .map(Path::to_path_buf)
        .map(Ok)
        .unwrap_or_else(hermes_profile_root)?;
    if !base.is_absolute() || !base.is_dir() {
        return Err(
            "Hermes profile folder is unavailable. Choose an existing absolute profile folder."
                .into(),
        );
    }
    let source = base.join("config.yaml");
    if !source.is_file() {
        return Err(
            "Hermes profile config was not found. Choose an existing Hermes profile folder.".into(),
        );
    }
    let destination = hermes_root_for(&base)
        .join("profiles")
        .join("opencore")
        .join("config.yaml");
    let mut config = parse_yaml(&read_config_text(if destination.is_file() {
        &destination
    } else {
        &source
    })?)?;
    if destination.is_file()
        && (config["model"]["base_url"].as_str() != Some(API_BASE)
            || config["model"]["default"].as_str() != Some("opencore"))
    {
        return Err("Hermes already has another profile named opencore. Rename that profile before connecting OpenCore. No settings were changed.".into());
    }
    install_hermes_provider(&mut config, context_tokens)?;
    let bytes =
        serde_yaml::to_string(&config).map_err(|_| "Cannot encode Hermes config".to_string())?;
    write_config(&destination, bytes.as_bytes())?;
    Ok(format!("Hermes' OpenCore profile is installed at {}. {} Existing profiles and credentials are preserved; an existing OpenCore config is backed up.", destination.display(), hermes_launch_guidance(&base)))
}

fn opencode_provider_enabled(config: &Value) -> bool {
    let contains = |ids: &[Value]| ids.iter().any(|id| id.as_str() == Some("opencore"));
    let allowed = match config.get("enabled_providers") {
        None => true,
        Some(Value::Array(ids)) => contains(ids),
        _ => false,
    };
    let unblocked = match config.get("disabled_providers") {
        None => true,
        Some(Value::Array(ids)) => !contains(ids),
        _ => false,
    };
    allowed && unblocked
}

pub fn opencode_configured() -> bool {
    let Ok(path) = opencode_config_path() else {
        return false;
    };
    let Ok(source) = read_config_text(&path) else {
        return false;
    };
    let Ok(config) = parse_jsonc(&source) else {
        return false;
    };
    config
        .pointer("/provider/opencore/options/baseURL")
        .and_then(Value::as_str)
        == Some(API_BASE)
        && config
            .pointer("/provider/opencore/npm")
            .and_then(Value::as_str)
            == Some("@ai-sdk/openai-compatible")
        && config
            .pointer("/provider/opencore/models/opencore/limit/context")
            .and_then(Value::as_u64)
            .is_some()
        && opencode_provider_enabled(&config)
}

pub fn hermes_configured() -> bool {
    hermes_configured_in(None)
}

pub fn hermes_configured_in(source_profile: Option<&Path>) -> bool {
    let Ok(base) = source_profile
        .map(Path::to_path_buf)
        .map(Ok)
        .unwrap_or_else(hermes_profile_root)
    else {
        return false;
    };
    let path = hermes_root_for(&base)
        .join("profiles")
        .join("opencore")
        .join("config.yaml");
    let Ok(source) = read_config_text(&path) else {
        return false;
    };
    let Ok(config) = parse_yaml(&source) else {
        return false;
    };
    config["model"]["base_url"].as_str() == Some(API_BASE)
        && config["model"]["default"].as_str() == Some("opencore")
        && config["model"]["context_length"]
            .as_u64()
            .is_some_and(|value| value >= HERMES_MIN_CONTEXT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opencode_jsonc_keeps_strings_other_providers_and_default_selection() {
        let mut value = parse_jsonc(r#"{
            // personal settings
            "model":"another/model", "provider":{"another":{"options":{"apiKey":"secret","baseURL":"https://host/v1"}}},
            "prompt":"/* string content */ // keep \\\"quoted\\\"", "list":[1,2,],
        }"#).unwrap();
        let previous = value.clone();
        install_opencode_provider(&mut value, 65_536).unwrap();
        assert_eq!(value["model"], previous["model"]);
        assert_eq!(
            value["provider"]["another"],
            previous["provider"]["another"]
        );
        assert_eq!(value["prompt"], previous["prompt"]);
        assert_eq!(value["list"], json!([1, 2]));
        assert_eq!(
            value["provider"]["opencore"]["options"]["headers"]["x-opencore-client"],
            "OpenCode"
        );
        assert_eq!(value["provider"]["opencore"]["limit"], Value::Null);
        assert_eq!(
            value["provider"]["opencore"]["models"]["opencore"]["limit"]["context"],
            65_536
        );
    }

    #[test]
    fn config_errors_expose_no_source_credentials_and_provider_collision_is_rejected() {
        let error = parse_jsonc("{\"apiKey\":\"private-credential\", broken").unwrap_err();
        assert!(!error.contains("private-credential"));
        let mut config =
            json!({"provider":{"opencore":{"options":{"baseURL":"https://another-host/v1"}}}});
        assert!(install_opencode_provider(&mut config, 65_536).is_err());
        assert_eq!(
            config["provider"]["opencore"]["options"]["baseURL"],
            "https://another-host/v1"
        );
    }

    #[test]
    fn opencode_provider_is_selectable_with_existing_allowlists_and_small_context() {
        let mut value = json!({"enabled_providers":["other"],"disabled_providers":["opencore","blocked"],"model":"other/default"});
        install_opencode_provider(&mut value, 8_192).unwrap();
        assert_eq!(value["enabled_providers"], json!(["other", "opencore"]));
        assert_eq!(value["disabled_providers"], json!(["blocked"]));
        assert_eq!(value["model"], "other/default");
        assert!(opencode_provider_enabled(&value));
        assert!(!opencode_provider_enabled(
            &json!({"enabled_providers":["other"]})
        ));
        assert!(!opencode_provider_enabled(
            &json!({"disabled_providers":["opencore"]})
        ));
        assert_eq!(
            value["provider"]["opencore"]["models"]["opencore"]["limit"]["output"],
            2_048
        );
        install_opencode_provider(&mut value, 8_192).unwrap();
        assert_eq!(value["enabled_providers"], json!(["other", "opencore"]));
    }

    #[test]
    fn hermes_profile_uses_custom_api_and_preserves_other_settings_and_secrets() {
        let mut config = parse_yaml("model:\n  default: original\n  provider: nous\n  temperature: 0.7\nproviders:\n  other:\n    api_key: original-secret\n  opencore:\n    base_url: http://127.0.0.1:8812/v1\n    extra_headers:\n      x-user-setting: retained-header\nterminal:\n  cwd: C:/original\ncustom_providers:\n- name: legacy\n  api_key: retained-secret\n").unwrap();
        install_hermes_provider(&mut config, 131_072).unwrap();
        assert_eq!(config["model"]["provider"].as_str(), Some("custom"));
        assert_eq!(config["model"]["base_url"].as_str(), Some(API_BASE));
        assert_eq!(config["model"]["context_length"].as_u64(), Some(131_072));
        assert_eq!(config["model"]["temperature"].as_f64(), Some(0.7));
        assert_eq!(
            config["providers"]["opencore"]["extra_headers"]["x-user-setting"].as_str(),
            Some("retained-header")
        );
        assert_eq!(
            config["providers"]["other"]["api_key"].as_str(),
            Some("original-secret")
        );
        assert_eq!(
            config["custom_providers"][0]["api_key"].as_str(),
            Some("retained-secret")
        );
        assert_eq!(config["terminal"]["cwd"].as_str(), Some("C:/original"));
        assert_eq!(
            config["providers"]["opencore"]["extra_headers"]["x-opencore-client"].as_str(),
            Some("Hermes Agent")
        );
        assert!(install_hermes_provider(&mut config, 32_768).is_err());
    }

    #[test]
    fn profile_root_does_not_nest_profiles() {
        let root = PathBuf::from("root");
        assert_eq!(hermes_root_for(&root.join("profiles").join("coder")), root);
        assert_eq!(
            hermes_root_for(&PathBuf::from("other")),
            PathBuf::from("other")
        );
        assert_eq!(super::super::GATEWAY, "http://127.0.0.1:8812");
    }

    #[test]
    fn hermes_launch_guidance_names_the_custom_root_for_root_and_named_profiles() {
        let root = PathBuf::from("D:/Hermes homes/custom home");
        let guidance = hermes_launch_guidance(&root);
        assert!(guidance.contains(&root.display().to_string()));
        assert!(guidance.contains("hermes --profile opencore"));
        assert!(guidance.contains("HERMES_HOME"));
        assert!(guidance.contains("for that process"));
        for name in ["coder", "opencore"] {
            assert_eq!(
                hermes_launch_guidance(&root.join("profiles").join(name)),
                guidance
            );
        }
        assert!(!guidance.contains("HERMES_HOME="));
        assert!(!guidance.contains("export "));
    }

    struct ConfigFixture(PathBuf);
    impl ConfigFixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "opencore-connector-config-{}",
                uuid::Uuid::new_v4()
            ));
            std::fs::create_dir_all(&root).unwrap();
            Self(root)
        }
    }
    impl Drop for ConfigFixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn hermes_install_leaves_source_profile_and_data_unchanged_and_backs_up_reconfiguration() {
        let fixture = ConfigFixture::new();
        let source = fixture.0.join("config.yaml");
        let original = b"# User settings\nmodel:\n  default: original\n  provider: nous\nproviders:\n  other:\n    api_key: retained-secret\nterminal:\n  cwd: C:/original\n";
        std::fs::write(&source, original).unwrap();
        std::fs::write(fixture.0.join("state.db"), b"source-history").unwrap();
        std::fs::write(fixture.0.join(".env"), b"source-credentials").unwrap();
        assert!(configure_hermes(32_768, Some(&fixture.0)).is_err());
        assert!(!fixture.0.join("profiles").exists());
        let result = configure_hermes(65_536, Some(&fixture.0)).unwrap();
        assert!(result.contains(&hermes_launch_guidance(&fixture.0)));
        let managed = fixture.0.join("profiles").join("opencore");
        let destination = managed.join("config.yaml");
        let first = std::fs::read(&destination).unwrap();
        assert_eq!(std::fs::read(&source).unwrap(), original);
        assert_eq!(
            std::fs::read(fixture.0.join("state.db")).unwrap(),
            b"source-history"
        );
        assert_eq!(
            std::fs::read(fixture.0.join(".env")).unwrap(),
            b"source-credentials"
        );
        assert_eq!(std::fs::read_dir(&managed).unwrap().count(), 1);
        assert!(hermes_configured_in(Some(&fixture.0)));
        configure_hermes(131_072, Some(&fixture.0)).unwrap();
        configure_hermes(262_144, Some(&fixture.0)).unwrap();
        let backups = std::fs::read_dir(&managed)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .filter(|path| {
                path.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .starts_with("config.yaml.opencore-backup-")
            })
            .collect::<Vec<_>>();
        assert_eq!(backups.len(), 2);
        assert!(backups
            .iter()
            .any(|path| std::fs::read(path).unwrap() == first));
        let installed = parse_yaml(&std::fs::read_to_string(&destination).unwrap()).unwrap();
        assert_eq!(
            installed["providers"]["other"]["api_key"].as_str(),
            Some("retained-secret")
        );
        assert_eq!(std::fs::read(&source).unwrap(), original);
        assert!(!fixture.0.join("active_profile").exists());
    }

    #[test]
    fn hermes_provider_and_profile_name_collisions_are_preserved() {
        let mut provider = parse_yaml("providers:\n  opencore:\n    base_url: https://another-host/v1\n    api_key: original-secret\n").unwrap();
        let before = provider.clone();
        assert!(install_hermes_provider(&mut provider, 65_536).is_err());
        assert_eq!(provider, before);
        let fixture = ConfigFixture::new();
        std::fs::write(
            fixture.0.join("config.yaml"),
            "model:\n  default: original\n",
        )
        .unwrap();
        let destination = fixture
            .0
            .join("profiles")
            .join("opencore")
            .join("config.yaml");
        std::fs::create_dir_all(destination.parent().unwrap()).unwrap();
        let original = b"model:\n  default: personal-model\n  api_key: personal-secret\n";
        std::fs::write(&destination, original).unwrap();
        let error = configure_hermes(65_536, Some(&fixture.0)).unwrap_err();
        assert!(error.contains("another profile"));
        assert!(!error.contains("personal-secret"));
        assert_eq!(std::fs::read(&destination).unwrap(), original);
        assert_eq!(
            std::fs::read_dir(destination.parent().unwrap())
                .unwrap()
                .count(),
            1
        );
    }

    #[test]
    fn repeated_config_writes_keep_unique_byte_exact_backups() {
        let fixture = ConfigFixture::new();
        let path = fixture.0.join("opencode.jsonc");
        let original = b"// comments and provider secrets\n{\"provider\":{\"other\":{\"apiKey\":\"retained-secret\"}}}";
        std::fs::write(&path, original).unwrap();
        write_config(&path, b"first").unwrap();
        write_config(&path, b"second").unwrap();
        let backups = std::fs::read_dir(&fixture.0)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .filter(|path| {
                path.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .contains(".opencore-backup-")
            })
            .collect::<Vec<_>>();
        assert_eq!(backups.len(), 2);
        assert!(backups
            .iter()
            .any(|path| std::fs::read(path).unwrap() == original));
        assert!(backups
            .iter()
            .any(|path| std::fs::read(path).unwrap() == b"first"));
        assert_eq!(std::fs::read(&path).unwrap(), b"second");
    }
}
