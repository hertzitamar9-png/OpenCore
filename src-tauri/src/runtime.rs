use crate::connector_config;
use crate::models::{ConnectorStatus, RuntimeSnapshot, TelemetrySnapshot};
use crate::store::EventStore;
use chrono::Utc;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};
use sysinfo::{Disks, ProcessesToUpdate, System};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// The GGUF declares a native Qwen3.5 context of 262,144 tokens. Pass `-c 0`
/// to llama.cpp so it reads that value from model metadata instead of imposing
/// the old 32K override. This is the per-inference model context; ECHO's exact
/// conversation archive is separate and has no token-count cap (storage bound).
/// KV stays in system RAM so context does not reserve the model's VRAM budget.
const ECHO_MODEL_CONTEXT: u64 = 262_144;
const DOUCODE_MODEL_CONTEXT: u64 = 262_144;
const DOUCODE_DEFAULT_PORT: u16 = 8840;
/// Context checkpoints let the hybrid recurrent model resume from the end of the previous
/// prompt instead of re-reading everything when an assistant turn is re-rendered.
const CONTEXT_CHECKPOINT_ARGS: [&str; 4] = ["--ctx-checkpoints", "64", "--checkpoint-min-step", "256"];

struct RuntimeInner {
    profile: String,
    preferred_profile: String,
    status: String,
    started_at: Option<String>,
    error: Option<String>,
    model: Option<Child>,
    echo: Option<Child>,
    attached_backend: Option<String>,
    tokens_per_second: f64,
    prompt_tokens: u64,
    completion_tokens: u64,
    total_prompt_tokens: u64,
    total_completion_tokens: u64,
    response_count: u64,
    loading_phase: String,
    loading_step: u8,
    loading_started: Option<Instant>,
    load_duration_ms: Option<u64>,
    active_experts: Vec<String>,
    telemetry_cache: Option<(Instant, TelemetrySnapshot)>,
    connectors_cache: Option<(Instant, Vec<ConnectorStatus>)>,
}

pub struct RuntimeManager {
    start_gate: Mutex<()>,
    stop_generation: AtomicU64,
    inner: Mutex<RuntimeInner>,
    store: Arc<EventStore>,
    install_root: PathBuf,
    resource_root: Option<PathBuf>,
    gateway_port: u16,
    backend_port: u16,
    echo_port: u16,
}

impl RuntimeManager {
    pub fn new(store: Arc<EventStore>) -> Self {
        Self::new_with_resources(store, None)
    }

