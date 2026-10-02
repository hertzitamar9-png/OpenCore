//! Optional, immutable model downloads. No weight file is bundled or auto-installed.
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::{Mutex, atomic::{AtomicBool, Ordering}};
use sysinfo::Disks;

// Small allowance for receipts and filesystem overhead, independent of model size.
const MIN_FREE_BYTES: u64 = 64 * 1024 * 1024;
#[path = "model_removal.rs"]
mod removal;
pub use removal::RemovalPlan;
static CANCEL: AtomicBool = AtomicBool::new(false);
static PROGRESS: Mutex<Option<InstallProgress>> = Mutex::new(None);
#[cfg(test)]
pub(crate) static MODEL_CATALOG_TEST_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Artifact {
    pub id: String, pub path: String, pub repo: String, pub revision: String,
    pub filename: String, pub sha256: String, pub bytes: u64,
    // Hash-bound local training exports; Hub downloads still use the original pin.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub compatible_local_sha256: Vec<String>,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Model {
    pub id: String, pub label: String, pub description: String, pub precision: String,
    pub context_tokens: u64, pub artifacts: Vec<String>, pub license: String,
    pub experimental: bool, pub note: String,
    pub selectable: bool,
    #[serde(default)] pub category: String,
    #[serde(default)] pub backend: String,
    #[serde(default="default_true")] pub runtime_ready: bool,
    #[serde(default="default_true")] pub installable: bool,
    #[serde(default,skip_serializing_if="Option::is_none")] pub source_url: Option<String>,
    #[serde(default,skip_serializing_if="Option::is_none")] pub setup_url: Option<String>,
    #[serde(default,skip_serializing_if="Option::is_none")] pub runtime_model_path: Option<String>,
    #[serde(default,skip_serializing_if="Option::is_none")] pub vision_projector_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub speech_language: Option<String>,
}
#[derive(Deserialize)]
struct Manifest { artifacts: Vec<Artifact>, models: Vec<Model> }
fn default_true()->bool {true}
pub fn gguf_model(id:&str)->Option<Model> {
    manifest().ok()?.models.into_iter().find(|model|model.id==id && model.selectable && model.backend=="gguf")
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelInfo { #[serde(flatten)] model: Model, installed: bool, external_managed: bool, download_bytes: u64, total_bytes: u64 }
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallProgress {
    pub model_id: String, pub phase: String, pub downloaded_bytes: u64,
    pub total_bytes: u64, pub current_file: String, pub error: Option<String>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Library { pub models: Vec<ModelInfo>, pub progress: Option<InstallProgress>, pub disk_free_bytes: u64, pub minimum_free_bytes: u64 }
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct FileReceipt { sha256: String, bytes: u64, modified_nanos: u128 }

fn manifest() -> Result<Manifest, String> {
    let data: Manifest = serde_json::from_str(include_str!("../resources/model-catalog.json")).map_err(|e| e.to_string())?;
    for file in &data.artifacts {
        if file.revision.len() != 40 || !file.revision.bytes().all(|c| c.is_ascii_hexdigit()) ||
           !valid_sha256(&file.sha256) || file.compatible_local_sha256.iter().any(|hash| !valid_sha256(hash)) || file.bytes == 0 {
            return Err(format!("Invalid pinned artifact {}", file.id));
        }
        safe_relative(&file.path)?; safe_relative(&file.filename)?;
        if file.repo.split('/').count() != 2 { return Err("Invalid Hub repository".into()); }
    }
    for model in &data.models {
        for path in model.runtime_model_path.iter().chain(model.vision_projector_path.iter()) {
            safe_relative(path)?;
            if !data.artifacts.iter().any(|file|file.path==*path && model.artifacts.contains(&file.id)){return Err(format!("Unpinned runtime path for {}",model.id));}
        }
    }
    Ok(data)
}
fn safe_relative(value: &str) -> Result<(), String> {
    if value.is_empty() || value.contains('\\') || value.contains(':') ||
        Path::new(value).components().any(|c| !matches!(c, Component::Normal(_))) {
        return Err(format!("Unsafe model path: {value}"));
    }
    Ok(())
}
pub(crate) fn safe_path(root: &Path, relative: &str) -> Result<PathBuf, String> {
    safe_relative(relative)?;
    let path = root.join(relative);
    for ancestor in path.ancestors() {
        if let Ok(metadata) = std::fs::symlink_metadata(ancestor) {
            if metadata.file_type().is_symlink() { return Err("Model path contains a symbolic link".into()); }
            #[cfg(windows)] {
                use std::os::windows::fs::MetadataExt;
                if metadata.file_attributes() & 0x400 != 0 { return Err("Model path contains a reparse point".into()); }
            }
        }
    }
    Ok(path)
}
fn modified(path: &Path) -> Result<u128, String> {
    std::fs::metadata(path).and_then(|m| m.modified()).map_err(|e| e.to_string())?
        .duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).map_err(|e| e.to_string())
}
fn file_receipt(root: &Path, file: &Artifact) -> PathBuf { root.join("models/receipts").join(format!("file-{}.json", file.id)) }
fn model_receipt(root: &Path, model: &Model) -> PathBuf { root.join("models/receipts").join(format!("model-{}.json", model.id)) }
fn complete_whisper_checkpoint(path: &Path) -> bool {
    let metadata = ["config.json", "generation_config.json", "preprocessor_config.json",
        "tokenizer_config.json", "normalizer.json", "special_tokens_map.json", "added_tokens.json"];
    metadata.iter().all(|name| path.join(name).is_file()) &&
        (path.join("model.safetensors").is_file() ||
            (path.join("model.safetensors.index.json").is_file() &&
                path.join("model-00001-of-00002.safetensors").is_file() &&
                path.join("model-00002-of-00002.safetensors").is_file())) &&
        (path.join("tokenizer.json").is_file() ||
            (path.join("vocab.json").is_file() && path.join("merges.txt").is_file()))
}
fn existing_large_v3_ct2(root: &Path) -> Option<PathBuf> {
    let path=root.join("speech/large-v3");
    let metadata=["config.json","preprocessor_config.json","tokenizer.json","vocabulary.json"];
    (metadata.iter().all(|name|path.join(name).is_file()) &&
        path.join("model.bin").metadata().is_ok_and(|m|m.len()==3087284237)).then_some(path)
}
fn externally_managed_speech(root: &Path, id: &str) -> bool {
    external_whisper_model_for(id).is_some() || (id == "whisper-large-v3" &&
        existing_large_v3_ct2(root).is_some() && !std::fs::read(root.join("models/receipts/model-whisper-large-v3.json")).ok()
            .and_then(|b|serde_json::from_slice::<Vec<String>>(&b).ok()).is_some_and(|ids|ids.iter().any(|id|id=="whisper-full-v3-model-bin")))
}
pub fn is_speech_model(id: &str) -> bool {
    matches!(id, "whisper-large-v3-turbo" | "whisper-large-v3" | "phonon-2")
}
pub fn external_whisper_model_for(id: &str) -> Option<PathBuf> {
    let layers = match id { "whisper-large-v3-turbo" => 4, "whisper-large-v3" => 32, _ => return None };
    let configured = std::env::var_os("OPENCORE_WHISPER_MODEL").map(PathBuf::from);
    let profile = std::env::var_os("USERPROFILE").map(PathBuf::from)
        .map(|home| home.join("OpenCore-Model-Test/asr").join(id));
    configured.into_iter().chain(profile).find(|path| {
        path.is_absolute() && complete_whisper_checkpoint(path) &&
        std::fs::read(path.join("config.json")).ok().and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
            .is_some_and(|config| config["model_type"] == "whisper" && config["decoder_layers"] == layers)
    })
}
pub fn speech_model_path(root: &Path, id: &str) -> Option<PathBuf> {
    match id {
        "whisper-large-v3-turbo" => Some(external_whisper_model_for(id).unwrap_or_else(|| root.join("speech/large-v3-turbo"))),
        "whisper-large-v3" => Some(external_whisper_model_for(id).or_else(||existing_large_v3_ct2(root)).unwrap_or_else(|| root.join("speech/large-v3"))),
        "phonon-2" => Some(root.join("speech/phonon-2")),
        _ => None,
    }
}
pub fn speech_python_path(root: &Path, id: &str) -> PathBuf {
    root.join(if id == "phonon-2" { "speech/phonon-venv/Scripts/python.exe" } else { "speech/whisper-venv/Scripts/python.exe" })
}
fn speech_runtime_ready(root: &Path, id: &str) -> bool {
    std::fs::read(root.join(if id == "phonon-2" { "speech/phonon-runtime.json" } else { "speech/whisper-runtime.json" })).ok()
        .and_then(|b|serde_json::from_slice::<serde_json::Value>(&b).ok()).is_some_and(|receipt|receipt["schema"]==2)
        && speech_python_path(root, id).is_file()
}
fn valid_model_receipt(root: &Path, model: &Model) -> bool {
    let read = |path: PathBuf| std::fs::read(path).ok()
        .and_then(|b| serde_json::from_slice::<Vec<String>>(&b).ok()).is_some_and(|ids| ids == model.artifacts);
    read(model_receipt(root, model)) || (model.id == "whisper-large-v3-turbo" &&
        read(root.join("models/receipts/model-whisper-large-v3.json")))
}
fn valid_sha256(hash: &str) -> bool {
    hash.len() == 64 && hash.bytes().all(|c| c.is_ascii_hexdigit())
}
fn accepted_local_hash(file: &Artifact, hash: &str) -> bool {
    hash == file.sha256 || file.compatible_local_sha256.iter().any(|approved| approved == hash)
}
fn verified_file(root: &Path, file: &Artifact) -> bool {
    let Ok(path) = safe_path(root, &file.path) else { return false; };
    let receipt = std::fs::read(file_receipt(root, file)).ok().and_then(|b| serde_json::from_slice::<FileReceipt>(&b).ok());
    let Some(receipt) = receipt else { return false; };
    accepted_local_hash(file, &receipt.sha256) && receipt.bytes == file.bytes &&
        std::fs::metadata(&path).map(|m| m.is_file() && m.len() == file.bytes).unwrap_or(false) &&
        modified(&path).ok() == Some(receipt.modified_nanos)
}
fn installed(root: &Path, model: &Model, data: &Manifest) -> bool {
    if !model.installable || model.artifacts.is_empty(){return false;}
    if is_speech_model(&model.id) {
        let complete = if model.id == "phonon-2" {
            root.join("speech/phonon-2/model.fermion").metadata().is_ok_and(|m|m.len() == 177438361)
        } else { externally_managed_speech(root,&model.id) };
        return speech_runtime_ready(root, &model.id) && (if model.id != "phonon-2" && complete { true } else {
            valid_model_receipt(root, model) && (model.id != "phonon-2" || complete) && model.artifacts.iter().all(|id|
                data.artifacts.iter().find(|f| &f.id == id).map(|f| verified_file(root, f)).unwrap_or(false))
        });
    }
    model_receipt(root, model).is_file() && model.artifacts.iter().all(|id|
        data.artifacts.iter().find(|f| &f.id == id).map(|f| verified_file(root, f)).unwrap_or(false))
}
pub fn require_installed(root: &Path, id: &str) -> Result<(), String> {
    let data = manifest()?;
    let model = data.models.iter().find(|m| m.id == id).ok_or("Unknown model")?;
    if installed(root, model, &data) { Ok(()) }
    else { Err(format!("{} is not installed. Open Models and choose Install.", model.label)) }
}
pub fn free_bytes(root: &Path) -> u64 {
    Disks::new_with_refreshed_list().list().iter().filter(|d| root.starts_with(d.mount_point()))
        .max_by_key(|d| d.mount_point().components().count()).map(|d| d.available_space()).unwrap_or(0)
}
fn reserve_space(root: &Path, additional: u64) -> Result<(), String> {
    let free = free_bytes(root);
    if free < MIN_FREE_BYTES.saturating_add(additional) {
        return Err(format!("Not enough disk space for this installation. Available: {:.2} GB; additional space needed: {:.2} GB, plus 64 MiB for installation metadata.", free as f64/1e9, additional as f64/1e9));
    }
    Ok(())
}
fn remaining_download_bytes(root: &Path, files: &[&Artifact]) -> Result<u64, String> {
    files.iter().filter(|file| !verified_file(root, file)).try_fold(0u64, |total, file| {
        let partial = safe_path(root, &format!("{}.partial", file.path))?;
        let offset = partial.metadata().ok().filter(|m| m.is_file() && m.len() <= file.bytes)
            .map(|m| m.len()).unwrap_or(0);
        Ok(total.saturating_add(file.bytes - offset))
    })
}
pub fn list(root: &Path) -> Result<Library, String> {
    let data = manifest()?;
    let models = data.models.iter().map(|m| -> Result<ModelInfo, String> {
        let files: Vec<_> = data.artifacts.iter().filter(|f| m.artifacts.contains(&f.id)).collect();
        let external_managed = externally_managed_speech(root, &m.id);
        Ok(ModelInfo { model: m.clone(), installed: installed(root, m, &data), external_managed,
            download_bytes: if external_managed { 0 } else { remaining_download_bytes(root, &files)? },
            total_bytes: files.iter().map(|f| f.bytes).sum() })
    }).collect::<Result<Vec<_>, _>>()?;
    Ok(Library { models, progress: PROGRESS.lock().map_err(|e| e.to_string())?.clone(),
        disk_free_bytes: free_bytes(root), minimum_free_bytes: MIN_FREE_BYTES })
}
fn update(phase: &str, bytes: u64, current_file: &str, error: Option<String>) {
    if let Ok(mut state) = PROGRESS.lock() {
        if let Some(p) = state.as_mut() {
            p.phase = phase.into(); p.downloaded_bytes = bytes; p.current_file = current_file.into(); p.error = error;
        }
    }
}
pub fn cancel() { CANCEL.store(true, Ordering::SeqCst); }
pub fn require_idle() -> Result<(), String> {
    if PROGRESS.lock().map_err(|e| e.to_string())?.as_ref().is_some_and(|p|
        matches!(p.phase.as_str(), "preparing" | "downloading" | "verifying" | "uninstalling")) {
        return Err("Finish the model installation before starting a runtime".into());
    }
    Ok(())
}
pub fn begin(id: &str) -> Result<(), String> {
    let data = manifest()?;
    let model = data.models.iter().find(|m| m.id == id).ok_or("Unknown model")?;
    if !model.installable || model.artifacts.is_empty(){return Err("This model needs upstream access or runtime setup. Open its Setup instructions in Models.".into());}
    let mut state = PROGRESS.lock().map_err(|e| e.to_string())?;
    if state.as_ref().is_some_and(|p| matches!(p.phase.as_str(), "downloading" | "verifying" | "preparing" | "uninstalling")) {
        return Err("Another model operation is in progress".into());
    }
    CANCEL.store(false, Ordering::SeqCst);
    let external_whisper = external_whisper_model_for(id).is_some();
    *state = Some(InstallProgress { model_id: id.into(), phase: "preparing".into(), downloaded_bytes: 0,
        total_bytes: if external_whisper { 0 } else { data.artifacts.iter().filter(|f| model.artifacts.contains(&f.id)).map(|f| f.bytes).sum() },
        current_file: String::new(), error: None });
    Ok(())
}
fn digest(path: &Path) -> Result<String, String> {
    let mut file = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let mut hash = Sha256::new(); let mut buffer = vec![0u8; 1024*1024];
    loop { let n = file.read(&mut buffer).map_err(|e| e.to_string())?; if n == 0 { break; } hash.update(&buffer[..n]); }
    Ok(format!("{:x}", hash.finalize()))
}
fn record_file(root: &Path, file: &Artifact, path: &Path) -> Result<(), String> {
    record_file_hash(root, file, path, &file.sha256)
}
fn record_file_hash(root: &Path, file: &Artifact, path: &Path, hash: &str) -> Result<(), String> {
    if !accepted_local_hash(file, hash) { return Err("Unapproved model checkpoint checksum".into()); }
    let receipt = FileReceipt { sha256: hash.into(), bytes: file.bytes, modified_nanos: modified(path)? };
    let target = safe_path(root, &format!("models/receipts/file-{}.json", file.id))?;
    std::fs::create_dir_all(target.parent().unwrap()).map_err(|e| e.to_string())?;
    std::fs::write(target, serde_json::to_vec(&receipt).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}
fn hub_token() -> Option<String> {
    std::env::var("HF_TOKEN").ok().filter(|s| !s.trim().is_empty()).or_else(|| {
        let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME"))?;
        let path = std::env::var_os("HF_TOKEN_PATH").map(PathBuf::from).unwrap_or_else(||
            std::env::var_os("HF_HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(home).join(".cache/huggingface")).join("token"));
        std::fs::read_to_string(path).ok().map(|t| t.trim().to_owned()).filter(|s| !s.is_empty())
    })
}
async fn install_inner(root: &Path, id: &str, resources: Option<&Path>) -> Result<(), String> {
    let data = manifest()?;
    let model = data.models.iter().find(|m| m.id == id).ok_or("Unknown model")?;
    let speech = is_speech_model(id);
    let external = externally_managed_speech(root, id);
    let files: Vec<_> = if external { Vec::new() } else { data.artifacts.iter().filter(|f| model.artifacts.contains(&f.id)).collect() };
    // Register verified existing downloads before calculating additional disk use.
    // This must work even when another task has brought free space below the download reserve.
    for file in &files {
        if verified_file(root,file) { continue; }
        let path=safe_path(root,&file.path)?;
        if path.metadata().is_ok_and(|m|m.is_file() && m.len()==file.bytes) {
            update("verifying",0,&file.path,None);
            let check=path.clone();
            let hash = tauri::async_runtime::spawn_blocking(move ||digest(&check)).await.map_err(|e|e.to_string())??;
            if accepted_local_hash(file, &hash) {
                record_file_hash(root,file,&path,&hash)?;
            }
        }
    }
    let shared_torch = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).is_some_and(|p|p.join("OpenCore/training-envs/lfm-bf16-py311/Scripts/python.exe").is_file());
    let runtime_reserve = if speech && !speech_runtime_ready(root, id) { if shared_torch {1_000_000_000} else {6_000_000_000} }
        else if id == "phonon-2" && !root.join("speech/phonon-2/model.fermion").is_file() { 177_438_361 } else { 0 };
    let additional=remaining_download_bytes(root, &files)?.saturating_add(runtime_reserve);
    if additional>0 { reserve_space(root,additional)?; }
    if speech && !speech_runtime_ready(root, id) {
        update("preparing", 0, "Preparing speech runtime", None);
        let root = root.to_path_buf();
        let script_name = if id == "phonon-2" { "prepare_phonon_runtime.py" } else { "prepare_runtime.py" };
        let setup_script = resources.map(|p|p.join("speech").join(script_name));
        let preparation = tauri::async_runtime::spawn_blocking(move || prepare_speech_runtime(&root, setup_script.as_deref(), script_name, false));
        preparation.await.map_err(|e| e.to_string())??;
    }
    if external {
        update("complete", 0, &format!("Using the existing {} checkpoint", model.label), None);
        return Ok(());
    }
    let client = reqwest::Client::builder().connect_timeout(std::time::Duration::from_secs(30))
        .timeout(std::time::Duration::from_secs(7200)).build().map_err(|e| e.to_string())?;
    let mut completed = 0u64;
    for file in files {
        if CANCEL.load(Ordering::SeqCst) { return Err("Installation cancelled".into()); }
        let path = safe_path(root, &file.path)?;
        if verified_file(root, file) { completed += file.bytes; continue; }
        if path.is_file() && path.metadata().map_err(|e| e.to_string())?.len() == file.bytes {
            update("verifying", completed, &file.path, None);
            let check = path.clone();
            let hash = tauri::async_runtime::spawn_blocking(move || digest(&check)).await.map_err(|e| e.to_string())??;
            if accepted_local_hash(file, &hash) {
                record_file_hash(root, file, &path, &hash)?; completed += file.bytes; continue;
            }
        }
        // Download to a sibling temporary file; no live weight is overwritten before verification.
        let partial = safe_path(root, &format!("{}.partial", file.path))?;
        std::fs::create_dir_all(partial.parent().unwrap()).map_err(|e| e.to_string())?;
        let mut offset = partial.metadata().map(|m| m.len()).unwrap_or(0);
        if offset > file.bytes { std::fs::remove_file(&partial).map_err(|e| e.to_string())?; offset = 0; }
        let url = format!("https://huggingface.co/{}/resolve/{}/{}", file.repo, file.revision, file.filename);
        let mut request = client.get(url);
        if let Some(token) = hub_token() { request = request.bearer_auth(token); }
        if offset > 0 { request = request.header(reqwest::header::RANGE, format!("bytes={offset}-")); }
        if offset < file.bytes {
            let pending = request.send();
            tokio::pin!(pending);
            let response = loop {
                tokio::select! {
                    response = &mut pending => break response.map_err(|e| format!("Download connection failed: {e}"))?,
                    _ = tokio::time::sleep(std::time::Duration::from_millis(250)) => {
                        if CANCEL.load(Ordering::SeqCst) { return Err("Installation cancelled; partial download retained for resume".into()); }
                    }
                }
            };
            if matches!(response.status().as_u16(), 401 | 403) {
                return Err("This model requires Hugging Face access. Sign in with hf auth login, then retry Install.".into());
            }
            let status = response.status();
            if !status.is_success() { return Err(format!("Model download returned HTTP {status}")); }
            if offset > 0 && status != reqwest::StatusCode::PARTIAL_CONTENT { offset = 0; }
            let mut output = std::fs::OpenOptions::new().write(true).create(true).append(offset > 0).truncate(offset == 0)
                .open(&partial).map_err(|e| e.to_string())?;
            // Recheck after a server ignores Range and the partial file is truncated.
            reserve_space(root, file.bytes - offset)?;
            let mut stream = response.bytes_stream();
            loop {
                let next = tokio::select! {
                    value = stream.next() => value,
                    _ = tokio::time::sleep(std::time::Duration::from_millis(250)) => {
                        if CANCEL.load(Ordering::SeqCst) { return Err("Installation cancelled; partial download retained for resume".into()); }
                        continue;
                    }
                };
                let Some(chunk) = next else { break; };
                if CANCEL.load(Ordering::SeqCst) { return Err("Installation cancelled; partial download retained for resume".into()); }
                let bytes = chunk.map_err(|e| format!("Download interrupted; retry to resume: {e}"))?;
                if offset.saturating_add(bytes.len() as u64) > file.bytes { return Err("Download exceeds pinned artifact length".into()); }
                reserve_space(root, bytes.len() as u64)?;
                output.write_all(&bytes).map_err(|e| e.to_string())?;
                offset += bytes.len() as u64;
                update("downloading", completed + offset, &file.path, None);
            }
            output.sync_all().map_err(|e| e.to_string())?;
        }
        if offset != file.bytes { return Err("Download is incomplete; retry Install to resume".into()); }
        update("verifying", completed + offset, &file.path, None);
        let check = partial.clone();
        if tauri::async_runtime::spawn_blocking(move || digest(&check)).await.map_err(|e| e.to_string())?? != file.sha256 {
            std::fs::remove_file(&partial).map_err(|e| e.to_string())?;
            return Err("Downloaded artifact failed SHA-256 verification; partial file removed".into());
        }
        if CANCEL.load(Ordering::SeqCst) { return Err("Installation cancelled".into()); }
        if path.exists() { std::fs::remove_file(&path).map_err(|e| e.to_string())?; }
        std::fs::rename(&partial, &path).map_err(|e| e.to_string())?;
        record_file(root, file, &path)?;
        completed += file.bytes;
    }
    if id == "phonon-2" {
        update("preparing", completed, "Preparing speech model", None);
        let setup = resources.map(|p| p.join("speech/prepare_phonon_runtime.py"));
        let root = root.to_path_buf();
        tauri::async_runtime::spawn_blocking(move || prepare_speech_runtime(&root, setup.as_deref(), "prepare_phonon_runtime.py", true)).await.map_err(|e| e.to_string())??;
    }
    let receipt = safe_path(root, &format!("models/receipts/model-{}.json", model.id))?;
    std::fs::create_dir_all(receipt.parent().unwrap()).map_err(|e| e.to_string())?;
    std::fs::write(receipt, serde_json::to_vec(&model.artifacts).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    update("complete", completed, "", None);
    Ok(())
}

fn prepare_speech_runtime(root: &Path, bundled_script: Option<&Path>, script_name: &str, prepare_model: bool) -> Result<(), String> {
    let development_script = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/speech").join(script_name);
    let script = bundled_script.filter(|p|p.is_file()).unwrap_or(&development_script);
    let candidates = [Some(root.join("speech/whisper-venv/Scripts/python.exe")),
        Some(root.join("speech/phonon-venv/Scripts/python.exe")),
        std::env::var_os("OPENCORE_SPEECH_TORCH_PYTHON").map(PathBuf::from),
        std::env::var_os("LOCALAPPDATA").map(|p|PathBuf::from(p).join("OpenCore/training-envs/lfm-bf16-py311/Scripts/python.exe"))];
    let python = candidates.into_iter().flatten().find(|p|p.is_file());
    let mut command = if let Some(python) = python {
        let mut cmd = std::process::Command::new(python);
        cmd.arg(&script);
        cmd
    } else {
        let mut cmd = std::process::Command::new("py");
        cmd.args(["-3.12", script.to_str().ok_or("Invalid speech setup path")?]);
        cmd
    };
    command.arg("--root").arg(root.join("speech"))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    if prepare_model { command.arg("--prepare-model"); }
    #[cfg(windows)] {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let output = command.output().map_err(|e| format!("Could not start Python for speech setup: {e}. Install Python 3.12 and retry."))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        let detail = detail.trim();
        return Err(if detail.is_empty() { "Speech runtime setup failed.".into() }
            else { format!("Speech runtime setup failed: {}", detail.chars().rev().take(500).collect::<String>().chars().rev().collect::<String>()) });
    }
    Ok(())
}
pub async fn install(root: PathBuf, id: String, resources: Option<PathBuf>) {
    if let Err(error) = install_inner(&root, &id, resources.as_deref()).await { update("failed", 0, "", Some(error)); }
}
pub fn removal_plan(root: &Path, id: &str) -> Result<RemovalPlan, String> {
    require_idle()?;
    let data = manifest()?;
    removal::plan(root, id, &data)
}
pub fn uninstall(root: &Path, id: &str, confirmation_token: &str) -> Result<(), String> {
    removal::remove(root, id, &manifest()?, confirmation_token)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn approved_local_checkpoint_remains_installed_without_redownload() {
        let root = std::env::temp_dir().join(format!("opencore-local-checkpoint-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join("models/receipts")).unwrap();
        let original = format!("{:x}", Sha256::digest(b"hello"));
        let trained = format!("{:x}", Sha256::digest(b"world"));
        let file: Artifact = serde_json::from_value(serde_json::json!({
            "id":"opencore-apex", "path":"OpenCore-Code-Single-File.gguf", "repo":"test/model",
            "revision":"a".repeat(40), "filename":"model.gguf", "sha256":original, "bytes":5,
            "compatibleLocalSha256":[trained]
        })).unwrap();
        let path = root.join(&file.path);
        std::fs::write(&path, b"world").unwrap();
        record_file_hash(&root, &file, &path, &trained).unwrap();
        let receipt: FileReceipt = serde_json::from_slice(&std::fs::read(file_receipt(&root, &file)).unwrap()).unwrap();
        assert_eq!(receipt.sha256, trained, "Record the actual trained hash, never the original hash");
        let mut data = manifest().unwrap();
        data.models.retain(|model| model.id == "echo");
        data.models[0].artifacts = vec![file.id.clone()];
        data.artifacts = vec![file.clone()];
        std::fs::write(model_receipt(&root, &data.models[0]), b"[\"opencore-apex\"]").unwrap();
        assert!(installed(&root, &data.models[0], &data), "Approved trained weights must remain selectable");
        assert_eq!(remaining_download_bytes(&root, &[&file]).unwrap(), 0, "Do not replace a selected trained checkpoint");
        std::fs::write(&path, b"changed").unwrap();
        assert!(!verified_file(&root, &file), "Changed checkpoint bytes must invalidate the receipt");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_checkpoint_receipt_still_requires_exact_hash_and_fresh_metadata() {
        let root = std::env::temp_dir().join(format!("opencore-local-checkpoint-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join("models/receipts")).unwrap();
        let file: Artifact = serde_json::from_value(serde_json::json!({
            "id":"opencore-apex", "path":"OpenCore-Code-Single-File.gguf", "repo":"test/model",
            "revision":"a".repeat(40), "filename":"model.gguf", "sha256":format!("{:x}", Sha256::digest(b"hello")), "bytes":5,
            "compatibleLocalSha256":[format!("{:x}", Sha256::digest(b"world"))]
        })).unwrap();
        let path = root.join(&file.path);
        std::fs::write(&path, b"world").unwrap();
        let mut receipt = FileReceipt { sha256: "c".repeat(64), bytes: 5, modified_nanos: modified(&path).unwrap() };
        std::fs::write(file_receipt(&root, &file), serde_json::to_vec(&receipt).unwrap()).unwrap();
        assert!(!verified_file(&root, &file), "A receipt alone must not authorize an unlisted checksum");
        assert!(record_file_hash(&root, &file, &path, &receipt.sha256).is_err(), "Registration must reject unknown hashes too");
        receipt.sha256 = format!("{:x}", Sha256::digest(b"world"));
        receipt.modified_nanos -= 1;
        std::fs::write(file_receipt(&root, &file), serde_json::to_vec(&receipt).unwrap()).unwrap();
        assert!(!verified_file(&root, &file), "Stale metadata must not authorize a local checkpoint");
        std::fs::write(&path, b"hello").unwrap();
        record_file(&root, &file, &path).unwrap();
        assert!(verified_file(&root, &file), "Existing pinned checkpoint receipts remain supported");
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn local_ultradata_checkpoint_does_not_change_the_hub_download_pin() {
        let catalog = manifest().unwrap();
        let echo = catalog.artifacts.iter().find(|file| file.id == "opencore-apex").unwrap();
        assert_eq!(echo.sha256, "261ef6c572bf9916f9ea5097bc156da0ee0ef6d631d52cf59dbcf293f416b7ae");
        assert_eq!(echo.compatible_local_sha256, ["4551c5333bb6287f0222e15a4d1e3a969df04cb7a69833125f5b3aa80239b91a"]);
        assert!(catalog.artifacts.iter().filter(|file| file.id != echo.id).all(|file| file.compatible_local_sha256.is_empty()));
    }
    #[test]
    fn platform_categories_have_real_pins_and_setup_only_entries_cannot_run() {
        let catalog=manifest().unwrap();
        for category in ["speech","text","computer-use","3d"] {
            assert!(catalog.models.iter().filter(|model|model.category==category).count()>=3,"{category}");
        }
        for model in &catalog.models {
            assert!(model.artifacts.iter().all(|id|catalog.artifacts.iter().any(|file|file.id==*id)),"{}",model.id);
            if !model.installable {assert!(!model.runtime_ready && !model.selectable,"{}",model.id);}
        }
        for id in ["swift-27b","dirk-27b","davidau-27b"] {
            let model=gguf_model(id).unwrap();
            assert!(model.runtime_model_path.is_some());
            assert!(model.context_tokens<=16_384);
        }
        for id in ["thinkingcap-27b","hy-motion-1","pixal3d"] {assert!(gguf_model(id).is_none());}
        let hy=catalog.models.iter().find(|model|model.id=="hy-motion-1").unwrap();
        assert_eq!(hy.category,"3d-animation");
        assert!(!hy.runtime_ready);
        assert!(catalog.artifacts.iter().filter(|file|hy.artifacts.contains(&file.id)).all(|file|file.repo=="tencent/HY-Motion-1.0"));
    }
    #[tokio::test]
    #[ignore = "Explicit opt-in only; registers and verifies already-present speech checkpoints"]
    async fn register_existing_speech_models() {
        let _test_guard = MODEL_CATALOG_TEST_LOCK.lock().unwrap();
        let root = PathBuf::from(std::env::var_os("OPENCORE_REGISTER_EXISTING_ROOT").expect("Set explicit model root"));
        assert!(root.is_absolute() && root.is_dir());
        let data = manifest().unwrap();
        for id in ["whisper-large-v3-turbo", "whisper-large-v3", "phonon-2"] {
            let model = data.models.iter().find(|m|m.id==id).unwrap();
            let external = external_whisper_model_for(id);
            for file in data.artifacts.iter().filter(|f|model.artifacts.contains(&f.id)) {
                let path = external.as_ref().map(|p|p.join(&file.filename)).unwrap_or_else(||safe_path(&root,&file.path).unwrap());
                assert_eq!(path.metadata().unwrap().len(), file.bytes, "No files may be downloaded by this check: {}", file.path);
                assert_eq!(digest(&path).unwrap(),file.sha256,"Existing speech file must match its exact pin: {}",file.path);
            }
            assert!(speech_runtime_ready(&root,id));
            begin(id).unwrap();
            install_inner(&root,id,None).await.unwrap();
            require_installed(&root,id).unwrap();
            println!("Verified installed speech model: {}",model.label);
        }
    }
    #[test]
    fn speech_profiles_have_distinct_checkpoints_and_explicit_language_support() {
        let data = manifest().unwrap();
        let turbo=data.models.iter().find(|m|m.id=="whisper-large-v3-turbo").unwrap();
        let full=data.models.iter().find(|m|m.id=="whisper-large-v3").unwrap();
        let phonon=data.models.iter().find(|m|m.id=="phonon-2").unwrap();
        assert_eq!(turbo.speech_language.as_deref(),Some("Multilingual"));
        assert_eq!(full.speech_language.as_deref(),Some("Multilingual"));
        assert_eq!(phonon.speech_language.as_deref(),Some("English only"));
        assert!(turbo.artifacts.iter().all(|id|!full.artifacts.contains(id)));
        assert!(data.artifacts.iter().filter(|f|full.artifacts.contains(&f.id)).all(|f|f.repo=="Systran/faster-whisper-large-v3"));
        let root=std::env::temp_dir().join(format!("opencore-speech-receipt-{}",uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join("models/receipts")).unwrap();
        std::fs::write(model_receipt(&root,full),serde_json::to_vec(&turbo.artifacts).unwrap()).unwrap();
        assert!(valid_model_receipt(&root,turbo));
        assert!(!valid_model_receipt(&root,full));
        std::fs::remove_dir_all(root).unwrap();
    }
    #[tokio::test]
    #[ignore = "Explicit opt-in only; verifies and registers already-present user-selected model files"]
    async fn register_existing_requested_models() {
        let _test_guard = MODEL_CATALOG_TEST_LOCK.lock().unwrap();
        let root = PathBuf::from(std::env::var_os("OPENCORE_REGISTER_EXISTING_ROOT")
            .expect("Set OPENCORE_REGISTER_EXISTING_ROOT explicitly"));
        let ids = std::env::var("OPENCORE_REGISTER_EXISTING_IDS")
            .expect("Set OPENCORE_REGISTER_EXISTING_IDS explicitly");
        assert!(root.is_absolute() && root.is_dir());
        let data = manifest().unwrap();
        for id in ids.split(',') {
            assert!(matches!(id, "doucode" | "fusioncore-kv"), "This check only registers the requested models");
            let model = data.models.iter().find(|m| m.id == id).unwrap();
            for file in data.artifacts.iter().filter(|f| model.artifacts.contains(&f.id)) {
                let path = safe_path(&root, &file.path).unwrap();
                assert_eq!(path.metadata().unwrap().len(), file.bytes, "No files will be downloaded by this check");
                assert_eq!(digest(&path).unwrap(), file.sha256, "Existing model must match its exact pin");
            }
            begin(id).unwrap();
            install_inner(&root, id, None).await.unwrap();
            require_installed(&root, id).unwrap();
            println!("Verified existing model installed: {}", model.label);
        }
    }

    #[test] fn catalog_is_pinned_and_paths_cannot_escape() {
        let catalog = manifest().unwrap();
        assert_eq!(catalog.models.iter().filter(|m| m.id.starts_with("dualcore") || m.id.starts_with("fusioncore")).count(), 4);
        for path in ["../model.gguf", "C:/model.gguf", "/model.gguf", "models\\model.gguf"] { assert!(safe_relative(path).is_err()); }
        for model in &catalog.models { for id in &model.artifacts { assert!(catalog.artifacts.iter().any(|f| &f.id == id)); } }
    }
    #[test] fn model_installation_allows_a_download_that_fits_on_disk() {
        let root = std::env::temp_dir();
        let free = free_bytes(&root);
        assert!(free > 4_000_000_000, "This test needs 4 GB free; it writes no data");
        assert!(reserve_space(&root, free - 4_000_000_000).is_ok(),
            "A download leaving 4 GB free must not require an unrelated 100 GB reserve");
        assert!(reserve_space(&root, free).is_err(), "Still reject downloads that would fill the disk");
    }
    #[test] fn resumed_download_space_excludes_partial_and_verified_shared_files() {
        let root = std::env::temp_dir().join(format!("opencore-download-space-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join("models")).unwrap();
        let file = Artifact { id: "space-test".into(), path: "models/weights.gguf".into(), repo: "test/model".into(),
            revision: "a".repeat(40), filename: "weights.gguf".into(), sha256: format!("{:x}", Sha256::digest(b"hello")), bytes: 5,
            compatible_local_sha256: Vec::new() };
        let partial = root.join("models/weights.gguf.partial");
        std::fs::write(&partial, b"hel").unwrap();
        assert_eq!(remaining_download_bytes(&root, &[&file]).unwrap(), 2);
        std::fs::write(&partial, b"oversized").unwrap();
        assert_eq!(remaining_download_bytes(&root, &[&file]).unwrap(), 5);
        let path = root.join(&file.path);
        std::fs::write(&path, b"hello").unwrap();
        record_file(&root, &file, &path).unwrap();
        assert_eq!(remaining_download_bytes(&root, &[&file]).unwrap(), 0);
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test] fn echo_profiles_are_selectable_with_incremental_kv() {
        let catalog = manifest().unwrap();
        for id in ["dualcore-echo", "fusioncore-echo"] {
            let model = catalog.models.iter().find(|model| model.id == id).unwrap();
            assert!(model.selectable, "{id} must remain selectable as an interactive chat model");
            assert!(model.note.contains("incremental F16 KV"));
            assert_eq!(model.context_tokens, 131_072, "{id} must show the runtime's actual rolling window");
            assert!(model.note.contains("131,072-token rolling context"), "{id} must distinguish ECHO archive from active attention");
        }
        for id in ["dualcore-kv", "fusioncore-kv"] {
            let model = catalog.models.iter().find(|model| model.id == id).unwrap();
            assert!(model.selectable);
            assert!(!model.note.contains("ECHO archive"));
        }
    }
    #[test] fn standalone_nanbeige_profiles_share_the_pinned_bf16_checkpoint() {
        let catalog = manifest().unwrap();
        let standard = catalog.models.iter().find(|model| model.id == "nanbeige-bf16").unwrap();
        let echo = catalog.models.iter().find(|model| model.id == "nanbeige-bf16-echo").unwrap();
        assert!(standard.selectable && echo.selectable);
        assert_eq!(standard.precision, "BF16");
        assert_eq!(echo.precision, "BF16");
        assert_eq!(standard.context_tokens, 262_144);
        assert_eq!(echo.context_tokens, 262_144);
        assert_eq!(standard.artifacts, echo.artifacts);
        let artifact = catalog.artifacts.iter().find(|artifact| artifact.id == "nanbeige-bf16-gguf").unwrap();
        assert_eq!(artifact.repo, "bartowski/Nanbeige_Nanbeige4.2-3B-GGUF");
        assert_eq!(artifact.filename, "Nanbeige_Nanbeige4.2-3B-bf16.gguf");
        assert_eq!(artifact.bytes, 8_343_845_760);
        assert_eq!(artifact.sha256, "f0802842ea97d02ed028ce93db7b10e7efc69c650c399046d942c28ed507df44");
    }
    #[test] fn removing_one_variant_preserves_shared_weights_and_history() {
        let _test_guard = MODEL_CATALOG_TEST_LOCK.lock().unwrap();
        let root = std::env::temp_dir().join(format!("opencore-catalog-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let mut data = manifest().unwrap();
        let shared = data.artifacts.iter_mut().find(|f| f.id == "lfm-q8").unwrap();
        shared.bytes = 5; shared.sha256 = format!("{:x}", Sha256::digest(b"hello"));
        let first = data.models.iter().find(|m| m.id == "dualcore-kv").unwrap();
        let file = data.artifacts.iter().find(|f| first.artifacts.contains(&f.id)).unwrap();
        let small = file.clone();
        let path = safe_path(&root, &small.path).unwrap();
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, b"hello").unwrap();
        let history = root.join("history.json"); std::fs::write(&history, b"keep").unwrap();
        record_file(&root, &small, &path).unwrap();
        assert!(verified_file(&root, file));
        for id in ["dualcore-kv", "fusioncore-kv"] {
            let model = data.models.iter().find(|m| m.id == id).unwrap();
            std::fs::write(model_receipt(&root, model), serde_json::to_vec(&model.artifacts).unwrap()).unwrap();
        }
        let plan = removal::plan(&root, "dualcore-kv", &data).unwrap();
        assert!(plan.retained_files.iter().any(|file| Path::new(&file.path) == path));
        removal::remove(&root, "dualcore-kv", &data, &plan.confirmation_token).unwrap();
        assert!(path.exists());
        assert!(installed(&root, data.models.iter().find(|m| m.id == "fusioncore-kv").unwrap(), &data));
        let plan = removal::plan(&root, "fusioncore-kv", &data).unwrap();
        removal::remove(&root, "fusioncore-kv", &data, &plan.confirmation_token).unwrap();
        assert!(!path.exists()); assert_eq!(std::fs::read(history).unwrap(), b"keep");
        std::fs::remove_dir_all(root).unwrap();
    }
}
