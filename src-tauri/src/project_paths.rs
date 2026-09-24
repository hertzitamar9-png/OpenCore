use std::path::Path;

pub fn canonical_existing_directory(path: &Path) -> Result<(String, String), String> {
    if !path.is_absolute() {
        return Err(format!("Project folder must be an absolute path: {}", path.display()));
    }
    let canonical = std::fs::canonicalize(path)
        .map_err(|error| format!("Project folder is unavailable ({}): {error}", path.display()))?;
    if !canonical.is_dir() {
        return Err(format!("Choose a folder, not a file: {}", path.display()));
    }
    let raw = canonical.to_string_lossy();
    #[cfg(windows)]
    let display = if let Some(unc) = raw.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{unc}")
    } else if let Some(local) = raw.strip_prefix(r"\\?\") {
        local.to_owned()
    } else {
        raw.into_owned()
    };
    #[cfg(not(windows))]
    let display = raw.into_owned();
    let key = display.replace('/', r"\").to_lowercase();
    Ok((display, key))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn same_named_directories_have_distinct_identities_and_missing_folders_fail() {
        let root = std::env::temp_dir().join(format!("opencore-paths-{}", uuid::Uuid::new_v4()));
        let left = root.join("left").join("app");
        let right = root.join("right").join("app");
        std::fs::create_dir_all(&left).unwrap();
        std::fs::create_dir_all(&right).unwrap();
        let a = canonical_existing_directory(&left).unwrap();
        let b = canonical_existing_directory(&right).unwrap();
        assert_ne!(a.1, b.1);
        assert!(canonical_existing_directory(&root.join("missing")).is_err());
        assert!(canonical_existing_directory(Path::new(".")).is_err());
        #[cfg(windows)] {
            let upper = std::path::PathBuf::from(left.to_string_lossy().to_uppercase());
            assert_eq!(canonical_existing_directory(&upper).unwrap().1, a.1);
        }
        std::fs::remove_dir_all(root).unwrap();
    }
}
