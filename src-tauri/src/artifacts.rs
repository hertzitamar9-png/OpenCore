use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::path::{Path, PathBuf};

const MAX_ARTIFACT_BYTES: usize = 16 * 1024 * 1024;

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ArtifactInfo {
    pub id: String,
    pub name: String,
    pub mime: String,
    pub size: usize,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ArtifactPreview {
    #[serde(flatten)]
    pub info: ArtifactInfo,
    pub data_url: String,
    pub text: Option<String>,
}

pub(crate) fn tool_spec() -> serde_json::Value {
    json!({"type":"function","function":{
        "name":"create_artifact",
        "description":"Create a binary image or PDF artifact for this chat. For code and other text files use the general dev workspace instead.",
        "parameters":{"type":"object","properties":{
            "filename":{"type":"string"},
            "content":{"type":"string","description":"Complete file content"},
            "encoding":{"type":"string","enum":["base64"],"description":"Base64 binary image or PDF data"}
        },"required":["filename","content"],"additionalProperties":false}
    }})
}

fn mime_for_name(name: &str) -> Option<&'static str> {
    match Path::new(name).extension()?.to_str()?.to_ascii_lowercase().as_str() {
        "html" | "htm" => Some("text/html"),
        "svg" => Some("image/svg+xml"),
        "png" => Some("image/png"),
        "jpg" | "jpeg" => Some("image/jpeg"),
        "gif" => Some("image/gif"),
        "webp" => Some("image/webp"),
        "pdf" => Some("application/pdf"),
        "txt" => Some("text/plain"),
        "md" => Some("text/markdown"),
        "json" => Some("application/json"),
        "js" => Some("text/javascript"),
        "css" => Some("text/css"),
        "py" => Some("text/x-python"),
        _ => None,
    }
}

fn safe_name(name: &str) -> Result<&str, String> {
    if name.is_empty() || name.len() > 120 || name == "." || name == ".."
        || name.chars().any(|ch| ch == '/' || ch == '\\' || ch == ':' || ch.is_control())
        || name.ends_with('.') || name.ends_with(' ') {
        return Err("Use a simple filename with a supported extension".into());
    }
    Ok(name)
}

fn valid_name(name: &str) -> Result<&str, String> {
    safe_name(name)?;
    mime_for_name(name).ok_or("Unsupported artifact file type")?;
    Ok(name)
}

pub(crate) fn create_text(root: &Path, name: &str, content: &str) -> Result<ArtifactInfo, String> {
    safe_name(name)?;
    let mime = mime_for_name(name).filter(|mime| mime.starts_with("text/") || *mime == "image/svg+xml")
        .unwrap_or("text/plain");
    create_bytes(root, name, mime, content.as_bytes().to_vec())
}

fn stored_path(root: &Path, id: &str, extension: &str) -> Result<PathBuf, String> {
    let parsed = uuid::Uuid::parse_str(id).map_err(|_| "Invalid artifact id")?;
    Ok(root.join(format!("{parsed}.{extension}")))
}

pub(crate) fn create(root: &Path, name: &str, content: &str, encoding: &str) -> Result<ArtifactInfo, String> {
    let name = valid_name(name)?;
    let mime = mime_for_name(name).unwrap();
    let bytes = match encoding {
        "utf8" | "" => content.as_bytes().to_vec(),
        "base64" => base64::engine::general_purpose::STANDARD.decode(content)
            .map_err(|_| "Invalid base64 artifact content")?,
        _ => return Err("Unsupported artifact encoding".into()),
    };
    if encoding == "base64" && !matches!(mime, "image/png" | "image/jpeg" | "image/gif" | "image/webp" | "application/pdf") {
        return Err("Base64 is supported for images and PDFs only".into());
    }
    create_bytes(root, name, mime, bytes)
}

fn create_bytes(root: &Path, name: &str, mime: &str, bytes: Vec<u8>) -> Result<ArtifactInfo, String> {
    if bytes.is_empty() || bytes.len() > MAX_ARTIFACT_BYTES {
        return Err("Artifact size must be between 1 byte and 16 MiB".into());
    }
    std::fs::create_dir_all(root).map_err(|e| e.to_string())?;
    let id = uuid::Uuid::new_v4().to_string();
    let info = ArtifactInfo { id: id.clone(), name: name.into(), mime: mime.into(), size: bytes.len() };
    let content_path = stored_path(root, &id, "bin")?;
    let meta_path = stored_path(root, &id, "json")?;
    std::fs::write(&content_path, bytes).map_err(|e| e.to_string())?;
    if let Err(error) = std::fs::write(&meta_path, serde_json::to_vec(&info).map_err(|e| e.to_string())?) {
        let _ = std::fs::remove_file(content_path);
        return Err(error.to_string());
    }
    Ok(info)
}

fn image_bytes(path: &Path) -> Result<(String, &'static str, Vec<u8>), String> {
    let name = path.file_name().and_then(|value| value.to_str()).ok_or("Image has no filename")?.to_string();
    valid_name(&name)?;
    let mime = mime_for_name(&name).ok_or("Unsupported image type")?;
    if !matches!(mime, "image/png" | "image/jpeg" | "image/gif" | "image/webp") {
        return Err("Unsupported image type".into());
    }
    let size = path.metadata().map_err(|e| e.to_string())?.len();
    if size == 0 || size > MAX_ARTIFACT_BYTES as u64 { return Err("Image must be between 1 byte and 16 MiB".into()); }
    let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    Ok((name, mime, bytes))
}

