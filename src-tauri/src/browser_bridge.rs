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
use tokio::sync::{mpsc, oneshot};

pub(crate) const PORT: u16 = 8814;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;

pub(crate) struct BrowserBridge {
    token: String,
    sender: Mutex<Option<mpsc::UnboundedSender<Value>>>,
    pending: Mutex<HashMap<String, oneshot::Sender<Value>>>,
}

impl BrowserBridge {
    pub(crate) fn new() -> Self {
        Self { token: uuid::Uuid::new_v4().to_string(), sender: Mutex::new(None), pending: Mutex::new(HashMap::new()) }
    }

    pub(crate) fn status(&self) -> Value {
        json!({"port": PORT, "token": self.token,
            "connected": self.sender.lock().map(|sender| sender.as_ref().is_some_and(|tx| !tx.is_closed())).unwrap_or(false)})
    }

    pub(crate) async fn command(&self, action: &str, args: Value) -> Result<Value, String> {
        validate_action(action, &args)?;
        let sender = self.sender.lock().map_err(|e| e.to_string())?.clone()
            .ok_or("Chrome extension is disconnected. Connect it from the Browser panel.")?;
        let id = uuid::Uuid::new_v4().to_string();
        let (tx, rx) = oneshot::channel();
        self.pending.lock().map_err(|e| e.to_string())?.insert(id.clone(), tx);
        if sender.send(json!({"id":id,"action":action,"args":args})).is_err() {
            self.pending.lock().map_err(|e| e.to_string())?.remove(&id);
            return Err("Chrome extension disconnected".into());
        }
        let response = tokio::time::timeout(std::time::Duration::from_secs(25), rx).await;
        self.pending.lock().map_err(|e| e.to_string())?.remove(&id);
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
    if !matches!(action, "list" | "open" | "navigate" | "activate" | "close" | "inspect" | "screenshot" | "click" | "type" | "scroll" | "key" | "back" | "forward" | "reload" | "evaluate") {
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
    if action == "type" && args.get("text").and_then(Value::as_str).is_none_or(|text| text.len() > 4000) {
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
    upgrade.on_upgrade(move |socket| socket_loop(bridge, socket)).into_response()
}

async fn socket_loop(bridge: Arc<BrowserBridge>, socket: WebSocket) {
    let (mut writer, mut reader) = socket.split();
    let (tx, mut rx) = mpsc::unbounded_channel::<Value>();
    if let Ok(mut current) = bridge.sender.lock() { *current = Some(tx.clone()); }
    let send_task = tokio::spawn(async move {
        while let Some(command) = rx.recv().await {
            if writer.send(Message::Text(command.to_string().into())).await.is_err() { break; }
        }
    });
    while let Some(Ok(message)) = reader.next().await {
        let Message::Text(text) = message else { continue };
        if text.len() > MAX_RESPONSE_BYTES { break; }
        let Ok(value) = serde_json::from_str::<Value>(&text) else { continue };
        let Some(id) = value.get("id").and_then(Value::as_str) else { continue };
        if let Ok(mut pending) = bridge.pending.lock() {
            if let Some(waiter) = pending.remove(id) { let _ = waiter.send(value); }
        }
    }
    let disconnected_current = if let Ok(mut current) = bridge.sender.lock() {
        if current.as_ref().is_some_and(|active| active.same_channel(&tx)) { *current = None; true } else { false }
    } else { false };
    if disconnected_current {
        if let Ok(mut pending) = bridge.pending.lock() { pending.clear(); }
    }
    send_task.abort();
}

pub(crate) async fn serve(bridge: Arc<BrowserBridge>) -> Result<(), String> {
    let router = Router::new().route("/ws", get(connect)).with_state(bridge);
    let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, PORT)).await.map_err(|e| e.to_string())?;
    axum::serve(listener, router).await.map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn chrome_bridge_does_not_conflict_with_echo() {
        assert_ne!(PORT, 8813);
    }
    #[tokio::test]
    async fn disconnected_command_fails_immediately() {
        let bridge = BrowserBridge::new();
        assert!(bridge.command("list", json!({})).await.unwrap_err().contains("disconnected"));
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
