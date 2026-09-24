use flate2::read::ZlibDecoder;
use rusqlite::{params, Connection, OpenFlags};
use serde::Serialize;
use sha2::{Digest, Sha256};
use base64::Engine;
use std::io::Read;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveConversation {
    pub conversation_id: String,
    pub pages: u64,
    pub source_bytes: u64,
    pub stored_bytes: u64,
    pub last_timestamp: f64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveOverview {
    pub archives: u64,
    pub pages: u64,
    pub source_bytes: u64,
    pub stored_bytes: u64,
    pub conversations: Vec<ArchiveConversation>,
    pub summaries: Vec<ArchiveSummary>,
    pub tool_calls: u64,
    pub tool_results: u64,
    pub image_assets: u64,
    pub file_events: u64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveSummary {
    pub conversation_id: String,
    pub generated_at: f64,
    pub source_pages: u64,
    pub model_calls: u64,
    pub truncated: bool,
    pub incomplete: bool,
    pub content: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveHit {
    pub archive_file: String,
    pub page_id: String,
    pub conversation_id: String,
    pub offset_start: u64,
    pub preview: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ArchivePageRef {
    pub archive_file: String,
    pub page_id: String,
    pub conversation_id: String,
    pub offset_start: u64,
    pub offset_end: u64,
    pub timestamp: f64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveEvent {
    pub event_id: String,
    pub conversation_id: String,
    pub timestamp: f64,
    pub kind: String,
    pub role: String,
    pub source: String,
    pub title: String,
    pub content: String,
    pub metadata: serde_json::Value,
    pub content_bytes: u64,
    pub truncated: bool,
}

fn archive_files(root: &Path) -> Result<Vec<PathBuf>, String> {
    if !root.exists() { return Ok(Vec::new()); }
    let mut files = std::fs::read_dir(root).map_err(|error| error.to_string())?
        .flatten().map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|extension| extension == "db"))
        .collect::<Vec<_>>();
    files.sort();
    Ok(files)
}

fn open(path: &Path) -> Result<Connection, String> {
    Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
        .map_err(|error| format!("{}: {error}", path.display()))
}

fn has_pages(connection: &Connection) -> bool {
    connection.query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages'", [], |_| Ok(()))
        .is_ok()
}

fn exact_text(compressed: &[u8], expected_hash: &str) -> Result<String, String> {
    let mut decoder = ZlibDecoder::new(compressed);
    let mut raw = Vec::new();
    decoder.read_to_end(&mut raw).map_err(|error| error.to_string())?;
    let actual = format!("{:x}", Sha256::digest(&raw));
    if actual != expected_hash { return Err("ECHO archive page failed its source hash check".into()); }
    String::from_utf8(raw).map_err(|error| error.to_string())
}

pub fn overview(root: &Path) -> Result<ArchiveOverview, String> {
    let mut result = ArchiveOverview { archives: 0, pages: 0, source_bytes: 0, stored_bytes: 0, conversations: Vec::new(), summaries: Vec::new(), tool_calls: 0, tool_results: 0, image_assets: 0, file_events: 0 };
    for path in archive_files(root)? {
        let connection = open(&path)?;
        if !has_pages(&connection) { continue; }
        result.archives += 1;
        if connection.query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_events'", [], |_| Ok(())).is_ok() {
            let mut counts = connection.prepare("SELECT kind,COUNT(*) FROM source_events GROUP BY kind")
                .map_err(|error| error.to_string())?;
            let rows = counts.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)? as u64)))
                .map_err(|error| error.to_string())?;
            for row in rows {
                let (kind, count) = row.map_err(|error| error.to_string())?;
                match kind.as_str() {
                    "tool_call" => result.tool_calls += count,
                    "tool_result" => result.tool_results += count,
                    "file" => result.file_events += count,
                    _ => {},
                }
            }
        }
        if connection.query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_assets'", [], |_| Ok(())).is_ok() {
            result.image_assets += connection.query_row("SELECT COUNT(*) FROM source_assets", [], |row| row.get::<_, i64>(0))
                .map_err(|error| error.to_string())? as u64;
        }
        let mut statement = connection.prepare("SELECT conversation_id,COUNT(*),COALESCE(SUM(offset_end-offset_start),0),COALESCE(SUM(LENGTH(compressed_bytes)),0),COALESCE(MAX(timestamp),0) FROM pages GROUP BY conversation_id")
            .map_err(|error| error.to_string())?;
        let rows = statement.query_map([], |row| Ok(ArchiveConversation {
            conversation_id: row.get(0)?, pages: row.get::<_, i64>(1)? as u64,
            source_bytes: row.get::<_, i64>(2)? as u64, stored_bytes: row.get::<_, i64>(3)? as u64,
            last_timestamp: row.get(4)?,
        })).map_err(|error| error.to_string())?;
        for row in rows {
            let item = row.map_err(|error| error.to_string())?;
            result.pages += item.pages;
            result.source_bytes += item.source_bytes;
            result.stored_bytes += item.stored_bytes;
            if let Some(previous) = result.conversations.iter_mut().find(|old| old.conversation_id == item.conversation_id) {
                previous.pages += item.pages;
                previous.source_bytes += item.source_bytes;
                previous.stored_bytes += item.stored_bytes;
                previous.last_timestamp = previous.last_timestamp.max(item.last_timestamp);
            } else { result.conversations.push(item); }
        }
        if connection.query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='derived_summaries'", [], |_| Ok(())).is_ok() {
            let mut summaries = connection.prepare("SELECT conversation_id,generated_at,source_pages,model_calls,truncated,incomplete,content FROM derived_summaries ORDER BY generated_at DESC")
                .map_err(|error| error.to_string())?;
            let rows = summaries.query_map([], |row| Ok(ArchiveSummary {
                conversation_id: row.get(0)?, generated_at: row.get(1)?,
                source_pages: row.get::<_, i64>(2)? as u64, model_calls: row.get::<_, i64>(3)? as u64,
                truncated: row.get::<_, i64>(4)? != 0, incomplete: row.get::<_, i64>(5)? != 0,
                content: row.get(6)?,
            })).map_err(|error| error.to_string())?;
            for row in rows { result.summaries.push(row.map_err(|error| error.to_string())?); }
        }
    }
    result.conversations.sort_by(|a, b| b.last_timestamp.total_cmp(&a.last_timestamp));
    result.summaries.sort_by(|a, b| b.generated_at.total_cmp(&a.generated_at));
    Ok(result)
}

