use crate::gateway::{capture_completion, capture_request, conversation_id, GatewayState};
use crate::redaction::redact_json;
use axum::body::Body;
use axum::http::{HeaderMap, HeaderValue, Response, StatusCode};
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use tauri::Emitter;
use uuid::Uuid;

fn json_response(status: StatusCode, value: &Value) -> Response<Body> {
    Response::builder()
        .status(status)
        .header("content-type", "application/json")
        .body(Body::from(value.to_string()))
        .unwrap()
}

fn sse_response(body: String) -> Response<Body> {
    Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "text/event-stream")
        .header("cache-control", "no-cache")
        .header("connection", "close")
        .body(Body::from(body))
        .unwrap()
}

fn provisional_generation_event(conversation: &str, run_id: &str, preview: &Value) -> Option<Value> {
    if preview.get("provisional").and_then(Value::as_bool) != Some(true) { return None; }
    Some(json!({
        "conversationId":conversation,
        "runId":run_id,
        "content":preview["content"],
        "reasoning":preview["reasoning"],
        "phase":preview["phase"],
    }))
}

#[derive(Default)]
struct AnthropicStreamBlocks {
    sent_text: String,
    text_open: bool,
    reasoning_sent: String,
    thinking_open: bool,
}

impl AnthropicStreamBlocks {
    fn push(&mut self, preview: &Value) -> Vec<Value> {
        // Anthropic deltas cannot replace an earlier draft when another brain
        // wins selection. The app receives these via its separate preview event.
        if preview["provisional"] == true { return Vec::new(); }
        let mut events = Vec::new();
        if let Some(current) = preview["reasoning"].as_str() {
            if !self.text_open && current.len() > self.reasoning_sent.len() && current.starts_with(&self.reasoning_sent) {
                if !self.thinking_open {
                    events.push(json!({"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"","signature":""}}));
                    self.thinking_open = true;
                }
                events.push(json!({"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":&current[self.reasoning_sent.len()..]}}));
                self.reasoning_sent = current.to_string();
            }
        }
        if let Some(current) = preview["content"].as_str() {
            if current.len() > self.sent_text.len() && current.starts_with(&self.sent_text) {
                if !self.text_open {
                    if self.thinking_open { events.push(json!({"type":"content_block_stop","index":0})); }
                    events.push(json!({"type":"content_block_start","index":usize::from(self.thinking_open),"content_block":{"type":"text","text":""}}));
                    self.text_open = true;
                }
                events.push(json!({"type":"content_block_delta","index":usize::from(self.thinking_open),"delta":{"type":"text_delta","text":&current[self.sent_text.len()..]}}));
                self.sent_text = current.to_string();
            }
        }
        events
    }
}

fn flatten_text(content: &Value) -> String {
    if let Some(text) = content.as_str() {
        return text.to_string();
    }
    content.as_array().map(|parts| {
        parts.iter().filter_map(|part| {
            let typ = part.get("type").and_then(Value::as_str).unwrap_or("");
            if matches!(typ, "text" | "input_text" | "output_text") {
                part.get("text").and_then(Value::as_str).map(str::to_string)
            } else {
                None
            }
        }).collect::<Vec<_>>().join("\n")
    }).unwrap_or_default()
}

fn image_part(part: &Value) -> Option<Value> {
    if part["type"] != "image" { return None; }
    let source = &part["source"];
    let url = if source["type"] == "base64" {
        format!("data:{};base64,{}", source["media_type"].as_str()?, source["data"].as_str()?)
    } else { source["url"].as_str()?.to_string() };
    Some(json!({"type":"image_url","image_url":{"url":url}}))
}

fn tool_content(content: &Value) -> Value {
    let Some(parts) = content.as_array() else { return json!(flatten_text(content)); };
    if !parts.iter().any(|p| p["type"] == "image") { return json!(flatten_text(content)); }
    json!(parts.iter().filter_map(|p| image_part(p).or_else(||
        (p["type"] == "text").then(|| p.clone()))).collect::<Vec<_>>())
}

fn usage_from_openai(value: &Value) -> (u64, u64) {
    (
        value.pointer("/usage/prompt_tokens").and_then(Value::as_u64).unwrap_or(0),
        value.pointer("/usage/completion_tokens").and_then(Value::as_u64).unwrap_or(0),
    )
}

fn apply_client_reasoning(payload: &Value, chat: &mut Value, anthropic: bool) {
    let explicit = if anthropic {
        let thinking = payload.get("thinking");
        match thinking.and_then(|value| value.get("type")).and_then(Value::as_str) {
            Some("disabled") => Some("off"),
            Some("enabled") => {
                let budget = thinking.and_then(|value| value.get("budget_tokens"))
                    .and_then(Value::as_u64).unwrap_or(1500);
                Some(if budget >= 12000 { "max" } else if budget >= 5000 { "extra-high" }
                    else if budget >= 2500 { "high" } else if budget >= 1000 { "medium" } else if budget == 0 { "off" } else { "low" })
            }
            _ => None,
        }
    } else {
        payload.pointer("/reasoning/effort").and_then(Value::as_str)
            .or_else(|| payload.get("reasoning_effort").and_then(Value::as_str))
    };
    let requested_model = payload.get("model").and_then(Value::as_str).unwrap_or("");
    let suffix = requested_model.strip_prefix("opencore:");
    let raw = explicit.or(suffix).unwrap_or("").to_ascii_lowercase();
    let effort = match raw.as_str() {
        "none" | "off" | "fast" => "off",
        "minimal" | "low" => "low",
        "medium" => "medium",
        "high" => "high",
        "xhigh" | "extra-high" | "extra_high" => "extra-high",
        "max" => "max",
        "ultra" | "opencore" => "opencore",
        _ => return,
    };
    chat["reasoning_effort"] = Value::String(effort.into());
    chat["reasoning_budget_tokens"] = json!(match effort {
        "off" => 0,
        "low" => 512,
        "medium" => 1500,
        "high" => 3000,
        "extra-high" | "opencore" => 6000,
        "max" => 12000,
        _ => 1500,
    });
}

fn codex_agent_owns_timeline(headers: &HeaderMap) -> bool {
    matches!(headers.get("x-opencore-harness").and_then(|v| v.to_str().ok()), Some("codex-sdk" | "codex-app-server"))
        && headers.get("x-opencore-timeline-owner").and_then(|v| v.to_str().ok()) == Some("app")
}

async fn call_chat(state: &GatewayState, payload: &Value) -> Result<Value, Response<Body>> {
    call_chat_with_conversation(state, payload, None, false).await
}

async fn call_chat_with_conversation(state: &GatewayState, payload: &Value,
                                     conversation: Option<&str>, app_owns_timeline: bool) -> Result<Value, Response<Body>> {
    let url = format!("{}/v1/chat/completions", state.runtime.upstream_url());
    let mut request = state.client.post(url);
    if let Some(conversation) = conversation {
        request = with_echo_conversation(request, conversation, app_owns_timeline);
        request = with_echo_project_scope(state, request, conversation);
    }
    let response = request.json(payload).send().await.map_err(|error| {
        json_response(StatusCode::BAD_GATEWAY, &json!({"error":{"message":error.to_string()}}))
    })?;
    let status = response.status();
    let bytes = response.bytes().await.unwrap_or_default();
    let value = serde_json::from_slice::<Value>(&bytes).unwrap_or_else(|_| {
        json!({"error":{"message":String::from_utf8_lossy(&bytes).to_string()}})
    });
    if !status.is_success() {
        return Err(json_response(
            StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::BAD_GATEWAY),
            &value,
        ));
    }
    state.runtime.record_response_metrics(&value);
    Ok(value)
}

pub(crate) fn anthropic_stream_upstream(runtime: &crate::runtime::RuntimeManager) -> String {
    // Even the embedded Agent SDK must use the selected public route: for ECHO
    // profiles that is the memory proxy, not the model's direct backend URL.
    runtime.upstream_url()
}

fn echo_conversation_header(conversation: &str) -> Option<HeaderValue> {
    HeaderValue::from_str(conversation).ok()
}

fn with_echo_conversation(request: reqwest::RequestBuilder, conversation: &str,
                          app_owns_timeline: bool) -> reqwest::RequestBuilder {
    let mut request = match echo_conversation_header(conversation) {
        Some(value) => request.header("x-echo-conversation", value),
        None => request,
    };
    if app_owns_timeline {
        request = request.header("x-opencore-timeline-owner", "app");
    }
    request
}

pub(crate) fn with_echo_project_scope(state: &GatewayState, request: reqwest::RequestBuilder,
                                     conversation: &str) -> reqwest::RequestBuilder {
    // Authorization comes from the app's project assignments, never a client's
    // arbitrary scope header or text. Cap the active lookup fanout.
    let mut scopes = state.store.echo_conversation_scope(conversation)
        .unwrap_or_else(|_| vec![conversation.to_string()]);
    scopes.retain(|id| id != conversation);
    scopes.insert(0, conversation.to_string());
    scopes.truncate(128);
    request.header("x-echo-project-scopes", serde_json::to_string(&scopes).unwrap_or_else(|_| "[]".into()))
}
fn anthropic_messages(payload: &Value) -> Vec<Value> {
    let mut out = Vec::new();
    if let Some(system) = payload.get("system") {
        let text = flatten_text(system);
        if !text.is_empty() {
            out.push(json!({"role":"system","content":text}));
        }
    }
    for message in payload.get("messages").and_then(Value::as_array).into_iter().flatten() {
        let role = message.get("role").and_then(Value::as_str).unwrap_or("user");
        let content = message.get("content").unwrap_or(&Value::Null);
        // Recent SDK versions send environment and budget updates as in-history
        // system messages. Qwen's template permits system only at position zero.
        // Preserve their chronological position (and the reusable prompt prefix).
        if matches!(role, "system" | "developer") {
            out.push(json!({"role":"user","opencore_harness_context":true,
                "content":format!("<harness_context>\n{}\n</harness_context>", flatten_text(content))}));
            continue;
        }
        if let Some(text) = content.as_str() {
            out.push(json!({"role":role,"content":text}));
            continue;
        }
        let Some(parts) = content.as_array() else { continue };
        if role == "assistant" {
            let mut text = Vec::new();
            let mut calls = Vec::new();
            for part in parts {
                match part.get("type").and_then(Value::as_str).unwrap_or("") {
                    "text" => if let Some(value) = part.get("text").and_then(Value::as_str) {
                        text.push(value.to_string());
                    },
                    "tool_use" => {
                        let id = part.get("id").and_then(Value::as_str)
                            .map(str::to_string).unwrap_or_else(|| format!("call_{}", Uuid::new_v4().simple()));
                        let name = part.get("name").and_then(Value::as_str).unwrap_or("tool");
                        let args = part.get("input").cloned().unwrap_or_else(|| json!({}));
                        calls.push(json!({
                            "id": id,
                            "type":"function",
                            "function":{"name":name,"arguments":args.to_string()}
                        }));
                    }
                    _ => {}
                }
            }
            let mut msg = Map::new();
            msg.insert("role".into(), Value::String("assistant".into()));
            msg.insert("content".into(), Value::String(text.join("\n")));
            if !calls.is_empty() { msg.insert("tool_calls".into(), Value::Array(calls)); }
            out.push(Value::Object(msg));
        } else {
            let mut text = Vec::new();
            let mut images = Vec::new();
            for part in parts {
                match part.get("type").and_then(Value::as_str).unwrap_or("") {
                    "text" => if let Some(value) = part.get("text").and_then(Value::as_str) {
                        text.push(value.to_string());
                    },
                    "tool_result" => {
                        let id = part.get("tool_use_id").and_then(Value::as_str).unwrap_or("tool");
                        let result = tool_content(part.get("content").unwrap_or(&Value::Null));
                        out.push(json!({"role":"tool","tool_call_id":id,"content":result}));
                    }
                    "image" => if let Some(image) = image_part(part) { images.push(image); },
                    _ => {}
                }
            }
            if !images.is_empty() {
                let mut content = vec![json!({"type":"text","text":text.join("\n")})];
                content.extend(images);
                out.push(json!({"role":"user","content":content}));
            } else if !text.is_empty() {
                out.push(json!({"role":"user","content":text.join("\n")}));
            }
        }
    }
    out
}

fn anthropic_tools(payload: &Value) -> Vec<Value> {
    payload.get("tools").and_then(Value::as_array).map(|tools| {
        tools.iter().filter_map(|tool| {
            let name = tool.get("name")?.as_str()?;
            Some(json!({
                "type":"function",
                "function":{
                    "name":name,
                    "description":tool.get("description").and_then(Value::as_str).unwrap_or(""),
                    "parameters":tool.get("input_schema").cloned().unwrap_or_else(|| json!({"type":"object"}))
                }
            }))
        }).collect()
    }).unwrap_or_default()
}

fn anthropic_output(openai: &Value, requested_model: &str) -> Value {
    let message = openai.pointer("/choices/0/message").cloned().unwrap_or_else(|| json!({}));
    let mut content = Vec::new();
    if let Some(text) = message.get("content").and_then(Value::as_str) {
        if !text.is_empty() { content.push(json!({"type":"text","text":text})); }
    }
    if let Some(calls) = message.get("tool_calls").and_then(Value::as_array) {
        for call in calls {
            let id = call.get("id").and_then(Value::as_str)
                .map(str::to_string).unwrap_or_else(|| format!("toolu_{}", Uuid::new_v4().simple()));
            let name = call.pointer("/function/name").and_then(Value::as_str).unwrap_or("tool");
            let raw = call.pointer("/function/arguments").and_then(Value::as_str).unwrap_or("{}");
            let input = serde_json::from_str::<Value>(raw).unwrap_or_else(|_| json!({"raw":raw}));
            content.push(json!({"type":"tool_use","id":id,"name":name,"input":input}));
        }
    }
    let (input_tokens, output_tokens) = usage_from_openai(openai);
    let stop_reason = if content.iter().any(|v| v.get("type").and_then(Value::as_str) == Some("tool_use")) {
        "tool_use"
    } else {
        "end_turn"
    };
    json!({
        "id": format!("msg_{}", Uuid::new_v4().simple()),
        "type":"message",
        "role":"assistant",
        "model": requested_model,
        "content":content,
        "stop_reason":stop_reason,
        "stop_sequence":Value::Null,
        "usage":{"input_tokens":input_tokens,"output_tokens":output_tokens}
    })
}
fn anthropic_sse(message: &Value) -> String {
    let id = message.get("id").and_then(Value::as_str).unwrap_or("msg_opencore");
    let model = message.get("model").and_then(Value::as_str).unwrap_or("opencore");
    let input_tokens = message.pointer("/usage/input_tokens").and_then(Value::as_u64).unwrap_or(0);
    let output_tokens = message.pointer("/usage/output_tokens").and_then(Value::as_u64).unwrap_or(0);
    let mut out = String::new();
    let start = json!({"type":"message_start","message":{
        "id":id,"type":"message","role":"assistant","model":model,"content":[],
        "stop_reason":Value::Null,"stop_sequence":Value::Null,
        "usage":{"input_tokens":input_tokens,"output_tokens":0}
    }});
    out.push_str(&format!("event: message_start\ndata: {}\n\n", start));
    for (index, block) in message.get("content").and_then(Value::as_array).into_iter().flatten().enumerate() {
        let typ = block.get("type").and_then(Value::as_str).unwrap_or("text");
        if typ == "tool_use" {
            let start = json!({"type":"content_block_start","index":index,"content_block":{
                "type":"tool_use",
                "id":block.get("id").cloned().unwrap_or(Value::String(format!("toolu_{index}"))),
                "name":block.get("name").cloned().unwrap_or(Value::String("tool".into())),
                "input":{}
            }});
            out.push_str(&format!("event: content_block_start\ndata: {}\n\n", start));
            let partial = block.get("input").cloned().unwrap_or_else(|| json!({})).to_string();
            let delta = json!({"type":"content_block_delta","index":index,"delta":{
                "type":"input_json_delta","partial_json":partial
            }});
            out.push_str(&format!("event: content_block_delta\ndata: {}\n\n", delta));
        } else {
            let start = json!({"type":"content_block_start","index":index,"content_block":{"type":"text","text":""}});
            out.push_str(&format!("event: content_block_start\ndata: {}\n\n", start));
            let text = block.get("text").and_then(Value::as_str).unwrap_or("");
            let delta = json!({"type":"content_block_delta","index":index,"delta":{"type":"text_delta","text":text}});
            out.push_str(&format!("event: content_block_delta\ndata: {}\n\n", delta));
        }
        let stop = json!({"type":"content_block_stop","index":index});
        out.push_str(&format!("event: content_block_stop\ndata: {}\n\n", stop));
    }
    let delta = json!({"type":"message_delta","delta":{
        "stop_reason":message.get("stop_reason").cloned().unwrap_or(Value::String("end_turn".into())),
        "stop_sequence":Value::Null
    },"usage":{"output_tokens":output_tokens}});
    out.push_str(&format!("event: message_delta\ndata: {}\n\n", delta));
    out.push_str("event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n");
    out
}

pub async fn anthropic(
    state: &GatewayState,
    path: &str,
    headers: &HeaderMap,
    payload: &Value,
) -> Response<Body> {
    if path.starts_with("/v1/messages/count_tokens") {
        let mut chars = flatten_text(payload.get("system").unwrap_or(&Value::Null)).len();
        chars += payload.get("messages").and_then(Value::as_array).into_iter().flatten()
            .map(|m| flatten_text(m.get("content").unwrap_or(&Value::Null)).len()).sum::<usize>();
        return json_response(StatusCode::OK, &json!({"input_tokens":(chars / 4).max(1)}));
    }
    let requested_model = payload.get("model").and_then(Value::as_str).unwrap_or("opencore");
    let embedded = headers.get("x-opencore-harness").and_then(|v| v.to_str().ok()) == Some("claude-agent-sdk");
    let model_output_limit = state.runtime.snapshot().context_size.saturating_sub(1_024).max(1_024);
    let requested_output = payload.get("max_tokens").and_then(Value::as_u64).unwrap_or(model_output_limit);
    let mut chat = json!({
        "model":"opencore",
        "messages":anthropic_messages(payload),
        "stream":false,
        "max_tokens":requested_output.min(model_output_limit)
    });
    apply_client_reasoning(payload, &mut chat, true);
    if embedded {
        let effort = chat["reasoning_effort"].as_str().unwrap_or("medium");
        let budget = match effort { "off" => 0, "low" => 512, "medium" => 1500, "high" => 3000, "max" => 12000, _ => 6000 };
        chat["reasoning_budget_tokens"] = json!(budget);
        chat["chat_template_kwargs"] = json!({"enable_thinking":budget > 0});
        chat["max_tokens"] = json!(requested_output.min(model_output_limit));
    }
    let tools = anthropic_tools(payload);
    if !tools.is_empty() { chat["tools"] = Value::Array(tools); }
    let client = "Claude Code";
    let conversation = conversation_id(headers, &chat);
    if !embedded { capture_request(state, &conversation, client, &redact_json(&chat)); }
    if payload["stream"] == true {
        return anthropic_live(state.clone(), chat, requested_model.to_string(), conversation, embedded).await;
    }
    let openai = match call_chat_with_conversation(state, &chat, Some(&conversation), embedded).await {
        Ok(value) => value,
        Err(response) => return response,
    };
    capture_completion(&state.store, &conversation, client, &redact_json(&openai));
    let output = anthropic_output(&openai, requested_model);
    if payload.get("stream").and_then(Value::as_bool).unwrap_or(false) {
        sse_response(anthropic_sse(&output))
    } else {
        json_response(StatusCode::OK, &output)
    }
}

// Emit genuine partial content as it arrives. Tool arguments are only committed
// after the complete, validated backend message; truncated calls never execute.
async fn anthropic_live(state: GatewayState, mut chat: Value, model: String, conversation: String, embedded: bool) -> Response<Body> {
    let upstream = anthropic_stream_upstream(&state.runtime);
    chat["stream"] = json!(true);
    chat["stream_options"] = json!({"include_usage":true});
    let request = with_echo_project_scope(&state, with_echo_conversation(state.client.post(format!("{upstream}/v1/chat/completions")), &conversation, embedded), &conversation).json(&chat);
    let response = match request.send().await {
        Ok(r) if r.status().is_success() => r,
        Ok(r) => { let status = StatusCode::from_u16(r.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY); return json_response(status,&json!({"error":{"type":"api_error","message":r.text().await.unwrap_or_default()}})); },
        Err(e) => return json_response(StatusCode::BAD_GATEWAY,&json!({"error":{"type":"api_error","message":e.to_string()}})),
    };
    let live_run = state.live_generation_runs.lock().ok()
        .and_then(|runs| runs.get(&conversation).cloned());
    let preview_app = state.app.clone();
    let preview_conversation = conversation.clone();
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<String>();
    let send = move |value: Value| { let _ = tx.send(format!("event: {}\ndata: {}\n\n", value["type"].as_str().unwrap_or("error"), value)); };
    let task = tokio::spawn(async move {
        send(json!({"type":"message_start","message":{"id":format!("msg_{}",Uuid::new_v4().simple()),"type":"message","role":"assistant","model":model,"content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":0}}}));
        let mut blocks = AnthropicStreamBlocks::default();
        let result = crate::chat_stream::read(response, |preview| {
            if preview["provisional"] == true {
                if let Some(run_id) = live_run.as_ref() {
                    if let Some(event) = provisional_generation_event(&preview_conversation, run_id, &preview) {
                        let _ = preview_app.emit("opencore-generation", event);
                        return;
                    }
                }
            }
            for event in blocks.push(&preview) { send(event); }
        }).await;
        match result {
            Ok(value) => {
                state.runtime.record_response_metrics(&value);
                if !embedded { capture_completion(&state.store, &conversation, "Claude Code", &redact_json(&value)); }
                let output = anthropic_output(&value, &model);
                if blocks.text_open { send(json!({"type":"content_block_stop","index":usize::from(blocks.thinking_open)})); }
                else if blocks.thinking_open { send(json!({"type":"content_block_stop","index":0})); }
                let mut index = usize::from(blocks.text_open) + usize::from(blocks.thinking_open);
                for block in output["content"].as_array().into_iter().flatten() {
                    if block["type"] == "text" && blocks.text_open { continue; }
                    if block["type"] == "tool_use" {
                        send(json!({"type":"content_block_start","index":index,"content_block":{"type":"tool_use","id":block["id"],"name":block["name"],"input":{}}}));
                        send(json!({"type":"content_block_delta","index":index,"delta":{"type":"input_json_delta","partial_json":block["input"].to_string()}}));
                    } else { send(json!({"type":"content_block_start","index":index,"content_block":block})); }
                    send(json!({"type":"content_block_stop","index":index})); index += 1;
                }
                let stop = if value.pointer("/choices/0/finish_reason").and_then(Value::as_str) == Some("length") { json!("max_tokens") } else { output["stop_reason"].clone() };
                send(json!({"type":"message_delta","delta":{"stop_reason":stop,"stop_sequence":null},"usage":output["usage"]}));
                send(json!({"type":"message_stop"}));
                if embedded {
                    let prior = state.store.get_setting(&format!("claude_context:{conversation}")).ok().flatten().and_then(|s| serde_json::from_str::<Value>(&s).ok()).unwrap_or(json!({}));
                    let window_tokens = state.runtime.snapshot().context_size;
                    let _ = state.store.set_setting(&format!("claude_context:{conversation}"), &json!({"available":true,"liveTokens":output["usage"]["input_tokens"],"promptTokens":output["usage"]["input_tokens"],"windowTokens":window_tokens,"compactions":prior["compactions"].as_u64().unwrap_or(0),"harness":{"name":"claude-agent-sdk","status":"working","tasks":[],"unverified":[]}}).to_string());
                }
            },
            Err(error) => send(json!({"type":"error","error":{"type":"api_error","message":error}})),
        }
    });
    struct Abort(tokio::task::JoinHandle<()>);
    impl Drop for Abort { fn drop(&mut self) { self.0.abort(); } }
    let body = async_stream::stream! { let _guard = Abort(task); while let Some(chunk) = rx.recv().await { yield Ok::<_,std::convert::Infallible>(chunk); } };
    Response::builder().status(StatusCode::OK).header("content-type","text/event-stream").header("cache-control","no-cache").body(Body::from_stream(body)).unwrap()
}
fn responses_messages(payload: &Value) -> Vec<Value> {
    let mut out = Vec::new();
    if let Some(instructions) = payload.get("instructions").and_then(Value::as_str) {
        if !instructions.is_empty() { out.push(json!({"role":"system","content":instructions})); }
    }
    if let Some(text) = payload.get("input").and_then(Value::as_str) {
        out.push(json!({"role":"user","content":text}));
        return out;
    }
    for item in payload.get("input").and_then(Value::as_array).into_iter().flatten() {
        match item.get("type").and_then(Value::as_str).unwrap_or("message") {
            "message" => {
                let role = item.get("role").and_then(Value::as_str).unwrap_or("user");
                let text = flatten_text(item.get("content").unwrap_or(&Value::Null));
                if !text.is_empty() { out.push(json!({"role":role,"content":text})); }
            }
            "function_call_output" | "custom_tool_call_output" => {
                let id = item.get("call_id").and_then(Value::as_str).unwrap_or("tool");
                let result = item.get("output").map(flatten_text).unwrap_or_default();
                out.push(json!({"role":"tool","tool_call_id":id,"content":result}));
            }
            "function_call" | "custom_tool_call" => {
                let call_id = item.get("call_id").and_then(Value::as_str).unwrap_or("call");
                let local_name = item.get("name").and_then(Value::as_str).unwrap_or("tool");
                let name = item.get("namespace").and_then(Value::as_str)
                    .map(|namespace| format!("{namespace}__{local_name}"))
                    .unwrap_or_else(|| local_name.to_string());
                let arguments = if item.get("type").and_then(Value::as_str) == Some("custom_tool_call") {
                    json!({"input":item.get("input").and_then(Value::as_str).unwrap_or("")}).to_string()
                } else {
                    item.get("arguments").and_then(Value::as_str).unwrap_or("{}").to_string()
                };
                out.push(json!({"role":"assistant","content":"","tool_calls":[{
                    "id":call_id,"type":"function","function":{"name":name,"arguments":arguments}
                }]}));
            }
            "compaction" => {
                if let Some(encoded) = item.get("encrypted_content").and_then(Value::as_str) {
                    if let Some(summary) = encoded.strip_prefix("opencore:") {
                        out.push(json!({"role":"system","content":format!("Compacted prior conversation state:\n{summary}")}));
                    }
                }
            }
            _ => {}
        }
    }
    out
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct ResponseToolRoute {
    kind: String,
    namespace: Option<String>,
    name: String,
}

fn responses_tools(payload: &Value) -> (Vec<Value>, HashMap<String, ResponseToolRoute>) {
    let mut out = Vec::new();
    let mut kinds = HashMap::new();
    fn add_tool(
        tool: &Value,
        flat_prefix: &str,
        namespace_path: Option<&str>,
        out: &mut Vec<Value>,
        kinds: &mut HashMap<String, ResponseToolRoute>,
    ) {
        let typ = tool.get("type").and_then(Value::as_str).unwrap_or("");
        match typ {
            "namespace" => {
                let namespace = tool.get("name").and_then(Value::as_str).unwrap_or("");
                if namespace.is_empty() { return; }
                let nested_prefix = format!("{flat_prefix}{namespace}__");
                let nested_namespace = namespace_path
                    .map(|parent| format!("{parent}__{namespace}"))
                    .unwrap_or_else(|| namespace.to_string());
                for nested in tool.get("tools").and_then(Value::as_array).into_iter().flatten() {
                    add_tool(nested, &nested_prefix, Some(&nested_namespace), out, kinds);
                }
            }
            "function" | "custom" => {
                let local_name = tool.get("name").and_then(Value::as_str).unwrap_or("tool");
                let name = format!("{flat_prefix}{local_name}");
                let kind = if typ == "custom" { "custom" } else { "function" };
                kinds.insert(name.clone(), ResponseToolRoute {
                    kind: kind.into(),
                    namespace: namespace_path.map(str::to_string),
                    name: local_name.into(),
                });
                let parameters = if kind == "custom" {
                    json!({"type":"object","properties":{"input":{"type":"string","description":"Raw tool input"}},"required":["input"]})
                } else {
                    tool.get("parameters").cloned().unwrap_or_else(|| json!({"type":"object"}))
                };
                out.push(json!({"type":"function","function":{
                    "name":name,
                    "description":tool.get("description").and_then(Value::as_str).unwrap_or(""),
                    "parameters":parameters
                }}));
            }
            _ => {}
        }
    }
    for tool in payload.get("tools").and_then(Value::as_array).into_iter().flatten() {
        add_tool(tool, "", None, &mut out, &mut kinds);
    }
    (out, kinds)
}

fn responses_output(openai: &Value, model: &str, tool_routes: &HashMap<String, ResponseToolRoute>) -> Value {
    let message = openai.pointer("/choices/0/message").cloned().unwrap_or_else(|| json!({}));
    let mut output = Vec::new();
    if let Some(reasoning) = message.get("reasoning_content").and_then(Value::as_str) {
        if !reasoning.is_empty() {
            output.push(json!({
                "id":format!("rs_{}",Uuid::new_v4().simple()),
                "type":"reasoning","status":"completed",
                "summary":[],
                "content":[{"type":"reasoning_text","text":reasoning}]
            }));
        }
    }
    if let Some(text) = message.get("content").and_then(Value::as_str) {
        if !text.is_empty() {
            output.push(json!({
                "id":format!("msg_{}",Uuid::new_v4().simple()),
                "type":"message","status":"completed","role":"assistant",
                "content":[{"type":"output_text","text":text,"annotations":[]}]
            }));
        }
    }
    if let Some(calls) = message.get("tool_calls").and_then(Value::as_array) {
        for call in calls {
            let name = call.pointer("/function/name").and_then(Value::as_str).unwrap_or("tool");
            let arguments = call.pointer("/function/arguments").and_then(Value::as_str).unwrap_or("{}");
            let call_id = call.get("id").cloned().unwrap_or(Value::String(format!("call_{}",Uuid::new_v4().simple())));
            let route = tool_routes.get(name);
            let response_name = route.map(|route| route.name.as_str()).unwrap_or(name);
            let namespace = route.and_then(|route| route.namespace.as_deref());
            if route.is_some_and(|route| route.kind == "custom") {
                let input = serde_json::from_str::<Value>(arguments).ok()
                    .and_then(|v| v.get("input").and_then(Value::as_str).map(str::to_string))
                    .unwrap_or_else(|| arguments.to_string());
                let mut item = json!({
                    "id":format!("ctc_{}",Uuid::new_v4().simple()),
                    "type":"custom_tool_call","status":"completed",
                    "call_id":call_id,
                    "name":response_name,
                    "input":input
                });
                if let Some(namespace) = namespace { item["namespace"] = json!(namespace); }
                output.push(item);
            } else {
                let mut item = json!({
                    "id":format!("fc_{}",Uuid::new_v4().simple()),
                    "type":"function_call","status":"completed",
                    "call_id":call_id,
                    "name":response_name,
                    "arguments":arguments
                });
                if let Some(namespace) = namespace { item["namespace"] = json!(namespace); }
                output.push(item);
            }
        }
    }
    let (input_tokens, output_tokens) = usage_from_openai(openai);
    json!({
        "id":format!("resp_{}",Uuid::new_v4().simple()),
        "object":"response",
        "created_at":chrono::Utc::now().timestamp(),
        "status":"completed",
        "error":Value::Null,
        "incomplete_details":Value::Null,
        "model":model,
        "output":output,
        "parallel_tool_calls":true,
        "usage":{"input_tokens":input_tokens,"output_tokens":output_tokens,"total_tokens":input_tokens+output_tokens}
    })
}
fn responses_sse(response: &Value) -> String {
    let mut out = String::new();
    let mut creating = response.clone();
    creating["status"] = Value::String("in_progress".into());
    creating["output"] = Value::Array(vec![]);
    out.push_str(&format!("event: response.created\ndata: {}\n\n", json!({"type":"response.created","sequence_number":0,"response":creating})));
    let mut sequence = 1u64;
    for (index, item) in response.get("output").and_then(Value::as_array).into_iter().flatten().enumerate() {
        out.push_str(&format!("event: response.output_item.added\ndata: {}\n\n",
            json!({"type":"response.output_item.added","sequence_number":sequence,"output_index":index,"item":item})));
        sequence += 1;
        if item.get("type").and_then(Value::as_str) == Some("reasoning") {
            if let Some(text) = item.pointer("/content/0/text").and_then(Value::as_str) {
                out.push_str(&format!("event: response.reasoning_text.delta\ndata: {}\n\n",
                    json!({"type":"response.reasoning_text.delta","sequence_number":sequence,"item_id":item["id"],"output_index":index,"content_index":0,"delta":text})));
                sequence += 1;
                out.push_str(&format!("event: response.reasoning_text.done\ndata: {}\n\n",
                    json!({"type":"response.reasoning_text.done","sequence_number":sequence,"item_id":item["id"],"output_index":index,"content_index":0,"text":text})));
                sequence += 1;
            }
        } else if item.get("type").and_then(Value::as_str) == Some("message") {
            if let Some(part) = item.get("content").and_then(Value::as_array).and_then(|v| v.first()) {
                let text = part.get("text").and_then(Value::as_str).unwrap_or("");
                out.push_str(&format!("event: response.content_part.added\ndata: {}\n\n",
                    json!({"type":"response.content_part.added","sequence_number":sequence,"item_id":item["id"],"output_index":index,"content_index":0,"part":{"type":"output_text","text":"","annotations":[]}})));
                sequence += 1;
                out.push_str(&format!("event: response.output_text.delta\ndata: {}\n\n",
                    json!({"type":"response.output_text.delta","sequence_number":sequence,"item_id":item["id"],"output_index":index,"content_index":0,"delta":text})));
                sequence += 1;
                out.push_str(&format!("event: response.output_text.done\ndata: {}\n\n",
                    json!({"type":"response.output_text.done","sequence_number":sequence,"item_id":item["id"],"output_index":index,"content_index":0,"text":text})));
                sequence += 1;
            }
        } else if item.get("type").and_then(Value::as_str) == Some("function_call") {
            let args = item.get("arguments").and_then(Value::as_str).unwrap_or("{}");
            out.push_str(&format!("event: response.function_call_arguments.delta\ndata: {}\n\n",
                json!({"type":"response.function_call_arguments.delta","sequence_number":sequence,"item_id":item["id"],"output_index":index,"delta":args})));
            sequence += 1;
            out.push_str(&format!("event: response.function_call_arguments.done\ndata: {}\n\n",
                json!({"type":"response.function_call_arguments.done","sequence_number":sequence,"item_id":item["id"],"output_index":index,"arguments":args})));
            sequence += 1;
        } else if item.get("type").and_then(Value::as_str) == Some("custom_tool_call") {
            let input = item.get("input").and_then(Value::as_str).unwrap_or("");
            out.push_str(&format!("event: response.custom_tool_call_input.delta\ndata: {}\n\n",
                json!({"type":"response.custom_tool_call_input.delta","sequence_number":sequence,"item_id":item["id"],"output_index":index,"delta":input})));
            sequence += 1;
            out.push_str(&format!("event: response.custom_tool_call_input.done\ndata: {}\n\n",
                json!({"type":"response.custom_tool_call_input.done","sequence_number":sequence,"item_id":item["id"],"output_index":index,"input":input})));
            sequence += 1;
        }
        out.push_str(&format!("event: response.output_item.done\ndata: {}\n\n",
            json!({"type":"response.output_item.done","sequence_number":sequence,"output_index":index,"item":item})));
        sequence += 1;
    }
    out.push_str(&format!("event: response.completed\ndata: {}\n\n",
        json!({"type":"response.completed","sequence_number":sequence,"response":response})));
    out
}

pub async fn responses(
    state: &GatewayState,
    headers: &HeaderMap,
    payload: &Value,
) -> Response<Body> {
    let model = payload.get("model").and_then(Value::as_str).unwrap_or("opencore");
    let model_output_limit = state.runtime.snapshot().context_size.saturating_sub(1_024).max(1_024);
    let requested_output = payload.get("max_output_tokens").and_then(Value::as_u64).unwrap_or(model_output_limit);
    let mut chat = json!({
        "model":"opencore",
        "messages":responses_messages(payload),
        "stream":false,
        "max_tokens":requested_output.min(model_output_limit)
    });
    apply_client_reasoning(payload, &mut chat, false);
    let (tools, tool_kinds) = responses_tools(payload);
    if !tools.is_empty() { chat["tools"] = Value::Array(tools); }
    let client = "Codex SDK";
    let conversation = conversation_id(headers, &chat);
    let embedded = headers.get("x-opencore-harness").and_then(|v| v.to_str().ok()) == Some("codex-sdk");
    let app_owns_timeline = codex_agent_owns_timeline(headers);
    if !app_owns_timeline { capture_request(state, &conversation, client, &redact_json(&chat)); }
    if embedded {
        if let Some(effort) = headers.get("x-opencore-effort").and_then(|v| v.to_str().ok()) {
            apply_client_reasoning(&json!({"reasoning":{"effort":effort}}), &mut chat, false);
        }
        let budget = chat["reasoning_budget_tokens"].as_u64().unwrap_or(1500);
        chat["chat_template_kwargs"] = json!({"enable_thinking":budget > 0});
    }
    let openai = match call_chat_with_conversation(state, &chat, Some(&conversation), app_owns_timeline).await {
        Ok(value) => value,
        Err(response) => return response,
    };
    if !app_owns_timeline { capture_completion(&state.store, &conversation, client, &redact_json(&openai)); }
    let output = responses_output(&openai, model, &tool_kinds);
    if payload.get("stream").and_then(Value::as_bool).unwrap_or(false) {
        sse_response(responses_sse(&output))
    } else {
        json_response(StatusCode::OK, &output)
    }
}


pub async fn responses_compact(
    state: &GatewayState,
    headers: &HeaderMap,
    payload: &Value,
) -> Response<Body> {
    let mut messages = responses_messages(payload);
    messages.push(json!({
        "role":"user",
        "content":"Create a concise working-memory summary of the conversation above. Preserve active goals, constraints, decisions, unresolved tasks, file paths, tool results, and facts needed to continue. Do not add commentary."
    }));
    let mut chat = json!({
        "model":"opencore",
        "messages":messages,
        "stream":false,
        "max_tokens":2048
    });
    let embedded = headers.get("x-opencore-harness").and_then(|v| v.to_str().ok()) == Some("codex-sdk");
    let app_owns_timeline = codex_agent_owns_timeline(headers);
    let conversation = conversation_id(headers, &chat);
    if embedded {
        if let Some(effort) = headers.get("x-opencore-effort").and_then(|v| v.to_str().ok()) {
            apply_client_reasoning(&json!({"reasoning":{"effort":effort}}), &mut chat, false);
        }
    }
    let openai = match call_chat_with_conversation(state, &chat, Some(&conversation), app_owns_timeline).await {
        Ok(value) => value,
        Err(response) => return response,
    };
    let summary = openai.pointer("/choices/0/message/content")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let (input_tokens, output_tokens) = usage_from_openai(&openai);
    let compact = json!({
        "id":format!("resp_{}",Uuid::new_v4().simple()),
        "object":"response.compaction",
        "created_at":chrono::Utc::now().timestamp(),
        "output":[{
            "id":format!("cmp_{}",Uuid::new_v4().simple()),
            "type":"compaction",
            "encrypted_content":format!("opencore:{summary}")
        }],
        "usage":{
            "input_tokens":input_tokens,
            "input_tokens_details":{"cached_tokens":0},
            "output_tokens":output_tokens,
            "output_tokens_details":{"reasoning_tokens":0},
            "total_tokens":input_tokens+output_tokens
        }
    });
    let client = "Codex SDK";
    if !app_owns_timeline { let _ = state.store.ensure_conversation(
        &conversation,
        client,
        &state.runtime.profile(),
        "Codex compaction",
    ); }
    let _ = state.store.add_timeline(
        &conversation,
        "echo",
        "system",
        client,
        "Codex compaction",
        &summary,
        &json!({"kind":"codex_compaction"})
    );
    if app_owns_timeline {
        let key = format!("agent_context:{conversation}");
        let mut context = state.store.get_setting(&key).ok().flatten()
            .and_then(|value| serde_json::from_str::<Value>(&value).ok()).unwrap_or(json!({"available":true}));
        context["compactions"] = json!(context["compactions"].as_u64().unwrap_or(0).saturating_add(1));
        context["autoCompactEnabled"] = json!(true);
        context["autoCompactThreshold"] = json!(state.runtime.snapshot().context_size.saturating_mul(85) / 100);
        state.store.set_setting(&key, &context.to_string()).ok();
    }
    json_response(StatusCode::OK, &compact)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gateway_forwards_the_app_conversation_id_to_echo() {
        let request = with_echo_conversation(
            reqwest::Client::new().post("http://127.0.0.1:1/v1/chat/completions"),
            "app-conversation-42",
            false,
        ).build().unwrap();
        assert_eq!(request.headers().get("x-echo-conversation").unwrap(), "app-conversation-42");
    }

    #[test]
    fn embedded_harness_marks_the_app_as_the_timeline_owner() {
        let request = with_echo_conversation(
            reqwest::Client::new().post("http://127.0.0.1:1/v1/chat/completions"),
            "app-conversation-42",
            true,
        ).build().unwrap();
        assert_eq!(request.headers().get("x-opencore-timeline-owner").unwrap(), "app");
    }

    #[test]
    fn converts_codex_mcp_namespaces_into_chat_completion_tools() {
        let (tools, routes) = responses_tools(&json!({"tools":[{
            "type":"namespace","name":"mcp__opencore","description":"OpenCore app tools",
            "tools":[{"type":"function","name":"test_action","description":"Run a test action",
                "parameters":{"type":"object","properties":{"value":{"type":"string"}},"required":["value"]}}]
        }]}));
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["function"]["name"], "mcp__opencore__test_action");
        assert_eq!(tools[0]["function"]["parameters"]["required"][0], "value");
        assert_eq!(routes.get("mcp__opencore__test_action"), Some(&ResponseToolRoute {
            kind: "function".into(), namespace: Some("mcp__opencore".into()), name: "test_action".into()
        }));
    }

    #[test]
    fn restores_mcp_namespace_on_model_tool_calls_and_flattens_history_back_for_local_model() {
        let (_, routes) = responses_tools(&json!({"tools":[{
            "type":"namespace","name":"mcp__opencore","description":"OpenCore app tools",
            "tools":[{"type":"function","name":"test_action","description":"Run a test action",
                "parameters":{"type":"object","properties":{"value":{"type":"string"}},"required":["value"]}}]
        }]}));
        let response = responses_output(&json!({"choices":[{"message":{"tool_calls":[{
            "id":"call_1","type":"function","function":{"name":"mcp__opencore__test_action","arguments":"{\"value\":\"bridge me\"}"}
        }]}}]}), "opencore", &routes);
        assert_eq!(response["output"][0]["type"], "function_call");
        assert_eq!(response["output"][0]["namespace"], "mcp__opencore");
        assert_eq!(response["output"][0]["name"], "test_action");

        let history = responses_messages(&json!({"input":[
            response["output"][0].clone(),
            {"type":"function_call_output","call_id":"call_1","output":"{\"ok\":true,\"value\":\"bridge me\"}"}
        ]}));
        assert_eq!(history[0]["tool_calls"][0]["function"]["name"], "mcp__opencore__test_action");
        assert_eq!(history[1]["role"], "tool");
        assert_eq!(history[1]["tool_call_id"], "call_1");
    }

    #[test]
    fn provisional_tokens_are_previewed_in_app_but_final_deltas_are_not() {
        let preview = json!({"provisional":true,"content":"draft answer","reasoning":"","phase":"drafting"});
        let event = provisional_generation_event("conversation-1", "run-1", &preview).unwrap();
        assert_eq!(event["conversationId"], "conversation-1");
        assert_eq!(event["runId"], "run-1");
        assert_eq!(event["content"], "draft answer");
        assert!(provisional_generation_event("conversation-1", "run-1", &json!({"content":"verified answer"})).is_none());
    }

    #[test]
    fn anthropic_client_receives_selected_answer_instead_of_provisional_draft() {
        let mut blocks = AnthropicStreamBlocks::default();
        let draft = blocks.push(&json!({"provisional":true,"content":"A long discarded draft.","reasoning":"Discarded thought."}));
        let answer = blocks.push(&json!({"content":"Checked answer.","reasoning":"Checked thought."}));
        assert!(draft.is_empty(), "A candidate draft is not committed Anthropic content");
        let text: String = answer.iter().filter_map(|event| event.pointer("/delta/text").and_then(Value::as_str)).collect();
        let thinking: String = answer.iter().filter_map(|event| event.pointer("/delta/thinking").and_then(Value::as_str)).collect();
        assert_eq!(text, "Checked answer.");
        assert_eq!(thinking, "Checked thought.");
    }

    #[tokio::test]
    async fn committed_text_is_emitted_before_the_upstream_stream_finishes() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (finish, wait_for_finish) = tokio::sync::oneshot::channel();
        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut request = [0; 4096];
            socket.read(&mut request).await.unwrap();
            socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n").await.unwrap();
            socket.write_all(b"data: {\"choices\":[{\"delta\":{\"content\":\"First words.\"}}]}\n\n").await.unwrap();
            socket.flush().await.unwrap();
            wait_for_finish.await.unwrap();
            socket.write_all(b"data: {\"choices\":[{\"delta\":{\"content\":\" More words.\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n").await.unwrap();
        });
        let response = reqwest::Client::builder().no_proxy().build().unwrap()
            .get(format!("http://{address}/")).send().await.unwrap();
        let (observed, mut updates) = tokio::sync::mpsc::unbounded_channel();
        let reader = tokio::spawn(async move {
            let mut blocks = AnthropicStreamBlocks::default();
            crate::chat_stream::read(response, |preview| {
                for event in blocks.push(&preview) { observed.send(event).unwrap(); }
            }).await
        });
        let partial = tokio::time::timeout(std::time::Duration::from_secs(5), async {
            loop {
                let event = updates.recv().await.unwrap();
                if event["delta"]["type"] == "text_delta" { break event; }
            }
        }).await.expect("No text arrived while upstream was still open");
        assert_eq!(partial["delta"]["text"], "First words.");
        assert!(!reader.is_finished());
        finish.send(()).unwrap();
        let result = reader.await.unwrap().unwrap();
        server.await.unwrap();
        assert_eq!(result["choices"][0]["message"]["content"], "First words. More words.");
        assert_eq!(updates.recv().await.unwrap()["delta"]["text"], " More words.");
    }

    #[test]
    fn anthropic_tools_translate_to_openai() {
        let payload = json!({"tools":[{"name":"read","description":"Read","input_schema":{"type":"object","properties":{"path":{"type":"string"}}}}]});
        let tools = anthropic_tools(&payload);
        assert_eq!(tools[0].pointer("/function/name").and_then(Value::as_str), Some("read"));
    }

    #[test]
    fn anthropic_preserves_user_and_tool_images() {
        let picture = json!({"type":"image","source":{"type":"base64","media_type":"image/png","data":"AA=="}});
        let messages = anthropic_messages(&json!({"messages":[
            {"role":"user","content":[{"type":"text","text":"Describe this"},picture.clone()]},
            {"role":"assistant","content":[{"type":"tool_use","id":"one","name":"capture","input":{}}]},
            {"role":"user","content":[{"type":"tool_result","tool_use_id":"one","content":[{"type":"text","text":"screen"},picture]}]}
        ]}));
        assert_eq!(messages[0]["content"][1]["image_url"]["url"],"data:image/png;base64,AA==");
        assert_eq!(messages[2]["role"],"tool");
        assert_eq!(messages[2]["tool_call_id"],"one");
        assert_eq!(messages[2]["content"][1]["image_url"]["url"],"data:image/png;base64,AA==");
    }

    #[test]
    fn sdk_environment_updates_keep_position_without_mid_history_system_roles() {
        let messages = anthropic_messages(&json!({"system":"instructions","messages":[
            {"role":"user","content":"task"},{"role":"system","content":"environment"},
            {"role":"assistant","content":"answer"},{"role":"system","content":[{"type":"text","text":"budget update"}]}
        ]}));
        assert_eq!(messages[0]["role"],"system");
        assert!(messages.iter().skip(1).all(|m| m["role"] != "system"));
        assert!(messages[2]["content"].as_str().unwrap().contains("environment"));
        assert!(messages[4]["content"].as_str().unwrap().contains("budget update"));
        assert_eq!(messages[2]["opencore_harness_context"], true);
        assert_eq!(messages[4]["opencore_harness_context"], true);
        assert!(messages[1].get("opencore_harness_context").is_none());
    }

    #[test]
    fn responses_function_output_becomes_tool_message() {
        let payload = json!({"input":[{"type":"function_call_output","call_id":"call_1","output":"done"}]});
        let messages = responses_messages(&payload);
        assert_eq!(messages[0].get("role").and_then(Value::as_str), Some("tool"));
        assert_eq!(messages[0].get("tool_call_id").and_then(Value::as_str), Some("call_1"));
    }

    #[test]
    fn codex_effort_reaches_the_opencore_chat_request() {
        let mut chat = json!({"model":"opencore"});
        apply_client_reasoning(&json!({"reasoning":{"effort":"xhigh"}}), &mut chat, false);
        assert_eq!(chat["reasoning_effort"], "extra-high");
    }

    #[test]
    fn only_the_app_codex_transport_skips_duplicate_gateway_timeline_events() {
        let mut headers = HeaderMap::new();
        headers.insert("x-opencore-harness", HeaderValue::from_static("codex-sdk"));
        assert!(!codex_agent_owns_timeline(&headers));
        headers.insert("x-opencore-timeline-owner", HeaderValue::from_static("app"));
        assert!(codex_agent_owns_timeline(&headers));
        headers.insert("x-opencore-harness", HeaderValue::from_static("codex-app-server"));
        assert!(codex_agent_owns_timeline(&headers));
        headers.insert("x-opencore-harness", HeaderValue::from_static("claude-agent-sdk"));
        assert!(!codex_agent_owns_timeline(&headers));
    }

    #[test]
    fn legacy_fast_effort_maps_to_reasoning_off() {
        let mut chat = json!({});
        apply_client_reasoning(&json!({"reasoning":{"effort":"fast"}}), &mut chat, false);
        assert_eq!(chat["reasoning_effort"], "off");
        assert_eq!(chat["reasoning_budget_tokens"], 0);
    }

    #[test]
    fn enabled_anthropic_512_token_budget_maps_to_low_effort() {
        let mut chat = json!({});
        apply_client_reasoning(&json!({"thinking":{"type":"enabled","budget_tokens":512}}), &mut chat, true);
        assert_eq!(chat["reasoning_effort"], "low");
        assert_eq!(chat["reasoning_budget_tokens"], 512);
    }

    #[test]
    fn zero_anthropic_budget_maps_to_reasoning_off() {
        let mut chat = json!({});
        apply_client_reasoning(&json!({"thinking":{"type":"enabled","budget_tokens":0}}), &mut chat, true);
        assert_eq!(chat["reasoning_effort"], "off");
        assert_eq!(chat["reasoning_budget_tokens"], 0);
    }

    #[test]
    fn claude_thinking_budget_reaches_the_opencore_chat_request() {
        let mut chat = json!({"model":"opencore"});
        apply_client_reasoning(&json!({"thinking":{"type":"enabled","budget_tokens":5000}}), &mut chat, true);
        assert_eq!(chat["reasoning_effort"], "extra-high");
    }

    #[test]
    fn responses_preserve_model_emitted_reasoning_as_a_reasoning_item() {
        let output = responses_output(&json!({"choices":[{"message":{
            "reasoning_content":"Checked the proof.", "content":"The result is 42."
        }}]}), "opencore", &HashMap::new());
        assert_eq!(output["output"][0]["type"], "reasoning");
        assert_eq!(output["output"][0]["content"][0]["text"], "Checked the proof.");
        assert_eq!(output["output"][1]["type"], "message");
    }
}
