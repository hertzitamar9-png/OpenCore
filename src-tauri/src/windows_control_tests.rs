//! Disposable native controls. These tests never operate installed applications.
use super::{cursor_position, platform};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};
use windows::core::{w, HSTRING};
use windows::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::UI::WindowsAndMessaging::*;

static BUTTON_CLICKS: AtomicUsize = AtomicUsize::new(0);
static BUTTON_NOTIFICATION_SOURCE: AtomicUsize = AtomicUsize::new(0);
static CANVAS_CLICKS: AtomicUsize = AtomicUsize::new(0);
static TARGET_ACTIVATIONS: AtomicUsize = AtomicUsize::new(0);
static TARGET_Z_CHANGES: AtomicUsize = AtomicUsize::new(0);
static DELAY_BUTTON_HANDLER: AtomicBool = AtomicBool::new(false);
static BUTTON_HANDLER_ENTERED: AtomicBool = AtomicBool::new(false);
static INDEPENDENT_INPUT_COMPLETE: AtomicBool = AtomicBool::new(false);
static BUTTON_HANDLER_SAW_INPUT: AtomicBool = AtomicBool::new(false);
static TRY_TARGET_FOREGROUND: AtomicBool = AtomicBool::new(false);
static TARGET_FOREGROUND_ALLOWED: AtomicBool = AtomicBool::new(false);

unsafe extern "system" fn target_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match message {
        WM_COMMAND if wparam.0 & 0xffff == 101 && wparam.0 >> 16 == 0 => {
            BUTTON_NOTIFICATION_SOURCE.store(lparam.0 as usize, Ordering::SeqCst);
            if TRY_TARGET_FOREGROUND.swap(false, Ordering::SeqCst) {
                TARGET_FOREGROUND_ALLOWED.store(SetForegroundWindow(hwnd).as_bool(), Ordering::SeqCst);
            }
            if DELAY_BUTTON_HANDLER.swap(false, Ordering::SeqCst) {
                BUTTON_HANDLER_ENTERED.store(true, Ordering::SeqCst);
                let deadline = Instant::now() + Duration::from_millis(750);
                while !INDEPENDENT_INPUT_COMPLETE.load(Ordering::SeqCst)
                    && Instant::now() < deadline
                {
                    thread::sleep(Duration::from_millis(1));
                }
                BUTTON_HANDLER_SAW_INPUT.store(
                    INDEPENDENT_INPUT_COMPLETE.load(Ordering::SeqCst),
                    Ordering::SeqCst,
                );
            }
            BUTTON_CLICKS.fetch_add(1, Ordering::SeqCst);
            LRESULT(0)
        }
        WM_ACTIVATE if wparam.0 & 0xffff != 0 => {
            TARGET_ACTIVATIONS.fetch_add(1, Ordering::SeqCst);
            DefWindowProcW(hwnd, message, wparam, lparam)
        }
        WM_WINDOWPOSCHANGING => {
            TARGET_Z_CHANGES.fetch_add(1, Ordering::SeqCst);
            DefWindowProcW(hwnd, message, wparam, lparam)
        }
        WM_CLOSE => {
            let _ = DestroyWindow(hwnd);
            PostQuitMessage(0);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, message, wparam, lparam),
    }
}

unsafe extern "system" fn canvas_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    if message == WM_LBUTTONDOWN {
        CANVAS_CLICKS.fetch_add(1, Ordering::SeqCst);
    }
    DefWindowProcW(hwnd, message, wparam, lparam)
}

struct Fixture {
    target: isize,
    cover: isize,
    button: isize,
    edit: isize,
    canvas: isize,
    list: isize,
    thread: Option<thread::JoinHandle<()>>,
}

fn hwnd(value: isize) -> HWND {
    HWND(value as *mut std::ffi::c_void)
}

fn window_description(window: HWND) -> String {
    let (mut title, mut class) = ([0u16; 256], [0u16; 256]);
    let (title_len, class_len) = unsafe {
        (
            GetWindowTextW(window, &mut title),
            GetClassNameW(window, &mut class),
        )
    };
    format!(
        "HWND={:#x}, class={:?}, title={:?}",
        window.0 as usize,
        String::from_utf16_lossy(&class[..class_len.max(0) as usize]),
        String::from_utf16_lossy(&title[..title_len.max(0) as usize])
    )
}

