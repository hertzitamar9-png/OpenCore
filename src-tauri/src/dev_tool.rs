use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::{Component, Path, PathBuf};
use std::time::Duration;

const MAX_FILE_BYTES: u64 = 16 * 1024 * 1024;

pub(crate) fn tool_spec() -> Value {
    json!({"type":"function","function":{
        "name":"dev","description":"General coding workspace for any text-based language. Inspect with status, search and read; use exact-hash edit/apply_patch on existing files; run compilers, tests or browser automation; inspect Git status/diff/log; commit named files and push without force. Use checkout for prior artifacts. Preserve the existing project and publish only a verified version. The run action executes command in this workspace using Windows PowerShell 5.1. Supply verifyPaths for each file being tested so successful checks can authorize publication. Use separate calls instead of &&. It can invoke any installed compiler, interpreter or test runner; it does not claim they are installed.",
        "parameters":{"type":"object","properties":{
            "action":{"type":"string","enum":["status","list","search","read","recall","checkpoint","checkout","write","edit","patch","apply_patch","run","git_status","git_diff","git_log","git_commit","git_push","publish"]},
            "title":{"type":"string","description":"Name of the completed component for checkpoint"},
            "summary":{"type":"string","description":"Component purpose, interfaces, dependencies, decisions and known failures; notes are not proof"},
            "nextSteps":{"type":"string","description":"Unfinished work to retain after checkpoint"},
            "versionSha256":{"type":"string","description":"Read an exact source version from a project checkpoint; omit to read current code"},
            "offset":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":32},
            "path":{"type":"string","description":"Workspace-relative file path; never an absolute path"},
            "query":{"type":"string","description":"Literal text to find in source files"},
            "artifactId":{"type":"string","description":"ID from status for an exact prior file version"},
            "content":{"type":"string","description":"Full UTF-8 content for a new file only"},
            "expectedSha256":{"type":"string","description":"Hash returned by read or write, required for patch"},
            "edits":{"type":"array","items":{"type":"object","properties":{"oldText":{"type":"string"},"newText":{"type":"string"}},"required":["oldText","newText"]}},
            "command":{"type":"string","description":"Shell command for the project's language-specific checks"},
            "paths":{"type":"array","items":{"type":"string"},"description":"Exact workspace-relative files to commit, or all component dependencies to checkpoint"},
            "message":{"type":"string","description":"Git commit message"},
            "remote":{"type":"string","description":"Configured Git remote name, defaults to origin"},
            "verifyPaths":{"type":"array","items":{"type":"string"},"description":"Optional files checked by this command. Their exact hashes are recorded on success; required before publish"},
            "startLine":{"type":"integer","minimum":1},"lineCount":{"type":"integer","minimum":1,"maximum":2000},
            "newProject":{"type":"boolean","description":"Explicitly start unrelated work instead of continuing a previous file"}
        },"required":["action"],"additionalProperties":false}
    }})
}

pub(crate) fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn text_arg<'a>(args: &'a Value, key: &str) -> Result<&'a str, String> {
    args.get(key).and_then(Value::as_str).filter(|s| !s.is_empty())
        .ok_or_else(|| format!("{key} is required"))
}

pub(crate) fn workspace_file(root: &Path, name: &str, create_parents: bool) -> Result<PathBuf, String> {
    let relative = Path::new(name);
    if name.is_empty() || relative.components().any(|part| !matches!(part, Component::Normal(_))) {
        return Err("Use a workspace-relative path without . or ..".into());
    }
    std::fs::create_dir_all(root).map_err(|e| e.to_string())?;
    let canonical_root = std::fs::canonicalize(root).map_err(|e| e.to_string())?;
    let mut target = canonical_root.clone();
    let components = relative.components().collect::<Vec<_>>();
    for (index, component) in components.iter().enumerate() {
        let Component::Normal(part) = component else { unreachable!() };
        target.push(part);
        if index + 1 == components.len() { break; }
        if target.exists() {
            if std::fs::symlink_metadata(&target).map_err(|e| e.to_string())?.file_type().is_symlink() {
                return Err("Workspace path crosses a symbolic link".into());
            }
            target = std::fs::canonicalize(&target).map_err(|e| e.to_string())?;
            if !target.starts_with(&canonical_root) || !target.is_dir() {
                return Err("Workspace path leaves the selected folder".into());
            }
        } else if create_parents {
            std::fs::create_dir(&target).map_err(|e| e.to_string())?;
        } else {
            return Err("Parent folder does not exist".into());
        }
    }
    if target.exists() {
        if std::fs::symlink_metadata(&target).map_err(|e| e.to_string())?.file_type().is_symlink() {
            return Err("Workspace file is a symbolic link".into());
        }
        let canonical = std::fs::canonicalize(&target).map_err(|e| e.to_string())?;
        if !canonical.starts_with(&canonical_root) || !canonical.is_file() {
            return Err("Workspace path is not a file inside the selected folder".into());
        }
        Ok(canonical)
    } else {
        Ok(target)
    }
}

