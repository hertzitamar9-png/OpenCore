use super::*;
use serde_json::json;
use std::fs;

#[tokio::test]
async fn serves_real_html_and_recorded_relative_assets_without_exposing_other_files() {
    let root = std::env::temp_dir().join(format!("opencore-browser-{}", uuid::Uuid::new_v4()));
    let project = root.join("project");
    fs::create_dir_all(project.join("assets")).unwrap();
    fs::write(project.join("שלום game.html"), b"<!doctype html><script>document.body.dataset.running='yes'</script><link rel=stylesheet href='assets/game.css'>").unwrap();
    fs::write(
        project.join("assets/game.css"),
        b"body { background: blue; }",
    )
    .unwrap();
    let ledger = WorkspaceLedger::new(root.join("data")).unwrap();
    let result = ledger
        .command(json!({"action":"index","workspace":project}))
        .unwrap();
    let selected = result["files"]
        .as_array()
        .unwrap()
        .iter()
        .find(|file| file["path"] == "שלום game.html")
        .unwrap();
    let browser = FileBrowser::new(ledger);
    let opened = browser
        .open(selected["id"].as_str().unwrap().into(), None)
        .await
        .unwrap();
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let page = client.get(&opened.url).send().await.unwrap();
    assert_eq!(page.status(), StatusCode::OK);
    assert_eq!(page.headers()["content-type"], "text/html");
    assert!(page
        .text()
        .await
        .unwrap()
        .contains("<script>document.body.dataset.running"));
    let base = reqwest::Url::parse(&opened.url).unwrap();
    let css = client
        .get(base.join("assets/game.css").unwrap())
        .send()
        .await
        .unwrap();
    assert_eq!(css.status(), StatusCode::OK);
    assert_eq!(css.text().await.unwrap(), "body { background: blue; }");
    fs::write(project.join("assets/game.css"), b"later unrelated content").unwrap();
    let css = client
        .get(base.join("assets/game.css").unwrap())
        .send()
        .await
        .unwrap();
    assert_eq!(css.text().await.unwrap(), "body { background: blue; }");
    let other = client
        .get(base.join("unrecorded.txt").unwrap())
        .send()
        .await
        .unwrap();
    assert_eq!(other.status(), StatusCode::NOT_FOUND);
    let forged_host = client
        .get(&opened.url)
        .header("host", "untrusted.example")
        .send()
        .await
        .unwrap();
    assert_eq!(forged_host.status(), StatusCode::FORBIDDEN);
    let unknown = client
        .get(format!(
            "{}/{}/assets/game.css",
            base.origin().ascii_serialization(),
            uuid::Uuid::new_v4()
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(unknown.status(), StatusCode::NOT_FOUND);
    let reopened = browser
        .open(selected["id"].as_str().unwrap().into(), None)
        .await
        .unwrap();
    assert_eq!(opened.url, reopened.url);
}

#[tokio::test]
async fn deleted_files_open_the_verified_before_version_and_corrupt_snapshots_fail() {
    let root = std::env::temp_dir().join(format!("opencore-browser-{}", uuid::Uuid::new_v4()));
    let project = root.join("project");
    fs::create_dir_all(&project).unwrap();
    fs::write(project.join("removed.html"), b"<h1>Recorded version</h1>").unwrap();
    let ledger = WorkspaceLedger::new(root.join("data")).unwrap();
    let capture = ledger.begin_turn("chat", "delete", &project).unwrap();
    fs::remove_file(project.join("removed.html")).unwrap();
    let result = ledger.finish_turn(capture, "completed").unwrap();
    let id = result["files"][0]["id"].as_str().unwrap().to_owned();
    let browser = FileBrowser::new(ledger.clone());
    let opened = browser.open(id.clone(), None).await.unwrap();
    let response = reqwest::Client::builder()
        .no_proxy()
        .build()
        .unwrap()
        .get(&opened.url)
        .send()
        .await
        .unwrap();
    assert_eq!(response.text().await.unwrap(), "<h1>Recorded version</h1>");
    assert!(browser
        .open(id.clone(), Some("after".into()))
        .await
        .is_err());
    let hash = result["files"][0]["beforeHash"].as_str().unwrap();
    fs::write(
        root.join("data/workspace-files/objects")
            .join(&hash[..2])
            .join(format!("{hash}.blob")),
        b"corrupted",
    )
    .unwrap();
    assert!(browser.open(id, None).await.is_err());
}

#[tokio::test]
async fn normal_task_opens_unchanged_assets_from_its_capture_after_restart() {
    let root = std::env::temp_dir().join(format!("opencore-browser-{}", uuid::Uuid::new_v4()));
    let project = root.join("project");
    fs::create_dir_all(project.join("assets")).unwrap();
    fs::write(project.join("index.html"), b"<h1>Before</h1>").unwrap();
    fs::write(project.join("assets/game.css"), b"body { color: blue; }").unwrap();
    fs::write(project.join("assets/game.js"), b"window.recorded = true;").unwrap();
    fs::write(
        project.join("assets/image.png"),
        include_bytes!("../icons/32x32.png"),
    )
    .unwrap();
    let ledger = WorkspaceLedger::new(root.join("data")).unwrap();
    let capture = ledger.begin_turn("chat", "edit", &project).unwrap();
    fs::write(
        project.join("index.html"),
        b"<script src='assets/game.js'></script>",
    )
    .unwrap();
    let result = ledger.finish_turn(capture, "completed").unwrap();
    let id = result["files"][0]["id"].as_str().unwrap().to_owned();
    fs::write(project.join("assets/game.js"), b"current unrelated content").unwrap();
    fs::write(project.join("unrecorded.txt"), b"must not be served").unwrap();
    let reopened = WorkspaceLedger::new(root.join("data")).unwrap();
    let browser = FileBrowser::new(reopened);
    let opened = browser.open(id, None).await.unwrap();
    let base = reqwest::Url::parse(&opened.url).unwrap();
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    for (path, expected) in [
        ("assets/game.css", b"body { color: blue; }".as_slice()),
        ("assets/game.js", b"window.recorded = true;".as_slice()),
        (
            "assets/image.png",
            include_bytes!("../icons/32x32.png").as_slice(),
        ),
    ] {
        let response = client.get(base.join(path).unwrap()).send().await.unwrap();
        assert_eq!(response.status(), StatusCode::OK, "{path}");
        assert_eq!(response.bytes().await.unwrap().as_ref(), expected, "{path}");
    }
    assert_eq!(
        client
            .get(base.join("unrecorded.txt").unwrap())
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::NOT_FOUND
    );
}

#[tokio::test]
async fn browser_sessions_use_distinct_origins_and_do_not_share_capture_routes() {
    let root = std::env::temp_dir().join(format!("opencore-browser-{}", uuid::Uuid::new_v4()));
    let project = root.join("project");
    fs::create_dir_all(&project).unwrap();
    fs::write(project.join("first.html"), b"<h1>First</h1>").unwrap();
    fs::write(project.join("second.html"), b"<h1>Second</h1>").unwrap();
    let ledger = WorkspaceLedger::new(root.join("data")).unwrap();
    let result = ledger
        .command(json!({"action":"index","workspace":project}))
        .unwrap();
    let browser = FileBrowser::new(ledger);
    let first = browser
        .open(result["files"][0]["id"].as_str().unwrap().into(), None)
        .await
        .unwrap();
    let second = browser
        .open(result["files"][1]["id"].as_str().unwrap().into(), None)
        .await
        .unwrap();
    let first_url = reqwest::Url::parse(&first.url).unwrap();
    let second_url = reqwest::Url::parse(&second.url).unwrap();
    assert_ne!(first_url.origin(), second_url.origin());
    let forged_capture = first_url.join(second_url.path()).unwrap();
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    assert_eq!(
        client.get(forged_capture).send().await.unwrap().status(),
        StatusCode::NOT_FOUND
    );
    let reopened = browser
        .open(result["files"][0]["id"].as_str().unwrap().into(), None)
        .await
        .unwrap();
    assert_eq!(first.url, reopened.url);
}

#[tokio::test]
async fn dropping_the_browser_releases_its_session_listener() {
    let root = std::env::temp_dir().join(format!("opencore-browser-{}", uuid::Uuid::new_v4()));
    let project = root.join("project");
    fs::create_dir_all(&project).unwrap();
    fs::write(project.join("index.html"), b"<h1>Recorded file</h1>").unwrap();
    let ledger = WorkspaceLedger::new(root.join("data")).unwrap();
    let result = ledger
        .command(json!({"action":"index","workspace":project}))
        .unwrap();
    let browser = FileBrowser::new(ledger);
    let opened = browser
        .open(result["files"][0]["id"].as_str().unwrap().into(), None)
        .await
        .unwrap();
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    assert_eq!(
        client.get(&opened.url).send().await.unwrap().status(),
        StatusCode::OK
    );
    let port = reqwest::Url::parse(&opened.url).unwrap().port().unwrap();
    drop(browser);
    tokio::time::timeout(std::time::Duration::from_secs(2), async {
        while tokio::net::TcpStream::connect(("127.0.0.1", port))
            .await
            .is_ok()
        {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("FileBrowser retained a listening socket after its owner was dropped");
}

#[cfg(windows)]
#[tokio::test]
async fn github_browser_executes_captured_assets_and_blocks_loopback_api_requests() {
    // This native/browser integration gate runs only on Actions; local native execution is prohibited.
    if std::env::var("GITHUB_ACTIONS").as_deref() != Ok("true") {
        return;
    }
    let root = std::env::temp_dir().join(format!("opencore-browser-csp-{}", uuid::Uuid::new_v4()));
    let project = root.join("project");
    fs::create_dir_all(project.join("assets")).unwrap();
    fs::write(project.join("index.html"), b"<h1>Before</h1>").unwrap();
    fs::write(
        project.join("assets/game.css"),
        b"#ready { color: rgb(1, 2, 3); }",
    )
    .unwrap();
    fs::write(
        project.join("assets/game.js"),
        b"document.documentElement.dataset.recordedScript = 'yes';",
    )
    .unwrap();
    fs::write(
        project.join("assets/image.png"),
        include_bytes!("../icons/32x32.png"),
    )
    .unwrap();
    let ledger = WorkspaceLedger::new(root.join("data")).unwrap();
    let capture = ledger.begin_turn("chat", "csp", &project).unwrap();
    fs::write(project.join("index.html"), b"<!doctype html><html><head><link rel='stylesheet' href='assets/game.css'><script src='assets/game.js'></script></head><body><p id='ready'>Recorded page</p><img id='recorded-image' src='assets/image.png'><script>document.documentElement.dataset.inlineScript='yes';localStorage.setItem('capture-only','visible');</script></body></html>").unwrap();
    let result = ledger.finish_turn(capture, "completed").unwrap();
    let browser = FileBrowser::new(ledger.clone());
    let opened = browser
        .open(result["files"][0]["id"].as_str().unwrap().into(), None)
        .await
        .unwrap();
    fs::write(
        project.join("other.html"),
        b"<h1>Other recorded capture</h1>",
    )
    .unwrap();
    let other_capture = ledger
        .command(json!({"action":"index","workspace":project}))
        .unwrap();
    let other_id = other_capture["files"]
        .as_array()
        .unwrap()
        .iter()
        .find(|file| file["path"] == "other.html")
        .unwrap()["id"]
        .as_str()
        .unwrap();
    let other = browser.open(other_id.into(), None).await.unwrap();
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../scripts/test-file-browser-confinement.mjs");
    let output = tauri::async_runtime::spawn_blocking(move || {
        std::process::Command::new("node")
            .arg(script)
            .arg(opened.url)
            .arg(other.url)
            .output()
            .unwrap()
    })
    .await
    .unwrap();
    assert!(
        output.status.success(),
        "Browser confinement regression failed:\n{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    println!("{}", String::from_utf8_lossy(&output.stdout));
}
