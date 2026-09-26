//! Incremental SSE decoding shared by the app's model/tool loop.
use futures_util::StreamExt;
use serde_json::{json, Value};
use std::collections::BTreeMap;

#[derive(Default)]
struct Decoder {
    pending: Vec<u8>,
    message: Value,
    calls: BTreeMap<u64, Value>,
    result: Value,
    preview: Value,
    done: bool,
}

impl Decoder {
    fn push(&mut self, bytes: &[u8]) -> Result<Vec<Value>, String> {
        self.pending.extend_from_slice(bytes);
        let mut previews = Vec::new();
        while let Some(end) = self.pending.iter().position(|byte| *byte == b'\n') {
            let line = self.pending.drain(..=end).collect::<Vec<_>>();
            let line = std::str::from_utf8(&line).map_err(|e| e.to_string())?.trim();
            let Some(data) = line.strip_prefix("data:").map(str::trim) else { continue; };
            if data == "[DONE]" { self.done = true; continue; }
            if data.is_empty() { continue; }
            let event: Value = serde_json::from_str(data).map_err(|e| format!("Invalid model SSE: {e}"))?;
            if let Some(error) = event.get("error") { return Err(format!("Model stream failed: {error}")); }
            if event.get("echo_context").is_some() { continue; }
            if let Some(preview) = event.get("echo_preview") {
                if self.preview["generation"] != preview["generation"] {
                    self.preview = json!({"generation":preview["generation"], "phase":preview["phase"],
                        "provisional":true, "content":"", "reasoning":""});
                }
                append(&mut self.preview, "content", &preview["delta"]["content"]);
                append(&mut self.preview, "reasoning", &preview["delta"]["reasoning_content"]);
                previews.push(self.preview.clone());
                continue;
            }
            if self.result.is_null() { self.result = json!({}); }
            for key in ["id", "model", "usage", "timings", "echo"] {
                if let Some(value) = event.get(key) { self.result[key] = value.clone(); }
            }
            for choice in event["choices"].as_array().into_iter().flatten() {
                if choice["index"].as_u64().unwrap_or(0) != 0 { continue; }
                if self.message.is_null() { self.message = json!({"role":"assistant", "content":"", "reasoning_content":""}); }
                let delta = &choice["delta"];
                append(&mut self.message, "content", &delta["content"]);
                append(&mut self.message, "reasoning_content", &delta["reasoning_content"]);
                for (position, call) in delta["tool_calls"].as_array().into_iter().flatten().enumerate() {
                    let target = self.calls.entry(call["index"].as_u64().unwrap_or(position as u64))
                        .or_insert_with(|| json!({"id":"", "type":"function", "function":{"name":"", "arguments":""}}));
                    if call["id"].is_string() { target["id"] = call["id"].clone(); }
                    append(&mut target["function"], "name", &call["function"]["name"]);
                    append(&mut target["function"], "arguments", &call["function"]["arguments"]);
                }
                if !choice["finish_reason"].is_null() { self.result["finish_reason"] = choice["finish_reason"].clone(); }
                previews.push(json!({"phase":"answering", "content":self.message["content"],
                    "reasoning":self.message["reasoning_content"]}));
            }
        }
        Ok(previews)
    }

    fn finish(mut self) -> Result<Value, String> {
        if !self.done || self.result["finish_reason"].is_null() {
            return Err("Model stream ended before completion; partial tool calls were not executed".into());
        }
        if !self.calls.is_empty() { self.message["tool_calls"] = json!(self.calls.into_values().collect::<Vec<_>>()); }
        let reason = self.result.as_object_mut().unwrap().remove("finish_reason").unwrap();
        self.result["choices"] = json!([{"message": self.message, "finish_reason":reason}]);
        Ok(self.result)
    }
}

fn append(object: &mut Value, key: &str, piece: &Value) {
    if let Some(piece) = piece.as_str() {
        let mut text = object[key].as_str().unwrap_or_default().to_string();
        text.push_str(piece);
        object[key] = json!(text);
    }
}

pub(crate) async fn read(response: reqwest::Response, mut on_preview: impl FnMut(Value)) -> Result<Value, String> {
    if !response.headers().get("content-type").and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.contains("text/event-stream")) {
        return response.json().await.map_err(|e| format!("Invalid model response: {e}"));
    }
    let mut decoder = Decoder::default();
    let mut source = response.bytes_stream();
    let mut last = std::time::Instant::now() - std::time::Duration::from_secs(1);
    let mut pending_preview = None;
    while let Some(chunk) = source.next().await {
        for preview in decoder.push(&chunk.map_err(|e| e.to_string())?)? { pending_preview = Some(preview); }
        if last.elapsed() >= std::time::Duration::from_millis(40) {
            if let Some(preview) = pending_preview.take() { on_preview(preview); last = std::time::Instant::now(); }
        }
    }
    if let Some(preview) = pending_preview { on_preview(preview); }
    decoder.finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fragmented_utf8_and_tool_arguments_are_preserved() {
        let text = concat!(
            "data: {\"choices\":[{\"delta\":{\"reasoning_content\":\"café\"}}]}\r\n\r\n",
            "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"a\",\"function\":{\"name\":\"dev\",\"arguments\":\"{\\\"action\\\":\"}}]}}]}\n\n",
            "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":\"\\\"read\\\"}\"}}]},\"finish_reason\":\"tool_calls\"}]}\n\ndata: [DONE]\n\n");
        let mut decoder = Decoder::default();
        let mut observed = Vec::new();
        for byte in text.as_bytes() { observed.extend(decoder.push(&[*byte]).unwrap()); }
        assert!(observed.iter().any(|v| v["reasoning"] == "café"));
        let result = decoder.finish().unwrap();
        assert_eq!(result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"], "{\"action\":\"read\"}");
    }

    #[test]
    fn preview_is_live_but_not_duplicated_in_final_answer() {
        let mut decoder = Decoder::default();
        let previews = decoder.push(b"data: {\"echo_preview\":{\"generation\":\"1\",\"phase\":\"working\",\"delta\":{\"content\":\"draft\"}}}\n\n").unwrap();
        assert_eq!(previews[0]["content"], "draft");
        assert_eq!(previews[0]["provisional"], true);
        let answer_previews = decoder.push(b"data: {\"choices\":[{\"delta\":{\"content\":\"checked answer\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n").unwrap();
        assert_eq!(answer_previews.last().unwrap()["content"], "checked answer");
        assert!(answer_previews.last().unwrap().get("provisional").is_none());
        assert_eq!(decoder.finish().unwrap()["choices"][0]["message"]["content"], "checked answer");
    }

    #[test]
    fn interrupted_stream_never_returns_an_executable_call() {
        let mut decoder = Decoder::default();
        decoder.push(b"data: {\"choices\":[{\"delta\":{\"content\":\"partial\"}}]}\n\n").unwrap();
        assert!(decoder.finish().is_err());
    }
}