pub(crate) fn read_text(path: &Path) -> Result<String, String> {
    if path.metadata().map_err(|e| e.to_string())?.len() > MAX_FILE_BYTES {
        return Err("File exceeds the 16 MiB coding workspace limit".into());
    }
    std::fs::read_to_string(path).map_err(|e| format!("Could not read UTF-8 code file: {e}"))
}

fn list_files(root: &Path) -> Result<Vec<Value>, String> {
    let mut files = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(folder) = pending.pop() {
        for item in std::fs::read_dir(folder).map_err(|e| e.to_string())? {
            let item = item.map_err(|e| e.to_string())?;
            let path = item.path();
            let kind = item.file_type().map_err(|e| e.to_string())?;
            if kind.is_symlink() { continue; }
            if kind.is_dir() {
                if !matches!(item.file_name().to_string_lossy().as_ref(), ".git" | "node_modules" | "target" | ".venv" | "dist") {
                    pending.push(path);
                }
            } else if kind.is_file() {
                files.push(json!({"path":path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/"),
                    "size":item.metadata().map_err(|e| e.to_string())?.len()}));
                if files.len() >= 200 { return Ok(files); }
            }
        }
    }
    files.sort_by(|a,b| a["path"].as_str().cmp(&b["path"].as_str()));
    Ok(files)
}

fn receipt_path(root: &Path) -> PathBuf { root.join("checks.json") }

fn read_receipts(root: &Path) -> Value {
    std::fs::read(receipt_path(root)).ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .filter(Value::is_object).unwrap_or_else(|| json!({}))
}

fn save_receipts(root: &Path, receipts: &Value) -> Result<(), String> {
    std::fs::create_dir_all(root).map_err(|e| e.to_string())?;
    std::fs::write(receipt_path(root), serde_json::to_vec(receipts).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())
}

async fn git_command(root: &Path, args: &[String]) -> Result<Value, String> {
    let mut process = tokio::process::Command::new("git");
    process.arg("-c").arg("core.quotepath=false").args(args).current_dir(root).kill_on_drop(true);
    #[cfg(windows)] { process.creation_flags(0x0800_0000); }
    let output = tokio::time::timeout(Duration::from_secs(120), process.output()).await
        .map_err(|_| "Git command timed out after 120 seconds".to_string())?
        .map_err(|e| e.to_string())?;
    Ok(json!({"exitCode":output.status.code().unwrap_or(-1),
        "stdout":String::from_utf8_lossy(&output.stdout).chars().take(16000).collect::<String>(),
        "stderr":String::from_utf8_lossy(&output.stderr).chars().take(16000).collect::<String>()}))
}

fn git_name(value: &str) -> Result<&str, String> {
    if value.is_empty() || value.len() > 100 || value.starts_with('-')
        || !value.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '/' | '-' | '_' | '.')) {
        return Err("Use a configured Git remote or branch name without options".into());
    }
    Ok(value)
}

