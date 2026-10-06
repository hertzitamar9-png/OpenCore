//! Separate, bounded history for authored files. Commands preview recorded IDs, never caller-supplied paths.
use base64::{engine::general_purpose::STANDARD, Engine};
use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

#[path = "workspace_diff.rs"]
mod workspace_diff;
#[cfg(test)]
use workspace_diff::line_counts;
use workspace_diff::line_counts_with_work;

#[derive(Clone, Copy)]
struct LedgerLimits {
    max_file_bytes: u64,
    max_total_bytes: u64,
    max_entries: usize,
    max_depth: usize,
    max_reference_bytes: u64,
    max_preview_bytes: u64,
    diff_work: usize,
}
impl Default for LedgerLimits {
    fn default() -> Self {
        Self {
            max_file_bytes: 4 * 1024 * 1024,
            max_total_bytes: 64 * 1024 * 1024,
            max_entries: 50_000,
            max_depth: 48,
            max_reference_bytes: 256 * 1024 * 1024,
            max_preview_bytes: 12 * 1024 * 1024,
            diff_work: 20_000_000,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FileRecord {
    pub id: String,
    pub conversation_id: String,
    pub turn_id: String,
    pub path: String,
    pub change: String,
    pub added: Option<u64>,
    pub removed: Option<u64>,
    pub before_hash: Option<String>,
    pub after_hash: Option<String>,
    pub timestamp: String,
    pub size: u64,
    pub mime: String,
    pub source: String,
    pub snapshot_available: bool,
    pub origin: String,
    pub status: String,
}

#[derive(Clone, Serialize, Deserialize)]
struct CapturedFile {
    hash: String,
    size: u64,
    mime: String,
    text: bool,
}
#[derive(Clone, Default, Serialize, Deserialize)]
struct Scan {
    files: BTreeMap<String, CapturedFile>,
    known: BTreeSet<String>,
    listed_dirs: BTreeSet<String>,
    coverage: Vec<String>,
}
impl Scan {
    /// A path is new only when a fully enumerated parent proves it did not exist in the before capture.
    fn proves_absent(&self, relative: &str) -> bool {
        let mut parent = String::new();
        for part in relative.split('/') {
            let current = if parent.is_empty() {
                part.to_owned()
            } else {
                format!("{parent}/{part}")
            };
            if !self.known.contains(&current) {
                return self.listed_dirs.contains(&parent);
            }
            parent = current;
        }
        false
    }
}

pub struct TurnCapture {
    capture_id: String,
    conversation: String,
    turn: String,
    workspace: PathBuf,
    before: Scan,
}

struct OutputSource {
    path: PathBuf,
    name: Option<String>,
    mime: Option<String>,
}

pub struct WorkspaceLedger {
    root: PathBuf,
    objects: PathBuf,
    db: Mutex<Connection>,
    object_writer: Mutex<()>,
    limits: LedgerLimits,
}

#[derive(Default)]
struct Coverage {
    notes: BTreeSet<String>,
    omitted: usize,
}
impl Coverage {
    fn add(&mut self, note: impl Into<String>) {
        let note = note.into();
        if self.notes.len() < 100 || self.notes.contains(&note) {
            self.notes.insert(note);
        } else {
            self.omitted += 1;
        }
    }
    fn extend(&mut self, notes: impl IntoIterator<Item = String>) {
        for note in notes {
            self.add(note);
        }
    }
    fn finish(self) -> Vec<String> {
        let mut result: Vec<_> = self.notes.into_iter().collect();
        if self.omitted > 0 {
            result.push(format!(
                "{} additional coverage omissions; this capture is incomplete",
                self.omitted
            ));
        }
        result
    }
}

impl WorkspaceLedger {
    /// `root` is application data, not the conversation DB or a model directory.
    pub fn new(root: PathBuf) -> Result<Arc<Self>, String> {
        Self::open(root, LedgerLimits::default())
    }
    fn open(root: PathBuf, limits: LedgerLimits) -> Result<Arc<Self>, String> {
        let root = absolute(&root)?.join("workspace-files");
        reject_links(&root)?;
        fs::create_dir_all(&root).map_err(err)?;
        let root = safe_root(&root)?;
        let objects = root.join("objects");
        reject_links(&objects)?;
        fs::create_dir_all(&objects).map_err(err)?;
        let database = root.join("ledger.sqlite3");
        reject_links(&database)?;
        for suffix in ["ledger.sqlite3-wal", "ledger.sqlite3-shm"] {
            reject_links(&root.join(suffix))?;
        }
        let db = Connection::open(database).map_err(err)?;
        db.busy_timeout(Duration::from_secs(5)).map_err(err)?;
        db.execute_batch(
            "PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS turns (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, capture_id TEXT NOT NULL UNIQUE,
                conversation_id TEXT NOT NULL, turn_id TEXT NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL, workspace TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, baseline TEXT, coverage TEXT NOT NULL DEFAULT '[]');
            CREATE INDEX IF NOT EXISTS turns_chat_order ON turns(conversation_id, sequence DESC);
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY, capture_id TEXT NOT NULL REFERENCES turns(capture_id),
                path TEXT NOT NULL, before_hash TEXT, after_hash TEXT, record TEXT NOT NULL,
                reference_root TEXT);
            CREATE INDEX IF NOT EXISTS files_capture ON files(capture_id);
            CREATE INDEX IF NOT EXISTS files_path ON files(path);",
        )
        .map_err(err)?;
        let ledger = Arc::new(Self {
            root,
            objects,
            db: Mutex::new(db),
            object_writer: Mutex::new(()),
            limits,
        });
        ledger.recover_interrupted()?;
        Ok(ledger)
    }
    fn database(&self) -> Result<MutexGuard<'_, Connection>, String> {
        self.db
            .lock()
            .map_err(|_| "File ledger lock is unavailable".to_owned())
    }

