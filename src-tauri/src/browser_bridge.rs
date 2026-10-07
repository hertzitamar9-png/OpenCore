use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicBool, Ordering};
use tokio::sync::{mpsc, oneshot, watch};

pub(crate) const PORT: u16 = 8814;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;

pub(crate) struct BrowserBridge {
    token: String,
    sender: Mutex<Option<mpsc::UnboundedSender<Value>>>,
    pending: Mutex<HashMap<String, oneshot::Sender<Value>>>,
    enabled: AtomicBool,
    epoch: watch::Sender<u64>,
}

struct PendingCommand<'a> { bridge: &'a BrowserBridge, id: String }
impl Drop for PendingCommand<'_> {
    fn drop(&mut self) {
        if let Ok(mut pending) = self.bridge.pending.lock() { pending.remove(&self.id); }
    }
}

impl BrowserBridge {
    pub(crate) fn set_enabled(&self, enabled: bool) {
        // Registration, cancellation, and peer replacement share this lock.
        // No waiter can slip onto a socket after it has been disabled.
        if let Ok(mut sender) = self.sender.lock() {
            if self.enabled.swap(enabled, Ordering::SeqCst) == enabled { return; }
            *sender = None;
            self.epoch.send_modify(|epoch| *epoch = epoch.wrapping_add(1));
            if let Ok(mut pending) = self.pending.lock() { pending.clear(); }
        } else { self.enabled.store(false, Ordering::SeqCst); }
    }
    pub(crate) fn from_store(store: &crate::store::EventStore) -> Result<Self, String> {
        Ok(Self { token: store.get_or_create_pairing_token("browser_pairing_token_v1")?,
            sender: Mutex::new(None), pending: Mutex::new(HashMap::new()),
            enabled: AtomicBool::new(matches!(store.get_setting("browser_access_enabled_v1")?.as_deref(), None | Some("true"))),
            epoch: watch::channel(0).0 })
    }

    #[cfg(test)]
    pub(crate) fn new() -> Self {
        Self { token: uuid::Uuid::new_v4().to_string(), sender: Mutex::new(None), pending: Mutex::new(HashMap::new()),
            enabled: AtomicBool::new(true), epoch: watch::channel(0).0 }
    }

    pub(crate) fn status(&self) -> Value {
        json!({"port": PORT, "token": self.token, "enabled":self.enabled.load(Ordering::SeqCst),
            "connected": self.sender.lock().map(|sender| sender.as_ref().is_some_and(|tx| !tx.is_closed())).unwrap_or(false)})
    }

    pub(crate) async fn command(&self, action: &str, args: Value) -> Result<Value, String> {
        validate_action(action, &args)?;
        let id = uuid::Uuid::new_v4().to_string();
        let (tx, rx) = oneshot::channel();
        let _pending = PendingCommand { bridge: self, id: id.clone() };
        {
            let sender = self.sender.lock().map_err(|e| e.to_string())?;
            if !self.enabled.load(Ordering::SeqCst) { return Err("Browser access is disabled. Reconnect Chrome from Settings > Browser use.".into()); }
            let sender = sender.as_ref().ok_or("Chrome extension is disconnected. Connect it from the Browser panel.")?;
            self.pending.lock().map_err(|e| e.to_string())?.insert(id.clone(), tx);
            if sender.send(json!({"id":id,"action":action,"args":args})).is_err() {
                return Err("Chrome extension disconnected".into());
            }
        }
        let response = tokio::time::timeout(std::time::Duration::from_secs(25), rx).await;
        let payload = response.map_err(|_| "Chrome command timed out")?
            .map_err(|_| "Chrome extension disconnected")?;
        if payload.get("ok").and_then(Value::as_bool) == Some(true) {
            Ok(payload.get("result").cloned().unwrap_or(Value::Null))
        } else {
            Err(payload.get("error").and_then(Value::as_str).unwrap_or("Chrome command failed").to_string())
        }
    }
}