impl Fixture {
    fn new() -> Self {
        assert_eq!(
            std::env::var("GITHUB_ACTIONS").as_deref(),
            Ok("true"),
            "Native fixture tests run only in GitHub Actions"
        );
        BUTTON_CLICKS.store(0, Ordering::SeqCst);
        BUTTON_NOTIFICATION_SOURCE.store(0, Ordering::SeqCst);
        CANVAS_CLICKS.store(0, Ordering::SeqCst);
        TARGET_ACTIVATIONS.store(0, Ordering::SeqCst);
        TARGET_Z_CHANGES.store(0, Ordering::SeqCst);
        DELAY_BUTTON_HANDLER.store(false, Ordering::SeqCst);
        BUTTON_HANDLER_ENTERED.store(false, Ordering::SeqCst);
        INDEPENDENT_INPUT_COMPLETE.store(false, Ordering::SeqCst);
        BUTTON_HANDLER_SAW_INPUT.store(false, Ordering::SeqCst);
        TRY_TARGET_FOREGROUND.store(false, Ordering::SeqCst);
        TARGET_FOREGROUND_ALLOWED.store(false, Ordering::SeqCst);
        let (sender, receiver) = mpsc::sync_channel(1);
        let thread = thread::spawn(move || unsafe {
            let target = CreateWindowExW(
                WINDOW_EX_STYLE::default(),
                w!("STATIC"),
                w!("OpenCore Background Target Fixture"),
                WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                40,
                40,
                520,
                440,
                None,
                None,
                HINSTANCE::default(),
                None,
            )
            .unwrap();
            SetWindowLongPtrW(target, GWLP_WNDPROC, target_proc as *const () as isize);
            let button = CreateWindowExW(
                WINDOW_EX_STYLE::default(),
                w!("BUTTON"),
                w!("Background fixture action"),
                WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                20,
                20,
                230,
                36,
                target,
                HMENU(101usize as *mut std::ffi::c_void),
                HINSTANCE::default(),
                None,
            )
            .unwrap();
            let edit = CreateWindowExW(
                WS_EX_CLIENTEDGE,
                w!("EDIT"),
                w!("initial fixture text"),
                WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                20,
                75,
                350,
                34,
                target,
                None,
                HINSTANCE::default(),
                None,
            )
            .unwrap();
            let canvas = CreateWindowExW(
                WINDOW_EX_STYLE::default(),
                w!("STATIC"),
                w!("Unsupported fixture canvas"),
                WS_CHILD | WS_VISIBLE,
                20,
                130,
                350,
                170,
                target,
                None,
                HINSTANCE::default(),
                None,
            )
            .unwrap();
            SetWindowLongPtrW(canvas, GWLP_WNDPROC, canvas_proc as *const () as isize);
            let list = CreateWindowExW(
                WS_EX_CLIENTEDGE,
                w!("LISTBOX"),
                w!("Background fixture scrolling"),
                WS_CHILD | WS_VISIBLE | WS_VSCROLL | WINDOW_STYLE(LBS_NOINTEGRALHEIGHT as u32),
                20,
                315,
                350,
                75,
                target,
                None,
                HINSTANCE::default(),
                None,
            )
            .unwrap();
            for row in 0..100 {
                let text = HSTRING::from(format!("Fixture row {row}"));
                SendMessageW(
                    list,
                    LB_ADDSTRING,
                    WPARAM(0),
                    LPARAM(text.as_ptr() as isize),
                );
            }
            let cover = CreateWindowExW(
                WINDOW_EX_STYLE::default(),
                w!("STATIC"),
                w!("OpenCore Foreground Cover Fixture"),
                WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                40,
                40,
                520,
                440,
                None,
                None,
                HINSTANCE::default(),
                None,
            )
            .unwrap();
            let _ = SetForegroundWindow(cover);
            SetWindowPos(cover, HWND_TOP, 40, 40, 520, 440, SWP_SHOWWINDOW).unwrap();
            sender
                .send((
                    target.0 as isize,
                    cover.0 as isize,
                    button.0 as isize,
                    edit.0 as isize,
                    canvas.0 as isize,
                    list.0 as isize,
                ))
                .unwrap();
            let mut message = MSG::default();
            while GetMessageW(&mut message, None, 0, 0).0 > 0 {
                let _ = TranslateMessage(&message);
                DispatchMessageW(&message);
            }
            let _ = DestroyWindow(cover);
        });
        let (target, cover, button, edit, canvas, list) =
            receiver.recv_timeout(Duration::from_secs(10)).unwrap();
        Self {
            target,
            cover,
            button,
            edit,
            canvas,
            list,
            thread: Some(thread),
        }
    }

