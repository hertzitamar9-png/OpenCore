//! A steady, click-through border. Never replaces the user's system cursor.
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::Manager;
use windows::Win32::Foundation::{HWND, RECT};
use windows::Win32::UI::WindowsAndMessaging::{
    IsIconic, IsWindow, SetWindowPos, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOOWNERZORDER,
    SWP_SHOWWINDOW,
};

const IDLE_GRACE: Duration = Duration::from_millis(350);
const TRACK_INTERVAL: Duration = Duration::from_millis(32);

pub(crate) fn build_overlay(app: &tauri::AppHandle) -> tauri::Result<tauri::WebviewWindow> {
    tauri::WebviewWindowBuilder::new(
        app,
        "desktop-activity",
        tauri::WebviewUrl::App("index.html?desktop-activity".into()),
    )
    .title("OpenCore activity")
    .decorations(false)
    // An undecorated Tauri shadow still reserves asymmetric native client
    // insets. The webview's frame must fill the outer DWM-sized HWND exactly.
    .shadow(false)
    .transparent(true)
    .always_on_top(true)
    .skip_taskbar(true)
    .focused(false)
    .focusable(false)
    .visible(false)
    .resizable(false)
    .inner_size(290.0, 54.0)
    .build()
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct Geometry {
    position: (i32, i32),
    size: (u32, u32),
}

impl Geometry {
    fn from_rect(rect: RECT) -> Option<Self> {
        let width = rect.right.checked_sub(rect.left)?;
        let height = rect.bottom.checked_sub(rect.top)?;
        (width > 0 && height > 0).then_some(Self {
            position: (rect.left, rect.top),
            size: (width as u32, height as u32),
        })
    }
}

struct Activity {
    generation: u64,
    target: Option<i64>,
    idle_until: Option<Instant>,
    hold_until_clear: bool,
    worker: Option<u64>,
    visible: bool,
    geometry: Option<Geometry>,
}

impl Activity {
    const fn new() -> Self {
        Self {
            generation: 0,
            target: None,
            idle_until: None,
            hold_until_clear: false,
            worker: None,
            visible: false,
            geometry: None,
        }
    }

    fn begin(&mut self, window_id: i64, hold_until_clear: bool) -> (u64, Option<u64>) {
        self.generation = self.generation.wrapping_add(1);
        self.target = (window_id > 0).then_some(window_id);
        self.idle_until = None;
        self.hold_until_clear = hold_until_clear;
        // A burst of commands updates one worker instead of spawning one task
        // for every click or text change.
        let worker = if self.worker.is_none() && (self.target.is_some() || self.visible) {
            self.worker = Some(self.generation);
            self.worker
        } else {
            None
        };
        (self.generation, worker)
    }

    fn complete(&mut self, generation: u64, now: Instant) {
        if self.generation == generation && self.target.is_some() {
            self.idle_until = if self.hold_until_clear {
                None
            } else {
                Some(now + IDLE_GRACE)
            };
        }
    }

    fn cancel(&mut self) -> u64 {
        self.generation = self.generation.wrapping_add(1);
        self.target = None;
        self.idle_until = None;
        self.hold_until_clear = false;
        self.generation
    }

    fn active_target(&self, now: Instant) -> Option<i64> {
        if self.idle_until.is_some_and(|deadline| now >= deadline) {
            None
        } else {
            self.target
        }
    }
}

static ACTIVITY: Mutex<Activity> = Mutex::new(Activity::new());

fn hide(app: &tauri::AppHandle, activity: &mut Activity) {
    if activity.visible {
        if let Some(overlay) = app.get_webview_window("desktop-activity") {
            let _ = overlay.hide();
        }
    }
    activity.visible = false;
    activity.geometry = None;
}

pub(crate) fn clear(app: &tauri::AppHandle) {
    let generation = ACTIVITY
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .cancel();
    let cleared_app = app.clone();
    // UI mutations execute on the main thread with ownership checked there.
    // A queued cancellation must not hide an action that began after it.
    let _ = app.run_on_main_thread(move || {
        let mut activity = ACTIVITY.lock().unwrap_or_else(|error| error.into_inner());
        if activity.generation == generation {
            hide(&cleared_app, &mut activity);
        }
    });
    crate::desktop_capture::stop_capture();
}

pub(crate) struct Guard {
    generation: u64,
}

impl Drop for Guard {
    fn drop(&mut self) {
        ACTIVITY
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .complete(self.generation, Instant::now());
    }
}

// This function runs on Tauri's main thread. Holding the ownership lock while
// changing the border cannot leave a stale hide/show queued after a newer begin.
fn update(app: &tauri::AppHandle, worker: u64) -> bool {
    let mut activity = ACTIVITY.lock().unwrap_or_else(|error| error.into_inner());
    if activity.worker != Some(worker) {
        return false;
    }
    let Some(window_id) = activity.active_target(Instant::now()) else {
        hide(app, &mut activity);
        activity.target = None;
        activity.idle_until = None;
        activity.worker = None;
        return false;
    };
    let hwnd = HWND(window_id as *mut std::ffi::c_void);
    let valid = unsafe {
        IsWindow(hwnd).as_bool()
            && !IsIconic(hwnd).as_bool()
    };
    let geometry = valid.then(|| crate::desktop_capture::visible_window_rect(window_id as isize).ok())
        .flatten().and_then(Geometry::from_rect);
    let Some(geometry) = geometry else {
        activity.cancel();
        hide(app, &mut activity);
        activity.worker = None;
        crate::desktop_capture::stop_capture();
        return false;
    };
    let Some(overlay) = app.get_webview_window("desktop-activity") else {
        activity.cancel();
        activity.visible = false;
        activity.geometry = None;
        activity.worker = None;
        return false;
    };
    // Move, resize and show together, using the exact DWM frame and physical
    // coordinates. SWP_NOACTIVATE also protects the first show and DPI changes;
    // there is no foreground handoff to this transparent overlay.
    let placed = if activity.geometry != Some(geometry) || !activity.visible {
        (|| -> Result<(), String> {
            let _dpi = crate::desktop_capture::PhysicalDpiScope::new()?;
            let handle = overlay.hwnd().map_err(|error| error.to_string())?;
            unsafe {
                SetWindowPos(HWND(handle.0 as _), HWND_TOPMOST,
                    geometry.position.0, geometry.position.1,
                    geometry.size.0 as i32, geometry.size.1 as i32,
                    SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_SHOWWINDOW)
            }.map_err(|error| error.to_string())
        })()
    } else { Ok(()) };
    if placed.is_err() {
        activity.cancel();
        hide(app, &mut activity);
        activity.worker = None;
        return false;
    }
    activity.geometry = Some(geometry);
    activity.visible = true;
    true
}

async fn track(app: tauri::AppHandle, worker: u64) {
    loop {
        let (sender, receiver) = tokio::sync::oneshot::channel();
        let tracked_app = app.clone();
        if app
            .run_on_main_thread(move || {
                let _ = sender.send(update(&tracked_app, worker));
            })
            .is_err()
        {
            break;
        }
        // Await each UI update before scheduling another. A busy main thread
        // cannot accumulate an unbounded queue of geometry changes.
        if !matches!(receiver.await, Ok(true)) {
            break;
        }
        tokio::time::sleep(TRACK_INTERVAL).await;
    }
    let mut activity = ACTIVITY.lock().unwrap_or_else(|error| error.into_inner());
    if activity.worker == Some(worker) {
        activity.worker = None;
    }
}

pub(crate) fn begin(app: &tauri::AppHandle, window_id: i64, args: &serde_json::Value) -> Guard {
    let hold_until_clear = args["holdActivityUntilComplete"].as_bool().unwrap_or(false);
    let (generation, worker) = ACTIVITY
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .begin(window_id, hold_until_clear);
    if let Some(worker) = worker {
        tauri::async_runtime::spawn(track(app.clone(), worker));
    }
    Guard { generation }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hidden_overlay_paints_the_whole_physical_frame_without_native_client_insets() {
        use windows::Win32::UI::WindowsAndMessaging::{
            GetWindowInfo, IsWindowVisible, WINDOWINFO, WS_EX_NOACTIVATE, WS_EX_TRANSPARENT,
        };

        // Exercise our real Tauri window settings. Merely converting a DWM RECT
        // cannot catch an undecorated window's shadow reserving resize margins
        // inside the HWND while the webview paints only its smaller client area.
        let mut context = tauri::generate_context!();
        context.config_mut().app.windows.clear();
        let mut app = tauri::Builder::default()
            .any_thread()
            .build(context)
            .expect("create an isolated native runtime for the hidden overlay");
        let overlay = build_overlay(app.handle()).expect("build the production activity overlay");
        overlay
            .set_ignore_cursor_events(true)
            .expect("keep the native overlay click-through");
        let handle = overlay.hwnd().unwrap();
        let hwnd = HWND(handle.0 as _);
        let _dpi = crate::desktop_capture::PhysicalDpiScope::new().unwrap();
        for (left, top, width, height) in [
            (120, 80, 960, 540),
            (-1920, 132, 1440, 840),
            (2200, -900, 640, 480),
        ] {
            unsafe {
                SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    left,
                    top,
                    width,
                    height,
                    SWP_NOACTIVATE | SWP_NOOWNERZORDER,
                )
            }
            .unwrap();
            let mut info = WINDOWINFO {
                cbSize: std::mem::size_of::<WINDOWINFO>() as u32,
                ..Default::default()
            };
            unsafe { GetWindowInfo(hwnd, &mut info) }.unwrap();
            let edges = |rect: RECT| (rect.left, rect.top, rect.right, rect.bottom);
            let expected = (left, top, left + width, top + height);
            assert_eq!(edges(info.rcWindow), expected, "physical overlay placement");
            assert_eq!(
                edges(info.rcClient),
                expected,
                "the CSS frame must paint the visible target's edges; native shadow insets displace it"
            );
            assert!(!unsafe { IsWindowVisible(hwnd) }.as_bool());
            assert_ne!(info.dwExStyle.0 & WS_EX_NOACTIVATE.0, 0);
            assert_ne!(info.dwExStyle.0 & WS_EX_TRANSPARENT.0, 0);
        }
        overlay.destroy().expect("destroy the hidden test overlay");
        // Drain the single queued destruction; never show a fixture or run the
        // production setup, capture, inference, or input paths in this test.
        #[allow(deprecated)]
        app.run_iteration(|_, _| {});
    }

    #[test]
    fn border_geometry_uses_visible_physical_bounds_on_negative_origin_monitors() {
        // A 150% display to the left of the primary display. These DWM values
        // are already physical pixels; no DPI multiplier or outer resize edge
        // belongs in the overlay position or size.
        let visible = RECT { left: -1920, top: 132, right: -480, bottom: 972 };
        let geometry = Geometry::from_rect(visible).unwrap();
        assert_eq!(geometry.position, (-1920, 132));
        assert_eq!(geometry.size, (1440, 840));
        assert!(Geometry::from_rect(RECT { left: 10, top: 20, right: 10, bottom: 40 }).is_none());
        assert!(Geometry::from_rect(RECT { left: 10, top: 20, right: 9, bottom: 40 }).is_none());
    }

    #[test]
    fn adjacent_actions_keep_the_indicator_active_with_one_worker() {
        let mut activity = Activity::new();
        let now = Instant::now();
        let (first, worker) = activity.begin(10, false);
        assert!(worker.is_some());
        activity.complete(first, now);
        assert_eq!(
            activity.active_target(now + Duration::from_millis(50)),
            Some(10)
        );
        let (second, another_worker) = activity.begin(10, false);
        assert!(
            another_worker.is_none(),
            "adjacent actions must share one worker"
        );
        assert_eq!(
            activity.active_target(now + Duration::from_secs(1)),
            Some(10),
            "the preceding completion must not expire the ongoing action"
        );
        activity.complete(second, now + Duration::from_secs(1));
        assert_eq!(
            activity.active_target(now + Duration::from_secs(2)),
            None,
            "the indicator must expire after the last completed action"
        );
    }

    #[test]
    fn stale_guard_completion_cannot_hide_a_new_target() {
        let mut activity = Activity::new();
        let now = Instant::now();
        let (first, _) = activity.begin(10, false);
        let (second, _) = activity.begin(20, false);
        activity.complete(first, now);
        assert_eq!(
            activity.active_target(now + Duration::from_secs(1)),
            Some(20)
        );
        activity.complete(second, now);
        assert_eq!(activity.active_target(now + Duration::from_secs(1)), None);
    }

    #[test]
    fn cancellation_invalidates_delayed_cleanup_before_the_next_action() {
        let mut activity = Activity::new();
        let now = Instant::now();
        let (first, _) = activity.begin(10, false);
        let cleared_generation = activity.cancel();
        activity.complete(first, now);
        assert_eq!(activity.active_target(now), None);
        let (next, _) = activity.begin(20, false);
        assert_ne!(
            cleared_generation, next,
            "queued cancellation must lose ownership"
        );
        activity.complete(first, now);
        assert_eq!(
            activity.active_target(now + Duration::from_secs(1)),
            Some(20)
        );
    }

    #[test]
    fn held_actions_keep_the_border_through_long_gaps_until_task_clear() {
        let mut activity = Activity::new();
        let now = Instant::now();
        let (first, worker) = activity.begin(10, true);
        assert!(worker.is_some());
        activity.complete(first, now);
        assert_eq!(
            activity.active_target(now + Duration::from_secs(300)),
            Some(10),
            "model action completion must not expire a held task indicator"
        );
        let (second, another_worker) = activity.begin(10, true);
        assert!(
            another_worker.is_none(),
            "held actions must retain the same worker"
        );
        activity.complete(second, now + Duration::from_secs(300));
        assert_eq!(
            activity.active_target(now + Duration::from_secs(600)),
            Some(10)
        );
        activity.cancel();
        assert_eq!(
            activity.active_target(now + Duration::from_secs(600)),
            None,
            "task completion or cancellation must clear a held border"
        );
        activity.complete(second, now + Duration::from_secs(600));
        assert_eq!(
            activity.active_target(now + Duration::from_secs(601)),
            None,
            "a held guard must not restore its cancelled target"
        );
        let (manual, _) = activity.begin(20, false);
        activity.complete(manual, now + Duration::from_secs(601));
        assert_eq!(
            activity.active_target(now + Duration::from_secs(602)),
            None,
            "a later manual action must regain normal idle expiry"
        );
    }
}