pub(crate) fn validate_action(action: &str, args: &Value) -> Result<(), String> {
    if !matches!(action, "list" | "open" | "navigate" | "activate" | "close" | "inspect" | "screenshot" | "click" | "type" | "click_element" | "type_element" | "scroll" | "key" | "back" | "forward" | "reload" | "evaluate") {
        return Err("Unsupported browser action".into());
    }
    if matches!(action, "open" | "navigate") {
        let url = args.get("url").and_then(Value::as_str).ok_or("URL is required")?;
        if url.len() > 2048 || !(url.starts_with("https://") || url.starts_with("http://")) {
            return Err("Only HTTP and HTTPS browser URLs are supported".into());
        }
    }
    if matches!(action, "click" | "type" | "scroll") {
        for coordinate in ["x", "y"] {
            let number = args.get(coordinate).and_then(Value::as_f64).ok_or("Browser coordinates are required")?;
            if !number.is_finite() || !(0.0..=10000.0).contains(&number) { return Err("Browser coordinates are out of bounds".into()); }
        }
    }
    if matches!(action, "click_element" | "type_element") && (args.get("elementId").and_then(Value::as_u64).is_none_or(|id| id >= 100)
        || args.get("snapshotId").and_then(Value::as_str).is_none_or(|id| id.is_empty() || id.len() > 64)) {
        return Err("Use elementId and snapshotId from inspect".into());
    }
    if matches!(action, "type" | "type_element") && args.get("text").and_then(Value::as_str).is_none_or(|text| text.chars().count() > 4000) {
        return Err("Browser text must be at most 4000 characters".into());
    }
    if action == "evaluate" && args.get("expression").and_then(Value::as_str).is_none_or(|code| code.is_empty() || code.len() > 16000) {
        return Err("DevTools expression must be 1 to 16000 characters".into());
    }
    if action == "key" && !matches!(args.get("key").and_then(Value::as_str), Some("Enter" | "Tab" | "Escape" | "Backspace" | "ArrowUp" | "ArrowDown" | "ArrowLeft" | "ArrowRight")) {
        return Err("Unsupported browser key".into());
    }
    if let Some(tab_id) = args.get("tabId") {
        if tab_id.as_i64().is_none_or(|id| id < 0) { return Err("Invalid Chrome tab id".into()); }
    }
    Ok(())
}

#[derive(Deserialize)]
struct Pairing { token: String }

async fn connect(State(bridge): State<Arc<BrowserBridge>>, Query(pairing): Query<Pairing>, upgrade: WebSocketUpgrade) -> Response {
    if pairing.token != bridge.token { return StatusCode::UNAUTHORIZED.into_response(); }
    if !bridge.enabled.load(Ordering::SeqCst) { return StatusCode::FORBIDDEN.into_response(); }
    upgrade.on_upgrade(move |socket| socket_loop(bridge, socket)).into_response()
}

async fn socket_loop(bridge: Arc<BrowserBridge>, socket: WebSocket) {
    let (mut writer, mut reader) = socket.split();
    let (tx, mut rx) = mpsc::unbounded_channel::<Value>();
    let mut epoch = {
        let Ok(mut current) = bridge.sender.lock() else { return };
        if !bridge.enabled.load(Ordering::SeqCst) { return; }
        bridge.epoch.send_modify(|value| *value = value.wrapping_add(1));
        if let Ok(mut pending) = bridge.pending.lock() { pending.clear(); }
        *current = Some(tx.clone());
        bridge.epoch.subscribe()
    };
    loop {
        let message = tokio::select! {
            biased;
            _ = epoch.changed() => { let _ = writer.send(Message::Close(None)).await; break; },
            command = rx.recv() => {
                let Some(command) = command else { break };
                if writer.send(Message::Text(command.to_string().into())).await.is_err() { break; }
                continue;
            },
            message = reader.next() => {
                let Some(Ok(message)) = message else { break };
                message
            }
        };
        let Message::Text(text) = message else { continue };
        if text.len() > MAX_RESPONSE_BYTES { break; }
        let Ok(value) = serde_json::from_str::<Value>(&text) else { continue };
        let Some(id) = value.get("id").and_then(Value::as_str) else { continue };
        if let Ok(current) = bridge.sender.lock() {
            if current.as_ref().is_some_and(|active| active.same_channel(&tx)) {
                if let Ok(mut pending) = bridge.pending.lock() {
                    if let Some(waiter) = pending.remove(id) { let _ = waiter.send(value); }
                }
            }
        }
    }
    if let Ok(mut current) = bridge.sender.lock() {
        if current.as_ref().is_some_and(|active| active.same_channel(&tx)) {
            *current = None;
            if let Ok(mut pending) = bridge.pending.lock() { pending.clear(); }
        }
    }
}

