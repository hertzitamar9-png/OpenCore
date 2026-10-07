//! Serve chosen recorded files to the external browser, on a separate loopback origin.
//! The browser receives no filesystem paths, model APIs, or access to other captures.
use crate::workspace_ledger::{BrowserManifest, WorkspaceLedger};
use axum::{
    body::{Body, Bytes},
    extract::{Path, State},
    http::{HeaderMap, Method, Response, StatusCode},
    routing::get,
    Router,
};
use serde::Serialize;
use std::{collections::HashMap, sync::Arc, time::Instant};
use tokio::io::{AsyncReadExt, AsyncSeekExt};
use tokio_util::sync::CancellationToken;

// Permit ordinary generated games and recorded resources, plus common HTTPS asset CDNs.
// An arbitrary HTTPS source would also permit HTTPS loopback APIs, so use named origins.
const ASSET_CDNS: &str =
    "https://cdn.jsdelivr.net https://unpkg.com https://cdnjs.cloudflare.com https://esm.sh";

fn browser_policy() -> String {
    format!("default-src 'none'; script-src 'self' 'unsafe-inline' 'unsafe-eval' {ASSET_CDNS}; style-src 'self' 'unsafe-inline' {ASSET_CDNS} https://fonts.googleapis.com; img-src 'self' data: blob: {ASSET_CDNS}; font-src 'self' data: {ASSET_CDNS} https://fonts.gstatic.com; media-src 'self' data: blob:; connect-src 'self' {ASSET_CDNS}; worker-src 'self' blob:; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'")
}