    pub fn new_with_resources(store: Arc<EventStore>, resource_root: Option<PathBuf>) -> Self {
        let profile_root = std::env::var_os("USERPROFILE")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."));
        let install_root = std::env::var_os("OPENCORE_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| profile_root.join("OpenCore"));
        Self {
            start_gate: Mutex::new(()),
            stop_generation: AtomicU64::new(0),
            inner: Mutex::new(RuntimeInner {
                profile: "stopped".into(),
                preferred_profile: "doucode".into(),
                status: "stopped".into(),
                started_at: None,
                error: None,
                model: None,
                echo: None,
                attached_backend: None,
                tokens_per_second: 0.0,
                prompt_tokens: 0,
                completion_tokens: 0,
                total_prompt_tokens: 0,
                total_completion_tokens: 0,
                response_count: 0,
                loading_phase: "Stopped".into(),
                loading_step: 0,
                loading_started: None,
                load_duration_ms: None,
                active_experts: vec![],
                telemetry_cache: None,
                connectors_cache: None,
            }),
            store,
            install_root,
            resource_root,
            gateway_port: 8812,
            backend_port: 8811,
            echo_port: 8813,
        }
    }

    pub fn install_root(&self) -> &Path {
        &self.install_root
    }

    pub fn echo_import_script_path(&self) -> PathBuf {
        if let Some(script) = self.resource_root.as_ref().map(|root| root.join("echo").join("echo_import.py")) {
            if script.is_file() { return script; }
        }
        self.install_root.join("echo").join("echo_import.py")
    }

    fn echo_script_path(&self) -> PathBuf {
        if let Some(script) = self.resource_root.as_ref().map(|root| root.join("echo").join("echo_server.py")) {
            if script.is_file() { return script; }
        }
        self.install_root.join("echo").join("echo_server.py")
    }

    pub fn upstream_url(&self) -> String {
        let inner = self.inner.lock().expect("runtime lock");
        if matches!(inner.profile.as_str(), "echo" | "native1m" | "unsloth-echo" | "doucode") {
            return format!("http://127.0.0.1:{}", self.echo_port);
        }
        if let Some(url) = &inner.attached_backend {
            return url.trim_end_matches('/').to_string();
        }
        format!("http://127.0.0.1:{}", self.backend_port)
    }

    pub fn profile(&self) -> String {
        self.inner.lock().expect("runtime lock").profile.clone()
    }

    pub fn select_profile(&self, profile: &str) -> Result<(), String> {
        if !matches!(profile, "echo" | "native1m" | "unsloth-echo" | "doucode") {
            return Err("profile must be echo, native1m, unsloth-echo, or doucode".into());
        }
        let mut inner = self.inner.lock().map_err(|error| error.to_string())?;
        if matches!(inner.status.as_str(), "starting" | "running") && inner.profile != profile {
            return Err("Stop the active model before selecting a different profile".into());
        }
        inner.preferred_profile = profile.to_string();
        Ok(())
    }

    pub fn direct_backend_url(&self) -> String {
        let inner = self.inner.lock().expect("runtime lock");
        if let Some(url) = &inner.attached_backend {
            return url.trim_end_matches('/').to_string();
        }
        let profile = if matches!(inner.status.as_str(), "running" | "starting") { &inner.profile } else { &inner.preferred_profile };
        if profile == "doucode" {
            return format!("http://127.0.0.1:{DOUCODE_DEFAULT_PORT}");
        }
        format!("http://127.0.0.1:{}", self.backend_port)
    }

    pub fn record_response_metrics(&self, value: &serde_json::Value) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.response_count += 1;
            if let Some(speed) = value.pointer("/timings/predicted_per_second").and_then(|v| v.as_f64()) {
                inner.tokens_per_second = speed;
            }
            if let Some(tokens) = value.pointer("/usage/prompt_tokens").and_then(|v| v.as_u64()) {
                inner.prompt_tokens = tokens;
                inner.total_prompt_tokens += tokens;
            }
            if let Some(tokens) = value.pointer("/usage/completion_tokens").and_then(|v| v.as_u64()) {
                inner.completion_tokens = tokens;
                inner.total_completion_tokens += tokens;
            }
            inner.telemetry_cache = None;
            for pointer in ["/opencore/active_experts", "/echo/active_experts"] {
                if let Some(experts) = value.pointer(pointer).and_then(|v| v.as_array()) {
                    inner.active_experts = experts
                        .iter()
                        .map(|expert| expert.as_str().map(str::to_string).unwrap_or_else(|| expert.to_string()))
                        .collect();
                    break;
                }
            }
        }
    }

    pub fn discover_unsloth_backend(&self) -> Result<String, String> {
        let own_pid = self
            .inner
            .lock()
            .map_err(|e| e.to_string())?
            .model
            .as_ref()
            .map(Child::id);
        let mut system = System::new_all();
        system.refresh_processes(ProcessesToUpdate::All, true);
        for (pid, process) in system.processes() {
            if own_pid == Some(pid.as_u32()) {
                continue;
            }
            let name = process.name().to_string_lossy().to_ascii_lowercase();
            if !name.contains("llama-server") {
                continue;
            }
            let command: Vec<String> = process
                .cmd()
                .iter()
                .map(|value| value.to_string_lossy().to_string())
                .collect();
            for index in 0..command.len().saturating_sub(1) {
                if matches!(command[index].as_str(), "--port" | "-p") {
                    if let Ok(port) = command[index + 1].parse::<u16>() {
                        if port != self.backend_port && port != self.echo_port && Self::port_open(port) {
                            return Ok(format!("http://127.0.0.1:{port}"));
                        }
                    }
                }
            }
        }
        Err("No loaded Unsloth llama-server was found. Open Unsloth and load the OpenCore GGUF first.".into())
    }

    fn port_open(port: u16) -> bool {
        TcpStream::connect_timeout(
            &SocketAddr::from(([127, 0, 0, 1], port)),
            Duration::from_millis(250),
        )
        .is_ok()
    }

    fn endpoint_ready(port: u16, path: &str) -> bool {
        let address = SocketAddr::from(([127, 0, 0, 1], port));
        let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(300)) else {
            return false;
        };
        let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
        if write!(stream, "GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n").is_err() {
            return false;
        }
        let mut buffer = [0u8; 128];
        let mut size = 0;
        while size < buffer.len() {
            let Ok(read) = stream.read(&mut buffer[size..]) else { return false; };
            if read == 0 { break; }
            size += read;
            if buffer[..size].contains(&b'\n') { break; }
        }
        let status = String::from_utf8_lossy(&buffer[..size]);
        status.starts_with("HTTP/1.1 200 ") || status.starts_with("HTTP/1.0 200 ")
    }

    fn wait_ready(&self, port: u16, path: &str, service: &str, generation: u64) -> Result<(), String> {
        let timeout = if service == "TwinCore" { 600 } else { 180 };
        let deadline = Instant::now() + Duration::from_secs(timeout);
        loop {
            if self.stop_generation.load(Ordering::SeqCst) != generation {
                return Err("Runtime loading stopped".into());
            }
            if Self::endpoint_ready(port, path) {
                self.store.log("info", "runtime", &format!("{service} ready on 127.0.0.1:{port}"));
                return Ok(());
            }
            {
                let mut inner = self.inner.lock().map_err(|e| e.to_string())?;
                let child = if matches!(service, "model" | "TwinCore") { inner.model.as_mut() } else { inner.echo.as_mut() };
                if let Some(child) = child {
                    if let Ok(Some(status)) = child.try_wait() {
                        return Err(format!("{service} exited before becoming ready ({status}). Check Runtime & Logs."));
                    }
                } else {
                    return Err(format!("{service} process was not started. Check Runtime & Logs."));
                }
            }
            if Instant::now() >= deadline {
                return Err(format!("{service} did not become ready on 127.0.0.1:{port} within {timeout} seconds. Check Runtime & Logs."));
            }
            std::thread::sleep(Duration::from_millis(300));
        }
    }


    fn reclaim_stale_opencore_port(&self, port: u16, echo: bool) -> bool {
        if !Self::port_open(port) {
            return true;
        }
        let mut system = System::new_all();
        system.refresh_processes(ProcessesToUpdate::All, true);
        let install = self.install_root.to_string_lossy().to_ascii_lowercase();
        let mut killed = false;
        for process in system.processes().values() {
            let name = process.name().to_string_lossy().to_ascii_lowercase();
            let command = process.cmd().iter()
                .map(|value| value.to_string_lossy().to_string())
                .collect::<Vec<_>>()
                .join(" ");
            let lowered = command.to_ascii_lowercase();
            let port_match = lowered.contains(&format!("--port {port}"))
                || lowered.contains(&format!("-p {port}"));
            let ours = if echo {
                port_match && lowered.contains("echo_server.py") && lowered.contains("opencore")
            } else {
                port_match && name.contains("llama-server")
                    && (lowered.contains(&install) || lowered.contains("opencore-code-single-file.gguf"))
            };
            if ours && process.kill() {
                killed = true;
                self.store.log("warn", "runtime", &format!(
                    "Reclaimed stale OpenCore {} process {} on port {}",
                    if echo { "ECHO" } else { "model" },
                    process.pid(),
                    port
                ));
            }
        }
        if killed {
            for _ in 0..30 {
                if !Self::port_open(port) {
                    return true;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        }
        !Self::port_open(port)
    }

    fn command(path: &Path) -> Command {
        let mut command = Command::new(path);
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        command
    }

    fn pipe_logs(store: Arc<EventStore>, source: &'static str, child: &mut Child) {
        if let Some(stdout) = child.stdout.take() {
            let store = store.clone();
            std::thread::spawn(move || {
                for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                    store.log("info", source, &line);
                }
            });
        }
        if let Some(stderr) = child.stderr.take() {
            std::thread::spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    let lowered = line.to_ascii_lowercase();
                    let level = if lowered.contains("error") || lowered.contains("failed") {
                        "error"
                    } else if lowered.contains("warn") {
                        "warn"
                    } else {
                        "info"
                    };
                    store.log(level, source, &line);
                }
            });
        }
    }

    pub fn python_path(&self) -> Option<PathBuf> {
        let embedded = self
            .install_root
            .join(".bootstrap")
            .join("python-3.13.15")
            .join("python.exe");
        if embedded.is_file() {
            return Some(embedded);
        }
        for candidate in ["python.exe", "python3.exe"] {
            if Command::new("where")
                .arg(candidate)
                .output()
                .map(|out| out.status.success())
                .unwrap_or(false)
            {
                return Some(PathBuf::from(candidate));
            }
        }
        None
    }

    fn doucode_release_dir(&self) -> PathBuf {
        if let Some(path) = std::env::var_os("OPENCORE_DOUCODE_RELEASE").map(PathBuf::from) {
            return path;
        }
        let profile_root = std::env::var_os("USERPROFILE").map(PathBuf::from).unwrap_or_default();
        let workspace_package = profile_root
            .join("Documents")
            .join("Best ai model in the world")
            .join("release")
            .join("doUcode");
        if workspace_package.join("twincore-config.json").is_file() {
            return workspace_package;
        }
        self.install_root.join("doUcode")
    }

    fn doucode_script_path(&self) -> Option<PathBuf> {
        self.resource_root.iter()
            .map(|root| root.join("doucode").join("serve_twincore_consensus.py"))
            .chain(std::iter::once(self.install_root.join("doucode").join("serve_twincore_consensus.py")))
            .find(|path| path.is_file())
    }

    fn doucode_llama_server(&self) -> PathBuf {
        std::env::var_os("OPENCORE_TWINCORE_LLAMA_SERVER")
            .map(PathBuf::from)
            .unwrap_or_else(|| self.install_root.join("runtime").join("llama-server.exe"))
    }

    pub fn start(&self, profile: &str, attach_url: Option<String>) -> Result<RuntimeSnapshot, String> {
        let generation = self.stop_generation.load(Ordering::SeqCst);
        let _gate = self.start_gate.lock().map_err(|e| e.to_string())?;
        self.start_inner(profile, attach_url, generation)
    }

    fn start_doucode(&self, generation: u64) -> Result<RuntimeSnapshot, String> {
        let release = self.doucode_release_dir();
        let config_path = release.join("twincore-config.json");
        let config_bytes = match std::fs::read(&config_path) {
            Ok(bytes) => bytes,
            Err(error) => return self.fail_start("doucode", format!(
                "doUcode package config not found at {}: {error}. Set OPENCORE_DOUCODE_RELEASE to its folder.",
                config_path.display()
            )),
        };
        let config: serde_json::Value = match serde_json::from_slice(&config_bytes) {
            Ok(value) => value,
            Err(error) => return self.fail_start("doucode", format!("Invalid doUcode config: {error}")),
        };
        if config.get("host").and_then(serde_json::Value::as_str) != Some("127.0.0.1") {
            return self.fail_start("doucode", "doUcode must bind to 127.0.0.1; refusing a non-loopback model endpoint".into());
        }
        let config_port = |key: &str, parent: Option<&str>| -> Result<u16, String> {
            let value = parent.and_then(|name| config.get(name)).unwrap_or(&config);
            let port = value.get(key).and_then(serde_json::Value::as_u64)
                .ok_or_else(|| format!("doUcode config is missing {key}"))?;
            u16::try_from(port).ok().filter(|port| *port != 0)
                .ok_or_else(|| format!("doUcode config has an invalid {key}"))
        };
        let service_port = match config_port("port", None) {
            Ok(port) => port,
            Err(error) => return self.fail_start("doucode", error),
        };
        let k2_port = match config_port("port", Some("k2")) {
            Ok(port) => port,
            Err(error) => return self.fail_start("doucode", error),
        };
        let nanbeige_port = match config_port("port", Some("nanbeige")) {
            Ok(port) => port,
            Err(error) => return self.fail_start("doucode", error),
        };
        let context_size = config.get("live_window_tokens").and_then(serde_json::Value::as_u64)
            .filter(|tokens| *tokens > 0).unwrap_or(DOUCODE_MODEL_CONTEXT);
        let model_path = |name: &str| -> Option<PathBuf> {
            if name.is_empty() || name.contains("..") || Path::new(name).is_absolute() { return None; }
            Some(release.join(name))
        };
        let k2_file = config.get("k2").and_then(|value| value.get("gguf_file")).and_then(serde_json::Value::as_str)
            .and_then(|name| model_path(&format!("backbones/k2/{name}")));
        let nanbeige_file = config.get("nanbeige").and_then(|value| value.get("gguf_file")).and_then(serde_json::Value::as_str)
            .and_then(|name| model_path(&format!("backbones/nanbeige/{name}")));
        let bridge_file = config.get("bridge_checkpoint").and_then(serde_json::Value::as_str)
            .and_then(model_path);
        for (label, path) in [("K2 GGUF", k2_file), ("Nanbeige GGUF", nanbeige_file), ("TwinCore bridge", bridge_file)] {
            let Some(path) = path else {
                return self.fail_start("doucode", format!("doUcode config is missing a safe path for {label}"));
            };
            if !path.is_file() {
                return self.fail_start("doucode", format!("{label} not found: {}", path.display()));
            }
        }
        let script = match self.doucode_script_path() {
            Some(path) => path,
            None => return self.fail_start("doucode", "Bundled TwinCore runtime is missing from this OpenCore build".into()),
        };
        let python = match self.python_path() {
            Some(path) => path,
            None => return self.fail_start("doucode", "doUcode requires Python with PyTorch; install a CUDA-enabled Python environment or set PATH".into()),
        };
        let llama_server = self.doucode_llama_server();
        if !llama_server.is_file() {
            return self.fail_start("doucode", format!(
                "TwinCore llama-server not found: {}. Set OPENCORE_TWINCORE_LLAMA_SERVER to the K2-compatible llama-server.exe.",
                llama_server.display()
            ));
        }
        for (port, name) in [(service_port, "TwinCore"), (k2_port, "K2"), (nanbeige_port, "Nanbeige"), (self.echo_port, "ECHO")] {
            if Self::port_open(port) {
                return self.fail_start("doucode", format!(
                    "Cannot start doUcode: {name} port {port} is already in use. The existing process was left untouched."
                ));
            }
        }
        if self.stop_generation.load(Ordering::SeqCst) != generation {
            return Err("Runtime loading stopped".into());
        }
        if let Ok(mut inner) = self.inner.lock() {
            inner.loading_phase = "Starting K2 and Nanbeige".into();
            inner.loading_step = 1;
        }
        let mut command = Self::command(&python);
        command
            .current_dir(&release)
            .arg(&script)
            .args(["--release", release.to_string_lossy().as_ref(), "--start-backbones", "--llama-server"])
            .arg(&llama_server)
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => return self.fail_start("doucode", format!("Could not start the TwinCore runtime: {error}")),
        };
        crate::child_guard::adopt(&child);
        Self::pipe_logs(self.store.clone(), "doUcode", &mut child);
        {
            let mut inner = self.inner.lock().map_err(|error| error.to_string())?;
            inner.model = Some(child);
            inner.attached_backend = Some(format!("http://127.0.0.1:{service_port}"));
            inner.loading_phase = "Starting TwinCore service".into();
            inner.loading_step = 2;
        }
        let upstream = format!("http://127.0.0.1:{service_port}");
        if let Err(error) = self.wait_ready(service_port, "/health", "TwinCore", generation) {
            return self.fail_start("doucode", error);
        }
        if let Ok(mut inner) = self.inner.lock() {
            inner.loading_phase = "Starting ECHO archive".into();
            inner.loading_step = 3;
        }
        if let Err(error) = self.start_echo(&upstream, Some(context_size)) {
            return self.fail_start("doucode", error);
        }
        if let Err(error) = self.wait_ready(self.echo_port, "/v1/models", "ECHO", generation) {
            return self.fail_start("doucode", error);
        }
        let mut inner = self.inner.lock().map_err(|error| error.to_string())?;
        inner.status = "running".into();
        inner.loading_phase = "Ready".into();
        inner.loading_step = 4;
        inner.load_duration_ms = inner.loading_started.take().map(|started| started.elapsed().as_millis() as u64);
        Ok(self.snapshot_locked(&mut inner))
    }

    pub fn ensure_running(&self) -> Result<RuntimeSnapshot, String> {
        let generation = self.stop_generation.load(Ordering::SeqCst);
        let _gate = self.start_gate.lock().map_err(|e| e.to_string())?;
        if self.stop_generation.load(Ordering::SeqCst) != generation { return Err("Runtime loading stopped".into()); }
        let snapshot = self.snapshot();
        if snapshot.status == "running" {
            return Ok(snapshot);
        }
        let profile = self.inner.lock().map_err(|error| error.to_string())?.preferred_profile.clone();
        self.start_inner(&profile, None, generation)
    }

    pub fn request_stop(&self) { self.stop_generation.fetch_add(1, Ordering::SeqCst); }

    fn fail_start(&self, profile: &str, error: String) -> Result<RuntimeSnapshot, String> {
        let _ = self.stop_inner();
        if let Ok(mut inner) = self.inner.lock() {
            inner.profile = profile.to_string();
            inner.status = "error".into();
            inner.loading_phase = "Failed".into();
            inner.error = Some(error.clone());
        }
        self.store.log("error", "runtime", &error);
        Err(error)
    }

    fn start_inner(&self, profile: &str, attach_url: Option<String>, generation: u64) -> Result<RuntimeSnapshot, String> {
        if self.stop_generation.load(Ordering::SeqCst) != generation { return Err("Runtime loading stopped".into()); }
        if !matches!(profile, "echo" | "native1m" | "unsloth-echo" | "doucode") {
            return Err("profile must be echo, native1m, unsloth-echo, or doucode".into());
        }
        self.stop_inner()?;
        let unsloth_upstream = if profile == "unsloth-echo" {
            Some(match attach_url {
                Some(url) if !url.trim().is_empty() => url,
                _ => self.discover_unsloth_backend()?,
            })
        } else {
            None
        };
        let mut inner = self.inner.lock().map_err(|e| e.to_string())?;
        inner.profile = profile.into();
        inner.preferred_profile = profile.into();
        inner.status = "starting".into();
        inner.started_at = Some(Utc::now().to_rfc3339());
        inner.loading_started = Some(Instant::now());
        inner.load_duration_ms = None;
        inner.loading_phase = "Preparing runtime".into();
        inner.loading_step = 0;
        inner.error = None;
        inner.attached_backend = None;
        inner.prompt_tokens = 0;
        inner.completion_tokens = 0;
        inner.total_prompt_tokens = 0;
        inner.total_completion_tokens = 0;
        inner.response_count = 0;
        inner.tokens_per_second = 0.0;
        inner.telemetry_cache = None;
        self.store.log("info", "runtime", &format!("Starting profile {profile}"));

        if profile == "unsloth-echo" {
            let upstream = unsloth_upstream.expect("Unsloth upstream resolved");
            inner.attached_backend = Some(upstream.clone());
            drop(inner);
            // Unsloth owns this backend's window; ECHO reads its real size from /props.
            if let Err(error) = self.start_echo(&upstream, None) {
                return self.fail_start(profile, error);
            }
            if let Ok(mut inner) = self.inner.lock() { inner.loading_phase = "Starting ECHO".into(); inner.loading_step = 2; }
            if let Err(error) = self.wait_ready(self.echo_port, "/v1/models", "ECHO", generation) {
                return self.fail_start(profile, error);
            }
            let mut inner = self.inner.lock().map_err(|e| e.to_string())?;
            inner.status = "running".into();
            inner.loading_phase = "Ready".into();
            inner.loading_step = 3;
            inner.load_duration_ms = inner.loading_started.take().map(|started| started.elapsed().as_millis() as u64);
            return Ok(self.snapshot_locked(&mut inner));
        }

        if profile == "doucode" {
            drop(inner);
            return self.start_doucode(generation);
        }

        let model = self.install_root.join("OpenCore-Code-Single-File.gguf");
        let server = self.install_root.join("runtime").join("llama-server.exe");
        drop(inner);
        if !model.is_file() {
            return self.fail_start(profile, format!("Model not found: {}", model.display()));
        }
        if !server.is_file() {
            return self.fail_start(profile, format!("Runtime not found: {}", server.display()));
        }
        if let Ok(mut inner) = self.inner.lock() { inner.loading_phase = "Loading model".into(); inner.loading_step = 1; }
        if Self::port_open(self.backend_port)
            && !self.reclaim_stale_opencore_port(self.backend_port, false)
        {
            return self.fail_start(profile, format!(
                "Backend port {} is in use by another application. Close that process or change its port.",
                self.backend_port
            ));
        }

        let mut command = Self::command(&server);
        command
            .current_dir(&self.install_root)
            .env("OPENCORE_BF16_RESIDENT_POOL", "1")
            .env("OPENCORE_BF16_EXPERT_GGUF", &model)
            .env("OPENCORE_BACKEND_DIR", self.install_root.join("runtime"))
            .env("OPENCORE_ACTIVE_EXPERTS", "5")
            .env("OPENCORE_WORKFLOW_STAGES", "18")
            .env("OPENCORE_Q8_STAGE_EXPERTS", "10000")
            .env("OPENCORE_STAGE_FILE", self.install_root.join("opencore-stage.txt"))
            .env("OPENCORE_FUSED_PRIVATE_SHARED", "1")
            .env("OPENCORE_CARRIER_GRAPH_INPUTS", "1")
            .args(["-m", model.to_string_lossy().as_ref()])
            .args(["--host", "127.0.0.1", "--port", &self.backend_port.to_string()])
            .args(["-ngl", "99", "-b", "512", "-ub", "512", "-np", "1"])
            .args(["--flash-attn", "on", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0"])
            .args(["-sm", "none", "-mg", "0", "--reasoning", "off"])
            .args(CONTEXT_CHECKPOINT_ARGS)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let projector = self.install_root.join("vision/mmproj-BF16.gguf");
        if projector.is_file() {
            command.args(["--mmproj", projector.to_string_lossy().as_ref(), "--image-max-tokens", "2048"]);
        }
        if profile == "echo" {
            command.args(["-c", "0", "-t", "1", "--no-kv-offload"]);
        } else {
            command.args(["-c", "1000000", "-t", "4"]);
            command.args([
                "--rope-scaling",
                "yarn",
                "--rope-scale",
                "3.814697265625",
                "--yarn-orig-ctx",
                "262144",
                "--override-kv",
                "qwen35.context_length=int:1000000",
                "--no-kv-offload",
            ]);
        }
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => return self.fail_start(profile, format!("Could not start llama-server: {error}")),
        };
        crate::child_guard::adopt(&child);
        Self::pipe_logs(self.store.clone(), "runtime", &mut child);
        self.inner.lock().map_err(|e| e.to_string())?.model = Some(child);

        if let Err(error) = self.wait_ready(self.backend_port, "/health", "model", generation) {
            return self.fail_start(profile, error);
        }

        let echo_enabled = matches!(profile, "echo" | "native1m");
        if let Ok(mut inner) = self.inner.lock() { inner.loading_phase = if echo_enabled { "Starting ECHO" } else { "Finishing startup" }.into(); inner.loading_step = 2; }

        if echo_enabled {
            // ECHO asks /props for the model's real metadata-derived n_ctx, except
            // the YaRN profile whose configured 1M window must match the proxy.
            let context_size = (profile == "native1m").then_some(1_000_000);
            if let Err(error) = self.start_echo(&format!("http://127.0.0.1:{}", self.backend_port), context_size) {
                return self.fail_start(profile, error);
            }
            if let Err(error) = self.wait_ready(self.echo_port, "/v1/models", "ECHO", generation) {
                return self.fail_start(profile, error);
            }
        }
        let mut inner = self.inner.lock().map_err(|e| e.to_string())?;
        inner.status = "running".into();
        inner.loading_phase = "Ready".into();
        inner.loading_step = 3;
        inner.load_duration_ms = inner.loading_started.take().map(|started| started.elapsed().as_millis() as u64);
        Ok(self.snapshot_locked(&mut inner))
    }

    fn start_echo(&self, upstream: &str, context_size: Option<u64>) -> Result<(), String> {
        let script = self.echo_script_path();
        if !script.is_file() {
            return Err(format!("ECHO server not found: {}", script.display()));
        }
        let python = self.python_path().ok_or("Python runtime not found")?;
        if Self::port_open(self.echo_port)
            && !self.reclaim_stale_opencore_port(self.echo_port, true)
        {
            return Err(format!(
                "ECHO internal port {} is in use by another application.",
                self.echo_port
            ));
        }
        let mut command = Self::command(&python);
        command
            .current_dir(&self.install_root)
            .arg(script)
            .args(["--upstream", upstream, "--port", &self.echo_port.to_string()])
            .args(["--max-continuations", "0"])
            .args(["--offload-every", "100"])
            .args(["--warm-cache-budget-mb", "128"])
            .args(["--archive", self.install_root.join("echo").join("archives").to_string_lossy().as_ref()])
            .arg("--no-console")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(size) = context_size {
            command.args(["--context-size", &size.to_string()]);
        }
        let mut child = command.spawn().map_err(|e| format!("Could not start ECHO: {e}"))?;
        crate::child_guard::adopt(&child);
        Self::pipe_logs(self.store.clone(), "echo", &mut child);
        self.inner.lock().map_err(|e| e.to_string())?.echo = Some(child);
        Ok(())
    }

    pub fn stop(&self) -> Result<(), String> {
        self.request_stop();
        let _gate = self.start_gate.lock().map_err(|e| e.to_string())?;
        self.stop_inner()
    }

    fn stop_inner(&self) -> Result<(), String> {
        let mut inner = self.inner.lock().map_err(|e| e.to_string())?;
        if let Some(mut process) = inner.echo.take() {
            let _ = process.kill();
            let _ = process.wait();
            self.store.log("info", "echo", "Process stopped by OpenCore");
        }
        if let Some(mut process) = inner.model.take() {
            let _ = process.kill();
            let _ = process.wait();
            self.store.log("info", "runtime", "Process stopped by OpenCore");
        }
        inner.profile = "stopped".into();
        inner.status = "stopped".into();
        inner.started_at = None;
        inner.loading_started = None;
        inner.loading_phase = "Stopped".into();
        inner.loading_step = 0;
        inner.attached_backend = None;
        Ok(())
    }

    fn snapshot_locked(&self, inner: &mut RuntimeInner) -> RuntimeSnapshot {
        if let Some(child) = inner.model.as_mut() {
            if let Ok(Some(status)) = child.try_wait() {
                inner.status = "error".into();
                inner.error = Some(if inner.profile == "doucode" {
                    format!("TwinCore service exited with {status}")
                } else {
                    format!("llama-server exited with {status}")
                });
            }
        }
        if let Some(child) = inner.echo.as_mut() {
            if let Ok(Some(status)) = child.try_wait() {
                inner.status = "error".into();
                inner.error = Some(format!("ECHO exited with {status}"));
            }
        }
        let (attention_kv_location, attention_kv_type) = if inner.status != "running" {
            ("not loaded".to_string(), "none".to_string())
        } else {
            match inner.profile.as_str() {
                "echo" | "native1m" | "doucode" => ("system RAM".to_string(), "Q4_0".to_string()),
                "unsloth-echo" => ("backend-managed".to_string(), "backend-reported".to_string()),
                _ => ("not loaded".to_string(), "none".to_string()),
            }
        };
        let selected_profile = if matches!(inner.status.as_str(), "running" | "starting") {
            inner.profile.as_str()
        } else {
            inner.preferred_profile.as_str()
        };
        let backend_port = inner.attached_backend.as_deref()
            .and_then(|url| url.rsplit(':').next())
            .and_then(|port| port.parse::<u16>().ok())
            .unwrap_or_else(|| if selected_profile == "doucode" { DOUCODE_DEFAULT_PORT } else { self.backend_port });
        let model_path = if selected_profile == "doucode" {
            self.doucode_release_dir().display().to_string()
        } else {
            self.install_root.join("OpenCore-Code-Single-File.gguf").display().to_string()
        };
        RuntimeSnapshot {
            profile: inner.profile.clone(),
            status: inner.status.clone(),
            started_at: inner.started_at.clone(),
            gateway_port: self.gateway_port,
            backend_port,
            echo_port: self.echo_port,
            model_pid: inner.model.as_ref().map(Child::id),
            echo_pid: inner.echo.as_ref().map(Child::id),
            model_path,
            archive_path: self.install_root.join("echo").join("archives").display().to_string(),
            context_size: if selected_profile == "native1m" { 1_000_000 } else if selected_profile == "doucode" { DOUCODE_MODEL_CONTEXT } else { ECHO_MODEL_CONTEXT },
            attention_kv_location,
            attention_kv_type,
            error: inner.error.clone(),
            loading_phase: inner.loading_phase.clone(),
            loading_step: inner.loading_step,
            loading_steps: if selected_profile == "doucode" { 4 } else { 3 },
            loading_elapsed_ms: inner.loading_started.map(|started| started.elapsed().as_millis() as u64).or(inner.load_duration_ms),
        }
    }

    pub fn snapshot(&self) -> RuntimeSnapshot {
        let mut inner = self.inner.lock().expect("runtime lock");
        self.snapshot_locked(&mut inner)
    }

    pub fn telemetry(&self) -> TelemetrySnapshot {
        {
            let inner = self.inner.lock().expect("runtime lock");
            if let Some((updated, cached)) = &inner.telemetry_cache {
                if updated.elapsed() < Duration::from_secs(10) {
                    return cached.clone();
                }
            }
        }
        let output = Self::command(Path::new("nvidia-smi.exe"))
            .args([
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ])
            .output();
        let fields: Vec<String> = output.ok()
            .and_then(|out| String::from_utf8(out.stdout).ok())
            .and_then(|text| text.lines().next().map(str::to_string))
            .map(|line| line.split(',').map(|v| v.trim().to_string()).collect())
            .unwrap_or_default();
        let mut system = System::new();
        system.refresh_memory();
        let disks = Disks::new_with_refreshed_list();
        let free = disks.iter().map(|disk| disk.available_space()).sum::<u64>();
        let mut inner = self.inner.lock().expect("runtime lock");
        let snapshot = TelemetrySnapshot {
            gpu_name: fields.first().cloned().unwrap_or_else(|| "Unavailable".into()),
            vram_used_mib: fields.get(1).and_then(|v| v.parse().ok()).unwrap_or(0),
            vram_total_mib: fields.get(2).and_then(|v| v.parse().ok()).unwrap_or(0),
            gpu_utilization: fields.get(3).and_then(|v| v.parse().ok()).unwrap_or(0),
            power_watts: fields.get(4).and_then(|v| v.parse().ok()).unwrap_or(0.0),
            system_memory_used_mib: system.used_memory() / 1024 / 1024,
            system_memory_total_mib: system.total_memory() / 1024 / 1024,
            disk_free_gib: free as f64 / 1024.0 / 1024.0 / 1024.0,
            tokens_per_second: inner.tokens_per_second,
            prompt_tokens: inner.prompt_tokens,
            completion_tokens: inner.completion_tokens,
            total_prompt_tokens: inner.total_prompt_tokens,
            total_completion_tokens: inner.total_completion_tokens,
            response_count: inner.response_count,
            active_experts: inner.active_experts.clone(),
        };
        inner.telemetry_cache = Some((Instant::now(), snapshot.clone()));
        snapshot
    }

    pub fn invalidate_connectors_cache(&self) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.connectors_cache = None;
        }
    }

    pub fn connectors(&self) -> Vec<ConnectorStatus> {
        {
            let inner = self.inner.lock().expect("runtime lock");
            if let Some((updated, cached)) = &inner.connectors_cache {
                if updated.elapsed() < Duration::from_secs(10) {
                    return cached.clone();
                }
            }
        }
        let mut connectors = self.store.connectors().unwrap_or_default();
        let profile = std::env::var_os("USERPROFILE").map(PathBuf::from).unwrap_or_default();
        for connector in &mut connectors {
            if connector.kind == "history" {
                let root = if connector.id == "claude-code" {
                    profile.join(".claude").join("projects")
                } else {
                    profile.join(".codex").join("sessions")
                };
                let configured = if connector.id == "claude-code" {
                    connector_config::claude_configured()
                } else {
                    connector_config::codex_configured()
                };
                if configured {
                    connector.status = "configured".into();
                    connector.details = if connector.id == "codex" {
                        "OpenCore Local profile is installed. Your normal Codex account remains the default; launch codex --profile opencore to use the local model.".into()
                    } else if connector.id == "claude-code" {
                        "OpenCore Local settings are installed separately. Your normal Claude account remains the default; launch Claude with --settings ~/.claude/opencore-settings.json to use the local model.".into()
                    } else {
                        "Connected to OpenCore. Load ECHO 3T or the 1M extended profile and this client will use it.".into()
                    };
                } else if root.is_dir() {
                    connector.status = "detected".into();
                    connector.details = "Client detected. Connect it to OpenCore, or sync its local transcript history.".into();
                } else {
                    connector.status = "offline".into();
                    connector.details = "Client history/config was not found.".into();
                }
                continue;
            }
            let port = connector.endpoint.rsplit(':').next()
                .and_then(|value| value.trim_end_matches("/v1").parse::<u16>().ok());
            let detected = port.map(Self::port_open).unwrap_or(false);
            if connector.observable {
                connector.status = "observed".into();
            } else if detected {
                connector.status = "detected".into();
                connector.details = format!(
                    "Detected; route its OpenCore provider to http://127.0.0.1:{}/v1",
                    self.gateway_port
                );
            } else {
                connector.status = "offline".into();
                connector.details = "Not currently detected".into();
            }
        }
        if let Ok(mut inner) = self.inner.lock() {
            inner.connectors_cache = Some((Instant::now(), connectors.clone()));
        }
        connectors
    }
}

