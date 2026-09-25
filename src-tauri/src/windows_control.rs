use base64::Engine;
use serde_json::{json, Value};

pub(crate) fn validate_action(action: &str, args: &Value) -> Result<(), String> {
    if !matches!(action, "list" | "inspect" | "screenshot" | "read_screen" | "invoke" | "set_value" | "move" | "click" | "drag" | "type" | "key" | "scroll" | "interact" | "set_at" | "commit_enter" | "commit_text" | "navigate_url") {
        return Err("Unsupported desktop action".into());
    }
    if action != "list" && args.get("windowId").and_then(Value::as_i64).is_none_or(|id| id < 0) {
        return Err("Select a window, or use windowId 0 for the whole desktop".into());
    }
    if matches!(action, "move" | "click" | "drag" | "interact" | "set_at" | "commit_enter" | "commit_text") {
        for field in ["x", "y"] {
            if args.get(field).and_then(Value::as_f64).is_none_or(|n| !n.is_finite() || !(0.0..=10000.0).contains(&n)) {
                return Err("Desktop coordinates are out of bounds".into());
            }
        }
    }
    if action == "drag" {
        for field in ["toX", "toY"] {
            if args.get(field).and_then(Value::as_f64).is_none_or(|n| !n.is_finite() || !(0.0..=10000.0).contains(&n)) {
                return Err("Drag destination coordinates are out of bounds".into());
            }
        }
        if args["windowId"].as_i64() == Some(0) {
            return Err("Choose an application window for dragging".into());
        }
    }
    if matches!(action, "type" | "set_at" | "commit_text") && args.get("text").and_then(Value::as_str).is_none_or(|s| s.len() > 4000) {
        return Err("Text must be at most 4000 characters".into());
    }
    if matches!(action, "interact" | "set_at" | "commit_enter" | "commit_text") && args["windowId"].as_i64() == Some(0) {
        return Err("Choose an application window for background interaction".into());
    }
    if matches!(action, "invoke" | "set_value") && args.get("elementId").and_then(Value::as_u64).is_none_or(|id| id >= 160) {
        return Err("Use an elementId returned by inspect".into());
    }
    if action == "set_value" && args.get("text").and_then(Value::as_str).is_none_or(|s| s.len() > 4000) {
        return Err("Text must be at most 4000 characters".into());
    }
    if action == "key" && !matches!(args.get("key").and_then(Value::as_str), Some("Enter" | "Tab" | "Escape" | "Backspace" | "ArrowUp" | "ArrowDown" | "ArrowLeft" | "ArrowRight" | "PageUp" | "PageDown" | "Ctrl+L")) {
        return Err("Unsupported desktop key".into());
    }
    if action == "navigate_url" {
        let address = args.get("url").and_then(Value::as_str).ok_or("A URL is required")?;
        if address.len() > 2048 || !tauri::Url::parse(address).is_ok_and(|url| matches!(url.scheme(), "http" | "https")) {
            return Err("Enter a valid HTTP or HTTPS URL".into());
        }
    }
    if action == "scroll" && !matches!(args.get("direction").and_then(Value::as_str), Some("up" | "down")) {
        return Err("Unsupported scroll direction".into());
    }
    Ok(())
}

#[cfg(windows)]
mod platform {
    use super::*;
    use uiautomation::inputs::{Keyboard, Mouse, MouseButton};
    use uiautomation::types::Point;
    use uiautomation::{UIAutomation, UIElement};

    fn windows(automation: &UIAutomation) -> Result<Vec<UIElement>, String> {
        let root = automation.get_root_element().map_err(|e| e.to_string())?;
        let walker = automation.get_control_view_walker().map_err(|e| e.to_string())?;
        Ok(walker.get_children(&root).unwrap_or_default().into_iter()
            .filter(|element| element.get_name().is_ok_and(|name| !name.trim().is_empty()))
            .take(100).collect())
    }

    fn rect_json(element: &UIElement) -> Value {
        element.get_bounding_rectangle().map(|rect| json!({
            "left":rect.get_left(),"top":rect.get_top(),
            "width":rect.get_width(),"height":rect.get_height()
        })).unwrap_or(Value::Null)
    }

    fn window_by_id(automation: &UIAutomation, id: isize) -> Result<UIElement, String> {
        windows(automation)?.into_iter().find(|window| {
            window.get_native_window_handle().ok().map(Into::<isize>::into) == Some(id)
        }).ok_or("Window is no longer available; choose a window again".into())
    }