pub(crate) async fn execute(root: &Path, artifacts_root: &Path, receipts_root: &Path,
    history: &[Value], args: &Value) -> Result<Value, String> {
    std::fs::create_dir_all(root).map_err(|e| e.to_string())?;
    let action = text_arg(args, "action")?;
    match action {
        "status" | "list" => Ok(json!({"workspace":root,"files":list_files(root)?,"priorArtifacts":history,
            "projectMemory":crate::project_memory::recall(root,receipts_root,args)?})),
        "recall" => crate::project_memory::recall(root,receipts_root,args),
        "checkpoint" => crate::project_memory::checkpoint(root,receipts_root,args,&read_receipts(receipts_root)),
        "search" => {
            let query = text_arg(args, "query")?;
            crate::tooling::execute_read_only(root, "search_project", &json!({"query":query}))
        }
        "read" => {
            let (name, content) = if let Some(hash) = args.get("versionSha256").and_then(Value::as_str) {
                let name = text_arg(args, "path")?;
                (name.to_string(), crate::project_memory::version(root,receipts_root,name,hash)?)
            } else if let Some(id) = args.get("artifactId").and_then(Value::as_str) {
                if !history.iter().any(|item| item["id"] == id) { return Err("Artifact is not in this conversation".into()); }
                let item = crate::artifacts::preview(artifacts_root, id)?;
                (item.info.name, item.text.ok_or("Artifact is not a UTF-8 code file")?)
            } else {
                let name = text_arg(args, "path")?.to_string();
                let path = workspace_file(root, &name, false)?;
                (name, read_text(&path)?)
            };
            let start = args.get("startLine").and_then(Value::as_u64).unwrap_or(1).max(1) as usize;
            let count = args.get("lineCount").and_then(Value::as_u64).unwrap_or(2000).clamp(1,2000) as usize;
            let lines = content.split_inclusive('\n').collect::<Vec<_>>();
            let shown = lines.iter().skip(start - 1).take(count).copied().collect::<String>();
            Ok(json!({"name":name,"sha256":sha256(content.as_bytes()),"totalLines":lines.len(),
                "startLine":start,"endLine":start.saturating_add(count).min(lines.len()+1)-1,
                "truncated":shown.len() < content.len(),"content":shown}))
        }
        "checkout" => {
            let id = text_arg(args, "artifactId")?;
            if !history.iter().any(|item| item["id"] == id) { return Err("Artifact is not in this conversation".into()); }
            let item = crate::artifacts::preview(artifacts_root, id)?;
            let content = item.text.ok_or("Artifact is not a UTF-8 code file")?;
            let name = args.get("path").and_then(Value::as_str).unwrap_or(&item.info.name);
            let path = workspace_file(root, name, true)?;
            if path.exists() { return Err("Workspace file already exists; read and patch it instead".into()); }
            std::fs::write(&path, content.as_bytes()).map_err(|e| e.to_string())?;
            Ok(json!({"path":name,"sourceArtifactId":id,"sha256":sha256(content.as_bytes()),"bytes":content.len()}))
        }
        "write" => {
            let name = text_arg(args, "path")?;
            let content = text_arg(args, "content")?;
            if content.len() as u64 > MAX_FILE_BYTES { return Err("Code file exceeds 16 MiB".into()); }
            if !history.is_empty() && !args.get("newProject").and_then(Value::as_bool).unwrap_or(false)
                && list_files(root)?.is_empty() {
                return Err("This conversation has prior files. Call status and checkout the relevant version, or set newProject=true for unrelated work".into());
            }
            let path = workspace_file(root, name, true)?;
            let mut file = std::fs::OpenOptions::new().write(true).create_new(true).open(&path)
                .map_err(|_| "Workspace file already exists; read and patch it instead".to_string())?;
            use std::io::Write;
            file.write_all(content.as_bytes()).map_err(|e| e.to_string())?;
            Ok(json!({"path":name,"sha256":sha256(content.as_bytes()),"bytes":content.len()}))
        }
        "edit" | "patch" | "apply_patch" => {
            let name = text_arg(args, "path")?;
            let expected = text_arg(args, "expectedSha256")?;
            let path = workspace_file(root, name, false)?;
            let original = read_text(&path)?;
            let old_hash = sha256(original.as_bytes());
            if expected != old_hash { return Err(format!("File changed since read: current sha256 {old_hash}")); }
            let edits = args.get("edits").and_then(Value::as_array).ok_or("edits array is required")?;
            if edits.is_empty() || edits.len() > 32 { return Err("Provide 1 to 32 exact edits".into()); }
            let mut updated = original.clone();
            for edit in edits {
                let old = text_arg(edit, "oldText")?;
                let new = edit.get("newText").and_then(Value::as_str).ok_or("newText is required")?;
                if updated.matches(old).count() != 1 { return Err("Each oldText must match exactly once; read the file and use a longer unique span".into()); }
                updated = updated.replacen(old, new, 1);
            }
            if updated.len() as u64 > MAX_FILE_BYTES { return Err("Patched file exceeds 16 MiB".into()); }
            std::fs::create_dir_all(receipts_root.join("versions")).map_err(|e| e.to_string())?;
            let backup = receipts_root.join("versions").join(format!("{old_hash}.bin"));
            if !backup.exists() { std::fs::write(&backup, original.as_bytes()).map_err(|e| e.to_string())?; }
            std::fs::write(&path, updated.as_bytes()).map_err(|e| e.to_string())?;
            Ok(json!({"path":name,"sha256":sha256(updated.as_bytes()),"previousSha256":old_hash,
                "bytes":updated.len(),"edits":edits.len(),"backup":backup}))
        }
        "run" => {
            let command = text_arg(args, "command")?;
            if command.len() > 16000 { return Err("Command exceeds 16,000 characters".into()); }
            let paths = match args.get("verifyPaths") {
                Some(value) => value.as_array().ok_or("verifyPaths must be an array")?.clone(),
                None => Vec::new(),
            };
            if paths.len() > 32 { return Err("List at most 32 files the command checks".into()); }
            let mut process = if cfg!(windows) { tokio::process::Command::new("powershell.exe") }
                else { tokio::process::Command::new("sh") };
            if cfg!(windows) { process.args(["-NoProfile", "-NonInteractive", "-Command", command]); }
            else { process.args(["-lc", command]); }
            process.current_dir(root).kill_on_drop(true);
            #[cfg(windows)] { process.creation_flags(0x0800_0000); }
            let output = tokio::time::timeout(Duration::from_secs(120), process.output()).await
                .map_err(|_| "Dev command timed out after 120 seconds".to_string())?
                .map_err(|e| e.to_string())?;
            let code = output.status.code().unwrap_or(-1);
            let stdout = String::from_utf8_lossy(&output.stdout).chars().take(16000).collect::<String>();
            let stderr = String::from_utf8_lossy(&output.stderr).chars().take(16000).collect::<String>();
            let mut checked = serde_json::Map::new();
            if output.status.success() {
                let mut receipts = read_receipts(receipts_root);
                for name in &paths {
                    let name = name.as_str().ok_or("verifyPaths must contain relative paths")?;
                    let path = workspace_file(root, name, false)?;
                    let digest = sha256(read_text(&path)?.as_bytes());
                    checked.insert(name.into(), json!(digest));
                    receipts[name] = json!({"sha256":digest,"command":command});
                }
                if !paths.is_empty() { save_receipts(receipts_root, &receipts)?; }
            }
            Ok(json!({"exitCode":code,"stdout":stdout,"stderr":stderr,"checked":checked}))
        }
        "git_status" => git_command(root, &["status".into(), "--short".into(), "--branch".into()]).await,
        "git_diff" => {
            let mut command = vec!["diff".into(), "--no-ext-diff".into()];
            if let Some(name) = args.get("path").and_then(Value::as_str) {
                workspace_file(root, name, false)?;
                command.extend(["--".into(), name.into()]);
            }
            git_command(root, &command).await
        }
        "git_log" => git_command(root, &["log".into(), "-n".into(), "20".into(), "--oneline".into()]).await,
        "git_commit" => {
            let message = text_arg(args, "message")?;
            if message.len() > 500 { return Err("Git commit message is too long".into()); }
            let paths = args.get("paths").and_then(Value::as_array).ok_or("paths is required")?;
            if paths.is_empty() || paths.len() > 32 { return Err("Specify 1 to 32 exact files to commit".into()); }
            let mut names = Vec::new();
            for value in paths {
                let name = value.as_str().ok_or("paths must contain relative files")?;
                workspace_file(root, name, false)?;
                names.push(name.to_string());
            }
            let mut add = vec!["add".into(), "--".into()];
            add.extend(names.iter().cloned());
            let staged = git_command(root, &add).await?;
            if staged["exitCode"] != 0 { return Ok(staged); }
            let mut commit = vec!["commit".into(), "--only".into(), "-m".into(), message.into(), "--".into()];
            commit.extend(names);
            git_command(root, &commit).await
        }
        "git_push" => {
            let remote = git_name(args.get("remote").and_then(Value::as_str).unwrap_or("origin"))?;
            let branch = git_command(root, &["symbolic-ref".into(), "--quiet".into(), "--short".into(), "HEAD".into()]).await?;
            if branch["exitCode"] != 0 { return Err("Check out a named branch before pushing".into()); }
            let branch = branch["stdout"].as_str().ok_or("Git did not return a branch")?.trim();
            git_name(branch)?;
            git_command(root, &["push".into(), "--set-upstream".into(), remote.into(), branch.into()]).await
        }
        "publish" => {
            let name = text_arg(args, "path")?;
            let path = workspace_file(root, name, false)?;
            let content = read_text(&path)?;
            let digest = sha256(content.as_bytes());
            let receipts = read_receipts(receipts_root);
            let check = &receipts[name];
            if check["sha256"] != digest {
                return Err("This exact file version has no passing check. Run the appropriate compiler, tests, or browser automation first".into());
            }
            let filename = path.file_name().and_then(|name| name.to_str()).ok_or("Invalid filename")?;
            let info = crate::artifacts::create_text(artifacts_root, filename, &content)?;
            Ok(json!({"id":info.id,"name":info.name,"mime":info.mime,"size":info.size,
                "sha256":digest,"sourcePath":name,"verification":check,
                "preview_link":format!("artifact://{}",info.id),
                "download_link":format!("artifact-download://{}",info.id)}))
        }
        _ => Err("Unknown dev action".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixture() -> (std::path::PathBuf, std::path::PathBuf, std::path::PathBuf) {
        let base = std::env::temp_dir().join(format!("opencore-dev-test-{}", uuid::Uuid::new_v4()));
        let workspace = base.join("workspace");
        let artifacts = base.join("artifacts");
        let receipts = base.join("receipts");
        std::fs::create_dir_all(&workspace).unwrap();
        (workspace, artifacts, receipts)
    }

    #[tokio::test]
    async fn completed_component_survives_new_chat_and_detects_changed_dependencies() {
        let (workspace, artifacts, receipts) = fixture();
        std::fs::write(workspace.join("movement.js"), "function run() { return 2; }\n").unwrap();
        let checkpoint = execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"checkpoint", "title":"Running system", "summary":"run doubles movement speed; walking must preserve it",
            "paths":["movement.js"], "nextSteps":"Add walking without changing run"
        })).await.unwrap();
        let original = checkpoint["files"][0]["sha256"].as_str().unwrap().to_string();
        let new_chat = receipts.parent().unwrap().join("other-chat");
        let recalled = execute(&workspace, &artifacts, &new_chat, &[], &json!({"action":"recall", "query":"Running"})).await.unwrap();
        assert_eq!(recalled["checkpoints"][0]["title"], "Running system");
        assert_eq!(recalled["checkpoints"][0]["stale"], false);
        assert_eq!(recalled["checkpoints"][0]["evidence"], "unverified");
        std::fs::write(workspace.join("movement.js"), "function run() { return 3; }\n").unwrap();
        let changed = execute(&workspace, &artifacts, &new_chat, &[], &json!({"action":"recall", "query":"Running"})).await.unwrap();
        assert_eq!(changed["checkpoints"][0]["stale"], true);
        let exact = execute(&workspace, &artifacts, &new_chat, &[], &json!({
            "action":"read", "path":"movement.js", "versionSha256":original
        })).await.unwrap();
        assert_eq!(exact["content"], "function run() { return 2; }\n");
        std::fs::remove_dir_all(workspace.parent().unwrap()).unwrap();
    }

    #[tokio::test]
    async fn keeps_existing_code_and_rejects_stale_or_ambiguous_edits() {
        let (workspace, artifacts, receipts) = fixture();
        let original = "const score = 0;\nconst levels = ['easy', 'hard'];\nstartGame();\n";
        let written = execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"write", "path":"game.js", "content":original
        })).await.unwrap();
        let hash = written["sha256"].as_str().unwrap();
        let patched = execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"patch", "path":"game.js", "expectedSha256":hash,
            "edits":[{"oldText":"const score = 0;", "newText":"const score = 1;"}]
        })).await.unwrap();
        assert_ne!(patched["sha256"], hash);
        assert_eq!(std::fs::read_to_string(workspace.join("game.js")).unwrap(),
            "const score = 1;\nconst levels = ['easy', 'hard'];\nstartGame();\n");
        assert!(execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"patch", "path":"game.js", "expectedSha256":hash,
            "edits":[{"oldText":"startGame();", "newText":"startGame(true);"}]
        })).await.is_err());
        assert!(execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"patch", "path":"game.js", "expectedSha256":patched["sha256"],
            "edits":[{"oldText":"const", "newText":"let"}]
        })).await.is_err());
        std::fs::remove_dir_all(workspace.parent().unwrap()).unwrap();
    }

    #[tokio::test]
    async fn legacy_artifact_can_be_checked_out_and_published_only_after_a_check() {
        let (workspace, artifacts, receipts) = fixture();
        let source = "print('hello')\n";
        let old = crate::artifacts::create(&artifacts, "script.py", source, "utf8").unwrap();
        let history = [json!({"id":old.id, "name":"script.py"})];
        let copied = execute(&workspace, &artifacts, &receipts, &history, &json!({
            "action":"checkout", "artifactId":old.id, "path":"script.py"
        })).await.unwrap();
        assert_eq!(copied["sha256"], json!(sha256(source.as_bytes())));
        assert_eq!(std::fs::read_to_string(workspace.join("script.py")).unwrap(), source);
        assert!(execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"publish", "path":"script.py"
        })).await.is_err());
        let checked = execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"run", "command":"python script.py", "verifyPaths":["script.py"]
        })).await.unwrap();
        assert_eq!(checked["exitCode"], 0);
        let published = execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"publish", "path":"script.py"
        })).await.unwrap();
        assert_eq!(published["verification"]["command"], "python script.py");
        assert_eq!(crate::artifacts::preview(&artifacts, published["id"].as_str().unwrap()).unwrap().text.as_deref(), Some(source));
        std::fs::remove_dir_all(workspace.parent().unwrap()).unwrap();
    }

    #[tokio::test]
    async fn refuses_paths_outside_the_workspace_and_requires_prior_artifact_checkout() {
        let (workspace, artifacts, receipts) = fixture();
        let history = [json!({"id":"previous", "name":"game.html"})];
        assert!(execute(&workspace, &artifacts, &receipts, &history, &json!({
            "action":"write", "path":"game.html", "content":"new game"
        })).await.is_err());
        assert!(execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"write", "path":"../escape.py", "content":"bad"
        })).await.is_err());
        assert!(execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"read", "path":"C:\\Windows\\win.ini"
        })).await.is_err());
        std::fs::remove_dir_all(workspace.parent().unwrap()).unwrap();
    }

    #[tokio::test]
    async fn failed_check_does_not_authorize_publication_and_patches_invalidate_checks() {
        let (workspace, artifacts, receipts) = fixture();
        let written = execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"write","path":"main.js","content":"console.log('ok');\n"
        })).await.unwrap();
        let failed = execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"run","command":"node -e \"process.exit(7)\"","verifyPaths":["main.js"]
        })).await.unwrap();
        assert_ne!(failed["exitCode"], 0);
        assert!(execute(&workspace, &artifacts, &receipts, &[], &json!({"action":"publish","path":"main.js"})).await.is_err());
        let passed = execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"run","command":"node main.js","verifyPaths":["main.js"]
        })).await.unwrap();
        assert_eq!(passed["exitCode"], 0);
        assert!(passed["stdout"].as_str().unwrap().contains("ok"));
        execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"patch","path":"main.js","expectedSha256":written["sha256"],
            "edits":[{"oldText":"ok","newText":"updated"}]
        })).await.unwrap();
        assert!(execute(&workspace, &artifacts, &receipts, &[], &json!({"action":"publish","path":"main.js"})).await.is_err());
        std::fs::remove_dir_all(workspace.parent().unwrap()).unwrap();
    }

    #[tokio::test]
    async fn searches_and_edits_only_the_selected_file() {
        let (workspace, artifacts, receipts) = fixture();
        std::fs::create_dir_all(workspace.join("src")).unwrap();
        std::fs::write(workspace.join("src/game.js"), "const wood = 'brown';\nconst sky = 'blue';\n").unwrap();
        std::fs::write(workspace.join("src/other.js"), "const wood = 'brown';\n").unwrap();
        let found = execute(&workspace, &artifacts, &receipts, &[], &json!({"action":"search","query":"wood"})).await.unwrap();
        assert_eq!(found["matches"].as_array().unwrap().len(), 2);
        let read = execute(&workspace, &artifacts, &receipts, &[], &json!({"action":"read","path":"src/game.js"})).await.unwrap();
        execute(&workspace, &artifacts, &receipts, &[], &json!({"action":"apply_patch","path":"src/game.js",
            "expectedSha256":read["sha256"],"edits":[{"oldText":"'brown'","newText":"'tan'"}]})).await.unwrap();
        assert!(std::fs::read_to_string(workspace.join("src/game.js")).unwrap().contains("'tan'"));
        assert!(std::fs::read_to_string(workspace.join("src/other.js")).unwrap().contains("'brown'"));
        std::fs::remove_dir_all(workspace.parent().unwrap()).unwrap();
    }

    #[tokio::test]
    async fn git_tools_commit_named_files_and_push_without_force() {
        let (workspace, artifacts, receipts) = fixture();
        let remote = workspace.parent().unwrap().join("remote.git");
        let git = |cwd: &Path, arguments: &[&str]| {
            let output = std::process::Command::new("git").args(arguments).current_dir(cwd).output().unwrap();
            assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        };
        git(&workspace, &["init", "-b", "main"]);
        git(&workspace, &["config", "user.name", "OpenCore Test"]);
        git(&workspace, &["config", "user.email", "test@opencore.invalid"]);
        git(&workspace.parent().unwrap(), &["init", "--bare", remote.to_str().unwrap()]);
        git(&workspace, &["remote", "add", "origin", remote.to_str().unwrap()]);
        std::fs::write(workspace.join("game.js"), "console.log('new');\n").unwrap();
        std::fs::write(workspace.join("other.js"), "do not commit\n").unwrap();
        let committed = execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"git_commit","paths":["game.js"],"message":"Add game"
        })).await.unwrap();
        assert_eq!(committed["exitCode"], 0);
        let status = execute(&workspace, &artifacts, &receipts, &[], &json!({"action":"git_status"})).await.unwrap();
        assert!(status["stdout"].as_str().unwrap().contains("other.js"));
        let pushed = execute(&workspace, &artifacts, &receipts, &[], &json!({"action":"git_push","remote":"origin"})).await.unwrap();
        assert_eq!(pushed["exitCode"], 0);
        let log = execute(&workspace, &artifacts, &receipts, &[], &json!({"action":"git_log"})).await.unwrap();
        assert!(log["stdout"].as_str().unwrap().contains("Add game"));
        assert!(execute(&workspace, &artifacts, &receipts, &[], &json!({"action":"git_push","remote":"--force"})).await.is_err());
        std::fs::remove_dir_all(workspace.parent().unwrap()).unwrap();
    }

    #[tokio::test]
    async fn terminal_can_inspect_the_environment_without_marking_a_file_verified() {
        let (workspace, artifacts, receipts) = fixture();
        std::fs::write(workspace.join("game.py"), "print('hello')\n").unwrap();
        let probe = execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"run","command":"python --version"
        })).await.unwrap();
        assert_eq!(probe["exitCode"], 0);
        assert!(probe["checked"].as_object().unwrap().is_empty());
        assert!(execute(&workspace, &artifacts, &receipts, &[], &json!({
            "action":"publish","path":"game.py"
        })).await.is_err());
        std::fs::remove_dir_all(workspace.parent().unwrap()).unwrap();
    }
}
