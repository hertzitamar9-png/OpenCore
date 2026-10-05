use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::ffi::OsString;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;
use thiserror::Error;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{ChildStdin, Command};
use tokio::sync::{mpsc, oneshot, watch, Mutex, RwLock};

const CODEX_APP_SERVER_ARGS: [&str; 3] = ["app-server", "--listen", "stdio://"];
const DEFAULT_MAX_MESSAGE_BYTES: usize = 2 * 1024 * 1024;
const DEFAULT_POOL_CAPACITY: usize = 4;
const DEFAULT_SHUTDOWN_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(3);
const SERVER_CLOSING: usize = 1usize << (usize::BITS - 1);
const MAX_QUEUED_EVENTS: usize = 32;

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct AppServerKey {
    pub conversation_id: String,
    pub workspace_identity: String,
    pub provider_id: String,
    pub runtime_schema_hash: String,
}

impl AppServerKey {
    pub fn new(
        conversation_id: impl Into<String>,
        workspace_identity: impl Into<String>,
        provider_id: impl Into<String>,
        runtime_schema_hash: impl Into<String>,
    ) -> Self {
        Self {
            conversation_id: conversation_id.into(),
            workspace_identity: workspace_identity.into(),
            provider_id: provider_id.into(),
            runtime_schema_hash: runtime_schema_hash.into(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct AppServerConfig {
    pub executable: PathBuf,
    pub command_args: Vec<OsString>,
    expected_cli_version: String,
    pub schema_path: PathBuf,
    pub protocol_revision: String,
    pub schema_sha256: String,
    pub environment: HashMap<OsString, OsString>,
    pub working_directory: Option<PathBuf>,
    pub initialize_params: Value,
    server_args: Vec<OsString>,
    max_message_bytes: usize,
}

impl AppServerConfig {
    pub fn new(
        executable: PathBuf,
        expected_cli_version: String,
        schema_path: PathBuf,
        protocol_revision: String,
        schema_sha256: String,
    ) -> Self {
        Self {
            executable,
            command_args: Vec::new(),
            expected_cli_version,
            schema_path,
            protocol_revision,
            schema_sha256,
            environment: HashMap::new(),
            working_directory: None,
            initialize_params: json!({
                "clientInfo": { "name": "opencore", "version": env!("CARGO_PKG_VERSION") },
                "capabilities": {}
            }),
            server_args: CODEX_APP_SERVER_ARGS.iter().map(OsString::from).collect(),
            max_message_bytes: DEFAULT_MAX_MESSAGE_BYTES,
        }
    }

    pub fn with_command_args(mut self, args: Vec<OsString>) -> Self {
        self.command_args = args;
        self
    }

    pub fn with_environment(mut self, environment: HashMap<OsString, OsString>) -> Self {
        self.environment = environment;
        self
    }

    pub fn with_working_directory(mut self, directory: PathBuf) -> Self {
        self.working_directory = Some(directory);
        self
    }

    pub fn with_initialize_params(mut self, params: Value) -> Self {
        self.initialize_params = params;
        self
    }
}

#[derive(Debug, Error)]
pub enum AppServerError {
    #[error("Codex app-server schema mismatch: {detail}")]
    SchemaMismatch { detail: String },
    #[error("unsupported Codex app-server version {actual}; expected {expected}")]
    VersionMismatch { expected: String, actual: String },
    #[error("could not verify the Codex app-server version: {0}")]
    VersionProbe(String),
    #[error("Codex app-server process failed: {0}")]
    Process(String),
    #[error("Codex app-server exited (code {code:?})")]
    ProcessExited { code: Option<i32> },
    #[error("Codex app-server protocol error: {0}")]
    Protocol(String),
    #[error("Codex app-server request failed ({code}): {message}")]
    RequestRejected { code: i64, message: String },
    #[error("Codex app-server request was interrupted")]
    Interrupted,
    #[error("all Codex app-server process slots are busy")]
    PoolFull,
    #[error("Codex app-server did not exit after shutdown")]
    ShutdownTimeout,
    #[error("Codex app-server response channel closed")]
    ResponseChannelClosed,
}

impl AppServerError {
    pub fn is_recoverable(&self) -> bool {
        matches!(
            self,
            Self::Process(_) | Self::ProcessExited { .. } | Self::ResponseChannelClosed
        )
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct JsonRpcError {
    pub code: i64,
    pub message: String,
    pub data: Option<Value>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum ServerMessage {
    Response {
        id: Value,
        result: Option<Value>,
        error: Option<JsonRpcError>,
    },
    Request {
        id: Value,
        method: String,
        params: Value,
    },
    Notification {
        method: String,
        params: Value,
    },
    ProcessExit {
        code: Option<i32>,
    },
    ProtocolError {
        message: String,
    },
}

struct PendingRequest {
    params: Value,
    response: oneshot::Sender<Result<Value, AppServerError>>,
}

struct ConnectionShared {
    pending: Mutex<HashMap<String, PendingRequest>>,
    canceled_ids: Mutex<HashSet<String>>,
    events: mpsc::Sender<ServerMessage>,
    alive: AtomicBool,
    active_requests: AtomicUsize,
    active_turn: AtomicBool,
    thread_id: RwLock<Option<String>>,
    kill: mpsc::UnboundedSender<()>,
}

pub struct CodexAppServer {
    key: AppServerKey,
    stdin: Mutex<Option<ChildStdin>>,
    shared: Arc<ConnectionShared>,
    events: Mutex<mpsc::Receiver<ServerMessage>>,
    request_id: AtomicU64,
    exit_status: watch::Receiver<bool>,
    max_message_bytes: usize,
}

impl CodexAppServer {
    async fn start(
        key: AppServerKey,
        config: AppServerConfig,
    ) -> Result<Arc<Self>, AppServerError> {
        validate_config(&key, &config).await?;

        let mut command = Command::new(&config.executable);
        command
            .args(&config.command_args)
            .args(&config.server_args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .env_clear()
            .envs(&config.environment);
        if let Some(directory) = &config.working_directory {
            command.current_dir(directory);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            command.as_std_mut().creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = command
            .spawn()
            .map_err(|error| AppServerError::Process(error.to_string()))?;
        let input = child
            .stdin
            .take()
            .ok_or_else(|| AppServerError::Process("stdin pipe was unavailable".into()))?;
        let output = child
            .stdout
            .take()
            .ok_or_else(|| AppServerError::Process("stdout pipe was unavailable".into()))?;
        let (event_tx, event_rx) = mpsc::channel(MAX_QUEUED_EVENTS);
        let (kill_tx, mut kill_rx) = mpsc::unbounded_channel();
        let (exit_tx, exit_rx) = watch::channel(false);
        let shared = Arc::new(ConnectionShared {
            pending: Mutex::new(HashMap::new()),
            canceled_ids: Mutex::new(HashSet::new()),
            events: event_tx,
            alive: AtomicBool::new(true),
            active_requests: AtomicUsize::new(0),
            active_turn: AtomicBool::new(false),
            thread_id: RwLock::new(None),
            kill: kill_tx,
        });

        let monitor_shared = shared.clone();
        tokio::spawn(async move {
            let status = tokio::select! {
                result = child.wait() => result,
                signal = kill_rx.recv() => {
                    if signal.is_some() {
                        let _ = child.kill().await;
                    }
                    child.wait().await
                }
            };
            let code = status.ok().and_then(|value| value.code());
            monitor_shared.alive.store(false, Ordering::SeqCst);
            let pending = std::mem::take(&mut *monitor_shared.pending.lock().await);
            for (_, request) in pending {
                let _ = request
                    .response
                    .send(Err(AppServerError::ProcessExited { code }));
            }
            monitor_shared.active_turn.store(false, Ordering::SeqCst);
            exit_tx.send_replace(true);
            let _ = monitor_shared
                .events
                .send(ServerMessage::ProcessExit { code })
                .await;
        });

        let server = Arc::new(Self {
            key,
            stdin: Mutex::new(Some(input)),
            shared: shared.clone(),
            events: Mutex::new(event_rx),
            request_id: AtomicU64::new(1),
            exit_status: exit_rx,
            max_message_bytes: config.max_message_bytes,
        });
        let reader_server = server.clone();
        let max_message_bytes = config.max_message_bytes;
        tokio::spawn(async move {
            read_server_messages(BufReader::new(output), reader_server, max_message_bytes).await;
        });

        let initialized = server.request("initialize", config.initialize_params).await;
        match initialized {
            Ok(result) if valid_initialize_result(&result) => {}
            Ok(_) => {
                let _ = server.shutdown().await;
                return Err(AppServerError::Protocol(
                    "initialize response did not match the pinned protocol".into(),
                ));
            }
            Err(error) => {
                let _ = server.shutdown().await;
                return Err(error);
            }
        }
        if let Err(error) = server.send_notification("initialized", Value::Null).await {
            let _ = server.shutdown().await;
            return Err(error);
        }
        Ok(server)
    }

    pub fn key(&self) -> &AppServerKey {
        &self.key
    }

    pub async fn request(&self, method: &str, params: Value) -> Result<Value, AppServerError> {
        if method.trim().is_empty() {
            return Err(AppServerError::Protocol(
                "JSON-RPC method cannot be empty".into(),
            ));
        }
        let _active = self.try_begin_request()?;
        if method == "turn/start" {
            self.shared.active_turn.store(true, Ordering::SeqCst);
        }
        let numeric_id = self.request_id.fetch_add(1, Ordering::Relaxed);
        let id = Value::from(numeric_id);
        let key = rpc_id_key(&id)
            .ok_or_else(|| AppServerError::Protocol("generated invalid request id".into()))?;
        let (response, receive) = oneshot::channel();
        self.shared.pending.lock().await.insert(
            key.clone(),
            PendingRequest {
                params: params.clone(),
                response,
            },
        );

        let message = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });
        if let Err(error) = self.write_json(&message).await {
            self.shared.pending.lock().await.remove(&key);
            if method == "turn/start" {
                self.shared.active_turn.store(false, Ordering::SeqCst);
            }
            return Err(error);
        }

        match receive
            .await
            .map_err(|_| AppServerError::ResponseChannelClosed)?
        {
            Ok(result) => Ok(result),
            Err(error) => {
                if method == "turn/start" {
                    self.shared.active_turn.store(false, Ordering::SeqCst);
                }
                Err(error)
            }
        }
    }

    pub async fn next_event(&self) -> Result<Option<ServerMessage>, AppServerError> {
        Ok(self.events.lock().await.recv().await)
    }

    pub async fn respond(&self, id: Value, result: Value) -> Result<(), AppServerError> {
        if rpc_id_key(&id).is_none() {
            return Err(AppServerError::Protocol(
                "server request had an invalid id".into(),
            ));
        }
        self.write_json(&json!({ "jsonrpc": "2.0", "id": id, "result": result }))
            .await
    }

    pub async fn interrupt(&self, thread_id: &str) -> Result<(), AppServerError> {
        self.cancel_pending_for_thread(thread_id).await;
        self.request("turn/interrupt", json!({ "threadId": thread_id }))
            .await?;
        self.shared.active_turn.store(false, Ordering::SeqCst);
        Ok(())
    }

    async fn cancel_pending_for_thread(&self, thread_id: &str) {
        let cancelled = {
            let mut pending = self.shared.pending.lock().await;
            let ids = pending
                .iter()
                .filter(|(_, request)| {
                    request.params.get("threadId").and_then(Value::as_str) == Some(thread_id)
                })
                .map(|(id, _)| id.clone())
                .collect::<Vec<_>>();
            let mut cancelled = Vec::with_capacity(ids.len());
            for id in ids {
                if let Some(request) = pending.remove(&id) {
                    cancelled.push((id, request.response));
                }
            }
            cancelled
        };
        {
            let mut canceled_ids = self.shared.canceled_ids.lock().await;
            for (id, _) in &cancelled {
                canceled_ids.insert(id.clone());
            }
            while canceled_ids.len() > 1024 {
                if let Some(id) = canceled_ids.iter().next().cloned() {
                    canceled_ids.remove(&id);
                }
            }
        }
        for (_, response) in cancelled {
            let _ = response.send(Err(AppServerError::Interrupted));
        }
    }

    async fn send_notification(&self, method: &str, params: Value) -> Result<(), AppServerError> {
        let mut message = json!({ "jsonrpc": "2.0", "method": method });
        if !params.is_null() {
            message["params"] = params;
        }
        self.write_json(&message).await
    }

    async fn write_json(&self, message: &Value) -> Result<(), AppServerError> {
        let mut bytes = serde_json::to_vec(message)
            .map_err(|error| AppServerError::Protocol(error.to_string()))?;
        if bytes.len() > self.max_message_bytes {
            return Err(AppServerError::Protocol(
                "outgoing JSON-RPC message exceeded the configured bound".into(),
            ));
        }
        bytes.push(b'\n');
        let mut stdin = self.stdin.lock().await;
        let input = stdin
            .as_mut()
            .ok_or(AppServerError::ProcessExited { code: None })?;
        input
            .write_all(&bytes)
            .await
            .map_err(|error| AppServerError::Process(error.to_string()))?;
        input
            .flush()
            .await
            .map_err(|error| AppServerError::Process(error.to_string()))
    }

    pub async fn shutdown(&self) -> Result<(), AppServerError> {
        self.shared
            .active_requests
            .fetch_or(SERVER_CLOSING, Ordering::SeqCst);
        self.shutdown_closed().await
    }

    async fn shutdown_closed(&self) -> Result<(), AppServerError> {
        self.stdin.lock().await.take();
        if *self.exit_status.borrow() {
            return Ok(());
        }
        if wait_for_exit(self.exit_status.clone(), DEFAULT_SHUTDOWN_TIMEOUT).await {
            return Ok(());
        }
        let _ = self.shared.kill.send(());
        if !wait_for_exit(self.exit_status.clone(), DEFAULT_SHUTDOWN_TIMEOUT).await {
            self.shared.alive.store(false, Ordering::SeqCst);
            return Err(AppServerError::ShutdownTimeout);
        }
        Ok(())
    }

    pub fn is_alive(&self) -> bool {
        self.shared.alive.load(Ordering::SeqCst)
    }

    pub async fn is_idle(&self) -> bool {
        if !self.is_alive() {
            return true;
        }
        self.shared.active_requests.load(Ordering::SeqCst) == 0
            && !self.shared.active_turn.load(Ordering::SeqCst)
            && self.shared.pending.lock().await.is_empty()
    }

    async fn evict_if_idle(&self) -> Result<bool, AppServerError> {
        if !self.is_alive() {
            self.shared
                .active_requests
                .fetch_or(SERVER_CLOSING, Ordering::SeqCst);
            return Ok(true);
        }
        if self.shared.active_turn.load(Ordering::SeqCst)
            || !self.shared.pending.lock().await.is_empty()
            || self.shared.active_requests.load(Ordering::SeqCst) != 0
        {
            return Ok(false);
        }
        if self
            .shared
            .active_requests
            .compare_exchange(0, SERVER_CLOSING, Ordering::SeqCst, Ordering::SeqCst)
            .is_err()
        {
            return Ok(false);
        }
        self.shutdown_closed().await?;
        Ok(true)
    }

    fn try_begin_request(&self) -> Result<ActiveRequestGuard<'_>, AppServerError> {
        loop {
            let state = self.shared.active_requests.load(Ordering::SeqCst);
            if state & SERVER_CLOSING != 0 || !self.is_alive() {
                return Err(AppServerError::ProcessExited { code: None });
            }
            if state & !SERVER_CLOSING == SERVER_CLOSING - 1 {
                return Err(AppServerError::Protocol(
                    "too many concurrent app-server requests".into(),
                ));
            }
            if self
                .shared
                .active_requests
                .compare_exchange_weak(state, state + 1, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
            {
                return Ok(ActiveRequestGuard(&self.shared.active_requests));
            }
        }
    }

    pub async fn pending_count(&self) -> usize {
        self.shared.pending.lock().await.len()
    }

    pub async fn set_thread_id(&self, thread_id: impl Into<String>) {
        *self.shared.thread_id.write().await = Some(thread_id.into());
    }

    pub async fn thread_id(&self) -> Option<String> {
        self.shared.thread_id.read().await.clone()
    }
}

struct ActiveRequestGuard<'a>(&'a AtomicUsize);

impl Drop for ActiveRequestGuard<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

#[derive(Default)]
struct PoolState {
    clock: u64,
    entries: HashMap<AppServerKey, PoolEntry>,
}

struct PoolEntry {
    server: Arc<CodexAppServer>,
    last_used: u64,
}

pub struct CodexAppServerPool {
    capacity: usize,
    state: Mutex<PoolState>,
}

impl Default for CodexAppServerPool {
    fn default() -> Self {
        Self::new()
    }
}

impl CodexAppServerPool {
    pub fn new() -> Self {
        Self::with_capacity(DEFAULT_POOL_CAPACITY)
    }

    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            capacity: capacity.max(1),
            state: Mutex::new(PoolState::default()),
        }
    }

    pub async fn get_or_start(
        &self,
        key: AppServerKey,
        config: AppServerConfig,
    ) -> Result<Arc<CodexAppServer>, AppServerError> {
        let mut state = self.state.lock().await;
        if let Some(entry) = state.entries.get(&key) {
            if entry.server.is_alive() {
                state.clock = state.clock.wrapping_add(1);
                let tick = state.clock;
                let entry = state
                    .entries
                    .get_mut(&key)
                    .expect("entry was just observed");
                entry.last_used = tick;
                return Ok(entry.server.clone());
            }
        }
        if let Some(entry) = state.entries.remove(&key) {
            entry.server.shutdown().await?;
        }

        if state.entries.len() >= self.capacity {
            let mut candidates = state
                .entries
                .iter()
                .map(|(key, entry)| (key.clone(), entry.last_used, entry.server.clone()))
                .collect::<Vec<_>>();
            candidates.sort_by_key(|(_, last_used, _)| *last_used);
            let mut victim = None;
            for (candidate_key, _, server) in candidates {
                if server.evict_if_idle().await? {
                    victim = Some(candidate_key);
                    break;
                }
            }
            let Some(victim_key) = victim else {
                return Err(AppServerError::PoolFull);
            };
            state.entries.remove(&victim_key);
        }

        let server = CodexAppServer::start(key.clone(), config).await?;
        state.clock = state.clock.wrapping_add(1);
        let tick = state.clock;
        state.entries.insert(
            key,
            PoolEntry {
                server: server.clone(),
                last_used: tick,
            },
        );
        Ok(server)
    }

    pub async fn remove(&self, key: &AppServerKey) -> Result<(), AppServerError> {
        if let Some(entry) = self.state.lock().await.entries.remove(key) {
            entry.server.shutdown().await?;
        }
        Ok(())
    }

    pub async fn shutdown_all(&self) -> Result<(), AppServerError> {
        let entries = {
            let mut state = self.state.lock().await;
            std::mem::take(&mut state.entries)
        };
        let mut first_error = None;
        for entry in entries.into_values() {
            if let Err(error) = entry.server.shutdown().await {
                first_error.get_or_insert(error);
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    pub async fn len(&self) -> usize {
        self.state.lock().await.entries.len()
    }
}

fn rpc_id_key(id: &Value) -> Option<String> {
    match id {
        Value::String(value) => Some(format!("s:{value}")),
        Value::Number(value) if value.is_u64() || value.is_i64() => Some(format!("n:{value}")),
        _ => None,
    }
}

fn valid_initialize_result(result: &Value) -> bool {
    result.get("codexHome").and_then(Value::as_str).is_some()
        && result
            .get("platformFamily")
            .and_then(Value::as_str)
            .is_some()
        && result.get("platformOs").and_then(Value::as_str).is_some()
        && result.get("userAgent").and_then(Value::as_str).is_some()
}

fn classify_server_message(value: Value) -> Result<ServerMessage, String> {
    let object = value
        .as_object()
        .ok_or_else(|| "JSON-RPC message must be an object".to_string())?;
    if object.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
        return Err("JSON-RPC message had an unsupported protocol version".into());
    }
    let id = object.get("id").cloned();
    let method = object
        .get("method")
        .and_then(Value::as_str)
        .map(str::to_string);
    match (id, method) {
        (Some(id), Some(method)) => {
            if object.contains_key("result") || object.contains_key("error") {
                return Err("JSON-RPC request cannot contain response fields".into());
            }
            if rpc_id_key(&id).is_none() {
                return Err("JSON-RPC server request had an invalid id".into());
            }
            Ok(ServerMessage::Request {
                id,
                method,
                params: object.get("params").cloned().unwrap_or(Value::Null),
            })
        }
        (None, Some(method)) => {
            if object.contains_key("result") || object.contains_key("error") {
                return Err("JSON-RPC notification cannot contain response fields".into());
            }
            Ok(ServerMessage::Notification {
                method,
                params: object.get("params").cloned().unwrap_or(Value::Null),
            })
        }
        (Some(id), None) => {
            if rpc_id_key(&id).is_none() {
                return Err("JSON-RPC response had an invalid id".into());
            }
            let has_result = object.contains_key("result");
            let error = object.get("error");
            if has_result == error.is_some() {
                return Err("JSON-RPC response must contain exactly one of result or error".into());
            }
            let error = error
                .map(|value| {
                    let error = value
                        .as_object()
                        .ok_or_else(|| "JSON-RPC error must be an object".to_string())?;
                    let code = error
                        .get("code")
                        .and_then(Value::as_i64)
                        .ok_or_else(|| "JSON-RPC error code must be an integer".to_string())?;
                    let message = error
                        .get("message")
                        .and_then(Value::as_str)
                        .ok_or_else(|| "JSON-RPC error message must be text".to_string())?
                        .to_string();
                    Ok::<JsonRpcError, String>(JsonRpcError {
                        code,
                        message,
                        data: error.get("data").cloned(),
                    })
                })
                .transpose()?;
            Ok(ServerMessage::Response {
                id,
                result: object.get("result").cloned(),
                error,
            })
        }
        (None, None) => Err("JSON-RPC message must contain a method or response id".into()),
    }
}

async fn read_bounded_line<R: tokio::io::AsyncBufRead + Unpin>(
    reader: &mut R,
    limit: usize,
) -> Result<Option<Vec<u8>>, AppServerError> {
    let mut line = Vec::new();
    loop {
        let available = reader
            .fill_buf()
            .await
            .map_err(|error| AppServerError::Process(error.to_string()))?;
        if available.is_empty() {
            return if line.is_empty() {
                Ok(None)
            } else {
                Err(AppServerError::Protocol(
                    "app-server closed with an incomplete JSON-RPC line".into(),
                ))
            };
        }
        let take = available
            .iter()
            .position(|byte| *byte == b'\n')
            .map(|position| position + 1)
            .unwrap_or(available.len());
        if line.len().saturating_add(take) > limit {
            return Err(AppServerError::Protocol(
                "incoming JSON-RPC line exceeded the configured bound".into(),
            ));
        }
        let has_newline = available.get(take.saturating_sub(1)) == Some(&b'\n');
        line.extend_from_slice(&available[..take]);
        reader.consume(take);
        if has_newline {
            line.pop();
            if line.last() == Some(&b'\r') {
                line.pop();
            }
            return Ok(Some(line));
        }
    }
}

async fn read_server_messages<R: tokio::io::AsyncBufRead + Unpin>(
    mut reader: R,
    server: Arc<CodexAppServer>,
    limit: usize,
) {
    loop {
        let line = match read_bounded_line(&mut reader, limit).await {
            Ok(Some(line)) => line,
            Ok(None) => return,
            Err(error) => {
                fail_protocol(&server.shared, error.to_string()).await;
                return;
            }
        };
        let parsed = serde_json::from_slice::<Value>(&line)
            .map_err(|_| "app-server sent malformed JSON".to_string())
            .and_then(classify_server_message);
        match parsed {
            Ok(ServerMessage::Response { id, result, error }) => {
                let Some(id_key) = rpc_id_key(&id) else {
                    fail_protocol(&server.shared, "app-server response id was invalid".into())
                        .await;
                    return;
                };
                let pending = server.shared.pending.lock().await.remove(&id_key);
                if let Some(pending) = pending {
                    if let Some(error) = error {
                        let _ = pending.response.send(Err(AppServerError::RequestRejected {
                            code: error.code,
                            message: error.message,
                        }));
                    } else if let Some(result) = result {
                        let _ = pending.response.send(Ok(result));
                    } else {
                        let _ = pending.response.send(Err(AppServerError::Protocol(
                            "response omitted its result".into(),
                        )));
                    }
                } else if server.shared.canceled_ids.lock().await.remove(&id_key) {
                    continue;
                } else {
                    fail_protocol(
                        &server.shared,
                        "app-server returned an unknown response id".into(),
                    )
                    .await;
                    return;
                }
            }
            Ok(message @ ServerMessage::Request { .. }) => {
                if server.shared.events.send(message).await.is_err() {
                    return;
                }
            }
            Ok(message @ ServerMessage::Notification { .. }) => {
                if let ServerMessage::Notification { method, .. } = &message {
                    if matches!(
                        method.as_str(),
                        "turn/completed" | "turn/failed" | "turn/cancelled" | "turn/interrupted"
                    ) {
                        server.shared.active_turn.store(false, Ordering::SeqCst);
                    }
                    if method == "turn/started" {
                        server.shared.active_turn.store(true, Ordering::SeqCst);
                    }
                }
                if server.shared.events.send(message).await.is_err() {
                    return;
                }
            }
            Ok(ServerMessage::ProcessExit { .. } | ServerMessage::ProtocolError { .. }) => {
                fail_protocol(
                    &server.shared,
                    "unexpected internal message classification".into(),
                )
                .await;
                return;
            }
            Err(message) => {
                fail_protocol(&server.shared, message).await;
                return;
            }
        }
    }
}

async fn fail_protocol(shared: &Arc<ConnectionShared>, message: String) {
    shared.alive.store(false, Ordering::SeqCst);
    shared
        .active_requests
        .fetch_or(SERVER_CLOSING, Ordering::SeqCst);
    let pending = std::mem::take(&mut *shared.pending.lock().await);
    for (_, request) in pending {
        let _ = request
            .response
            .send(Err(AppServerError::Protocol(message.clone())));
    }
    let _ = shared.kill.send(());
    let _ = shared
        .events
        .send(ServerMessage::ProtocolError { message })
        .await;
}

async fn validate_config(
    key: &AppServerKey,
    config: &AppServerConfig,
) -> Result<(), AppServerError> {
    if config.protocol_revision != "v2" {
        return Err(AppServerError::SchemaMismatch {
            detail: format!("unsupported protocol revision {}", config.protocol_revision),
        });
    }
    let schema = tokio::fs::read(&config.schema_path)
        .await
        .map_err(|error| AppServerError::SchemaMismatch {
            detail: format!("could not read pinned schema: {error}"),
        })?;
    let actual_hash = format!("{:x}", Sha256::digest(&schema));
    if actual_hash != config.schema_sha256 || key.runtime_schema_hash != config.schema_sha256 {
        return Err(AppServerError::SchemaMismatch {
            detail: "runtime key, manifest, and checked-in schema hash differ".into(),
        });
    }
    if !config.expected_cli_version.is_empty() {
        let executable = config.executable.clone();
        let args = config.command_args.clone();
        let environment = config.environment.clone();
        let output = tokio::task::spawn_blocking(move || {
            let mut command = std::process::Command::new(executable);
            command
                .args(args)
                .arg("--version")
                .env_clear()
                .envs(environment);
            command.output()
        })
        .await
        .map_err(|error| AppServerError::VersionProbe(error.to_string()))?
        .map_err(|error| AppServerError::VersionProbe(error.to_string()))?;
        if !output.status.success() {
            return Err(AppServerError::VersionProbe(
                "Codex executable returned a failure status".into(),
            ));
        }
        let version_output = String::from_utf8_lossy(&output.stdout);
        let actual = version_output
            .split_whitespace()
            .last()
            .unwrap_or("unknown")
            .to_string();
        if actual != config.expected_cli_version.as_str() {
            return Err(AppServerError::VersionMismatch {
                expected: config.expected_cli_version.clone(),
                actual,
            });
        }
    }
    Ok(())
}

async fn wait_for_exit(status: watch::Receiver<bool>, timeout: std::time::Duration) -> bool {
    if *status.borrow() {
        return true;
    }
    let mut waiter = status.clone();
    tokio::time::timeout(timeout, async move {
        while !*waiter.borrow() {
            if waiter.changed().await.is_err() {
                break;
            }
        }
    })
    .await
    .is_ok()
        && *status.borrow()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::collections::HashMap;
    use std::path::PathBuf;
    use std::time::Duration;

    const FIXTURE_SERVER: &str = r#"
const readline = require('node:readline');
const mode = process.env.OPENCORE_APPSERVER_FIXTURE || 'default';
const input = readline.createInterface({ input: process.stdin });
let firstRequest = null;
let heldRequest = null;
function response(id, result) { process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id, result }) + '\n'); }
function errorResponse(id, message) { process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id, error: { code: -32800, message } }) + '\n'); }
async function main() {
for await (const line of input) {
  const message = JSON.parse(line);
  if (!message.method) continue;
  if (message.method === 'initialize') {
    response(message.id, { codexHome: process.env.CODEX_HOME || 'fixture-home', platformFamily: 'windows', platformOs: 'windows', userAgent: 'codex-cli/0.160.0' });
  } else if (message.method === 'initialized') {
    if (mode === 'server_request') process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: 'approval-1', method: 'execCommandApproval', params: { command: 'echo approved?' } }) + '\n');
    if (mode === 'malformed') process.stdout.write('not-json\n');
    if (mode === 'unknown') process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: 'unknown-id', result: { unexpected: true } }) + '\n');
  } else if (message.method === 'first') {
    firstRequest = message;
    process.stdout.write(JSON.stringify({ jsonrpc: '2.0', method: 'fixture/first-received' }) + '\n');
  } else if (message.method === 'second') {
    response(message.id, { method: 'second' });
    response(firstRequest.id, { method: 'first' });
  } else if (message.method === 'run') {
    heldRequest = message;
    process.stdout.write(JSON.stringify({ jsonrpc: '2.0', method: 'fixture/run-received' }) + '\n');
  } else if (message.method === 'turn/interrupt') {
    response(message.id, {});
    if (heldRequest) errorResponse(heldRequest.id, 'interrupted');
  } else if (message.method === 'die') {
    process.exit(0);
  } else {
    response(message.id, { method: message.method });
  }
}
}
main().catch(() => process.exit(1));
"#;

    fn schema_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("resources")
            .join("codex")
            .join("protocol")
            .join("app-server.schema.json")
    }

    fn schema_hash() -> String {
        let bytes = std::fs::read(schema_path()).unwrap();
        format!("{:x}", sha2::Sha256::digest(bytes))
    }

    fn config(mode: &str) -> AppServerConfig {
        let mut config = AppServerConfig::new(
            PathBuf::from("node"),
            "0.160.0".into(),
            schema_path(),
            "v2".into(),
            schema_hash(),
        );
        config.expected_cli_version.clear();
        config.command_args = vec!["-e".into(), FIXTURE_SERVER.into()];
        config.server_args.clear();
        config.environment = HashMap::from([
            ("OPENCORE_APPSERVER_FIXTURE".into(), mode.into()),
            ("SystemRoot".into(), std::env::var_os("SystemRoot").unwrap()),
            ("WINDIR".into(), std::env::var_os("WINDIR").unwrap()),
            ("TEMP".into(), std::env::var_os("TEMP").unwrap()),
            ("TMP".into(), std::env::var_os("TMP").unwrap()),
            ("PATH".into(), std::env::var_os("PATH").unwrap()),
            (
                "USERPROFILE".into(),
                std::env::var_os("USERPROFILE").unwrap(),
            ),
        ]);
        config
    }

    fn key(conversation: &str, workspace: &str, provider: &str, schema: &str) -> AppServerKey {
        let hash = if schema == "schema2" {
            "b".repeat(64)
        } else {
            schema_hash()
        };
        AppServerKey::new(conversation, workspace, provider, hash)
    }

    async fn wait_for_pending(server: &CodexAppServer) {
        tokio::time::timeout(Duration::from_secs(2), async {
            while server.pending_count().await == 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn json_rpc_responses_correlate_when_replies_arrive_out_of_order() {
        let pool = CodexAppServerPool::new();
        let server = pool
            .get_or_start(key("c1", "w1", "local", "schema1"), config("out_of_order"))
            .await
            .unwrap();
        let first_server = server.clone();
        let second_server = server.clone();
        let first = tokio::spawn(async move { first_server.request("first", json!({})).await });
        assert!(
            matches!(server.next_event().await.unwrap(), Some(ServerMessage::Notification { method, .. }) if method == "fixture/first-received")
        );
        let second = tokio::spawn(async move { second_server.request("second", json!({})).await });
        let (first, second) = tokio::join!(first, second);
        assert_eq!(first.unwrap().unwrap()["method"], "first");
        assert_eq!(second.unwrap().unwrap()["method"], "second");
        pool.shutdown_all().await.unwrap();
    }

    #[tokio::test]
    async fn server_requests_are_not_mistaken_for_notifications() {
        let pool = CodexAppServerPool::new();
        let server = pool
            .get_or_start(
                key("c2", "w1", "local", "schema1"),
                config("server_request"),
            )
            .await
            .unwrap();
        let request = server.next_event().await.unwrap().unwrap();
        let ServerMessage::Request { id, method, params } = request else {
            panic!("expected a server request")
        };
        assert_eq!(method, "execCommandApproval");
        assert_eq!(params["command"], "echo approved?");
        server
            .respond(id, json!({ "decision": "decline" }))
            .await
            .unwrap();
        pool.shutdown_all().await.unwrap();
    }

    #[tokio::test]
    async fn malformed_or_unknown_messages_fail_closed() {
        for mode in ["malformed", "unknown"] {
            let pool = CodexAppServerPool::new();
            let server = pool
                .get_or_start(key(mode, "w1", "local", "schema1"), config(mode))
                .await
                .unwrap();
            let event = server.next_event().await.unwrap().unwrap();
            assert!(matches!(event, ServerMessage::ProtocolError { .. }));
            assert!(server
                .request("after-protocol-error", json!({}))
                .await
                .is_err());
            pool.shutdown_all().await.unwrap();
        }
    }

    #[tokio::test]
    async fn interrupt_cancels_pending_requests_and_reaps_child() {
        let pool = CodexAppServerPool::new();
        let server = pool
            .get_or_start(key("c3", "w1", "local", "schema1"), config("hold"))
            .await
            .unwrap();
        let pending_server = server.clone();
        let pending = tokio::spawn(async move {
            pending_server
                .request("run", json!({ "threadId": "thread-3" }))
                .await
        });
        assert!(
            matches!(server.next_event().await.unwrap(), Some(ServerMessage::Notification { method, .. }) if method == "fixture/run-received")
        );
        server.interrupt("thread-3").await.unwrap();
        assert!(matches!(
            pending.await.unwrap(),
            Err(AppServerError::Interrupted)
        ));
        assert!(server.is_alive());
        server.shutdown().await.unwrap();
        assert!(!server.is_alive());
    }

    #[tokio::test]
    async fn process_exit_mid_request_returns_recoverable_error_without_losing_thread_id() {
        let pool = CodexAppServerPool::new();
        let server = pool
            .get_or_start(key("c4", "w1", "local", "schema1"), config("exit"))
            .await
            .unwrap();
        server.set_thread_id("thread-preserved").await;
        let error = server.request("die", json!({})).await.unwrap_err();
        assert!(error.is_recoverable());
        assert_eq!(
            server.thread_id().await.as_deref(),
            Some("thread-preserved")
        );
        pool.shutdown_all().await.unwrap();
    }

    #[tokio::test]
    async fn schema_version_mismatch_prevents_initialize() {
        let pool = CodexAppServerPool::new();
        let mut bad_config = config("default");
        bad_config.protocol_revision = "v1".into();
        let result = pool
            .get_or_start(key("c5", "w1", "local", "schema1"), bad_config)
            .await;
        assert!(matches!(result, Err(AppServerError::SchemaMismatch { .. })));
        assert_eq!(pool.len().await, 0);
    }

    #[tokio::test]
    async fn conversation_pool_reuses_only_the_matching_scoped_server() {
        let pool = CodexAppServerPool::new();
        let original_key = key("c6", "w1", "local", "schema1");
        let original = pool
            .get_or_start(original_key.clone(), config("default"))
            .await
            .unwrap();
        let same = pool
            .get_or_start(original_key, config("default"))
            .await
            .unwrap();
        assert!(Arc::ptr_eq(&original, &same));
        let changed_workspace = pool
            .get_or_start(key("c6", "w2", "local", "schema1"), config("default"))
            .await
            .unwrap();
        let changed_provider = pool
            .get_or_start(key("c6", "w1", "openai", "schema1"), config("default"))
            .await
            .unwrap();
        let changed_schema = pool
            .get_or_start(key("c6", "w1", "local", "schema2"), config("default"))
            .await;
        assert!(!Arc::ptr_eq(&original, &changed_workspace));
        assert!(!Arc::ptr_eq(&original, &changed_provider));
        assert!(matches!(
            changed_schema,
            Err(AppServerError::SchemaMismatch { .. })
        ));
        pool.shutdown_all().await.unwrap();
    }

    #[tokio::test]
    async fn pool_caps_live_children_at_four_and_evicts_only_idle_server() {
        let pool = CodexAppServerPool::with_capacity(4);
        let first_key = key("c7-1", "w1", "local", "schema1");
        let first = pool
            .get_or_start(first_key.clone(), config("hold"))
            .await
            .unwrap();
        let first_pending_server = first.clone();
        let first_pending = tokio::spawn(async move {
            first_pending_server
                .request("run", json!({ "threadId": "thread-1" }))
                .await
        });
        wait_for_pending(&first).await;

        let second_key = key("c7-2", "w1", "local", "schema1");
        let second = pool
            .get_or_start(second_key.clone(), config("hold"))
            .await
            .unwrap();
        let third_key = key("c7-3", "w1", "local", "schema1");
        let third = pool
            .get_or_start(third_key.clone(), config("hold"))
            .await
            .unwrap();
        let fourth_key = key("c7-4", "w1", "local", "schema1");
        let fourth = pool
            .get_or_start(fourth_key.clone(), config("hold"))
            .await
            .unwrap();

        let fifth_key = key("c7-5", "w1", "local", "schema1");
        let fifth = pool
            .get_or_start(fifth_key.clone(), config("hold"))
            .await
            .unwrap();
        assert_eq!(pool.len().await, 4);
        assert!(Arc::ptr_eq(
            &first,
            &pool
                .get_or_start(first_key.clone(), config("hold"))
                .await
                .unwrap()
        ));
        assert!(!second.is_alive());

        let mut pending = vec![first_pending];
        for server in [third, fourth, fifth] {
            let pending_server = server.clone();
            pending.push(tokio::spawn(async move {
                pending_server
                    .request("run", json!({ "threadId": "busy" }))
                    .await
            }));
            wait_for_pending(&server).await;
        }
        let sixth = pool
            .get_or_start(key("c7-6", "w1", "local", "schema1"), config("hold"))
            .await;
        assert!(matches!(sixth, Err(AppServerError::PoolFull)));
        assert_eq!(pool.len().await, 4);
        pool.shutdown_all().await.unwrap();
        for task in pending {
            let _ = task.await;
        }
    }
}