    fn window_point(window: &UIElement, args: &Value) -> Result<Point, String> {
        let rect = window.get_bounding_rectangle().map_err(|e| e.to_string())?;
        let x = args["x"].as_f64().unwrap_or(-1.0);
        let y = args["y"].as_f64().unwrap_or(-1.0);
        let width = rect.get_width();
        let height = rect.get_height();
        if width <= 0 || height <= 0 || x >= f64::from(width) || y >= f64::from(height) {
            return Err(format!("Pointer ({x}, {y}) is outside this {width}x{height} window. Use inspect again and copy its window-relative x,y center; screen coordinates are not accepted."));
        }
        Ok(Point::new(rect.get_left() + x.round() as i32, rect.get_top() + y.round() as i32))
    }

    fn inspect_tree(automation: &UIAutomation, window: &UIElement) -> Value {
        let Ok(walker) = automation.get_control_view_walker() else { return json!([]) };
        let mut rows = Vec::new();
        let origin = rect_json(window);
        let mut queue = std::collections::VecDeque::from([(window.clone(), 0)]);
        while let Some((element, depth)) = queue.pop_front() {
            let password = element.is_password().unwrap_or(false);
            let bounds = rect_json(&element);
            let x = bounds["left"].as_i64().unwrap_or(0) - origin["left"].as_i64().unwrap_or(0) + bounds["width"].as_i64().unwrap_or(0) / 2;
            let y = bounds["top"].as_i64().unwrap_or(0) - origin["top"].as_i64().unwrap_or(0) + bounds["height"].as_i64().unwrap_or(0) / 2;
            let inside = x >= 0 && y >= 0 && x < origin["width"].as_i64().unwrap_or(0) && y < origin["height"].as_i64().unwrap_or(0);
            rows.push(json!({
                "x":if inside { Some(x) } else { None }, "y":if inside { Some(y) } else { None },
                "coordinateSpace":"window", "insideWindow":inside,
                "elementId":rows.len(),
                "depth":depth,
                "name":if password { "[password]".to_string() } else { element.get_name().unwrap_or_default().chars().take(160).collect() },
                "controlType":element.get_control_type().map(|kind| format!("{kind:?}")).unwrap_or_default(),
                "bounds":bounds, "boundsCoordinateSpace":"screen"
            }));
            if rows.len() >= 160 { break; }
            if depth < 5 {
                for child in walker.get_children(&element).unwrap_or_default().into_iter().take(35) {
                    queue.push_back((child, depth + 1));
                }
            }
        }
        json!(rows)
    }

    fn element_by_index(automation: &UIAutomation, window: &UIElement, index: usize) -> Result<UIElement, String> {
        let walker = automation.get_control_view_walker().map_err(|e| e.to_string())?;
        let mut queue = std::collections::VecDeque::from([(window.clone(), 0)]);
        let mut seen = 0;
        while let Some((element, depth)) = queue.pop_front() {
            if seen == index { return Ok(element); }
            seen += 1;
            if seen >= 160 { break; }
            if depth < 5 {
                for child in walker.get_children(&element).unwrap_or_default().into_iter().take(35) {
                    queue.push_back((child, depth + 1));
                }
            }
        }
        Err("Element is no longer available; inspect the window again".into())
    }

    fn element_at(automation: &UIAutomation, window: &UIElement, point: &Point) -> Result<UIElement, String> {
        let walker = automation.get_control_view_walker().map_err(|e| e.to_string())?;
        let mut best = None;
        let mut queue = std::collections::VecDeque::from([(window.clone(), 0usize)]);
        let mut seen = 0;
        while let Some((element, depth)) = queue.pop_front() {
            seen += 1;
            if seen > 600 { break; }
            let within = element.get_bounding_rectangle().is_ok_and(|r| {
                point.get_x() >= r.get_left() && point.get_y() >= r.get_top()
                    && point.get_x() < r.get_left() + r.get_width()
                    && point.get_y() < r.get_top() + r.get_height()
            });
            if !within { continue; }
            if depth > 0 && (element.get_pattern::<uiautomation::patterns::UIValuePattern>().is_ok()
                || element.get_pattern::<uiautomation::patterns::UIInvokePattern>().is_ok()
                || element.get_pattern::<uiautomation::patterns::UITogglePattern>().is_ok()
                || element.get_pattern::<uiautomation::patterns::UISelectionItemPattern>().is_ok()
                || element.get_pattern::<uiautomation::patterns::UIExpandCollapsePattern>().is_ok()) {
                best = Some(element.clone());
            }
            if depth < 12 {
                for child in walker.get_children(&element).unwrap_or_default().into_iter().take(80) {
                    queue.push_back((child, depth + 1));
                }
            }
        }
        best.ok_or("No accessible control at this point. Use the app directly for this control.".into())
    }