impl Drop for RuntimeManager {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn readiness_requires_http_200_not_just_an_open_port() {
        for (reply, expected) in [("HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n", false),
                                  ("HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n", true)] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let port = listener.local_addr().unwrap().port();
            let server = std::thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                let mut request = [0u8; 256];
                let mut used = 0;
                while used < request.len() {
                    let read = stream.read(&mut request[used..]).unwrap();
                    if read == 0 { break; }
                    used += read;
                    if request[..used].windows(4).any(|part| part == b"\r\n\r\n") { break; }
                }
                stream.write_all(reply.as_bytes()).unwrap();
                stream.flush().unwrap();
            });
            assert_eq!(RuntimeManager::endpoint_ready(port, "/health"), expected);
            server.join().unwrap();
        }
    }

    #[test]
    fn unsloth_echo_public_route_goes_through_echo_proxy() {
        let path = std::env::temp_dir().join(format!("opencore-runtime-{}.sqlite3", uuid::Uuid::new_v4()));
        let store = Arc::new(EventStore::open(&path).unwrap());
        let manager = RuntimeManager::new(store);
        {
            let mut inner = manager.inner.lock().unwrap();
            inner.profile = "unsloth-echo".into();
            inner.attached_backend = Some("http://127.0.0.1:54389".into());
        }
        assert_eq!(manager.upstream_url(), "http://127.0.0.1:8813");
        drop(manager);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn native1m_public_route_goes_through_echo_proxy() {
        let path = std::env::temp_dir().join(format!("opencore-runtime-{}.sqlite3", uuid::Uuid::new_v4()));
        let store = Arc::new(EventStore::open(&path).unwrap());
        let manager = RuntimeManager::new(store);
        manager.inner.lock().unwrap().profile = "native1m".into();
        assert_eq!(manager.upstream_url(), "http://127.0.0.1:8813");
        drop(manager);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn bundled_echo_proxy_takes_precedence_over_the_older_model_package_script() {
        let root = std::env::temp_dir().join(format!("opencore-bundled-{}", uuid::Uuid::new_v4()));
        let database = root.join("events.sqlite3");
        let bundled = root.join("resources").join("echo").join("echo_server.py");
        std::fs::create_dir_all(bundled.parent().unwrap()).unwrap();
        std::fs::write(&bundled, "# bundled proxy").unwrap();
        let store = Arc::new(EventStore::open(&database).unwrap());
        let manager = RuntimeManager::new_with_resources(store, Some(root.join("resources")));
        assert_eq!(manager.echo_script_path(), bundled);
        drop(manager);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn selecting_doucode_sets_the_profile_without_loading_models() {
        let path = std::env::temp_dir().join(format!("opencore-profile-{}.sqlite3", uuid::Uuid::new_v4()));
        let store = Arc::new(EventStore::open(&path).unwrap());
        let manager = RuntimeManager::new(store);
        manager.select_profile("doucode").unwrap();
        let snapshot = manager.snapshot();
        assert_eq!(snapshot.profile, "stopped");
        assert_eq!(snapshot.status, "stopped");
        assert_eq!(snapshot.context_size, DOUCODE_MODEL_CONTEXT);
        assert!(snapshot.model_path.to_ascii_lowercase().contains("doucode"));
        assert_eq!(snapshot.model_pid, None);
        assert_eq!(snapshot.echo_pid, None);
        assert!(manager.select_profile("not-a-profile").is_err());
        drop(manager);
        let _ = std::fs::remove_file(path);
    }
}
