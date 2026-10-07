//! One declared conversion of the pinned Woof publisher checkpoint, never arbitrary GGUF import.
use crate::model_catalog::{self, safe_path, Artifact, Model};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

const PREPARATION_KIND: &str = "woof-mlx-affine4-bf16";
const SOURCE_REPO: &str = "ConwayResearch/Underdog-Woof-4B-1.1";
const SOURCE_REVISION: &str = "cf5f8db5409258e73303b78e112051fc443cb02b";
const SOURCE_SHA256: &str = "db21a4aae693db80ec907adc6d635c7bcb0c47622dff2ca0bc741af769a8174e";
const SOURCE_BYTES: u64 = 2_367_237_149;
const OUTPUT_SHA256: &str = "965b2ae8d2b570e01f7d8d5da70f26e697c6bc587878eedb89112a37ef980df5";
const OUTPUT_BYTES: u64 = 8_424_393_184;
const MANIFEST_SHA256: &str = "0d3101797d98295dbf984eff912d368a7bbb4ddb1543a394cf35c0a505808e2a";
const CONVERTER_REVISION: &str = "bed0a856606ee4a24a164066f73d2379447033f5";
const MAX_MANIFEST_BYTES: usize = 256 * 1024;
const MAX_RECEIPT_BYTES: usize = 16 * 1024;

#[derive(Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PreparedRuntime {
    pub kind: String,
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
    pub source_repo: String,
    pub source_revision: String,
    pub source_filename: String,
    pub source_sha256: String,
    pub source_bytes: u64,
    pub conversion_manifest_sha256: String,
}

pub(crate) fn validate_descriptor(model: &Model, artifacts: &[Artifact]) -> Result<(), String> {
    let Some(pin) = &model.prepared_runtime else {
        return Ok(());
    };
    if !matches!(
        model.id.as_str(),
        "underdog-woof-4b-11" | "underdog-woof-4b-11-native"
    ) || model.backend != "gguf"
        || model.selectable
        || model.runtime_ready
        || model.runtime_model_path.as_deref() != Some(pin.path.as_str())
        || pin.kind != PREPARATION_KIND
        || pin.source_repo != SOURCE_REPO
        || pin.source_revision != SOURCE_REVISION
        || pin.source_filename != "model.safetensors"
        || pin.source_sha256 != SOURCE_SHA256
        || pin.source_bytes != SOURCE_BYTES
        || pin.sha256 != OUTPUT_SHA256
        || pin.bytes != OUTPUT_BYTES
        || pin.conversion_manifest_sha256 != MANIFEST_SHA256
        || pin.path
            != "models/prepared/underdog-woof-4b-11/Underdog-Woof-4B-1.1-MLX4bit-dequant-BF16.gguf"
    {
        return Err(format!(
            "Invalid declared Woof preparation for {}",
            model.id
        ));
    }
    safe_path(Path::new("."), &pin.path)?;
    let sources: Vec<_> = artifacts
        .iter()
        .filter(|file| model.artifacts.contains(&file.id))
        .collect();
    if sources.len() != model.artifacts.len()
        || !sources.iter().any(|file| {
            file.filename == pin.source_filename
                && file.sha256 == pin.source_sha256
                && file.bytes == pin.source_bytes
        })
        || sources.iter().any(|file| {
            file.repo != pin.source_repo
                || file.revision != pin.source_revision
                || !file.compatible_local_sha256.is_empty()
                || file.path == pin.path
                || file.path == format!("{}.manifest.json", pin.path)
        })
    {
        return Err(format!(
            "Prepared runtime does not match the original source pins for {}",
            model.id
        ));
    }
    Ok(())
}

#[derive(Deserialize)]
struct ConversionManifest {
    schema_version: u32,
    model_id: String,
    source: SourceManifest,
    artifact: OutputManifest,
    conversion: ConverterManifest,
    validation: ValidationManifest,
}
#[derive(Deserialize)]
struct SourceManifest {
    repo_id: String,
    revision: String,
    files: BTreeMap<String, SourceFile>,
    published_quantization: Quantization,
}
#[derive(Deserialize)]
struct SourceFile {
    bytes: u64,
    sha256: String,
}
#[derive(Deserialize)]
struct Quantization {
    bits: u32,
    group_size: u32,
    mode: String,
}
#[derive(Deserialize)]
struct OutputManifest {
    format: String,
    bytes: u64,
    sha256: String,
    architecture: String,
    file_type: u32,
    tensor_count: u32,
    parameter_count: u64,
    reconstructed_from_quantized_source: bool,
}
#[derive(Deserialize)]
struct ConverterManifest {
    llama_cpp_commit: String,
    converter_exit_code: i32,
    omitted_source_parameter_tensors: Vec<String>,
}
#[derive(Deserialize)]
struct ValidationManifest {
    status: String,
    gguf_tensors_checked: u32,
    independent_mlx_fixtures: FixtureValidation,
}
#[derive(Deserialize)]
struct FixtureValidation {
    status: String,
}

