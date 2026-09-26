use base64::Engine;
use std::path::{Path, PathBuf};

const MAX_ATTACHMENT_BYTES: usize = 32 * 1024 * 1024;

pub fn stage_attachment_bytes(root: &Path, name: &str, encoded: &str) -> Result<PathBuf, String> {
    if encoded.len() > MAX_ATTACHMENT_BYTES.div_ceil(3) * 4 {
        return Err(
            "Pasted files are limited to 32 MiB; drag larger files into the composer instead."
                .into(),
        );
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .map_err(|error| format!("Clipboard attachment data is invalid: {error}"))?;
    if bytes.len() > MAX_ATTACHMENT_BYTES {
        return Err(
            "Pasted files are limited to 32 MiB; drag larger files into the composer instead."
                .into(),
        );
    }

    let basename = name.rsplit(['/', '\\']).next().unwrap_or("").trim();
    let safe_name: String = basename
        .chars()
        .filter(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '-' | '_')
        })
        .take(120)
        .collect();
    let safe_name = if safe_name.is_empty() || safe_name == "." || safe_name == ".." {
        "clipboard-attachment.bin"
    } else {
        &safe_name
    };

    std::fs::create_dir_all(root)
        .map_err(|error| format!("Cannot create clipboard attachment directory: {error}"))?;
    let path = root.join(format!("{}-{safe_name}", uuid::Uuid::new_v4()));
    if let Err(error) = std::fs::write(&path, bytes) {
        let _ = std::fs::remove_file(&path);
        return Err(format!("Cannot save clipboard attachment: {error}"));
    }
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::stage_attachment_bytes;

    #[test]
    fn stages_clipboard_bytes_inside_the_attachment_directory_with_a_safe_name() {
        let root =
            std::env::temp_dir().join(format!("opencore-clipboard-test-{}", uuid::Uuid::new_v4()));
        let path = stage_attachment_bytes(&root, r"..\outside\image.png", "aGVsbG8=").unwrap();

        assert!(path.starts_with(&root));
        assert!(path
            .file_name()
            .unwrap()
            .to_string_lossy()
            .ends_with("-image.png"));
        assert_eq!(std::fs::read(&path).unwrap(), b"hello");

        std::fs::remove_dir_all(root).unwrap();
    }
}
