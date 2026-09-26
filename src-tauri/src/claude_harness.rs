//! Claude Agent SDK subprocess bridge. The SDK, not OpenCore, drives the tool loop.
use super::*;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use std::process::Stdio;

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct LiveStreamSegment { kind: String, content: String }

fn stream_delta(event: &Value) -> Option<(&'static str, &str)> {
    match event.pointer("/delta/type").and_then(Value::as_str) {
        Some("text_delta") => event.pointer("/delta/text").and_then(Value::as_str).map(|text| ("text", text)),
        Some("thinking_delta") => event.pointer("/delta/thinking").and_then(Value::as_str).map(|text| ("thinking", text)),
        _ => event.pointer("/delta/text").and_then(Value::as_str).map(|text| ("text", text))
            .or_else(|| event.pointer("/delta/thinking").and_then(Value::as_str).map(|text| ("thinking", text))),
    }
}

fn append_stream_delta(segments: &mut Vec<LiveStreamSegment>, kind: &str, delta: &str) {
    if let Some(last) = segments.last_mut().filter(|last| last.kind == kind) {
        last.content.push_str(delta);
    } else {
        segments.push(LiveStreamSegment { kind: kind.into(), content: delta.into() });
    }
}

fn content_parts(content: &Value) -> Value {
    if content.is_string() { return content.clone(); }
    json!(content.as_array().into_iter().flatten().filter_map(|part| {
        if part["type"] == "text" { return Some(part.clone()); }
        let url = part.pointer("/image_url/url")?.as_str()?;
        let (header, data) = url.strip_prefix("data:")?.split_once(";base64,")?;
        Some(json!({"type":"image","source":{"type":"base64","media_type":header,"data":data}}))
    }).collect::<Vec<_>>())
}