fn hex_digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn validate_manifest(pin: &PreparedRuntime, bytes: &[u8]) -> Result<(), String> {
    if bytes.len() > MAX_MANIFEST_BYTES || hex_digest(bytes) != pin.conversion_manifest_sha256 {
        return Err("Conversion manifest does not match the approved SHA-256".into());
    }
    let manifest: ConversionManifest =
        serde_json::from_slice(bytes).map_err(|e| format!("Invalid conversion manifest: {e}"))?;
    let source = manifest
        .source
        .files
        .get(&pin.source_filename)
        .ok_or("Conversion manifest is missing the publisher checkpoint")?;
    let quant = &manifest.source.published_quantization;
    if manifest.schema_version != 1
        || manifest.model_id != pin.source_repo
        || manifest.source.repo_id != pin.source_repo
        || manifest.source.revision != pin.source_revision
        || source.sha256 != pin.source_sha256
        || source.bytes != pin.source_bytes
        || quant.bits != 4
        || quant.group_size != 64
        || quant.mode != "affine"
        || manifest.artifact.format != "GGUF"
        || manifest.artifact.sha256 != pin.sha256
        || manifest.artifact.bytes != pin.bytes
        || manifest.artifact.architecture != "qwen35"
        || manifest.artifact.file_type != 32
        || manifest.artifact.tensor_count != 426
        || manifest.artifact.parameter_count != 4_205_751_296
        || !manifest.artifact.reconstructed_from_quantized_source
        || manifest.conversion.llama_cpp_commit != CONVERTER_REVISION
        || manifest.conversion.converter_exit_code != 0
        || !manifest
            .conversion
            .omitted_source_parameter_tensors
            .is_empty()
        || manifest.validation.status != "passed"
        || manifest.validation.gguf_tensors_checked != 426
        || manifest.validation.independent_mlx_fixtures.status != "passed"
    {
        return Err("Conversion manifest source, output, or validation differs from the declared Woof preparation".into());
    }
    Ok(())
}

