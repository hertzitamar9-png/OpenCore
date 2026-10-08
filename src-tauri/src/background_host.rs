//! Per-user tray lifecycle. One Tauri core continues owning jobs when its UI is hidden.
use crate::store::EventStore;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::Path, sync::Mutex};
use tauri::{Manager, menu::{Menu, MenuItem}, tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent}};

const KEY: &str = "background-agent-v1";
const TRAY_ID: &str = "opencore-background";
const LOGIN_KEY: &str = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run";
const LOGIN_VALUE: &str = "OpenCoreBackground";
static SAVE_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", default, deny_unknown_fields)]
pub struct Configuration {
    pub enabled: bool,
    pub start_at_login: bool,
    pub revision: u64,
}
impl Default for Configuration {
    fn default() -> Self { Self { enabled: true, start_at_login: cfg!(windows), revision: 0 } }
}
/// Defaults apply once. Explicitly saved opt-outs are never overwritten.
pub fn initialize(store: &EventStore, executable: &Path) -> Result<(), String> {
    let saved = store.get_setting(KEY)?;
    let configuration = configuration(store)?;
    if saved.is_none() || (configuration.start_at_login && !registered(executable)) {
        save(store, executable, configuration)?;
    }
    Ok(())
}
pub fn configuration(store: &EventStore) -> Result<Configuration, String> {
    store.get_setting(KEY)?.map(|value| serde_json::from_str(&value).map_err(|error| format!("Invalid background agent configuration: {error}"))).unwrap_or(Ok(Configuration::default()))
}
pub fn hidden_start(arguments: &[String]) -> bool { arguments.iter().any(|argument| argument == "--background") }
pub fn should_hide(configuration: &Configuration, tray_available: bool, updating: bool) -> bool {
    configuration.enabled && tray_available && !updating
}
fn login_command(executable: &Path) -> Result<String, String> {
    let path = executable.to_str().ok_or("The OpenCore executable path is not valid Unicode")?;
    if !executable.is_absolute() || path.contains(['\0', '\n', '\r', '"']) { return Err("Invalid OpenCore startup executable path".into()); }
    let command = format!("\"{path}\" --background");
    if command.encode_utf16().count() > 260 { return Err("The OpenCore startup command exceeds Windows' 260-character limit".into()); }
    Ok(command)
}
#[cfg(windows)]
fn registry(arguments: &[&str]) -> Result<std::process::Output, String> {
    use std::os::windows::process::CommandExt;
    std::process::Command::new("reg.exe").args(arguments).creation_flags(0x08000000).output().map_err(|error| format!("Could not access Windows login startup: {error}"))
}
fn registered(executable: &Path) -> bool {
    #[cfg(windows)] {
        let Ok(expected) = login_command(executable) else { return false; };
        return registry(&["query", LOGIN_KEY, "/v", LOGIN_VALUE]).is_ok_and(|output| output.status.success() && String::from_utf8_lossy(&output.stdout).to_lowercase().contains(&expected.to_lowercase()));
    }
    #[cfg(not(windows))] { let _ = executable; false }
}
fn set_login(executable: &Path, enabled: bool) -> Result<(), String> {
    #[cfg(windows)] {
        let command = login_command(executable)?;
        let output = if enabled { registry(&["add", LOGIN_KEY, "/v", LOGIN_VALUE, "/t", "REG_SZ", "/d", &command, "/f"])? }
        else {
            // A missing value is already disabled; never remove another startup value.
            if !registry(&["query", LOGIN_KEY, "/v", LOGIN_VALUE])?.status.success() { return Ok(()); }
            registry(&["delete", LOGIN_KEY, "/v", LOGIN_VALUE, "/f"])?
        };
        if !output.status.success() { return Err(format!("Windows login startup update failed: {}", String::from_utf8_lossy(&output.stderr).trim())); }
        if enabled && !registered(executable) { return Err("Windows login startup could not be verified after registration".into()); }
        Ok(())
    }
    #[cfg(not(windows))] { let _ = executable; if enabled { Err("Login startup is currently supported on Windows".into()) } else { Ok(()) } }
}
pub fn save(store: &EventStore, executable: &Path, mut next: Configuration) -> Result<Configuration, String> {
    let _guard = SAVE_LOCK.lock().map_err(|_| "Background settings lock is unavailable")?;
    let previous = configuration(store)?;
    if next.revision != previous.revision { return Err("Background settings changed. Refresh before saving again.".into()); }
    if next.start_at_login && !next.enabled { return Err("Enable the background agent before enabling login startup".into()); }
    if previous.start_at_login != next.start_at_login || (next.start_at_login && !registered(executable)) { set_login(executable, next.start_at_login)?; }
    next.revision = previous.revision.checked_add(1).ok_or("Background settings revision overflow")?;
    if let Err(error) = store.set_setting(KEY, &serde_json::to_string(&next).map_err(|error| error.to_string())?) {
        let rollback = set_login(executable, previous.start_at_login);
        return Err(format!("Background settings were not saved: {error}{}", rollback.err().map(|error| format!("; login rollback also failed: {error}")).unwrap_or_default()));
    }
    Ok(next)
}
pub fn status(app: &tauri::AppHandle, store: &EventStore) -> Result<Value, String> {
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    Ok(json!({"configuration":configuration(store)?,"trayAvailable":app.tray_by_id(TRAY_ID).is_some(),"loginStartupSupported":cfg!(windows),"loginRegistered":registered(&executable),"windowVisible":app.get_webview_window("main").is_some_and(|window|window.is_visible().unwrap_or(false))}))
}
pub fn show(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") { let _=window.unminimize(); let _=window.show(); let _=window.set_focus(); }
}
pub fn attach(app: &tauri::AppHandle) -> Result<(), String> {
    let open = MenuItem::with_id(app, "background-open", "Open OpenCore", true, None::<&str>).map_err(|error| error.to_string())?;
    let quit = MenuItem::with_id(app, "background-quit", "Quit OpenCore", true, None::<&str>).map_err(|error| error.to_string())?;
    let menu = Menu::with_items(app, &[&open, &quit]).map_err(|error| error.to_string())?;
    let icon = app.default_window_icon().ok_or("The OpenCore tray icon is unavailable")?.clone();
    TrayIconBuilder::with_id(TRAY_ID).icon(icon).menu(&menu).tooltip("OpenCore background agent").show_menu_on_left_click(false)
        .on_menu_event(|app,event|match event.id.as_ref(){"background-open"=>show(app),"background-quit"=>app.exit(0),_=>{}})
        .on_tray_icon_event(|tray,event|if matches!(event,TrayIconEvent::Click{button:MouseButton::Left,button_state:MouseButtonState::Up,..}){show(tray.app_handle());})
        .build(app).map_err(|error|error.to_string())?;
    Ok(())
}
pub fn can_hide(app: &tauri::AppHandle, store: &EventStore, updating: bool) -> bool {
    configuration(store).is_ok_and(|configuration| should_hide(&configuration, app.tray_by_id(TRAY_ID).is_some(), updating))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn closing_only_hides_when_enabled_and_recoverable_and_not_updating() {
        let enabled=Configuration{enabled:true,..Default::default()};
        assert!(should_hide(&enabled,true,false));
        assert!(!should_hide(&enabled,false,false));
        assert!(!should_hide(&enabled,true,true));
        assert!(!should_hide(&Configuration{enabled:false,start_at_login:false,revision:0},true,false));
    }
    #[test]
    fn background_start_requires_the_exact_cli_flag() {
        assert!(hidden_start(&["OpenCore.exe".into(),"--background".into()]));
        assert!(!hidden_start(&["OpenCore.exe".into(),"file--background.html".into()]));
    }
    #[test]
    fn login_command_quotes_spaces_and_rejects_injected_arguments() {
        let path=std::env::temp_dir().join("Open Core").join("OpenCore.exe");
        assert_eq!(login_command(&path).unwrap(),format!("\"{}\" --background",path.display()));
        assert!(login_command(Path::new("relative.exe")).is_err());
        assert!(login_command(&std::env::temp_dir().join("bad\" --other.exe")).is_err());
    }
    #[test]
    fn persisted_configuration_rejects_stale_writes_without_changing_settings() {
        let root=std::env::temp_dir().join(format!("opencore-background-settings-{}",uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let store=EventStore::open(&root.join("events.sqlite3")).unwrap();
        let first=save(&store,&root.join("OpenCore.exe"),Configuration{enabled:true,start_at_login:false,revision:0}).unwrap();
        assert_eq!(first.revision,1);
        assert_eq!(configuration(&store).unwrap(),first);
        assert!(save(&store,&root.join("OpenCore.exe"),Configuration::default()).is_err());
        assert_eq!(configuration(&store).unwrap(),first);
        drop(store); let _=std::fs::remove_dir_all(root);
    }
    #[test]
    fn initialization_preserves_an_explicit_background_opt_out() {
        let root=std::env::temp_dir().join(format!("background-opt-out-{}",uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let store=EventStore::open(&root.join("events.sqlite3")).unwrap();
        let disabled=Configuration{enabled:false,start_at_login:false,revision:4};
        store.set_setting(KEY,&serde_json::to_string(&disabled).unwrap()).unwrap();
        initialize(&store,&root.join("OpenCore.exe")).unwrap();
        assert_eq!(configuration(&store).unwrap(),disabled);
        drop(store); let _=std::fs::remove_dir_all(root);
    }
}
