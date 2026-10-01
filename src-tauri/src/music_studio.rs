//! Use the existing YuE installation; do not duplicate models or song files.
use serde::Serialize;
use serde_json::Value;
use std::{path::{Path, PathBuf}, process::{Child, Command, Stdio}, time::Duration};
use tokio::sync::Mutex;
const URL: &str = "http://127.0.0.1:7860";
static OWNED: Mutex<Option<Child>> = Mutex::const_new(None);
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MusicStatus { installed: bool, running: bool, owned: bool, url: Option<String>, folder: String, model_loaded: bool, error: Option<String> }
fn root() -> PathBuf {
    std::env::var_os("OPENCORE_MUSIC_HOME").map(PathBuf::from)
        .unwrap_or_else(||PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default()).join("YuE"))
}
fn installed(root:&Path)->bool { root.join("studio/server.py").is_file() && root.join(".venv/Scripts/python.exe").is_file() }
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
#[tauri::command]
pub async fn music_studio_status()->MusicStatus {
    let root=root(); let checked=inspect(&root).await; let mut owned=OWNED.lock().await;
    if owned.as_mut().is_some_and(|child|child.try_wait().ok().flatten().is_some()){*owned=None;}
    let (running,model_loaded,error)=match checked {
        Ok(Some(info))=>(true,info["model_loaded"].as_bool().unwrap_or(false),None),
        Ok(None)=>(false,false,None),Err(error)=>(false,false,Some(error)),
    };
    MusicStatus{installed:installed(&root),running,owned:owned.is_some(),url:running.then(||URL.into()),folder:root.to_string_lossy().into_owned(),model_loaded,error}
}
#[tauri::command]
pub async fn start_music_studio()->Result<MusicStatus,String> {
    let root=root(); let mut owned=OWNED.lock().await;
    if inspect(&root).await?.is_some(){drop(owned);return Ok(music_studio_status().await);}
    if !installed(&root){return Err(format!("YuE2 Studio was not found in {}. Set OPENCORE_MUSIC_HOME to your existing installation.",root.display()));}
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
    let mut command=if use_wsl {
        let mut command=Command::new("wsl.exe");
        command.args(["-d","Ubuntu-24.04","-u","root","--","env","PYTHONIOENCODING=utf-8","YUE2_MODELS=/root/yue2/models","YUE2_BACKEND=vllm"])
            .arg(format!("YUE2_DOWNLOADS={}",wsl_path(&PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default()).join("Downloads"))?))
            .args(["/root/yue2/venv/bin/python","-u"]).arg(wsl_path(&root.join("studio/server.py"))?).arg("--no-browser"); command
    }else{
        let mut command=Command::new(root.join(".venv/Scripts/python.exe"));
        command.arg("-u").arg(root.join("studio/server.py")).arg("--no-browser");command
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
#[cfg(test)] mod tests {
    use super::*;
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
