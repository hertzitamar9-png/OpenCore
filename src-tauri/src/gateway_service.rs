//! The lightweight gateway has a lifecycle independent of model inference.
use crate::store::EventStore;
use axum::Router;
use serde::{Deserialize, Serialize};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio_util::sync::CancellationToken;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GatewaySnapshot {
    pub status: String,
    pub port: u16,
    pub restart_count: u64,
    pub error: Option<String>,
}

pub struct GatewayService {
    state: Mutex<GatewaySnapshot>,
    stop: CancellationToken,
    task: Mutex<Option<tauri::async_runtime::JoinHandle<()>>>,
    store: Arc<EventStore>,
}

impl GatewayService {
    pub fn new(store: Arc<EventStore>, port: u16) -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new(GatewaySnapshot {
                status: "starting".into(),
                port,
                restart_count: 0,
                error: None,
            }),
            stop: CancellationToken::new(),
            task: Mutex::new(None),
            store,
        })
    }

    pub fn snapshot(&self) -> GatewaySnapshot {
        self.state.lock().expect("gateway state").clone()
    }

    fn set_state(&self, status: &str, error: Option<String>) {
        let mut state = self.state.lock().expect("gateway state");
        if state.status != status || state.error != error {
            self.store.log(
                if error.is_some() { "warn" } else { "info" },
                "gateway",
                &error
                    .as_ref()
                    .map(|error| format!("Gateway {status}: {error}"))
                    .unwrap_or_else(|| format!("Gateway {status} on 127.0.0.1:{}", state.port)),
            );
        }
        state.status = status.into();
        state.error = error;
    }

    pub fn start(self: &Arc<Self>, router: Router) {
        let mut slot = self.task.lock().expect("gateway task");
        if slot.is_some() || self.stop.is_cancelled() {
            return;
        }
        let service = self.clone();
        *slot = Some(tauri::async_runtime::spawn(async move {
            service.supervise(router).await;
        }));
    }

    async fn supervise(&self, router: Router) {
        let port = self.snapshot().port;
        let client = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(2))
            .build()
            .expect("gateway health client");
        let mut failures = 0u32;
        loop {
            if self.stop.is_cancelled() {
                break;
            }
            let result = match tokio::net::TcpListener::bind(("127.0.0.1", port)).await {
                Err(error) => Err(format!("Could not listen on port {port}: {error}")),
                Ok(listener) => {
                    let app = router.clone();
                    let mut server = tokio::spawn(async move { axum::serve(listener, app).await });
                    let mut unhealthy = 0;
                    let mut probe = tokio::time::interval(Duration::from_secs(3));
                    self.set_state("starting", None);
                    let result = loop {
                        tokio::select! {
                            _ = self.stop.cancelled() => break Ok(()),
                            result = &mut server => break Err(match result {
                                Ok(Ok(())) => "Gateway listener exited unexpectedly".into(),
                                Ok(Err(error)) => error.to_string(),
                                Err(error) => format!("Gateway listener failed: {error}"),
                            }),
                            _ = probe.tick() => {
                                let healthy = match client.get(format!("http://127.0.0.1:{port}/health")).send().await {
                                    Ok(response) if response.status().is_success() => response.json::<serde_json::Value>().await
                                        .is_ok_and(|body| body["service"] == "opencore-control-gateway" && body["status"] == "ok"),
                                    _ => false,
                                };
                                if healthy {
                                    unhealthy = 0; failures = 0; self.set_state("ready", None);
                                } else {
                                    unhealthy += 1;
                                    self.set_state("recovering", Some("Gateway health check failed; reconnecting automatically".into()));
                                    if unhealthy >= 3 { break Err("Gateway stopped responding to health checks".into()); }
                                }
                            }
                        }
                    };
                    if !server.is_finished() {
                        server.abort();
                        let _ = server.await;
                    }
                    result
                }
            };
            if self.stop.is_cancelled() {
                break;
            }
            if let Err(error) = result {
                self.state.lock().expect("gateway state").restart_count += 1;
                self.set_state("recovering", Some(error));
            }
            let delay = Duration::from_secs(1u64 << failures.min(5));
            failures = failures.saturating_add(1);
            tokio::select! { _ = self.stop.cancelled() => break, _ = tokio::time::sleep(delay) => {} }
        }
        self.set_state("stopped", None);
    }

    pub async fn shutdown(&self) {
        self.stop.cancel();
        let handle = self.task.lock().ok().and_then(|mut task| task.take());
        if let Some(handle) = handle {
            let _ = handle.await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{routing::get, Json};
    use serde_json::json;

    #[tokio::test]
    async fn busy_port_recovers_without_starting_the_model_and_quit_releases_it() {
        let root = std::env::temp_dir().join(format!("gateway-recovery-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let store = Arc::new(EventStore::open(&root.join("events.sqlite3")).unwrap());
        let model = crate::runtime::RuntimeManager::new(store.clone());
        let occupied = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let port = occupied.local_addr().unwrap().port();
        let gateway = GatewayService::new(store.clone(), port);
        gateway.start(Router::new().route(
            "/health",
            get(|| async { Json(json!({"status":"ok","service":"opencore-control-gateway"})) }),
        ));
        tokio::time::timeout(Duration::from_secs(5), async {
            while gateway.snapshot().status != "recovering" {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .unwrap();
        assert!(gateway
            .snapshot()
            .error
            .as_deref()
            .unwrap()
            .contains("Could not listen"));
        drop(occupied);
        tokio::time::timeout(Duration::from_secs(8), async {
            while gateway.snapshot().status != "ready" {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .unwrap();
        assert!(gateway.snapshot().restart_count > 0);
        assert_eq!(model.snapshot().status, "stopped");
        assert!(model.snapshot().model_pid.is_none());
        gateway.shutdown().await;
        assert_eq!(gateway.snapshot().status, "stopped");
        let released = tokio::net::TcpListener::bind(("127.0.0.1", port))
            .await
            .unwrap();
        drop(released);
        drop(gateway);
        drop(model);
        drop(store);
        let _ = std::fs::remove_dir_all(root);
    }

    #[tokio::test]
    async fn an_unresponsive_gateway_is_replaced_and_becomes_ready_again() {
        use std::sync::atomic::{AtomicBool, Ordering};
        let root = std::env::temp_dir().join(format!("gateway-unhealthy-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let store = Arc::new(EventStore::open(&root.join("events.sqlite3")).unwrap());
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        let healthy = Arc::new(AtomicBool::new(false));
        let flag = healthy.clone();
        let gateway = GatewayService::new(store, port);
        gateway.start(Router::new().route(
            "/health",
            get(move || {
                let flag = flag.clone();
                async move {
                    Json(if flag.load(Ordering::Acquire) {
                        json!({"status":"ok","service":"opencore-control-gateway"})
                    } else {
                        json!({"status":"failed"})
                    })
                }
            }),
        ));
        tokio::time::timeout(Duration::from_secs(15), async {
            while gateway.snapshot().restart_count == 0 {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .unwrap();
        healthy.store(true, Ordering::Release);
        tokio::time::timeout(Duration::from_secs(8), async {
            while gateway.snapshot().status != "ready" {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .unwrap();
        gateway.shutdown().await;
        drop(gateway);
        let _ = std::fs::remove_dir_all(root);
    }
}