    pub fn begin_turn(
        &self,
        conversation: &str,
        turn: &str,
        workspace: impl AsRef<Path>,
    ) -> Result<TurnCapture, String> {
        valid_label(conversation, "conversation", false)?;
        valid_label(turn, "turn", false)?;
        let workspace = safe_root(workspace.as_ref())?;
        let before = self.scan(&workspace)?;
        let capture_id = uuid::Uuid::new_v4().to_string();
        self.database()?.execute("INSERT INTO turns(capture_id,conversation_id,turn_id,kind,status,workspace,started_at,baseline,coverage) VALUES(?1,?2,?3,'task','running',?4,?5,?6,?7)", params![capture_id, conversation, turn, path_text(&workspace)?, now(), serde_json::to_string(&before).map_err(err)?, serde_json::to_string(&before.coverage).map_err(err)?]).map_err(err)?;
        Ok(TurnCapture {
            capture_id,
            conversation: conversation.to_owned(),
            turn: turn.to_owned(),
            workspace,
            before,
        })
    }

    pub fn finish_turn(&self, capture: TurnCapture, status: &str) -> Result<Value, String> {
        valid_label(status, "status", false)?;
        let after = self.scan(&capture.workspace).unwrap_or_else(|cause| Scan {
            coverage: vec![format!("After capture unavailable: {cause}")],
            ..Scan::default()
        });
        let mut coverage = Coverage::default();
        coverage.extend(capture.before.coverage.clone());
        coverage.extend(after.coverage.clone());
        let timestamp = now();
        let mut files = Vec::new();
        let mut diff_work_remaining = self.limits.diff_work;
        let candidates: BTreeSet<_> = capture
            .before
            .files
            .keys()
            .chain(after.files.keys())
            .cloned()
            .collect();
        for path in candidates {
            let before = capture.before.files.get(&path);
            let next = after.files.get(&path);
            if before.map(|file| &file.hash) == next.map(|file| &file.hash) {
                continue;
            }
            let change = match (before, next) {
                (Some(_), Some(_)) => "modified",
                (None, Some(_)) if capture.before.proves_absent(&path) => "created",
                (None, Some(_)) => {
                    coverage.add(format!(
                        "Before version unavailable for {path}; change could not be determined"
                    ));
                    continue;
                }
                (Some(_), None) => match confirmed_absent(&capture.workspace, &path) {
                    Ok(true) => "deleted",
                    Ok(false) => {
                        coverage.add(format!("After version unavailable for {path}; existing file is not recorded as deleted"));
                        continue;
                    }
                    Err(cause) => {
                        coverage.add(format!("Could not determine deletion of {path}: {cause}"));
                        continue;
                    }
                },
                (None, None) => continue,
            };
            let data = next.or(before).expect("one captured version");
            let (added, removed) = if before.into_iter().chain(next).all(|file| file.text) {
                let counts = self.snapshot_text(before).and_then(|old| {
                    self.snapshot_text(next).and_then(|new| {
                        line_counts_with_work(
                            old.as_deref().unwrap_or(""),
                            new.as_deref().unwrap_or(""),
                            diff_work_remaining,
                        )
                    })
                });
                match counts {
                    Ok((added, removed, work)) => {
                        diff_work_remaining = diff_work_remaining.saturating_sub(work);
                        (Some(added), Some(removed))
                    }
                    Err(cause) => {
                        diff_work_remaining = 0;
                        coverage.add(format!("{path}: {cause}"));
                        (None, None)
                    }
                }
            } else {
                (None, None)
            };
            files.push(FileRecord {
                id: uuid::Uuid::new_v4().to_string(),
                conversation_id: capture.conversation.clone(),
                turn_id: capture.turn.clone(),
                path: path.clone(),
                change: change.into(),
                added,
                removed,
                before_hash: before.map(|file| file.hash.clone()),
                after_hash: next.map(|file| file.hash.clone()),
                timestamp: timestamp.clone(),
                size: data.size,
                mime: data.mime.clone(),
                source: path_text(&capture.workspace.join(&path))?.to_owned(),
                snapshot_available: true,
                origin: "workspace".into(),
                status: status.to_owned(),
            });
        }
        let coverage = coverage.finish();
        self.save_finished(
            &capture.capture_id,
            status,
            &timestamp,
            &files,
            &coverage,
            None,
        )?;
        self.latest_changes(&json!({"conversationId":capture.conversation,"turnId":capture.turn}))
    }