fn match_expression(query: &str) -> Result<String, String> {
    let words = query.split(|character: char| !character.is_alphanumeric() && character != '_')
        .filter(|word| !word.is_empty()).take(16).collect::<Vec<_>>();
    if words.is_empty() { return Err("Search for a word, name, or code symbol".into()); }
    Ok(words.into_iter().map(|word| format!("\"{}\"", word.replace('"', ""))).collect::<Vec<_>>().join(" AND "))
}

pub fn search(root: &Path, query: &str, limit: usize, conversation_ids: &[String]) -> Result<Vec<ArchiveHit>, String> {
    let query = query.trim();
    if query.is_empty() { return Ok(Vec::new()); }
    let expression = match_expression(query)?;
    let mut hits = Vec::new();
    for path in archive_files(root)? {
        if hits.len() >= limit { break; }
        let connection = open(&path)?;
        if !has_pages(&connection) { continue; }
        let mut statement = connection.prepare("SELECT p.page_id,p.conversation_id,p.offset_start,p.content_hash,p.compressed_bytes FROM pages_fts f JOIN pages p ON p.page_id=f.page_id WHERE pages_fts MATCH ?1 ORDER BY p.timestamp DESC LIMIT 2000")
            .map_err(|error| error.to_string())?;
        let rows = statement.query_map([&expression], |row| Ok((
            row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, i64>(2)? as u64,
            row.get::<_, String>(3)?, row.get::<_, Vec<u8>>(4)?
        ))).map_err(|error| error.to_string())?;
        for row in rows {
            let (page_id, conversation_id, offset_start, hash, compressed) = row.map_err(|error| error.to_string())?;
            if !conversation_ids.is_empty() && !conversation_ids.contains(&conversation_id) { continue; }
            let text = exact_text(&compressed, &hash)?;
            let Some(start) = text.to_lowercase().find(&query.to_lowercase()) else { continue; };
            let mut snippet_start = start.saturating_sub(90);
            while !text.is_char_boundary(snippet_start) { snippet_start -= 1; }
            let mut snippet_end = (start + query.len() + 150).min(text.len());
            while !text.is_char_boundary(snippet_end) { snippet_end += 1; }
            hits.push(ArchiveHit { archive_file: path.file_name().unwrap().to_string_lossy().into_owned(), page_id,
                conversation_id, offset_start, preview: text[snippet_start..snippet_end].to_string() });
            if hits.len() >= limit { break; }
        }
    }
    Ok(hits)
}

