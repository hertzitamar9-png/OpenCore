//! Durable, cancellable dependency and testing-environment setup.
//! A dependency receipt never substitutes for a completed model inference job.
use futures_util::{FutureExt, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    process::Stdio,
    sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex},
    time::Duration,
};
use tokio::{io::{AsyncBufReadExt, BufReader}, process::Command};
use tokio_util::sync::CancellationToken;

const RECIPES: &str = include_str!("../resources/runtime-setup/recipes.json");
const ACTIVE_STATES: &[&str] = &["queued", "running", "cancelling"];

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SetupOptions {
    #[serde(default = "yes")]
    pub install_weights: bool,
    #[serde(default)]
    pub accept_licenses: bool,
    #[serde(default)]
    pub allow_administrator: bool,
    #[serde(default)]
    pub iso_path: Option<String>,
    #[serde(default)]
    pub guest_user: Option<String>,
    #[serde(default)]
    pub password_env: Option<String>,
}
fn yes() -> bool { true }

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SetupJob {
    pub id: String,
    pub target_id: String,
    pub recipe_id: String,
    pub status: String,
    pub stage: String,
    pub detail: String,
    pub created_at: String,
    pub updated_at: String,
    #[serde(default)]
    pub downloaded_bytes: u64,
    #[serde(default)]
    pub total_bytes: u64,
    #[serde(default)]
    pub diagnostics: Vec<String>,
    pub error: Option<String>,
    pub receipt: Option<Value>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SetupSnapshot {
    pub recipes: Vec<Value>,
    pub jobs: Vec<SetupJob>,
    pub receipts: Vec<Value>,
    pub active_job_id: Option<String>,
    pub managed_root: PathBuf,
}
struct ActiveJob { id: String, token: CancellationToken, weights_active: Arc<AtomicBool> }
pub struct RuntimeSetupManager {
    root: PathBuf,
    resources: PathBuf,
    jobs: Mutex<Vec<SetupJob>>,
    active: Mutex<Option<ActiveJob>>,
}

fn manifest() -> Value { serde_json::from_str(RECIPES).expect("Bundled runtime setup recipes must be valid") }
pub fn recipe_for(target: &str) -> Option<Value> {
    manifest()["recipes"].as_array()?.iter().find(|recipe|
        recipe["modelIds"].as_array().is_some_and(|ids| ids.iter().any(|id| id.as_str() == Some(target))))
        .cloned().map(|mut value| { value["supported"] = json!(true); value })
}
fn recipe_fingerprint(recipe: &Value) -> Result<String, String> {
    fn sorted(value: &Value) -> Value {
        match value {
            Value::Object(object)=>{
                let mut entries=object.iter().collect::<Vec<_>>();entries.sort_by(|a,b|a.0.cmp(b.0));
                let mut result=serde_json::Map::new();
                for (key,value) in entries {result.insert(key.clone(),sorted(value));}
                Value::Object(result)
            },
            Value::Array(values)=>Value::Array(values.iter().map(sorted).collect()),
            _=>value.clone(),
        }
    }
    let mut bare=recipe.clone();
    if let Some(object)=bare.as_object_mut(){object.remove("supported");}
    Ok(format!("{:x}", Sha256::digest(serde_json::to_vec(&sorted(&bare)).map_err(|error|error.to_string())?)))
}
fn atomic_json(path: &Path, value: &impl Serialize) -> Result<(), String> {
    let parent = path.parent().ok_or("Missing setup state directory")?;
    std::fs::create_dir_all(parent).map_err(|error|error.to_string())?;
    let temporary = path.with_extension("json.partial");
    let bytes = serde_json::to_vec_pretty(value).map_err(|error|error.to_string())?;
    {
        use std::io::Write;
        let mut output = std::fs::File::create(&temporary).map_err(|error|error.to_string())?;
        output.write_all(&bytes).map_err(|error|error.to_string())?;
        output.sync_all().map_err(|error|error.to_string())?;
    }
    // Windows rename does not replace existing files. MoveFileExW provides one
    // replace operation; a crash cannot leave a partly written live JSON file.
    #[cfg(windows)] {
        use std::os::windows::ffi::OsStrExt;
        let source: Vec<u16> = temporary.as_os_str().encode_wide().chain(Some(0)).collect();
        let target: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        #[link(name = "kernel32")]
        extern "system" { fn MoveFileExW(source: *const u16, target: *const u16, flags: u32) -> i32; }
        if unsafe { MoveFileExW(source.as_ptr(), target.as_ptr(), 0x1 | 0x8) } == 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
    }
    #[cfg(not(windows))]
    std::fs::rename(temporary, path).map_err(|error|error.to_string())?;
    Ok(())
}
fn now() -> String { chrono::Utc::now().to_rfc3339() }
async fn wait_cancel_deadline(deadline: Option<tokio::time::Instant>) {
    match deadline {Some(deadline)=>tokio::time::sleep_until(deadline).await,None=>std::future::pending::<()>().await}
}
fn command(executable: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut result = Command::new(executable);
    result.kill_on_drop(true).stdin(Stdio::null());
    #[cfg(windows)] result.creation_flags(0x0800_0000);
    result
}

