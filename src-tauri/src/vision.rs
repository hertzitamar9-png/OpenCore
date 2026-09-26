//! OpenCore Reflex Vision: the computer-use model that sees the screen. It is H Company's
//! Holo3.1-0.8B (0.85B parameters, Apache-2.0, built on Qwen3.5-0.8B), stored with
//! OpenCore's weights under `reflex\vision` and run by the same llama-server as the main
//! model. It is never loaded with the main model: the first computer-use call that needs
//! to see a window starts it, llama-server unloads the weights after `IDLE_SLEEP_SECONDS`
//! without requests (they reload in about 2 s), and the process ends with the app.

use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const PORT: u16 = 8816;
const IDLE_SLEEP_SECONDS: u32 = 120;
pub const MODEL_FILE: &str = "reflex-vision-0.8b-q8_0.gguf";
pub const PROJECTOR_FILE: &str = "reflex-vision-0.8b-mmproj-f16.gguf";

pub struct VisionManager {
    child: Mutex<Option<Child>>,
    starting: tokio::sync::Mutex<()>,
    install_root: PathBuf,
    resource_root: Option<PathBuf>,
}

/// A window image and where its top-left pixel sits in the window's own coordinates
/// (the capture skips the invisible resize border that click coordinates include).
pub struct Frame {
    pub data_url: String,
    pub width: u32,
    pub height: u32,
    pub origin: (i64, i64),
}

impl VisionManager {
    pub fn new(install_root: PathBuf, resource_root: Option<PathBuf>) -> Self {
        Self { child: Mutex::new(None), starting: tokio::sync::Mutex::new(()), install_root, resource_root }
    }

    fn files(&self) -> Result<[PathBuf; 3], String> {
        let dir = self.install_root.join("reflex").join("vision");
        let files = [self.install_root.join("runtime").join("llama-server.exe"),
                     dir.join(MODEL_FILE), dir.join(PROJECTOR_FILE)];
        match files.iter().find(|path| !path.is_file()) {
            Some(missing) => Err(format!("OpenCore Reflex Vision is not installed: {} is missing", missing.display())),
            None => Ok(files),
        }
    }

    fn client(timeout: Duration) -> Result<reqwest::Client, String> {
        reqwest::Client::builder().no_proxy().timeout(timeout).build().map_err(|error| error.to_string())
    }

    async fn healthy() -> bool {
        let Ok(client) = Self::client(Duration::from_secs(2)) else { return false };
        match client.get(format!("http://127.0.0.1:{PORT}/health")).send().await {
            Ok(response) => response.status().is_success(),
            Err(_) => false,
        }
    }

    fn running(&self) -> bool {
        self.child.lock().ok().is_some_and(|mut child| {
            child.as_mut().is_some_and(|process| process.try_wait().ok().flatten().is_none())
        })
    }

