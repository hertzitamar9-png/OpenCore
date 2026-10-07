//! Project provenance belongs to copied chats; project files remain in their original folders.
use super::*;

#[derive(Debug, Clone, Default)]
pub(super) struct SourceProject {
    cwd: Option<String>,
    folder: Option<String>,
    name: Option<String>,
}

impl SourceProject {
    pub(super) fn from_header(header: &Value) -> Self {
        let string_at = |pointers: &[&str]| -> Option<String> {
            pointers
                .iter()
                .find_map(|pointer| header.pointer(pointer).and_then(Value::as_str))
                .filter(|value| !value.trim().is_empty())
                .map(str::to_owned)
        };
        let cwd = string_at(&[
            "/cwd",
            "/directory",
            "/sourceCwd",
            "/working_directory",
            "/workdir",
            "/path/cwd",
            "/metadata/cwd",
            "/info/directory",
        ]);
        // Only explicit source folder metadata can select a folder different from cwd.
        let folder = string_at(&[
            "/sourceProjectFolder",
            "/projectFolder",
            "/project/folderPath",
            "/project/folder_path",
            "/project/directory",
        ])
        .or_else(|| {
            string_at(&["/git_repo_root"]).filter(|root| {
                cwd.as_ref().is_none_or(|cwd| {
                    let cwd = path_key(cwd);
                    let root = path_key(root);
                    !root.is_empty()
                        && (cwd == root
                            || cwd
                                .strip_prefix(&root)
                                .is_some_and(|rest| rest.starts_with('/')))
                })
            })
        })
        .or_else(|| cwd.clone());
        let name = string_at(&[
            "/sourceProject/name",
            "/project/name",
            "/projectName",
            "/project",
        ]);
        Self { cwd, folder, name }
    }

    pub(super) fn display_folder(&self) -> Option<String> {
        self.folder.as_ref().map(|folder| redact_text(folder))
    }

    pub(super) fn status(&self) -> &'static str {
        let Some(folder) = &self.folder else {
            return "not-recorded";
        };
        let path = Path::new(folder);
        if !path.is_absolute() {
            return "nonlocal";
        }
        match fs::metadata(path) {
            Ok(metadata) if metadata.is_dir() => "available",
            Ok(_) => "unavailable",
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => "missing",
            Err(_) => "unavailable",
        }
    }

    pub(super) fn persist(
        &self,
        store: &EventStore,
        id: &str,
        result: &mut ImportConversationResult,
    ) {
        let cwd = self.cwd.as_deref().map(Path::new);
        let folder = self.folder.as_deref().map(Path::new);
        if let Err(error) = store.record_imported_project(id, cwd, folder, self.name.as_deref()) {
            result.warnings.push(format!("Original project folder was not linked: {}. Select an existing folder in Projects to continue there.", redact_text(&error)));
        }
        result.project_id = store.conversation_project_id(id).ok().flatten();
        if result.folder_status == "available" {
            if let (Some(project_id), Some(folder)) = (&result.project_id, folder) {
                // A manual move must survive sync. Report 'linked' only for this exact source folder.
                if let (Ok(expected), Ok(projects)) =
                    (fs::canonicalize(folder), store.list_projects())
                {
                    if projects
                        .iter()
                        .find(|project| &project.id == project_id)
                        .and_then(|project| project.folder_path.as_ref())
                        .and_then(|path| fs::canonicalize(path).ok())
                        .as_ref()
                        == Some(&expected)
                    {
                        result.folder_status = "linked".into();
                    }
                }
            }
        } else if result.folder_status == "not-recorded"
            && matches!(result.status.as_str(), "imported" | "updated" | "skipped")
        {
            result.warnings.push("The source did not record an original project folder. Link this chat to an existing project to continue there.".into());
        }
    }
}

