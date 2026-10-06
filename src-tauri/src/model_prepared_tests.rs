use super::*;
use serde_json::json;

struct Fixture {
    root: PathBuf,
    descriptor: PreparedRuntime,
    manifest: Vec<u8>,
}
impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("opencore-prepared-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let mut descriptor = PreparedRuntime {
            kind: PREPARATION_KIND.into(),
            path: "models/prepared/woof/model.gguf".into(),
            sha256: hex_digest(b"GGUFtest"),
            bytes: 8,
            source_repo: SOURCE_REPO.into(),
            source_revision: SOURCE_REVISION.into(),
            source_filename: "model.safetensors".into(),
            source_sha256: SOURCE_SHA256.into(),
            source_bytes: SOURCE_BYTES,
            conversion_manifest_sha256: String::new(),
        };
        let manifest = serde_json::to_vec(&json!({
            "schema_version":1,"model_id":SOURCE_REPO,
            "source":{"repo_id":SOURCE_REPO,"revision":SOURCE_REVISION,
                "files":{"model.safetensors":{"sha256":SOURCE_SHA256,"bytes":SOURCE_BYTES}},
                "published_quantization":{"bits":4,"group_size":64,"mode":"affine"}},
            "artifact":{"format":"GGUF","bytes":8,"sha256":descriptor.sha256,
                "architecture":"qwen35","file_type":32,"tensor_count":426,
                "parameter_count":4205751296u64,"reconstructed_from_quantized_source":true},
            "conversion":{"llama_cpp_commit":CONVERTER_REVISION,"converter_exit_code":0,
                "omitted_source_parameter_tensors":[]},
            "validation":{"status":"passed","gguf_tensors_checked":426,
                "independent_mlx_fixtures":{"status":"passed"}}
        }))
        .unwrap();
        descriptor.conversion_manifest_sha256 = hex_digest(&manifest);
        Self {
            root,
            descriptor,
            manifest,
        }
    }
    fn input(&self, bytes: &[u8]) -> PathBuf {
        let path = self.root.join("external.gguf");
        std::fs::write(&path, bytes).unwrap();
        path
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        std::fs::remove_dir_all(&self.root).unwrap();
    }
}
struct Operation;
impl Operation {
    fn new() -> Self {
        crate::model_catalog::begin("echo").unwrap();
        Self
    }
}
impl Drop for Operation {
    fn drop(&mut self) {
        crate::model_catalog::update("complete", 0, "", None);
    }
}

#[test]
fn retained_actual_conversion_manifest_matches_the_declared_schema_and_sha() {
    let bytes = include_bytes!("../test-fixtures/woof-affine4-bf16.manifest.json");
    assert_eq!(
        hex_digest(bytes),
        MANIFEST_SHA256,
        "Retain the original manifest bytes, including line endings"
    );
    let model = crate::model_catalog::model("underdog-woof-4b-11").unwrap();
    let descriptor = model.prepared_runtime.as_ref().unwrap();
    assert_eq!(descriptor.sha256, OUTPUT_SHA256);
    assert_eq!(descriptor.bytes, OUTPUT_BYTES);
    validate_manifest(descriptor, bytes).unwrap();
}

#[test]
fn manifest_requires_exact_hash_and_publisher_identity() {
    let fixture = Fixture::new();
    validate_manifest(&fixture.descriptor, &fixture.manifest).unwrap();
    let mut changed: serde_json::Value = serde_json::from_slice(&fixture.manifest).unwrap();
    changed["source"]["revision"] = json!("a".repeat(40));
    let changed = serde_json::to_vec(&changed).unwrap();
    assert!(validate_manifest(&fixture.descriptor, &changed).is_err());
    let mut descriptor = fixture.descriptor.clone();
    descriptor.conversion_manifest_sha256 = hex_digest(&changed);
    assert!(
        validate_manifest(&descriptor, &changed).is_err(),
        "Matching manifest hash cannot authorize another source"
    );
    assert!(validate_manifest(&fixture.descriptor, &vec![b' '; MAX_MANIFEST_BYTES + 1]).is_err());
}

#[test]
fn registration_verifies_copy_and_receipt_before_claiming_readiness() {
    let _guard = crate::model_catalog::MODEL_CATALOG_TEST_LOCK
        .lock()
        .unwrap();
    let _operation = Operation::new();
    let fixture = Fixture::new();
    let input = fixture.input(b"GGUFtest");
    assert!(!receipt_ready(&fixture.root, &fixture.descriptor));
    register_files(
        &fixture.root,
        &fixture.descriptor,
        &input,
        &fixture.manifest,
    )
    .unwrap();
    assert!(receipt_ready(&fixture.root, &fixture.descriptor));
    let target = safe_path(&fixture.root, &fixture.descriptor.path).unwrap();
    assert_eq!(std::fs::read(&target).unwrap(), b"GGUFtest");
    assert_eq!(
        std::fs::read(&input).unwrap(),
        b"GGUFtest",
        "External original is preserved"
    );
    std::fs::write(&target, b"modified runtime").unwrap();
    assert!(!receipt_ready(&fixture.root, &fixture.descriptor));
}