    fn foreground_click(window_id: isize, point: &Point, allowed: bool) -> Result<Value, String> {
        if !allowed { return Err("This control needs foreground mouse input. Turn off Keep my window in front to use it.".into()); }
        use windows::Win32::Foundation::{HWND, POINT};
        use windows::Win32::UI::WindowsAndMessaging::{GetAncestor, GetForegroundWindow, GetWindowLongW, IsIconic, SetCursorPos, SetForegroundWindow, SetWindowPos, ShowWindow, WindowFromPoint, GA_ROOT, GWL_EXSTYLE, HWND_NOTOPMOST, HWND_TOP, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW, SW_RESTORE, WS_EX_TOPMOST};
        let hwnd = HWND(window_id as *mut std::ffi::c_void);
        let previous = unsafe { GetForegroundWindow() };
        let cursor = Mouse::get_cursor_pos().ok();
        let was_topmost = unsafe { (GetWindowLongW(hwnd, GWL_EXSTYLE) as u32 & WS_EX_TOPMOST.0) != 0 };
        unsafe {
            if IsIconic(hwnd).as_bool() { let _ = ShowWindow(hwnd, SW_RESTORE); }
            SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
                .map_err(|e| format!("Could not expose the selected window: {e}"))?;
        }
        let hit = unsafe { GetAncestor(WindowFromPoint(POINT { x: point.get_x(), y: point.get_y() }), GA_ROOT) };
        let result = if hit != hwnd {
            Err(format!("Another window still covers the selected control (selected {window_id}, hit {})", hit.0 as isize))
        } else { Mouse::new().move_time(0).click(point).map_err(|error| error.to_string()) };
        if let Some(cursor) = cursor {
            unsafe { let _ = SetCursorPos(cursor.get_x(), cursor.get_y()); }
        }
        if !was_topmost {
            unsafe { let _ = SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE); }
        }
        if previous != hwnd && !previous.0.is_null() {
            unsafe {
                let _ = SetWindowPos(previous, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
                let _ = SetForegroundWindow(previous);
            }
        }
        result?;
        Ok(json!({"activated":true,"inputMode":"pointer","foregroundReturned":previous != hwnd}))
    }

    fn foreground_drag(window_id: isize, start: &Point, end: &Point) -> Result<Value, String> {
        use windows::Win32::Foundation::{HWND, POINT};
        use windows::Win32::UI::WindowsAndMessaging::{GetAncestor, GetForegroundWindow, GetWindowLongW, IsIconic, SetCursorPos, SetForegroundWindow, SetWindowPos, ShowWindow, WindowFromPoint, GA_ROOT, GWL_EXSTYLE, HWND_NOTOPMOST, HWND_TOP, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW, SW_RESTORE, WS_EX_TOPMOST};
        let hwnd = HWND(window_id as *mut std::ffi::c_void);
        let previous = unsafe { GetForegroundWindow() };
        let cursor = Mouse::get_cursor_pos().ok();
        let was_topmost = unsafe { (GetWindowLongW(hwnd, GWL_EXSTYLE) as u32 & WS_EX_TOPMOST.0) != 0 };
        let result: Result<(), String> = (|| {
            unsafe {
                if IsIconic(hwnd).as_bool() { let _ = ShowWindow(hwnd, SW_RESTORE); }
                SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
                    .map_err(|e| format!("Could not expose the selected window: {e}"))?;
            }
            for point in [start, end] {
                let hit = unsafe { GetAncestor(WindowFromPoint(POINT { x: point.get_x(), y: point.get_y() }), GA_ROOT) };
                if hit != hwnd { return Err("Another window covers the drag path endpoint".into()); }
            }
            let mouse = Mouse::new().move_time(120);
            mouse.move_to(start).map_err(|error| error.to_string())?;
            mouse.drag_to(MouseButton::LEFT, end).map_err(|error| error.to_string())?;
            Ok(())
        })();
        if let Some(cursor) = cursor {
            unsafe { let _ = SetCursorPos(cursor.get_x(), cursor.get_y()); }
        }
        if !was_topmost {
            unsafe { let _ = SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE); }
        }
        if previous != hwnd && !previous.0.is_null() {
            unsafe {
                let _ = SetWindowPos(previous, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
                let _ = SetForegroundWindow(previous);
            }
        }
        result?;
        Ok(json!({"dragged":true,"inputMode":"pointer","foregroundReturned":previous != hwnd}))
    }