    fn args(&self, control: isize) -> Value {
        let _dpi = crate::desktop_capture::PhysicalDpiScope::new().unwrap();
        let (mut window, mut child) = (RECT::default(), RECT::default());
        unsafe {
            GetWindowRect(hwnd(self.target), &mut window).unwrap();
            GetWindowRect(hwnd(control), &mut child).unwrap();
        }
        json!({"windowId":self.target,
            "x":child.left - window.left + (child.right - child.left) / 2,
            "y":child.top - window.top + (child.bottom - child.top) / 2,
            "backgroundOnly":true,"allowForegroundFallback":true,"manualControl":true})
    }

    fn assert_covered(&self, args: &Value) {
        let _dpi = crate::desktop_capture::PhysicalDpiScope::new().unwrap();
        let mut rect = RECT::default();
        unsafe {
            GetWindowRect(hwnd(self.target), &mut rect).unwrap();
            let hit = WindowFromPoint(POINT {
                x: rect.left + args["x"].as_i64().unwrap() as i32,
                y: rect.top + args["y"].as_i64().unwrap() as i32,
            });
            let hit_root = GetAncestor(hit, GA_ROOT);
            assert_eq!(
                hit_root,
                hwnd(self.cover),
                "target control must remain occluded; actual {}; expected {}",
                window_description(hit_root),
                window_description(hwnd(self.cover))
            );
        }
    }

    fn assert_desktop_unchanged(
        &self,
        operation: &str,
        foreground: HWND,
        cursor: (i32, i32),
        activations: usize,
        z_changes: usize,
    ) {
        let current = unsafe { GetForegroundWindow() };
        assert_eq!(
            current,
            foreground,
            "{operation}: foreground changed; actual {}; expected {}; target {}",
            window_description(current),
            window_description(foreground),
            window_description(hwnd(self.target))
        );
        assert_eq!(
            cursor_position(),
            Some(cursor),
            "{operation}: desktop cursor changed"
        );
        assert_eq!(
            TARGET_ACTIVATIONS.load(Ordering::SeqCst),
            activations,
            "{operation}: target must not be briefly activated; {}",
            window_description(hwnd(self.target))
        );
        assert_eq!(
            TARGET_Z_CHANGES.load(Ordering::SeqCst),
            z_changes,
            "{operation}: target must not be temporarily exposed or reordered; {}",
            window_description(hwnd(self.target))
        );
    }