pub fn page(root: &Path, archive_file: &str, page_id: &str) -> Result<String, String> {
    if archive_file != Path::new(archive_file).file_name().unwrap_or_default().to_string_lossy()
        || !archive_file.ends_with(".db") || page_id.len() != 64 || !page_id.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("Invalid archive page address".into());
    }
    let path = root.join(archive_file);
    let connection = open(&path)?;
    let (hash, compressed): (String, Vec<u8>) = connection.query_row(
        "SELECT content_hash,compressed_bytes FROM pages WHERE page_id=?", params![page_id],
        |row| Ok((row.get(0)?, row.get(1)?)),
    ).map_err(|error| error.to_string())?;
    exact_text(&compressed, &hash)
}

pub fn pages(root: &Path, conversation_id: &str, offset: usize, limit: usize) -> Result<Vec<ArchivePageRef>, String> {
    if conversation_id.trim().is_empty() || limit == 0 { return Ok(Vec::new()); }
    let mut result = Vec::new();
    for path in archive_files(root)? {
        let connection = open(&path)?;
        if !has_pages(&connection) { continue; }
        let mut statement = connection.prepare("SELECT page_id,offset_start,offset_end,timestamp FROM pages WHERE conversation_id=?1 ORDER BY timestamp,offset_start")
            .map_err(|error| error.to_string())?;
        let rows = statement.query_map([conversation_id], |row| Ok(ArchivePageRef {
            archive_file: path.file_name().unwrap().to_string_lossy().into_owned(),
            page_id: row.get(0)?, conversation_id: conversation_id.to_string(),
            offset_start: row.get::<_, i64>(1)? as u64, offset_end: row.get::<_, i64>(2)? as u64,
            timestamp: row.get(3)?,
        })).map_err(|error| error.to_string())?;
        for row in rows { result.push(row.map_err(|error| error.to_string())?); }
    }
    result.sort_by(|a, b| a.timestamp.total_cmp(&b.timestamp).then(a.offset_start.cmp(&b.offset_start)));
    Ok(result.into_iter().skip(offset).take(limit.min(100)).collect())
}

