//! Serve chosen recorded files to the external browser, on a separate loopback origin.
//! The browser receives no filesystem paths, model APIs, or access to other captures.
use crate::workspace_ledger::{BrowserManifest, WorkspaceLedger};
use axum::{
    body::Body,
    extract::{Path, State},
    http::{HeaderMap, Response, StatusCode},
    routing::get,
    Router,
};
use serde::Serialize;
use std::{collections::HashMap, sync::Arc, time::Instant};
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
            let file = ledger.browser_asset(selected)?;
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
    match tauri::async_runtime::spawn_blocking(move || ledger.browser_asset(&selected)).await {
        Ok(Ok(file)) => {
            let displayed = file.mime.starts_with("text/")
                || file.mime.starts_with("image/")
                || file.mime.starts_with("audio/")
                || file.mime.starts_with("video/")
                || matches!(file.mime.as_str(), "application/pdf" | "application/json");
            let mut response = Response::builder()
                .status(StatusCode::OK)
                .header("content-type", &file.mime)
                .header("x-content-type-options", "nosniff")
                .header("cache-control", "no-store")
                .header("referrer-policy", "no-referrer")
                .header("content-security-policy", browser_policy());
            if !displayed {
                response = response.header(
                    "content-disposition",
                    format!(
                        "attachment; filename*=UTF-8''{}",
                        urlencoding::encode(&file.name)
                    ),
                );
            }
            response.body(Body::from(file.bytes)).unwrap_or_else(|_| {
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
