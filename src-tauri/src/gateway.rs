use crate::redaction::redact_json;
use crate::runtime::RuntimeManager;
use crate::store::EventStore;
use async_stream::stream;
use axum::body::{Body, Bytes};
use axum::extract::{DefaultBodyLimit, Request, State};
use axum::http::{HeaderMap, HeaderName, HeaderValue, Response, StatusCode};
use axum::routing::any;
use axum::Router;
use futures_util::StreamExt;
use serde_json::{json, Value};
use std::convert::Infallible;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use tauri::AppHandle;
use uuid::Uuid;

#[derive(Clone)]
pub struct GatewayState {
    pub runtime: Arc<RuntimeManager>,
    pub store: Arc<EventStore>,
    pub(crate) app: AppHandle,
    pub(crate) live_generation_runs: Arc<Mutex<HashMap<String, String>>>,
    pub(crate) client: reqwest::Client,
}

impl GatewayState {
    pub fn new(
        runtime: Arc<RuntimeManager>,
        store: Arc<EventStore>,
        app: AppHandle,
        live_generation_runs: Arc<Mutex<HashMap<String, String>>>,
    ) -> Self {
        Self {
            runtime,
            store,
            app,
            live_generation_runs,
            client: reqwest::Client::builder()
                .no_proxy()
                .timeout(std::time::Duration::from_secs(86_400))
                .build()
                .expect("gateway client"),
        }
    }
}

fn source_client(headers: &HeaderMap) -> String {
    for key in ["x-opencore-client", "x-client-name", "user-agent"] {
        if let Some(value) = headers.get(key).and_then(|value| value.to_str().ok()) {
            let value = value.trim();
            if !value.is_empty() {
                return value.chars().take(80).collect();
            }
        }
    }
    "API client".into()
}

pub(crate) fn conversation_id(headers: &HeaderMap, payload: &Value) -> String {
    for candidate in [
        headers.get("x-echo-conversation").and_then(|v| v.to_str().ok()),
        payload.get("conversation_id").and_then(Value::as_str),
        payload.get("user").and_then(Value::as_str),
    ] {
        if let Some(value) = candidate {
            if !value.trim().is_empty() {
                return value.chars().take(128).collect();
            }
        }
    }
    Uuid::new_v4().to_string()
}

fn title_from_payload(payload: &Value) -> String {
    payload
        .get("messages")
        .and_then(Value::as_array)
        .and_then(|messages| {
            messages.iter().rev().find_map(|message| {
                if message.get("role").and_then(Value::as_str) == Some("user") {
                    message.get("content").and_then(Value::as_str)
                } else {
                    None
                }
            })
        })
        .unwrap_or("New conversation")
        .chars()
        .take(80)
        .collect()
}

pub(crate) fn capture_request(state: &GatewayState, id: &str, client: &str, payload: &Value) {
    let profile = state.runtime.profile();
    let _ = state
        .store
        .ensure_conversation(id, client, &profile, &title_from_payload(payload));
    if let Some(messages) = payload.get("messages").and_then(Value::as_array) {
        if let Some(message) = messages.last() {
            let role = message.get("role").and_then(Value::as_str).unwrap_or("unknown");
            let content = message
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or_else(|| if message.get("tool_calls").is_some() { "Tool calls" } else { "" });
            let kind = if role == "tool" { "tool_result" } else { "message" };
            let _ = state.store.add_timeline(
                id,
                kind,
                role,
                client,
                &format!("{} · {}", role, client),
                content,
                message,
            );
        }
    }
    state.store.log("info", "gateway", &format!("{client} request in {id}"));
}

