//! Scoped, click-through indicator. Never replaces the user's system cursor.
use std::sync::atomic::{AtomicU64, Ordering};
use tauri::{Emitter, Manager};
use windows::Win32::Foundation::{HWND, RECT};
use windows::Win32::UI::WindowsAndMessaging::{GetWindowRect, IsWindow, IsIconic};

static GENERATION: AtomicU64 = AtomicU64::new(0);

pub(crate) fn clear(app: &tauri::AppHandle) {
    GENERATION.fetch_add(1, Ordering::SeqCst);
    if let Some(overlay) = app.get_webview_window("desktop-activity") { let _ = overlay.hide(); }
    crate::desktop_capture::stop_capture();
}

pub(crate) struct Guard { app: tauri::AppHandle, generation: u64 }
impl Drop for Guard {
    fn drop(&mut self) {
        if GENERATION.load(Ordering::SeqCst) == self.generation { clear(&self.app); }
    }
}

pub(crate) fn begin(app: &tauri::AppHandle, window_id: i64, args: &serde_json::Value) -> Guard {
    let generation = GENERATION.fetch_add(1, Ordering::SeqCst) + 1;
    let guard = Guard { app: app.clone(), generation };
    if window_id <= 0 { return guard; }
    let app = app.clone();
    let point = args["x"].as_f64().zip(args["y"].as_f64());
    tauri::async_runtime::spawn(async move {
        let Some(overlay) = app.get_webview_window("desktop-activity") else { return };
        let mut first = true;
        // Each action owns the indicator. Dropping its future also clears it.
        while GENERATION.load(Ordering::SeqCst) == generation {
            let hwnd = HWND(window_id as *mut std::ffi::c_void);
            let mut rect = RECT::default();
            if unsafe { !IsWindow(hwnd).as_bool() || IsIconic(hwnd).as_bool() || GetWindowRect(hwnd, &mut rect).is_err() } { break; }
            let (width, height) = (rect.right - rect.left, rect.bottom - rect.top);
            if width <= 0 || height <= 0 { break; }
            let _ = overlay.set_position(tauri::PhysicalPosition::new(rect.left, rect.top));
            let _ = overlay.set_size(tauri::PhysicalSize::new(width as u32, height as u32));
            if first {
                let _ = overlay.show();
                first = false;
            }
            let (x, y) = point.unwrap_or_else(|| crate::windows_control::cursor_position()
                .map(|(x,y)| ((x - rect.left) as f64, (y - rect.top) as f64)).unwrap_or((16.0, 16.0)));
            let _ = overlay.emit("opencore-desktop-pointer", serde_json::json!({"x":x,"y":y}));
            tokio::time::sleep(std::time::Duration::from_millis(32)).await;
        }
        if GENERATION.load(Ordering::SeqCst) == generation { let _ = overlay.hide(); }
    });
    guard
}