/// Read the per-profile project registry with the same protected snapshot used for history.
/// A registry failure leaves session cwd usable and is surfaced in each result.
pub(super) fn hermes_registry(
    path: &Path,
    cancelled: &dyn Fn() -> bool,
) -> Result<(Vec<Value>, Option<String>), String> {
    let registry = path.parent().unwrap_or(Path::new("")).join("projects.db");
    if !registry.is_file() || registry == path {
        return Ok((vec![], None));
    }
    let read = (|| {
        let snapshot = snapshot_database(&registry, cancelled)?;
        let db = Connection::open_with_flags(
            &snapshot.path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|error| error.to_string())?;
        db.execute_batch("PRAGMA query_only=ON; PRAGMA trusted_schema=OFF;")
            .map_err(|error| error.to_string())?;
        let columns = table_columns(&db, "Hermes", "projects", &["id", "name"])?;
        let folder_columns = table_columns(&db, "Hermes", "project_folders", &["project_id", "path"])?;
        let mut statement = db
            .prepare("SELECT * FROM projects ORDER BY id LIMIT 1001")
            .map_err(|error| error.to_string())?;
        let mut rows = statement.query([]).map_err(|error| error.to_string())?;
        let mut projects = vec![];
        let mut budget = 0;
        while let Some(row) = rows.next().map_err(|error| error.to_string())? {
            check_cancelled(cancelled)?;
            if projects.len() >= MAX_CONVERSATIONS {
                return Err("The Hermes project registry exceeds the 1,000 project limit.".into());
            }
            let mut project = sql_record(row, &columns, &mut budget)?;
            let id = scalar_id(project.get("id")).ok_or("A Hermes project has no ID")?;
            let mut folders_statement = db
                .prepare(
                    "SELECT * FROM project_folders WHERE project_id=?1 ORDER BY path LIMIT 1001",
                )
                .map_err(|error| error.to_string())?;
            let mut folder_rows = folders_statement
                .query([&id])
                .map_err(|error| error.to_string())?;
            let mut folders = vec![];
            while let Some(folder) = folder_rows.next().map_err(|error| error.to_string())? {
                check_cancelled(cancelled)?;
                if folders.len() >= MAX_CONVERSATIONS {
                    return Err("A Hermes project exceeds the 1,000 folder limit.".into());
                }
                folders.push(sql_record(folder, &folder_columns, &mut budget)?);
            }
            project["folders"] = Value::Array(folders);
            projects.push(project);
        }
        Ok::<_, String>(projects)
    })();
    match read {
        Ok(projects) => Ok((projects, None)),
        Err(error) if error == IMPORT_CANCELLED => Err(error),
        Err(error) => Ok((vec![], Some(format!("Hermes project names/folder metadata could not be read; the recorded session folder is preserved. {}", redact_text(&error))))),
    }
}

fn path_key(value: &str) -> String {
    let key = value.replace('\\', "/").trim_end_matches('/').to_owned();
    // Windows directories compare without case; POSIX source paths preserve it.
    if key.as_bytes().get(1) == Some(&b':') || key.starts_with("//") {
        key.to_ascii_lowercase()
    } else {
        key
    }
}

pub(super) fn attach_opencode_project(session: &mut Value, project: &Value) {
    let directory = session
        .get("directory")
        .and_then(Value::as_str)
        .unwrap_or("");
    let folder = project
        .get("worktree")
        .or_else(|| project.get("folderPath"))
        .and_then(Value::as_str)
        .filter(|folder| !folder.is_empty());
    if let Some(folder) = folder {
        let directory_key = path_key(directory);
        let folder_key = path_key(folder);
        // Another checkout retains its recorded directory. '/' is a global project sentinel.
        if folder != "/"
            && (directory.is_empty()
                || (!folder_key.is_empty()
                    && (directory_key == folder_key
                        || directory_key
                            .strip_prefix(&folder_key)
                            .is_some_and(|rest| rest.starts_with('/')))))
        {
            session["sourceProjectFolder"] = json!(folder);
        }
    }
    session["sourceProject"] = project.clone();
}

pub(super) fn attach_hermes_project(session: &mut Value, registry: &[Value]) {
    let Some(cwd) = session
        .get("cwd")
        .and_then(Value::as_str)
        .filter(|cwd| !cwd.is_empty())
    else {
        return;
    };
    let cwd_key = path_key(cwd);
    let mut matches = vec![];
    for project in registry {
        let folders = project
            .get("folders")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|folder| folder.get("path").and_then(Value::as_str))
            .chain(project.get("primary_path").and_then(Value::as_str));
        for folder in folders {
            let key = path_key(folder);
            if !key.is_empty()
                && (cwd_key == key
                    || cwd_key
                        .strip_prefix(&key)
                        .is_some_and(|rest| rest.starts_with('/')))
            {
                matches.push((key.len(), folder.to_string(), project));
            }
        }
    }
    matches.sort_by(|left, right| right.0.cmp(&left.0));
    let Some((length, folder, project)) = matches.first() else {
        return;
    };
    // Equally close folders belonging to different projects are ambiguous; retain cwd only.
    if matches
        .iter()
        .any(|(size, _, other)| size == length && other.get("id") != project.get("id"))
    {
        session["sourceProjectConflict"] = json!("Multiple source projects share this folder");
        return;
    }
    session["sourceProject"] = (*project).clone();
    session["sourceProjectFolder"] = json!(folder);
}