    fn edit_text(&self) -> String {
        let mut text = [0u16; 512];
        let len = unsafe { GetWindowTextW(hwnd(self.edit), &mut text) };
        String::from_utf16_lossy(&text[..len as usize])
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        unsafe {
            let _ = PostMessageW(hwnd(self.target), WM_CLOSE, WPARAM(0), LPARAM(0));
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn wait_for(operation: &str, mut predicate: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(3);
    while !predicate() {
        assert!(
            Instant::now() < deadline,
            "{operation}: fixture operation did not complete"
        );
        thread::sleep(Duration::from_millis(10));
    }
}

fn verify_headless_helper(fixture: &Fixture, button: &Value, foreground: HWND,
    cursor: (i32, i32), activations: usize, z_changes: usize) {
    use windows::Win32::Foundation::{CloseHandle, HANDLE};
    use windows::Win32::System::Threading::{GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION};

    struct ProcessHandle(HANDLE);
    impl Drop for ProcessHandle {
        fn drop(&mut self) { unsafe { let _ = CloseHandle(self.0); } }
    }
    struct Receipt(std::path::PathBuf);
    impl Drop for Receipt {
        fn drop(&mut self) { let _ = std::fs::remove_file(&self.0); }
    }
    let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();

    // Exercise the actual parent/helper handshake, including a same-process
    // target notification handler that tries to activate its own window.
    let clicks = BUTTON_CLICKS.load(Ordering::SeqCst);
    TRY_TARGET_FOREGROUND.store(true, Ordering::SeqCst);
    let delegated = runtime.block_on(crate::windows_control::command("interact".into(), button.clone()))
        .expect("headless helper must invoke the covered fixture control");
    assert_eq!(delegated["activated"], true);
    assert_eq!(delegated["inputMode"], "window-message");
    assert!(!TARGET_FOREGROUND_ALLOWED.load(Ordering::SeqCst),
        "foreground protection must cover a same-process target notification handler");
    assert_eq!(BUTTON_CLICKS.load(Ordering::SeqCst), clicks + 1);
    fixture.assert_desktop_unchanged("headless button dispatch", foreground, cursor, activations, z_changes);
    fixture.assert_covered(button);

    let mut edit = fixture.args(fixture.edit);
    edit["text"] = json!("headless helper background text");
    let delegated = runtime.block_on(crate::windows_control::command("commit_text".into(), edit))
        .expect("headless helper must update the covered native edit");
    assert_eq!(delegated["updated"], true);
    assert_eq!(delegated["submitted"], false);
    assert_eq!(fixture.edit_text(), "headless helper background text");
    fixture.assert_desktop_unchanged("headless text dispatch", foreground, cursor, activations, z_changes);

    // Both an expired dispatch and a dropped command future must end their
    // exact owned subprocess, then release foreground protection. Hold its
    // process handle before teardown so an exited/reused PID cannot fake this.
    for cancel in [false, true] {
        let activations = TARGET_ACTIVATIONS.load(Ordering::SeqCst);
        let z_changes = TARGET_Z_CHANGES.load(Ordering::SeqCst);
        let receipt = Receipt(std::env::temp_dir().join(format!("opencore-desktop-helper-{}.pid", uuid::Uuid::new_v4())));
        let mut args = button.clone();
        args["processReceipt"] = json!(receipt.0.to_string_lossy());
        let mut command = crate::desktop_helper::helper_command().unwrap();
        command.env("OPENCORE_DESKTOP_HELPER_TEST_MODE", "hang_dispatch");
        let started = Instant::now();
        runtime.block_on(async {
            let mut operation = Box::pin(crate::desktop_helper::execute("interact".into(), args, command));
            let process = loop {
                tokio::select! {
                    result = &mut operation => panic!("blocked fixture ended before its receipt: {result:?}"),
                    _ = tokio::time::sleep(Duration::from_millis(10)) => {}
                }
                if let Some(pid) = std::fs::read_to_string(&receipt.0).ok().and_then(|text| text.parse::<u32>().ok()) {
                    break ProcessHandle(unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }.unwrap());
                }
                assert!(started.elapsed() < Duration::from_secs(10), "headless fixture never reached its dispatch");
            };
            if cancel {
                drop(operation);
            } else {
                let error = operation.await.unwrap_err();
                assert!(error.contains("dispatch deadline") && error.contains("may have completed"), "{error}");
            }
            let deadline = Instant::now() + Duration::from_secs(2);
            loop {
                let mut code = 259u32; // STILL_ACTIVE returned by GetExitCodeProcess.
                unsafe { GetExitCodeProcess(process.0, &mut code).unwrap(); }
                if code != 259 { break; }
                assert!(Instant::now() < deadline, "the owned blocked desktop helper survived teardown");
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        });
        assert!(started.elapsed() < Duration::from_secs(10), "blocked manual dispatch retained its lock too long");
        fixture.assert_desktop_unchanged("blocked helper teardown", foreground, cursor, activations, z_changes);
        assert!(unsafe { SetForegroundWindow(hwnd(fixture.target)) }.as_bool(),
            "helper teardown must release its foreground lock; this activation is fixture verification only");
        wait_for("released helper foreground verification", || unsafe { GetForegroundWindow() } == hwnd(fixture.target));
        assert!(unsafe { SetForegroundWindow(hwnd(fixture.cover)) }.as_bool());
        wait_for("helper foreground fixture restore", || unsafe { GetForegroundWindow() } == hwnd(fixture.cover));
    }
}

#[test]
fn strict_accessibility_client_disables_automatic_pattern_focus() {
    assert_eq!(
        std::env::var("GITHUB_ACTIONS").as_deref(),
        Ok("true"),
        "native accessibility tests must run only on GitHub Actions"
    );
    assert!(
        !platform::background_auto_set_focus_for_test()
            .expect("strict production client must expose verified automatic focus control"),
        "every strict client must disable automatic focus before obtaining controls or patterns"
    );
}

#[test]
fn occluded_background_controls_never_activate_move_cursor_or_expose_target() {
    let fixture = Fixture::new();
    wait_for(
        "foreground fixture setup",
        || unsafe { GetForegroundWindow() } == hwnd(fixture.cover),
    );
    // The same process's UIA client and an application's notification handler
    // can both attempt activation. The production manual dispatch guard must
    // block an explicit SetForegroundWindow call too, then release on errors.
    let manual = json!({"manualControl":true});
    let cover = unsafe { GetForegroundWindow() };
    let activated = platform::manual_dispatch(&manual, || {
        Ok(unsafe { SetForegroundWindow(hwnd(fixture.target)) }.as_bool())
    }).expect("manual dispatch must protect its foreground process");
    assert!(!activated, "the dispatch must deny target foreground activation, including a same-process call");
    assert_eq!(unsafe { GetForegroundWindow() }, cover);
    let exact_error: Result<(), String> = platform::manual_dispatch(&manual, || Err("fixture dispatch error".into()));
    assert_eq!(exact_error, Err("fixture dispatch error".into()));
    assert!(unsafe { SetForegroundWindow(hwnd(fixture.target)) }.as_bool(),
        "a failed dispatch must release its lock; this activation is fixture verification only");
    wait_for("released dispatch foreground verification", || unsafe { GetForegroundWindow() } == hwnd(fixture.target));
    assert!(unsafe { SetForegroundWindow(hwnd(fixture.cover)) }.as_bool());
    wait_for("foreground fixture restore after dispatch verification", || unsafe { GetForegroundWindow() } == hwnd(fixture.cover));

    // A worker entering from an unaware thread must obtain physical rectangles
    // and restore that thread's exact original context when finished.
    use windows::Win32::UI::HiDpi::{AreDpiAwarenessContextsEqual, GetThreadDpiAwarenessContext,
        SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT_UNAWARE};
    let original_dpi = unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_UNAWARE) };
    assert!(!original_dpi.0.is_null());
    let physical = crate::desktop_capture::physical_window_rect(fixture.target)
        .expect("physical rectangle must be available from an unaware worker");
    assert!(unsafe { AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(), DPI_AWARENESS_CONTEXT_UNAWARE) }.as_bool(),
        "physical bounds must restore the worker's DPI context");
    let expected = {
        let _dpi = crate::desktop_capture::PhysicalDpiScope::new().unwrap();
        let mut rect = RECT::default();
        unsafe { GetWindowRect(hwnd(fixture.target), &mut rect).unwrap(); }
        rect
    };
    assert_eq!((physical.left, physical.top, physical.right, physical.bottom),
        (expected.left, expected.top, expected.right, expected.bottom));
    unsafe { SetThreadDpiAwarenessContext(original_dpi); }
    let foreground = unsafe { GetForegroundWindow() };
    let cursor = cursor_position().expect("fixture desktop cursor");
    let activations = TARGET_ACTIVATIONS.load(Ordering::SeqCst);
    let z_changes = TARGET_Z_CHANGES.load(Ordering::SeqCst);
    println!(
        "background fixture setup: target {}; cover {}; button {}; cursor={cursor:?}",
        window_description(hwnd(fixture.target)),
        window_description(hwnd(fixture.cover)),
        window_description(hwnd(fixture.button))
    );
    let button = fixture.args(fixture.button);
    fixture.assert_covered(&button);
    let clicked = platform::run("interact", &button).expect("occluded background button interact");
    assert_eq!(clicked["activated"], true);
    assert_eq!(clicked["inputMode"], "window-message");
    assert_eq!(clicked["notification"], "BN_CLICKED");
    wait_for("background button interact notification", || {
        BUTTON_CLICKS.load(Ordering::SeqCst) == 1
    });
    assert_eq!(
        BUTTON_NOTIFICATION_SOURCE.load(Ordering::SeqCst),
        fixture.button as usize,
        "BN_CLICKED LPARAM must identify the exact native button"
    );
    fixture.assert_desktop_unchanged(
        "button interact",
        foreground,
        cursor,
        activations,
        z_changes,
    );
    fixture.assert_covered(&button);

