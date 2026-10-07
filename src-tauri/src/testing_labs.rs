//! Structured controls for existing PC VMs and Android SDK devices.
//! This adapter never downloads an operating system or claims a screenshot is a test.
use crate::store::EventStore;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::process::Command;

const KEY: &str = "testing-lab-profiles-v1";
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TestingLabProfile {
    pub id: String,
    pub label: String,
    pub kind: String,
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub executable: String,
    #[serde(default)]
    pub vm_name: Option<String>,
    #[serde(default)]
    pub device_serial: Option<String>,
    #[serde(default)]
    pub avd_name: Option<String>,
    #[serde(default)]
    pub emulator_executable: Option<String>,
    #[serde(default)]
    pub sdk_root: Option<String>,
    #[serde(default)]
    pub avd_home: Option<String>,
    #[serde(default)]
    pub android_user_home: Option<String>,
    #[serde(default)]
    pub emulator_port: Option<u16>,
    #[serde(default)]
    pub guest_user: Option<String>,
    #[serde(default)]
    pub password_env: Option<String>,
}
pub fn profiles(store: &EventStore) -> Result<Vec<TestingLabProfile>, String> {
    store
        .get_setting(KEY)?
        .map(|v| serde_json::from_str(&v).map_err(|e| format!("Invalid testing profile: {e}")))
        .unwrap_or(Ok(vec![]))
}
fn validate(profiles: &[TestingLabProfile]) -> Result<(), String> {
    if profiles.len() > 32 {
        return Err("At most 32 testing profiles are supported".into());
    }
    let mut ids = HashSet::new();
    for p in profiles {
        if p.id.is_empty()
            || p.id.len() > 80
            || !p
                .id
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
            || !ids.insert(&p.id)
        {
            return Err(
                "Testing profile IDs must be unique letters, digits, dashes or underscores".into(),
            );
        }
        if p.label.trim().is_empty()
            || p.label.len() > 160
            || !matches!(p.kind.as_str(), "android" | "virtualbox")
        {
            return Err("A testing profile needs a label and android or virtualbox kind".into());
        }
        for value in [
            &p.executable,
            p.vm_name.as_deref().unwrap_or(""),
            p.device_serial.as_deref().unwrap_or(""),
            p.avd_name.as_deref().unwrap_or(""),
            p.emulator_executable.as_deref().unwrap_or(""),
            p.sdk_root.as_deref().unwrap_or(""),
            p.avd_home.as_deref().unwrap_or(""),
            p.android_user_home.as_deref().unwrap_or(""),
            p.guest_user.as_deref().unwrap_or(""),
            p.password_env.as_deref().unwrap_or(""),
        ] {
            if value.len() > 4096 || value.contains(['\0', '\n', '\r']) {
                return Err("Testing profile values must be bounded single lines".into());
            }
        }
        if p.kind == "virtualbox" && p.vm_name.as_deref().unwrap_or("").trim().is_empty() {
            return Err("PC profiles require an existing VirtualBox VM name".into());
        }
        if p.emulator_port.is_some_and(|port|!(5554..=5682).contains(&port) || port%2!=0) {
            return Err("Android emulator ports must be even numbers from 5554 to 5682".into());
        }
        if let Some(name) = &p.password_env {
            if name.is_empty() || !name.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
                return Err("Password environment variable names must contain letters, digits or underscores".into());
            }
        }
    }
    Ok(())
}
pub fn save_profiles(
    store: &EventStore,
    value: Vec<TestingLabProfile>,
) -> Result<Vec<TestingLabProfile>, String> {
    validate(&value)?;
    store.set_setting(
        KEY,
        &serde_json::to_string(&value).map_err(|e| e.to_string())?,
    )?;
    Ok(value)
}
/// Add only a successfully provisioned environment; preserve unrelated saved
/// profiles and the user's label/enabled choice when repairing a managed AVD.
pub fn save_setup_profile(store: &EventStore, receipt: &Value) -> Result<(), String> {
    let target=receipt["targetId"].as_str().unwrap_or("");
    let required=|key:&str|->Result<String,String>{receipt[key].as_str().filter(|v|!v.is_empty()).map(str::to_owned).ok_or_else(||format!("Verified testing setup is missing {key}"))};
    let mut next:TestingLabProfile=match target {
        "testing-android" if receipt["bootVerified"]==true=>TestingLabProfile{
            id:"opencore-managed-android-api36".into(),label:"OpenCore Android API 36".into(),kind:"android".into(),enabled:true,
            executable:required("executable")?,emulator_executable:Some(required("emulatorExecutable")?),
            sdk_root:Some(required("sdkRoot")?),avd_home:Some(required("avdHome")?),android_user_home:Some(required("androidUserHome")?),
            emulator_port:Some(receipt["emulatorPort"].as_u64().and_then(|port|u16::try_from(port).ok()).ok_or("Verified Android setup is missing its emulator port")?),
            device_serial:Some(required("deviceSerial")?),avd_name:Some(required("avdName")?),vm_name:None,guest_user:None,password_env:None,
        },
        "testing-pc-vm" if receipt["guestVerified"]==true=>TestingLabProfile{
            id:format!("opencore-vm-{}",required("vmId")?),label:required("vmName")?,kind:"virtualbox".into(),enabled:true,
            executable:required("executable")?,vm_name:Some(required("vmId")?),guest_user:Some(required("guestUser")?),password_env:Some(required("passwordEnv")?),
            emulator_executable:None,sdk_root:None,avd_home:None,android_user_home:None,emulator_port:None,device_serial:None,avd_name:None,
        },
        "testing-android"|"testing-pc-vm"=>return Err("A testing profile requires a verified guest boot or command".into()),
        _=>return Ok(()),
    };
    let mut saved=profiles(store)?;
    if let Some(existing)=saved.iter_mut().find(|profile|profile.id==next.id) {
        next.label=existing.label.clone();next.enabled=existing.enabled;*existing=next;
    } else {saved.push(next);}
    save_profiles(store,saved).map(|_|())
}
pub fn tool_spec() -> Value {
    json!({"type":"function","function":{"name":"testing_lab","description":"Control an existing configured VirtualBox PC or Android device/emulator. Status/list discover profiles and SDK readiness; configure saves a validated profiles array through the same persistence as Settings. Launch, install an APK, inspect UI hierarchy, screenshot, tap, type, key, or execute a guest application. Screenshots are visual evidence, not automatically a passing test. No VM images are downloaded. Follow the selected approval policy and report host/device changes.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["list","configure","status","start","stop","inspect","screenshot","install_app","launch_app","tap","key","text"]},"profiles":{"type":"array","maxItems":32,"items":{"type":"object","properties":{"id":{"type":"string"},"label":{"type":"string"},"kind":{"type":"string","enum":["android","virtualbox"]},"enabled":{"type":"boolean"},"executable":{"type":"string"},"vmName":{"type":"string"},"deviceSerial":{"type":"string"},"avdName":{"type":"string"},"emulatorExecutable":{"type":"string"},"guestUser":{"type":"string"},"passwordEnv":{"type":"string"}},"required":["id","label","kind"]}},"profileId":{"type":"string"},"path":{"type":"string"},"args":{"type":"array","items":{"type":"string"}},"x":{"type":"integer"},"y":{"type":"integer"},"text":{"type":"string"},"key":{"type":"string"}},"required":["action"]}}})
}
fn command(executable: &str, args: &[String]) -> Command {
    let mut c = Command::new(executable);
    c.args(args).kill_on_drop(true);
    #[cfg(windows)]
    c.creation_flags(0x08000000);
    c
}
async fn run(executable: &str, args: &[String]) -> Result<std::process::Output, String> {
    tokio::time::timeout(Duration::from_secs(90),command(executable,args).output()).await
        .map_err(|_|"Testing command exceeded 90 seconds; its process was stopped".to_string())?
        .map_err(|e|format!("Cannot start {executable}: {e}. Install/configure the SDK or VM application in Testing labs."))
}
fn output(o: std::process::Output) -> Result<Value, String> {
    let stdout = String::from_utf8_lossy(&o.stdout)
        .chars()
        .take(16000)
        .collect::<String>();
    let stderr = String::from_utf8_lossy(&o.stderr)
        .chars()
        .take(4000)
        .collect::<String>();
    if !o.status.success() {
        return Err(format!(
            "Testing command failed ({}): {} {}",
            o.status, stdout, stderr
        ));
    }
    Ok(json!({"stdout":stdout,"stderr":stderr,"exitCode":o.status.code(),"verified":false}))
}
fn bounded_text(args: &Value, key: &str) -> Result<String, String> {
    let s = args[key]
        .as_str()
        .ok_or_else(|| format!("{key} is required"))?;
    if s.is_empty() || s.len() > 4096 || s.contains('\0') {
        return Err(format!("{key} must contain 1-4096 characters without NUL"));
    }
    Ok(s.into())
}
fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}
fn adb_base(p: &TestingLabProfile) -> Vec<String> {
    p.device_serial
        .as_ref()
        .filter(|s| !s.is_empty())
        .map(|s| vec!["-s".into(), s.clone()])
        .unwrap_or_default()
}
fn local_file(value: &str, extension: Option<&str>) -> Result<PathBuf, String> {
    let p = PathBuf::from(value);
    if !p.is_absolute() || !p.is_file() {
        return Err("Use an existing absolute file path".into());
    }
    if let Some(extension) = extension {
        if !p
            .extension()
            .is_some_and(|v| v.to_string_lossy().eq_ignore_ascii_case(extension))
        {
            return Err(format!("The file must be a .{extension}"));
        }
    }
    std::fs::canonicalize(p).map_err(|e| e.to_string())
}
pub async fn execute(store: &EventStore, data: &Path, args: &Value) -> Result<Value, String> {
    let list = profiles(store)?;
    let action = args["action"].as_str().unwrap_or("status");
    if action == "list" {
        return serde_json::to_value(list).map_err(|e| e.to_string());
    }
    if action == "configure" {
        let next: Vec<TestingLabProfile> = serde_json::from_value(args["profiles"].clone())
            .map_err(|e| format!("Invalid testing profiles: {e}"))?;
        let changed = serde_json::to_value(&list).map_err(|e| e.to_string())?
            != serde_json::to_value(&next).map_err(|e| e.to_string())?;
        let profiles = save_profiles(store, next)?;
        return Ok(
            json!({"changed":changed,"profiles":profiles,"verified":false,"note":"Profiles saved; test status before claiming the device is connected."}),
        );
    }
    let id = args["profileId"]
        .as_str()
        .ok_or("Choose a testing profileId from testing_lab list")?;
    let p = list
        .iter()
        .find(|p| p.id == id)
        .ok_or("Testing profile was not found")?;
    if !p.enabled {
        return Err("Enable this testing profile in Settings before using it".into());
    }
    let exe = if p.executable.trim().is_empty() {
        if p.kind == "android" {
            "adb"
        } else {
            "VBoxManage"
        }
    } else {
        &p.executable
    };
    let mut cmd = if p.kind == "android" {
        adb_base(p)
    } else {
        vec![]
    };
    let screenshot_dir = data.join("testing-labs").join("screenshots");
    let screenshot = screenshot_dir.join(format!("{}-{}.png", p.id, uuid::Uuid::new_v4()));
    let mut password_file: Option<PasswordFile> = None;
    if p.kind == "android" {
        match action {
            "status" => cmd.extend(["devices".into(), "-l".into()]),
            "start" => {
                let avd=p.avd_name.as_deref().filter(|s|!s.is_empty()).ok_or("To launch an emulator, configure an existing avdName. Real devices are connected through ADB status.")?;
                let executable = p
                    .emulator_executable
                    .as_deref()
                    .filter(|s| !s.is_empty())
                    .unwrap_or("emulator");
                // The Android emulator is intentionally long lived. Do not kill it at tool completion.
                let mut c = Command::new(executable);
                c.args(["-avd", avd]);
                if let Some(port)=p.emulator_port {
                    // Never redirect a managed serial to an unrelated emulator.
                    for number in [port,port+1] {std::net::TcpListener::bind(("127.0.0.1",number)).map_err(|_|format!("Android emulator port {number} is already in use. Check this profile's status or stop its existing emulator."))?;}
                    c.args(["-port",&port.to_string()]);
                }
                if let Some(path)=&p.sdk_root {c.env("ANDROID_SDK_ROOT",path).env("ANDROID_HOME",path);}
                if let Some(path)=&p.avd_home {c.env("ANDROID_AVD_HOME",path);}
                if let Some(path)=&p.android_user_home {c.env("ANDROID_USER_HOME",path);}
                c
                    .stdin(std::process::Stdio::null())
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null());
                #[cfg(windows)]
                c.creation_flags(0x08000000);
                let mut child = c
                    .spawn()
                    .map_err(|e| format!("Cannot launch the configured Android emulator: {e}"))?;
                let pid = child.id();
                tokio::spawn(async move {
                    let _ = child.wait().await;
                });
                return Ok(
                    json!({"profileId":id,"action":action,"pid":pid,"status":"starting","change":"Started Android emulator","verified":false}),
                );
            }
            "stop" => cmd.extend(["emu".into(), "kill".into()]),
            "screenshot" => cmd.extend(["exec-out".into(), "screencap".into(), "-p".into()]),
            "install_app" => cmd.extend([
                "install".into(),
                "-r".into(),
                local_file(&bounded_text(args, "path")?, Some("apk"))?
                    .to_string_lossy()
                    .into(),
            ]),
            "launch_app" => {
                let target = bounded_text(args, "path")?;
                if !target
                    .bytes()
                    .all(|v| v.is_ascii_alphanumeric() || b"._/".contains(&v))
                {
                    return Err("Android app paths must be package/activity identifiers".into());
                }
                cmd.extend([
                    "shell".into(),
                    "am".into(),
                    "start".into(),
                    "-n".into(),
                    target,
                ]);
            }
            "tap" => {
                let x = args["x"]
                    .as_u64()
                    .filter(|v| *v <= 65535)
                    .ok_or("x must be a positive screen coordinate")?;
                let y = args["y"]
                    .as_u64()
                    .filter(|v| *v <= 65535)
                    .ok_or("y must be a positive screen coordinate")?;
                cmd.extend([
                    "shell".into(),
                    "input".into(),
                    "tap".into(),
                    x.to_string(),
                    y.to_string(),
                ]);
            }
            "key" => {
                let key = bounded_text(args, "key")?;
                if !key.bytes().all(|v| v.is_ascii_alphanumeric() || v == b'_') {
                    return Err("key must be an Android keycode number or KEYCODE_NAME".into());
                }
                cmd.extend(["shell".into(), "input".into(), "keyevent".into(), key]);
            }
            "text" => {
                let text = bounded_text(args, "text")?;
                cmd.extend([
                    "shell".into(),
                    format!("input text {}", shell_quote(&text.replace(' ', "%s"))),
                ]);
            }
            "inspect" => cmd.extend(["shell".into(), "uiautomator dump /dev/tty".into()]),
            _ => return Err("Unsupported Android testing action".into()),
        }
    } else {
        let name = p
            .vm_name
            .as_deref()
            .ok_or("vmName is required")?
            .to_string();
        match action {
            "status"|"inspect"=>cmd.extend(["showvminfo".into(),name,"--machinereadable".into()]),
            "start"=>cmd.extend(["startvm".into(),name,"--type".into(),"headless".into()]),
            "stop"=>cmd.extend(["controlvm".into(),name,"acpipowerbutton".into()]),
            "screenshot"=>{std::fs::create_dir_all(&screenshot_dir).map_err(|e|e.to_string())?;cmd.extend(["controlvm".into(),name,"screenshotpng".into(),screenshot.to_string_lossy().into()]);}
            "launch_app"=>{
                let user=p.guest_user.as_deref().filter(|v|!v.is_empty()).ok_or("Guest control needs guestUser and VirtualBox Guest Additions")?;
                cmd.extend(["guestcontrol".into(),name,"run".into(),"--username".into(),user.into()]);
                if let Some(env)=&p.password_env {
                    let secret=std::env::var(env).map_err(|_|format!("Guest password environment variable {env} is not available"))?;
                    if secret.contains(['\0','\n','\r']){return Err("Guest passwords must be a single line".into());}
                    let guard=PasswordFile::new(data,secret.as_bytes())?;
                    cmd.extend(["--passwordfile".into(),guard.0.to_string_lossy().into()]);password_file=Some(guard);
                }
                let path=bounded_text(args,"path")?;
                cmd.extend(["--exe".into(),path.clone(),"--wait-stdout".into(),"--wait-stderr".into(),"--timeout".into(),"80000".into(),"--".into(),path]);
                let values=args["args"].as_array().cloned().unwrap_or_default();
                if values.len()>128{return Err("Too many guest application arguments".into());}
                for arg in values {let v=arg.as_str().filter(|v|v.len()<=4096&&!v.contains('\0')).ok_or("Invalid guest argument")?;cmd.push(v.into());}
            }
            "key"=>{
                let codes=bounded_text(args,"key")?;
                if !codes.split_whitespace().all(|v|v.len()==2&&v.bytes().all(|b|b.is_ascii_hexdigit())) {return Err("PC key uses space separated two-digit keyboard scancodes".into());}
                cmd.extend(["controlvm".into(),name,"keyboardputscancode".into()]);cmd.extend(codes.split_whitespace().map(str::to_string));
            }
            _=>return Err("This PC VM action is unavailable. Use launch_app with Guest Additions to run installers/tests, key for keyboard input, or inspect/screenshot.".into()),
        }
    }
    let o = run(exe, &cmd).await?;
    drop(password_file);
    let mut result = if action == "screenshot" {
        if !o.status.success() {
            return output(o);
        }
        if p.kind == "android" {
            if o.stdout.len() > 32 * 1024 * 1024 || !o.stdout.starts_with(b"\x89PNG\r\n\x1a\n") {
                return Err("The device did not return a valid bounded PNG screenshot".into());
            }
            std::fs::create_dir_all(&screenshot_dir).map_err(|e| e.to_string())?;
            std::fs::write(&screenshot, &o.stdout).map_err(|e| e.to_string())?;
        }
        if !screenshot.is_file() {
            return Err("Screenshot was not created".into());
        }
        json!({"imagePath":screenshot,"mime":"image/png","verified":false,"evidence":"captured screenshot; inspect pixels before claiming a visual check passed"})
    } else {
        output(o)?
    };
    result["profileId"] = json!(id);
    result["action"] = json!(action);
    if !matches!(action, "status" | "list" | "inspect" | "screenshot") {
        result["change"] = json!(format!("Testing lab {id}: {action}"));
    }
    Ok(result)
}
struct PasswordFile(PathBuf);
impl PasswordFile {
    fn new(data: &Path, bytes: &[u8]) -> Result<Self, String> {
        use std::io::Write;
        let root = data.join("testing-labs").join("credentials");
        std::fs::create_dir_all(&root).map_err(|e| e.to_string())?;
        let path = root.join(format!("{}.tmp", uuid::Uuid::new_v4()));
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&path).map_err(|e| e.to_string())?;
        let guard = Self(path);
        file.write_all(bytes).map_err(|e| e.to_string())?;
        Ok(guard)
    }
}
impl Drop for PasswordFile {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn profile() -> TestingLabProfile {
        TestingLabProfile {
            id: "phone".into(),
            label: "Phone".into(),
            kind: "android".into(),
            enabled: true,
            executable: "adb".into(),
            vm_name: None,
            device_serial: Some("emulator-5554".into()),
            avd_name: None,
            emulator_executable: None,
            sdk_root:None,avd_home:None,android_user_home:None,emulator_port:None,
            guest_user: None,
            password_env: None,
        }
    }
    #[test]
    fn profile_validation_requires_unique_ids_and_existing_vm_names() {
        let p = profile();
        assert!(validate(&[p.clone(), p]).is_err());
        let mut p = profile();
        p.kind = "virtualbox".into();
        assert!(validate(&[p]).is_err());
    }
    #[test]
    fn adb_targets_one_configured_device() {
        assert_eq!(adb_base(&profile()), ["-s", "emulator-5554"]);
    }
    #[test]
    fn android_text_quotes_shell_control_characters() {
        assert_eq!(shell_quote("a'; echo bad"), "'a'\\''; echo bad'");
    }
    #[test]
    fn profiles_persist_without_installing_an_sdk() {
        let root = std::env::temp_dir().join(format!("labs-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let store = EventStore::open(&root.join("app.sqlite3")).unwrap();
        save_profiles(&store, vec![profile()]).unwrap();
        assert_eq!(
            profiles(&store).unwrap()[0].device_serial.as_deref(),
            Some("emulator-5554")
        );
    }
    #[test]
    fn provisioned_profile_preserves_existing_profiles_and_managed_environment() {
        let root=std::env::temp_dir().join(format!("labs-setup-{}",uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();let store=EventStore::open(&root.join("app.sqlite3")).unwrap();
        save_profiles(&store,vec![profile()]).unwrap();
        let receipt=json!({"targetId":"testing-android","bootVerified":true,"executable":"C:/managed/sdk/platform-tools/adb.exe",
            "emulatorExecutable":"C:/managed/sdk/emulator/emulator.exe","sdkRoot":"C:/managed/sdk","avdHome":"C:/managed/avd",
            "androidUserHome":"C:/managed/user","avdName":"OpenCore_API_36","emulatorPort":5580,"deviceSerial":"emulator-5580"});
        save_setup_profile(&store,&receipt).unwrap();let mut saved=profiles(&store).unwrap();
        assert_eq!(saved.len(),2);assert_eq!(saved[0].id,"phone");assert_eq!(saved[1].avd_home.as_deref(),Some("C:/managed/avd"));
        assert_eq!(adb_base(&saved[1]),["-s","emulator-5580"]);
        saved[1].enabled=false;saved[1].label="My emulator".into();save_profiles(&store,saved).unwrap();
        save_setup_profile(&store,&receipt).unwrap();let repaired=profiles(&store).unwrap();
        assert_eq!(repaired.len(),2);assert!(!repaired[1].enabled);assert_eq!(repaired[1].label,"My emulator");
        assert!(save_setup_profile(&store,&json!({"targetId":"testing-pc-vm","guestVerified":false})).is_err());
    }
    #[tokio::test]
    async fn disabled_profiles_cannot_launch() {
        let root = std::env::temp_dir().join(format!("labs-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let store = EventStore::open(&root.join("app.sqlite3")).unwrap();
        let mut p = profile();
        p.enabled = false;
        save_profiles(&store, vec![p]).unwrap();
        assert!(execute(
            &store,
            &root,
            &json!({"action":"start","profileId":"phone"})
        )
        .await
        .unwrap_err()
        .contains("Enable"));
    }
    #[tokio::test]
    async fn agent_configuration_uses_the_same_store_and_rejects_invalid_updates() {
        let root = std::env::temp_dir().join(format!("labs-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let store = EventStore::open(&root.join("app.sqlite3")).unwrap();
        let args = json!({"action":"configure","profiles":[profile()]});
        assert_eq!(
            execute(&store, &root, &args).await.unwrap()["changed"],
            true
        );
        assert_eq!(
            execute(&store, &root, &args).await.unwrap()["changed"],
            false
        );
        assert!(execute(
            &store,
            &root,
            &json!({"action":"configure","profiles":[{"id":"bad/","label":"Bad","kind":"android"}]})
        )
        .await
        .is_err());
        assert_eq!(profiles(&store).unwrap()[0].id, "phone");
    }
}