    fn foreground_type(window_id: isize, point: &Point, text: &str, allowed: bool) -> Result<Value, String> {
        if !allowed { return Err("Turn off Keep my window in front to type into this control.".into()); }
        use windows::Win32::Foundation::{HWND, POINT};
        use windows::Win32::UI::WindowsAndMessaging::{GetAncestor, GetForegroundWindow, GetWindowLongW, IsIconic, SetCursorPos, SetForegroundWindow, SetWindowPos, ShowWindow, WindowFromPoint, GA_ROOT, GWL_EXSTYLE, HWND_NOTOPMOST, HWND_TOP, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW, SW_RESTORE, WS_EX_TOPMOST};
        let hwnd = HWND(window_id as *mut std::ffi::c_void);
        let previous = unsafe { GetForegroundWindow() };
        let cursor = Mouse::get_cursor_pos().ok();
        let was_topmost = unsafe { (GetWindowLongW(hwnd, GWL_EXSTYLE) as u32 & WS_EX_TOPMOST.0) != 0 };
        unsafe {
            if IsIconic(hwnd).as_bool() { let _ = ShowWindow(hwnd, SW_RESTORE); }
            SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
                .map_err(|e| format!("Could not expose the selected window: {e}"))?;
        }
        let mouse = Mouse::new().move_time(0);
        let result = (|| {
            let hit = unsafe { GetAncestor(WindowFromPoint(POINT { x: point.get_x(), y: point.get_y() }), GA_ROOT) };
            if hit != hwnd { return Err("Another window still covers the selected text control".into()); }
            mouse.click(point).map_err(|error| error.to_string())?;
            if unsafe { GetForegroundWindow() } != hwnd { return Err("The selected app did not receive keyboard focus".into()); }
            let keyboard = Keyboard::new().interval(1);
            keyboard.send_keys("{ctrl}a").map_err(|error| error.to_string())?;
            keyboard.send_text(text).map_err(|error| error.to_string())?;
            keyboard.send_keys("{enter}").map_err(|error| error.to_string())?;
            Ok::<_, String>(())
        })();
        if let Some(cursor) = cursor {
            unsafe { let _ = SetCursorPos(cursor.get_x(), cursor.get_y()); }
        }
        if !was_topmost {
            unsafe { let _ = SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE); }
        }
        if previous != hwnd && !previous.0.is_null() {
            unsafe {
                let _ = SetWindowPos(previous, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
                let _ = SetForegroundWindow(previous);
            }
        }
        result?;
        Ok(json!({"submitted":true,"inputMode":"pointer","foregroundReturned":previous != hwnd}))
    }

    fn read_screen(args: &Value) -> Result<Value, String> {
        use windows::core::HSTRING;
        use windows::Graphics::Imaging::BitmapDecoder;
        use windows::Media::Ocr::OcrEngine;
        use windows::Storage::StorageFile;
        use windows::Win32::System::WinRT::{RoInitialize, RoUninitialize, RO_INIT_MULTITHREADED};

        let shot = run("screenshot", args)?;
        // OCR measures the captured image; clicks use the window rectangle around it.
        let origin_x = shot["origin"]["x"].as_f64().unwrap_or(0.0) as f32;
        let origin_y = shot["origin"]["y"].as_f64().unwrap_or(0.0) as f32;
        let data_url = shot["dataUrl"].as_str().ok_or("Window capture has no image")?;
        let encoded = data_url.split_once(',').ok_or("Window capture is invalid")?.1;
        let bytes = base64::engine::general_purpose::STANDARD.decode(encoded).map_err(|e| e.to_string())?;
        let path = std::env::temp_dir().join(format!("opencore-ocr-{}.png", uuid::Uuid::new_v4()));
        std::fs::write(&path, bytes).map_err(|e| e.to_string())?;
        let result = (|| {
            unsafe { RoInitialize(RO_INIT_MULTITHREADED).map_err(|e| e.to_string())?; }
            let recognized = (|| {
                let file = StorageFile::GetFileFromPathAsync(&HSTRING::from(path.to_string_lossy().as_ref()))
                    .map_err(|e| e.to_string())?.get().map_err(|e| e.to_string())?;
                let stream = file.OpenReadAsync().map_err(|e| e.to_string())?.get().map_err(|e| e.to_string())?;
                let decoder = BitmapDecoder::CreateAsync(&stream).map_err(|e| e.to_string())?.get().map_err(|e| e.to_string())?;
                let bitmap = decoder.GetSoftwareBitmapAsync().map_err(|e| e.to_string())?.get().map_err(|e| e.to_string())?;
                let engine = OcrEngine::TryCreateFromUserProfileLanguages().map_err(|e| e.to_string())?;
                let result = engine.RecognizeAsync(&bitmap).map_err(|e| e.to_string())?.get().map_err(|e| e.to_string())?;
                let lines = result.Lines().map_err(|e| e.to_string())?;
                let mut words = Vec::new();
                let mut line_rows = Vec::new();
                for line_index in 0..lines.Size().map_err(|e| e.to_string())?.min(80) {
                    let line = lines.GetAt(line_index).map_err(|e| e.to_string())?;
                    let found = line.Words().map_err(|e| e.to_string())?;
                    let mut line_words = Vec::new();
                    let mut left = f32::MAX;
                    let mut top = f32::MAX;
                    let mut right = 0.0_f32;
                    let mut bottom = 0.0_f32;
                    for index in 0..found.Size().map_err(|e| e.to_string())?.min(40) {
                        let word = found.GetAt(index).map_err(|e| e.to_string())?;
                        let mut bounds = word.BoundingRect().map_err(|e| e.to_string())?;
                        bounds.X += origin_x;
                        bounds.Y += origin_y;
                        let text = word.Text().map_err(|e| e.to_string())?.to_string();
                        left = left.min(bounds.X);
                        top = top.min(bounds.Y);
                        right = right.max(bounds.X + bounds.Width);
                        bottom = bottom.max(bounds.Y + bounds.Height);
                        line_words.push(text.clone());
                        words.push(json!({"text":text,
                            "x":(bounds.X + bounds.Width / 2.0).round() as i32,
                            "y":(bounds.Y + bounds.Height / 2.0).round() as i32}));
                    }
                    if !line_words.is_empty() {
                        line_rows.push(json!({"text":line_words.join(" "),
                            "x":((left + right) / 2.0).round() as i32,
                            "y":((top + bottom) / 2.0).round() as i32}));
                    }
                }
                line_rows.sort_by_key(|line| (line["y"].as_i64().unwrap_or_default() / 12,
                    line["x"].as_i64().unwrap_or_default()));
                let ordered_text = line_rows.iter().filter_map(|line| line["text"].as_str())
                    .collect::<Vec<_>>().join(" | ");
                Ok::<_, String>(json!({"windowId":shot["windowId"],"bounds":shot["bounds"],
                    "text":ordered_text.chars().take(8000).collect::<String>(),
                    "lines":line_rows,"words":words,
                    "visualInput":"Windows OCR; line x,y are centers relative to the selected window; non-text shapes are not described"}))
            })();
            unsafe { RoUninitialize(); }
            recognized
        })();
        let _ = std::fs::remove_file(&path);
        result
    }