fn checked_external(path: &Path) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err("Choose an absolute local file path".into());
    }
    let parent = path.parent().ok_or("Missing file parent")?;
    let name = path
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or("Invalid local filename")?;
    let path = safe_path(parent, name)?;
    if !path.metadata().is_ok_and(|m| m.is_file()) {
        return Err(format!("Missing local file: {}", path.display()));
    }
    Ok(path)
}
fn read_bounded(path: &Path, limit: usize) -> Result<Vec<u8>, String> {
    let path = checked_external(path)?;
    let file = std::fs::File::open(path).map_err(|e| e.to_string())?;
    if file.metadata().map_err(|e| e.to_string())?.len() > limit as u64 {
        return Err("Model metadata exceeds the allowed size".into());
    }
    let mut bytes = Vec::new();
    file.take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > limit {
        return Err("Model metadata exceeds the allowed size".into());
    }
    Ok(bytes)
}
fn modified(path: &Path) -> Result<u128, String> {
    path.metadata()
        .and_then(|m| m.modified())
        .map_err(|e| e.to_string())?
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .map_err(|e| e.to_string())
}
fn cancelled() -> Result<(), String> {
    if model_catalog::install_cancelled() {
        Err("Prepared model registration cancelled".into())
    } else {
        Ok(())
    }
}
fn verify_file(path: &Path, bytes: u64, expected: &str) -> Result<(), String> {
    let path = checked_external(path)?;
    let before = modified(&path)?;
    let mut file = std::fs::File::open(&path).map_err(|e| e.to_string())?;
    if file.metadata().map_err(|e| e.to_string())?.len() != bytes {
        return Err("Prepared model file size does not match the approved pin".into());
    }
    let mut hash = Sha256::new();
    let mut buffer = vec![0u8; 1024 * 1024];
    let mut count = 0u64;
    loop {
        cancelled()?;
        let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        count = count.saturating_add(n as u64);
        if count > bytes {
            return Err("Model file changed while verifying".into());
        }
        hash.update(&buffer[..n]);
        model_catalog::update("verifying", count, &path.display().to_string(), None);
    }
    if count != bytes || format!("{:x}", hash.finalize()) != expected || modified(&path)? != before
    {
        return Err(
            "Prepared model SHA-256 does not match the approved pin, or the file changed".into(),
        );
    }
    Ok(())
}
fn manifest_path(root: &Path, pin: &PreparedRuntime) -> Result<PathBuf, String> {
    safe_path(root, &format!("{}.manifest.json", pin.path))
}
fn receipt_path(root: &Path, pin: &PreparedRuntime) -> Result<PathBuf, String> {
    safe_path(
        root,
        &format!("models/receipts/prepared-{}.json", pin.sha256),
    )
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PreparedReceipt {
    schema_version: u32,
    descriptor: PreparedRuntime,
    modified_nanos: u128,
}

// Like ordinary installed checkpoints, full hashes are recorded at registration; listings
// require that receipt plus unchanged size/mtime. The small manifest is rehashed on each check.
fn receipt_ready(root: &Path, pin: &PreparedRuntime) -> bool {
    let check = || -> Result<bool, String> {
        let receipt: PreparedReceipt =
            serde_json::from_slice(&read_bounded(&receipt_path(root, pin)?, MAX_RECEIPT_BYTES)?)
                .map_err(|e| e.to_string())?;
        let path = safe_path(root, &pin.path)?;
        Ok(receipt.schema_version == 1
            && receipt.descriptor == *pin
            && path
                .metadata()
                .is_ok_and(|m| m.is_file() && m.len() == pin.bytes)
            && modified(&path)? == receipt.modified_nanos
            && validate_manifest(
                pin,
                &read_bounded(&manifest_path(root, pin)?, MAX_MANIFEST_BYTES)?,
            )
            .is_ok())
    };
    check().unwrap_or(false)
}
pub(crate) fn ready(root: &Path, model: &Model) -> bool {
    model
        .prepared_runtime
        .as_ref()
        .is_some_and(|pin| receipt_ready(root, pin))
}
pub(crate) fn managed_files(root: &Path, model: &Model) -> Result<Vec<PathBuf>, String> {
    let Some(pin) = &model.prepared_runtime else {
        return Ok(Vec::new());
    };
    Ok(vec![
        safe_path(root, &pin.path)?,
        safe_path(root, &format!("{}.partial", pin.path))?,
        manifest_path(root, pin)?,
        receipt_path(root, pin)?,
    ])
}

// A hard link publishes a complete sibling temporary file without replacing an
// existing target on either Windows or Unix. Both paths are on the managed model volume.
fn publish_new(temporary: &Path, target: &Path) -> Result<(), String> {
    std::fs::hard_link(temporary, target).map_err(|e| {
        format!("Could not publish prepared model without replacing an existing file: {e}")
    })?;
    std::fs::remove_file(temporary).map_err(|e| e.to_string())
}
fn copy_runtime(
    root: &Path,
    pin: &PreparedRuntime,
    input: &Path,
    target: &Path,
) -> Result<(), String> {
    model_catalog::reserve_space(root, pin.bytes)?;
    std::fs::create_dir_all(target.parent().ok_or("Missing prepared model directory")?)
        .map_err(|e| e.to_string())?;
    let temporary = safe_path(root, &format!("{}.partial", pin.path))?;
    let mut output = std::fs::OpenOptions::new().write(true).create_new(true).open(&temporary)
        .map_err(|e|format!("Could not create prepared temporary file. Review any interrupted preparation in Uninstall: {e}"))?;
    let result = (|| {
        let mut source = std::fs::File::open(input).map_err(|e| e.to_string())?;
        let mut buffer = vec![0u8; 1024 * 1024];
        let mut hash = Sha256::new();
        let mut copied = 0u64;
        loop {
            cancelled()?;
            let n = source.read(&mut buffer).map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            copied = copied.saturating_add(n as u64);
            if copied > pin.bytes {
                return Err("Input GGUF changed during copying".into());
            }
            output.write_all(&buffer[..n]).map_err(|e| e.to_string())?;
            hash.update(&buffer[..n]);
            model_catalog::update(
                "preparing",
                copied,
                "Copying verified Woof GGUF into Models",
                None,
            );
        }
        if copied != pin.bytes || format!("{:x}", hash.finalize()) != pin.sha256 {
            return Err("Copied GGUF does not match its approved SHA-256".into());
        }
        output.sync_all().map_err(|e| e.to_string())?;
        drop(output);
        cancelled()?;
        safe_path(root, &pin.path)?;
        safe_path(root, &format!("{}.partial", pin.path))?;
        publish_new(&temporary, target)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}
fn store_manifest(root: &Path, pin: &PreparedRuntime, bytes: &[u8]) -> Result<(), String> {
    let target = manifest_path(root, pin)?;
    if target.exists() {
        validate_manifest(pin, &read_bounded(&target, MAX_MANIFEST_BYTES)?)?;
        return Ok(());
    }
    let temporary = safe_path(
        root,
        &format!("{}.manifest-{}.tmp", pin.path, uuid::Uuid::new_v4()),
    )?;
    let result = (|| {
        let mut output = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|e| e.to_string())?;
        output.write_all(bytes).map_err(|e| e.to_string())?;
        output.sync_all().map_err(|e| e.to_string())?;
        drop(output);
        manifest_path(root, pin)?;
        publish_new(&temporary, &target)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}
fn register_files(
    root: &Path,
    pin: &PreparedRuntime,
    input: &Path,
    manifest: &[u8],
) -> Result<(), String> {
    validate_manifest(pin, manifest)?;
    let input = checked_external(input)?;
    verify_file(&input, pin.bytes, &pin.sha256)?;
    let target = safe_path(root, &pin.path)?;
    if target.exists() {
        verify_file(&target, pin.bytes, &pin.sha256)?;
    } else {
        copy_runtime(root, pin, &input, &target)?;
    }
    store_manifest(root, pin, manifest)?;
    cancelled()?;
    let receipt = PreparedReceipt {
        schema_version: 1,
        descriptor: pin.clone(),
        modified_nanos: modified(&target)?,
    };
    let target = receipt_path(root, pin)?;
    std::fs::create_dir_all(target.parent().ok_or("Missing receipt directory")?)
        .map_err(|e| e.to_string())?;
    let temporary = safe_path(
        root,
        &format!(
            "models/receipts/prepared-{}-{}.tmp",
            pin.sha256,
            uuid::Uuid::new_v4()
        ),
    )?;
    let result = (|| {
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|e| e.to_string())?;
        file.write_all(&serde_json::to_vec(&receipt).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
        file.sync_all().map_err(|e| e.to_string())?;
        drop(file);
        cancelled()?;
        receipt_path(root, pin)?;
        // Only receipt metadata is replaced, after every managed weight/manifest is verified.
        std::fs::rename(&temporary, &target).map_err(|e| e.to_string())
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}
fn register(root: &Path, id: &str, path: &Path, manifest_path: &Path) -> Result<(), String> {
    let model = model_catalog::model(id).ok_or("Unknown model")?;
    let pin = model
        .prepared_runtime
        .as_ref()
        .ok_or("This catalog model has no declared prepared runtime")?;
    let sources = model_catalog::preparation_sources(root, id)?;
    let manifest = read_bounded(manifest_path, MAX_MANIFEST_BYTES)?;
    validate_manifest(pin, &manifest)?;
    for source in &sources {
        verify_file(
            &safe_path(root, &source.path)?,
            source.bytes,
            &source.sha256,
        )?;
    }
    register_files(root, pin, path, &manifest)
}

#[tauri::command]
pub async fn register_prepared_model(
    core: tauri::State<'_, Arc<crate::AppCore>>,
    id: String,
    path: String,
    manifest_path: String,
) -> Result<(), String> {
    core.ensure_not_updating()?;
    if core.background.busy_gpu() {
        return Err("Wait for GPU background workers before changing model files".into());
    }
    if core.studios.busy() {
        return Err("Wait for studio jobs before changing model files".into());
    }
    if matches!(
        core.runtime.snapshot().status.as_str(),
        "starting" | "running"
    ) {
        return Err("Stop the runtime before preparing a model".into());
    }
    let root = core.runtime.install_root().to_path_buf();
    model_catalog::begin_prepared(&root, &id)?;
    let result = tauri::async_runtime::spawn_blocking(move || {
        register(&root, &id, Path::new(&path), Path::new(&manifest_path))
    })
    .await
    .map_err(|e| e.to_string())
    .and_then(|result| result);
    match &result {
        Ok(()) => model_catalog::update(
            "complete",
            OUTPUT_BYTES,
            "Verified prepared Woof runtime registered",
            None,
        ),
        Err(error) => model_catalog::update("failed", 0, "", Some(error.clone())),
    }
    result
}

#[cfg(test)]
#[path = "model_prepared_tests.rs"]
mod tests;
