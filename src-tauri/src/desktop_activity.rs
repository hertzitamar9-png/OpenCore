//! A steady, click-through border. Never replaces the user's system cursor.
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::Manager;
use windows::Win32::Foundation::{HWND, RECT};
use windows::Win32::UI::WindowsAndMessaging::{
    GetAncestor, GetForegroundWindow, GetWindow, IsIconic, IsWindow, IsWindowVisible, SetWindowPos, ShowWindow,
    GA_ROOT, GW_HWNDPREV, HWND_TOP, SWP_NOACTIVATE, SWP_NOOWNERZORDER,
    SWP_SHOWWINDOW, SW_HIDE,
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
    .always_on_top(false)
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
    // Hide the actual HWND even if a prior interrupted update lost its state.
    if let Some(overlay) = app.get_webview_window("desktop-activity") {
        if let Ok(handle) = overlay.hwnd() {
            unsafe { let _ = ShowWindow(HWND(handle.0 as _), SW_HIDE); }
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
    // Closing to the tray keeps this process and the target app alive. Check
    // the owner at display time so held tasks and delayed actions cannot leave
    // a border behind, or restore one after the close handler cleared it.
    if !app.get_webview_window("main").is_some_and(|window| window.is_visible().unwrap_or(false)) {
        activity.cancel();
        hide(app, &mut activity);
        activity.worker = None;
        crate::desktop_capture::stop_capture();
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
            && IsWindowVisible(hwnd).as_bool()
            && !IsIconic(hwnd).as_bool()
    };
    // Recheck at display time: Stop may revoke access after an action's
    // initial authorization but before its first UI update was dispatched.
    if app.try_state::<std::sync::Arc<crate::AppCore>>().is_some_and(|core|
        crate::computer_access::check_window(&core.store, window_id).is_err()
            || crate::windows_control::validate_control_window(window_id).is_err()) {
        activity.cancel();
        hide(app, &mut activity);
        activity.worker = None;
        return false;
    }
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
    let placed = {
        (|| -> Result<(), String> {
            let _dpi = crate::desktop_capture::PhysicalDpiScope::new()?;
            let handle = overlay.hwnd().map_err(|error| error.to_string())?;
            unsafe {
                place_overlay(HWND(handle.0 as _), hwnd, geometry)
            }.map_err(|error| error.to_string())
        })()
    };
    if placed.is_err() {
        activity.cancel();
        hide(app, &mut activity);
        activity.worker = None;
        return false;
    }
    activity.geometry = Some(geometry);
    activity.visible = overlay.is_visible().unwrap_or(false);
    true
}

// Keep the border immediately above its app, below every covering window.
unsafe fn place_overlay(overlay: HWND, target: HWND, geometry: Geometry) -> windows::core::Result<()> {
    place_overlay_for_foreground(overlay, target, geometry, GetForegroundWindow())
}

unsafe fn place_overlay_for_foreground(overlay: HWND, target: HWND, geometry: Geometry, foreground: HWND) -> windows::core::Result<()> {
    // A background task can remain active while the user switches apps. Hide
    // its indicator entirely until the target is foreground again, including
    // when OpenCore itself covers the target. Never raise a border over it.
    if GetAncestor(foreground, GA_ROOT) != GetAncestor(target, GA_ROOT) {
        let _ = ShowWindow(overlay, SW_HIDE);
        return Ok(());
    }
    let mut previous = GetWindow(target, GW_HWNDPREV).unwrap_or_default();
    if previous == overlay { previous = GetWindow(overlay, GW_HWNDPREV).unwrap_or_default(); }
    if previous.0.is_null() { previous = HWND_TOP; }
    SetWindowPos(overlay, previous, geometry.position.0, geometry.position.1,
        geometry.size.0 as i32, geometry.size.1 as i32,
        SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_SHOWWINDOW)
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
    fn closing_to_tray_prevents_held_or_delayed_actions_from_showing_the_border() {
        use windows::core::w;
        use windows::Win32::UI::WindowsAndMessaging::{
            CreateWindowExW, DestroyWindow, SW_SHOWNOACTIVATE, WINDOW_EX_STYLE, WS_POPUP,
        };
        struct Target(HWND);
        impl Drop for Target {
            fn drop(&mut self) { unsafe { let _ = DestroyWindow(self.0); } }
        }
        let mut context = tauri::generate_context!();
        context.config_mut().app.windows.clear();
        let mut app = tauri::Builder::default().any_thread().build(context).unwrap();
        let main = tauri::WebviewWindowBuilder::new(app.handle(), "main",
            tauri::WebviewUrl::App("index.html".into()))
            .visible(false).focused(false).focusable(false).inner_size(160.0, 100.0)
            .build().unwrap();
        let overlay = build_overlay(app.handle()).unwrap();
        overlay.set_ignore_cursor_events(true).unwrap();
        let overlay_hwnd = HWND(overlay.hwnd().unwrap().0 as _);
        let target = Target(unsafe { CreateWindowExW(WINDOW_EX_STYLE::default(), w!("STATIC"),
            w!("OpenCore close cleanup fixture"), WS_POPUP, 40, 40, 120, 80,
            None, None, None, None) }.unwrap());
        unsafe { let _ = ShowWindow(target.0, SW_SHOWNOACTIVATE); }
        let target_id = target.0.0 as i64;
        let worker = {
            let mut activity = ACTIVITY.lock().unwrap_or_else(|error| error.into_inner());
            *activity = Activity::new();
            let (_, worker) = activity.begin(target_id, true);
            worker.unwrap()
        };
        // The selected application remains open when OpenCore closes to its tray.
        // A held action or an already queued action must not show an orphan border.
        let tracked = update(app.handle(), worker);
        let visible = unsafe { IsWindowVisible(overlay_hwnd) }.as_bool();
        let delayed_worker = {
            let mut activity = ACTIVITY.lock().unwrap_or_else(|error| error.into_inner());
            let (_, next_worker) = activity.begin(target_id, true);
            next_worker.or(activity.worker).unwrap()
        };
        let delayed_tracked = update(app.handle(), delayed_worker);
        let delayed_visible = unsafe { IsWindowVisible(overlay_hwnd) }.as_bool();
        // Opening OpenCore again permits a new action; closing must not disable
        // the computer-use feature permanently.
        unsafe { let _ = ShowWindow(HWND(main.hwnd().unwrap().0 as _), SW_SHOWNOACTIVATE); }
        let reopened_worker = {
            let mut activity = ACTIVITY.lock().unwrap_or_else(|error| error.into_inner());
            let (_, next_worker) = activity.begin(target_id, true);
            next_worker.or(activity.worker).unwrap()
        };
        let reopened_tracked = update(app.handle(), reopened_worker);
        {
            let mut activity = ACTIVITY.lock().unwrap_or_else(|error| error.into_inner());
            activity.cancel();
            hide(app.handle(), &mut activity);
            activity.worker = None;
        }
        overlay.destroy().unwrap();
        main.destroy().unwrap();
        #[allow(deprecated)]
        app.run_iteration(|_, _| {});
        assert!(!tracked, "hidden OpenCore must reject a delayed activity update");
        assert!(!visible, "a live target must not keep the border visible after OpenCore closes");
        assert!(!delayed_tracked && !delayed_visible, "a later action must not restore the closed owner's border");
        assert!(reopened_tracked, "a new action must work after OpenCore is reopened");
    }
    use windows::Win32::UI::WindowsAndMessaging::HWND_TOPMOST;

    #[test]
    #[ignore = "creates disposable native windows to verify compositor stacking"]
    fn live_overlay_stays_below_covering_window() {
        use windows::core::w;
        use windows::Win32::Foundation::POINT;
        use windows::Win32::UI::Input::KeyboardAndMouse::EnableWindow;
        use windows::Win32::UI::WindowsAndMessaging::{CreateWindowExW, DestroyWindow,
            WindowFromPoint, SetLayeredWindowAttributes, LWA_ALPHA, WINDOW_EX_STYLE,
            WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_NOACTIVATE, WS_POPUP, WS_VISIBLE, SWP_NOMOVE, SWP_NOSIZE};
        struct TestWindows(Vec<HWND>);
        impl Drop for TestWindows {
            fn drop(&mut self) { for hwnd in &self.0 { unsafe { let _ = DestroyWindow(*hwnd); } } }
        }
        let _dpi = crate::desktop_capture::PhysicalDpiScope::new().unwrap();
        unsafe {
            let mut windows = TestWindows(Vec::new());
            for title in [w!("OpenCore overlay test target"), w!("OpenCore overlay test cover"), w!("OpenCore overlay test indicator")] {
                // WindowFromPoint intentionally skips STATIC text controls.
                let style = if windows.0.len() == 2 { WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE }
                    else { WINDOW_EX_STYLE::default() };
                windows.0.push(CreateWindowExW(style, w!("BUTTON"), title,
                    WS_POPUP | WS_VISIBLE, 230, 190, 180, 180, None, None, None, None).unwrap());
            }
            let [target, cover, overlay] = windows.0[..] else { unreachable!() };
            SetLayeredWindowAttributes(overlay, windows::Win32::Foundation::COLORREF(0), 255, LWA_ALPHA).unwrap();
            let _ = EnableWindow(overlay, false);
            SetWindowPos(target, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE).unwrap();
            SetWindowPos(cover, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE).unwrap();
            let geometry = Geometry { position: (230, 190), size: (180, 180) };
            for _ in 0..3 { place_overlay_for_foreground(overlay, target, geometry, cover).unwrap(); }
            assert!(!IsWindowVisible(overlay).as_bool(), "the indicator must hide when a covering app is foreground");
            assert_eq!(WindowFromPoint(POINT { x: 250, y: 210 }), cover);
            let _ = ShowWindow(cover, SW_HIDE);
            place_overlay_for_foreground(overlay, target, geometry, target).unwrap();
            assert!(IsWindowVisible(overlay).as_bool());
            assert_eq!(WindowFromPoint(POINT { x: 250, y: 210 }), target, "indicator must not intercept input hit testing");
        }
    }

    #[test]
    fn activity_border_hides_when_another_app_is_in_front() {
        use windows::core::w;
        use windows::Win32::UI::WindowsAndMessaging::{CreateWindowExW, DestroyWindow,
            WINDOW_EX_STYLE, WS_POPUP, WS_VISIBLE};
        unsafe {
            let target = CreateWindowExW(WINDOW_EX_STYLE::default(), w!("BUTTON"),
                w!("Activity visibility target"), WS_POPUP | WS_VISIBLE, 40, 40, 120, 80,
                None, None, None, None).unwrap();
            let cover = CreateWindowExW(WINDOW_EX_STYLE::default(), w!("BUTTON"),
                w!("Activity visibility cover"), WS_POPUP | WS_VISIBLE, 40, 40, 120, 80,
                None, None, None, None).unwrap();
            let overlay = CreateWindowExW(WINDOW_EX_STYLE::default(), w!("BUTTON"),
                w!("Activity visibility indicator"), WS_POPUP, 40, 40, 120, 80,
                None, None, None, None).unwrap();
            let geometry = Geometry { position: (40, 40), size: (120, 80) };
            // Use real native windows with explicit foreground snapshots. The
            // OS may deny a background test process permission to steal focus.
            place_overlay_for_foreground(overlay, target, geometry, target).unwrap();
            let active_visible = IsWindowVisible(overlay).as_bool();
            place_overlay_for_foreground(overlay, target, geometry, cover).unwrap();
            let covered_visible = IsWindowVisible(overlay).as_bool();
            place_overlay_for_foreground(overlay, target, geometry, target).unwrap();
            let resumed_visible = IsWindowVisible(overlay).as_bool();
            for window in [overlay, cover, target] { let _ = DestroyWindow(window); }
            assert!(active_visible, "the selected foreground app should show its border");
            assert!(!covered_visible, "switching to OpenCore or another app must hide the border entirely");
            assert!(resumed_visible, "returning to the controlled app should restore an active task's border");
        }
    }

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
