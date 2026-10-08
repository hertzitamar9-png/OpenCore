//! Native Jobs UI explicitly chooses a stable destination, without inference.
use crate::{models::ChatSendRequest, store::EventStore};
use serde_json::Value;

pub fn prepare(
    store: &EventStore,
    conversation: &str,
    name: &str,
    defaults: &Value,
    create: bool,
) -> Result<(), String> {
    if create {
        let id = conversation
            .strip_prefix("background:")
            .ok_or("Invalid new job chat ID")?;
        uuid::Uuid::parse_str(id).map_err(|_| "Invalid new job chat ID")?;
    } else if !store.conversation_exists(conversation)?
    {
        return Err("The selected chat no longer exists. Choose another chat.".into());
    }
    let key = format!("chat_request_{conversation}");
    // A retry, or a later task in the same chat, retains the original settings.
    if store.get_setting(&key)?.is_some() {
        return Ok(());
    }
    let profile = defaults["modelProfile"]
        .as_str()
        .filter(|profile| crate::runtime::supported_profile(profile))
        .ok_or("Choose a model before creating a job chat")?;
    let mut request: ChatSendRequest = serde_json::from_value(defaults["request"].clone())
        .map_err(|error| format!("Invalid job chat settings: {error}"))?;
    request.conversation_id = conversation.into();
    request.text.clear();
    request.files.clear();
    request.submission_id = None;
    if create {
        store.ensure_conversation(conversation, "OpenCore", profile, name)?;
    }
    store.set_setting(&format!("chat_model_{conversation}"), profile)?;
    store.set_setting(
        &key,
        &serde_json::to_string(&request).map_err(|error| error.to_string())?,
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn new_chat_is_created_once_and_retries_preserve_its_exact_settings() {
        let root = std::env::temp_dir().join(format!("job-chat-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let store = EventStore::open(&root.join("events.sqlite3")).unwrap();
        let id = format!("background:{}", uuid::Uuid::new_v4());
        let mut defaults = json!({"modelProfile":"doucode","request":{"conversationId":"ignored","text":"not submitted","files":["not attached"],"approvalMode":"ask-every-time","reasoningEffort":"high","skills":["web-dev"],"maxSubagents":2}});
        prepare(&store, &id, "Daily review", &defaults, true).unwrap();
        defaults["request"]["approvalMode"] = json!("allow-all");
        defaults["modelProfile"] = json!("echo");
        prepare(&store, &id, "Retry", &defaults, true).unwrap();
        assert_eq!(store.list_conversations(None).unwrap().len(), 1);
        let saved: Value = serde_json::from_str(
            &store
                .get_setting(&format!("chat_request_{id}"))
                .unwrap()
                .unwrap(),
        )
        .unwrap();
        assert_eq!(saved["conversationId"], id);
        assert_eq!(saved["approvalMode"], "ask-every-time");
        assert_eq!(saved["reasoningEffort"], "high");
        assert_eq!(saved["skills"], json!(["web-dev"]));
        assert_eq!(saved["text"], "");
        assert_eq!(saved["files"], json!([]));
        assert_eq!(
            store
                .get_setting(&format!("chat_model_{id}"))
                .unwrap()
                .unwrap(),
            "doucode"
        );
        drop(store);
        let _ = std::fs::remove_dir_all(root);
    }
}
