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
use tokio::sync::{mpsc, oneshot};
use uuid::Uuid;

pub(crate) struct CodexToolBridgeCall {
    pub name: String,
    pub arguments: Value,
    pub response: oneshot::Sender<Value>,
}

pub(crate) type CodexToolBridgeMap = Arc<Mutex<HashMap<String, mpsc::Sender<CodexToolBridgeCall>>>>;

#[derive(Clone)]
pub struct GatewayState {
    pub runtime: Arc<RuntimeManager>,
    pub store: Arc<EventStore>,
    pub(crate) app: AppHandle,
    pub(crate) live_generation_runs: Arc<Mutex<HashMap<String, String>>>,
    pub(crate) codex_tool_bridges: CodexToolBridgeMap,
    pub(crate) client: reqwest::Client,
}

impl GatewayState {
    pub fn new(
        runtime: Arc<RuntimeManager>,
        store: Arc<EventStore>,
        app: AppHandle,
        live_generation_runs: Arc<Mutex<HashMap<String, String>>>,
        codex_tool_bridges: CodexToolBridgeMap,
    ) -> Self {
        Self {
            runtime,
            store,
            app,
            live_generation_runs,
            codex_tool_bridges,
            client: reqwest::Client::builder()
                .no_proxy()
                .timeout(std::time::Duration::from_secs(86_400))
                .build()
                .expect("gateway client"),
        }
    }
}

fn json_response(status: StatusCode, value: Value) -> Response<Body> {
    Response::builder().status(status).header("content-type", "application/json")
        .body(Body::from(value.to_string())).unwrap()
}

fn token_matches(expected: &str, received: &str) -> bool {
    if expected.is_empty() || expected.len() != received.len() { return false; }
    expected.bytes().zip(received.bytes()).fold(0u8, |diff, (a, b)| diff | (a ^ b)) == 0
}

