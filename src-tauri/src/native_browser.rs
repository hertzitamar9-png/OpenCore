use serde_json::{json, Value};
use tauri::{Emitter, LogicalPosition, LogicalSize, Manager, Position, Rect, Size, WebviewUrl};

const LABEL: &str = "opencore-native-browser";

fn browser_key(input: &str) -> Result<&'static str, String> {
    match input.trim().to_ascii_lowercase().as_str() {
        "enter" | "return" => Ok("Enter"),
        "escape" | "esc" => Ok("Escape"),
        "tab" => Ok("Tab"),
        "left" | "arrowleft" => Ok("ArrowLeft"),
        "right" | "arrowright" => Ok("ArrowRight"),
        "up" | "arrowup" => Ok("ArrowUp"),
        "down" | "arrowdown" => Ok("ArrowDown"),
        "pagedown" => Ok("PageDown"),
        "pageup" => Ok("PageUp"),
        "space" | "spacebar" => Ok(" "),
        _ => Err("Unsupported browser key".into()),
    }
}

fn destination(input: &str) -> Result<tauri::Url, String> {
    let input = input.trim();
    if input.is_empty() { return Err("Enter a website address".into()); }
    let address = if input.starts_with("http://") || input.starts_with("https://") {
        input.to_string()
    } else if input.starts_with("localhost:") || input.starts_with("127.0.0.1:") {
        format!("http://{input}")
    } else if input.contains('.') && !input.contains(' ') {
        format!("https://{input}")
    } else {
        return Err("Enter a URL, such as google.com. Search on Google after opening it".into());
    };
    let url: tauri::Url = address.parse().map_err(|_| "Invalid browser address".to_string())?;
    if !matches!(url.scheme(), "http" | "https") { return Err("Only HTTP and HTTPS pages can open here".into()); }
    Ok(url)
}

fn webview_label(args: &Value) -> Result<String, String> {
    let Some(tab_id) = args.get("tabId").and_then(Value::as_str) else {
        return Ok(LABEL.to_string());
    };
    if tab_id.is_empty() || tab_id.len() > 64
        || !tab_id.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        return Err("Invalid in-app browser tab id".into());
    }
    if tab_id == "default" {
        return Ok(LABEL.to_string());
    }
    Ok(format!("{LABEL}-{tab_id}"))
}

pub(crate) fn command(app: &tauri::AppHandle, action: &str, args: &Value) -> Result<Value, String> {
    let label = webview_label(args)?;
    if action == "navigate" && app.get_webview(&label).is_none() {
        return command(app, "open", args);
    }
    if action == "open" {
        if app.get_webview(&label).is_none() {
            let window = app.get_window("main").ok_or("The OpenCore window is unavailable")?;
            let url = destination(args.get("url").and_then(Value::as_str).unwrap_or("https://www.google.com"))?;
            let builder = tauri::webview::WebviewBuilder::new(label.clone(), WebviewUrl::External(url)).devtools(false);
            window.add_child(builder, LogicalPosition::new(0.0, 0.0), LogicalSize::new(1.0, 1.0)).map_err(|e| e.to_string())?;
        } else if let Some(address) = args.get("url").and_then(Value::as_str) {
            app.get_webview(&label).ok_or("The browser closed while opening")?
                .navigate(destination(address)?).map_err(|e| e.to_string())?;
        }
        let _ = app.emit("opencore-open-native-browser", ());
    }
    let Some(view) = app.get_webview(&label) else {
        if action == "status" || action == "close" { return Ok(json!({"open":false})); }
        return Err("Open the in-app browser first".into());
    };
    match action {
        "open" | "status" => Ok(json!({"open":true,"url":view.url().map(|url| url.to_string()).unwrap_or_default()})),
        "navigate" => {
            let url = destination(args.get("url").and_then(Value::as_str).unwrap_or_default())?;
            view.navigate(url.clone()).map_err(|e| e.to_string())?;
            Ok(json!({"open":true,"url":url.to_string()}))
        }
        "back" => { view.eval("history.back()").map_err(|e| e.to_string())?; Ok(json!({"open":true})) }
        "forward" => { view.eval("history.forward()").map_err(|e| e.to_string())?; Ok(json!({"open":true})) }
        "reload" => { view.reload().map_err(|e| e.to_string())?; Ok(json!({"open":true})) }
        "bounds" => {
            let number = |key: &str| args.get(key).and_then(Value::as_f64).unwrap_or(0.0);
            let (x, y, width, height) = (number("x"), number("y"), number("width"), number("height"));
            if ![x, y, width, height].iter().all(|value| value.is_finite()) || x < 0.0 || y < 0.0 || width < 1.0 || height < 1.0 {
                return Err("Invalid in-app browser bounds".into());
            }
            view.set_bounds(Rect { position: Position::Logical(LogicalPosition::new(x, y)), size: Size::Logical(LogicalSize::new(width, height)) }).map_err(|e| e.to_string())?;
            Ok(json!({"open":true}))
        }
        "hide" => { view.hide().map_err(|e| e.to_string())?; Ok(json!({"open":true})) }
        "show" => { view.show().map_err(|e| e.to_string())?; Ok(json!({"open":true})) }
        "close" => { view.close().map_err(|e| e.to_string())?; Ok(json!({"open":false})) }
        _ => Err("Unsupported in-app browser action".into()),
    }
}