impl RuntimeSetupManager {
    pub fn new(root: PathBuf, resources: PathBuf) -> Result<Arc<Self>, String> {
        let path = root.join("runtime-setup/jobs.json");
        let mut jobs: Vec<SetupJob> = if path.is_file() {
            serde_json::from_slice(&std::fs::read(&path).map_err(|error|error.to_string())?)
                .map_err(|error|format!("Cannot read persisted runtime setup jobs: {error}"))?
        } else { Vec::new() };
        for job in &mut jobs {
            if ACTIVE_STATES.contains(&job.status.as_str()) {
                job.status = "interrupted".into();
                job.stage = "interrupted".into();
                job.error = Some("The application closed during setup. Completed files were retained; retry setup to verify and continue.".into());
                job.updated_at = now();
            }
        }
        let manager = Arc::new(Self { root, resources, jobs: Mutex::new(jobs), active: Mutex::new(None) });
        manager.persist()?;
        Ok(manager)
    }
    fn persist(&self) -> Result<(), String> {
        atomic_json(&self.root.join("runtime-setup/jobs.json"), &*self.jobs.lock().map_err(|error|error.to_string())?)
    }
    fn update(&self, id: &str, modify: impl FnOnce(&mut SetupJob)) -> Result<(), String> {
        {
            let mut jobs = self.jobs.lock().map_err(|error|error.to_string())?;
            let job = jobs.iter_mut().find(|job|job.id == id).ok_or("Runtime setup job was not found")?;
            modify(job);
            job.updated_at = now();
            atomic_json(&self.root.join("runtime-setup/jobs.json"), &*jobs)?;
        }
        Ok(())
    }
    pub fn busy(&self) -> bool { self.active.lock().map(|active|active.is_some()).unwrap_or(true) }
    fn bundled(&self, name: &str) -> PathBuf {
        let installed = self.resources.join(name);
        if installed.is_file() { installed } else { PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources").join(name) }
    }
    pub fn snapshot(&self) -> Result<SetupSnapshot, String> {
        let recipes = manifest()["recipes"].as_array().cloned().unwrap_or_default();
        let mut receipts = Vec::new();
        for recipe in &recipes {
            for target in recipe["modelIds"].as_array().into_iter().flatten().filter_map(Value::as_str) {
                if let Some(receipt) = self.receipt(target)? { receipts.push(receipt); }
            }
        }
        Ok(SetupSnapshot { recipes, jobs: self.jobs.lock().map_err(|error|error.to_string())?.clone(), receipts,
            active_job_id: self.active.lock().map_err(|error|error.to_string())?.as_ref().map(|active|active.id.clone()),
            managed_root: self.root.join("runtime-setup") })
    }
    pub fn receipt(&self, target: &str) -> Result<Option<Value>, String> {
        let Some(recipe) = recipe_for(target) else { return Ok(None); };
        let bytes = match std::fs::read(self.root.join("runtime-setup/receipts").join(format!("{target}.json"))) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.to_string()),
        };
        let mut receipt: Value = serde_json::from_slice(&bytes).map_err(|error|format!("Invalid runtime setup receipt: {error}"))?;
        let executable = receipt["python"].as_str().or_else(||receipt["executable"].as_str()).map(PathBuf::from);
        if receipt["schema"] != 1 || receipt["dependenciesVerified"] != true
            || receipt["recipeFingerprint"].as_str() != Some(recipe_fingerprint(&recipe)?.as_str())
            || executable.as_ref().is_none_or(|path|!path.is_absolute() || !path.is_file()) {
            return Ok(None);
        }
        if recipe["kind"] == "studio" && executable.as_ref().is_some_and(|path|!path.starts_with(self.root.join("runtime-setup/environments"))) {
            return Ok(None);
        }
        // Evidence expires when the model pin changes or an output is removed.
        if receipt["inferenceVerified"] == true {
            let pin = model_pin(target);
            let proof = &receipt["inferenceProof"];
            let valid_outputs = proof["outputs"].as_array().is_some_and(|outputs|!outputs.is_empty() && outputs.iter().all(|output|
                output["path"].as_str().and_then(|path|std::fs::metadata(path).ok()).is_some_and(|meta|
                    meta.is_file() && Some(meta.len()) == output["bytes"].as_u64() && modified_ns(&meta) == output["modifiedNanos"].as_str().unwrap_or(""))));
            let valid_transcript = recipe["kind"] == "speech" && proof["transcriptCharacters"].as_u64().is_some_and(|count|count > 0);
            if proof["modelPin"] != pin || !(valid_outputs || valid_transcript) { receipt["inferenceVerified"] = json!(false); }
        }
        Ok(Some(receipt))
    }
    pub async fn probe(&self) -> Result<Value, String> {
        match discover_python(&self.root).await {
            Ok(python) => {
                let output = tokio::time::timeout(Duration::from_secs(90), command(python)
                    .arg(self.bundled("runtime-setup/setup_manager.py")).arg("--root").arg(&self.root).arg("--probe")
                    .env("PYTHONIOENCODING", "utf-8").output()).await
                    .map_err(|_|"Environment discovery exceeded 90 seconds".to_string())?.map_err(|error|error.to_string())?;
                let text = String::from_utf8_lossy(&output.stdout);
                let result = text.lines().rev().find_map(|line|serde_json::from_str::<Value>(line).ok()).ok_or_else(||format!("Environment discovery failed: {}",String::from_utf8_lossy(&output.stderr)))?;
                result.get("inventory").cloned().ok_or_else(||result["error"].as_str().unwrap_or("Environment discovery returned no inventory").into())
            }
            Err(error) => {
                let system = sysinfo::System::new_all();
                Ok(json!({"python":[],"pythonError":error,"ramBytes":system.total_memory(),"platform":std::env::consts::OS,"architecture":std::env::consts::ARCH,"tools":{},"gpus":[],"bootstrapAvailable":cfg!(all(windows,target_arch="x86_64"))}))
            }
        }
    }
    pub fn start(self: &Arc<Self>, core: Arc<crate::AppCore>, app: tauri::AppHandle, target: String, options: SetupOptions) -> Result<SetupJob, String> {
        core.ensure_not_updating()?;
        let recipe = recipe_for(&target).ok_or("Automatic setup has no verified publisher-compatible worker recipe for this architecture. Connect the publisher runtime explicitly.")?;
        if recipe["requiresLicenseAcceptance"] == true && !options.accept_licenses {
            return Err("Review and accept the publisher license in the setup form before provisioning.".into());
        }
        if core.studios.busy() || core.background.busy_gpu() || crate::studio_jobs::gpu_reserved()
            || core.speech.is_active().now_or_never().unwrap_or(true) {
            return Err("Finish active chats, dictation and studio jobs before starting dependency setup.".into());
        }
        let chats=core.active_chats.lock().map_err(|error|error.to_string())?;
        if !chats.is_empty(){return Err("Finish active chats before starting dependency setup.".into());}
        let mut active = self.active.lock().map_err(|error|error.to_string())?;
        if active.is_some() { return Err("Another runtime setup is in progress. Cancel it or wait for completion.".into()); }
        let id = uuid::Uuid::new_v4().to_string();
        let timestamp = now();
        let job = SetupJob { id: id.clone(), target_id: target.clone(), recipe_id: recipe["id"].as_str().unwrap_or("").into(),
            status: "queued".into(), stage: "queued".into(), detail: "Preparing automatic setup".into(), created_at: timestamp.clone(), updated_at: timestamp,
            downloaded_bytes: 0, total_bytes: 0, diagnostics: vec![], error: None, receipt: None };
        let token = CancellationToken::new();
        let weights_active = Arc::new(AtomicBool::new(false));
        {
            let mut jobs = self.jobs.lock().map_err(|error|error.to_string())?;
            jobs.push(job.clone());
            if jobs.len() > 48 { jobs.remove(0); }
            atomic_json(&self.root.join("runtime-setup/jobs.json"), &*jobs)?;
        }
        *active = Some(ActiveJob { id: id.clone(), token: token.clone(), weights_active: weights_active.clone() });
        drop(active);
        drop(chats);
        let manager = self.clone();
        tauri::async_runtime::spawn(async move {
            let result = manager.run_setup(core.clone(), app, &id, &target, &options, &token, &weights_active).await;
            let _ = manager.update(&id, |job| {
                match result {
                    Ok(receipt) => { job.status = "dependencies-verified".into(); job.stage = "dependencies-verified".into(); job.receipt = Some(receipt); job.detail = "Dependencies configured. Complete a real model job to verify inference.".into(); }
                    Err(error) => { job.status = if token.is_cancelled() { "cancelled" } else { "failed" }.into(); job.stage = job.status.clone(); job.error = Some(error); }
                }
            });
            if let Ok(mut active) = manager.active.lock() { if active.as_ref().is_some_and(|value|value.id == id) { *active = None; } }
        });
        Ok(job)
    }
    async fn run_setup(&self, core: Arc<crate::AppCore>, _app: tauri::AppHandle, id: &str, target: &str, options: &SetupOptions, token: &CancellationToken, weights_active: &Arc<AtomicBool>) -> Result<Value, String> {
        self.update(id, |job|{job.status="running".into();job.stage="discovering-python".into();job.detail="Finding a healthy compatible Python installation".into();})?;
        let detected=tokio::select! {biased;_=token.cancelled()=>return Err("Runtime setup cancelled during interpreter discovery".into()),value=discover_python(&self.root)=>value};
        let python = match detected { Ok(python)=>python, Err(_)=>self.bootstrap_python(id, token).await? };
        if token.is_cancelled() { return Err("Runtime setup cancelled".into()); }
        let job_dir = self.root.join("runtime-setup/runs").join(id);
        std::fs::create_dir_all(&job_dir).map_err(|error|error.to_string())?;
        let options_file = job_dir.join("options.json");
        atomic_json(&options_file, options)?;
        let cancel_file = job_dir.join("cancel");
        let resources = if self.resources.join("runtime-setup/setup_manager.py").is_file() { self.resources.clone() }
            else { PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources") };
        let mut process = command(python);
        process.arg(self.bundled("runtime-setup/setup_manager.py")).arg("--root").arg(&self.root)
            .arg("--resources").arg(resources).arg("--target").arg(target).arg("--cancel-file").arg(&cancel_file)
            .arg("--options-file").arg(options_file).env("PYTHONIOENCODING", "utf-8")
            .stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = process.spawn().map_err(|error|format!("Cannot start managed dependency setup: {error}"))?;
        #[cfg(windows)] if let Some(handle)=child.raw_handle() { crate::child_guard::adopt_handle(handle); }
        let mut stdout = BufReader::new(child.stdout.take().ok_or("Missing setup output")?).lines();
        let mut stderr = BufReader::new(child.stderr.take().ok_or("Missing setup diagnostics")?).lines();
        let mut out_done = false;
        let mut err_done = false;
        let mut receipt = None;
        let mut cancelled = false;
        let mut cancel_deadline = None;
        loop {
            tokio::select! {
                biased;
                _ = token.cancelled(), if !cancelled => {
                    std::fs::write(&cancel_file,b"cancel").map_err(|error|error.to_string())?;
                    cancelled=true;cancel_deadline=Some(tokio::time::Instant::now()+Duration::from_secs(12));
                    self.update(id,|job|{job.status="cancelling".into();job.detail="Stopping setup subprocesses and releasing resources".into();})?;
                }
                // This absolute deadline is polled before output. A process
                // emitting continuous diagnostics cannot postpone cancellation.
                _=wait_cancel_deadline(cancel_deadline)=>{
                    let _=child.kill().await;let _=child.wait().await;
                    return Err("Setup cancelled; its process was stopped. Windows administrator installers may require completing their own permission/rollback dialog.".into());
                }
                line=stdout.next_line(), if !out_done => match line.map_err(|error|error.to_string())? {
                    None=>out_done=true,
                    Some(line)=>{
                        if line.len()>64*1024 {return Err("Setup returned an oversized progress message".into());}
                        match serde_json::from_str::<Value>(&line) {
                            Ok(value)=>{
                                if let Some(result)=value.get("receipt"){receipt=Some(result.clone());}
                                self.update(id,|job|{
                                    let stage=value["stage"].as_str().unwrap_or("diagnostic");
                                    if stage!="diagnostic" {job.stage=stage.into();}
                                    if let Some(detail)=value["detail"].as_str(){
                                        if stage=="diagnostic" {job.diagnostics.push(detail.chars().take(2048).collect());if job.diagnostics.len()>80{job.diagnostics.remove(0);}}
                                        else {job.detail=detail.chars().take(2048).collect();}
                                    }
                                    if let Some(bytes)=value["downloadedBytes"].as_u64(){job.downloaded_bytes=bytes;}
                                    if let Some(bytes)=value["totalBytes"].as_u64(){job.total_bytes=bytes;}
                                    if let Some(error)=value["error"].as_str(){job.error=Some(error.chars().take(16000).collect());}
                                })?;
                            }
                            Err(_)=>self.update(id,|job|{job.diagnostics.push(line.chars().take(2048).collect());if job.diagnostics.len()>80{job.diagnostics.remove(0);}})?,
                        }
                    }
                },
                line=stderr.next_line(), if !err_done => match line.map_err(|error|error.to_string())? {
                    None=>err_done=true,
                    Some(line)=>self.update(id,|job|{job.diagnostics.push(line.chars().take(2048).collect());if job.diagnostics.len()>80{job.diagnostics.remove(0);}})?,
                },
            }
            if out_done && err_done {break;}
        }
        // A child can close its pipes and remain alive. Keep cancellation and
        // the same deadline active while waiting for its actual exit.
        let status = loop {tokio::select! {
            biased;
            _=token.cancelled(),if !cancelled=>{
                std::fs::write(&cancel_file,b"cancel").map_err(|error|error.to_string())?;
                cancelled=true;cancel_deadline=Some(tokio::time::Instant::now()+Duration::from_secs(12));
            },
            _=wait_cancel_deadline(cancel_deadline)=>{
                let _=child.kill().await;let _=child.wait().await;
                return Err("Setup cancelled; the remaining setup process was stopped".into());
            },
            status=child.wait()=>break status.map_err(|error|error.to_string())?,
        }};
        if cancelled {return Err("Setup cancelled; completed files were retained for retry.".into());}
        if !status.success() {
            let job = self.jobs.lock().map_err(|error|error.to_string())?.iter().find(|job|job.id==id).cloned();
            return Err(job.and_then(|job|job.error.or_else(||job.diagnostics.last().cloned())).unwrap_or_else(||format!("Runtime setup exited with {status}")));
        }
        let receipt = receipt.ok_or("Runtime setup returned without a verified dependency receipt")?;
        if self.receipt(target)?.is_none() {return Err("Runtime setup receipt failed validation".into());}
        crate::testing_labs::save_setup_profile(&core.store,&receipt)?;
        if recipe_for(target).is_some_and(|recipe|recipe["kind"]=="studio") {
            let python=receipt["python"].as_str().ok_or("Verified runtime did not return its interpreter")?;
            core.studios.configure(crate::studio_jobs::StudioRuntime { model_id:target.into(),python:PathBuf::from(python),source_dir:None,runner:None })?;
        }
        if options.install_weights && crate::model_catalog::model(target).is_some() && crate::model_catalog::require_installed(&self.root,target).is_err() {
            crate::model_catalog::begin(target)?;
            weights_active.store(true,Ordering::Release);
            self.update(id,|job|{job.stage="installing-weights".into();job.detail="Downloading and verifying the selected pinned checkpoint".into();})?;
            let install=crate::model_catalog::install(self.root.clone(),target.into(),Some(self.resources.clone()));
            tokio::pin!(install);
            loop {
                tokio::select! {
                    _=&mut install=>break,
                    _=token.cancelled(), if !cancelled=>{crate::model_catalog::cancel();cancelled=true;},
                    _=tokio::time::sleep(Duration::from_millis(500))=>{
                        if let Ok(library)=crate::model_catalog::list(&self.root) {
                            let progress=serde_json::to_value(library).ok().and_then(|library|library.get("progress").cloned());
                            if let Some(progress)=progress {self.update(id,|job|{job.downloaded_bytes=progress["downloadedBytes"].as_u64().unwrap_or(0);job.total_bytes=progress["totalBytes"].as_u64().unwrap_or(0);job.detail=progress["currentFile"].as_str().unwrap_or("Verifying checkpoint").into();})?;}
                        }
                    }
                }
            }
            weights_active.store(false,Ordering::Release);
            if cancelled {return Err("Checkpoint installation cancelled; verified dependencies and resumable files were retained.".into());}
            crate::model_catalog::require_installed(&self.root,target)?;
        }
        Ok(receipt)
    }
    pub fn cancel(&self, id: &str) -> Result<(), String> {
        let active=self.active.lock().map_err(|error|error.to_string())?;
        let Some(active)=active.as_ref().filter(|active|active.id==id) else{return Err("This setup job is no longer running".into());};
        active.token.cancel();
        if active.weights_active.load(Ordering::Acquire){crate::model_catalog::cancel();}
        Ok(())
    }
    pub async fn shutdown(&self) {
        if let Ok(active)=self.active.lock(){if let Some(active)=active.as_ref(){active.token.cancel();if active.weights_active.load(Ordering::Acquire){crate::model_catalog::cancel();}}}
        let deadline=tokio::time::Instant::now()+Duration::from_secs(20);
        while self.busy() && tokio::time::Instant::now()<deadline {tokio::time::sleep(Duration::from_millis(100)).await;}
    }
    pub fn record_inference(&self, job: &crate::studio_jobs::StudioJob) -> Result<Value, String> {
        if job.status!="completed" {return Err("Inference evidence requires a completed studio job".into());}
        let target=&job.request.model_id;
        let mut receipt=self.receipt(target)?.ok_or("Install and verify this runtime before recording inference evidence")?;
        let mut outputs=Vec::new();
        for file in &job.outputs {
            let path=Path::new(file);
            if !path.is_absolute(){return Err("Inference output must be an absolute file path".into());}
            let metadata=std::fs::metadata(path).map_err(|error|error.to_string())?;
            if !metadata.is_file() || metadata.len()==0 {return Err("Inference output is missing or empty".into());}
            outputs.push(json!({"path":path,"bytes":metadata.len(),"modifiedNanos":modified_ns(&metadata)}));
        }
        let transcript=if crate::model_catalog::is_speech_model(target){job.progress["text"].as_str().unwrap_or("").trim().chars().count()}else{0};
        if outputs.is_empty() && transcript==0 {return Err("Completed job returned no output or transcript; inference was not verified".into());}
        receipt["inferenceVerified"]=json!(true);
        receipt["inferenceProof"]=json!({"jobId":job.id,"checkedAt":now(),"modelPin":model_pin(target),"outputs":outputs,"transcriptCharacters":transcript});
        atomic_json(&self.root.join("runtime-setup/receipts").join(format!("{target}.json")),&receipt)?;
        Ok(receipt)
    }
    async fn bootstrap_python(&self, id: &str, token: &CancellationToken) -> Result<PathBuf, String> {
        if !cfg!(all(windows,target_arch="x86_64")){return Err("No healthy Python 3.10–3.12 x64 was found. Automatic Python bootstrap is supported on Windows x64.".into());}
        let pin=manifest()["pythonBootstrap"].clone();
        let base=self.root.join("runtime-setup/python-3.12.10");
        let python=base.join("python.exe");
        let downloads=self.root.join("runtime-setup/downloads");
        std::fs::create_dir_all(&downloads).map_err(|error|error.to_string())?;
        let installer=downloads.join("python-3.12.10-amd64.exe");
        let temporary=installer.with_extension("exe.partial");
        self.update(id,|job|{job.stage="bootstrapping-python".into();job.detail="Downloading the verified publisher Python installer into the managed runtime directory".into();})?;
        let client=reqwest::Client::builder().connect_timeout(Duration::from_secs(30)).timeout(Duration::from_secs(300)).build().map_err(|error|error.to_string())?;
        let response=tokio::select!{_=token.cancelled()=>return Err("Python bootstrap cancelled".into()),result=client.get(pin["url"].as_str().ok_or("Missing Python source pin")?).send()=>result.map_err(|error|error.to_string())?};
        let response=response.error_for_status().map_err(|error|error.to_string())?;
        let total=response.content_length().unwrap_or(0);
        if total>40_000_000{return Err("Python installer exceeds its recipe size limit".into());}
        let mut stream=response.bytes_stream();
        let mut output=tokio::fs::File::create(&temporary).await.map_err(|error|error.to_string())?;
        let mut hash=Sha256::new();let mut downloaded=0u64;
        loop {
            let next=tokio::select!{_=token.cancelled()=>return Err("Python bootstrap cancelled".into()),value=stream.next()=>value};
            let Some(bytes)=next else{break;};let bytes=bytes.map_err(|error|error.to_string())?;
            downloaded+=bytes.len() as u64;if downloaded>40_000_000{return Err("Python installer exceeds its recipe size limit".into());}
            hash.update(&bytes);
            use tokio::io::AsyncWriteExt;
            output.write_all(&bytes).await.map_err(|error|error.to_string())?;
            self.update(id,|job|{job.downloaded_bytes=downloaded;job.total_bytes=total;})?;
        }
        output.sync_all().await.map_err(|error|error.to_string())?;drop(output);
        if format!("{:x}",hash.finalize())!=pin["sha256"].as_str().unwrap_or(""){let _=std::fs::remove_file(temporary);return Err("Python installer failed SHA-256 verification".into());}
        if installer.exists(){std::fs::remove_file(&installer).map_err(|error|error.to_string())?;}
        std::fs::rename(temporary,&installer).map_err(|error|error.to_string())?;
        self.update(id,|job|{job.detail="Installing the managed Python interpreter without changing PATH or existing environments".into();})?;
        let mut child=command(installer).args(["/quiet","InstallAllUsers=0","Include_pip=1","Include_launcher=0","Include_test=0","PrependPath=0","Shortcuts=0","AssociateFiles=0"])
            .arg(format!("TargetDir={}",base.display())).spawn().map_err(|error|error.to_string())?;
        let status=tokio::select!{_=token.cancelled()=>{let _=child.kill().await;let _=child.wait().await;return Err("Managed Python installation cancelled; retry to repair its managed directory".into());},result=child.wait()=>result.map_err(|error|error.to_string())?};
        if !status.success() || !healthy_python(&python).await{return Err(format!("Managed Python bootstrap did not pass its interpreter health check ({status})"));}
        Ok(python)
    }
}
fn modified_ns(metadata:&std::fs::Metadata)->String{metadata.modified().ok().and_then(|time|time.duration_since(std::time::UNIX_EPOCH).ok()).map(|time|time.as_nanos().to_string()).unwrap_or_default()}
fn model_pin(target:&str)->Value{crate::model_catalog::model(target).and_then(|model|serde_json::to_value(model).ok()).map(|model|json!({"artifacts":model["artifacts"],"sourceUrl":model["sourceUrl"],"precision":model["precision"]})).unwrap_or(Value::Null)}

