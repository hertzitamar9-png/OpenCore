//! Disposable native controls. These tests never operate installed applications.
use super::{cursor_position, platform};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicUsize, Ordering};
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

unsafe extern "system" fn target_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match message {
        WM_COMMAND if wparam.0 & 0xffff == 101 && wparam.0 >> 16 == 0 => {
            BUTTON_NOTIFICATION_SOURCE.store(lparam.0 as usize, Ordering::SeqCst);
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

#[test]
fn occluded_background_controls_never_activate_move_cursor_or_expose_target() {
    let fixture = Fixture::new();
    wait_for(
        "foreground fixture setup",
        || unsafe { GetForegroundWindow() } == hwnd(fixture.cover),
    );
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
    assert_eq!(fixture.edit_text(), "background edit fixture result");
    fixture.assert_desktop_unchanged(
        "ValuePattern edit update",
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

    let mut scroll = fixture.args(fixture.list);
    scroll["direction"] = json!("down");
    let before_scroll =
        unsafe { SendMessageW(hwnd(fixture.list), LB_GETTOPINDEX, WPARAM(0), LPARAM(0)).0 };
    let scrolled = platform::run("scroll_at", &scroll).expect("occluded ScrollPattern list");
    assert_eq!(scrolled["scrolled"], true);
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
}
