use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeSnapshot {
    pub profile: String,
    pub status: String,
    pub started_at: Option<String>,
    pub gateway_port: u16,
    pub backend_port: u16,
    pub echo_port: u16,
    pub model_pid: Option<u32>,
    pub echo_pid: Option<u32>,
    pub model_path: String,
    pub archive_path: String,
    pub context_size: u64,
    pub attention_kv_location: String,
    pub attention_kv_type: String,
    pub error: Option<String>,
    pub loading_phase: String,
    pub loading_step: u8,
    pub loading_steps: u8,
    pub loading_elapsed_ms: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TelemetrySnapshot {
    pub gpu_name: String,
    pub vram_used_mib: u64,
    pub vram_total_mib: u64,
    pub gpu_utilization: u64,
    pub power_watts: f64,
    pub system_memory_used_mib: u64,
    pub system_memory_total_mib: u64,
    pub disk_free_gib: f64,
    pub tokens_per_second: f64,
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub total_prompt_tokens: u64,
    pub total_completion_tokens: u64,
    pub response_count: u64,
    pub active_experts: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConversationSummary {
    pub id: String,
    pub title: String,
    pub client: String,
    pub created_at: String,
    pub updated_at: String,
    pub message_count: u64,
    pub profile: String,
    pub status: String,
    pub project: String,
    pub project_id: Option<String>,
    pub pinned: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectSummary {
    pub id: String,
    pub name: String,
    pub folder_path: Option<String>,
    pub needs_folder: bool,
    pub folder_available: bool,
    pub created_at: String,
    pub updated_at: String,
    pub conversation_count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OperationRecord {
    pub id: String,
    pub kind: String,
    pub target: String,
    pub phase: String,
    pub status: String,
    pub current: u64,
    pub total: u64,
    pub imported: u64,
    pub updated: u64,
    pub skipped: u64,
    pub summary: String,
    pub error: Option<String>,
    pub started_at: String,
    pub finished_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TimelineEntry {
    pub id: i64,
    pub conversation_id: String,
    pub timestamp: String,
    pub kind: String,
    pub role: String,
    pub source: String,
    pub title: String,
    pub content: String,
    pub metadata: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LogEntry {
    pub id: i64,
    pub timestamp: String,
    pub level: String,
    pub source: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConnectorStatus {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub status: String,
    pub endpoint: String,
    pub observable: bool,
    pub details: String,
    pub custom: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConnectorInput {
    pub id: Option<String>,
    pub name: String,
    pub kind: String,
    pub endpoint: String,
    pub match_pattern: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppSnapshot {
    pub runtime: RuntimeSnapshot,
    pub telemetry: TelemetrySnapshot,
    pub conversations: Vec<ConversationSummary>,
    pub projects: Vec<ProjectSummary>,
    pub logs: Vec<LogEntry>,
    pub connectors: Vec<ConnectorStatus>,
    pub active_conversation_ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartProfileRequest {
    pub profile: String,
    pub attach_url: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExportResult {
    pub path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatSendRequest {
    pub conversation_id: String,
    pub text: String,
    #[serde(default)]
    pub files: Vec<String>,
    #[serde(default)]
    pub reasoning_effort: ReasoningEffort,
    #[serde(default)]
    pub approval_mode: ApprovalMode,
    #[serde(default)]
    pub skills: Vec<String>,
    #[serde(default)]
    pub subagents_enabled: bool,
    #[serde(default = "default_max_subagents")]
    pub max_subagents: u16,
    #[serde(default)]
    pub project_skills_enabled: bool,
    #[serde(default = "default_compact_at_tokens")]
    pub compact_at_tokens: u32,
}

fn default_max_subagents() -> u16 { 3 }
fn default_compact_at_tokens() -> u32 { 200_000 }

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ApprovalMode {
    #[default]
    AskEveryTime,
    ApproveForMe,
    AllowChat,
    AllowAll,
}

impl ApprovalMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::AskEveryTime => "ask-every-time",
            Self::ApproveForMe => "approve-for-me",
            Self::AllowChat => "allow-chat",
            Self::AllowAll => "allow-all",
        }
    }
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ReasoningEffort {
    Off,
    Low,
    #[default]
    Medium,
    High,
    ExtraHigh,
    Max,
    Opencore,
}

impl ReasoningEffort {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Low => "low",
            Self::Medium => "medium",
            Self::High => "high",
            Self::ExtraHigh => "extra-high",
            Self::Max => "max",
            Self::Opencore => "opencore",
        }
    }

    pub fn budget_tokens(self) -> u32 {
        match self {
            Self::Off => 0,
            Self::Low => 512,
            Self::Medium => 1500,
            Self::High => 3000,
            Self::ExtraHigh => 6000,
            Self::Max => 12000,
            Self::Opencore => 6000,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatSendResult {
    pub conversation_id: String,
    pub title: String,
}

#[cfg(test)]
mod reasoning_tests {
    use super::*;

    #[test]
    fn accepts_all_user_reasoning_modes_and_rejects_unknown_values() {
        for mode in ["off", "low", "medium", "high", "extra-high", "max", "opencore"] {
            let request: ChatSendRequest = serde_json::from_value(serde_json::json!({
                "conversationId": "test", "text": "hello", "reasoningEffort": mode
            })).unwrap();
            assert_eq!(request.reasoning_effort.as_str(), mode);
        }
        let invalid = serde_json::from_value::<ChatSendRequest>(serde_json::json!({
            "conversationId": "test", "text": "hello", "reasoningEffort": "unbounded"
        }));
        assert!(invalid.is_err());
    }

    #[test]
    fn defaults_to_medium_for_older_clients() {
        let request: ChatSendRequest = serde_json::from_value(serde_json::json!({
            "conversationId": "test", "text": "hello"
        })).unwrap();
        assert_eq!(request.reasoning_effort.as_str(), "medium");
        assert_eq!(request.approval_mode.as_str(), "ask-every-time");
    }

    #[test]
    fn accepts_four_approval_modes_and_rejects_unknown_values() {
        for mode in ["ask-every-time", "approve-for-me", "allow-chat", "allow-all"] {
            let request: ChatSendRequest = serde_json::from_value(serde_json::json!({
                "conversationId":"test", "text":"hello", "approvalMode":mode
            })).unwrap();
            assert_eq!(request.approval_mode.as_str(), mode);
        }
        assert!(serde_json::from_value::<ChatSendRequest>(serde_json::json!({
            "conversationId":"test", "text":"hello", "approvalMode":"unsafe"
        })).is_err());
    }

    #[test]
    fn accepts_optional_composer_skills() {
        let request: ChatSendRequest = serde_json::from_value(serde_json::json!({
            "conversationId":"test", "text":"open a tab", "skills":["chrome-control"]
        })).unwrap();
        assert_eq!(request.skills, vec!["chrome-control"]);
    }
}