pub fn events(root: &Path, conversation_id: &str, offset: usize, limit: usize) -> Result<Vec<ArchiveEvent>, String> {
    if conversation_id.trim().is_empty() || limit == 0 { return Ok(Vec::new()); }
    let all = conversation_id == "*";
    let mut result = Vec::new();
    for path in archive_files(root)? {
        let connection = open(&path)?;
        if connection.query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_events'", [], |_| Ok(())).is_err() { continue; }
        let asset_column = if connection.query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_event_assets'", [], |_| Ok(())).is_ok() {
            "(SELECT json_group_array(json_object('name',name,'asset','echo-asset:'||asset_id)) FROM source_event_assets WHERE event_id=source_events.event_id)"
        } else { "'[]'" };
        let query = format!("SELECT event_id,conversation_id,timestamp,kind,role,source,title,substr(content,1,8000),CASE WHEN length(metadata)>65536 THEN '{{}}' ELSE metadata END,length(content),length(metadata),{asset_column} FROM source_events WHERE (?1='*' OR conversation_id=?1) ORDER BY timestamp DESC,event_id DESC LIMIT ?2");
        let mut statement = connection.prepare(&query)
            .map_err(|error| error.to_string())?;
        let per_file_limit = if all { offset.saturating_add(limit).min(300) } else { usize::MAX / 2 };
        let rows = statement.query_map(params![conversation_id, per_file_limit.min(i64::MAX as usize) as i64], |row| {
            let raw: String = row.get(8)?;
            let linked: String = row.get(11)?;
            let mut metadata: serde_json::Value = serde_json::from_str(&raw).unwrap_or_default();
            if let Ok(assets) = serde_json::from_str::<serde_json::Value>(&linked) {
                if assets.as_array().is_some_and(|items| !items.is_empty()) {
                    if let Some(object) = metadata.as_object_mut() { object.insert("assets".into(), assets); }
                }
            }
            Ok(ArchiveEvent { event_id: row.get(0)?, conversation_id: row.get(1)?, timestamp: row.get(2)?,
                kind: row.get(3)?, role: row.get(4)?, source: row.get(5)?, title: row.get(6)?,
                content: row.get(7)?, metadata,
                content_bytes: row.get::<_, i64>(9)? as u64,
                truncated: row.get::<_, i64>(9)? > 8000 || row.get::<_, i64>(10)? > 65536, })
        }).map_err(|error| error.to_string())?;
        for row in rows { result.push(row.map_err(|error| error.to_string())?); }
    }
    result.sort_by(|a, b| b.timestamp.total_cmp(&a.timestamp).then(b.event_id.cmp(&a.event_id)));
    Ok(result.into_iter().skip(offset).take(limit.min(100)).collect())
}

pub fn event(root: &Path, event_id: &str) -> Result<ArchiveEvent, String> {
    if event_id.len() != 64 || !event_id.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("Invalid ECHO event id".into());
    }
    for path in archive_files(root)? {
        let connection = open(&path)?;
        if connection.query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_events'", [], |_| Ok(())).is_err() { continue; }
        let found = connection.query_row("SELECT event_id,conversation_id,timestamp,kind,role,source,title,content,metadata FROM source_events WHERE event_id=?1", [event_id], |row| {
            let content: String = row.get(7)?;
            let raw: String = row.get(8)?;
            Ok(ArchiveEvent { event_id: row.get(0)?, conversation_id: row.get(1)?, timestamp: row.get(2)?,
                kind: row.get(3)?, role: row.get(4)?, source: row.get(5)?, title: row.get(6)?,
                content_bytes: content.len() as u64, content, metadata: serde_json::from_str(&raw).unwrap_or_default(), truncated: false })
        });
        if let Ok(found) = found { return Ok(found); }
    }
    Err("ECHO event was not found".into())
}