struct Session {
    created: Instant,
    target: String,
    records: BrowserManifest,
    token: String,
    port: u16,
    ledger: Arc<WorkspaceLedger>,
    shutdown: CancellationToken,
}
pub(crate) struct FileBrowser {
    ledger: Arc<WorkspaceLedger>,
    sessions: tokio::sync::Mutex<HashMap<String, Arc<Session>>>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct BrowserOpen {
    pub url: String,
    pub name: String,
    pub sha256: String,
}
impl FileBrowser {
    pub(crate) fn new(ledger: Arc<WorkspaceLedger>) -> Arc<Self> {
        Arc::new(Self {
            ledger,
            sessions: tokio::sync::Mutex::new(HashMap::new()),
        })
    }
    pub(crate) async fn open(
        self: &Arc<Self>,
        id: String,
        version: Option<String>,
    ) -> Result<BrowserOpen, String> {
        let ledger = self.ledger.clone();
        let (target, records, name, sha256) = tauri::async_runtime::spawn_blocking(move || {
            let (target, records) = ledger.browser_manifest(&id, version.as_deref())?;
            let selected = records
                .get(&target)
                .ok_or("Selected file is absent from its capture")?;
            let file = ledger.browser_stream(selected)?;
            Ok::<_, String>((target, records, file.name, file.sha256))
        })
        .await
        .map_err(|error| error.to_string())??;
        let session = {
            let mut sessions = self.sessions.lock().await;
            if let Some(session) = sessions
                .values()
                .find(|session| session.target == target && session.records == records)
            {
                session.clone()
            } else {
                // Bound both metadata and listener ownership. Every session has a distinct
                // browser origin, so captured scripts cannot read another session's storage.
                if sessions.len() >= 64 {
                    if let Some(oldest) = sessions
                        .iter()
                        .min_by_key(|(_, session)| session.created)
                        .map(|(token, _)| token.clone())
                    {
                        if let Some(session) = sessions.remove(&oldest) {
                            session.shutdown.cancel();
                        }
                    }
                }
                let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
                    .await
                    .map_err(|error| error.to_string())?;
                let bound = listener
                    .local_addr()
                    .map_err(|error| error.to_string())?
                    .port();
                let session = Arc::new(Session {
                    created: Instant::now(),
                    target: target.clone(),
                    records,
                    token: uuid::Uuid::new_v4().to_string(),
                    port: bound,
                    ledger: self.ledger.clone(),
                    shutdown: CancellationToken::new(),
                });
                let service = Router::new()
                    .route("/{token}/{*path}", get(serve_file))
                    .with_state(session.clone());
                let shutdown = session.shutdown.clone();
                tauri::async_runtime::spawn(async move {
                    let _ = axum::serve(listener, service)
                        .with_graceful_shutdown(shutdown.cancelled_owned())
                        .await;
                });
                sessions.insert(session.token.clone(), session.clone());
                session
            }
        };
        let path = target
            .split('/')
            .map(|part| urlencoding::encode(part).into_owned())
            .collect::<Vec<_>>()
            .join("/");
        Ok(BrowserOpen {
            url: format!("http://127.0.0.1:{}/{}/{path}", session.port, session.token),
            name,
            sha256,
        })
    }
}

impl Drop for FileBrowser {
    fn drop(&mut self) {
        for session in self.sessions.get_mut().values() {
            session.shutdown.cancel();
        }
    }
}

async fn serve_file(
    State(session): State<Arc<Session>>,
    Path((token, path)): Path<(String, String)>,
    method: Method,
    headers: HeaderMap,
) -> Response<Body> {
    let host = headers.get("host").and_then(|value| value.to_str().ok());
    if host != Some(format!("127.0.0.1:{}", session.port).as_str()) {
        return error_response(StatusCode::FORBIDDEN, "Use the recorded local browser URL");
    }
    if token != session.token
        || path.contains('\\')
        || path.split('/').any(|part| matches!(part, "" | "." | ".."))
    {
        return error_response(StatusCode::NOT_FOUND, "Unknown recorded file");
    }
    let Some(selected) = session.records.get(&path).cloned() else {
        return error_response(
            StatusCode::NOT_FOUND,
            "This asset is not part of the recorded capture",
        );
    };
    let ledger = session.ledger.clone();
    match tauri::async_runtime::spawn_blocking(move || ledger.browser_stream(&selected)).await {
        Ok(Ok(file)) => {
            let etag = format!("\"{}\"", file.sha256);
            // HEAD describes the whole representation. A stale If-Range requests
            // the complete verified version rather than a range of another version.
            let requested = if method == Method::GET
                && headers
                    .get("if-range")
                    .map(|value| value.to_str().ok() == Some(etag.as_str()))
                    .unwrap_or(true)
            {
                headers.get("range")
            } else {
                None
            };
            let range = match requested {
                Some(value) => match value
                    .to_str()
                    .ok()
                    .and_then(|value| byte_range(value, file.size).ok())
                {
                    Some(range) => Some(range),
                    None => {
                        return Response::builder()
                            .status(StatusCode::RANGE_NOT_SATISFIABLE)
                            .header("content-range", format!("bytes */{}", file.size))
                            .header("accept-ranges", "bytes")
                            .header("etag", &etag)
                            .header("cache-control", "no-store")
                            .body(Body::empty())
                            .unwrap()
                    }
                },
                None => None,
            };
            let (start, length) = range.unwrap_or((0, file.size));
            let displayed = file.mime.starts_with("text/")
                || file.mime.starts_with("image/")
                || file.mime.starts_with("audio/")
                || file.mime.starts_with("video/")
                || matches!(file.mime.as_str(), "application/pdf" | "application/json");
            let mut response = Response::builder()
                .status(if range.is_some() {
                    StatusCode::PARTIAL_CONTENT
                } else {
                    StatusCode::OK
                })
                .header("content-type", &file.mime)
                .header("content-length", length.to_string())
                .header("accept-ranges", "bytes")
                .header("etag", &etag)
                .header("x-content-type-options", "nosniff")
                .header("cache-control", "no-store")
                .header("referrer-policy", "no-referrer")
                .header("content-security-policy", browser_policy());
            if range.is_some() {
                response = response.header(
                    "content-range",
                    format!("bytes {start}-{}/{}", start + length - 1, file.size),
                );
            }
            if !displayed {
                response = response.header(
                    "content-disposition",
                    format!(
                        "attachment; filename*=UTF-8''{}",
                        urlencoding::encode(&file.name)
                    ),
                );
            }
            let body = if method == Method::HEAD {
                Body::empty()
            } else {
                let stream = async_stream::stream! {
                    let mut source = tokio::fs::File::from_std(file.file);
                    if let Err(cause) = source.seek(std::io::SeekFrom::Start(start)).await {
                        yield Err::<Bytes,std::io::Error>(cause);
                        return;
                    }
                    let mut remaining = length;
                    let mut buffer = [0u8;64*1024];
                    while remaining > 0 {
                        let capacity = remaining.min(buffer.len() as u64) as usize;
                        match source.read(&mut buffer[..capacity]).await {
                            Ok(0) => {yield Err(std::io::Error::new(std::io::ErrorKind::UnexpectedEof,"Verified file was truncated during streaming"));break;}
                            Ok(count) => {remaining -= count as u64;yield Ok(Bytes::copy_from_slice(&buffer[..count]));}
                            Err(cause) => {yield Err(cause);break;}
                        }
                    }
                };
                Body::from_stream(stream)
            };
            response.body(body).unwrap_or_else(|_| {
                error_response(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Could not display this file",
                )
            })
        }
        Ok(Err(_)) => error_response(
            StatusCode::NOT_FOUND,
            "The recorded file is unavailable or failed verification",
        ),
        Err(_) => error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            "The file preview is unavailable",
        ),
    }
}

/// One RFC byte range, including open-ended and suffix ranges. Multipart
/// ranges are rejected so each response retains bounded memory and one handle.
fn byte_range(value: &str, size: u64) -> Result<(u64, u64), ()> {
    let value = value.trim().strip_prefix("bytes=").ok_or(())?;
    if size == 0 || value.contains(',') {
        return Err(());
    }
    let (start, end) = value.split_once('-').ok_or(())?;
    if start.is_empty() {
        let length = end.parse::<u64>().map_err(|_| ())?.min(size);
        if length == 0 {
            return Err(());
        }
        return Ok((size - length, length));
    }
    let start = start.parse::<u64>().map_err(|_| ())?;
    let end = if end.is_empty() {
        size - 1
    } else {
        end.parse::<u64>().map_err(|_| ())?.min(size - 1)
    };
    if start >= size || end < start {
        return Err(());
    }
    Ok((start, end - start + 1))
}
fn error_response(status: StatusCode, message: &str) -> Response<Body> {
    Response::builder()
        .status(status)
        .header("content-type", "text/plain; charset=utf-8")
        .header("cache-control", "no-store")
        .body(Body::from(message.to_owned()))
        .unwrap()
}

#[cfg(test)]
#[path = "file_browser_tests.rs"]
mod tests;
