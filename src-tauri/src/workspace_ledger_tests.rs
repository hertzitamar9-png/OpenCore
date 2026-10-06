use super::*;

struct Fixture {
    root: PathBuf,
    workspace: PathBuf,
    ledger: Arc<WorkspaceLedger>,
}
impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("opencore-files-{}", uuid::Uuid::new_v4()));
        let workspace = root.join("project");
        fs::create_dir_all(&workspace).unwrap();
        let ledger = WorkspaceLedger::new(root.join("data")).unwrap();
        Self {
            root,
            workspace,
            ledger,
        }
    }
    fn write(&self, path: &str, bytes: &[u8]) {
        let path = self.workspace.join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, bytes).unwrap();
    }
    fn capture(&self, turn: &str) -> TurnCapture {
        self.ledger
            .begin_turn("chat", turn, &self.workspace)
            .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn counts_real_edits_and_keeps_the_two_exact_versions() {
    let f = Fixture::new();
    f.write("main.rs", b"one\ntwo\nthree\n");
    let capture = f.capture("edit");
    f.write("main.rs", b"one\nnew two\nthree\nfour\n");
    let result = f.ledger.finish_turn(capture, "completed").unwrap();
    assert_eq!(result["added"], 2);
    assert_eq!(result["removed"], 1);
    let file = &result["files"][0];
    assert_eq!(file["change"], "modified");
    assert_ne!(file["beforeHash"], file["afterHash"]);
    let before = f
        .ledger
        .command(json!({"action":"preview","id":file["id"],"version":"before"}))
        .unwrap();
    let after = f
        .ledger
        .command(json!({"action":"preview","id":file["id"],"version":"after"}))
        .unwrap();
    assert_eq!(before["text"], "one\ntwo\nthree\n");
    assert_eq!(after["text"], "one\nnew two\nthree\nfour\n");
    f.write("main.rs", b"later unrelated content");
    assert_eq!(
        f.ledger
            .command(json!({"action":"preview","id":file["id"],"version":"after"}))
            .unwrap()["text"],
        after["text"]
    );
}

#[test]
fn deleted_files_keep_their_before_snapshot_even_after_restart() {
    let f = Fixture::new();
    f.write("gone.txt", "שלום\nsecond line\n".as_bytes());
    let capture = f.capture("delete");
    fs::remove_file(f.workspace.join("gone.txt")).unwrap();
    let result = f.ledger.finish_turn(capture, "cancelled").unwrap();
    assert_eq!(result["status"], "cancelled");
    assert_eq!(result["added"], 0);
    assert_eq!(result["removed"], 2);
    let file = &result["files"][0];
    assert_eq!(file["change"], "deleted");
    assert!(file["afterHash"].is_null());
    let reopened = WorkspaceLedger::new(f.root.join("data")).unwrap();
    let preview = reopened
        .command(json!({"action":"preview","id":file["id"],"version":"before"}))
        .unwrap();
    assert_eq!(preview["text"], "שלום\nsecond line\n");
    assert!(reopened
        .command(json!({"action":"preview","id":file["id"],"version":"after"}))
        .is_err());
}

#[test]
fn creations_binary_outputs_and_empty_latest_tasks_do_not_invent_counts() {
    let f = Fixture::new();
    let capture = f.capture("create");
    f.write("hello.txt", "héllo 🌍\nlast".as_bytes());
    f.write("image.png", b"\x89PNG\r\n\x1a\n\x00binary");
    let result = f.ledger.finish_turn(capture, "failed").unwrap();
    assert_eq!(result["added"], 2);
    assert_eq!(result["removed"], 0);
    let files = result["files"].as_array().unwrap();
    let image = files
        .iter()
        .find(|file| file["path"] == "image.png")
        .unwrap();
    assert!(image["added"].is_null());
    assert!(image["removed"].is_null());
    let preview = f
        .ledger
        .command(json!({"action":"preview","id":image["id"],"version":"after"}))
        .unwrap();
    assert!(preview["dataUrl"]
        .as_str()
        .unwrap()
        .starts_with("data:image/png;base64,"));
    let capture = f.capture("nothing");
    f.ledger.finish_turn(capture, "completed").unwrap();
    let latest = f
        .ledger
        .command(json!({"action":"changes","conversationId":"chat"}))
        .unwrap();
    assert_eq!(latest["turnId"], "nothing");
    assert_eq!(latest["files"].as_array().unwrap().len(), 0);
}

#[test]
fn studio_references_are_linked_to_the_job_and_deduplicated() {
    let f = Fixture::new();
    f.write("song.wav", b"RIFF\x00test WAV");
    let output = f.workspace.join("song.wav");
    let result = f
        .ledger
        .register_outputs("chat", "music-job", &[output.clone()])
        .unwrap();
    let file = &result["files"][0];
    assert_eq!(file["change"], "output");
    assert_eq!(file["turnId"], "music-job");
    assert_eq!(file["origin"], "studio");
    assert_eq!(
        file["source"].as_str().unwrap(),
        fs::canonicalize(&output).unwrap().to_string_lossy()
    );
    f.ledger
        .register_outputs("chat", "music-job", &[output])
        .unwrap();
    let listed = f
        .ledger
        .command(json!({"action":"list","conversationId":"chat"}))
        .unwrap();
    assert_eq!(listed["files"].as_array().unwrap().len(), 1);
}

#[test]
fn browser_routes_legacy_absolute_outputs_without_revealing_source_directories() {
    let f = Fixture::new();
    f.write("index.html", b"<script src='assets/game.js'></script>");
    f.write("assets/game.js", b"window.recorded = true;");
    f.write("one/result.png", b"first image");
    f.write("two/result.png", b"second image");
    let outputs = [
        "index.html",
        "assets/game.js",
        "one/result.png",
        "two/result.png",
    ]
    .map(|path| f.workspace.join(path));
    let result = f
        .ledger
        .register_outputs("chat", "legacy-studio", &outputs)
        .unwrap();
    let id = result["files"][0]["id"].as_str().unwrap();
    // This is the pre-existing ledger format: path is the absolute display/source path.
    assert!(Path::new(result["files"][0]["path"].as_str().unwrap()).is_absolute());
    let (target, manifest) = f.ledger.browser_manifest(id, None).unwrap();
    assert_eq!(target, "index.html");
    assert_eq!(
        manifest.keys().map(String::as_str).collect::<Vec<_>>(),
        [
            "assets/game.js",
            "index.html",
            "one/result.png",
            "two/result.png"
        ]
    );
    f.write("assets/game.js", b"later unrelated script");
    assert_eq!(
        f.ledger
            .browser_asset(&manifest["assets/game.js"])
            .unwrap()
            .bytes,
        b"window.recorded = true;"
    );
}

#[test]
fn browser_capture_keeps_unchanged_assets_and_both_exact_versions_after_restart() {
    let f = Fixture::new();
    f.write("index.html", b"<h1>Before</h1>");
    f.write("assets/game.css", b"body { color: blue; }");
    f.write("assets/game.js", b"window.recorded = true;");
    f.write("assets/image.png", b"\x89PNG\r\n\x1a\nimage");
    let capture = f.capture("modify-page");
    f.write(
        "index.html",
        b"<h1>After</h1><script src='assets/game.js'></script>",
    );
    let result = f.ledger.finish_turn(capture, "completed").unwrap();
    assert_eq!(result["files"].as_array().unwrap().len(), 1);
    let id = result["files"][0]["id"].as_str().unwrap();
    f.write("assets/game.css", b"later unrelated stylesheet");
    let reopened = WorkspaceLedger::new(f.root.join("data")).unwrap();
    let (_, after) = reopened.browser_manifest(id, Some("after")).unwrap();
    assert_eq!(
        after.keys().map(String::as_str).collect::<Vec<_>>(),
        [
            "assets/game.css",
            "assets/game.js",
            "assets/image.png",
            "index.html"
        ]
    );
    assert_eq!(
        reopened
            .browser_asset(&after["assets/game.css"])
            .unwrap()
            .bytes,
        b"body { color: blue; }"
    );
    assert_eq!(
        reopened.browser_asset(&after["index.html"]).unwrap().bytes,
        b"<h1>After</h1><script src='assets/game.js'></script>"
    );
    let (_, before) = reopened.browser_manifest(id, Some("before")).unwrap();
    assert_eq!(
        reopened.browser_asset(&before["index.html"]).unwrap().bytes,
        b"<h1>Before</h1>"
    );
    assert_eq!(
        reopened
            .browser_asset(&before["assets/game.js"])
            .unwrap()
            .bytes,
        b"window.recorded = true;"
    );
    let object = reopened
        .object_path(&sha256(b"body { color: blue; }"))
        .unwrap();
    fs::write(object, b"tampered asset").unwrap();
    assert!(reopened
        .browser_asset(&after["assets/game.css"])
        .err()
        .unwrap()
        .contains("hash"));
}

#[test]
fn historical_capture_without_asset_metadata_never_reads_current_or_other_capture_assets() {
    let f = Fixture::new();
    f.write("index.html", b"<h1>Before</h1>");
    f.write("assets/game.js", b"old independently indexed script");
    f.ledger
        .command(json!({"action":"index","workspace":f.workspace}))
        .unwrap();
    let capture = f.capture("historical-page");
    f.write("index.html", b"<script src='assets/game.js'></script>");
    let result = f.ledger.finish_turn(capture, "completed").unwrap();
    let id = result["files"][0]["id"].as_str().unwrap();
    // Simulate a completed capture written before full asset manifests existed.
    f.ledger
        .database()
        .unwrap()
        .execute("DELETE FROM capture_assets", [])
        .unwrap();
    f.write("assets/game.js", b"current unrecorded script");
    let reopened = WorkspaceLedger::new(f.root.join("data")).unwrap();
    let (target, manifest) = reopened.browser_manifest(id, None).unwrap();
    assert_eq!(target, "index.html");
    assert_eq!(
        manifest.keys().map(String::as_str).collect::<Vec<_>>(),
        ["index.html"]
    );
    assert_eq!(
        reopened
            .browser_asset(&manifest["index.html"])
            .unwrap()
            .bytes,
        b"<script src='assets/game.js'></script>"
    );
}

#[test]
fn indexed_published_artifacts_use_real_name_mime_and_current_snapshot_only() {
    let f = Fixture::new();
    f.write("artifact-id.bin", b"<h1>Saved HTML</h1>");
    let result = f.ledger.command(json!({"action":"index","conversationId":"chat","jobId":"evidence-id","source":"published","entries":[{"path":f.workspace.join("artifact-id.bin"),"name":"site.html","mime":"text/html"}]})).unwrap();
    let file = &result["files"][0];
    assert_eq!(file["path"], "site.html");
    assert_eq!(file["mime"], "text/html");
    assert_eq!(file["origin"], "published");
    assert!(file["beforeHash"].is_null());
    assert!(file["added"].is_null());
    let preview = f
        .ledger
        .command(json!({"action":"preview","id":file["id"]}))
        .unwrap();
    assert_eq!(preview["text"], "<h1>Saved HTML</h1>");
    assert_eq!(preview["mime"], "text/html");
    assert!(f
        .ledger
        .command(json!({"action":"changes","conversationId":"chat"}))
        .unwrap()["turnId"]
        .is_null());
}

#[test]
fn task_receipt_merges_published_outputs_even_when_workspace_is_unchanged() {
    let f = Fixture::new();
    let capture = f.capture("submission-id");
    let outside_workspace = f.root.join("published.bin");
    fs::write(&outside_workspace, b"<h1>Artifact</h1>").unwrap();
    f.ledger.command(json!({"action":"index","conversationId":"chat","jobId":"submission-id","source":"published","live":true,"entries":[{"path":outside_workspace,"name":"generated.html","mime":"text/html"}]})).unwrap();
    f.ledger.finish_turn(capture, "failed").unwrap();
    let changes = f
        .ledger
        .command(json!({"action":"changes","conversationId":"chat"}))
        .unwrap();
    assert_eq!(changes["status"], "failed");
    assert_eq!(changes["turnId"], "submission-id");
    assert_eq!(changes["files"].as_array().unwrap().len(), 1);
    assert_eq!(changes["files"][0]["path"], "generated.html");
    assert!(changes["files"][0]["added"].is_null());
    let newer = f.capture("newer-task");
    f.ledger.finish_turn(newer, "completed").unwrap();
    f.ledger
        .register_outputs("chat", "submission-id", &[f.root.join("published.bin")])
        .unwrap();
    assert_eq!(
        f.ledger
            .command(json!({"action":"changes","conversationId":"chat"}))
            .unwrap()["turnId"],
        "newer-task"
    );
}

#[test]
fn large_output_references_are_verified_and_never_substituted_for_changed_bytes() {
    let f = Fixture::new();
    f.write(
        "song.wav",
        b"RIFF\0this output is larger than its snapshot limit",
    );
    let limits = LedgerLimits {
        max_file_bytes: 16,
        max_total_bytes: 32,
        max_entries: 50,
        max_depth: 8,
        max_reference_bytes: 128,
        max_preview_bytes: 128,
        diff_work: 10_000,
    };
    let ledger = WorkspaceLedger::open(f.root.join("references"), limits).unwrap();
    let output = f.workspace.join("song.wav");
    let result = ledger
        .register_outputs("chat", "job", &[output.clone()])
        .unwrap();
    let file = &result["files"][0];
    assert_eq!(file["snapshotAvailable"], false);
    assert_eq!(
        ledger
            .command(json!({"action":"preview","id":file["id"]}))
            .unwrap()["snapshotAvailable"],
        false
    );
    fs::write(output, b"RIFF\0changed output has a different content hash").unwrap();
    assert!(ledger
        .command(json!({"action":"preview","id":file["id"]}))
        .unwrap_err()
        .contains("hash"));
}

#[test]
fn size_and_dependency_omissions_are_visible_and_are_never_called_deletions() {
    let f = Fixture::new();
    f.write("small.txt", b"before\n");
    f.write("node_modules/hidden.js", b"dependency\n");
    f.write("weights.gguf", b"weight data");
    let limits = LedgerLimits {
        max_file_bytes: 16,
        max_total_bytes: 32,
        max_entries: 50,
        max_depth: 8,
        max_reference_bytes: 128,
        max_preview_bytes: 128,
        diff_work: 10_000,
    };
    let ledger = WorkspaceLedger::open(f.root.join("limited"), limits).unwrap();
    let capture = ledger.begin_turn("chat", "limit", &f.workspace).unwrap();
    f.write("small.txt", b"this file became too large to snapshot\n");
    let result = ledger.finish_turn(capture, "completed").unwrap();
    assert!(result["files"].as_array().unwrap().is_empty());
    let coverage = result["coverage"].to_string();
    assert!(coverage.contains("node_modules"));
    assert!(coverage.contains("weights.gguf"));
    assert!(coverage.contains("small.txt"));
    assert!(coverage.contains("size"));
}

#[test]
fn traversal_and_byte_limits_report_incomplete_capture() {
    let f = Fixture::new();
    f.write("a.txt", b"12345678");
    f.write("b.txt", b"abcdefgh");
    f.write("c.txt", b"ijklmnop");
    let limits = LedgerLimits {
        max_file_bytes: 16,
        max_total_bytes: 8,
        max_entries: 2,
        max_depth: 8,
        max_reference_bytes: 128,
        max_preview_bytes: 128,
        diff_work: 10_000,
    };
    let ledger = WorkspaceLedger::open(f.root.join("limited"), limits).unwrap();
    let result = ledger
        .command(json!({"action":"index","workspace":f.workspace,"conversationId":"chat"}))
        .unwrap();
    assert!(!result["coverage"].as_array().unwrap().is_empty());
    assert!(result["files"].as_array().unwrap().len() <= 1);
}

#[test]
fn previews_accept_only_known_record_ids_and_reject_tampered_objects() {
    let f = Fixture::new();
    let capture = f.capture("create");
    f.write("safe.txt", b"safe\n");
    let result = f.ledger.finish_turn(capture, "completed").unwrap();
    let file = &result["files"][0];
    assert!(f
        .ledger
        .command(json!({"action":"preview","id":"../project/safe.txt"}))
        .is_err());
    assert!(f
        .ledger
        .command(json!({"action":"preview","id":file["id"],"version":"../../outside"}))
        .is_err());
    let hash = file["afterHash"].as_str().unwrap();
    fs::write(f.ledger.object_path(hash).unwrap(), b"evil\n").unwrap();
    assert!(f
        .ledger
        .command(json!({"action":"preview","id":file["id"],"version":"after"}))
        .unwrap_err()
        .contains("hash"));
}

#[test]
fn restart_recovers_partial_changes_from_the_persisted_before_capture() {
    let f = Fixture::new();
    f.write("partial.txt", b"before\n");
    let _capture = f.capture("interrupted");
    f.write("partial.txt", b"after\n");
    let reopened = WorkspaceLedger::new(f.root.join("data")).unwrap();
    let result = reopened
        .command(json!({"action":"changes","conversationId":"chat"}))
        .unwrap();
    assert_eq!(result["status"], "interrupted");
    assert_eq!(result["added"], 1);
    assert_eq!(result["removed"], 1);
}

#[cfg(unix)]
#[test]
fn neither_workspace_links_nor_snapshot_links_can_escape() {
    use std::os::unix::fs::symlink;
    let f = Fixture::new();
    let outside = f.root.join("outside");
    fs::create_dir_all(&outside).unwrap();
    fs::write(outside.join("secret.txt"), b"secret").unwrap();
    symlink(&outside, f.workspace.join("escape")).unwrap();
    let capture = f.capture("links");
    f.write("safe.txt", b"inside\n");
    let result = f.ledger.finish_turn(capture, "completed").unwrap();
    assert_eq!(result["files"].as_array().unwrap().len(), 1);
    assert!(result["coverage"].to_string().contains("symlink"));
    let file = &result["files"][0];
    let hash = file["afterHash"].as_str().unwrap();
    let object = f.ledger.object_path(hash).unwrap();
    fs::remove_file(&object).unwrap();
    symlink(outside.join("secret.txt"), &object).unwrap();
    assert!(f
        .ledger
        .command(json!({"action":"preview","id":file["id"]}))
        .is_err());
    assert!(f
        .ledger
        .command(json!({"action":"index","workspace":f.workspace.join("escape")}))
        .is_err());
}

#[test]
fn exact_line_diff_handles_repeated_lines_newlines_and_unicode() {
    assert_eq!(
        line_counts("a\nx\na\n", "a\na\ny\n", 10_000).unwrap(),
        (1, 1)
    );
    assert_eq!(
        line_counts("שלום\n🌍\n", "שלום\nחדש\n🌍\n", 10_000).unwrap(),
        (1, 0)
    );
    assert_eq!(line_counts("last", "last\n", 10_000).unwrap(), (1, 1));
    assert_eq!(line_counts("", "", 10_000).unwrap(), (0, 0));
    assert!(line_counts("a\nb\nc\n", "x\ny\nz\n", 1).is_err());
}
