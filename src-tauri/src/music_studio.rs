//! Use the existing YuE installation; do not duplicate models or song files.
use serde::Serialize;
use serde_json::Value;
use std::{path::{Path, PathBuf}, process::{Child, Command, Stdio}, time::Duration};
use tokio::sync::Mutex;
const URL: &str = "http://127.0.0.1:7860";
static OWNED: Mutex<Option<Child>> = Mutex::const_new(None);
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MusicStatus { installed: bool, pub running: bool, owned: bool, url: Option<String>, folder: String, pub model_loaded: bool, integration_current: bool, error: Option<String> }
pub(crate) fn root() -> PathBuf {
    std::env::var_os("OPENCORE_MUSIC_HOME").map(PathBuf::from)
        .unwrap_or_else(||PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default()).join("YuE"))
}
fn installed(root:&Path)->bool { root.join("studio/server.py").is_file() && root.join(".venv/Scripts/python.exe").is_file() }
pub(crate) fn runtime_available()->bool {installed(&root())}
fn prepare_host(root: &Path) -> Result<PathBuf, String> {
    let folder = root.join("studio").join("_opencore");
    std::fs::create_dir_all(&folder).map_err(|error| error.to_string())?;
    for (name, bytes) in [
        ("music_host.py", include_bytes!("../resources/studio/music_host.py").as_slice()),
        ("music_index.html", include_bytes!("../resources/studio/music_index.html").as_slice()),
        ("YuE-LICENSE.txt", include_bytes!("../resources/studio/YuE-LICENSE.txt").as_slice()),
        ("MUSIC-NOTICE.md", include_bytes!("../resources/studio/MUSIC-NOTICE.md").as_slice()),
    ] {
        let target = folder.join(name);
        if std::fs::read(&target).ok().as_deref() != Some(bytes) {
            std::fs::write(&target, bytes).map_err(|error| format!("Could not refresh Music Studio host: {error}"))?;
        }
    }
    Ok(folder.join("music_host.py"))
}
pub(crate) fn worker_active(status: &Value) -> bool {
    status["status"] == "running" || status["worker_active"] == true
}
fn integration_current(info: &Value) -> bool {
    let bytes = [include_bytes!("../resources/studio/music_host.py").as_slice(), include_bytes!("../resources/studio/music_index.html").as_slice()].concat();
    info["opencore_host_revision"].as_str() == Some(crate::dev_tool::sha256(&bytes).as_str())
}
fn wsl_path(path:&Path)->Result<String,String> {
    let text=path.to_string_lossy().replace('\\',"/");
    if text.as_bytes().get(1)!=Some(&b':'){return Err("YuE WSL integration requires an absolute Windows drive path".into());}
    Ok(format!("/mnt/{}{}",text[..1].to_ascii_lowercase(),&text[2..]))
}
fn is_yue(info:&Value,root:&Path)->bool {
    info["defaults"].is_object() && info["model_loaded"].is_boolean() && info["output"].as_str().is_some_and(|output|{
        Path::new(output)==root.join("studio-output") || wsl_path(&root.join("studio-output")).is_ok_and(|path|path==output)
    })
}
async fn inspect(root:&Path)->Result<Option<Value>,String> {
    let client=reqwest::Client::builder().no_proxy().timeout(Duration::from_secs(3)).build().map_err(|e|e.to_string())?;
    match client.get(format!("{URL}/api/info")).send().await {
        Err(error) if error.is_connect()=>Ok(None), Err(error)=>Err(format!("Music Studio did not respond: {error}")),
        Ok(response)=>{
            let info:Value=response.json().await.map_err(|_|"Port 7860 is occupied by another service".to_string())?;
            if !is_yue(&info,root){return Err("Port 7860 is occupied by a different service or YuE installation".into());}
            Ok(Some(info))
        }
    }
}
pub async fn request(method: &str, endpoint: &str, body: Option<&Value>) -> Result<Value,String> {
    if !matches!(endpoint,"/api/info"|"/api/status"|"/api/history"|"/api/generate"|"/api/cancel"|"/api/model/unload"|"/api/shutdown") {return Err("Unknown Music Studio operation".into());}
    inspect(&root()).await?.ok_or("Music Studio is stopped")?;
    let client=reqwest::Client::builder().no_proxy().timeout(Duration::from_secs(30)).build().map_err(|e|e.to_string())?;
    let mut req=client.request(if method=="POST" {reqwest::Method::POST}else{reqwest::Method::GET},format!("{URL}{endpoint}"));
    if let Some(body)=body {req=req.json(body);}
    let response=req.send().await.map_err(|e|e.to_string())?;let status=response.status();
    let value:Value=response.json().await.map_err(|e|e.to_string())?;
    if !status.is_success(){return Err(value["error"].as_str().unwrap_or("Music Studio request failed").into());}Ok(value)
}
pub async fn require_idle_gpu() -> Result<(),String> {
    if let Some(info) = inspect(&root()).await? {
        let status = request("GET", "/api/status", None).await?;
        if info["model_loaded"] == true || worker_active(&status) {
            return Err("Music Studio is generating or has a model loaded. Finish or cancel the generation and unload its model before loading a text model.".into());
        }
    }
    Ok(())
}
#[tauri::command]
pub async fn music_studio_status()->MusicStatus {
    let root=root(); let checked=inspect(&root).await; let mut owned=OWNED.lock().await;
    if owned.as_mut().is_some_and(|child|child.try_wait().ok().flatten().is_some()){*owned=None;}
    let (running,model_loaded,integration_current,error)=match checked {
        Ok(Some(info))=>(true,info["model_loaded"].as_bool().unwrap_or(false),integration_current(&info),None),
        Ok(None)=>(false,false,false,None),Err(error)=>(false,false,false,Some(error)),
    };
    MusicStatus{installed:installed(&root),running,owned:owned.is_some(),url:running.then(||URL.into()),folder:root.to_string_lossy().into_owned(),model_loaded,integration_current,error}
}
#[tauri::command]
pub async fn start_music_studio(core: tauri::State<'_, std::sync::Arc<crate::AppCore>>)->Result<MusicStatus,String> {
    core.ensure_not_updating()?;
    start_music_studio_unchecked().await
}
pub async fn start_music_studio_unchecked()->Result<MusicStatus,String> {
    let root=root(); let mut owned=OWNED.lock().await;
    if let Some(info) = inspect(&root).await? {
        if integration_current(&info) { drop(owned); return Ok(music_studio_status().await); }
        let status = request("GET", "/api/status", None).await?;
        if worker_active(&status) { return Err("Music Studio controls need an update. Finish or cancel the existing music job, then select Update Music Studio controls.".into()); }
        // The output path already proved this is the same existing YuE installation.
        // Restart only an idle legacy host; its shutdown releases the loaded pipeline.
        // The legacy host closes HTTP before final cleanup, so unload while HTTP
        // is alive and await the successful response before considering replacement.
        request("POST", "/api/model/unload", Some(&serde_json::json!({}))).await?;
        request("POST", "/api/shutdown", Some(&serde_json::json!({}))).await?;
        let mut stopped = false;
        for _ in 0..120 {
            let launcher_finished = match owned.as_mut() {
                Some(child) => child.try_wait().map_err(|error| error.to_string())?.is_some(),
                None => true,
            };
            if inspect(&root).await?.is_none() && launcher_finished { stopped = true; break; }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        if !stopped { return Err("The previous Music Studio host has not stopped yet. Retry Update Music Studio controls after it closes.".into()); }
    }
    if !installed(&root){return Err(format!("YuE2 Studio was not found in {}. Set OPENCORE_MUSIC_HOME to your existing installation.",root.display()));}
    let host = prepare_host(&root)?;
    if let Some(mut previous)=owned.take(){let _=previous.kill();let _=previous.wait();}
    let prefs:Value=std::fs::read(root.join("studio-output/_app.json")).ok().and_then(|bytes|serde_json::from_slice(&bytes).ok()).unwrap_or_default();
    // Some older YuE Windows installations have a broken Python base. Use
    // their already-installed Linux engine when the native interpreter fails.
    // Do not rewrite YuE preferences or install a second model/environment.
    let native=root.join(".venv/Scripts/python.exe");
    let use_wsl=if prefs["engine"]=="wsl" {true} else {
        tokio::task::spawn_blocking(move||{
            let mut probe=Command::new(native);
            probe.args(["-I","-c","import ctypes"]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
            #[cfg(windows)] {use std::os::windows::process::CommandExt;probe.creation_flags(0x0800_0000);}
            !probe.status().is_ok_and(|status|status.success())
        }).await.map_err(|error|error.to_string())?
    };
    let model_root=std::env::var_os("OPENCORE_HOME").map(PathBuf::from).unwrap_or_else(||PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default()).join("OpenCore"));
    let models=crate::music_weights::models_path(&model_root);
    let mut command=if use_wsl {
        let mut command=Command::new("wsl.exe");
        let linux_models=if models.to_string_lossy().starts_with(r"\\wsl.localhost\Ubuntu-24.04\") {models.to_string_lossy().trim_start_matches(r"\\wsl.localhost\Ubuntu-24.04").replace('\\',"/")}else{wsl_path(&models)?};
        command.args(["-d","Ubuntu-24.04","-u","root","--","env","PYTHONIOENCODING=utf-8","YUE2_BACKEND=vllm"])
            .arg(format!("YUE2_MODELS={linux_models}"))
            .arg(format!("OPENCORE_MUSIC_HOME={}", wsl_path(&root)?))
            .arg(format!("YUE2_DOWNLOADS={}",wsl_path(&PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default()).join("Downloads"))?))
            .args(["/root/yue2/venv/bin/python","-u"]).arg(wsl_path(&host)?).arg("--no-browser"); command
    }else{
        let mut command=Command::new(root.join(".venv/Scripts/python.exe"));
        command.env("YUE2_MODELS",&models);
        command.env("OPENCORE_MUSIC_HOME", &root);
        command.arg("-u").arg(&host).arg("--no-browser");command
    };
    std::fs::create_dir_all(root.join("studio-output")).map_err(|e|e.to_string())?;
    let log=std::fs::OpenOptions::new().create(true).append(true).open(root.join("studio-output/_opencore-server.log")).map_err(|e|e.to_string())?;
    command.current_dir(&root).env("PYTHONIOENCODING","utf-8").stdin(Stdio::null()).stdout(log.try_clone().map_err(|e|e.to_string())?).stderr(log);
    #[cfg(windows)] {use std::os::windows::process::CommandExt;command.creation_flags(0x0800_0000);}
    let child=command.spawn().map_err(|e|format!("Could not start YuE2 Studio: {e}"))?;
    #[cfg(windows)] crate::child_guard::adopt(&child);
    *owned=Some(child);
    for _ in 0..60 {
        match inspect(&root).await {
            Ok(Some(_))=>{drop(owned);return Ok(music_studio_status().await);},
            Ok(None)=>{},
            Err(error)=>{
                if let Some(mut child)=owned.take(){let _=child.kill();let _=child.wait();}
                return Err(error);
            }
        }
        if owned.as_mut().and_then(|child|child.try_wait().ok().flatten()).is_some(){*owned=None;return Err("YuE2 Studio stopped during startup. See studio-output/_opencore-server.log.".into());}
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    if let Some(mut child)=owned.take(){let _=child.kill();let _=child.wait();}
    Err("YuE2 Studio startup timed out. See studio-output/_opencore-server.log.".into())
}

pub async fn shutdown_owned() {
    let mut owned=OWNED.lock().await;
    let Some(mut child)=owned.take() else{return};
    // WSL services survive their Windows launcher. Ask this verified service
    // to unload its model before ending the launcher on normal app shutdown.
    let root=root();
    if matches!(inspect(&root).await,Ok(Some(_))) {
        if let Ok(client)=reqwest::Client::builder().no_proxy().timeout(Duration::from_secs(3)).build(){
            let _=client.post(format!("{URL}/api/shutdown")).json(&serde_json::json!({})).send().await;
        }
    }
    // Let YuE's finally block unload its engine and workers before ending the
    // WSL launcher. HTTP shutdown acknowledges before cleanup has completed.
    for _ in 0..40 {
        if child.try_wait().ok().flatten().is_some(){return;}
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
    let _=child.kill();let _=child.wait();
}

/// Stop the verified YuE service's active generation and unload its model.
/// An externally launched YuE server is left available after unloading; only
/// a child process started by OpenCore is shut down.
pub async fn stop_for_update() -> Result<(), String> {
    let root = root();
    let result = async {
        // Only send shutdown/cancel requests to the YuE service whose install
        // identity this app verified. An unknown process on the port is not
        // managed by OpenCore and must not block updating the app shell.
        if matches!(inspect(&root).await, Ok(Some(_))) {
            let mut status = request("GET", "/api/status", None).await?;
            if worker_active(&status) {
                request("POST", "/api/cancel", Some(&serde_json::json!({}))).await?;
                let mut stopped = false;
                for _ in 0..120 {
                    status = request("GET", "/api/status", None).await?;
                    if !worker_active(&status) {
                        stopped = true;
                        break;
                    }
                    tokio::time::sleep(Duration::from_millis(250)).await;
                }
                if !stopped {
                    return Err("Music Studio did not stop its active generation within 30 seconds.".to_string());
                }
            }
            let info = request("GET", "/api/info", None).await?;
            if info["model_loaded"] == true { request("POST", "/api/model/unload", Some(&serde_json::json!({}))).await?; }
        }
        Ok(())
    }.await;
    if let Err(error) = result {
        shutdown_owned().await;
        return Err(format!("{error} OpenCore did not install the update."));
    }
    shutdown_owned().await;
    Ok(())
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn cancelled_worker_keeps_gpu_reserved_until_cleanup_finishes() {
        assert!(worker_active(&serde_json::json!({"status":"cancelled","worker_active":true})));
        assert!(!worker_active(&serde_json::json!({"status":"cancelled","worker_active":false})));
        assert!(worker_active(&serde_json::json!({"status":"running"})));
    }
    #[test] fn music_host_refreshes_without_replacing_user_server_or_songs() {
        let root = std::env::temp_dir().join(format!("music-host-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join("studio")).unwrap();
        std::fs::create_dir_all(root.join("studio-output")).unwrap();
        std::fs::write(root.join("studio/server.py"), b"User server").unwrap();
        std::fs::write(root.join("studio-output/song.txt"), b"User song").unwrap();
        let host = prepare_host(&root).unwrap();
        assert!(host.is_file());
        std::fs::write(&host, b"Old host").unwrap();
        prepare_host(&root).unwrap();
        assert_eq!(std::fs::read(host).unwrap(), include_bytes!("../resources/studio/music_host.py"));
        assert_eq!(std::fs::read(root.join("studio/server.py")).unwrap(), b"User server");
        assert_eq!(std::fs::read(root.join("studio-output/song.txt")).unwrap(), b"User song");
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test] fn unrelated_local_service_is_never_embedded(){
        let root=PathBuf::from("music");
        assert!(!is_yue(&serde_json::json!({"status":"ok"}),&root));
        assert!(is_yue(&serde_json::json!({"defaults":{},"model_loaded":false,"output":root.join("studio-output")}),&root));
        assert!(!is_yue(&serde_json::json!({"defaults":{},"model_loaded":false,"output":"another-installation"}),&root));
    }
    #[test] fn wsl_paths_preserve_spaces_without_shell_interpolation(){
        assert_eq!(wsl_path(Path::new("C:\\Users\\User Name\\YuE")).unwrap(),"/mnt/c/Users/User Name/YuE");
        assert!(wsl_path(Path::new("relative")).is_err());
    }
}
