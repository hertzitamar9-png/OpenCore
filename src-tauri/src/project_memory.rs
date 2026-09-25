//! Durable component checkpoints, scoped to a canonical workspace rather than a chat.
//! Summaries are model notes; version hashes and command receipts are observed evidence.
use crate::dev_tool::{read_text, sha256, workspace_file};
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::path::Path;

fn open(root: &Path, receipts: &Path) -> Result<Connection, String> {
    let identity = std::fs::canonicalize(root).map_err(|e| e.to_string())?;
    let identity = identity.to_string_lossy().to_string();
    #[cfg(windows)] let identity = identity.to_lowercase();
    let folder = receipts.parent().ok_or("Missing memory storage parent")?.join("project-memory");
    std::fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
    let connection = Connection::open(folder.join(format!("{}.sqlite3", sha256(identity.as_bytes())))).map_err(|e| e.to_string())?;
    connection.busy_timeout(std::time::Duration::from_secs(5)).map_err(|e| e.to_string())?;
    connection.execute_batch("PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS versions(hash TEXT PRIMARY KEY, content TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checkpoints(id INTEGER PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL,
            next_steps TEXT NOT NULL, files TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS file_versions(path TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(path,hash));
        CREATE VIRTUAL TABLE IF NOT EXISTS checkpoint_search USING fts5(title,summary,next_steps);")
        .map_err(|e| e.to_string())?;
    Ok(connection)
}

pub(crate) fn checkpoint(root: &Path, receipts: &Path, args: &Value, checks: &Value) -> Result<Value, String> {
    let title = args["title"].as_str().filter(|s| !s.trim().is_empty()).ok_or("checkpoint title is required")?;
    let summary = args["summary"].as_str().filter(|s| !s.trim().is_empty()).ok_or("checkpoint summary is required")?;
    let next = args["nextSteps"].as_str().unwrap_or_default();
    if title.len() > 200 || summary.len() > 8000 || next.len() > 4000 { return Err("Checkpoint notes exceed size limit".into()); }
    let paths = args["paths"].as_array().filter(|v| !v.is_empty() && v.len() <= 32).ok_or("Checkpoint needs 1-32 dependency paths")?;
    let mut connection = open(root, receipts)?;
    let transaction = connection.transaction().map_err(|e| e.to_string())?;
    let mut files = Vec::new();
    for path in paths {
        let name = path.as_str().ok_or("Dependency paths must be strings")?;
        let content = read_text(&workspace_file(root, name, false)?)?;
        let hash = sha256(content.as_bytes());
        transaction.execute("INSERT OR IGNORE INTO versions(hash,content) VALUES (?,?)", params![hash,content]).map_err(|e| e.to_string())?;
        transaction.execute("INSERT OR IGNORE INTO file_versions(path,hash) VALUES (?,?)", params![name,hash]).map_err(|e| e.to_string())?;
        let check = &checks[name];
        let verified = check["sha256"] == hash && check["command"].is_string();
        files.push(json!({"path":name,"sha256":hash,"check":if verified {check.clone()} else {Value::Null}}));
    }
    transaction.execute("INSERT INTO checkpoints(title,summary,next_steps,files,created_at) VALUES (?,?,?,?,?)",
        params![title,summary,next,json!(files).to_string(),chrono::Utc::now().to_rfc3339()]).map_err(|e| e.to_string())?;
    let id = transaction.last_insert_rowid();
    transaction.execute("INSERT INTO checkpoint_search(rowid,title,summary,next_steps) VALUES (?,?,?,?)",
        params![id,title,summary,next]).map_err(|e| e.to_string())?;
    transaction.commit().map_err(|e| e.to_string())?;
    Ok(json!({"checkpointSaved":true,"id":id,"title":title,"summary":summary,"nextSteps":next,"files":files,
        "evidence":if files.iter().all(|f| !f["check"].is_null()) {"checks-passed"} else {"unverified"},
        "note":"Notes are model assertions. Checks cover only their named command and file hashes."}))
}

pub(crate) fn recall(root: &Path, receipts: &Path, args: &Value) -> Result<Value, String> {
    let connection = open(root, receipts)?;
    let query = args["query"].as_str().unwrap_or_default();
    // Quoted tokens prevent FTS syntax from turning arbitrary user text into operators.
    let terms = query.split(|c: char| !c.is_alphanumeric()).filter(|s| s.len() > 1).take(32)
        .map(|s| format!("\"{s}\"" )).collect::<Vec<_>>().join(" OR ");
    let offset = args["offset"].as_u64().unwrap_or(0).min(i64::MAX as u64) as i64;
    let limit = args["limit"].as_u64().unwrap_or(8).clamp(1,32) as i64;
    let total: i64 = connection.query_row("SELECT COUNT(*) FROM checkpoints", [], |r| r.get(0)).map_err(|e| e.to_string())?;
    let sql = if terms.is_empty() {
        "SELECT id,title,summary,next_steps,files,created_at FROM checkpoints ORDER BY id DESC LIMIT ?2 OFFSET ?3"
    } else {
        "SELECT id,c.title,c.summary,c.next_steps,files,created_at FROM checkpoint_search s JOIN checkpoints c ON s.rowid=c.id
         WHERE checkpoint_search MATCH ?1 ORDER BY rank,c.id DESC LIMIT ?2 OFFSET ?3"
    };
    let mut statement = connection.prepare(sql).map_err(|e| e.to_string())?;
    let rows = statement.query_map(params![terms,limit+1,offset], |row| Ok((row.get::<_,i64>(0)?, row.get::<_,String>(1)?,
        row.get::<_,String>(2)?,row.get::<_,String>(3)?,row.get::<_,String>(4)?,row.get::<_,String>(5)?)))
        .map_err(|e| e.to_string())?;
    let mut notes = Vec::new();
    for row in rows {
        let (id,title,summary,next,files,created) = row.map_err(|e| e.to_string())?;
        let mut files: Vec<Value> = serde_json::from_str(&files).map_err(|e| e.to_string())?;
        let mut stale = false;
        for file in &mut files {
            let name = file["path"].as_str().unwrap_or_default();
            let current = workspace_file(root,name,false).and_then(|p| read_text(&p)).map(|s| sha256(s.as_bytes())).ok();
            let changed = current.as_deref() != file["sha256"].as_str();
            file["stale"] = json!(changed);
            file["currentSha256"] = json!(current);
            stale |= changed;
        }
        notes.push(json!({"id":id,"title":title,"summary":summary,"nextSteps":next,"createdAt":created,"stale":stale,
            "evidence":if stale {"stale"} else if files.iter().all(|f| !f["check"].is_null()) {"checks-passed"} else {"unverified"},"files":files}));
    }
    let more = notes.len() > limit as usize;
    notes.truncate(limit as usize);
    Ok(json!({"checkpoints":notes,"totalCheckpoints":total,"nextOffset":if more {Some(offset+limit)} else {None},
        "search":"lexical; no matches is not proof of absence; omit query to browse",
        "source":"Use dev read with path and versionSha256 for the exact archived source. Read current source before patching."}))
}

pub(crate) fn version(root: &Path, receipts: &Path, name: &str, hash: &str) -> Result<String, String> {
    let connection = open(root, receipts)?;
    let bound: bool = connection.query_row("SELECT EXISTS(SELECT 1 FROM file_versions WHERE path=? AND hash=?)",
        params![name,hash], |r| r.get(0)).map_err(|e| e.to_string())?;
    if !bound { return Err("This file version is not recorded in this project".into()); }
    let content: String = connection.query_row("SELECT content FROM versions WHERE hash=?", [hash], |r| r.get(0)).map_err(|e| e.to_string())?;
    if sha256(content.as_bytes()) != hash { return Err("Archived source hash verification failed".into()); }
    Ok(content)
}
