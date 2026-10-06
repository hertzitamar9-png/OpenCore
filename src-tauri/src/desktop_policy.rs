use serde_json::{json, Value};

pub fn apply(action: &str, args: &mut Value, keep_window_in_front: bool) -> Result<(), String> {
    let fields = args
        .as_object_mut()
        .ok_or("Desktop action arguments must be an object")?;
    for flag in ["backgroundOnly", "allowForegroundFallback"] {
        if fields.get(flag).is_some_and(|value| !value.is_boolean()) {
            return Err(format!("{flag} must be true or false"));
        }
    }
    let background = keep_window_in_front
        || fields
            .get("backgroundOnly")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    if background
        && matches!(
            action,
            "move" | "click" | "drag" | "type" | "key" | "scroll" | "navigate_url"
        )
    {
        return Err("This action needs foreground input. Use the Computer preview's background controls to keep OpenCore in front.".into());
    }
    if background {
        fields.insert("backgroundOnly".into(), json!(true));
        fields.insert("allowForegroundFallback".into(), json!(false));
    }
    // Respect a caller's explicit refusal even when the global setting is off.
    Ok(())
}

pub fn shows_activity(action: &str) -> bool {
    matches!(
        action,
        "invoke"
            | "set_value"
            | "move"
            | "click"
            | "drag"
            | "type"
            | "key"
            | "scroll"
            | "interact"
            | "set_at"
            | "commit_enter"
            | "commit_text"
            | "scroll_at"
            | "navigate_url"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn caller_background_policy_survives_global_foreground_mode() {
        let mut args = json!({"backgroundOnly":true,"allowForegroundFallback":true});
        apply("interact", &mut args, false).unwrap();
        assert_eq!(args["allowForegroundFallback"], false);
        assert_eq!(args["backgroundOnly"], true);
        let mut args = json!({"allowForegroundFallback":false});
        apply("interact", &mut args, false).unwrap();
        assert_eq!(args["allowForegroundFallback"], false);
    }

    #[test]
    fn global_background_mode_allows_accessible_text_but_rejects_injected_input() {
        let mut args = json!({"backgroundOnly":false});
        apply("commit_text", &mut args, true).unwrap();
        assert_eq!(args["backgroundOnly"], true);
        assert_eq!(args["allowForegroundFallback"], false);
        for action in [
            "move",
            "click",
            "drag",
            "type",
            "key",
            "scroll",
            "navigate_url",
        ] {
            assert!(apply(action, &mut json!({}), true).is_err());
        }
    }

    #[test]
    fn malformed_flags_are_rejected_and_capture_does_not_show_indicator() {
        assert!(apply("interact", &mut json!([]), false).is_err());
        assert!(apply("interact", &mut json!({"backgroundOnly":"false"}), false).is_err());
        assert!(apply(
            "interact",
            &mut json!({"allowForegroundFallback":null}),
            false
        )
        .is_err());
        for action in ["list", "inspect", "screenshot", "read_screen"] {
            assert!(!shows_activity(action));
        }
        assert!(shows_activity("interact"));
    }
}
