use chrono::{DateTime, FixedOffset};
use rusqlite::{Connection, OpenFlags};
use std::{io::Write, path::{Path, PathBuf}, time::Duration};

fn latest_conversation_update(path: &Path) -> Option<DateTime<FixedOffset>> {
    let connection = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX).ok()?;
    let _ = connection.busy_timeout(Duration::from_millis(500));
    let timestamp: Option<String> = connection.query_row(
        "SELECT MAX(updated_at) FROM conversations",
        [],
        |row| row.get(0),
    ).ok()?;
    DateTime::parse_from_rfc3339(timestamp?.as_str()).ok()
}

pub(crate) fn select_database(primary: &Path, recovered: &Path) -> Result<PathBuf, String> {
    let selection = primary.with_extension("database-choice");
    match std::fs::read_to_string(&selection) {
        Ok(choice) => {
            let selected = match choice.trim() {
                "primary" => primary,
                "recovered" => recovered,
                _ => return Err("The saved conversation database choice is invalid; both databases have been preserved.".into()),
            };
            return match std::fs::metadata(selected) {
                Ok(metadata) if metadata.is_file() => Ok(selected.to_path_buf()),
                Ok(_) => Err(format!("The selected conversation database is not a regular file: {}. Both database locations have been preserved.", selected.display())),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => Err(format!("The selected conversation database is missing: {}. Restore it before restarting; another copy has not been opened automatically.", selected.display())),
                Err(error) => Err(format!("Could not inspect the selected conversation database {}: {error} (kind={:?}, Windows error={:?}). Another copy has not been opened automatically.", selected.display(), error.kind(), error.raw_os_error())),
            };
        },
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
        Err(error) => return Err(format!("Could not read the conversation database choice: {error}")),
    }
    // Select once during migration. Remaining conversation timestamps can move
    // backward when a user deletes a chat, so they must not select a different
    // database on every launch.
    let selected = match (latest_conversation_update(primary), latest_conversation_update(recovered)) {
        (Some(primary_time), Some(recovered_time)) if recovered_time > primary_time => recovered.to_path_buf(),
        (None, Some(_)) => recovered.to_path_buf(),
        (_, _) if primary.exists() => primary.to_path_buf(),
        (_, _) if recovered.exists() => recovered.to_path_buf(),
        _ => primary.to_path_buf(),
    };
    if let Some(parent) = selection.parent() { std::fs::create_dir_all(parent).map_err(|e|e.to_string())?; }
    let mut marker = match std::fs::OpenOptions::new().write(true).create_new(true).open(&selection) {
        Ok(marker) => marker,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => return select_database(primary, recovered),
        Err(error) => return Err(format!("Could not preserve the selected conversation database: {error}")),
    };
    marker.write_all(if selected == recovered { b"recovered\n" } else { b"primary\n" }).map_err(|e|e.to_string())?;
    marker.sync_all().map_err(|e|e.to_string())?;
    Ok(selected)
}

#[cfg(test)]
mod tests {
    use super::select_database;
    use rusqlite::Connection;
    use std::path::{Path, PathBuf};

    fn test_databases() -> (PathBuf, PathBuf, PathBuf) {
        let root = std::env::temp_dir().join(format!("opencore-db-select-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        (root.clone(), root.join("control-center.sqlite3"), root.join("control-center.recovered.sqlite3"))
    }

    fn write_database(path: &Path, timestamp: &str) {
        let connection = Connection::open(path).unwrap();
        connection.execute_batch("CREATE TABLE conversations(updated_at TEXT NOT NULL);").unwrap();
        if !timestamp.is_empty() {
            connection.execute("INSERT INTO conversations(updated_at) VALUES(?1)", [timestamp]).unwrap();
        }
    }

    #[test]
    fn keeps_the_selected_database_after_all_its_chats_are_deleted() {
        let (root, primary, recovered) = test_databases();
        write_database(&primary, "2026-09-26T21:21:07Z");
        write_database(&recovered, "2026-09-25T18:16:49Z");
        assert_eq!(select_database(&primary, &recovered).unwrap(), primary);
        Connection::open(&primary).unwrap().execute("DELETE FROM conversations", []).unwrap();
        assert_eq!(select_database(&primary, &recovered).unwrap(), primary,
            "Deleting chats must not resurrect them from the older recovery copy at next launch");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn selects_the_database_with_the_most_recent_conversation() {
        let (root, primary, recovered) = test_databases();
        write_database(&primary, "2026-09-26T21:21:07Z");
        write_database(&recovered, "2026-09-25T18:16:49Z");
        assert_eq!(
            select_database(&primary, &recovered).unwrap(),
            primary
        );
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn selects_recovery_only_when_it_has_newer_conversation_data() {
        let (root, primary, recovered) = test_databases();
        write_database(&primary, "2026-09-25T18:16:49Z");
        write_database(&recovered, "2026-09-26T21:21:07Z");
        assert_eq!(select_database(&primary, &recovered).unwrap(), recovered);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn prefers_the_primary_database_when_timestamps_are_tied_or_unavailable() {
        let (root, primary, recovered) = test_databases();
        write_database(&primary, "2026-09-25T18:00:00Z");
        write_database(&recovered, "2026-09-25T18:00:00Z");
        assert_eq!(select_database(&primary, &recovered).unwrap(), primary);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn missing_selected_database_does_not_open_an_older_copy() {
        let (root, primary, recovered) = test_databases();
        write_database(&recovered, "2026-09-25T18:00:00Z");
        std::fs::write(primary.with_extension("database-choice"), "primary\n").unwrap();
        let error = select_database(&primary, &recovered).unwrap_err();
        assert!(error.contains("is missing"));
        assert!(error.contains("another copy has not been opened"));
        assert_eq!(std::fs::read_to_string(primary.with_extension("database-choice")).unwrap(), "primary\n");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn directory_at_selected_database_path_is_reported_accurately() {
        let (root, primary, recovered) = test_databases();
        std::fs::create_dir(&primary).unwrap();
        std::fs::write(primary.with_extension("database-choice"), "primary\n").unwrap();
        let error = select_database(&primary, &recovered).unwrap_err();
        assert!(error.contains("not a regular file"));
        assert!(!error.contains("is missing"));
        assert!(primary.is_dir());
        std::fs::remove_dir_all(root).unwrap();
    }
}
