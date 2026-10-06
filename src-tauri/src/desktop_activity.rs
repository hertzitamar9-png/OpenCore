//! A steady, click-through border. Never replaces the user's system cursor.
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::Manager;
use windows::Win32::Foundation::{HWND, RECT};
use windows::Win32::UI::WindowsAndMessaging::{GetWindowRect, IsIconic, IsWindow};

const IDLE_GRACE: Duration = Duration::from_millis(350);
const TRACK_INTERVAL: Duration = Duration::from_millis(32);

#[derive(Clone, Copy, PartialEq, Eq)]
struct Geometry {
    position: (i32, i32),
    size: (u32, u32),
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
    let mut rect = RECT::default();
    let valid = unsafe {
        IsWindow(hwnd).as_bool()
            && !IsIconic(hwnd).as_bool()
            && GetWindowRect(hwnd, &mut rect).is_ok()
    };
    let (width, height) = (rect.right - rect.left, rect.bottom - rect.top);
    if !valid || width <= 0 || height <= 0 {
        activity.cancel();
        hide(app, &mut activity);
        activity.worker = None;
        crate::desktop_capture::stop_capture();
        return false;
    }
    let Some(overlay) = app.get_webview_window("desktop-activity") else {
        activity.cancel();
        activity.visible = false;
        activity.geometry = None;
        activity.worker = None;
        return false;
    };
    let geometry = Geometry {
        position: (rect.left, rect.top),
        size: (width as u32, height as u32),
    };
    let positioned = if activity.geometry.map(|old| old.position) != Some(geometry.position) {
        overlay.set_position(tauri::PhysicalPosition::new(rect.left, rect.top))
    } else {
        Ok(())
    };
    let sized =
        if positioned.is_ok() && activity.geometry.map(|old| old.size) != Some(geometry.size) {
            overlay.set_size(tauri::PhysicalSize::new(width as u32, height as u32))
        } else {
            Ok(())
        };
    if positioned.is_err() || sized.is_err() || (!activity.visible && overlay.show().is_err()) {
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