pub(crate) async fn serve(bridge: Arc<BrowserBridge>) -> Result<(), String> {
    let router = Router::new().route("/ws", get(connect)).with_state(bridge);
    let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, PORT)).await.map_err(|e| e.to_string())?;
    axum::serve(listener, router).await.map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::EventStore;

    #[test]
    fn pairing_identity_survives_reopening_the_app_database() {
        let path = std::env::temp_dir().join(format!("opencore-browser-pairing-{}.sqlite3", uuid::Uuid::new_v4()));
        let store = EventStore::open(&path).unwrap();
        let first = BrowserBridge::from_store(&store).unwrap().token;
        drop(store);
        let reopened = EventStore::open(&path).unwrap();
        let second = BrowserBridge::from_store(&reopened).unwrap().token;
        assert_eq!(first, second);
        assert_eq!(uuid::Uuid::parse_str(&first).unwrap().get_version_num(), 4);
        drop(reopened);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn invalid_saved_pairing_identity_is_repaired_once() {
        let path = std::env::temp_dir().join(format!("opencore-browser-pairing-{}.sqlite3", uuid::Uuid::new_v4()));
        let store = EventStore::open(&path).unwrap();
        store.set_setting("browser_pairing_token_v1", "00000000-0000-0000-0000-000000000000").unwrap();
        let repaired = BrowserBridge::from_store(&store).unwrap().token;
        assert_ne!(repaired, "00000000-0000-0000-0000-000000000000");
        assert_eq!(BrowserBridge::from_store(&store).unwrap().token, repaired);
        drop(store);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn two_app_connections_share_one_saved_pairing_identity() {
        let path = std::env::temp_dir().join(format!("opencore-browser-pairing-{}.sqlite3", uuid::Uuid::new_v4()));
        let one = EventStore::open(&path).unwrap();
        let two = EventStore::open(&path).unwrap();
        let barrier = std::sync::Barrier::new(2);
        std::thread::scope(|scope| {
            let first = scope.spawn(|| { barrier.wait(); BrowserBridge::from_store(&one).unwrap().token });
            let second = scope.spawn(|| { barrier.wait(); BrowserBridge::from_store(&two).unwrap().token });
            assert_eq!(first.join().unwrap(), second.join().unwrap());
        });
        drop(one);
        drop(two);
        std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn chrome_bridge_does_not_conflict_with_echo() {
        assert_ne!(PORT, 8813);
    }
    #[tokio::test]
    async fn disconnected_command_fails_immediately() {
        let bridge = BrowserBridge::new();
        assert!(bridge.command("list", json!({})).await.unwrap_err().contains("disconnected"));
    }
    #[tokio::test]
    async fn disabling_cancels_waiters_and_blocks_commands_until_reenabled() {
        let bridge = BrowserBridge::new();
        let (tx, mut rx) = mpsc::unbounded_channel();
        *bridge.sender.lock().unwrap() = Some(tx);
        let command = bridge.command("list", json!({}));
        tokio::pin!(command);
        tokio::select! {
            _ = &mut command => panic!("command should await response"),
            _ = async { rx.recv().await.unwrap(); } => {}
        }
        bridge.set_enabled(false);
        assert!(command.await.is_err());
        assert_eq!(bridge.status()["connected"], false);
        assert_eq!(bridge.status()["enabled"], false);
        assert!(bridge.command("list", json!({})).await.unwrap_err().contains("disabled"));
        bridge.set_enabled(true);
        assert_eq!(bridge.status()["enabled"], true);
        assert_eq!(bridge.status()["connected"], false);
    }
    #[tokio::test]
    async fn dropping_a_cancelled_command_releases_its_waiter() {
        let bridge = BrowserBridge::new();
        let (tx, mut rx) = mpsc::unbounded_channel();
        *bridge.sender.lock().unwrap() = Some(tx);
        let mut command = Box::pin(bridge.command("list", json!({})));
        tokio::select! {
            _ = &mut command => panic!("command should await response"),
            _ = async { rx.recv().await.unwrap(); } => {}
        }
        assert_eq!(bridge.pending.lock().unwrap().len(), 1);
        drop(command);
        assert!(bridge.pending.lock().unwrap().is_empty());
    }
    #[tokio::test]
    async fn disconnect_closes_the_real_socket_and_refuses_reconnection() {
        use tokio::io::AsyncReadExt;
        let bridge = Arc::new(BrowserBridge::new());
        let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let router = Router::new().route("/ws", get(connect)).with_state(bridge.clone());
        let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
        let request = || reqwest::Client::new().get(format!("http://{address}/ws?token={}", bridge.token))
            .header("connection", "Upgrade").header("upgrade", "websocket")
            .header("sec-websocket-version", "13").header("sec-websocket-key", "dGhlIHNhbXBsZSBub25jZQ==");
        let response = request().send().await.unwrap();
        assert_eq!(response.status(), StatusCode::SWITCHING_PROTOCOLS);
        let mut socket = response.upgrade().await.unwrap();
        tokio::time::timeout(std::time::Duration::from_secs(2), async {
            while bridge.status()["connected"] != true { tokio::task::yield_now().await; }
        }).await.unwrap();
        bridge.set_enabled(true);
        assert_eq!(bridge.status()["connected"], true, "idempotent enable must preserve the connection");
        bridge.set_enabled(false);
        let byte = tokio::time::timeout(std::time::Duration::from_secs(2), socket.read_u8()).await.unwrap().unwrap();
        assert_eq!(byte & 0x0f, 8, "server must send a websocket Close frame");
        assert_eq!(request().send().await.unwrap().status(), StatusCode::FORBIDDEN);
        bridge.set_enabled(true);
        assert_eq!(request().send().await.unwrap().status(), StatusCode::SWITCHING_PROTOCOLS);
        server.abort();
    }
    #[test]
    fn element_actions_require_bounded_snapshot_references() {
        for action in ["click_element", "type_element"] {
            assert!(validate_action(action, &json!({"tabId":7,"elementId":2,"snapshotId":"fresh","text":"hello"})).is_ok());
            for args in [json!({"elementId":2}), json!({"elementId":100,"snapshotId":"fresh"}),
                json!({"elementId":-1,"snapshotId":"fresh"}), json!({"elementId":2,"snapshotId":""})] {
                assert!(validate_action(action, &args).is_err());
            }
        }
        assert!(validate_action("type_element", &json!({"elementId":2,"snapshotId":"fresh","text":"x".repeat(4001)})).is_err());
    }
    #[test]
    fn invalid_browser_actions_never_reach_the_extension() {
        assert!(validate_action("execute_script", &json!({"script":"alert(1)"})).is_err());
        assert!(validate_action("evaluate", &json!({"expression":"document.title"})).is_ok());
        assert!(validate_action("open", &json!({"url":"file:///C:/secret"})).is_err());
        assert!(validate_action("click", &json!({"x":-1,"y":4})).is_err());
        assert!(validate_action("type", &json!({"x":2,"y":3,"text":"hello"})).is_ok());
    }
}
