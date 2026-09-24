use serde_json::Value;

const SECRET_KEYS: &[&str] = &[
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "id_token",
    "cookie",
    "set-cookie",
    "hf_token",
];

fn is_secret_key(key: &str) -> bool {
    let normalized = key.to_ascii_lowercase().replace('-', "_");
    SECRET_KEYS.iter().any(|secret| normalized == *secret)
        || normalized.ends_with("_api_key")
        || normalized.ends_with("_access_token")
        || normalized.ends_with("_refresh_token")
}

pub fn redact_text(input: &str) -> String {
    let mut output = input.to_string();
    for marker in ["Bearer ", "hf_", "sk-"] {
        let mut start = 0;
        while let Some(offset) = output[start..].find(marker) {
            let begin = start + offset;
            let token_start = begin + marker.len();
            let token_len = output[token_start..]
                .chars()
                .take_while(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
                .map(char::len_utf8)
                .sum::<usize>();
            if token_len < 6 {
                start = token_start;
                continue;
            }
            output.replace_range(begin..token_start + token_len, "[REDACTED]");
            start = begin + "[REDACTED]".len();
        }
    }
    output
}

pub fn redact_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(key, value)| {
                    let lowered = key.to_ascii_lowercase();
                    if is_secret_key(&lowered) {
                        (key.clone(), Value::String("[REDACTED]".into()))
                    } else {
                        (key.clone(), redact_json(value))
                    }
                })
                .collect(),
        ),
        Value::Array(values) => Value::Array(values.iter().map(redact_json).collect()),
        Value::String(value) => Value::String(redact_text(value)),
        other => other.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn removes_headers_and_token_shaped_text() {
        let value = serde_json::json!({
            "Authorization": "Bearer very-secret-token-value",
            "nested": {"api_key": "abc", "message": "using hf_abcdefghijklmnopqrstuvwxyz"}
        });
        let redacted = redact_json(&value).to_string();
        assert!(!redacted.contains("very-secret"));
        assert!(!redacted.contains("abcdefghijklmnopqrstuvwxyz"));
        assert!(redacted.contains("REDACTED"));
    }

    #[test]
    fn preserves_observability_token_counts() {
        let value = serde_json::json!({
            "prompt_tokens": 512,
            "live_budget_tokens": 10000,
            "max_live_tokens": 29788,
            "access_token": "secret-value"
        });
        let redacted = redact_json(&value);
        assert_eq!(redacted["prompt_tokens"], 512);
        assert_eq!(redacted["live_budget_tokens"], 10000);
        assert_eq!(redacted["max_live_tokens"], 29788);
        assert_eq!(redacted["access_token"], "[REDACTED]");
    }
}