pub(crate) fn is_image_name(name: &str) -> bool {
    matches!(mime_for_name(name), Some("image/png" | "image/jpeg" | "image/gif" | "image/webp"))
}

pub(crate) fn store_attached_image(root: &Path, path: &Path) -> Result<ArtifactInfo, String> {
    let (name, mime, bytes) = image_bytes(path)?;
    create_bytes(root, &name, mime, bytes)
}

pub(crate) fn preview_attached_image(path: &Path) -> Result<String, String> {
    let (_, mime, bytes) = image_bytes(path)?;
    Ok(format!("data:{mime};base64,{}", base64::engine::general_purpose::STANDARD.encode(bytes)))
}

fn load(root: &Path, id: &str) -> Result<(ArtifactInfo, Vec<u8>), String> {
    let meta = std::fs::read(stored_path(root, id, "json")?).map_err(|_| "Artifact is unavailable")?;
    let info: ArtifactInfo = serde_json::from_slice(&meta).map_err(|_| "Artifact metadata is invalid")?;
    if info.id != id || safe_name(&info.name).is_err() { return Err("Artifact metadata is invalid".into()); }
    let path = stored_path(root, id, "bin")?;
    if path.metadata().map_err(|_| "Artifact is unavailable")?.len() > MAX_ARTIFACT_BYTES as u64 {
        return Err("Artifact exceeds the preview limit".into());
    }
    let bytes = std::fs::read(path).map_err(|_| "Artifact is unavailable")?;
    if bytes.len() != info.size { return Err("Artifact size does not match its metadata".into()); }
    Ok((info, bytes))
}

pub(crate) fn preview(root: &Path, id: &str) -> Result<ArtifactPreview, String> {
    let (info, bytes) = load(root, id)?;
    let text = if info.mime.starts_with("text/") || matches!(info.mime.as_str(), "application/json" | "image/svg+xml") {
        Some(String::from_utf8(bytes.clone()).map_err(|_| "Artifact is not valid UTF-8")?)
    } else { None };
    let data_url = format!("data:{};base64,{}", info.mime, base64::engine::general_purpose::STANDARD.encode(bytes));
    Ok(ArtifactPreview { info, data_url, text })
}

pub(crate) fn download(root: &Path, downloads: &Path, id: &str) -> Result<PathBuf, String> {
    let (info, bytes) = load(root, id)?;
    std::fs::create_dir_all(downloads).map_err(|e| e.to_string())?;
    let original = Path::new(&info.name);
    let stem = original.file_stem().unwrap().to_string_lossy();
    let ext = original.extension().unwrap().to_string_lossy();
    for index in 0..1000 {
        let name = if index == 0 { info.name.clone() } else { format!("{stem} ({index}).{ext}") };
        let target = downloads.join(name);
        match std::fs::OpenOptions::new().write(true).create_new(true).open(&target) {
            Ok(mut file) => {
                use std::io::Write;
                if let Err(error) = file.write_all(&bytes) {
                    let _ = std::fs::remove_file(&target);
                    return Err(error.to_string());
                }
                return Ok(target);
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.to_string()),
        }
    }
    Err("No available filename in Downloads".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn artifact_preview_and_download_are_confined_and_do_not_overwrite() {
        let root = std::env::temp_dir().join(format!("opencore-artifact-{}", uuid::Uuid::new_v4()));
        let downloads = root.join("Downloads");
        let files = root.join("artifacts");
        let item = create(&files, "game.html", "<h1>Play</h1>", "utf8").unwrap();
        assert_eq!(preview(&files, &item.id).unwrap().text.as_deref(), Some("<h1>Play</h1>"));
        let first = download(&files, &downloads, &item.id).unwrap();
        let second = download(&files, &downloads, &item.id).unwrap();
        assert_ne!(first, second);
        assert_eq!(std::fs::read_to_string(first).unwrap(), "<h1>Play</h1>");
        assert!(preview(&files, "../outside").is_err());
        assert!(create(&files, "../outside.html", "bad", "utf8").is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn published_source_in_other_languages_can_be_previewed() {
        let root = std::env::temp_dir().join(format!("opencore-source-artifact-{}", uuid::Uuid::new_v4()));
        let item = create_text(&root, "main.rs", "fn main() { println!(\"hi\"); }\n").unwrap();
        assert_eq!(preview(&root, &item.id).unwrap().text.as_deref(), Some("fn main() { println!(\"hi\"); }\n"));
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn attached_image_preview_survives_original_file_removal() {
        let root = std::env::temp_dir().join(format!("opencore-image-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let image = root.join("sample.png");
        let bytes = [137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 0];
        std::fs::write(&image, bytes).unwrap();
        let draft = preview_attached_image(&image).unwrap();
        assert!(draft.starts_with("data:image/png;base64,"));
        let saved = store_attached_image(&root.join("artifacts"), &image).unwrap();
        std::fs::remove_file(image).unwrap();
        assert_eq!(preview(&root.join("artifacts"), &saved.id).unwrap().data_url, draft);
        std::fs::remove_dir_all(root).unwrap();
    }
}