    /// Start the vision server if needed. While it sleeps it still answers health checks,
    /// and the next request wakes it, so a sleeping server counts as running.
    pub async fn ensure_running(&self) -> Result<(), String> {
        crate::model_catalog::require_idle()?;
        let _one_start = self.starting.lock().await;
        if self.running() && Self::healthy().await { return Ok(()); }
        let [server, model, projector] = self.files()?;
        if !self.running() {
            let mut command = Command::new(&server);
            crate::child_guard::inference_dependencies(&mut command, self.resource_root.as_deref());
            command.args(["-m", model.to_string_lossy().as_ref(), "--mmproj", projector.to_string_lossy().as_ref()])
                .args(["--host", "127.0.0.1", "--port", &PORT.to_string()])
                // Full detail up to a 4K window (8192 image tokens), the setting that scored
                // 44/100 on ScreenSpot-Pro; the default 4096 cap would halve a 4K screen.
                .args(["-ngl", "99", "-c", "12288", "--image-max-tokens", "8192", "-np", "1", "--flash-attn", "on"])
                .args(["--no-warmup", "--reasoning", "off", "--no-webui"])
                .args(["--sleep-idle-seconds", &IDLE_SLEEP_SECONDS.to_string()])
                .current_dir(server.parent().unwrap_or(&self.install_root))
                .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
            #[cfg(windows)]
            command.creation_flags(0x0800_0000);
            let process = command.spawn().map_err(|error| format!("Could not start OpenCore Reflex Vision: {error}"))?;
            crate::child_guard::adopt(&process);
            *self.child.lock().map_err(|error| error.to_string())? = Some(process);
        }
        let deadline = Instant::now() + Duration::from_secs(90);
        while Instant::now() < deadline {
            if Self::healthy().await { return Ok(()); }
            if let Ok(mut child) = self.child.lock() {
                if let Some(Ok(Some(status))) = child.as_mut().map(|process| process.try_wait()) {
                    *child = None;
                    return Err(format!("OpenCore Reflex Vision exited during startup ({status})"));
                }
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        Err("OpenCore Reflex Vision did not become ready within 90 seconds".into())
    }

    async fn chat(&self, frame: &Frame, text: &str, max_tokens: u32, format: Option<Value>) -> Result<(String, f64), String> {
        self.ensure_running().await?;
        let mut body = json!({
            "messages":[{"role":"user","content":[
                {"type":"image_url","image_url":{"url":frame.data_url}},
                {"type":"text","text":text}]}],
            "temperature":0, "max_tokens":max_tokens,
            "chat_template_kwargs":{"enable_thinking":false}
        });
        if let Some(format) = format { body["response_format"] = format; }
        let started = Instant::now();
        let response = Self::client(Duration::from_secs(120))?
            .post(format!("http://127.0.0.1:{PORT}/v1/chat/completions")).json(&body).send().await
            .map_err(|error| format!("OpenCore Reflex Vision request failed: {error}"))?;
        let status = response.status();
        let value = response.json::<Value>().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(value.pointer("/error/message").and_then(Value::as_str).map(str::to_string)
                .unwrap_or_else(|| format!("OpenCore Reflex Vision returned {status}")));
        }
        let content = value.pointer("/choices/0/message/content").and_then(Value::as_str).unwrap_or_default();
        Ok((content.trim().to_string(), started.elapsed().as_secs_f64() * 1000.0))
    }

    /// Window-relative point for a described target, or None when the model gives none.
    pub async fn locate(&self, frame: &Frame, target: &str) -> Result<Value, String> {
        let format = json!({"type":"json_schema","json_schema":{"name":"point","schema":point_schema()}});
        let (answer, ms) = self.chat(frame, &locate_prompt(target), 24, Some(format)).await?;
        Ok(match parse_point(&answer).and_then(|point| to_window(point, frame)) {
            Some((x, y)) => json!({"found":true,"x":x,"y":y,"inference_ms":ms.round()}),
            None => json!({"found":false,"reason":"The vision model did not return a point","raw":answer,
                           "inference_ms":ms.round()}),
        })
    }

    /// A short answer about what the window currently shows.
    pub async fn ask(&self, frame: &Frame, question: &str) -> Result<Value, String> {
        let prompt = format!("{question}\nAnswer from what is visible in this screenshot only, briefly and concretely. \
                              Say so if something is not visible.");
        let (answer, ms) = self.chat(frame, &prompt, 400, None).await?;
        Ok(json!({"answer":answer,"inference_ms":ms.round()}))
    }

    pub fn stop(&self) {
        if let Ok(mut child) = self.child.lock() {
            if let Some(mut process) = child.take() {
                let _ = process.kill();
                let _ = process.wait();
            }
        }
    }
}

impl Drop for VisionManager {
    fn drop(&mut self) { self.stop(); }
}

fn point_schema() -> Value {
    let axis = |name: &str| json!({"description":format!("{name} coordinate as integer in [0, 1000]"),
        "maximum":1000,"minimum":0,"title":name,"type":"integer"});
    json!({"properties":{"x":axis("X"),"y":axis("Y")},"required":["x","y"],"title":"VisualLocalizerOutput","type":"object"})
}

/// H Company's localization prompt for Holo3/Holo3.1; the schema is printed the way
/// Python prints the dict, as in their documentation.
fn locate_prompt(target: &str) -> String {
    let schema = "{'properties': {'x': {'description': 'X coordinate as integer in [0, 1000]', 'maximum': 1000, \
'minimum': 0, 'title': 'X', 'type': 'integer'}, 'y': {'description': 'Y coordinate as integer in [0, 1000]', \
'maximum': 1000, 'minimum': 0, 'title': 'Y', 'type': 'integer'}}, 'required': ['x', 'y'], \
'title': 'VisualLocalizerOutput', 'type': 'object'}";
    format!("Localize an element on the GUI image according to the provided target and output a click position.\n \
* You must output a valid JSON following the format: {schema}\n Your target is:\n{}", target.trim())
}

/// The model answers {"x": int, "y": int} on a 0-1000 grid over the whole image.
fn parse_point(answer: &str) -> Option<(f64, f64)> {
    let value: Value = serde_json::from_str(answer.trim()).ok()?;
    let x = value.get("x")?.as_f64()?;
    let y = value.get("y")?.as_f64()?;
    ((0.0..=1000.0).contains(&x) && (0.0..=1000.0).contains(&y)).then_some((x / 1000.0, y / 1000.0))
}

fn to_window((nx, ny): (f64, f64), frame: &Frame) -> Option<(i64, i64)> {
    if frame.width == 0 || frame.height == 0 { return None; }
    let x = ((nx * f64::from(frame.width)).round() as i64).min(i64::from(frame.width) - 1);
    let y = ((ny * f64::from(frame.height)).round() as i64).min(i64::from(frame.height) - 1);
    Some((x + frame.origin.0, y + frame.origin.1))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(width: u32, height: u32, origin: (i64, i64)) -> Frame {
        Frame { data_url: String::new(), width, height, origin }
    }

    #[test]
    fn points_map_from_the_model_grid_to_window_coordinates() {
        assert_eq!(parse_point("{\"x\": 500, \"y\": 250}"), Some((0.5, 0.25)));
        assert_eq!(parse_point("{\"x\": 1200, \"y\": 5}"), None);
        assert_eq!(parse_point("Click(10, 20)"), None);
        // The capture starts 7 px right of the window rectangle's left edge.
        assert_eq!(to_window((0.5, 0.25), &frame(1290, 844, (7, 0))), Some((652, 211)));
        assert_eq!(to_window((1.0, 1.0), &frame(100, 50, (0, 0))), Some((99, 49)));
    }

    /// Grounds named controls in the windows that are open right now and checks each point
    /// against the control's accessibility bounds. Nothing is clicked.
    /// cargo test --lib vision::tests::live -- --ignored --nocapture
    #[cfg(windows)]
    #[tokio::test]
    #[ignore = "needs open windows and the installed Reflex Vision weights"]
    async fn live_grounding_matches_accessibility_bounds() {
        let home = PathBuf::from(std::env::var_os("USERPROFILE").unwrap()).join("OpenCore");
        let vision = VisionManager::new(home, None);
        let listed = crate::windows_control::command("list".into(), json!({})).await.unwrap();
        let kinds = [("Button", "button"), ("MenuItem", "menu item"), ("TabItem", "tab"), ("ListItem", "list item"),
                     ("Hyperlink", "link"), ("CheckBox", "checkbox"), ("RadioButton", "option"), ("TreeItem", "tree item"),
                     ("SplitButton", "button"), ("ComboBox", "drop-down"), ("Edit", "text field")];
        let (mut hits, mut total) = (0, 0);
        for window in listed["windows"].as_array().unwrap() {
            let id = window["windowId"].as_i64().unwrap_or(0);
            let bounds = &window["bounds"];
            let (left, top) = (bounds["left"].as_i64().unwrap_or(-1), bounds["top"].as_i64().unwrap_or(-1));
            let (width, height) = (bounds["width"].as_i64().unwrap_or(0), bounds["height"].as_i64().unwrap_or(0));
            if id == 0 || left < -100 || width < 300 || height < 200 { continue; }
            let Ok(inspected) = crate::windows_control::command("inspect".into(), json!({"windowId":id})).await else { continue };
            let Ok(frame) = crate::vision_frame(id).await else { continue };
            let mut targets = Vec::new();
            for row in inspected["elements"].as_array().unwrap().iter().skip(1) {
                let name = row["name"].as_str().unwrap_or_default().trim().to_string();
                let Some((_, kind)) = kinds.iter().find(|(kind, _)| row["controlType"] == *kind) else { continue };
                let b = &row["bounds"];
                let (x, y) = (b["left"].as_i64().unwrap_or(-1) - left, b["top"].as_i64().unwrap_or(-1) - top);
                let (w, h) = (b["width"].as_i64().unwrap_or(0), b["height"].as_i64().unwrap_or(0));
                let named = name.chars().count() >= 2 && name.chars().count() <= 40;
                let inside = x >= 0 && y >= 0 && x + w <= width && y + h <= height && w >= 8 && h >= 8;
                let fresh = !targets.iter().any(|(other, ..): &(String, &str, (i64, i64, i64, i64))| other == &name);
                if named && inside && fresh { targets.push((name, *kind, (x, y, w, h))); }
                if targets.len() == 5 { break; }
            }
            let title: String = window["title"].as_str().unwrap_or_default().chars().take(40).collect();
            println!("{title} | frame {}x{} origin {:?} | window {width}x{height}", frame.width, frame.height, frame.origin);
            for (name, kind, (x, y, w, h)) in targets {
                let result = vision.locate(&frame, &format!("the \"{name}\" {kind}")).await.unwrap();
                let (px, py) = (result["x"].as_i64().unwrap_or(-1), result["y"].as_i64().unwrap_or(-1));
                let hit = px >= x && px <= x + w && py >= y && py <= y + h;
                hits += usize::from(hit);
                total += 1;
                println!("   {} {kind} {name:?} box=({x},{y},{w},{h}) point=({px},{py}) {}ms",
                         if hit { "HIT " } else { "MISS" }, result["inference_ms"]);
            }
        }
        println!("{hits}/{total} named controls grounded inside their bounds");
        assert!(total > 0, "no named controls were found in open windows");
    }

    /// Full ground-and-click path on reflex/fixtures/click_targets.html, open in a browser
    /// app window titled "Click test - ready". Each click writes what it hit into the title.
    /// cargo test --lib vision::tests::live_clicks -- --ignored --nocapture --test-threads=1
    #[cfg(windows)]
    #[tokio::test]
    #[ignore = "needs the click test page open and the installed Reflex Vision weights"]
    async fn live_clicks_land_on_the_described_control() {
        let home = PathBuf::from(std::env::var_os("USERPROFILE").unwrap()).join("OpenCore");
        let vision = VisionManager::new(home, None);
        // Hebrew Windows wraps window titles in bidi embedding marks.
        let plain = |title: &str| title.chars()
            .filter(|c| !matches!(c, '\u{200e}' | '\u{200f}' | '\u{202a}'..='\u{202e}')).collect::<String>();
        let title_of = |listed: &Value, id: i64| plain(listed["windows"].as_array().unwrap().iter()
            .find(|w| w["windowId"].as_i64() == Some(id)).and_then(|w| w["title"].as_str()).unwrap_or_default());
        let listed = crate::windows_control::command("list".into(), json!({})).await.unwrap();
        let id = listed["windows"].as_array().unwrap().iter()
            .find(|w| w["title"].as_str().is_some_and(|t| plain(t).starts_with("Click test")))
            .and_then(|w| w["windowId"].as_i64()).expect("open reflex/fixtures/click_targets.html first");
        let targets = [("the Search icon button", "Search"), ("the settings gear button", "Settings"),
                       ("the notifications bell", "Notifications"), ("ההזמנות שלי", "ההזמנות שלי"),
                       ("חשבוניות", "חשבוניות"), ("the \"שמירה\" button", "שמירה"), ("Export CSV", "Export CSV"),
                       ("the \"Remember this device\" checkbox", "Remember this device"),
                       ("the \"חיפוש הזמנה\" text field", "חיפוש הזמנה"), ("צור קשר", "צור קשר"),
                       ("the Delete draft button", "Delete draft"), ("Account details link", "Account details")];
        let mut hits = 0;
        for (goal, expected) in targets {
            let frame = crate::vision_frame(id).await.unwrap();
            let point = vision.locate(&frame, goal).await.unwrap();
            let clicked = crate::windows_control::command("click".into(),
                json!({"windowId":id,"x":point["x"],"y":point["y"]})).await;
            tokio::time::sleep(Duration::from_millis(500)).await;
            let listed = crate::windows_control::command("list".into(), json!({})).await.unwrap();
            let title = title_of(&listed, id);
            let hit = title.ends_with(&format!("- {expected}"));
            hits += usize::from(hit);
            println!("{} {goal:?} -> ({}, {}) {}ms click={} title={title:?}", if hit { "HIT " } else { "MISS" },
                     point["x"], point["y"], point["inference_ms"], clicked.is_ok());
        }
        println!("{hits}/{} clicks landed on the described control", targets.len());
        assert!(hits * 2 >= targets.len(), "most clicks missed: check capture origin and click mapping");
    }

    #[test]
    fn prompt_and_schema_match_the_documented_format() {
        let prompt = locate_prompt("  the Save button ");
        assert!(prompt.starts_with("Localize an element on the GUI image according to the provided target and output a click position.\n * You must output a valid JSON following the format: {'properties': {'x': {'description': 'X coordinate"));
        assert!(prompt.ends_with(" Your target is:\nthe Save button"));
        let schema = point_schema();
        assert_eq!(schema["required"], json!(["x", "y"]));
        assert_eq!(schema["properties"]["y"]["maximum"], json!(1000));
    }
}