pub(crate) fn capture_completion(store: &EventStore, id: &str, client: &str, payload: &Value) {
    if let Some(message) = payload
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|values| values.first())
        .and_then(|choice| choice.get("message"))
    {
        if let Some(reasoning) = message.get("reasoning_content").and_then(Value::as_str) {
            if !reasoning.is_empty() {
                let _ = store.add_timeline(id, "thinking", "assistant", client, "Thinking", reasoning, message);
            }
        }
        if let Some(tool_calls) = message.get("tool_calls").and_then(Value::as_array) {
            for call in tool_calls {
                let title = call
                    .pointer("/function/name")
                    .and_then(Value::as_str)
                    .unwrap_or("Tool call");
                let _ = store.add_timeline(id, "tool_call", "assistant", client, title, &call.to_string(), call);
            }
        }
        let content = message.get("content").and_then(Value::as_str).unwrap_or_default();
        if !content.is_empty() {
            let _ = store.add_timeline(id, "message", "assistant", client, "Assistant", content, message);
        }
    }
    if let Some(echo) = payload.get("echo") {
        let _ = store.add_timeline(id, "echo", "system", "ECHO", "ECHO memory", &echo.to_string(), echo);
    }
    let status = payload
        .pointer("/choices/0/finish_reason")
        .and_then(Value::as_str)
        .unwrap_or("complete");
    store.finish_conversation(id, status);
}

fn capture_sse(store: &EventStore, id: &str, client: &str, body: &str) {
    let mut answer = String::new();
    let mut reasoning = String::new();
    let mut finish = "complete".to_string();
    let mut echo = None;
    for line in body.lines().filter_map(|line| line.strip_prefix("data: ")) {
        if line == "[DONE]" {
            continue;
        }
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if let Some(content) = value.pointer("/choices/0/delta/content").and_then(Value::as_str) {
            answer.push_str(content);
        }
        if let Some(content) = value
            .pointer("/choices/0/delta/reasoning_content")
            .and_then(Value::as_str)
        {
            reasoning.push_str(content);
        }
        if let Some(reason) = value.pointer("/choices/0/finish_reason").and_then(Value::as_str) {
            finish = reason.into();
        }
        if value.get("echo").is_some() {
            echo = value.get("echo").cloned();
        }
    }
    if !reasoning.is_empty() {
        let _ = store.add_timeline(id, "thinking", "assistant", client, "Thinking", &reasoning, &json!({}));
    }
    if !answer.is_empty() {
        let _ = store.add_timeline(id, "message", "assistant", client, "Assistant", &answer, &json!({}));
    }
    if let Some(echo) = echo {
        let _ = store.add_timeline(id, "echo", "system", "ECHO", "ECHO memory", &echo.to_string(), &echo);
    }
    store.finish_conversation(id, &finish);
}

fn copy_response_headers(source: &reqwest::header::HeaderMap, target: &mut Response<Body>) {
    for (name, value) in source {
        if matches!(name.as_str(), "connection" | "content-length" | "transfer-encoding") {
            continue;
        }
        if let (Ok(name), Ok(value)) = (
            HeaderName::from_bytes(name.as_str().as_bytes()),
            HeaderValue::from_bytes(value.as_bytes()),
        ) {
            target.headers_mut().insert(name, value);
        }
    }
}