    let tree = platform::run("inspect", &button).expect("inspect occluded native button");
    let element_id = tree["elements"]
        .as_array()
        .unwrap()
        .iter()
        .find(|element| element["name"] == "Background fixture action")
        .expect("fixture button must have an inspect elementId")["elementId"]
        .clone();
    let mut direct = button.clone();
    direct["elementId"] = element_id;
    let invoked =
        platform::run("invoke", &direct).expect("occluded background button direct invoke");
    assert_eq!(invoked["activated"], true);
    assert_eq!(invoked["inputMode"], "window-message");
    assert_eq!(invoked["notification"], "BN_CLICKED");
    wait_for("background button direct invoke notification", || {
        BUTTON_CLICKS.load(Ordering::SeqCst) == 2
    });
    assert_eq!(
        BUTTON_NOTIFICATION_SOURCE.load(Ordering::SeqCst),
        fixture.button as usize
    );
    fixture.assert_desktop_unchanged(
        "button direct invoke",
        foreground,
        cursor,
        activations,
        z_changes,
    );
    fixture.assert_covered(&button);

    let mut edit = fixture.args(fixture.edit);
    let inspected = platform::run("interact", &edit).expect("occluded ValuePattern edit");
    assert_eq!(inspected["editable"], true);
    assert_eq!(inspected["value"], "initial fixture text");
    edit["text"] = json!("background edit fixture result");
    let updated = platform::run("set_at", &edit).expect("background SetValue");
    assert_eq!(updated["updated"], true);
    assert_eq!(updated["inputMode"], "window-message");
    assert_eq!(fixture.edit_text(), "background edit fixture result");
    fixture.assert_desktop_unchanged(
        "ValuePattern edit update",
        foreground,
        cursor,
        activations,
        z_changes,
    );
    fixture.assert_covered(&edit);