    /// Output paths come from the studio's persisted, owned output list. Large binaries are references, not copied weights.
    pub fn register_outputs(
        &self,
        conversation: &str,
        job: &str,
        paths: &[PathBuf],
    ) -> Result<Value, String> {
        let sources: Vec<_> = paths
            .iter()
            .map(|path| OutputSource {
                path: path.clone(),
                name: None,
                mime: None,
            })
            .collect();
        self.record_outputs(conversation, job, &sources, "studio", "completed", "output")
    }
    fn record_outputs(
        &self,
        conversation: &str,
        job: &str,
        paths: &[OutputSource],
        origin: &str,
        status: &str,
        kind: &str,
    ) -> Result<Value, String> {
        valid_label(conversation, "conversation", true)?;
        valid_label(job, "job", false)?;
        let capture_id = format!(
            "{kind}:{origin}:{}",
            sha256(format!("{conversation}\0{job}").as_bytes())
        );
        let timestamp = now();
        self.database()?.execute("INSERT OR IGNORE INTO turns(capture_id,conversation_id,turn_id,kind,status,workspace,started_at,finished_at,coverage) VALUES(?1,?2,?3,?4,?5,'',?6,?6,'[]')", params![capture_id, conversation, job, kind, status, timestamp]).map_err(err)?;
        let mut files = Vec::new();
        let mut references = BTreeMap::new();
        let mut coverage = Coverage::default();
        let mut total_read = 0u64;
        for output in paths.iter().take(self.limits.max_entries.min(1000)) {
            let path = &output.path;
            let record = (|| -> Result<(FileRecord, PathBuf), String> {
                let path = absolute(path)?;
                reject_links(&path)?;
                let path = fs::canonicalize(&path).map_err(err)?;
                let root = safe_root(path.parent().ok_or("Output has no parent directory")?)?;
                let file = open_confined(&root, &path)?;
                let mut size = file.metadata().map_err(err)?.len();
                let name = path_text(&path)?.to_owned();
                let display_name = output.name.as_deref().unwrap_or(&name);
                valid_label(display_name, "output name", false)?;
                let hinted_mime = output.mime.as_deref().map(normalize_mime).transpose()?;
                let mut mime = hinted_mime
                    .clone()
                    .unwrap_or_else(|| mime_for(Path::new(display_name), false).to_owned());
                let mut saved = false;
                let hash = if is_weight(&path)
                    || size > self.limits.max_reference_bytes
                    || total_read.saturating_add(size) > self.limits.max_reference_bytes
                {
                    coverage.add(format!(
                        "Output reference only (hash/size limit or model weights): {name}"
                    ));
                    None
                } else if size <= self.limits.max_file_bytes {
                    let bytes = read_confined(&root, &path, self.limits.max_file_bytes)?;
                    size = bytes.len() as u64;
                    total_read += size;
                    let text = hinted_mime
                        .as_deref()
                        .map(|mime| is_text_for_mime(&bytes, mime))
                        .unwrap_or_else(|| is_text(&bytes, Path::new(display_name)));
                    mime = hinted_mime
                        .clone()
                        .unwrap_or_else(|| mime_for(Path::new(display_name), text).to_owned());
                    let hash = self.put_snapshot(&bytes)?;
                    saved = true;
                    Some(hash)
                } else {
                    let (hash, observed_size) =
                        hash_confined(&root, &path, self.limits.max_reference_bytes)?;
                    size = observed_size;
                    total_read += size;
                    coverage.add(format!("Large output is a verified source reference without a copied snapshot: {name}"));
                    Some(hash)
                };
                let id = sha256(
                    format!(
                        "{origin}\0{conversation}\0{job}\0{name}\0{}",
                        hash.as_deref().unwrap_or("unhashed")
                    )
                    .as_bytes(),
                );
                let record = FileRecord {
                    id: uuid_from_hash(&id),
                    conversation_id: conversation.into(),
                    turn_id: job.into(),
                    path: display_name.to_owned(),
                    change: "output".into(),
                    added: None,
                    removed: None,
                    before_hash: None,
                    after_hash: hash,
                    timestamp: timestamp.clone(),
                    size,
                    mime,
                    source: name,
                    snapshot_available: saved,
                    origin: origin.into(),
                    status: status.into(),
                };
                Ok((record, root))
            })();
            match record {
                Ok((record, reference)) => {
                    references.insert(record.id.clone(), reference);
                    files.push(record);
                }
                Err(cause) => coverage.add(format!("Output {} omitted: {cause}", path.display())),
            }
        }
        if paths.len() > self.limits.max_entries.min(1000) {
            coverage.add("Output path traversal limit reached");
        }
        let coverage = coverage.finish();
        self.save_finished(
            &capture_id,
            status,
            &timestamp,
            &files,
            &coverage,
            Some(&references),
        )?;
        Ok(json!({"files":files,"coverage":coverage,"turnId":job}))
    }

    pub fn command(&self, args: Value) -> Result<Value, String> {
        match args.get("action").and_then(Value::as_str).unwrap_or("list") {
            "list" => self.list(&args),
            "changes" => self.latest_changes(&args),
            "preview" => self.preview(&args),
            "diff" => self.diff(&args),
            "index" => self.index(&args),
            _ => Err("Unknown workspace file action".into()),
        }
    }