async fn proxy(State(state): State<GatewayState>, request: Request) -> Response<Body> {
    let method = request.method().clone();
    let path = request
        .uri()
        .path_and_query()
        .map(|value| value.as_str())
        .unwrap_or("/")
        .to_string();
    if path == "/health" {
        return Response::builder()
            .status(StatusCode::OK)
            .header("content-type", "application/json")
            .body(Body::from(r#"{"status":"ok","service":"opencore-control-gateway"}"#))
            .unwrap();
    }
    let headers = request.headers().clone();
    let body = match axum::body::to_bytes(request.into_body(), 64 * 1024 * 1024).await {
        Ok(body) => body,
        Err(error) => {
            return Response::builder()
                .status(StatusCode::BAD_REQUEST)
                .body(Body::from(error.to_string()))
                .unwrap()
        }
    };
    let payload = serde_json::from_slice::<Value>(&body).unwrap_or_else(|_| json!({}));
    if path.starts_with("/v1/messages") {
        return crate::compat::anthropic(&state, &path, &headers, &payload).await;
    }
    if path.starts_with("/v1/responses/compact") {
        return crate::compat::responses_compact(&state, &headers, &payload).await;
    }
    if path.starts_with("/v1/responses") {
        return crate::compat::responses(&state, &headers, &payload).await;
    }
    let client_name = source_client(&headers);
    let conversation = conversation_id(&headers, &payload);
    let is_chat = path.starts_with("/v1/chat/completions");
    if is_chat {
        state.store.observe_client(&client_name);
        capture_request(&state, &conversation, &client_name, &redact_json(&payload));
    }

    let upstream = format!("{}{}", state.runtime.upstream_url(), path);
    let mut builder = state.client.request(method, &upstream).body(body);
    for (name, value) in &headers {
        if matches!(name.as_str(), "host" | "connection" | "content-length" | "authorization" | "cookie") {
            continue;
        }
        builder = builder.header(name, value);
    }
    let response = match builder.send().await {
        Ok(response) => response,
        Err(error) => {
            state.store.log("error", "gateway", &format!("Upstream failed: {error}"));
            if is_chat {
                let _ = state.store.add_timeline(
                    &conversation,
                    "error",
                    "system",
                    "gateway",
                    "Upstream error",
                    &error.to_string(),
                    &json!({}),
                );
                state.store.finish_conversation(&conversation, "error");
            }
            return Response::builder()
                .status(StatusCode::BAD_GATEWAY)
                .header("content-type", "application/json")
                .body(Body::from(json!({"error":{"message":error.to_string()}}).to_string()))
                .unwrap();
        }
    };
    let status = response.status();
    let response_headers = response.headers().clone();
    let streaming = response_headers
        .get("content-type")
        .and_then(|value| value.to_str().ok())
        .map(|value| value.contains("text/event-stream"))
        .unwrap_or(false);

    if streaming && is_chat {
        let store = state.store.clone();
        let runtime = state.runtime.clone();
        let id = conversation.clone();
        let client = client_name.clone();
        let mut upstream_stream = response.bytes_stream();
        let stream = stream! {
            let mut captured = Vec::new();
            while let Some(next) = upstream_stream.next().await {
                match next {
                    Ok(bytes) => {
                        captured.extend_from_slice(&bytes);
                        yield Ok::<Bytes, Infallible>(bytes);
                    }
                    Err(error) => {
                        store.log("error", "gateway", &format!("Stream interrupted: {error}"));
                        break;
                    }
                }
            }
            for line in String::from_utf8_lossy(&captured).lines().filter_map(|line| line.strip_prefix("data: ")) {
                if let Ok(value) = serde_json::from_str::<Value>(line) {
                    runtime.record_response_metrics(&value);
                }
            }
            capture_sse(&store, &id, &client, &String::from_utf8_lossy(&captured));
        };
        let mut output = Response::builder().status(status).body(Body::from_stream(stream)).unwrap();
        copy_response_headers(&response_headers, &mut output);
        return output;
    }

    let bytes = response.bytes().await.unwrap_or_default();
    if is_chat {
        if let Ok(value) = serde_json::from_slice::<Value>(&bytes) {
            state.runtime.record_response_metrics(&value);
            capture_completion(&state.store, &conversation, &client_name, &redact_json(&value));
        } else {
            state.store.finish_conversation(&conversation, "invalid_response");
        }
    }
    let mut output = Response::builder().status(status).body(Body::from(bytes)).unwrap();
    copy_response_headers(&response_headers, &mut output);
    output
}

pub async fn serve(state: GatewayState, port: u16) -> Result<(), String> {
    let app = Router::new()
        .route("/", any(proxy))
        .route("/{*path}", any(proxy))
        .layer(DefaultBodyLimit::max(64 * 1024 * 1024))
        .with_state(state.clone());
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port))
        .await
        .map_err(|e| format!("Could not bind Control Gateway on {port}: {e}"))?;
    state.store.log("info", "gateway", &format!("Control Gateway listening on 127.0.0.1:{port}"));
    axum::serve(listener, app).await.map_err(|e| e.to_string())
}