pub(crate) async fn agent_command(app: &tauri::AppHandle, action: &str, args: &Value) -> Result<Value, String> {
    if matches!(action, "open" | "navigate") {
        let requested = destination(args.get("url").and_then(Value::as_str).unwrap_or("https://www.google.com"))?;
        command(app, action, args)?;
        for _ in 0..60 {
            let view = app.get_webview(LABEL).ok_or("OpenCore Browser closed during navigation")?;
            if view.url().is_ok_and(|url| url == requested) {
                // The URL changes before the page's controls are ready for inspection.
                tokio::time::sleep(std::time::Duration::from_millis(200)).await;
                return Ok(json!({"open":true,"url":requested.to_string()}));
            }
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        }
        return Err(format!("OpenCore Browser did not navigate to {requested}"));
    }
    if matches!(action, "back" | "forward" | "reload" | "status") {
        return command(app, action, args);
    }
    let view = app.get_webview(LABEL).ok_or("Open the in-app browser first")?;
    let script = match action {
        "inspect" | "read_screen" => r#"(() => ({title:document.title,url:location.href,text:(document.body?.innerText||'').slice(0,12000),controls:[...document.querySelectorAll('a,button,input,textarea,select,[role="button"]')].map(el=>{const r=el.getBoundingClientRect();return {tag:el.tagName.toLowerCase(),text:(el.innerText||el.getAttribute('aria-label')||el.getAttribute('placeholder')||'').slice(0,120),x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),visible:r.width>0&&r.height>0&&r.x+r.width/2>=0&&r.y+r.height/2>=0&&r.x+r.width/2<innerWidth&&r.y+r.height/2<innerHeight}}).filter(item=>item.visible).slice(0,100).map(({visible,...item})=>item)}))()"#.to_string(),
        "click" => {
            let (x, y) = coordinates(args)?;
            format!(r#"(() => {{ if({x}>=innerWidth||{y}>=innerHeight)return {{clicked:false,error:'Coordinates are outside the visible browser viewport. Scroll and inspect again.'}}; const el=document.elementFromPoint({x},{y}); if(!el)return {{clicked:false,error:'No visible element at these coordinates'}}; el.focus(); el.click(); return {{clicked:true,tag:el.tagName.toLowerCase(),text:(el.innerText||el.getAttribute('aria-label')||'').slice(0,120)}}; }})()"#)
        }
        "click_text" => {
            let label = args.get("text").and_then(Value::as_str).ok_or("Visible control text is required")?.trim();
            if label.is_empty() || label.len() > 120 { return Err("Choose a visible control label under 120 characters".into()); }
            let literal = json!(label).to_string();
            format!(r#"(() => {{ const wanted={literal}.toLocaleLowerCase(); const controls=[...document.querySelectorAll('button,a,[role="button"],input[type="submit"]')].filter(el=>{{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&r.x+r.width/2>=0&&r.y+r.height/2>=0&&r.x+r.width/2<innerWidth&&r.y+r.height/2<innerHeight}}); const name=el=>(el.innerText||el.getAttribute('aria-label')||el.getAttribute('value')||'').trim(); const el=controls.find(el=>name(el).toLocaleLowerCase()===wanted)||controls.find(el=>name(el).toLocaleLowerCase().includes(wanted)); if(!el)return {{clicked:false,error:'No matching visible control. Scroll and inspect again.'}}; el.focus(); el.click(); return {{clicked:true,tag:el.tagName.toLowerCase(),text:name(el).slice(0,120)}}; }})()"#)
        }
        "type" => {
            let text = args.get("text").and_then(Value::as_str).ok_or("Text is required")?;
            if text.len() > 10_000 { return Err("Browser typing is limited to 10,000 characters".into()); }
            let literal = json!(text).to_string();
            let focus = if args.get("x").is_some() { let (x,y) = coordinates(args)?; format!("document.elementFromPoint({x},{y})?.focus();") } else { String::new() };
            format!(r#"(() => {{ {focus} const el=document.activeElement; if(!el)return {{typed:false}}; const value={literal}; if(el instanceof HTMLInputElement||el instanceof HTMLTextAreaElement){{ const proto=el instanceof HTMLInputElement?HTMLInputElement.prototype:HTMLTextAreaElement.prototype; Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value); el.dispatchEvent(new InputEvent('input',{{bubbles:true,data:value,inputType:'insertText'}})); el.dispatchEvent(new Event('change',{{bubbles:true}})); return {{typed:true}}; }} if(el.isContentEditable){{el.textContent=value;el.dispatchEvent(new InputEvent('input',{{bubbles:true,data:value,inputType:'insertText'}}));return {{typed:true}};}}return {{typed:false,reason:'Focus an editable field first'}}; }})()"#)
        }
        "scroll" => {
            let delta = args.get("deltaY").and_then(Value::as_f64).unwrap_or(600.0).clamp(-3000.0, 3000.0);
            format!("(() => {{ window.scrollBy(0,{delta}); return {{scrolled:true,y:window.scrollY}}; }})()")
        }
        "key" | "commit_enter" => {
            let key = browser_key(if action == "commit_enter" { "Enter" } else { args.get("key").and_then(Value::as_str).unwrap_or("Enter") })?;
            let literal = json!(key).to_string();
            format!(r#"(() => {{ const key={literal}; const el=document.activeElement||document.body; if(key==='Enter'&&el?.form){{el.form.requestSubmit();return {{key}};}} if(key==='PageDown')window.scrollBy(0,innerHeight*.85);else if(key==='PageUp')window.scrollBy(0,-innerHeight*.85);else {{el?.dispatchEvent(new KeyboardEvent('keydown',{{key,bubbles:true,cancelable:true}}));el?.dispatchEvent(new KeyboardEvent('keyup',{{key,bubbles:true,cancelable:true}}));}}return {{key}}; }})()"#)
        }
        "screenshot" => {
            let listed = crate::windows_control::command("list".into(), json!({})).await?;
            let window_id = listed["windows"].as_array().and_then(|windows| windows.iter().find(|window|
                window["title"].as_str().is_some_and(|title| title == "OpenCore")))
                .and_then(|window| window["windowId"].as_i64())
                .ok_or("OpenCore Browser window is not visible")?;
            return crate::windows_control::command("screenshot".into(), json!({"windowId":window_id})).await;
        }
        _ => return Err("Unsupported in-app browser action".into()),
    };
    let (sender, receiver) = tokio::sync::oneshot::channel();
    let sender = std::sync::Mutex::new(Some(sender));
    view.eval_with_callback(script, move |result| { if let Ok(mut sender) = sender.lock() { if let Some(sender) = sender.take() { let _ = sender.send(result); } } }).map_err(|e| e.to_string())?;
    let raw = tokio::time::timeout(std::time::Duration::from_secs(8), receiver).await
        .map_err(|_| "The browser page did not respond".to_string())?
        .map_err(|_| "The browser page response was lost".to_string())?;
    let mut result: Value = serde_json::from_str(&raw).map_err(|e| format!("Browser page returned invalid data: {e}"))?;
    if matches!(action, "click" | "click_text") && result["clicked"] == true {
        // Many buttons finish an asynchronous update after click() returns.
        // Include the settled page state so the model needn't reload and lose it.
        tokio::time::sleep(std::time::Duration::from_millis(300)).await;
        let (sender, receiver) = tokio::sync::oneshot::channel();
        let sender = std::sync::Mutex::new(Some(sender));
        if view.eval_with_callback("(() => ({url:location.href,text:(document.body?.innerText||'').slice(-1200)}))()", move |value| {
            if let Ok(mut sender) = sender.lock() { if let Some(sender) = sender.take() { let _ = sender.send(value); } }
        }).is_ok() {
            if let Ok(Ok(raw)) = tokio::time::timeout(std::time::Duration::from_secs(2), receiver).await {
                if let Ok(after) = serde_json::from_str::<Value>(&raw) { result["after"] = after; }
            }
        }
    }
    Ok(result)
}

fn coordinates(args: &Value) -> Result<(i32, i32), String> {
    let (x,y) = (args.get("x").and_then(Value::as_i64), args.get("y").and_then(Value::as_i64));
    match (x,y) { (Some(x),Some(y)) if (0..=100_000).contains(&x) && (0..=100_000).contains(&y) => Ok((x as i32,y as i32)), _ => Err("Valid browser x and y coordinates are required".into()) }
}

#[cfg(test)]
mod tests {
    use super::{browser_key, destination, webview_label, LABEL};
    use serde_json::json;
    #[test]
    fn browser_address_accepts_sites_and_search_terms() {
        assert_eq!(destination("example.com").unwrap().as_str(), "https://example.com/");
        assert_eq!(destination("localhost:8787").unwrap().as_str(), "http://localhost:8787/");
        assert!(destination("venom style ui").is_err());
        assert!(destination("javascript:alert(1)").is_err());
    }

    #[test]
    fn browser_keys_accept_model_spelling_and_game_directions() {
        assert_eq!(browser_key("enter").unwrap(), "Enter");
        assert_eq!(browser_key("ARROWLEFT").unwrap(), "ArrowLeft");
        assert_eq!(browser_key("up").unwrap(), "ArrowUp");
        assert_eq!(browser_key("space").unwrap(), " ");
        assert!(browser_key("Control+L").is_err());
    }

    #[test]
    fn browser_tabs_get_distinct_safe_webview_labels() {
        assert_eq!(webview_label(&json!({})).unwrap(), LABEL);
        assert_eq!(webview_label(&json!({"tabId":"default"})).unwrap(), LABEL);
        assert_eq!(webview_label(&json!({"tabId":"91e8b7b2-9088-47f3-99ab-a362ae4e1791"})).unwrap(),
            format!("{LABEL}-91e8b7b2-9088-47f3-99ab-a362ae4e1791"));
        assert!(webview_label(&json!({"tabId":"../../main"})).is_err());
    }
}
