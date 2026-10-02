//! Existing YuE installations keep ownership of their checkpoints and songs.
use crate::model_catalog::Artifact;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    io::Read,
    path::{Path, PathBuf},
};
#[derive(Serialize, Deserialize)]
struct Receipt {
    path: PathBuf,
    sha256: String,
    bytes: u64,
    modified: u128,
}
fn files() -> Vec<Artifact> {
    serde_json::from_str::<serde_json::Value>(include_str!("../resources/model-catalog.json"))
        .unwrap()["artifacts"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|v| {
            v["repo"]
                .as_str()
                .is_some_and(|r| r.starts_with("m-a-p/YuE2-"))
        })
        .filter_map(|v| serde_json::from_value(v.clone()).ok())
        .collect()
}
fn relative(file: &Artifact) -> &str {
    file.path.strip_prefix("models/music/").unwrap()
}
fn stamp(path: &Path) -> Option<u128> {
    path.metadata()
        .ok()?
        .modified()
        .ok()?
        .duration_since(std::time::UNIX_EPOCH)
        .ok()
        .map(|t| t.as_nanos())
}
pub fn external_dir() -> Option<PathBuf> {
    let home = PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default());
    [
        std::env::var_os("OPENCORE_YUE_MODELS").map(PathBuf::from),
        Some(crate::music_studio::root().join("models")),
        Some(home.join("YuE/models")),
        #[cfg(windows)]
        Some(PathBuf::from(
            r"\\wsl.localhost\Ubuntu-24.04\root\yue2\models",
        )),
    ]
    .into_iter()
    .flatten()
    .find(|dir| {
        let files = files();
        !files.is_empty()
            && files.iter().all(|file| {
                dir.join(relative(file))
                    .metadata()
                    .is_ok_and(|m| m.is_file() && m.len() == file.bytes)
            })
    })
}
fn receipt(root: &Path) -> PathBuf {
    root.join("models/receipts/music-external.json")
}
pub fn registered(root: &Path) -> bool {
    let Ok(bytes) = std::fs::read(receipt(root)) else {
        return false;
    };
    let Ok(records) = serde_json::from_slice::<BTreeMap<String, Receipt>>(&bytes) else {
        return false;
    };
    let files = files();
    !files.is_empty()
        && files.iter().all(|file| {
            records.get(&file.id).is_some_and(|r| {
                r.sha256 == file.sha256
                    && r.bytes == file.bytes
                    && stamp(&r.path) == Some(r.modified)
                    && r.path
                        .metadata()
                        .is_ok_and(|m| m.is_file() && m.len() == r.bytes)
            })
        })
}
pub fn registered_dir(root: &Path) -> Option<PathBuf> {
    if !registered(root) {
        return None;
    }
    let records: BTreeMap<String, Receipt> =
        serde_json::from_slice(&std::fs::read(receipt(root)).ok()?).ok()?;
    records
        .values()
        .next()?
        .path
        .parent()?
        .parent()
        .map(Path::to_path_buf)
}
pub fn register(root: &Path, dir: &Path) -> Result<(), String> {
    let mut records = BTreeMap::new();
    for file in files() {
        let path = dir.join(relative(&file));
        let before = stamp(&path).ok_or("Music checkpoint is missing")?;
        let mut input = std::fs::File::open(&path).map_err(|e| e.to_string())?;
        let mut hash = Sha256::new();
        let mut buffer = vec![0u8; 1024 * 1024];
        let mut bytes = 0u64;
        loop {
            if crate::model_catalog::install_cancelled() {
                return Err("Installation cancelled; existing music files retained".into());
            }
            let n = input.read(&mut buffer).map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            hash.update(&buffer[..n]);
            bytes += n as u64;
        }
        let sha256 = format!("{:x}", hash.finalize());
        if sha256 != file.sha256 || bytes != file.bytes || stamp(&path) != Some(before) {
            return Err(format!(
                "Existing music file failed pinned verification: {}",
                path.display()
            ));
        }
        records.insert(
            file.id,
            Receipt {
                path,
                sha256,
                bytes,
                modified: before,
            },
        );
    }
    let target = receipt(root);
    std::fs::create_dir_all(target.parent().unwrap()).map_err(|e| e.to_string())?;
    let pending = target.with_extension("pending");
    std::fs::write(
        &pending,
        serde_json::to_vec(&records).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    if target.exists() {
        std::fs::remove_file(&target).map_err(|e| e.to_string())?;
    }
    std::fs::rename(pending, target).map_err(|e| e.to_string())
}
pub fn external_files(root: &Path) -> Vec<PathBuf> {
    std::fs::read(receipt(root))
        .ok()
        .and_then(|b| serde_json::from_slice::<BTreeMap<String, Receipt>>(&b).ok())
        .unwrap_or_default()
        .into_values()
        .map(|r| r.path)
        .collect()
}
pub fn models_path(root: &Path) -> PathBuf {
    registered_dir(root).unwrap_or_else(|| root.join("models/music"))
}
