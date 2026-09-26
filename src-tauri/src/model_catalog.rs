//! Optional, immutable model downloads. No weight file is bundled or auto-installed.
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::{Mutex, atomic::{AtomicBool, Ordering}};
use sysinfo::Disks;

const MIN_FREE_BYTES: u64 = 200_000_000_000;
static CANCEL: AtomicBool = AtomicBool::new(false);
static PROGRESS: Mutex<Option<InstallProgress>> = Mutex::new(None);

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Artifact {
    pub id: String, pub path: String, pub repo: String, pub revision: String,
    pub filename: String, pub sha256: String, pub bytes: u64,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Model {
    pub id: String, pub label: String, pub description: String, pub precision: String,
    pub context_tokens: u64, pub artifacts: Vec<String>, pub license: String,
    pub experimental: bool, pub note: String,
    pub selectable: bool,
}
#[derive(Deserialize)]
struct Manifest { artifacts: Vec<Artifact>, models: Vec<Model> }
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelInfo { #[serde(flatten)] model: Model, installed: bool, download_bytes: u64, total_bytes: u64 }
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
           file.sha256.len() != 64 || !file.sha256.bytes().all(|c| c.is_ascii_hexdigit()) || file.bytes == 0 {
            return Err(format!("Invalid pinned artifact {}", file.id));
        }
        safe_relative(&file.path)?; safe_relative(&file.filename)?;
        if file.repo.split('/').count() != 2 { return Err("Invalid Hub repository".into()); }
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
fn safe_path(root: &Path, relative: &str) -> Result<PathBuf, String> {
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
fn verified_file(root: &Path, file: &Artifact) -> bool {
    let Ok(path) = safe_path(root, &file.path) else { return false; };
    let receipt = std::fs::read(file_receipt(root, file)).ok().and_then(|b| serde_json::from_slice::<FileReceipt>(&b).ok());
    let Some(receipt) = receipt else { return false; };
    receipt.sha256 == file.sha256 && receipt.bytes == file.bytes &&
        std::fs::metadata(&path).map(|m| m.is_file() && m.len() == file.bytes).unwrap_or(false) &&
        modified(&path).ok() == Some(receipt.modified_nanos)
}
fn installed(root: &Path, model: &Model, data: &Manifest) -> bool {
    model_receipt(root, model).is_file() && model.artifacts.iter().all(|id|
        data.artifacts.iter().find(|f| &f.id == id).map(|f| verified_file(root, f)).unwrap_or(false))
}
pub fn free_bytes(root: &Path) -> u64 {
    Disks::new_with_refreshed_list().list().iter().filter(|d| root.starts_with(d.mount_point()))
        .max_by_key(|d| d.mount_point().components().count()).map(|d| d.available_space()).unwrap_or(0)
}
fn reserve_space(root: &Path, additional: u64) -> Result<(), String> {
    let free = free_bytes(root);
    if free < MIN_FREE_BYTES.saturating_add(additional).saturating_add(64 * 1024 * 1024) {
        return Err(format!("Installation would leave less than 200 GB free. Available: {:.1} GB; remaining download: {:.1} GB.", free as f64/1e9, additional as f64/1e9));
    }
    Ok(())
}
pub fn list(root: &Path) -> Result<Library, String> {
    let data = manifest()?;
    let models = data.models.iter().map(|m| {
        let files: Vec<_> = data.artifacts.iter().filter(|f| m.artifacts.contains(&f.id)).collect();
        ModelInfo { model: m.clone(), installed: installed(root, m, &data),
            download_bytes: files.iter().filter(|f| !verified_file(root, f)).map(|f| f.bytes).sum(),
            total_bytes: files.iter().map(|f| f.bytes).sum() }
    }).collect();
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
    let mut state = PROGRESS.lock().map_err(|e| e.to_string())?;
    if state.as_ref().is_some_and(|p| matches!(p.phase.as_str(), "downloading" | "verifying" | "preparing" | "uninstalling")) {
        return Err("Another model operation is in progress".into());
    }
    CANCEL.store(false, Ordering::SeqCst);
    *state = Some(InstallProgress { model_id: id.into(), phase: "preparing".into(), downloaded_bytes: 0,
        total_bytes: data.artifacts.iter().filter(|f| model.artifacts.contains(&f.id)).map(|f| f.bytes).sum(),
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
    let receipt = FileReceipt { sha256: file.sha256.clone(), bytes: file.bytes, modified_nanos: modified(path)? };
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
async fn install_inner(root: &Path, id: &str) -> Result<(), String> {
    let data = manifest()?;
    let model = data.models.iter().find(|m| m.id == id).ok_or("Unknown model")?;
    let files: Vec<_> = data.artifacts.iter().filter(|f| model.artifacts.contains(&f.id)).collect();
    reserve_space(root, files.iter().filter(|f| !verified_file(root, f)).map(|f| f.bytes).sum())?;
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
            if tauri::async_runtime::spawn_blocking(move || digest(&check)).await.map_err(|e| e.to_string())?? == file.sha256 {
                record_file(root, file, &path)?; completed += file.bytes; continue;
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
    let receipt = safe_path(root, &format!("models/receipts/model-{}.json", model.id))?;
    std::fs::create_dir_all(receipt.parent().unwrap()).map_err(|e| e.to_string())?;
    std::fs::write(receipt, serde_json::to_vec(&model.artifacts).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    update("complete", completed, "", None);
    Ok(())
}
pub async fn install(root: PathBuf, id: String) {
    if let Err(error) = install_inner(&root, &id).await { update("failed", 0, "", Some(error)); }
}
pub fn uninstall(root: &Path, id: &str) -> Result<(), String> {
    let data = manifest()?;
    uninstall_inner(root, id, &data)
}
fn uninstall_inner(root: &Path, id: &str, data: &Manifest) -> Result<(), String> {
    let model = data.models.iter().find(|m| m.id == id).ok_or("Unknown model")?;
    begin(id)?;
    update("uninstalling", 0, "", None);
    let result: Result<(), String> = (|| {
        let used: HashSet<_> = data.models.iter().filter(|m| m.id != id && installed(root, m, &data))
            .flat_map(|m| m.artifacts.iter().cloned()).collect();
        // Exact allowlisted files only. Conversations, archives, and unrelated models are untouched.
        for file in data.artifacts.iter().filter(|f| model.artifacts.contains(&f.id) && !used.contains(&f.id)) {
            for relative in [&file.path, &format!("{}.partial", file.path), &format!("models/receipts/file-{}.json", file.id)] {
                let path = safe_path(root, relative)?;
                if path.is_file() { std::fs::remove_file(path).map_err(|e| e.to_string())?; }
            }
        }
        let receipt = safe_path(root, &format!("models/receipts/model-{}.json", id))?;
        if receipt.is_file() { std::fs::remove_file(receipt).map_err(|e| e.to_string())?; }
        Ok(())
    })();
    match &result { Ok(()) => update("complete", 0, "", None), Err(error) => update("failed", 0, "", Some(error.clone())) }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn catalog_is_pinned_and_paths_cannot_escape() {
        let catalog = manifest().unwrap();
        assert_eq!(catalog.models.iter().filter(|m| m.id.starts_with("dualcore") || m.id.starts_with("fusioncore")).count(), 4);
        for path in ["../model.gguf", "C:/model.gguf", "/model.gguf", "models\\model.gguf"] { assert!(safe_relative(path).is_err()); }
        for model in &catalog.models { for id in &model.artifacts { assert!(catalog.artifacts.iter().any(|f| &f.id == id)); } }
    }
    #[test] fn removing_one_variant_preserves_shared_weights_and_history() {
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
        uninstall_inner(&root, "dualcore-kv", &data).unwrap();
        assert!(path.exists());
        assert!(installed(&root, data.models.iter().find(|m| m.id == "fusioncore-kv").unwrap(), &data));
        uninstall_inner(&root, "fusioncore-kv", &data).unwrap();
        assert!(!path.exists()); assert_eq!(std::fs::read(history).unwrap(), b"keep");
        std::fs::remove_dir_all(root).unwrap();
    }
}