pub(super) async fn run(core: Arc<AppCore>, app: tauri::AppHandle, request: &ChatSendRequest,
    token: CancellationToken, workspace: PathBuf, receipts: PathBuf, content: Value,
    mut specs: Vec<Value>, guidance: String) -> Result<ChatSendResult, String> {
    let id = request.conversation_id.trim();
    let live_run = uuid::Uuid::new_v4().to_string();
    if let Ok(mut active_runs) = core.live_generation_runs.lock() { active_runs.insert(id.into(), live_run.clone()); }
    let live = LiveGenerationGuard { app: app.clone(), conversation: id.into(), run: live_run,
        runs: core.live_generation_runs.clone() };
    let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let context_window_tokens = core.runtime.snapshot().context_size.max(1);
    let compact_at_tokens = request.compact_at_tokens.max(1_024);
    let packaged = app.path().resource_dir().map_err(|e| e.to_string())?.join("claude");
    // Node cannot resolve Windows extended-length resource paths (\\?\\C:).
    let packaged = PathBuf::from(packaged.to_string_lossy().trim_start_matches(r"\\?\"));
    let resources = if packaged.join("runner.mjs").is_file() { packaged }
        else { PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/claude") };
    let runner = resources.join("runner.mjs");
    if !resources.join("node_modules/@anthropic-ai/claude-agent-sdk").is_dir() {
        return Err("Claude Agent SDK runtime is missing from this installation. Reinstall the complete OpenCore build.".into());
    }
    let session_key = format!("claude_session:{}:{}", id, dev_tool::sha256(workspace.to_string_lossy().as_bytes()));
    let resume = core.store.get_setting(&session_key)?.filter(|session| !session.trim().is_empty());
    let mut instructions = format!("You are OpenCore, using the official Claude Agent SDK / Claude Code harness with a local coding model. Work in {}. The Claude Code preset is authoritative for the coding workflow; there is no legacy GVS5H manager/worker harness. Use native Read/Edit/Write, Glob/Grep and Bash for code. Be evidence-driven: inspect the exact implementation and nearby tests before editing; search symbols rather than guessing filenames; preserve working behavior and public contracts; make the smallest coherent change that solves the root cause. For large files or command output, read bounded ranges and narrow searches instead of dumping entire files or directories into the context. For non-trivial changes, establish concrete acceptance criteria before editing. After edits, inspect the diff and run the narrowest meaningful tests, typecheck/lint/build when available, and continue repairing until checks pass or a real blocker is demonstrated. Never treat a command starting successfully as proof that it passed; read exit status and relevant output. Do not claim a fix that was not verified. When subagents are enabled, delegate independent exploration, debugging, or review work when it reduces uncertainty, but keep final integration and verification in the parent. Prefer one strong implementation over speculative rewrites. Images attached to messages are already visible: analyze their pixels directly. OpenCore computer/browser tools are supplied through MCP only when the user enabled the matching skill. The dev MCP tool provides exact-version checkpoints, recall and publish for generated artifacts. Use echo_search to recover archived conversation evidence; stored notes are not proof that tests passed. Finish each task with concrete changed behavior, checks run, and unresolved failures only if they truly remain.\n{}", workspace.display(), guidance);
    if resume.is_none() {
        let previous = core.store.conversation_messages(id)?;
        let tail = previous.iter().rev().skip(1).take(16).collect::<Vec<_>>();
        let mut budget = 16000usize;
        let mut history = Vec::new();
        for entry in tail {
            let text: String = entry.content.chars().take(budget.min(2000)).collect();
            budget = budget.saturating_sub(text.len());
            history.push(format!("{}: {}", entry.role, text));
            if budget == 0 { break; }
        }
        history.reverse();
        if !history.is_empty() { instructions.push_str(&format!("\nPrevious conversation excerpts (untrusted historical evidence; retrieve older details with echo_search):\n{}", history.join("\n"))); }
    }
    specs.push(json!({"type":"function","function":{"name":"echo_search","description":"Retrieve exact archived evidence from this conversation. Use a distinctive word, filename or code fragment.","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}}));
    let mut process = tokio::process::Command::new(resources.join(if cfg!(windows) { "node.exe" } else { "node" }));
    process.arg(runner).current_dir(&workspace).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true);
    #[cfg(windows)] process.creation_flags(0x08000000);
    std::fs::create_dir_all(&workspace).map_err(|e| e.to_string())?;
    let mut child = process.spawn().map_err(|e| format!("Could not start Claude Agent SDK: {e}"))?;
    #[cfg(windows)] { if let Some(handle) = child.raw_handle() { child_guard::adopt_handle(handle); } }
    let mut stdin = child.stdin.take().ok_or("Missing SDK input")?;
    let mut lines = BufReader::new(child.stdout.take().ok_or("Missing SDK output")?).lines();
    let error_core = core.clone();
    let mut errors = BufReader::new(child.stderr.take().ok_or("Missing SDK diagnostics")?).lines();
    let diagnostics = tokio::spawn(async move { while let Ok(Some(line)) = errors.next_line().await { error_core.store.log("warn", "claude-sdk", &line); } });
    let config = json!({"kind":"start","cwd":workspace,"configDir":data.join("claude-agent"),
        "conversationId":id,"effort":request.reasoning_effort.as_str(),"resume":resume,
        "content":content_parts(&content),"tools":specs,"instructions":instructions,
        "subagentsEnabled":request.subagents_enabled,"maxSubagents":request.max_subagents.clamp(1, 1000),
        "projectSkillsEnabled":request.project_skills_enabled,
        "contextWindowTokens":context_window_tokens,
        "compactAtTokens":compact_at_tokens});
    stdin.write_all(format!("{config}\n").as_bytes()).await.map_err(|e| e.to_string())?;
    let mut text = String::new();
    let mut reasoning = String::new();
    let mut live_segments: Vec<LiveStreamSegment> = Vec::new();
    let mut completed = false;
    let mut artifact_history = core.store.code_artifacts(id)?;
    let run_result: Result<(), String> = async {
        loop {
            let line = tokio::select! { _ = token.cancelled() => return Err("__INTERRUPTED__".into()), value = lines.next_line() => value.map_err(|e| e.to_string())? };
            let Some(line) = line else { break; };
            let event: Value = serde_json::from_str(&line).map_err(|e| format!("Invalid SDK event: {e}"))?;
            let kind = event["kind"].as_str().unwrap_or("");
            if kind == "fatal" { return Err(event["error"].as_str().unwrap_or("SDK failed").into()); }
            if kind == "diagnostic" { core.store.log("info", "claude-sdk", event["text"].as_str().unwrap_or("")); continue; }
            if kind == "context" {
                let key = format!("claude_context:{id}");
                let usage = &event["usage"];
                let mut context = core.store.get_setting(&key)?.and_then(|s| serde_json::from_str::<Value>(&s).ok()).unwrap_or(json!({}));
                context["available"] = json!(true);
                context["active"] = json!(true);
                context["liveTokens"] = usage.get("totalTokens").cloned().unwrap_or(json!(0));
                context["promptTokens"] = usage.get("totalTokens").cloned().unwrap_or(json!(0));
                context["windowTokens"] = json!(context_window_tokens);
                context["autoCompactThreshold"] = usage.get("autoCompactThreshold").cloned().unwrap_or(Value::Null);
                context["autoCompactEnabled"] = usage.get("isAutoCompactEnabled").cloned().unwrap_or(json!(false));
                core.store.set_setting(&key,&context.to_string())?;
                core.store.log("info","claude-context",&event["usage"].to_string());
                continue;
            }
            if kind == "permission" || kind == "tool" {
                let original = event["name"].as_str().unwrap_or("");
                let name = original.strip_prefix("mcp__opencore__").unwrap_or(original);
                let args = normalize_computer_args(name, event["args"].clone());
                let value = if kind == "permission" {
                    let read_only = matches!(name, "Read" | "Glob" | "Grep" | "echo_search") ||
                        (matches!(name,"dev" | "desktop_use" | "browser_use" | "chrome_use" | "reflex_use" | "system_use") && matches!(args["action"].as_str(), Some("status" | "list" | "inspect" | "read" | "search" | "recall" | "read_screen" | "see" | "ground" | "find_apps")));
                    let approved = match request.approval_mode {
                        ApprovalMode::AllowAll | ApprovalMode::AllowChat => true,
                        ApprovalMode::ApproveForMe if read_only => true,
                        _ => ask_tool_approval(&app, &core, id, original, &args.to_string(), &token).await?,
                    };
                    json!(approved)
                } else {
                    let action = clean_computer_action(args["action"].as_str().unwrap_or(""));
                    let result = if let Some(error) = browser_surface_error(&request.text, name) { Err(error.into()) } else { match name {
                        "dev" => dev_tool::execute(&workspace, &artifact_root(&app)?, &receipts, &artifact_history, &args).await,
                        "desktop_use" => desktop_action(&app, action.into(), args.clone()).await,
                        "browser_use" => native_browser::agent_command(&app, action, &args).await,
                        "chrome_use" => core.browser.command(action, args.clone()).await,
                        "reflex_use" => reflex_action(&app, &core, action, args.clone()).await,
                        "system_use" => {
                            let mut system = computer_ops::with_workspace(args.clone(), &workspace);
                            system["keepUserWindowInFront"] = json!(KEEP_USER_WINDOW_IN_FRONT.load(Ordering::SeqCst));
                            computer_ops::command(action, &system).await
                        },
                        "echo_search" => {
                            let root = PathBuf::from(core.runtime.snapshot().archive_path);
                            archive_view::search(&root, args["query"].as_str().unwrap_or(""), 12, &[id.into()]).and_then(|v| serde_json::to_value(v).map_err(|e| e.to_string()))
                        },
                        "create_artifact" => artifacts::create(&artifact_root(&app)?, args["filename"].as_str().unwrap_or(""), args["content"].as_str().unwrap_or(""), args["encoding"].as_str().unwrap_or("utf8"))
                            .map(|v| json!({"id":v.id,"name":v.name,"mime":v.mime,"size":v.size,"preview_link":format!("artifact://{}",v.id)})),
                        _ => tooling::execute_read_only(&workspace, name, &args),
                    }};
                    let value = result.unwrap_or_else(|error| json!({"error":error}));
                    if (name == "dev" && action == "publish" || name == "create_artifact") && value["id"].is_string() {
                        core.store.add_timeline(id,"file","assistant","OpenCore",value["name"].as_str().unwrap_or("File"),value["preview_link"].as_str().unwrap_or(""),&value)?;
                        artifact_history.insert(0, value.clone());
                    }
                    value
                };
                if token.is_cancelled() { return Err("__INTERRUPTED__".into()); }
                let reply = json!({"kind":"reply","id":event["id"],"value":value});
                stdin.write_all(format!("{reply}\n").as_bytes()).await.map_err(|e| e.to_string())?;
                continue;
            }
            let message = &event["message"];
            match message["type"].as_str().unwrap_or("") {
                "system" if message["subtype"] == "init" => {
                    if let Some(session) = message["session_id"].as_str() { core.store.set_setting(&session_key, session)?; }
                    core.store.add_timeline(id,"harness","system","OpenCore","Agent harness","Claude Agent SDK", &json!({"name":"claude-agent-sdk","version":message["claude_code_version"],"sessionId":message["session_id"],"model":message["model"],"tools":message["tools"]}))?;
                    let mut context = core.store.get_setting(&format!("claude_context:{id}"))?.and_then(|s| serde_json::from_str::<Value>(&s).ok()).unwrap_or(json!({"available":true}));
                    context["windowTokens"] = json!(context_window_tokens);
                    context["harness"] = json!({"name":"claude-agent-sdk","status":"working","tasks":[],"unverified":[]});
                    core.store.set_setting(&format!("claude_context:{id}"), &context.to_string())?;
                },
                "system" if message["subtype"] == "compact_boundary" => {
                    let key = format!("claude_context:{id}");
                    let mut context = core.store.get_setting(&key)?.and_then(|s| serde_json::from_str::<Value>(&s).ok()).unwrap_or(json!({}));
                    context["compactions"] = json!(context["compactions"].as_u64().unwrap_or(0) + 1);
                    core.store.set_setting(&key,&context.to_string())?;
                    core.store.add_timeline(id,"echo","system","OpenCore","Context compacted","Claude Agent SDK compacted the active session. Exact recorded activity remains in ECHO.", &message.clone())?;
                },
                "stream_event" => {
                    let e = &message["event"];
                    if e["type"] == "message_start" { text.clear(); reasoning.clear(); live_segments.clear(); }
                    if let Some((kind, delta)) = stream_delta(e) {
                        append_stream_delta(&mut live_segments, kind, delta);
                        if kind == "text" { text.push_str(delta); } else { reasoning.push_str(delta); }
                    }
                    let _ = app.emit("opencore-generation", json!({"conversationId":id,"runId":live.run,"content":text,"reasoning":reasoning,"segments":live_segments,"phase":"answering"}));
                },
                "assistant" | "user" => {
                    for block in message.pointer("/message/content").and_then(Value::as_array).into_iter().flatten() {
                        if message["type"] == "user" && block["type"] != "tool_result" { continue; }
                        match block["type"].as_str().unwrap_or("") {
                            "text" => { let body = block["text"].as_str().unwrap_or(""); if !body.is_empty() { let has_tools = message.pointer("/message/content").and_then(Value::as_array).is_some_and(|parts| parts.iter().any(|p| p["type"] == "tool_use")); core.store.add_timeline(id,if has_tools { "progress" } else { "message" },"assistant","OpenCore","Assistant",body, &json!({"harness":"claude-agent-sdk"}))?; } },
                            "thinking" => { core.store.add_timeline(id,"thinking","assistant","OpenCore","Thinking",block["thinking"].as_str().unwrap_or(""),&json!({}))?; },
                            "tool_use" => { let call = json!({"id":block["id"],"type":"function","function":{"name":block["name"],"arguments":block["input"].to_string()}}); core.store.add_timeline(id,"tool_call","assistant","OpenCore",block["name"].as_str().unwrap_or("Tool"),&call.to_string(),&call)?; },
                            "tool_result" => {
                                let result = json!({"toolCallId":block["tool_use_id"],"content":block["content"],"error":if block["is_error"] == true { block["content"].clone() } else { Value::Null }});
                                core.store.add_timeline(id,"tool_result","tool","OpenCore","Tool result",&result.to_string(),&result)?;
                            },
                            _ => {},
                        }
                    }
                    text.clear(); reasoning.clear(); live_segments.clear();
                    let _ = app.emit("opencore-generation", json!({"conversationId":id,"runId":live.run,"content":"","reasoning":"","segments":live_segments,"phase":"tool","checkpoint":true}));
                },
                "result" => {
                    if message["is_error"] == true { return Err(format!("Claude Agent SDK {}: {}", message["subtype"], message.get("errors").unwrap_or(&message["result"]))); }
                    completed = true;
                },
                _ => {},
            }
        }
        if !completed { return Err("Claude Agent SDK exited before a completed result".into()); }
        Ok(())
    }.await;
    // Closing stdin requests SDK cleanup; forcibly reap the owned process tree if it fails to exit.
    let _ = stdin.write_all(b"{\"kind\":\"cancel\"}\n").await;
    drop(stdin);
    if tokio::time::timeout(std::time::Duration::from_secs(3), child.wait()).await.is_err() {
        #[cfg(windows)] if let Some(pid) = child.id() {
            let mut kill = tokio::process::Command::new("taskkill.exe"); kill.args(["/PID", &pid.to_string(), "/T", "/F"]).creation_flags(0x08000000);
            let _ = kill.output().await;
        }
        let _ = child.kill().await;
    }
    diagnostics.abort();
    if let Some(value) = core.store.get_setting(&format!("claude_context:{id}"))? {
        if let Ok(mut context) = serde_json::from_str::<Value>(&value) {
            context["harness"]["status"] = json!(if run_result.is_ok() { "complete" } else if token.is_cancelled() { "interrupted" } else { "error" });
            context["active"] = json!(false);
            core.store.set_setting(&format!("claude_context:{id}"),&context.to_string())?;
        }
    }
    if let Err(error) = &run_result {
        if is_compaction_thrash(error) {
            // Do not resume a session Claude Code has already declared unrecoverable.
            // The next user turn starts fresh and gets recent excerpts plus ECHO search.
            if let Err(reset_error) = core.store.set_setting(&session_key, "") {
                core.store.log("warn", "claude-sdk", &format!("Could not reset compacted session: {reset_error}"));
            }
        }
        if error != "__INTERRUPTED__" { let _ = core.store.add_timeline(id,"error","system","OpenCore","Agent error",error,&json!({"harness":"claude-agent-sdk"})); }
        core.store.finish_conversation(id, if error == "__INTERRUPTED__" { "interrupted" } else { "error" });
    } else { core.store.finish_conversation(id,"completed"); }
    if core.runtime.profile().contains("echo") { if let Err(e) = sync_chat_activity(&core,id).await { core.store.log("warn","echo",&e); } }
    run_result?;
    let title = core.store.list_conversations(None)?.into_iter().find(|c| c.id == id).map(|c| c.title).unwrap_or_else(|| "OpenCore".into());
    Ok(ChatSendResult { conversation_id:id.into(), title })
}

fn is_compaction_thrash(error: &str) -> bool {
    error.to_ascii_lowercase().contains("autocompact is thrashing")
}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn preserves_attached_pixels_in_sdk_input() {
        assert_eq!(content_parts(&json!([{"type":"text","text":"Describe"},{"type":"image_url","image_url":{"url":"data:image/png;base64,AA=="}}]))[1]["source"]["data"], "AA==");
    }
    #[test] fn reads_claude_stream_event_delta_types_and_keeps_ordered_segments() {
        let mut segments = Vec::new();
        let thought = json!({"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"think"}});
        let answer = json!({"type":"content_block_delta","delta":{"type":"text_delta","text":"answer"}});
        let thought_more = json!({"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":" again"}});
        for event in [&thought, &answer, &thought_more] {
            let (kind, delta) = stream_delta(event).unwrap();
            append_stream_delta(&mut segments, kind, delta);
        }
        assert_eq!(segments.iter().map(|part| (part.kind.as_str(), part.content.as_str())).collect::<Vec<_>>(),
            vec![("thinking", "think"), ("text", "answer"), ("thinking", " again")]);
    }
    #[test] fn detects_compaction_thrash_for_session_recovery() {
        assert!(is_compaction_thrash("Autocompact is thrashing: context refilled to the limit"));
        assert!(!is_compaction_thrash("Claude Agent SDK request timed out"));
    }
}