    fn scan(&self, workspace: &Path) -> Result<Scan, String> {
        let workspace = safe_root(workspace)?;
        let mut result = Scan::default();
        let mut coverage = Coverage::default();
        let mut stack = vec![(workspace.clone(), String::new(), 0usize)];
        let mut visited = 0usize;
        let mut bytes_read = 0u64;
        while let Some((directory, relative_dir, depth)) = stack.pop() {
            if visited >= self.limits.max_entries {
                coverage.add(format!(
                    "Traversal limit {} reached; unvisited paths omitted",
                    self.limits.max_entries
                ));
                break;
            }
            if depth > self.limits.max_depth {
                coverage.add(format!("Directory depth limit reached: {relative_dir}"));
                continue;
            }
            if let Err(cause) = reject_links(&directory) {
                coverage.add(format!("Directory omitted: {relative_dir}: {cause}"));
                continue;
            }
            let resolved = match fs::canonicalize(&directory) {
                Ok(path) if path.starts_with(&workspace) => path,
                _ => {
                    coverage.add(format!(
                        "Unconfined or unavailable directory omitted: {relative_dir}"
                    ));
                    continue;
                }
            };
            if resolved == self.root || resolved.starts_with(&self.root) {
                coverage.add(format!("File ledger storage excluded: {relative_dir}"));
                continue;
            }
            let entries = match fs::read_dir(&directory) {
                Ok(entries) => entries,
                Err(cause) => {
                    coverage.add(format!("Unreadable directory {relative_dir}: {cause}"));
                    continue;
                }
            };
            let remaining = self.limits.max_entries - visited;
            let mut children = Vec::new();
            let mut complete = true;
            for entry in entries.take(remaining + 1) {
                match entry {
                    Ok(entry) => children.push(entry.path()),
                    Err(cause) => {
                        complete = false;
                        coverage.add(format!(
                            "Directory enumeration omitted entries in {relative_dir}: {cause}"
                        ));
                    }
                }
            }
            if children.len() > remaining {
                complete = false;
                children.truncate(remaining);
                coverage.add(format!(
                    "Traversal limit {} reached in {relative_dir}; unvisited paths omitted",
                    self.limits.max_entries
                ));
            }
            if complete {
                result.listed_dirs.insert(relative_dir.clone());
            }
            children.sort();
            let mut subdirectories = Vec::new();
            for path in children {
                visited += 1;
                let relative = match path.strip_prefix(&workspace).ok().and_then(Path::to_str) {
                    Some(path) => path.replace('\\', "/"),
                    None => {
                        coverage.add(format!("Non-UTF8 path omitted: {}", path.display()));
                        continue;
                    }
                };
                result.known.insert(relative.clone());
                let metadata = match fs::symlink_metadata(&path) {
                    Ok(metadata) => metadata,
                    Err(cause) => {
                        coverage.add(format!("Metadata unavailable for {relative}: {cause}"));
                        continue;
                    }
                };
                if is_link(&metadata) {
                    coverage.add(format!("symlink/reparse point excluded: {relative}"));
                    continue;
                }
                if metadata.is_dir() {
                    if excluded_directory(
                        path.file_name()
                            .and_then(|name| name.to_str())
                            .unwrap_or(""),
                    ) {
                        coverage.add(format!(
                            "Dependency/build/VCS directory excluded: {relative}"
                        ));
                    } else {
                        subdirectories.push((path, relative, depth + 1));
                    }
                    continue;
                }
                if !metadata.is_file() {
                    coverage.add(format!("Non-regular file excluded: {relative}"));
                    continue;
                }
                if is_weight(&path) {
                    coverage.add(format!("Model weight file excluded: {relative}"));
                    continue;
                }
                if metadata.len() > self.limits.max_file_bytes {
                    coverage.add(format!(
                        "File size limit {} bytes exceeded: {relative}",
                        self.limits.max_file_bytes
                    ));
                    continue;
                }
                if bytes_read.saturating_add(metadata.len()) > self.limits.max_total_bytes {
                    coverage.add(format!(
                        "Capture byte limit {} bytes reached: {relative}",
                        self.limits.max_total_bytes
                    ));
                    continue;
                }
                let bytes = match read_confined(
                    &workspace,
                    &path,
                    self.limits
                        .max_file_bytes
                        .min(self.limits.max_total_bytes - bytes_read),
                ) {
                    Ok(bytes) => bytes,
                    Err(cause) => {
                        coverage.add(format!("File omitted: {relative}: {cause}"));
                        continue;
                    }
                };
                bytes_read += bytes.len() as u64;
                let text = is_text(&bytes, &path);
                match self.put_snapshot(&bytes) {
                    Ok(hash) => {
                        result.files.insert(
                            relative,
                            CapturedFile {
                                hash,
                                size: bytes.len() as u64,
                                mime: mime_for(&path, text).to_owned(),
                                text,
                            },
                        );
                    }
                    Err(cause) => {
                        coverage.add(format!("Snapshot unavailable for {relative}: {cause}"))
                    }
                }
            }
            for entry in subdirectories.into_iter().rev() {
                stack.push(entry);
            }
        }
        result.coverage = coverage.finish();
        Ok(result)
    }

