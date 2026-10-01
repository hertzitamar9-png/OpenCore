//! Review and delete exact model files; never recursively delete a model directory.
use super::*;
use std::collections::BTreeMap;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RemovalFile {
    pub path: String,
    pub bytes: u64,
    pub external: bool,
    pub shared_with: Vec<String>,
    modified_nanos: u128,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RemovalPlan {
    pub model_id: String,
    pub label: String,
    pub files: Vec<RemovalFile>,
    pub retained_files: Vec<RemovalFile>,
    pub total_bytes: u64,
    pub confirmation_token: String,
}

fn checked_file(path: &Path, external: bool, shared_with: Vec<String>) -> Result<Option<RemovalFile>, String> {
    if !path.is_absolute() { return Err("Model deletion requires an absolute file path".into()); }
    let parent = path.parent().ok_or("Model file has no parent")?;
    let name = path.file_name().and_then(|s| s.to_str()).ok_or("Invalid model filename")?;
    let path = safe_path(parent, name)?;
    match std::fs::metadata(&path) {
        Ok(metadata) if metadata.is_file() => Ok(Some(RemovalFile { path: path.to_string_lossy().into_owned(),
            bytes: metadata.len(), external, shared_with, modified_nanos: modified(&path)? })),
        Ok(_) => Err(format!("Expected a model file: {}", path.display())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

fn whisper_files(directory: &Path) -> Vec<PathBuf> {
    // Only checkpoint filenames accepted by OpenCore's Whisper loader. No directory recursion.
    ["model.safetensors", "model.safetensors.index.json", "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors", "model.bin", "config.json", "generation_config.json",
        "preprocessor_config.json", "tokenizer_config.json", "normalizer.json", "special_tokens_map.json",
        "added_tokens.json", "tokenizer.json", "vocab.json", "merges.txt", "vocabulary.json"]
        .iter().map(|name| directory.join(name)).collect()
}

fn model_files(root: &Path, model: &Model, data: &Manifest) -> Result<Vec<(PathBuf, bool)>, String> {
    let mut files = Vec::new();
    for artifact in data.artifacts.iter().filter(|f| model.artifacts.contains(&f.id)) {
        files.push((safe_path(root, &artifact.path)?, false));
        files.push((safe_path(root, &format!("{}.partial", artifact.path))?, false));
        files.push((safe_path(root, &format!("models/receipts/file-{}.json", artifact.id))?, false));
    }
    if model.id == "phonon-2" { files.push((safe_path(root, "speech/phonon-2/model.fermion")?, false)); }
    if let Some(external) = external_whisper_model_for(&model.id) {
        files.extend(whisper_files(&external).into_iter().map(|path| (path, true)));
    }
    if model.id == "whisper-large-v3" && existing_large_v3_ct2(root).is_some() {
        files.extend(whisper_files(&root.join("speech/large-v3")).into_iter().map(|path| (path, false)));
    }
    Ok(files)
}

pub(super) fn plan(root: &Path, id: &str, data: &Manifest) -> Result<RemovalPlan, String> {
    let model = data.models.iter().find(|m| m.id == id).ok_or("Unknown model")?;
    let mut shared: BTreeMap<PathBuf, Vec<String>> = BTreeMap::new();
    for other in data.models.iter().filter(|m| m.id != id && installed(root, m, data)) {
        for (path, _) in model_files(root, other, data)? {
            shared.entry(path).or_default().push(other.label.clone());
        }
    }
    let mut candidates: BTreeMap<PathBuf, bool> = model_files(root, model, data)?.into_iter().collect();
    candidates.insert(safe_path(root, &format!("models/receipts/model-{}.json", model.id))?, false);
    // Older releases registered Turbo under the full-v3 receipt. Include only that exact legacy receipt.
    if model.id == "whisper-large-v3-turbo" {
        let legacy = safe_path(root, "models/receipts/model-whisper-large-v3.json")?;
        if std::fs::read(&legacy).ok().and_then(|b| serde_json::from_slice::<Vec<String>>(&b).ok())
            .is_some_and(|ids| ids == model.artifacts) { candidates.insert(legacy, false); }
    }
    let mut files = Vec::new();
    let mut retained_files = Vec::new();
    for (path, external) in candidates {
        let users = shared.get(&path).cloned().unwrap_or_default();
        if let Some(file) = checked_file(&path, external, users)? {
            if file.shared_with.is_empty() { files.push(file); } else { retained_files.push(file); }
        }
    }
    let total_bytes = files.iter().map(|file| file.bytes).sum();
    let identity = serde_json::to_vec(&(id, &files, &retained_files)).map_err(|e| e.to_string())?;
    let confirmation_token = format!("{:x}", Sha256::digest(identity));
    Ok(RemovalPlan { model_id: id.into(), label: model.label.clone(), files, retained_files, total_bytes, confirmation_token })
}

pub(super) fn remove(root: &Path, id: &str, data: &Manifest, confirmation_token: &str) -> Result<(), String> {
    require_idle()?;
    let reviewed = plan(root, id, data)?;
    if confirmation_token.is_empty() || reviewed.confirmation_token != confirmation_token {
        return Err("Model files changed. Open Delete again and review the updated confirmation.".into());
    }
    if reviewed.files.is_empty() { return Err("No local files to delete for this model".into()); }
    begin(id)?;
    update("uninstalling", 0, "", None);
    let result: Result<(), String> = (|| {
        // Check every target before the first deletion, then check again immediately before each file.
        for file in &reviewed.files {
            let current = checked_file(Path::new(&file.path), file.external, Vec::new())?.ok_or("Model file disappeared")?;
            if current.bytes != file.bytes || current.modified_nanos != file.modified_nanos {
                return Err("Model files changed. Review the deletion again.".into());
            }
        }
        for file in &reviewed.files {
            let path = Path::new(&file.path);
            let current = checked_file(path, file.external, Vec::new())?.ok_or("Model file disappeared")?;
            if current.bytes != file.bytes || current.modified_nanos != file.modified_nanos {
                return Err("Model files changed during deletion. Review the remaining files again.".into());
            }
            std::fs::remove_file(path).map_err(|e| format!("Could not delete {}: {e}", path.display()))?;
        }
        Ok(())
    })();
    match &result { Ok(()) => update("complete", 0, "", None), Err(error) => update("failed", 0, "", Some(error.clone())) }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    // All destructive tests use tiny fixtures in one unique temporary directory.
    struct Fixture { root: PathBuf, data: Manifest }
    impl Fixture {
        fn new(id: &str, artifact_path: &str) -> Self {
            let root = std::env::temp_dir().join(format!("opencore-removal-{}", uuid::Uuid::new_v4()));
            std::fs::create_dir_all(&root).unwrap();
            let mut data = manifest().unwrap();
            data.models.retain(|model| model.id == id);
            data.models[0].artifacts = vec!["test-weight".into()];
            data.artifacts = vec![Artifact { id: "test-weight".into(), path: artifact_path.into(),
                repo: "test/model".into(), revision: "a".repeat(40), filename: "model.gguf".into(),
                sha256: format!("{:x}", Sha256::digest(b"fixture")), bytes: 7 }];
            Self { root, data }
        }
        fn write(&self, path: &str, bytes: &[u8]) -> PathBuf {
            let path = safe_path(&self.root, path).unwrap();
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, bytes).unwrap();
            path
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) { std::fs::remove_dir_all(&self.root).unwrap(); }
    }

    #[test]
    fn missing_or_stale_confirmation_does_not_delete_any_files() {
        let _guard = MODEL_CATALOG_TEST_LOCK.lock().unwrap();
        let fixture = Fixture::new("echo", "models/test/model.gguf");
        let weight = fixture.write("models/test/model.gguf", b"fixture");
        let reviewed = plan(&fixture.root, "echo", &fixture.data).unwrap();
        assert!(weight.is_file(), "Review must be read-only");
        assert!(remove(&fixture.root, "echo", &fixture.data, "").is_err());
        std::fs::write(&weight, b"a changed model file").unwrap();
        assert!(remove(&fixture.root, "echo", &fixture.data, &reviewed.confirmation_token).is_err());
        assert_eq!(std::fs::read(&weight).unwrap(), b"a changed model file");
    }

    #[test]
    fn removes_partial_downloads_and_derived_weights_but_keeps_unrelated_data() {
        let _guard = MODEL_CATALOG_TEST_LOCK.lock().unwrap();
        let fixture = Fixture::new("phonon-2", "speech/phonon-2/download.gguf");
        let partial = fixture.write("speech/phonon-2/download.gguf.partial", b"partial");
        let derived = fixture.write("speech/phonon-2/model.fermion", b"derived");
        let history = fixture.write("echo/history.json", b"valuable history");
        let other = fixture.write("speech/phonon-2/recording.wav", b"user recording");
        let reviewed = plan(&fixture.root, "phonon-2", &fixture.data).unwrap();
        assert_eq!(reviewed.files.len(), 2);
        assert_eq!(reviewed.total_bytes, 14);
        remove(&fixture.root, "phonon-2", &fixture.data, &reviewed.confirmation_token).unwrap();
        assert!(!partial.exists() && !derived.exists());
        assert_eq!(std::fs::read(history).unwrap(), b"valuable history");
        assert_eq!(std::fs::read(other).unwrap(), b"user recording");
    }

    #[test]
    fn external_checkpoint_candidates_are_exact_files_and_never_recursive() {
        let fixture = Fixture::new("echo", "models/test/model.gguf");
        let directory = fixture.root.join("external-whisper");
        std::fs::create_dir_all(directory.join("recordings")).unwrap();
        std::fs::write(directory.join("model.safetensors"), b"fixture").unwrap();
        std::fs::write(directory.join("personal.txt"), b"keep").unwrap();
        std::fs::write(directory.join("recordings/model.safetensors"), b"keep").unwrap();
        let files: Vec<_> = whisper_files(&directory).iter()
            .filter_map(|path| checked_file(path, true, Vec::new()).unwrap()).collect();
        assert_eq!(files.len(), 1);
        assert!(files[0].external);
        assert_eq!(files[0].path, directory.join("model.safetensors").to_string_lossy());
        assert!(checked_file(&directory, true, Vec::new()).is_err());
    }

    #[test]
    fn rejects_escaping_paths_and_empty_models() {
        let _guard = MODEL_CATALOG_TEST_LOCK.lock().unwrap();
        let mut fixture = Fixture::new("echo", "models/test/model.gguf");
        let reviewed = plan(&fixture.root, "echo", &fixture.data).unwrap();
        assert!(reviewed.files.is_empty());
        assert!(remove(&fixture.root, "echo", &fixture.data, &reviewed.confirmation_token).is_err());
        fixture.data.artifacts[0].path = "../outside.gguf".into();
        assert!(plan(&fixture.root, "echo", &fixture.data).is_err());
    }
}
