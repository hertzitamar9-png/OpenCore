use crate::gateway::{capture_completion, capture_request, conversation_id, GatewayState};
use crate::redaction::redact_json;
use axum::body::Body;
use axum::http::{HeaderMap, Response, StatusCode};
use serde_json::{json, Map, Value};
use std::collections::HashMap;
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
                    else if budget >= 2500 { "high" } else if budget >= 1000 { "medium" } else { "low" })
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
        "none" | "off" => "off",
        "minimal" | "low" => "low",
        "medium" => "medium",
        "high" => "high",
        "xhigh" | "extra-high" | "extra_high" => "extra-high",
        "max" => "max",
        "ultra" | "opencore" => "opencore",
        _ => return,
    };
    chat["reasoning_effort"] = Value::String(effort.into());
}

async fn call_chat(state: &GatewayState, payload: &Value) -> Result<Value, Response<Body>> {
    let url = format!("{}/v1/chat/completions", state.runtime.upstream_url());
    let response = state.client.post(url).json(payload).send().await.map_err(|error| {
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
            for part in parts {
                match part.get("type").and_then(Value::as_str).unwrap_or("") {
                    "text" => if let Some(value) = part.get("text").and_then(Value::as_str) {
                        text.push(value.to_string());
                    },
                    "tool_result" => {
                        let id = part.get("tool_use_id").and_then(Value::as_str).unwrap_or("tool");
                        let result = flatten_text(part.get("content").unwrap_or(&Value::Null));
                        out.push(json!({"role":"tool","tool_call_id":id,"content":result}));
                    }
                    _ => {}
                }
            }
            if !text.is_empty() {
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
    let mut chat = json!({
        "model":"opencore",
        "messages":anthropic_messages(payload),
        "stream":false,
        "max_tokens":payload.get("max_tokens").and_then(Value::as_u64).unwrap_or(4096)
    });
    apply_client_reasoning(payload, &mut chat, true);
    let tools = anthropic_tools(payload);
    if !tools.is_empty() { chat["tools"] = Value::Array(tools); }
    let client = "Claude Code";
    let conversation = conversation_id(headers, &chat);
    capture_request(state, &conversation, client, &redact_json(&chat));
    let openai = match call_chat(state, &chat).await {
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
                let name = item.get("name").and_then(Value::as_str).unwrap_or("tool");
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

fn responses_tools(payload: &Value) -> (Vec<Value>, HashMap<String, String>) {
    let mut out = Vec::new();
    let mut kinds = HashMap::new();
    for tool in payload.get("tools").and_then(Value::as_array).into_iter().flatten() {
        let typ = tool.get("type").and_then(Value::as_str).unwrap_or("");
        let name = tool.get("name").and_then(Value::as_str).unwrap_or("tool").to_string();
        match typ {
            "function" => {
                kinds.insert(name.clone(), "function".into());
                out.push(json!({
                    "type":"function",
                    "function":{
                        "name":name,
                        "description":tool.get("description").and_then(Value::as_str).unwrap_or(""),
                        "parameters":tool.get("parameters").cloned().unwrap_or_else(|| json!({"type":"object"}))
                    }
                }));
            }
            "custom" => {
                kinds.insert(name.clone(), "custom".into());
                out.push(json!({
                    "type":"function",
                    "function":{
                        "name":name,
                        "description":tool.get("description").and_then(Value::as_str).unwrap_or(""),
                        "parameters":{
                            "type":"object",
                            "properties":{"input":{"type":"string","description":"Raw tool input"}},
                            "required":["input"]
                        }
                    }
                }));
            }
            _ => {}
        }
    }
    (out, kinds)
}

fn responses_output(openai: &Value, model: &str, tool_kinds: &HashMap<String, String>) -> Value {
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
            if tool_kinds.get(name).map(String::as_str) == Some("custom") {
                let input = serde_json::from_str::<Value>(arguments).ok()
                    .and_then(|v| v.get("input").and_then(Value::as_str).map(str::to_string))
                    .unwrap_or_else(|| arguments.to_string());
                output.push(json!({
                    "id":format!("ctc_{}",Uuid::new_v4().simple()),
                    "type":"custom_tool_call","status":"completed",
                    "call_id":call_id,
                    "name":name,
                    "input":input
                }));
            } else {
                output.push(json!({
                    "id":format!("fc_{}",Uuid::new_v4().simple()),
                    "type":"function_call","status":"completed",
                    "call_id":call_id,
                    "name":name,
                    "arguments":arguments
                }));
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
    let mut chat = json!({
        "model":"opencore",
        "messages":responses_messages(payload),
        "stream":false,
        "max_tokens":payload.get("max_output_tokens").and_then(Value::as_u64).unwrap_or(4096)
    });
    apply_client_reasoning(payload, &mut chat, false);
    let (tools, tool_kinds) = responses_tools(payload);
    if !tools.is_empty() { chat["tools"] = Value::Array(tools); }
    let client = "Codex";
    let conversation = conversation_id(headers, &chat);
    capture_request(state, &conversation, client, &redact_json(&chat));
    let openai = match call_chat(state, &chat).await {
        Ok(value) => value,
        Err(response) => return response,
    };
    capture_completion(&state.store, &conversation, client, &redact_json(&openai));
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
    let chat = json!({
        "model":"opencore",
        "messages":messages,
        "stream":false,
        "max_tokens":2048
    });
    let openai = match call_chat(state, &chat).await {
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
    let client = "Codex";
    let conversation = conversation_id(headers, &chat);
    let _ = state.store.ensure_conversation(
        &conversation,
        client,
        &state.runtime.profile(),
        "Codex compaction",
    );
    let _ = state.store.add_timeline(
        &conversation,
        "echo",
        "system",
        client,
        "Codex compaction",
        &summary,
        &json!({"kind":"codex_compaction"})
    );
    json_response(StatusCode::OK, &compact)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn anthropic_tools_translate_to_openai() {
        let payload = json!({"tools":[{"name":"read","description":"Read","input_schema":{"type":"object","properties":{"path":{"type":"string"}}}}]});
        let tools = anthropic_tools(&payload);
        assert_eq!(tools[0].pointer("/function/name").and_then(Value::as_str), Some("read"));
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