    let edit_element_id = tree["elements"]
        .as_array()
        .unwrap()
        .iter()
        .find(|element| element["controlType"] == "Edit")
        .expect("fixture edit must have an inspect elementId")["elementId"]
        .clone();
    let mut direct_edit = edit.clone();
    direct_edit["elementId"] = edit_element_id;
    direct_edit["text"] = json!("background direct value fixture result");
    let direct_updated =
        platform::run("set_value", &direct_edit).expect("occluded native edit direct set_value");
    assert_eq!(direct_updated["updated"], true);
    assert_eq!(direct_updated["inputMode"], "window-message");
    assert_eq!(
        fixture.edit_text(),
        "background direct value fixture result"
    );
    fixture.assert_desktop_unchanged(
        "direct native edit update",
        foreground,
        cursor,
        activations,
        z_changes,
    );
    fixture.assert_covered(&edit);

    edit["text"] = json!("background apply fixture result");
    let applied = platform::run("commit_text", &edit)
        .expect("background value apply without unsupported submission");
    assert_eq!(applied["updated"], true);
    assert_eq!(applied["inputMode"], "window-message");
    assert_eq!(
        applied["submitted"], false,
        "ValuePattern does not prove submission"
    );
    assert!(applied["message"]
        .as_str()
        .is_some_and(|text| !text.is_empty()));
    assert_eq!(fixture.edit_text(), "background apply fixture result");
    assert!(
        platform::run("commit_enter", &edit).is_err(),
        "unsupported Enter must not steal focus"
    );
    fixture.assert_desktop_unchanged(
        "text apply and unsupported Enter",
        foreground,
        cursor,
        activations,
        z_changes,
    );
    fixture.assert_covered(&edit);