async fn codex_tool_bridge(
    state: &GatewayState,
    method: &axum::http::Method,
    headers: &HeaderMap,
    token: &str,
    request: Request,
) -> Response<Body> {
    if method != axum::http::Method::POST {
        return json_response(StatusCode::METHOD_NOT_ALLOWED, json!({"error":"POST required"}));
    }
    if token.len() > 128 || !token.bytes().all(|byte| byte.is_ascii_hexdigit() || byte == b'-') {
        return json_response(StatusCode::NOT_FOUND, json!({"error":"Unknown OpenCore tool route"}));
    }
    let received = headers.get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok()).and_then(|value| value.strip_prefix("Bearer "));
    if !received.is_some_and(|received| token_matches(token, received)) {
        return json_response(StatusCode::UNAUTHORIZED, json!({"error":"Invalid bridge token"}));
    }
    let body = match axum::body::to_bytes(request.into_body(), 1024 * 1024).await {
        Ok(body) => body,
        Err(_) => return json_response(StatusCode::PAYLOAD_TOO_LARGE, json!({"error":"Tool request exceeds 1 MiB"})),
    };
    let payload = match serde_json::from_slice::<Value>(&body) {
        Ok(payload) => payload,
        Err(_) => return json_response(StatusCode::BAD_REQUEST, json!({"error":"Tool request must be JSON"})),
    };
    let Some(name) = payload.get("name").and_then(Value::as_str).filter(|name| {
        !name.trim().is_empty() && name.len() <= 128 && !name.chars().any(char::is_control)
    }) else { return json_response(StatusCode::BAD_REQUEST, json!({"error":"A valid tool name is required"})); };
    let arguments = payload.get("arguments").cloned().unwrap_or_else(|| json!({}));
    if !arguments.is_object() {
        return json_response(StatusCode::BAD_REQUEST, json!({"error":"Tool arguments must be an object"}));
    }
    let sender = match state.codex_tool_bridges.lock() {
        Ok(bridges) => bridges.get(token).cloned(),
        Err(_) => return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({"error":"Tool bridge unavailable"})),
    };
    let Some(sender) = sender else { return json_response(StatusCode::SERVICE_UNAVAILABLE, json!({"error":"No active OpenCore turn owns this tool request"})); };
    let (response, receive) = oneshot::channel();
    let call = CodexToolBridgeCall { name: name.into(), arguments, response };
    if tokio::time::timeout(std::time::Duration::from_secs(5), sender.send(call)).await.is_err() {
        return json_response(StatusCode::SERVICE_UNAVAILABLE, json!({"error":"OpenCore tool dispatcher is busy"}));
    }
    match tokio::time::timeout(std::time::Duration::from_secs(600), receive).await {
        Ok(Ok(value)) => json_response(StatusCode::OK, value),
        Ok(Err(_)) => json_response(StatusCode::SERVICE_UNAVAILABLE, json!({"error":"OpenCore turn ended before the tool completed"})),
        Err(_) => json_response(StatusCode::GATEWAY_TIMEOUT, json!({"error":"OpenCore tool call timed out"})),
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
    if let Some(token) = path.strip_prefix("/opencore/codex-tool/") {
        let headers = request.headers().clone();
        return codex_tool_bridge(&state, &method, &headers, token, request).await;
    }
    if path == "/background/events" {
        if method != axum::http::Method::POST { return json_response(StatusCode::METHOD_NOT_ALLOWED,json!({"error":"POST required"})); }
        use tauri::Manager;
        let Some(core)=state.app.try_state::<Arc<crate::AppCore>>() else { return json_response(StatusCode::SERVICE_UNAVAILABLE,json!({"error":"Background scheduler not ready"})); };
        let bearer=request.headers().get(axum::http::header::AUTHORIZATION).and_then(|v|v.to_str().ok()).and_then(|v|v.strip_prefix("Bearer ")).unwrap_or("");
        if !core.background.authenticate_webhook(bearer) { return json_response(StatusCode::UNAUTHORIZED,json!({"error":"Invalid background event token"})); }
        let body=match axum::body::to_bytes(request.into_body(),64*1024).await { Ok(body)=>body,Err(_)=>return json_response(StatusCode::PAYLOAD_TOO_LARGE,json!({"error":"Event exceeds 64 KiB"})) };
        let event=match serde_json::from_slice::<Value>(&body) { Ok(event)=>event,Err(_)=>return json_response(StatusCode::BAD_REQUEST,json!({"error":"Event must be JSON"})) };
        return match core.background.emit(event) { Ok(value)=>json_response(StatusCode::ACCEPTED,value),Err(error)=>json_response(StatusCode::BAD_REQUEST,json!({"error":error})) };
    }
    let headers = request.headers().clone();
    let bridge = path == "/opencore/claude-bridge";
    let body = match axum::body::to_bytes(request.into_body(), if bridge { 1024 * 1024 } else { 64 * 1024 * 1024 }).await {
        Ok(body) => body,
        Err(error) => {
            return Response::builder()
                .status(StatusCode::BAD_REQUEST)
                .body(Body::from(error.to_string()))
                .unwrap()
        }
    };
    let payload = serde_json::from_slice::<Value>(&body).unwrap_or_else(|_| json!({}));
    if bridge {
        if method != axum::http::Method::POST {
            return Response::builder().status(StatusCode::METHOD_NOT_ALLOWED).body(Body::empty()).unwrap();
        }
        return crate::claude_bridge::handle(&state, &headers, &payload).await;
    }
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
        if matches!(name.as_str(), "host" | "connection" | "content-length" | "authorization" | "cookie" | "x-echo-project-scopes" | "x-opencore-bridge-token") {
            continue;
        }
        builder = builder.header(name, value);
    }
    if is_chat {
        builder = builder.header("x-echo-conversation", &conversation);
        builder = crate::compat::with_echo_project_scope(&state, builder, &conversation);
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

pub fn router(state: GatewayState) -> Router {
    Router::new()
        .route("/", any(proxy))
        .route("/{*path}", any(proxy))
        .layer(DefaultBodyLimit::max(64 * 1024 * 1024))
        .with_state(state)
}
