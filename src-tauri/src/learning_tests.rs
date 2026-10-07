use super::*;

fn fixture() -> (PathBuf, Arc<LearningManager>) {
    let root = std::env::temp_dir().join(format!("opencore-learning-{}", uuid::Uuid::new_v4()));
    let manager = LearningManager::new(root.clone(), root.join("resources")).unwrap();
    (root, manager)
}

#[test]
fn restart_preserves_runs_and_marks_unfinished_training_interrupted() {
    let (root, manager) = fixture();
    let run = json!({"id":"run", "status":"running", "createdAt":now(), "config":{"precision":"bf16-lora"}, "receipt":{"raw":"keep me"}});
    manager.save(&run).unwrap();
    drop(manager);
    let restored = LearningManager::new(root.clone(), root.join("resources")).unwrap();
    let run = restored.run("run").unwrap();
    assert_eq!(run["status"], "interrupted");
    assert_eq!(run["config"]["precision"], "bf16-lora");
    assert_eq!(run["receipt"]["raw"], "keep me");
    assert!(run["error"].as_str().unwrap().contains("interrupted"));
    drop(restored);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn a_checkpoint_never_looks_completed_and_an_auto_run_requires_context() {
    assert_eq!(
        next_status("checkpoint-ready", "auto", true).unwrap(),
        "awaiting-review"
    );
    assert_eq!(
        next_status("checkpoint-ready", "manual", false).unwrap(),
        "queued"
    );
    assert!(next_status("checkpoint-ready", "auto", false).is_err());
    assert_eq!(next_status("rejected", "auto", true).unwrap(), "rejected");
    assert!(next_status("completed", "auto", true).is_err());
}

#[test]
fn rejected_receipt_keeps_all_exact_gate_evidence() {
    let (root, manager) = fixture();
    let receipt = json!({"status":"rejected","baseline":{"loss":1.1},"candidate":{"loss":1.2},"gates":[{"metric":"loss","observed":-0.1,"minimum":0.01,"passed":false}],"reasons":["held-out loss increased from 1.1 to 1.2"]});
    let run = json!({"id":"rejected", "status":"rejected", "createdAt":now(), "receipt":receipt});
    manager.save(&run).unwrap();
    assert_eq!(manager.run("rejected").unwrap()["receipt"], receipt);
    drop(manager);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn invalid_config_cannot_disable_training_limits_or_change_precision_silently() {
    assert!(validate_config(
        &json!({"precision":"bf16-lora","maxSteps":10,"maxMinutes":5,"maxDiskBytes":1073741824})
    )
    .is_ok());
    assert!(validate_config(
        &json!({"precision":"fp32","maxSteps":10,"maxMinutes":5,"maxDiskBytes":1073741824})
    )
    .is_err());
    assert!(validate_config(
        &json!({"precision":"bf16-lora","maxSteps":0,"maxMinutes":5,"maxDiskBytes":1073741824})
    )
    .is_err());
    assert!(validate_config(
        &json!({"precision":"bf16-lora","maxSteps":10,"maxMinutes":0,"maxDiskBytes":1073741824})
    )
    .is_err());
}

#[test]
fn agent_cannot_continue_or_read_a_run_from_another_chat() {
    let run = json!({"id":"private-run","conversationId":"owner"});
    assert!(check_scope(&run, Some("owner")).is_ok());
    assert!(check_scope(&run, Some("stranger")).is_err());
    assert!(check_scope(&run, None).is_ok());
}

#[test]
fn raw_log_reads_expose_continuation_and_do_not_claim_a_preview_is_complete() {
    let (root, manager) = fixture();
    let path = root.join("events.jsonl");
    std::fs::write(&path, b"first\nsecond\nthird\n").unwrap();
    let first = read_range(&path, 0, 6).unwrap();
    assert_eq!(first["content"], "first\n");
    assert_eq!(first["nextOffset"], 6);
    assert_eq!(first["complete"], false);
    let rest = read_range(&path, 6, 100).unwrap();
    assert_eq!(rest["content"], "second\nthird\n");
    assert_eq!(rest["complete"], true);
    drop(manager);
    std::fs::remove_dir_all(root).unwrap();
}

fn durable_checkpoint(manager: &LearningManager, id: &str, step: u64) -> (Value, Value) {
    let folder = manager.root.join("runs").join(id);
    fs::create_dir_all(&folder).unwrap();
    let folder = folder.canonicalize().unwrap();
    let inputs = manager.root.join(format!("inputs-{id}"));
    fs::create_dir_all(inputs.join("model")).unwrap();
    fs::write(inputs.join("model/config.json"), b"{}").unwrap();
    for name in ["manifest.json", "train.jsonl", "validation.jsonl"] {
        fs::write(inputs.join(name), format!("{id}:{name}\n")).unwrap();
    }
    let config =
        json!({"precision":"bf16-lora","maxSteps":10,"maxMinutes":5,"maxDiskBytes":1073741824});
    let request = json!({"runId":id,"outputDir":folder,"modelPath":inputs.join("model").canonicalize().unwrap(),"datasetManifest":inputs.join("manifest.json").canonicalize().unwrap(),"trainPath":inputs.join("train.jsonl").canonicalize().unwrap(),"validationPath":inputs.join("validation.jsonl").canonicalize().unwrap(),"config":config});
    let identity = "a".repeat(64);
    let manifest = json!({"schemaVersion":1,"runId":id,"outputDir":folder,"identitySha256":identity,"config":config,"model":{"path":request["modelPath"]},"datasetManifest":{"path":request["datasetManifest"],"sha256":hash_file(Path::new(request["datasetManifest"].as_str().unwrap())).unwrap()},"datasets":{"train":{"path":request["trainPath"],"sha256":hash_file(Path::new(request["trainPath"].as_str().unwrap())).unwrap()},"validation":{"path":request["validationPath"],"sha256":hash_file(Path::new(request["validationPath"].as_str().unwrap())).unwrap()}}});
    save_json(&folder.join("run-manifest.json"), &manifest).unwrap();
    let checkpoint = folder.join(format!("checkpoint-{step}"));
    fs::create_dir_all(&checkpoint).unwrap();
    save_json(
        &checkpoint.join("trainer_state.json"),
        &json!({"global_step":step}),
    )
    .unwrap();
    for name in [
        "optimizer.pt",
        "scheduler.pt",
        "rng_state.pth",
        "adapter_model.safetensors",
    ] {
        fs::write(
            checkpoint.join(name),
            format!("real test state {name} at step {step}"),
        )
        .unwrap();
    }
    let mut files = Vec::new();
    for entry in fs::read_dir(&checkpoint).unwrap() {
        let path = entry.unwrap().path();
        files.push(json!({"path":path.file_name().unwrap().to_str().unwrap(),"bytes":fs::metadata(&path).unwrap().len(),"sha256":hash_file(&path).unwrap()}));
    }
    let seal = json!({"path":checkpoint,"step":step,"identitySha256":identity,"trainerStateSha256":hash_file(&checkpoint.join("trainer_state.json")).unwrap(),"files":files,"savedAt":now()});
    save_json(&checkpoint.join("learning-checkpoint.json"), &seal).unwrap();
    let run = json!({"id":id,"status":"running","mode":"auto","reviewTaskId":"review-task","conversationId":"owner","createdAt":now(),"updatedAt":now(),"config":config,"workerRequest":request,"identitySha256":identity,"receipt":{"runId":id,"lastStep":0}});
    (run, seal)
}

#[test]
fn restart_recovers_a_sealed_checkpoint_newer_than_the_database_and_disk_receipt() {
    let (root, manager) = fixture();
    let (run, seal) = durable_checkpoint(&manager, "recover", 4);
    manager.save(&run).unwrap();
    save_json(&manager.root.join("runs/recover/receipt.json"), &json!({"runId":"recover","status":"training","lastStep":0,"identitySha256":run["identitySha256"],"activeSeconds":12.25})).unwrap();
    drop(manager);
    let restored = LearningManager::new(root.clone(), root.join("resources")).unwrap();
    let recovered = restored.run("recover").unwrap();
    assert_eq!(recovered["status"], "interrupted");
    assert_eq!(recovered["receipt"]["lastStep"], 4);
    assert_eq!(recovered["receipt"]["checkpoint"], seal["path"]);
    assert_eq!(recovered["receipt"]["activeSeconds"], 12.25);
    assert_eq!(recovered["receipt"]["checkpointSeal"], seal);
    assert!(recovered["recoveryError"].is_null());
    drop(restored);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn recovery_rejects_foreign_paths_changed_state_and_unbound_identity() {
    let (root, manager) = fixture();
    let (mut run, seal) = durable_checkpoint(&manager, "integrity", 3);
    let receipt_path = manager.root.join("runs/integrity/receipt.json");
    save_json(&receipt_path, &json!({"runId":"integrity","status":"checkpoint-ready","lastStep":3,"checkpoint":root.join("foreign"),"identitySha256":run["identitySha256"]})).unwrap();
    assert!(manager
        .reconcile_run(&mut run)
        .unwrap_err()
        .contains("checkpoint"));
    assert_eq!(run["receipt"]["lastStep"], 0);
    save_json(&receipt_path, &json!({"runId":"integrity","status":"checkpoint-ready","lastStep":3,"checkpoint":seal["path"],"identitySha256":"b".repeat(64)})).unwrap();
    assert!(manager
        .reconcile_run(&mut run)
        .unwrap_err()
        .contains("identity"));
    save_json(&receipt_path, &json!({"runId":"integrity","status":"checkpoint-ready","lastStep":3,"checkpoint":seal["path"],"identitySha256":run["identitySha256"]})).unwrap();
    fs::write(
        Path::new(seal["path"].as_str().unwrap()).join("optimizer.pt"),
        b"changed",
    )
    .unwrap();
    assert!(manager
        .reconcile_run(&mut run)
        .unwrap_err()
        .contains("hash"));
    assert_eq!(run["receipt"]["lastStep"], 0);
    drop(manager);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn checkpoint_review_outbox_survives_restart_and_only_its_bound_event_can_resume() {
    let (root, manager) = fixture();
    let (mut run, seal) = durable_checkpoint(&manager, "review", 2);
    let receipt = json!({"runId":"review","status":"checkpoint-ready","lastStep":2,"checkpoint":seal["path"],"checkpointSeal":seal,"identitySha256":run["identitySha256"],"invocationId":"one"});
    save_json(&manager.root.join("runs/review/receipt.json"), &receipt).unwrap();
    run["receipt"] = receipt;
    run["status"] = json!("awaiting-review");
    manager.stage_event(&mut run).unwrap();
    let event = run["pendingEvent"].clone();
    manager.save(&run).unwrap();
    drop(manager);
    let manager = LearningManager::new(root.clone(), root.join("resources")).unwrap();
    assert_eq!(manager.run("review").unwrap()["pendingEvent"], event);
    let mut stale = event.clone();
    stale["id"] = json!("old-event");
    manager
        .review_finished(&json!({"event":stale}), Ok(()))
        .unwrap();
    assert_eq!(manager.run("review").unwrap()["status"], "awaiting-review");
    let mut wrong_checkpoint = event.clone();
    wrong_checkpoint["step"] = json!(1);
    manager
        .review_finished(&json!({"event":wrong_checkpoint}), Ok(()))
        .unwrap();
    assert_eq!(manager.run("review").unwrap()["status"], "awaiting-review");
    let mut pending = manager.run("review").unwrap();
    assert!(manager
        .request_continue(&mut pending)
        .unwrap_err()
        .contains("review"));
    manager
        .review_finished(&json!({"event":event}), Ok(()))
        .unwrap();
    let resumed = manager.run("review").unwrap();
    assert_eq!(resumed["status"], "queued");
    assert_eq!(resumed["reviewPending"], false);
    assert!(resumed["pendingEvent"].is_null());
    drop(manager);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn only_explicit_continue_clears_the_owned_cancellation_marker() {
    let (root, manager) = fixture();
    let (mut run, seal) = durable_checkpoint(&manager, "continue", 3);
    run["status"] = json!("paused");
    save_json(&manager.root.join("runs/continue/receipt.json"), &json!({"runId":"continue","status":"cancelled","lastStep":3,"checkpoint":seal["path"],"identitySha256":run["identitySha256"]})).unwrap();
    manager.write_cancel_marker("continue").unwrap();
    manager.reconcile_run(&mut run).unwrap();
    assert!(manager
        .root
        .join("runs/continue/cancel.requested")
        .is_file());
    manager.request_continue(&mut run).unwrap();
    assert!(!manager.root.join("runs/continue/cancel.requested").exists());
    assert_eq!(run["status"], "queued");
    assert_eq!(run["receipt"]["lastStep"], 3);
    assert!(manager.write_cancel_marker("../foreign").is_err());
    drop(manager);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn hard_deadline_subtracts_all_previous_worker_active_time() {
    assert_eq!(
        remaining_training_time(
            &json!({"config":{"maxMinutes":2},"receipt":{"activeSeconds":89.5}})
        )
        .unwrap(),
        Duration::from_millis(30_500)
    );
    assert!(remaining_training_time(
        &json!({"config":{"maxMinutes":2},"receipt":{"activeSeconds":121}})
    )
    .is_err());
    assert!(remaining_training_time(
        &json!({"config":{"maxMinutes":2},"receipt":{"activeSeconds":-1}})
    )
    .is_err());
}

#[test]
fn tool_schema_matches_opaque_cursor_and_explicit_scope_contract() {
    let spec = tool_spec();
    let properties = &spec["function"]["parameters"]["properties"];
    assert_eq!(properties["cursor"]["type"], "string");
    assert_eq!(properties["countOnly"]["type"], "boolean");
    assert!(properties["scope"]["enum"]
        .as_array()
        .unwrap()
        .contains(&json!("global")));
    for action in ["plan", "recommend", "setup"] {
        assert!(properties["action"]["enum"]
            .as_array()
            .unwrap()
            .contains(&json!(action)));
    }
}

fn waiting_process(folder: &Path) -> crate::scheduler_worker::WorkerConfig {
    #[cfg(windows)]
    let (command, args) = (
        "cmd.exe",
        vec![
            "/D".into(),
            "/C".into(),
            "echo owned-learning-helper & ping -n 120 127.0.0.1 >nul".into(),
        ],
    );
    #[cfg(not(windows))]
    let (command, args) = (
        "sh",
        vec![
            "-c".into(),
            "printf owned-learning-helper; sleep 120".into(),
        ],
    );
    crate::scheduler_worker::WorkerConfig {
        command: command.into(),
        args,
        cwd: folder.to_path_buf(),
        uses_gpu: false,
        long_running: false,
        wait_policy: "when-idle".into(),
    }
}

#[tokio::test]
async fn owned_helper_deadline_stops_the_process_tree_and_preserves_raw_logs() {
    let (root, manager) = fixture();
    let logs = root.join("deadline-process-logs");
    let result = manager
        .owned_worker(
            waiting_process(&root),
            CancellationToken::new(),
            logs.clone(),
            Duration::from_millis(1500),
            None,
            Arc::new(|_| Ok(())),
            None,
        )
        .await
        .unwrap();
    assert!(result.deadline_exceeded);
    assert!(result.worker.cancelled);
    assert!(result.elapsed < Duration::from_secs(10));
    assert!(!manager.busy());
    assert!(logs.join("stdout.log").is_file());
    assert!(logs.join("stderr.log").is_file());
    drop(manager);
    fs::remove_dir_all(root).unwrap();
}

#[tokio::test]
async fn update_cancellation_owns_probe_and_planning_helpers_until_they_exit() {
    let (root, manager) = fixture();
    let (send, receive) = tokio::sync::oneshot::channel();
    let send = Arc::new(Mutex::new(Some(send)));
    let started = Arc::new(move |_| {
        if let Some(send) = send.lock().unwrap().take() {
            let _ = send.send(());
        }
        Ok(())
    });
    let helper = manager.clone();
    let worker = waiting_process(&root);
    let logs = root.join("cancelled-probe-logs");
    let task = tokio::spawn(async move {
        helper
            .owned_worker(
                worker,
                CancellationToken::new(),
                logs,
                Duration::from_secs(120),
                None,
                started,
                None,
            )
            .await
    });
    receive.await.unwrap();
    assert!(manager.busy());
    manager.cancel_active().await.unwrap();
    let result = task.await.unwrap().unwrap();
    assert!(result.stop_requested);
    assert!(result.worker.cancelled);
    assert!(!manager.busy());
    drop(manager);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn missing_logs_are_explicit_and_byte_pages_can_reassemble_split_unicode() {
    let (root, manager) = fixture();
    let path = root.join("not-started.log");
    let missing = read_range(&path, 0, 100).unwrap();
    assert_eq!(missing["available"], false);
    assert_eq!(missing["rawFilePreserved"], false);
    let original = "שלום 😀\n".as_bytes();
    fs::write(&path, original).unwrap();
    let mut offset = 0;
    let mut exact = Vec::new();
    while offset < original.len() as u64 {
        let page = read_range(&path, offset, 3).unwrap();
        exact.extend(
            base64::engine::general_purpose::STANDARD
                .decode(page["bytesBase64"].as_str().unwrap())
                .unwrap(),
        );
        offset = page["nextOffset"].as_u64().unwrap();
    }
    assert_eq!(exact, original);
    drop(manager);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn durable_scheduler_review_outcomes_recover_the_callback_crash_gap() {
    let (root, manager) = fixture();
    for (id, status, expected) in [
        ("review-completed", "completed", "queued"),
        ("review-interrupted", "interrupted", "paused"),
        ("review-queued", "queued", "awaiting-review"),
    ] {
        let (mut run, seal) = durable_checkpoint(&manager, id, 2);
        run["status"] = json!("awaiting-review");
        run["receipt"] = json!({"runId":id,"status":"checkpoint-ready","lastStep":2,"checkpoint":seal["path"],"identitySha256":run["identitySha256"]});
        save_json(
            &manager.root.join("runs").join(id).join("receipt.json"),
            &run["receipt"],
        )
        .unwrap();
        manager.stage_event(&mut run).unwrap();
        manager.save(&run).unwrap();
        let event = run["reviewEvent"].clone();
        let snapshot = json!({"tasks":[{"id":"review-task","paused":false}],"runs":[{"id":format!("scheduler-{id}"),"taskId":"review-task","occurrence":format!("event:{}",event["id"].as_str().unwrap()),"status":status,"error":"Original interruption evidence","evidence":{"event":event}}]});
        manager.recover_review_outcomes(&[run], &snapshot).unwrap();
        let mut recovered = manager.run(id).unwrap();
        assert_eq!(recovered["status"], expected);
        if status == "interrupted" {
            assert_eq!(recovered["reviewPending"], false);
            assert!(recovered["reviewError"]
                .as_str()
                .unwrap()
                .contains("Original interruption evidence"));
            manager.request_continue(&mut recovered).unwrap();
            assert_eq!(recovered["status"], "queued");
        }
    }
    let (mut missing, seal) = durable_checkpoint(&manager, "review-deleted", 2);
    missing["status"] = json!("awaiting-review");
    missing["receipt"] =
        json!({"lastStep":2,"checkpoint":seal["path"],"identitySha256":missing["identitySha256"]});
    manager.stage_event(&mut missing).unwrap();
    manager.save(&missing).unwrap();
    manager
        .recover_review_outcomes(&[missing], &json!({"tasks":[],"runs":[]}))
        .unwrap();
    assert_eq!(manager.run("review-deleted").unwrap()["status"], "paused");
    drop(manager);
    fs::remove_dir_all(root).unwrap();
}

#[tokio::test]
async fn preflight_and_loader_share_one_cumulative_owned_workflow_deadline() {
    let (root, manager) = fixture();
    let (mut run, _) = durable_checkpoint(&manager, "workflow-budget", 1);
    run["config"]["maxMinutes"] = json!(0.05);
    manager.save(&run).unwrap();
    let workflow = manager.begin_workflow("workflow-budget").unwrap();
    let token = CancellationToken::new();
    let first = manager
        .owned_worker(
            waiting_process(&root),
            token.clone(),
            root.join("preflight-logs"),
            Duration::from_millis(1000),
            Some("workflow-budget".into()),
            Arc::new(|_| Ok(())),
            None,
        )
        .await
        .unwrap();
    assert!(first.deadline_exceeded);
    assert!(!token.is_cancelled());
    let second = manager
        .owned_worker(
            waiting_process(&root),
            token.clone(),
            root.join("loader-logs"),
            Duration::from_secs(120),
            Some("workflow-budget".into()),
            Arc::new(|_| Ok(())),
            None,
        )
        .await
        .unwrap();
    assert!(second.deadline_exceeded);
    assert!(token.is_cancelled());
    assert_eq!(
        manager.run("workflow-budget").unwrap()["nativeDeadlineExceeded"],
        true
    );
    assert!(remaining_training_time(&manager.run("workflow-budget").unwrap()).is_err());
    drop(workflow);
    drop(manager);
    fs::remove_dir_all(root).unwrap();
}