    pub(super) fn run(action: &str, args: &Value) -> Result<Value, String> {
        let automation = UIAutomation::new().map_err(|e| e.to_string())?;
        if action == "list" {
            let mut rows = vec![json!({"windowId":0,"title":"Whole desktop","bounds":rect_json(&automation.get_root_element().map_err(|e| e.to_string())?)})];
            rows.extend(windows(&automation)?.into_iter().filter_map(|element| {
                let id: isize = element.get_native_window_handle().ok()?.into();
                if id <= 0 { return None; }
                Some(json!({"windowId":id,"title":element.get_name().unwrap_or_default(),"bounds":rect_json(&element)}))
            }));
            return Ok(json!({"windows":rows}));
        }
        let id = args["windowId"].as_i64().ok_or("Select a window first")? as isize;
        let window = if id == 0 { automation.get_root_element().map_err(|e| e.to_string())? } else { window_by_id(&automation, id)? };
        match action {
            "read_screen" => read_screen(args),
            "inspect" => Ok(json!({"windowId":id,"title":window.get_name().unwrap_or_default(),"bounds":rect_json(&window),"coordinateSpace":"window","elements":inspect_tree(&automation, &window)})),
            "invoke" | "set_value" => {
                use uiautomation::patterns::{UIInvokePattern, UIValuePattern};
                let element_id = args["elementId"].as_u64().unwrap_or(160) as usize;
                let element = element_by_index(&automation, &window, element_id)?;
                if action == "invoke" {
                    element.get_pattern::<UIInvokePattern>().map_err(|_| "This control cannot be invoked without foreground pointer input".to_string())?
                        .invoke().map_err(|e| e.to_string())?;
                } else {
                    element.get_pattern::<UIValuePattern>().map_err(|_| "This control cannot accept a value without foreground keyboard input".to_string())?
                        .set_value(args["text"].as_str().unwrap_or_default()).map_err(|e| e.to_string())?;
                }
                Ok(json!({"windowId":id,"elementId":element_id,"action":action}))
            }
            "screenshot" => {
                if id != 0 {
                    let frame = crate::desktop_capture::frame_for_window(id)?;
                    let (x, y) = crate::desktop_capture::frame_origin(id);
                    return Ok(json!({"windowId":id,"bounds":{"left":0,"top":0,"width":frame.width,"height":frame.height},
                        "origin":{"x":x,"y":y},"dataUrl":frame.data_url}));
                }
                let image = window.screenshot().map_err(|e| e.to_string())?;
                let path = std::env::temp_dir().join(format!("opencore-window-{}.png", uuid::Uuid::new_v4()));
                image.save_png(&path).map_err(|e| e.to_string())?;
                let bytes = std::fs::read(&path).map_err(|e| e.to_string());
                let _ = std::fs::remove_file(&path);
                let bytes = bytes?;
                if bytes.len() > 12 * 1024 * 1024 { return Err("Window image is too large".into()); }
                Ok(json!({"windowId":id,"bounds":rect_json(&window),"dataUrl":format!("data:image/png;base64,{}",base64::engine::general_purpose::STANDARD.encode(bytes))}))
            }
            "interact" | "set_at" | "commit_enter" => {
                use uiautomation::patterns::{UIExpandCollapsePattern, UIInvokePattern, UISelectionItemPattern, UITogglePattern, UIValuePattern};
                let point = window_point(&window, args)?;
                let element = match element_at(&automation, &window, &point) {
                    Ok(element) => element,
                    Err(_) if action == "interact" => return foreground_click(id, &point, args["allowForegroundFallback"].as_bool().unwrap_or(true)),
                    Err(error) => return Err(error),
                };
                if element.is_password().unwrap_or(false) {
                    return Err("Password controls require direct interaction in the application".into());
                }
                if let Ok(value) = element.get_pattern::<UIValuePattern>() {
                    if !value.is_readonly().unwrap_or(true) {
                        if action == "interact" {
                            return Ok(json!({"editable":true,"value":value.get_value().unwrap_or_default(),"name":element.get_name().unwrap_or_default()}));
                        }
                        if action == "set_at" {
                            value.set_value(args["text"].as_str().unwrap_or_default()).map_err(|e| e.to_string())?;
                            return Ok(json!({"editable":true,"updated":true}));
                        }
                        let hwnd = windows::Win32::Foundation::HWND(id as *mut std::ffi::c_void);
                        unsafe {
                            use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, IsIconic, SetForegroundWindow, ShowWindow, SW_RESTORE};
                            if IsIconic(hwnd).as_bool() { let _ = ShowWindow(hwnd, SW_RESTORE); }
                            let _ = SetForegroundWindow(hwnd);
                            if GetForegroundWindow() != hwnd { return Err("Windows did not allow this app to move to the foreground".into()); }
                        }
                        element.set_focus().map_err(|e| format!("Could not focus the control: {e}"))?;
                        Keyboard::new().interval(1).send_keys("{enter}").map_err(|e| e.to_string())?;
                        return Ok(json!({"submitted":true}));
                    }
                }
                if action != "interact" {
                    return Err("This point is not an editable control".into());
                }
                if let Ok(pattern) = element.get_pattern::<UIInvokePattern>() {
                    pattern.invoke().map_err(|e| e.to_string())?;
                } else if let Ok(pattern) = element.get_pattern::<UITogglePattern>() {
                    pattern.toggle().map_err(|e| e.to_string())?;
                } else if let Ok(pattern) = element.get_pattern::<UISelectionItemPattern>() {
                    pattern.select().map_err(|e| e.to_string())?;
                } else if let Ok(pattern) = element.get_pattern::<UIExpandCollapsePattern>() {
                    pattern.expand().map_err(|e| e.to_string())?;
                } else {
                    return foreground_click(id, &point, args["allowForegroundFallback"].as_bool().unwrap_or(true));
                }
                Ok(json!({"activated":true,"name":element.get_name().unwrap_or_default()}))
            }
            "commit_text" => {
                let point = window_point(&window, args)?;
                foreground_type(id, &point, args["text"].as_str().unwrap_or_default(), args["allowForegroundFallback"].as_bool().unwrap_or(true))
            }
            "drag" => {
                let start = window_point(&window, args)?;
                let destination = json!({"x":args["toX"],"y":args["toY"]});
                let end = window_point(&window, &destination)?;
                let result = foreground_drag(id, &start, &end)?;
                Ok(json!({"windowId":id,"x":args["x"],"y":args["y"],
                    "toX":args["toX"],"toY":args["toY"],"action":"drag",
                    "dragged":true,"foregroundReturned":result["foregroundReturned"]}))
            }
            "move" | "click" => {
                let point = window_point(&window, args)?;
                if action == "click" && id != 0 {
                    let result = foreground_click(id, &point, args["allowForegroundFallback"].as_bool().unwrap_or(true))?;
                    return Ok(json!({"windowId":id,"x":args["x"],"y":args["y"],"action":action,
                        "activated":true,"inputMode":"pointer","foregroundReturned":result["foregroundReturned"]}));
                }
                let mouse = Mouse::new().move_time(20);
                if action == "move" { mouse.move_to(&point).map_err(|e| e.to_string())?; }
                else { mouse.click(&point).map_err(|e| e.to_string())?; }
                Ok(json!({"windowId":id,"x":args["x"],"y":args["y"],"action":action}))
            }
            "type" => {
                if id != 0 { window.set_focus().map_err(|e| format!("Could not focus the selected window: {e}"))?; }
                Keyboard::new().interval(1).send_text(args["text"].as_str().unwrap_or_default()).map_err(|e| e.to_string())?;
                Ok(json!({"typed":args["text"].as_str().unwrap_or_default().chars().count()}))
            }
            "navigate_url" => {
                if !window.get_name().unwrap_or_default().contains("Google Chrome") {
                    return Err("Select a Google Chrome window for navigate_url".into());
                }
                window.set_focus().map_err(|e| format!("Could not focus Chrome: {e}"))?;
                let keyboard = Keyboard::new().interval(1);
                keyboard.send_keys("{ctrl}l").map_err(|e| e.to_string())?;
                keyboard.send_text(args["url"].as_str().unwrap_or_default()).map_err(|e| e.to_string())?;
                keyboard.send_keys("{enter}").map_err(|e| e.to_string())?;
                Ok(json!({"windowId":id,"requestedUrl":args["url"],"submitted":true,"verified":false}))
            }
            "key" | "scroll" => {
                if id != 0 { window.set_focus().map_err(|e| format!("Could not focus the selected window: {e}"))?; }
                let key = if action == "scroll" {
                    if args["direction"] == "up" { "PageUp" } else { "PageDown" }
                } else { args["key"].as_str().unwrap_or("Escape") };
                let sequence = if key == "Ctrl+L" { "{ctrl}l".to_string() } else { format!("{{{}}}", key.to_lowercase()) };
                Keyboard::new().interval(1).send_keys(&sequence).map_err(|e| e.to_string())?;
                Ok(json!({"key":key}))
            }
            _ => Err("Unsupported desktop action".into()),
        }
    }
}

