//! Keep the private engine compatible with OpenCore's installed Responses gateway.
//! Namespaces stay intact inside the engine, including approvals and MCP dispatch.
use codex_api::ResponseEvent;
use codex_api::ResponsesApiRequest;
use codex_protocol::models::ResponseItem;
use serde_json::Value;
use std::sync::Arc;

fn wire_name(namespace: &str, name: &str) -> String {
    if namespace == "functions" {
        name.to_owned()
    } else {
        format!("ocns_{}_{}_{}", namespace.len(), namespace, name)
    }
}

fn original_name(name: &str) -> Option<(&str, &str)> {
    let (length, rest) = name.strip_prefix("ocns_")?.split_once('_')?;
    let length = length.parse::<usize>().ok()?;
    let namespace = rest.get(..length)?;
    let function = rest.get(length..)?.strip_prefix('_')?;
    (!namespace.is_empty() && !function.is_empty()).then_some((namespace, function))
}

fn flatten_tools(tools: &[Value], namespace: Option<&str>, out: &mut Vec<Value>) {
    for tool in tools {
        if tool["type"] == "namespace" {
            if let (Some(name), Some(children)) = (tool["name"].as_str(), tool["tools"].as_array()) {
                let start = out.len();
                flatten_tools(children, Some(name), out);
                // Retain namespace documentation once rather than multiplying it
                // by every method in a server. Argument schemas stay byte-exact.
                if let (Some(first), Some(description)) = (out.get_mut(start), tool["description"].as_str()) {
                    if !description.is_empty() {
                        first["description"] = Value::String(format!("{}\n\n{}", description,
                            first["description"].as_str().unwrap_or_default()));
                    }
                }
            }
        } else {
            let mut tool = tool.clone();
            if let (Some(namespace), Some(name)) = (namespace, tool["name"].as_str()) {
                tool["name"] = Value::String(wire_name(namespace, name));
            }
            out.push(tool);
        }
    }
}

pub(super) fn prepare(request: &mut ResponsesApiRequest) -> serde_json::Result<()> {
    if let Some(tools) = request.tools.as_ref() {
        let value = serde_json::to_value(tools)?;
        if let Some(tools) = value.as_array() {
            let mut flat = Vec::new();
            flatten_tools(tools, None, &mut flat);
            let raw = serde_json::value::to_raw_value(&flat)?;
            request.tools = Some(Arc::<serde_json::value::RawValue>::from(raw).into());
        }
    }
    for item in &mut request.input {
        match item {
            ResponseItem::FunctionCall { name, namespace, .. }
            | ResponseItem::CustomToolCall { name, namespace, .. } => {
                if let Some(original_namespace) = namespace.take() {
                    *name = wire_name(&original_namespace, name);
                }
            }
            _ => {}
        }
    }
    Ok(())
}

pub(super) fn restore(event: &mut ResponseEvent) {
    if let ResponseEvent::OutputItemAdded(item) | ResponseEvent::OutputItemDone(item) = event {
        match item {
            ResponseItem::FunctionCall { name, namespace, .. }
            | ResponseItem::CustomToolCall { name, namespace, .. } if namespace.is_none() => {
                if let Some((original_namespace, original_function)) = original_name(name) {
                    let original_namespace = original_namespace.to_owned();
                    let original_function = original_function.to_owned();
                    *namespace = Some(original_namespace);
                    *name = original_function;
                }
            }
            _ => {}
        }
    }
}