    // WM_SETTEXT itself can replace read-only text, so the native path must
    // enforce the control's state before sending the update notification.
    const EM_SETREADONLY: u32 = 0x00cf;
    assert_ne!(
        unsafe { SendMessageW(hwnd(fixture.edit), EM_SETREADONLY, WPARAM(1), LPARAM(0)).0 },
        0
    );
    direct_edit["text"] = json!("must not replace read-only fixture text");
    assert!(platform::run("set_value", &direct_edit).is_err());
    assert!(platform::run("set_at", &direct_edit).is_err());
    assert!(platform::run("commit_text", &direct_edit).is_err());
    assert_eq!(fixture.edit_text(), "background apply fixture result");
    fixture.assert_desktop_unchanged(
        "read-only native edit rejection",
        foreground,
        cursor,
        activations,
        z_changes,
    );
    unsafe {
        SendMessageW(hwnd(fixture.edit), EM_SETREADONLY, WPARAM(0), LPARAM(0));
        let _ =
            windows::Win32::UI::Input::KeyboardAndMouse::EnableWindow(hwnd(fixture.edit), false);
    }
    assert!(platform::run("set_value", &direct_edit).is_err());
    assert_eq!(fixture.edit_text(), "background apply fixture result");
    fixture.assert_desktop_unchanged(
        "disabled native edit rejection",
        foreground,
        cursor,
        activations,
        z_changes,
    );
    unsafe {
        let _ = windows::Win32::UI::Input::KeyboardAndMouse::EnableWindow(hwnd(fixture.edit), true);
    }
    fixture.assert_covered(&edit);

    let mut scroll = fixture.args(fixture.list);
    scroll["direction"] = json!("down");
    let before_scroll =
        unsafe { SendMessageW(hwnd(fixture.list), LB_GETTOPINDEX, WPARAM(0), LPARAM(0)).0 };
    let selected_before_scroll =
        unsafe { SendMessageW(hwnd(fixture.list), LB_GETCURSEL, WPARAM(0), LPARAM(0)).0 };
    let scrolled = platform::run("scroll_at", &scroll).expect("occluded ScrollPattern list");
    assert_eq!(scrolled["scrolled"], true);
    assert_eq!(scrolled["inputMode"], "accessibility");
    assert_eq!(scrolled["backgroundVerified"], true);
    let after_scroll =
        unsafe { SendMessageW(hwnd(fixture.list), LB_GETTOPINDEX, WPARAM(0), LPARAM(0)).0 };
    assert!(
        after_scroll > before_scroll,
        "background scrolling must change the real visible list position"
    );
    fixture.assert_desktop_unchanged(
        "ScrollPattern list scroll",
        foreground,
        cursor,
        activations,
        z_changes,
    );
    fixture.assert_covered(&scroll);
    assert_eq!(
        unsafe { SendMessageW(hwnd(fixture.list), LB_GETCURSEL, WPARAM(0), LPARAM(0)).0 },
        selected_before_scroll,
        "background scroll must not change the selected list item"
    );

    scroll["direction"] = json!("up");
    let scrolled_up =
        platform::run("scroll_at", &scroll).expect("occluded upward ScrollPattern list");
    assert_eq!(scrolled_up["scrolled"], true);
    assert_eq!(scrolled_up["inputMode"], "accessibility");
    assert_eq!(scrolled_up["backgroundVerified"], true);
    let after_up_scroll =
        unsafe { SendMessageW(hwnd(fixture.list), LB_GETTOPINDEX, WPARAM(0), LPARAM(0)).0 };
    assert!(
        after_up_scroll < after_scroll,
        "background upward scroll must change the real visible list position"
    );
    assert_eq!(
        unsafe { SendMessageW(hwnd(fixture.list), LB_GETCURSEL, WPARAM(0), LPARAM(0)).0 },
        selected_before_scroll,
        "background upward scroll must not change the selected list item"
    );
    fixture.assert_desktop_unchanged(
        "upward ScrollPattern list scroll",
        foreground,
        cursor,
        activations,
        z_changes,
    );
    fixture.assert_covered(&scroll);