async fn healthy_python(python:&Path)->bool {
    tokio::time::timeout(Duration::from_secs(8),command(python).args(["-I","-c","import ctypes,json,sys,struct,tempfile,venv,ssl; assert (3,10)<=sys.version_info[:2]<=(3,12) and struct.calcsize('P')==8; print(sys.executable)"]).output())
        .await.ok().and_then(Result::ok).is_some_and(|output|output.status.success())
}
pub async fn discover_python(root:&Path)->Result<PathBuf,String> {
    let mut candidates=Vec::<PathBuf>::new();
    candidates.push(root.join("runtime-setup/python-3.12.10/python.exe"));
    for variable in ["OPENCORE_PYTHON","OPENCORE_SPEECH_TORCH_PYTHON"] {if let Some(path)=std::env::var_os(variable){candidates.push(path.into());}}
    for variable in ["VIRTUAL_ENV","CONDA_PREFIX"] {if let Some(path)=std::env::var_os(variable){candidates.push(PathBuf::from(&path).join("Scripts/python.exe"));candidates.push(PathBuf::from(path).join("python.exe"));}}
    let mut directories=vec![root.join("training-envs"),root.join("runtime-setup/environments"),root.join("speech")];
    if let Some(local)=std::env::var_os("LOCALAPPDATA") {
        let local=PathBuf::from(local);
        directories.push(local.join("Programs/Python"));directories.push(local.join("OpenCore/training-envs"));
    }
    if let Some(roaming)=std::env::var_os("APPDATA"){directories.push(PathBuf::from(roaming).join("uv/python"));}
    for directory in directories {
        if let Ok(entries)=std::fs::read_dir(directory){for entry in entries.flatten().take(64){let path=entry.path();candidates.push(path.join("Scripts/python.exe"));candidates.push(path.join("python.exe"));}}
    }
    if let Some(paths)=std::env::var_os("PATH") {for directory in std::env::split_paths(&paths){candidates.push(directory.join(if cfg!(windows){"python.exe"}else{"python3"}));}}
    if cfg!(windows) {
        if let Ok(Ok(output))=tokio::time::timeout(Duration::from_secs(8),command("py").arg("-0p").output()).await {
            for line in String::from_utf8_lossy(&output.stdout).lines(){if let Some(start)=line.find(":\\"){if start>0{candidates.push(PathBuf::from(line[start-1..].trim()));}}}
        }
    }
    let mut seen=HashSet::new();
    for candidate in candidates {
        if !candidate.is_file() || !seen.insert(candidate.to_string_lossy().to_lowercase()){continue;}
        if healthy_python(&candidate).await{return Ok(candidate);}
    }
    Err("No healthy Python 3.10–3.12 x64 was found in PATH, registered Python, virtual environments or app-managed environments. Setup can install a pinned managed Python on Windows x64.".into())
}

