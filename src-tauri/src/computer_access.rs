//! PC-wide computer access with a persistent Stop state and target identity checks.
use serde::{Deserialize, Serialize};

pub(crate) const SETTING: &str = "computer_access_v1";
static WRITES: std::sync::Mutex<()> = std::sync::Mutex::new(());
static CHANGES: std::sync::OnceLock<tokio::sync::watch::Sender<u64>> = std::sync::OnceLock::new();

pub(crate) fn require_settings_surface(label: &str) -> Result<(), String> {
    if label == "main" { Ok(()) }
    else { Err("Automation settings are available only in the OpenCore app interface".into()) }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct AppPermission { pub path: String, pub name: String, pub access: Access }

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub(crate) enum Access { Allow, Deny }

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Policy {
    pub enabled: bool,
    pub apps: Vec<AppPermission>,
    #[serde(default)]
    pub revision: u64,
}

impl Default for Policy {
    fn default() -> Self { Self { enabled: true, apps: Vec::new(), revision: 0 } }
}

impl Policy {
    pub(crate) fn require_enabled(&self) -> Result<(), String> {
        if self.enabled { Ok(()) }
        else { Err("Computer use is disabled. Enable it in Settings > Computer use.".into()) }
    }
    pub(crate) fn access(&self, path: &str) -> Option<Access> {
        if path.is_empty() { return None; }
        // Legacy per-app records remain readable, but no longer gate access.
        // Running computer use grants PC-wide access until the user stops it.
        Some(Access::Allow)
    }
    pub(crate) fn authorize(&self, path: &str) -> Result<(), String> {
        self.require_enabled()?;
        if path.is_empty() { Err("Could not identify this application's executable".into()) }
        else { Ok(()) }
    }
    fn validate(&self) -> Result<(), String> {
        if self.apps.len() > 256 { return Err("At most 256 app permissions are supported".into()); }
        let mut seen = std::collections::HashSet::new();
        for app in &self.apps {
            let path = identity(&app.path);
            let drive = path.as_bytes().first().is_some_and(u8::is_ascii_alphabetic)
                && path.as_bytes().get(1) == Some(&b':') && path.as_bytes().get(2) == Some(&b'\\');
            let unc = path.starts_with("\\\\") && path[2..].split('\\').filter(|part| !part.is_empty()).count() >= 3;
            let ambiguous = path.split('\\').any(|part| matches!(part, "." | "..") || part.ends_with(' ') || part.ends_with('.'));
            if (!drive && !unc) || ambiguous || app.path.len() > 32768 || app.path.contains('\0')
                || app.name.len() > 256 || !seen.insert(path) {
                return Err("App permissions need unique absolute executable paths".into());
            }
        }
        Ok(())
    }
}

pub(crate) fn identity(path: &str) -> String {
    let path = path.replace('/', "\\").to_lowercase();
    if let Some(unc) = path.strip_prefix("\\\\?\\unc\\") { format!("\\\\{unc}") }
    else { path.strip_prefix("\\\\?\\").unwrap_or(&path).to_string() }
}

pub(crate) fn subscribe() -> tokio::sync::watch::Receiver<u64> {
    CHANGES.get_or_init(|| tokio::sync::watch::channel(0).0).subscribe()
}
fn changed() {
    CHANGES.get_or_init(|| tokio::sync::watch::channel(0).0).send_modify(|version| *version = version.wrapping_add(1));
}

pub(crate) fn load(store: &crate::store::EventStore) -> Result<Policy, String> {
    let policy: Policy = match store.get_setting(SETTING)? {
        None => return Ok(Policy::default()),
        Some(value) => serde_json::from_str(&value)
            .map_err(|_| "Computer permissions could not be read; access remains blocked".to_string())?,
    };
    policy.validate().map_err(|_| "Computer permissions are invalid; access remains blocked".to_string())?;
    Ok(policy)
}

fn persist(store: &crate::store::EventStore, policy: &mut Policy, previous: &Policy) -> Result<(), String> {
    policy.validate()?;
    policy.revision = previous.revision.checked_add(1).ok_or("Computer permission revision is exhausted")?;
    store.set_setting(SETTING, &serde_json::to_string(&policy).map_err(|e| e.to_string())?)?;
    changed();
    Ok(())
}

pub(crate) fn save(store: &crate::store::EventStore, mut policy: Policy) -> Result<Policy, String> {
    let _lock = WRITES.lock().map_err(|e| e.to_string())?;
    let previous = load(store)?;
    if previous.revision != policy.revision {
        // A Stop click may race an app grant. Honor the stop while retaining
        // the newest app grants/denials, rather than restoring a stale list.
        if !policy.enabled {
            let mut stopped = previous.clone(); stopped.enabled = false;
            persist(store, &mut stopped, &previous)?;
            return Ok(stopped);
        }
        return Err("Computer permissions changed while you were editing. Refresh the settings and try again.".into());
    }
    persist(store, &mut policy, &previous)?;
    Ok(policy)
}

pub(crate) fn stop(store: &crate::store::EventStore) -> Result<Policy, String> {
    let _lock = WRITES.lock().map_err(|e| e.to_string())?;
    let previous = load(store)?;
    let mut stopped = previous.clone(); stopped.enabled = false;
    persist(store, &mut stopped, &previous)?;
    Ok(stopped)
}

pub(crate) fn grant(store: &crate::store::EventStore, app: &WindowIdentity) -> Result<(), String> {
    let _lock = WRITES.lock().map_err(|e| e.to_string())?;
    let previous = load(store)?;
    previous.require_enabled()?;
    if previous.access(&app.path) == Some(Access::Deny) {
        return Err("Application access was denied while approval was pending".into());
    }
    if previous.access(&app.path).is_some() { return Ok(()); }
    let mut policy = previous.clone();
    policy.apps.push(AppPermission { path: app.path.clone(), name: app.name.clone(), access: Access::Allow });
    persist(store, &mut policy, &previous)
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WindowIdentity { pub window_id: i64, pub path: String, pub name: String, pub pid: u32 }

#[cfg(windows)]
pub(crate) fn window_identity(window_id: i64) -> Result<WindowIdentity, String> {
    use windows::Win32::Foundation::{CloseHandle, HWND};
    use windows::Win32::System::Threading::{OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_NAME_WIN32};
    use windows::Win32::UI::WindowsAndMessaging::{GetWindowThreadProcessId, IsWindow};
    if window_id == 0 {
        return Ok(WindowIdentity { window_id: 0, path: "Windows desktop".into(), name: "Windows desktop".into(), pid: 0 });
    }
    if window_id < 0 { return Err("Choose a valid application window or the whole desktop".into()); }
    unsafe {
        let hwnd = HWND(window_id as *mut std::ffi::c_void);
        if !IsWindow(hwnd).as_bool() { return Err("The selected window is no longer available".into()); }
        let mut pid = 0;
        GetWindowThreadProcessId(hwnd, Some(&mut pid));
        if pid == 0 { return Err("Could not identify this app; access is blocked".into()); }
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
            .map_err(|_| "Could not read this app's executable identity; access is blocked")?;
        let mut buffer = vec![0u16; 32768];
        let mut length = buffer.len() as u32;
        let result = QueryFullProcessImageNameW(process, PROCESS_NAME_WIN32, windows::core::PWSTR(buffer.as_mut_ptr()), &mut length);
        let _ = CloseHandle(process);
        result.map_err(|_| "Could not read this app's executable identity; access is blocked")?;
        let path = String::from_utf16(&buffer[..length as usize]).map_err(|e| e.to_string())?;
        let name = path.rsplit('\\').next().unwrap_or(&path).to_string();
        Ok(WindowIdentity { window_id, path, name, pid })
    }
}
#[cfg(not(windows))]
pub(crate) fn window_identity(_window_id: i64) -> Result<WindowIdentity, String> { Err("Computer use requires Windows".into()) }

pub(crate) fn check_window(store: &crate::store::EventStore, window_id: i64) -> Result<WindowIdentity, String> {
    let policy = load(store)?;
    policy.require_enabled()?;
    let app = window_identity(window_id)?;
    policy.authorize(&app.path)?;
    Ok(app)
}

pub(crate) fn recheck_window(store: &crate::store::EventStore, expected: &WindowIdentity) -> Result<(), String> {
    let current = check_window(store, expected.window_id)?;
    if current.pid != expected.pid || identity(&current.path) != identity(&expected.path) {
        return Err("The selected application's window changed before dispatch. No input was sent.".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn permitted() -> Policy {
        Policy { enabled: true, apps: vec![AppPermission { path: "C:\\Apps\\Notes.exe".into(), name: "Notes".into(), access: Access::Allow }], ..Policy::default() }
    }
    fn store() -> (crate::store::EventStore, std::path::PathBuf) {
        let path = std::env::temp_dir().join(format!("opencore-access-{}.sqlite3", uuid::Uuid::new_v4()));
        (crate::store::EventStore::open(&path).unwrap(), path)
    }
    #[test]
    fn default_disabled_rejects_even_previously_allowed_apps() {
        let mut policy = permitted(); policy.enabled = false;
        assert!(policy.authorize("C:\\Apps\\Notes.exe").unwrap_err().contains("disabled"));
    }
    #[test]
    fn running_computer_use_accepts_any_identifiable_app_without_a_grant() {
        let policy = permitted();
        assert!(policy.authorize("c:/apps/NOTES.exe").is_ok());
        assert!(policy.authorize("\\\\?\\C:\\Apps\\Notes.exe").is_ok());
        assert!(policy.authorize("D:\\Other\\Notes.exe").is_ok());
        assert!(Policy::default().authorize("C:\\New App\\App.exe").is_ok());
        assert!(policy.authorize("").is_err());
    }
    #[test]
    fn legacy_app_denials_do_not_gate_pc_wide_access() {
        let mut policy = permitted();
        policy.apps.push(AppPermission { path: "c:/apps/notes.exe".into(), name: "another title".into(), access: Access::Deny });
        assert!(policy.authorize("C:\\Apps\\Notes.exe").is_ok());
    }
    #[test]
    fn saved_permissions_survive_restart_and_corruption_blocks_access() {
        let (store, path) = store();
        assert_eq!(load(&store).unwrap(), Policy::default());
        let saved = save(&store, permitted()).unwrap();
        drop(store);
        let reopened = crate::store::EventStore::open(&path).unwrap();
        assert_eq!(load(&reopened).unwrap(), saved);
        reopened.set_setting(SETTING, "broken-json").unwrap();
        assert!(load(&reopened).is_err());
        reopened.set_setting(SETTING, r#"{"enabled":true,"apps":[{"path":"Notes.exe","name":"Notes","access":"allow"}]}"#).unwrap();
        assert!(load(&reopened).is_err());
        drop(reopened); std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn stale_settings_cannot_undo_an_emergency_stop_or_new_denial() {
        let (store, path) = store();
        let stale = save(&store, permitted()).unwrap();
        let mut stopped = stale.clone(); stopped.enabled = false;
        let saved = save(&store, stopped).unwrap();
        assert!(save(&store, stale).unwrap_err().contains("changed"));
        assert_eq!(load(&store).unwrap(), saved);
        drop(store); std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn pending_grant_cannot_reenable_stopped_access() {
        let (store, path) = store();
        let app = WindowIdentity { window_id: 12, path: "C:\\Apps\\Notes.exe".into(), name: "Notes".into(), pid: 42 };
        stop(&store).unwrap();
        assert!(grant(&store, &app).unwrap_err().contains("disabled"));
        let mut policy = load(&store).unwrap(); policy.enabled = true; policy.apps = permitted().apps; policy.apps[0].access = Access::Deny;
        let saved = save(&store, policy).unwrap();
        assert!(grant(&store, &app).is_ok());
        assert_eq!(load(&store).unwrap(), saved);
        drop(store); std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn relative_ambiguous_and_duplicate_paths_cannot_be_saved() {
        for path in ["Notes.exe", "C:\\Apps\\..\\Notes.exe", "C:\\Apps\\Notes.exe "] {
            let mut policy = permitted(); policy.apps[0].path = path.into();
            assert!(policy.validate().is_err());
        }
        let mut policy = permitted(); policy.apps.push(policy.apps[0].clone());
        assert!(policy.validate().is_err());
    }
    #[test]
    fn embedded_pages_cannot_change_permissions_or_read_pairing_secrets() {
        assert!(require_settings_surface("main").is_ok());
        assert!(require_settings_surface("opencore-native-browser").is_err());
        assert!(require_settings_surface("desktop-activity").is_err());
    }
    #[cfg(windows)]
    #[test]
    fn a_real_hidden_window_uses_process_identity_and_rechecks_revocation() {
        use windows::Win32::UI::WindowsAndMessaging::{CreateWindowExW, DestroyWindow, WINDOW_EX_STYLE, WINDOW_STYLE};
        let hwnd = unsafe { CreateWindowExW(WINDOW_EX_STYLE(0), windows::core::w!("STATIC"), windows::core::w!("A misleading title"), WINDOW_STYLE(0), 0, 0, 1, 1, None, None, None, None) }.unwrap();
        struct Fixture(windows::Win32::Foundation::HWND);
        impl Drop for Fixture { fn drop(&mut self) { unsafe { let _ = DestroyWindow(self.0); } } }
        let fixture = Fixture(hwnd);
        let target = window_identity(hwnd.0 as i64).unwrap();
        assert_eq!(target.pid, std::process::id());
        let (store, path) = store();
        let empty = save(&store, Policy { enabled: true, ..Policy::default() }).unwrap();
        assert!(check_window(&store, target.window_id).is_ok());
        assert!(recheck_window(&store, &target).is_ok());
        let mut reused = target.clone(); reused.pid = reused.pid.wrapping_add(1);
        assert!(recheck_window(&store, &reused).unwrap_err().contains("changed"));
        stop(&store).unwrap();
        assert!(recheck_window(&store, &target).unwrap_err().contains("disabled"));
        assert!(save(&store, empty).is_err());
        drop(fixture); drop(store); std::fs::remove_file(path).unwrap();
    }
}