#[test]
fn wrong_runtime_hash_and_existing_unapproved_file_are_not_overwritten() {
    let _guard = crate::model_catalog::MODEL_CATALOG_TEST_LOCK
        .lock()
        .unwrap();
    let _operation = Operation::new();
    let fixture = Fixture::new();
    let input = fixture.input(b"GGUFnope");
    assert!(register_files(
        &fixture.root,
        &fixture.descriptor,
        &input,
        &fixture.manifest
    )
    .is_err());
    assert!(!receipt_path(&fixture.root, &fixture.descriptor)
        .unwrap()
        .exists());
    assert!(!safe_path(&fixture.root, &fixture.descriptor.path)
        .unwrap()
        .exists());
    std::fs::write(&input, b"GGUFtest").unwrap();
    let target = safe_path(&fixture.root, &fixture.descriptor.path).unwrap();
    std::fs::create_dir_all(target.parent().unwrap()).unwrap();
    std::fs::write(&target, b"keep existing model").unwrap();
    assert!(register_files(
        &fixture.root,
        &fixture.descriptor,
        &input,
        &fixture.manifest
    )
    .is_err());
    assert_eq!(std::fs::read(&target).unwrap(), b"keep existing model");
    assert!(!receipt_ready(&fixture.root, &fixture.descriptor));
}

#[test]
fn manifest_changes_and_descriptor_changes_invalidate_registration() {
    let _guard = crate::model_catalog::MODEL_CATALOG_TEST_LOCK
        .lock()
        .unwrap();
    let _operation = Operation::new();
    let fixture = Fixture::new();
    register_files(
        &fixture.root,
        &fixture.descriptor,
        &fixture.input(b"GGUFtest"),
        &fixture.manifest,
    )
    .unwrap();
    let mut different = fixture.descriptor.clone();
    different.source_revision = "a".repeat(40);
    assert!(!receipt_ready(&fixture.root, &different));
    std::fs::write(
        manifest_path(&fixture.root, &fixture.descriptor).unwrap(),
        b"{}",
    )
    .unwrap();
    assert!(!receipt_ready(&fixture.root, &fixture.descriptor));
}

#[test]
fn cancelled_copy_removes_only_its_temporary_file_and_records_no_readiness() {
    let _guard = crate::model_catalog::MODEL_CATALOG_TEST_LOCK
        .lock()
        .unwrap();
    let _operation = Operation::new();
    let fixture = Fixture::new();
    let input = fixture.input(b"GGUFtest");
    let target = safe_path(&fixture.root, &fixture.descriptor.path).unwrap();
    crate::model_catalog::cancel();
    assert!(copy_runtime(&fixture.root, &fixture.descriptor, &input, &target).is_err());
    assert!(!target.exists());
    assert!(!safe_path(
        &fixture.root,
        &format!("{}.partial", fixture.descriptor.path)
    )
    .unwrap()
    .exists());
    assert!(!receipt_path(&fixture.root, &fixture.descriptor)
        .unwrap()
        .exists());
    assert_eq!(std::fs::read(input).unwrap(), b"GGUFtest");
}

#[test]
fn declared_preparation_is_specific_and_confined_to_app_models() {
    let data: serde_json::Value =
        serde_json::from_str(include_str!("../resources/model-catalog.json")).unwrap();
    let models = data["models"].as_array().unwrap();
    let model: Model = serde_json::from_value(
        models
            .iter()
            .find(|model| model["id"] == "underdog-woof-4b-11")
            .unwrap()
            .clone(),
    )
    .unwrap();
    let files: Vec<Artifact> = serde_json::from_value(data["artifacts"].clone()).unwrap();
    validate_descriptor(&model, &files).unwrap();
    let mut escaping = model.clone();
    escaping.prepared_runtime.as_mut().unwrap().path = "../external.gguf".into();
    assert!(validate_descriptor(&escaping, &files).is_err());
    let mut invented_hub = model.clone();
    invented_hub.prepared_runtime.as_mut().unwrap().source_repo = "test/another-model".into();
    assert!(validate_descriptor(&invented_hub, &files).is_err());
}