#[tauri::command]
pub fn runtime_setup_status(core:tauri::State<'_,Arc<crate::AppCore>>)->Result<SetupSnapshot,String>{core.runtime_setup.snapshot()}
#[tauri::command]
pub async fn runtime_setup_probe(core:tauri::State<'_,Arc<crate::AppCore>>)->Result<Value,String>{core.runtime_setup.probe().await}
#[tauri::command]
pub fn runtime_setup_start(core:tauri::State<'_,Arc<crate::AppCore>>,app:tauri::AppHandle,webview:tauri::Webview,target_id:String,options:SetupOptions)->Result<SetupJob,String>{crate::computer_access::require_settings_surface(webview.label())?;core.runtime_setup.start(core.inner().clone(),app,target_id,options)}
#[tauri::command]
pub fn runtime_setup_cancel(core:tauri::State<'_,Arc<crate::AppCore>>,webview:tauri::Webview,job_id:String)->Result<(),String>{crate::computer_access::require_settings_surface(webview.label())?;core.runtime_setup.cancel(&job_id)}
#[tauri::command]
pub fn runtime_setup_record_inference(core:tauri::State<'_,Arc<crate::AppCore>>,webview:tauri::Webview,job_id:String)->Result<Value,String>{crate::computer_access::require_settings_surface(webview.label())?;core.runtime_setup.record_inference(&core.studios.get(&job_id)?)}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn recipe_fingerprint_matches_python_canonical_utf8_fixture() {
        let recipe=json!({"supported":true,"nested":{"z":2,"a":1},"label":"café","id":"fixture"});
        assert_eq!(recipe_fingerprint(&recipe).unwrap(),"82eb70d7b4c560b040337f074e68fc522442eab39a71f0f2d51786aa587fe34a");
        let mut bare=recipe;bare.as_object_mut().unwrap().remove("supported");
        assert_eq!(recipe_fingerprint(&bare).unwrap(),"82eb70d7b4c560b040337f074e68fc522442eab39a71f0f2d51786aa587fe34a");
    }
    #[tokio::test]
    async fn absolute_cancellation_deadline_is_not_starved_by_ready_output() {
        let deadline=tokio::time::Instant::now()+Duration::from_millis(20);
        tokio::time::timeout(Duration::from_millis(250),async {
            loop {tokio::select! {biased;_=wait_cancel_deadline(Some(deadline))=>break,_=std::future::ready(())=>tokio::task::yield_now().await}}
        }).await.expect("Continuous ready output must not reset the cancellation deadline");
    }
    #[test]
    fn unsupported_models_have_no_automatic_setup_recipe(){assert!(recipe_for("flux-2-klein-4b").is_none());assert!(recipe_for("sana-16").is_some());assert!(recipe_for("triposr").is_none());}
    #[test]
    fn restarting_retains_interrupted_setup_diagnostics(){
        let root=std::env::temp_dir().join(format!("setup-recovery-{}",uuid::Uuid::new_v4()));
        let manager=RuntimeSetupManager::new(root.clone(),root.join("resources")).unwrap();
        let timestamp=now();
        let job=SetupJob{id:"fixture".into(),target_id:"sana-16".into(),recipe_id:"diffusers-images-v1".into(),status:"running".into(),stage:"installing-dependencies".into(),detail:"Installing".into(),created_at:timestamp.clone(),updated_at:timestamp,downloaded_bytes:9,total_bytes:20,diagnostics:vec!["Publisher package fixture".into()],error:None,receipt:None};
        manager.jobs.lock().unwrap().push(job);manager.persist().unwrap();drop(manager);
        let restored=RuntimeSetupManager::new(root.clone(),root.join("resources")).unwrap();let snapshot=restored.snapshot().unwrap();
        assert_eq!(snapshot.jobs[0].status,"interrupted");assert_eq!(snapshot.jobs[0].downloaded_bytes,9);assert_eq!(snapshot.jobs[0].diagnostics,vec!["Publisher package fixture"]);assert!(!restored.busy());
        drop(restored);std::fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn missing_interpreter_and_false_verification_do_not_create_readiness(){
        let root=std::env::temp_dir().join(format!("setup-receipt-{}",uuid::Uuid::new_v4()));let manager=RuntimeSetupManager::new(root.clone(),root.clone()).unwrap();
        let recipe=recipe_for("sana-16").unwrap();let receipt=json!({"schema":1,"recipeFingerprint":recipe_fingerprint(&recipe).unwrap(),"python":root.join("missing/python.exe"),"dependenciesVerified":true,"inferenceVerified":false});
        atomic_json(&root.join("runtime-setup/receipts/sana-16.json"),&receipt).unwrap();assert!(manager.receipt("sana-16").unwrap().is_none());
        drop(manager);std::fs::remove_dir_all(root).unwrap();
    }
}