    fn object_path(&self, hash: &str) -> Result<PathBuf, String> {
        if hash.len() != 64
            || !hash
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err("Invalid snapshot hash".into());
        }
        Ok(self.objects.join(&hash[..2]).join(format!("{hash}.blob")))
    }
    fn put_snapshot(&self, bytes: &[u8]) -> Result<String, String> {
        let hash = sha256(bytes);
        let path = self.object_path(&hash)?;
        let _writer = self
            .object_writer
            .lock()
            .map_err(|_| "Snapshot writer lock is unavailable")?;
        reject_links(&path)?;
        if path.exists() {
            self.read_snapshot(&hash)?;
            return Ok(hash);
        }
        let parent = path.parent().ok_or("Snapshot has no parent")?;
        reject_links(parent)?;
        fs::create_dir_all(parent).map_err(err)?;
        let temporary = parent.join(format!("{}.tmp", uuid::Uuid::new_v4()));
        let write = (|| -> Result<(), String> {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temporary)
                .map_err(err)?;
            file.write_all(bytes).map_err(err)?;
            file.sync_all().map_err(err)?;
            reject_links(&path)?;
            fs::rename(&temporary, &path).map_err(err)?;
            Ok(())
        })();
        if let Err(cause) = write {
            let _ = fs::remove_file(&temporary);
            if path.exists() {
                self.read_snapshot(&hash)?;
            } else {
                return Err(cause);
            }
        }
        Ok(hash)
    }
    fn read_snapshot(&self, hash: &str) -> Result<Vec<u8>, String> {
        let bytes = read_confined(
            &self.objects,
            &self.object_path(hash)?,
            self.limits.max_file_bytes,
        )?;
        if sha256(&bytes) != hash {
            return Err("Snapshot hash verification failed".into());
        }
        Ok(bytes)
    }
    fn snapshot_text(&self, file: Option<&CapturedFile>) -> Result<Option<String>, String> {
        file.map(|file| {
            self.read_snapshot(&file.hash).and_then(|bytes| {
                String::from_utf8(bytes).map_err(|_| "Snapshot is not UTF-8 text".into())
            })
        })
        .transpose()
    }

    fn save_finished(
        &self,
        capture_id: &str,
        status: &str,
        timestamp: &str,
        files: &[FileRecord],
        coverage: &[String],
        references: Option<&BTreeMap<String, PathBuf>>,
    ) -> Result<(), String> {
        let mut db = self.database()?;
        let transaction = db.transaction().map_err(err)?;
        for file in files {
            let reference = references
                .and_then(|references| references.get(&file.id))
                .map(|path| path_text(path).map(str::to_owned))
                .transpose()?;
            transaction.execute("INSERT OR IGNORE INTO files(id,capture_id,path,before_hash,after_hash,record,reference_root) VALUES(?1,?2,?3,?4,?5,?6,?7)", params![file.id, capture_id, file.path, file.before_hash, file.after_hash, serde_json::to_string(file).map_err(err)?, reference]).map_err(err)?;
            // A completed job may strengthen an earlier indexed observation of that exact same output.
            transaction.execute("UPDATE files SET capture_id=?1,record=?2,reference_root=?3 WHERE id=?4 AND ?5='completed' AND capture_id IN(SELECT capture_id FROM turns WHERE kind='index')", params![capture_id, serde_json::to_string(file).map_err(err)?, reference, file.id, status]).map_err(err)?;
        }
        transaction.execute("UPDATE turns SET status=?2,finished_at=?3,baseline=NULL,coverage=?4 WHERE capture_id=?1", params![capture_id, status, timestamp, serde_json::to_string(coverage).map_err(err)?]).map_err(err)?;
        transaction.commit().map_err(err)
    }

    fn list(&self, args: &Value) -> Result<Value, String> {
        let conversation = optional_arg(args, "conversationId");
        let search = optional_arg(args, "search")
            .filter(|search| !search.is_empty())
            .map(|search| {
                format!(
                    "%{}%",
                    search
                        .replace('\\', "\\\\")
                        .replace('%', "\\%")
                        .replace('_', "\\_")
                )
            });
        let limit = args["limit"].as_u64().unwrap_or(300).clamp(1, 2000) as usize;
        let db = self.database()?;
        let mut statement = db.prepare("SELECT f.record,t.coverage FROM files f JOIN turns t ON f.capture_id=t.capture_id WHERE (?1 IS NULL OR t.conversation_id=?1) AND (?2 IS NULL OR f.path LIKE ?2 ESCAPE '\' OR t.conversation_id LIKE ?2 ESCAPE '\' OR t.turn_id LIKE ?2 ESCAPE '\' OR f.record LIKE ?2 ESCAPE '\') ORDER BY t.sequence DESC,f.path ASC,f.rowid DESC LIMIT ?3").map_err(err)?;
        let rows = statement
            .query_map(params![conversation, search, (limit + 1) as i64], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })
            .map_err(err)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(err)?;
        let mut files = Vec::<FileRecord>::new();
        let mut coverage = Coverage::default();
        if rows.len() > limit {
            coverage.add(format!("Showing {limit} newest matching file versions; refine the search for older history"));
        }
        for (record, notes) in rows.into_iter().take(limit) {
            files.push(serde_json::from_str(&record).map_err(err)?);
            coverage.extend(serde_json::from_str::<Vec<String>>(&notes).map_err(err)?);
        }
        let mut statement = db.prepare("SELECT coverage FROM turns WHERE (?1 IS NULL OR conversation_id=?1) ORDER BY sequence DESC LIMIT 10").map_err(err)?;
        let notes = statement
            .query_map(params![conversation], |row| row.get::<_, String>(0))
            .map_err(err)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(err)?;
        for notes in notes {
            coverage.extend(serde_json::from_str::<Vec<String>>(&notes).map_err(err)?);
        }
        Ok(json!({"files":files,"coverage":coverage.finish()}))
    }
    fn latest_changes(&self, args: &Value) -> Result<Value, String> {
        let conversation = required_arg(args, "conversationId")?;
        let turn = optional_arg(args, "turnId");
        let db = self.database()?;
        let row = db.query_row("SELECT t.turn_id,t.status,COALESCE(t.finished_at,t.started_at) FROM turns t WHERE t.conversation_id=?1 AND (?2 IS NULL OR t.turn_id=?2) AND (t.kind='task' OR (t.kind='output' AND NOT EXISTS(SELECT 1 FROM turns parent WHERE parent.kind='task' AND parent.conversation_id=t.conversation_id AND parent.turn_id=t.turn_id))) ORDER BY t.sequence DESC LIMIT 1", params![conversation, turn], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?))).optional().map_err(err)?;
        let Some((turn, status, timestamp)) = row else {
            return Ok(changes_value(None, "completed", "", &[], &[]));
        };
        let mut statement = db.prepare("SELECT f.record FROM files f JOIN turns t ON t.capture_id=f.capture_id WHERE t.conversation_id=?1 AND t.turn_id=?2 AND t.kind!='index' ORDER BY CASE WHEN t.kind='task' THEN 0 ELSE 1 END,f.path,f.id").map_err(err)?;
        let records = statement
            .query_map(params![conversation, turn], |row| row.get::<_, String>(0))
            .map_err(err)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(err)?;
        let files = records
            .iter()
            .map(|record| serde_json::from_str::<FileRecord>(record).map_err(err))
            .collect::<Result<Vec<_>, _>>()?;
        let mut unique = BTreeMap::new();
        for file in files {
            unique
                .entry((
                    file.source.clone(),
                    file.before_hash.clone(),
                    file.after_hash.clone(),
                ))
                .or_insert(file);
        }
        let files: Vec<_> = unique.into_values().collect();
        let mut statement = db.prepare("SELECT coverage FROM turns WHERE conversation_id=?1 AND turn_id=?2 AND kind!='index'").map_err(err)?;
        let notes = statement
            .query_map(params![conversation, turn], |row| row.get::<_, String>(0))
            .map_err(err)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(err)?;
        let mut coverage = Coverage::default();
        for notes in notes {
            coverage.extend(serde_json::from_str::<Vec<String>>(&notes).map_err(err)?);
        }
        Ok(changes_value(
            Some(&turn),
            &status,
            &timestamp,
            &files,
            &coverage.finish(),
        ))
    }
    fn get_record(&self, id: &str) -> Result<(FileRecord, Option<PathBuf>), String> {
        uuid::Uuid::parse_str(id).map_err(|_| "Invalid file record ID".to_owned())?;
        let row = self
            .database()?
            .query_row(
                "SELECT record,reference_root FROM files WHERE id=?1",
                params![id],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?)),
            )
            .optional()
            .map_err(err)?
            .ok_or("Unknown file record ID")?;
        Ok((
            serde_json::from_str(&row.0).map_err(err)?,
            row.1.map(PathBuf::from),
        ))
    }
    fn version_bytes(
        &self,
        record: &FileRecord,
        reference: Option<&Path>,
        before: bool,
    ) -> Result<(Vec<u8>, String, bool), String> {
        let hash = if before {
            record.before_hash.as_ref()
        } else {
            record.after_hash.as_ref()
        }
        .ok_or_else(|| {
            format!(
                "No {} snapshot or verified reference for this file",
                if before { "before" } else { "after" }
            )
        })?;
        let object = self.object_path(hash)?;
        if object.exists() || fs::symlink_metadata(&object).is_ok() {
            return Ok((self.read_snapshot(hash)?, hash.clone(), true));
        }
        if before || record.snapshot_available {
            return Err(
                "Recorded snapshot is unavailable; the current source is not a substitute".into(),
            );
        }
        let root = reference.ok_or("This record has no confined source reference")?;
        let bytes = read_confined(
            root,
            Path::new(&record.source),
            self.limits.max_preview_bytes,
        )?;
        if bytes.len() as u64 != record.size || sha256(&bytes) != *hash {
            return Err(
                "Source reference hash verification failed; this output has changed".into(),
            );
        }
        Ok((bytes, hash.clone(), false))
    }
    fn preview(&self, args: &Value) -> Result<Value, String> {
        let (record, reference) = self.get_record(required_arg(args, "id")?)?;
        let version = optional_arg(args, "version").unwrap_or(if record.change == "deleted" {
            "before"
        } else {
            "after"
        });
        if version != "before" && version != "after" {
            return Err("File version must be before or after".into());
        }
        let (bytes, hash, saved) =
            self.version_bytes(&record, reference.as_deref(), version == "before")?;
        let mut result = json!({"name":Path::new(&record.path).file_name().map(|name|name.to_string_lossy()).unwrap_or_default(),"mime":record.mime,"sha256":hash,"size":bytes.len(),"snapshotAvailable":saved});
        if is_text_for_mime(&bytes, &record.mime) {
            result["text"] = Value::String(String::from_utf8(bytes.clone()).map_err(err)?);
        }
        if record.mime.starts_with("image/")
            || record.mime.starts_with("audio/")
            || record.mime.starts_with("video/")
        {
            result["dataUrl"] = Value::String(format!(
                "data:{};base64,{}",
                record.mime,
                STANDARD.encode(&bytes)
            ));
        }
        Ok(result)
    }
    fn diff(&self, args: &Value) -> Result<Value, String> {
        let (record, reference) = self.get_record(required_arg(args, "id")?)?;
        let mut coverage = Vec::new();
        let old = record
            .before_hash
            .as_ref()
            .map(|_| {
                self.version_bytes(&record, reference.as_deref(), true)
                    .map(|value| value.0)
            })
            .transpose()?;
        let new = record
            .after_hash
            .as_ref()
            .map(|_| {
                self.version_bytes(&record, reference.as_deref(), false)
                    .map(|value| value.0)
            })
            .transpose()?;
        let binary = old
            .iter()
            .chain(new.iter())
            .any(|bytes| !is_text_for_mime(bytes, &record.mime));
        let before = if binary {
            None
        } else {
            old.map(String::from_utf8).transpose().map_err(err)?
        };
        let after = if binary {
            None
        } else {
            new.map(String::from_utf8).transpose().map_err(err)?
        };
        if record.added.is_none() && !binary && record.origin == "workspace" {
            coverage.push(
                "Exact line counts were unavailable within the captured diff work limit".to_owned(),
            );
        }
        Ok(
            json!({"path":record.path,"before":before,"after":after,"added":record.added,"removed":record.removed,"binary":binary,"coverage":coverage}),
        )
    }
    fn index(&self, args: &Value) -> Result<Value, String> {
        if args.get("paths").is_some() || args.get("entries").is_some() {
            let mut paths = Vec::new();
            if let Some(entries) = args.get("entries") {
                let entries = entries
                    .as_array()
                    .ok_or("Output entries must be an array")?;
                for entry in entries {
                    paths.push(OutputSource {
                        path: PathBuf::from(required_arg(entry, "path")?),
                        name: optional_arg(entry, "name").map(str::to_owned),
                        mime: optional_arg(entry, "mime").map(str::to_owned),
                    });
                }
            }
            if let Some(entries) = args.get("paths") {
                let entries = entries.as_array().ok_or("Output paths must be an array")?;
                for path in entries {
                    paths.push(OutputSource {
                        path: PathBuf::from(path.as_str().ok_or("Output paths must be strings")?),
                        name: None,
                        mime: None,
                    });
                }
            }
            let conversation = optional_arg(args, "conversationId").unwrap_or("");
            let job = required_arg(args, "jobId")?;
            let origin = optional_arg(args, "source").unwrap_or("indexed");
            if !matches!(origin, "studio" | "published" | "indexed") {
                return Err("Existing output source must be studio, published or indexed".into());
            }
            let live = args["live"].as_bool().unwrap_or(false);
            return self.record_outputs(
                conversation,
                job,
                &paths,
                origin,
                if live { "completed" } else { "indexed" },
                if live { "output" } else { "index" },
            );
        }
        let workspace = safe_root(Path::new(required_arg(args, "workspace")?))?;
        let conversation = optional_arg(args, "conversationId").unwrap_or("");
        valid_label(conversation, "conversation", true)?;
        let turn = optional_arg(args, "turnId")
            .map(str::to_owned)
            .unwrap_or_else(|| format!("index:{}", uuid::Uuid::new_v4()));
        valid_label(&turn, "turn", false)?;
        let scan = self.scan(&workspace)?;
        let timestamp = now();
        let capture_id = uuid::Uuid::new_v4().to_string();
        self.database()?.execute("INSERT INTO turns(capture_id,conversation_id,turn_id,kind,status,workspace,started_at,finished_at,coverage) VALUES(?1,?2,?3,'index','indexed',?4,?5,?5,?6)", params![capture_id, conversation, turn, path_text(&workspace)?, timestamp, serde_json::to_string(&scan.coverage).map_err(err)?]).map_err(err)?;
        let files = scan
            .files
            .into_iter()
            .map(|(path, data)| {
                Ok(FileRecord {
                    id: uuid::Uuid::new_v4().to_string(),
                    conversation_id: conversation.to_owned(),
                    turn_id: turn.clone(),
                    source: path_text(&workspace.join(&path))?.to_owned(),
                    path,
                    change: "output".into(),
                    added: None,
                    removed: None,
                    before_hash: None,
                    after_hash: Some(data.hash),
                    timestamp: timestamp.clone(),
                    size: data.size,
                    mime: data.mime,
                    snapshot_available: true,
                    origin: "indexed".into(),
                    status: "indexed".into(),
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        self.save_finished(
            &capture_id,
            "indexed",
            &timestamp,
            &files,
            &scan.coverage,
            None,
        )?;
        Ok(json!({"files":files,"coverage":scan.coverage}))
    }
    fn recover_interrupted(&self) -> Result<(), String> {
        let pending = {
            let db = self.database()?;
            let mut statement = db.prepare("SELECT capture_id,conversation_id,turn_id,workspace,baseline FROM turns WHERE kind='task' AND status='running' AND baseline IS NOT NULL").map_err(err)?;
            let rows = statement
                .query_map([], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                        row.get::<_, String>(4)?,
                    ))
                })
                .map_err(err)?
                .collect::<Result<Vec<_>, _>>()
                .map_err(err)?;
            rows
        };
        for (capture_id, conversation, turn, workspace, baseline) in pending {
            match serde_json::from_str::<Scan>(&baseline) {
                Ok(before) => {
                    self.finish_turn(
                        TurnCapture {
                            capture_id,
                            conversation,
                            turn,
                            workspace: PathBuf::from(workspace),
                            before,
                        },
                        "interrupted",
                    )?;
                }
                Err(cause) => self.save_finished(
                    &capture_id,
                    "interrupted",
                    &now(),
                    &[],
                    &[format!(
                        "Interrupted capture could not be recovered: {cause}"
                    )],
                    None,
                )?,
            }
        }
        Ok(())
    }
}