    let mut canvas = fixture.args(fixture.canvas);
    let error = platform::run("interact", &canvas).unwrap_err();
    assert!(
        error.contains("background") || error.contains("foreground"),
        "{error}"
    );
    canvas["text"] = json!("never inject this");
    assert!(platform::run("commit_text", &canvas).is_err());
    assert!(platform::run("commit_enter", &canvas).is_err());
    canvas["direction"] = json!("down");
    assert!(platform::run("scroll_at", &canvas).is_err());
    assert_eq!(
        CANVAS_CLICKS.load(Ordering::SeqCst),
        0,
        "canvas must never receive pointer fallback"
    );
    fixture.assert_desktop_unchanged(
        "unsupported canvas actions",
        foreground,
        cursor,
        activations,
        z_changes,
    );
    fixture.assert_covered(&canvas);
    assert_eq!(
        TARGET_ACTIVATIONS.load(Ordering::SeqCst),
        activations,
        "target must not be briefly activated"
    );
    assert_eq!(
        TARGET_Z_CHANGES.load(Ordering::SeqCst),
        z_changes,
        "target must not be temporarily exposed or reordered"
    );
    println!("background native fixture: button invoked, text updated, list scrolled {}->{}, unsupported canvas/Enter rejected; foreground={:#x}, cursor=({}, {}), no target activation or z changes", before_scroll, after_scroll, foreground.0 as usize, cursor.0, cursor.1);

    verify_headless_helper(&fixture, &button, foreground, cursor, activations, z_changes);

    // Change desktop input from another thread only after the real notification
    // handler begins. Its completed button outcome must survive the warning,
    // and the background action must not restore the independent cursor input.
    let cursor_before_delay = cursor_position().unwrap();
    let clicks_before_delay = BUTTON_CLICKS.load(Ordering::SeqCst);
    let activations = TARGET_ACTIVATIONS.load(Ordering::SeqCst);
    let z_changes = TARGET_Z_CHANGES.load(Ordering::SeqCst);
    let (left, width) = unsafe {
        (
            GetSystemMetrics(SM_XVIRTUALSCREEN),
            GetSystemMetrics(SM_CXVIRTUALSCREEN),
        )
    };
    assert!(
        width > 1,
        "fixture needs a desktop wide enough for independent input"
    );
    let independent_cursor = (
        if cursor_before_delay.0 < left + width - 1 {
            cursor_before_delay.0 + 1
        } else {
            cursor_before_delay.0 - 1
        },
        cursor_before_delay.1,
    );
    DELAY_BUTTON_HANDLER.store(true, Ordering::SeqCst);
    let independent_input = thread::spawn(move || {
        wait_for("delayed button handler entry", || {
            BUTTON_HANDLER_ENTERED.load(Ordering::SeqCst)
        });
        unsafe { SetCursorPos(independent_cursor.0, independent_cursor.1) }
            .expect("independent fixture cursor input");
        assert_eq!(cursor_position(), Some(independent_cursor));
        INDEPENDENT_INPUT_COMPLETE.store(true, Ordering::SeqCst);
    });
    let completed = platform::run("interact", &button);
    independent_input
        .join()
        .expect("independent fixture input thread");
    let completed = completed
        .expect("completed button activation must not become an error after independent input");
    assert!(
        BUTTON_HANDLER_SAW_INPUT.load(Ordering::SeqCst),
        "independent input must occur before the successful handler returns"
    );
    assert_eq!(
        BUTTON_CLICKS.load(Ordering::SeqCst),
        clicks_before_delay + 1,
        "the notification must execute once; a warning must not invite duplicate activation"
    );
    assert_eq!(completed["activated"], true);
    assert_eq!(completed["inputMode"], "window-message");
    assert_eq!(completed["backgroundVerified"], false);
    assert_eq!(completed["warning"]["code"], "desktop_state_changed");
    assert_eq!(completed["warning"]["cause"], "unknown");
    assert_eq!(completed["warning"]["foregroundChanged"], false);
    assert_eq!(completed["warning"]["cursorChanged"], true);
    assert_eq!(completed["warning"]["verifyOutcomeBeforeRetry"], true);
    assert!(completed["message"]
        .as_str()
        .unwrap()
        .contains("Verify the outcome before retrying"));
    fixture.assert_desktop_unchanged(
        "completed action with independent input",
        foreground,
        independent_cursor,
        activations,
        z_changes,
    );
    fixture.assert_covered(&button);
}
