//! Disposable native controls. These tests never operate installed applications.
use super::{cursor_position, platform};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};
use windows::core::w;
use windows::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::UI::WindowsAndMessaging::*;

static BUTTON_CLICKS: AtomicUsize = AtomicUsize::new(0);
static CANVAS_CLICKS: AtomicUsize = AtomicUsize::new(0);
static TARGET_ACTIVATIONS: AtomicUsize = AtomicUsize::new(0);
static TARGET_Z_CHANGES: AtomicUsize = AtomicUsize::new(0);

unsafe extern "system" fn target_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match message {
        WM_COMMAND if wparam.0 & 0xffff == 101 && wparam.0 >> 16 == 0 => {
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
    thread: Option<thread::JoinHandle<()>>,
}

fn hwnd(value: isize) -> HWND {
    HWND(value as *mut std::ffi::c_void)
}

impl Fixture {
    fn new() -> Self {
        assert_eq!(
            std::env::var("GITHUB_ACTIONS").as_deref(),
            Ok("true"),
            "Native fixture tests run only in GitHub Actions"
        );
        BUTTON_CLICKS.store(0, Ordering::SeqCst);
        CANVAS_CLICKS.store(0, Ordering::SeqCst);
        TARGET_ACTIVATIONS.store(0, Ordering::SeqCst);
        TARGET_Z_CHANGES.store(0, Ordering::SeqCst);
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
                ))
                .unwrap();
            let mut message = MSG::default();
            while GetMessageW(&mut message, None, 0, 0).0 > 0 {
                let _ = TranslateMessage(&message);
                DispatchMessageW(&message);
            }
            let _ = DestroyWindow(cover);
        });
        let (target, cover, button, edit, canvas) =
            receiver.recv_timeout(Duration::from_secs(10)).unwrap();
        Self {
            target,
            cover,
            button,
            edit,
            canvas,
            thread: Some(thread),
        }
    }

    fn args(&self, control: isize) -> Value {
        let (mut window, mut child) = (RECT::default(), RECT::default());
        unsafe {
            GetWindowRect(hwnd(self.target), &mut window).unwrap();
            GetWindowRect(hwnd(control), &mut child).unwrap();
        }
        json!({"windowId":self.target,
            "x":child.left - window.left + (child.right - child.left) / 2,
            "y":child.top - window.top + (child.bottom - child.top) / 2,
            "backgroundOnly":true,"allowForegroundFallback":true})
    }

    fn assert_covered(&self, args: &Value) {
        let mut rect = RECT::default();
        unsafe {
            GetWindowRect(hwnd(self.target), &mut rect).unwrap();
            let hit = WindowFromPoint(POINT {
                x: rect.left + args["x"].as_i64().unwrap() as i32,
                y: rect.top + args["y"].as_i64().unwrap() as i32,
            });
            assert_eq!(
                GetAncestor(hit, GA_ROOT),
                hwnd(self.cover),
                "target control must remain occluded by the fixture cover"
            );
        }
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

fn wait_for(mut predicate: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(3);
    while !predicate() {
        assert!(
            Instant::now() < deadline,
            "fixture operation did not complete"
        );
        thread::sleep(Duration::from_millis(10));
    }
}

#[test]
fn occluded_background_controls_never_activate_move_cursor_or_expose_target() {
    let fixture = Fixture::new();
    wait_for(|| unsafe { GetForegroundWindow() } == hwnd(fixture.cover));
    let foreground = unsafe { GetForegroundWindow() };
    let cursor = cursor_position().expect("fixture desktop cursor");
    let activations = TARGET_ACTIVATIONS.load(Ordering::SeqCst);
    let z_changes = TARGET_Z_CHANGES.load(Ordering::SeqCst);
    let button = fixture.args(fixture.button);
    fixture.assert_covered(&button);
    let clicked = platform::run("interact", &button).expect("occluded InvokePattern button");
    assert_eq!(clicked["activated"], true);
    wait_for(|| BUTTON_CLICKS.load(Ordering::SeqCst) == 1);
    assert_eq!(unsafe { GetForegroundWindow() }, foreground);
    assert_eq!(cursor_position(), Some(cursor));
    fixture.assert_covered(&button);

    let mut edit = fixture.args(fixture.edit);
    let inspected = platform::run("interact", &edit).expect("occluded ValuePattern edit");
    assert_eq!(inspected["editable"], true);
    assert_eq!(inspected["value"], "initial fixture text");
    edit["text"] = json!("background edit fixture result");
    let updated = platform::run("set_at", &edit).expect("background SetValue");
    assert_eq!(updated["updated"], true);
    assert_eq!(fixture.edit_text(), "background edit fixture result");
    assert_eq!(unsafe { GetForegroundWindow() }, foreground);
    assert_eq!(cursor_position(), Some(cursor));
    fixture.assert_covered(&edit);

    edit["text"] = json!("background apply fixture result");
    let applied = platform::run("commit_text", &edit)
        .expect("background value apply without unsupported submission");
    assert_eq!(applied["updated"], true);
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
    assert_eq!(unsafe { GetForegroundWindow() }, foreground);
    assert_eq!(cursor_position(), Some(cursor));
    fixture.assert_covered(&edit);

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
    assert_eq!(unsafe { GetForegroundWindow() }, foreground);
    assert_eq!(cursor_position(), Some(cursor));
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
    println!("background native fixture: button invoked, text updated, unsupported canvas/Enter rejected; foreground={:#x}, cursor=({}, {}), no target activation or z changes", foreground.0 as usize, cursor.0, cursor.1);
}