pub fn asset(root: &Path, asset_id: &str) -> Result<String, String> {
    if asset_id.len() != 64 || !asset_id.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("Invalid ECHO asset id".into());
    }
    for path in archive_files(root)? {
        let connection = open(&path)?;
        if connection.query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_assets'", [], |_| Ok(())).is_err() { continue; }
        let found = connection.query_row("SELECT mime,bytes FROM source_assets WHERE asset_id=?1", [asset_id], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, Vec<u8>>(1)?))
        });
        if let Ok((mime, bytes)) = found {
            if bytes.len() > 24 * 1024 * 1024 || !matches!(mime.as_str(), "image/png" | "image/jpeg" | "image/gif" | "image/webp") { return Err("ECHO image is not previewable".into()); }
            if format!("{:x}", Sha256::digest(&bytes)) != asset_id { return Err("ECHO image failed its source hash check".into()); }
            return Ok(format!("data:{mime};base64,{}", base64::engine::general_purpose::STANDARD.encode(bytes)));
        }
    }
    Err("ECHO image was not found".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_archive_search_and_scope() {
        let root = std::env::temp_dir().join(format!("opencore-archive-view-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let path = root.join("test.db");
        let connection = Connection::open(&path).unwrap();
        connection.execute_batch("CREATE TABLE pages(page_id TEXT PRIMARY KEY,conversation_id TEXT,offset_start INTEGER,offset_end INTEGER,timestamp REAL,content_hash TEXT,compressed_bytes BLOB);CREATE VIRTUAL TABLE pages_fts USING fts5(text,page_id UNINDEXED)").unwrap();
        let source = "assistant: exact code\n```python\nprint('ECHO')\n```";
        use std::io::Write;
        let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
        encoder.write_all(source.as_bytes()).unwrap();
        let compressed = encoder.finish().unwrap();
        let page_id = "a".repeat(64);
        connection.execute("INSERT INTO pages VALUES(?1,'chat-1',0,?2,1.0,?3,?4)", params![page_id, source.len(), format!("{:x}", Sha256::digest(source.as_bytes())), compressed]).unwrap();
        connection.execute("INSERT INTO pages_fts VALUES(?1,?2)", params![source, page_id]).unwrap();
        drop(connection);
        let found = search(&root, "exact code", 10, &[]).unwrap();
        assert_eq!(found.len(), 1);
        assert_eq!(page(&root, &found[0].archive_file, &found[0].page_id).unwrap(), source);
        assert!(search(&root, "exact code", 10, &["other".into()]).unwrap().is_empty());
        assert_eq!(overview(&root).unwrap().source_bytes, source.len() as u64);
        let listed = pages(&root, "chat-1", 0, 20).unwrap();
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].page_id, page_id);
        assert!(pages(&root, "chat-1", 1, 20).unwrap().is_empty());
        assert!(page(&root, "../test.db", &page_id).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn typed_events_and_image_assets_are_verified() {
        let root = std::env::temp_dir().join(format!("opencore-archive-assets-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let connection = Connection::open(root.join("events.db")).unwrap();
        connection.execute_batch("CREATE TABLE source_events(event_id TEXT PRIMARY KEY,conversation_id TEXT,timestamp REAL,kind TEXT,role TEXT,source TEXT,title TEXT,content TEXT,metadata TEXT);CREATE TABLE source_assets(asset_id TEXT PRIMARY KEY,mime TEXT,bytes BLOB);CREATE TABLE source_event_assets(event_id TEXT,asset_id TEXT,name TEXT,PRIMARY KEY(event_id,asset_id))").unwrap();
        let bytes = b"\x89PNG\r\n\x1a\nsource-image";
        let id = format!("{:x}", Sha256::digest(bytes));
        connection.execute("INSERT INTO source_events VALUES('event','chat',1.0,'tool_result','tool','OpenCore','screenshot','image','{}')", []).unwrap();
        connection.execute("INSERT INTO source_assets VALUES(?1,'image/png',?2)", params![id, bytes]).unwrap();
        connection.execute("INSERT INTO source_event_assets VALUES('event',?1,'Source screenshot')", [&id]).unwrap();
        drop(connection);
        let listed = events(&root, "chat", 0, 10).unwrap();
        assert_eq!(listed.len(), 1);
        assert_eq!(events(&root, "*", 0, 10).unwrap().len(), 1);
        assert_eq!(listed[0].kind, "tool_result");
        assert_eq!(listed[0].metadata["assets"][0]["asset"], format!("echo-asset:{id}"));
        assert!(asset(&root, &id).unwrap().starts_with("data:image/png;base64,"));
        assert!(asset(&root, &"0".repeat(64)).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn installed_archive_pages_are_readable_when_requested() {
        let Some(root) = std::env::var_os("OPENCORE_LIVE_ARCHIVE") else { return; };
        let root = PathBuf::from(root);
        let totals = overview(&root).unwrap();
        assert!(totals.pages > 0, "no installed ECHO pages found");
        let hits = search(&root, "assistant", 10, &[]).unwrap();
        assert!(!hits.is_empty(), "installed archive has no searchable assistant pages");
        for hit in hits {
            assert!(!page(&root, &hit.archive_file, &hit.page_id).unwrap().is_empty());
        }
    }
}