pub(crate) async fn command(action: String, args: Value) -> Result<Value, String> {
    validate_action(&action, &args)?;
    #[cfg(windows)]
    { tokio::task::spawn_blocking(move || platform::run(&action, &args)).await.map_err(|e| e.to_string())? }
    #[cfg(not(windows))]
    { let _ = (action, args); Err("Desktop control is available on Windows".into()) }
}

#[cfg(windows)]
pub(crate) fn cursor_position() -> Option<(i32, i32)> {
    use uiautomation::inputs::Mouse;
    Mouse::get_cursor_pos().ok().map(|point| (point.get_x(), point.get_y()))
}

#[cfg(windows)]
pub(crate) fn restore_cursor_if_unchanged(expected: (i32, i32), original: (i32, i32)) {
    use windows::Win32::UI::WindowsAndMessaging::SetCursorPos;
    if cursor_position() == Some(expected) {
        unsafe { let _ = SetCursorPos(original.0, original.1); }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn desktop_commands_are_bounded() {
        assert!(validate_action("execute", &json!({})).is_err());
        assert!(validate_action("click", &json!({"windowId":1,"x":-1,"y":4})).is_err());
        assert!(validate_action("type", &json!({"windowId":1,"text":"Hello"})).is_ok());
        assert!(validate_action("key", &json!({"windowId":1,"key":"Delete"})).is_err());
        assert!(validate_action("interact", &json!({"windowId":1,"x":40,"y":20})).is_ok());
        assert!(validate_action("set_at", &json!({"windowId":1,"x":40,"y":20,"text":"hello"})).is_ok());
        assert!(validate_action("set_at", &json!({"windowId":1,"x":40,"y":20})).is_err());
        assert!(validate_action("commit_enter", &json!({"windowId":1,"x":40,"y":20})).is_ok());
        assert!(validate_action("key", &json!({"windowId":1,"key":"Ctrl+L"})).is_ok());
        assert!(validate_action("navigate_url", &json!({"windowId":1,"url":"https://www.google.com/search?q=snake"})).is_ok());
        assert!(validate_action("navigate_url", &json!({"windowId":1,"url":"javascript:alert(1)"})).is_err());
        assert!(validate_action("commit_text", &json!({"windowId":1,"x":40,"y":20,"text":"hello"})).is_ok());
        assert!(validate_action("commit_text", &json!({"windowId":1,"x":40,"y":20})).is_err());
        assert!(validate_action("read_screen", &json!({"windowId":1})).is_ok());
        assert!(validate_action("scroll", &json!({"windowId":1,"direction":"down"})).is_ok());
        assert!(validate_action("scroll", &json!({"windowId":1})).is_err());
        assert!(validate_action("drag", &json!({"windowId":1,"x":40,"y":20,"toX":90,"toY":80})).is_ok());
        assert!(validate_action("drag", &json!({"windowId":1,"x":40,"y":20,"toX":90})).is_err());
        assert!(validate_action("drag", &json!({"windowId":0,"x":40,"y":20,"toX":90,"toY":80})).is_err());
    }
    #[cfg(windows)]
    #[test]
    #[ignore = "manual Windows desktop smoke test"]
    fn live_window_listing_and_screenshot() {
        let listed = platform::run("list", &json!({})).unwrap();
        let windows = listed["windows"].as_array().unwrap();
        assert!(!windows.is_empty());
        let selected = windows.iter().find(|row| row["title"] == "OpenCore")
            .unwrap_or(&windows[0]);
        let id = selected["windowId"].as_i64().unwrap();
        let shot = platform::run("screenshot", &json!({"windowId":id})).unwrap();
        assert!(shot["dataUrl"].as_str().unwrap().starts_with("data:image/png;base64,iVBOR"));
        let tree = platform::run("inspect", &json!({"windowId":id})).unwrap();
        assert!(tree["elements"].as_array().is_some_and(|items| !items.is_empty()));
    }
    #[cfg(windows)]
    #[test]
    #[ignore = "manual Windows desktop capture smoke test"]
    fn live_whole_desktop_screenshot() {
        let shot = platform::run("screenshot", &json!({"windowId":0})).unwrap();
        assert!(shot["dataUrl"].as_str().unwrap().starts_with("data:image/png;base64,iVBOR"));
    }
    #[cfg(windows)]
    #[test]
    #[ignore = "requires the disposable tkinter computer use fixture"]
    fn live_canvas_fallback_click() {
        let cursor_before = cursor_position();
        let listed = platform::run("list", &json!({})).unwrap();
        let fixture = listed["windows"].as_array().unwrap().iter()
            .find(|row| row["title"] == "OpenCore Computer Use Fixture").unwrap();
        let id = fixture["windowId"].as_i64().unwrap();
        let result = platform::run("interact", &json!({"windowId":id,"x":145,"y":125,"allowForegroundFallback":true})).unwrap();
        assert_eq!(result["activated"], true);
        assert_eq!(cursor_position(), cursor_before);
    }
    #[cfg(windows)]
    #[test]
    #[ignore = "requires the disposable tkinter computer use fixture"]
    fn live_click_activates_covered_window() {
        use windows::Win32::UI::WindowsAndMessaging::GetForegroundWindow;
        let foreground_before = unsafe { GetForegroundWindow() };
        let cursor_before = cursor_position();
        let events = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../test computer use/desktop_fixture_events.jsonl");
        let before = std::fs::read_to_string(&events).unwrap_or_default().lines().count();
        let listed = platform::run("list", &json!({})).unwrap();
        let fixture = listed["windows"].as_array().unwrap().iter()
            .find(|row| row["title"] == "OpenCore Computer Use Fixture").unwrap();
        let result = platform::run("click", &json!({"windowId":fixture["windowId"],"x":114,"y":100})).unwrap();
        assert_eq!(result["activated"], true);
        assert_eq!(cursor_position(), cursor_before);
        assert_eq!(unsafe { GetForegroundWindow() }, foreground_before);
        let after = std::fs::read_to_string(&events).unwrap_or_default();
        assert!(after.lines().count() > before, "click was not received by the fixture");
        assert!(after.lines().last().unwrap_or_default().contains("\"target\": 1"), "{after}");
    }
    #[cfg(windows)]
    #[test]
    #[ignore = "requires the disposable tkinter computer use fixture"]
    fn live_unexposed_text_fallback() {
        use windows::Win32::UI::WindowsAndMessaging::GetForegroundWindow;
        let foreground_before = unsafe { GetForegroundWindow() };
        let cursor_before = cursor_position();
        let listed = platform::run("list", &json!({})).unwrap();
        let fixture = listed["windows"].as_array().unwrap().iter()
            .find(|row| row["title"] == "OpenCore Computer Use Fixture").unwrap();
        let id = fixture["windowId"].as_i64().unwrap();
        let result = platform::run("commit_text", &json!({"windowId":id,"x":200,"y":577,"text":"opencore-test","allowForegroundFallback":true})).unwrap();
        assert_eq!(result["submitted"], true);
        assert_eq!(cursor_position(), cursor_before);
        assert_eq!(unsafe { GetForegroundWindow() }, foreground_before);
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "requires the disposable tkinter computer use fixture"]
    fn live_screen_text_for_canvas() {
        let listed = platform::run("list", &json!({})).unwrap();
        let fixture = listed["windows"].as_array().unwrap().iter()
            .find(|row| row["title"] == "OpenCore Computer Use Fixture").unwrap();
        let read = platform::run("read_screen", &json!({"windowId":fixture["windowId"]})).unwrap();
        println!("{}", read);
        assert!(read["text"].as_str().unwrap().contains("Target"), "{read}");
        assert!(read["words"].as_array().is_some_and(|words| !words.is_empty()), "{read}");
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "local UI inspection"]
    fn inspect_opencore_ui() {
        let listed = platform::run("list", &json!({})).unwrap();
        let row = listed["windows"].as_array().unwrap().iter()
            .find(|row| row["title"] == "OpenCore").unwrap();
        let tree = platform::run("inspect", &json!({"windowId":row["windowId"]})).unwrap();
        for element in tree["elements"].as_array().unwrap() {
            let name = element["name"].as_str().unwrap_or("");
            if !name.is_empty() { println!("{} | {} | {}", element["elementId"], name, element["bounds"]); }
        }
    }
}