fn now() -> String {
    Utc::now().to_rfc3339()
}
fn err(cause: impl std::fmt::Display) -> String {
    cause.to_string()
}
fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn uuid_from_hash(hash: &str) -> String {
    format!(
        "{}-{}-{}-{}-{}",
        &hash[..8],
        &hash[8..12],
        &hash[12..16],
        &hash[16..20],
        &hash[20..32]
    )
}
fn optional_arg<'a>(args: &'a Value, name: &str) -> Option<&'a str> {
    args.get(name).and_then(Value::as_str)
}
fn required_arg<'a>(args: &'a Value, name: &str) -> Result<&'a str, String> {
    optional_arg(args, name)
        .filter(|text| !text.is_empty())
        .ok_or_else(|| format!("{name} is required"))
}
fn valid_label(label: &str, name: &str, allow_empty: bool) -> Result<(), String> {
    if (!allow_empty && label.is_empty()) || label.len() > 2048 || label.contains('\0') {
        Err(format!("Invalid {name}"))
    } else {
        Ok(())
    }
}
fn path_text(path: &Path) -> Result<&str, String> {
    path.to_str()
        .ok_or_else(|| "Non-UTF8 source paths cannot be recorded exactly".into())
}
fn changes_value(
    turn: Option<&str>,
    status: &str,
    timestamp: &str,
    files: &[FileRecord],
    coverage: &[String],
) -> Value {
    json!({"turnId":turn,"status":status,"timestamp":timestamp,"files":files,"added":files.iter().filter_map(|file|file.added).sum::<u64>(),"removed":files.iter().filter_map(|file|file.removed).sum::<u64>(),"coverage":coverage})
}
fn absolute(path: &Path) -> Result<PathBuf, String> {
    if path
        .components()
        .any(|part| matches!(part, Component::ParentDir))
    {
        return Err("Path traversal is not allowed".into());
    }
    if path.is_absolute() {
        Ok(path.to_owned())
    } else {
        std::env::current_dir()
            .map(|root| root.join(path))
            .map_err(err)
    }
}
fn safe_root(path: &Path) -> Result<PathBuf, String> {
    let path = absolute(path)?;
    reject_links(&path)?;
    let path = fs::canonicalize(path).map_err(err)?;
    if !path.is_dir() {
        return Err("Workspace must be a directory".into());
    }
    path_text(&path)?;
    Ok(path)
}
fn is_link(metadata: &fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return true;
        }
    }
    false
}
fn reject_links(path: &Path) -> Result<(), String> {
    let mut current = PathBuf::new();
    for part in path.components() {
        if matches!(part, Component::ParentDir) {
            return Err("Path traversal is not allowed".into());
        }
        current.push(part.as_os_str());
        if matches!(part, Component::Prefix(_)) {
            continue;
        }
        match fs::symlink_metadata(&current) {
            Ok(metadata) if is_link(&metadata) => {
                return Err(format!(
                    "symlink/reparse path excluded: {}",
                    current.display()
                ))
            }
            Ok(_) => {}
            Err(cause) if cause.kind() == std::io::ErrorKind::NotFound => {}
            Err(cause) => return Err(cause.to_string()),
        }
    }
    Ok(())
}
fn confirmed_absent(root: &Path, relative: &str) -> Result<bool, String> {
    let path = root.join(relative);
    reject_links(&path)?;
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(false),
        Err(cause) if cause.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(cause) => Err(cause.to_string()),
    }
}
fn open_confined(root: &Path, path: &Path) -> Result<File, String> {
    reject_links(root)?;
    reject_links(path)?;
    let root = fs::canonicalize(root).map_err(err)?;
    let resolved = fs::canonicalize(path).map_err(err)?;
    if !resolved.starts_with(&root) {
        return Err("File is outside its recorded workspace".into());
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x00200000);
    }
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(0x20000);
    }
    let file = options.open(path).map_err(err)?;
    let metadata = file.metadata().map_err(err)?;
    if is_link(&metadata) || !metadata.is_file() {
        return Err("Only regular, non-symlink files may be captured".into());
    }
    reject_links(path)?;
    if !fs::canonicalize(path).map_err(err)?.starts_with(&root) {
        return Err("File escaped its recorded workspace".into());
    }
    #[cfg(target_os = "linux")]
    {
        use std::os::fd::AsRawFd;
        let target = fs::read_link(format!("/proc/self/fd/{}", file.as_raw_fd())).map_err(err)?;
        if !target.starts_with(&root) {
            return Err("Opened file escaped its recorded workspace".into());
        }
    }
    #[cfg(windows)]
    {
        if !windows_handle_path(&file)?.starts_with(&root) {
            return Err("Opened file escaped its recorded workspace".into());
        }
    }
    Ok(file)
}
#[cfg(windows)]
fn windows_handle_path(file: &File) -> Result<PathBuf, String> {
    use std::os::windows::ffi::OsStringExt;
    use std::os::windows::io::AsRawHandle;
    #[link(name = "kernel32")]
    extern "system" {
        fn GetFinalPathNameByHandleW(
            handle: *mut std::ffi::c_void,
            buffer: *mut u16,
            length: u32,
            flags: u32,
        ) -> u32;
    }
    let mut buffer = vec![0u16; 1024];
    loop {
        let length = unsafe {
            GetFinalPathNameByHandleW(
                file.as_raw_handle(),
                buffer.as_mut_ptr(),
                buffer.len() as u32,
                0,
            )
        };
        if length == 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        if length as usize >= buffer.len() {
            if length > 32768 {
                return Err("Resolved file path is too long".into());
            }
            buffer.resize(length as usize + 1, 0);
            continue;
        }
        return Ok(PathBuf::from(std::ffi::OsString::from_wide(
            &buffer[..length as usize],
        )));
    }
}
fn unchanged(before: &fs::Metadata, after: &fs::Metadata) -> bool {
    before.len() == after.len() && before.modified().ok() == after.modified().ok()
}
pub(crate) fn read_confined(root: &Path, path: &Path, limit: u64) -> Result<Vec<u8>, String> {
    let mut file = open_confined(root, path)?;
    let before = file.metadata().map_err(err)?;
    if before.len() > limit {
        return Err(format!("Preview/capture size limit is {limit} bytes"));
    }
    let mut bytes = Vec::with_capacity(before.len() as usize);
    (&mut file)
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(err)?;
    if bytes.len() as u64 > limit
        || bytes.len() as u64 != before.len()
        || !unchanged(&before, &file.metadata().map_err(err)?)
    {
        return Err("File changed during capture; no exact snapshot was recorded".into());
    }
    Ok(bytes)
}
fn hash_confined(root: &Path, path: &Path, limit: u64) -> Result<(String, u64), String> {
    let mut file = open_confined(root, path)?;
    let before = file.metadata().map_err(err)?;
    if before.len() > limit {
        return Err("Output hash byte limit reached".into());
    }
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut read = 0u64;
    loop {
        let count = file.read(&mut buffer).map_err(err)?;
        if count == 0 {
            break;
        }
        read += count as u64;
        if read > limit {
            return Err("Output grew beyond its hash byte limit".into());
        }
        hasher.update(&buffer[..count]);
    }
    if read != before.len() || !unchanged(&before, &file.metadata().map_err(err)?) {
        return Err("Output changed while hashing".into());
    }
    Ok((format!("{:x}", hasher.finalize()), read))
}
fn excluded_directory(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        ".git"
            | ".hg"
            | ".svn"
            | ".bzr"
            | "node_modules"
            | "target"
            | "dist"
            | "build"
            | "vendor"
            | ".venv"
            | "venv"
            | "env"
            | "__pycache__"
            | ".cache"
            | ".next"
            | ".nuxt"
            | ".svelte-kit"
            | ".turbo"
            | ".pytest_cache"
            | ".mypy_cache"
            | ".ruff_cache"
            | ".tox"
            | ".nox"
            | ".worktrees"
            | ".superpowers"
    )
}
fn is_weight(path: &Path) -> bool {
    matches!(
        path.extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("")
            .to_ascii_lowercase()
            .as_str(),
        "gguf" | "ggml" | "safetensors" | "ckpt" | "pt" | "pth" | "onnx"
    )
}
fn is_text(bytes: &[u8], path: &Path) -> bool {
    is_text_for_mime(bytes, mime_for(path, true))
}
fn is_text_for_mime(bytes: &[u8], mime: &str) -> bool {
    !mime.starts_with("audio/")
        && !mime.starts_with("video/")
        && (!mime.starts_with("image/") || mime == "image/svg+xml")
        && mime != "model/gltf-binary"
        && mime != "application/pdf"
        && !bytes.contains(&0)
        && std::str::from_utf8(bytes).is_ok()
}
fn normalize_mime(mime: &str) -> Result<String, String> {
    let mime = mime
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    let parts: Vec<_> = mime.split('/').collect();
    if parts.len() != 2
        || parts.iter().any(|part| {
            part.is_empty()
                || !part
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b".+-_".contains(&byte))
        })
    {
        return Err("Invalid output MIME type".into());
    }
    Ok(mime)
}
fn mime_for(path: &Path, text: bool) -> &'static str {
    match path
        .extension()
        .and_then(|extension| extension.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "html" | "htm" => "text/html",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "ico" => "image/x-icon",
        "wav" => "audio/wav",
        "mp3" => "audio/mpeg",
        "flac" => "audio/flac",
        "ogg" => "audio/ogg",
        "m4a" => "audio/mp4",
        "mp4" => "video/mp4",
        "webm" => "video/webm",
        "mov" => "video/quicktime",
        "json" | "gltf" => "application/json",
        "glb" => "model/gltf-binary",
        "pdf" => "application/pdf",
        "css" => "text/css",
        "js" | "mjs" | "cjs" | "jsx" => "text/javascript",
        "ts" | "tsx" => "text/typescript",
        "md" => "text/markdown",
        "xml" => "application/xml",
        "csv" => "text/csv",
        _ if text => "text/plain",
        _ => "application/octet-stream",
    }
}

#[cfg(test)]
#[path = "workspace_ledger_tests.rs"]
mod tests;
