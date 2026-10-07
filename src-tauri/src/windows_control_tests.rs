//! Disposable native controls. These tests never operate installed applications.
use super::{cursor_position, platform};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};
use windows::core::{w, HSTRING};
use windows::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::UI::Input::KeyboardAndMouse::GetActiveWindow;
use windows::Win32::UI::WindowsAndMessaging::*;

static BUTTON_CLICKS: AtomicUsize = AtomicUsize::new(0);
static BUTTON_NOTIFICATION_SOURCE: AtomicUsize = AtomicUsize::new(0);
static CANVAS_CLICKS: AtomicUsize = AtomicUsize::new(0);
static TARGET_ACTIVATIONS: AtomicUsize = AtomicUsize::new(0);
static TARGET_LOCAL_ACTIVATIONS: AtomicUsize = AtomicUsize::new(0);
static TARGET_Z_CHANGES: AtomicUsize = AtomicUsize::new(0);
static BUTTON_HANDLER_ENTERED: AtomicBool = AtomicBool::new(false);
static BUTTON_HANDLER_SAW_INPUT: AtomicBool = AtomicBool::new(false);
static TARGET_FOREGROUND_ALLOWED: AtomicBool = AtomicBool::new(false);
static TARGET_QUEUE_STALLED: AtomicBool = AtomicBool::new(false);
static SELF_BUTTON_CLICKS: AtomicUsize = AtomicUsize::new(0);
static TARGET_DIRECTORY: OnceLock<std::path::PathBuf> = OnceLock::new();
static TARGET_EVENTS: Mutex<Vec<TargetEvent>> = Mutex::new(Vec::new());
static FOREGROUND_EVENTS: Mutex<Vec<ForegroundEvent>> = Mutex::new(Vec::new());
static EVENT_COVER: AtomicUsize = AtomicUsize::new(0);
static EVENT_BARRIER_REQUEST: AtomicUsize = AtomicUsize::new(0);
static EVENT_BARRIER_DONE: AtomicUsize = AtomicUsize::new(0);
const FIXTURE_ACTIVATE: u32 = WM_APP + 7;
const FIXTURE_EVENT_BARRIER: u32 = WM_APP + 8;
const FIXTURE_STALL_QUEUE: u32 = WM_APP + 9;

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
struct TargetEvent {
    phase: String,
    message: String,
    foreground: isize,
    local_active: isize,
    detail: usize,
}

#[derive(Clone, Debug)]
struct ForegroundEvent {
    window: isize,
    time: u32,
}

unsafe extern "system" fn foreground_event(
    _hook: windows_uia::Win32::UI::Accessibility::HWINEVENTHOOK,
    event: u32,
    window: windows_uia::Win32::Foundation::HWND,
    object: i32,
    child: i32,
    _thread: u32,
    time: u32,
) {
    if event == EVENT_SYSTEM_FOREGROUND {
        FOREGROUND_EVENTS.lock().unwrap().push(ForegroundEvent { window: window.0 as isize, time });
    } else if event == EVENT_OBJECT_NAMECHANGE && window.0 as usize == EVENT_COVER.load(Ordering::SeqCst)
        && object == OBJID_CLIENT.0 && child > 0 {
        // This marker shares the same out-of-context hook as foreground
        // events, whose delivery is guaranteed to remain in sequential order.
        EVENT_BARRIER_DONE.store(child as usize, Ordering::SeqCst);
    }
}

fn record_target_event(message: &str, detail: usize) {
    let event = TargetEvent {
        phase: std::fs::read_to_string(target_directory().join("phase")).unwrap_or_else(|_| "setup".into()),
        message: message.into(),
        foreground: unsafe { GetForegroundWindow() }.0 as isize,
        local_active: unsafe { GetActiveWindow() }.0 as isize,
        detail,
    };
    let mut events = TARGET_EVENTS.lock().unwrap();
    events.push(event);
    if events.len() > 32 { events.remove(0); }
}

#[derive(serde::Serialize, serde::Deserialize)]
struct TargetHandles {
    process: u32,
    target: isize,
    button: isize,
    edit: isize,
    canvas: isize,
    list: isize,
}

#[derive(Debug, serde::Serialize, serde::Deserialize)]
struct TargetState {
    button_clicks: usize,
    button_source: usize,
    canvas_clicks: usize,
    activations: usize,
    local_activations: usize,
    z_changes: usize,
    handler_entered: bool,
    handler_saw_input: bool,
    foreground_allowed: bool,
    queue_stalled: bool,
    events: Vec<TargetEvent>,
}

fn target_directory() -> &'static std::path::Path {
    TARGET_DIRECTORY.get().expect("only the controlled target process writes evidence").as_path()
}

fn target_flag(name: &str) -> bool {
    std::fs::remove_file(target_directory().join(name)).is_ok()
}

fn record_target_state() {
    let state = TargetState {
        button_clicks: BUTTON_CLICKS.load(Ordering::SeqCst),
        button_source: BUTTON_NOTIFICATION_SOURCE.load(Ordering::SeqCst),
        canvas_clicks: CANVAS_CLICKS.load(Ordering::SeqCst),
        activations: TARGET_ACTIVATIONS.load(Ordering::SeqCst),
        local_activations: TARGET_LOCAL_ACTIVATIONS.load(Ordering::SeqCst),
        z_changes: TARGET_Z_CHANGES.load(Ordering::SeqCst),
        handler_entered: BUTTON_HANDLER_ENTERED.load(Ordering::SeqCst),
        handler_saw_input: BUTTON_HANDLER_SAW_INPUT.load(Ordering::SeqCst),
        foreground_allowed: TARGET_FOREGROUND_ALLOWED.load(Ordering::SeqCst),
        queue_stalled: TARGET_QUEUE_STALLED.load(Ordering::SeqCst),
        events: TARGET_EVENTS.lock().unwrap().clone(),
    };
    let result = serde_json::to_vec(&state).map_err(std::io::Error::other).and_then(|data| {
        std::fs::write(target_directory().join("state.next"), data)?;
        std::fs::rename(target_directory().join("state.next"), target_directory().join("state.json"))
    });
    if let Err(error) = result {
        eprintln!("Cannot record disposable target state: {error}");
        std::process::exit(71);
    }
}

unsafe extern "system" fn target_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match message {
        WM_COMMAND if wparam.0 & 0xffff == 101 && wparam.0 >> 16 == 0 => {
            record_target_event("button notification entered", 0);
            BUTTON_NOTIFICATION_SOURCE.store(lparam.0 as usize, Ordering::SeqCst);
            if target_flag("try-foreground") {
                let allowed = SetForegroundWindow(hwnd).as_bool();
                TARGET_FOREGROUND_ALLOWED.store(allowed, Ordering::SeqCst);
                record_target_event("button foreground attempt", if allowed { 1 } else { 0 });
            }
            if target_flag("delay-button") {
                BUTTON_HANDLER_ENTERED.store(true, Ordering::SeqCst);
                record_target_state();
                let deadline = Instant::now() + Duration::from_millis(750);
                while !target_directory().join("input-complete").is_file()
                    && Instant::now() < deadline
                {
                    thread::sleep(Duration::from_millis(1));
                }
                BUTTON_HANDLER_SAW_INPUT.store(
                    target_directory().join("input-complete").is_file(),
                    Ordering::SeqCst,
                );
            }
            BUTTON_CLICKS.fetch_add(1, Ordering::SeqCst);
            record_target_state();
            LRESULT(0)
        }
        FIXTURE_STALL_QUEUE => {
            // A bounded, deliberately non-pumping GUI thread proves hook
            // registration alone cannot authorize an unacknowledged mutation.
            TARGET_QUEUE_STALLED.store(true, Ordering::SeqCst);
            record_target_state();
            let deadline = Instant::now() + Duration::from_secs(3);
            while !target_flag("resume-queue") && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(2));
            }
            TARGET_QUEUE_STALLED.store(false, Ordering::SeqCst);
            record_target_state();
            LRESULT(0)
        }
        FIXTURE_ACTIVATE => {
            let allowed = SetForegroundWindow(hwnd).as_bool();
            record_target_event("explicit foreground probe", if allowed { 1 } else { 0 });
            if allowed {
                // Grant activation back only to the test parent that launched
                // this exact owned process. This is fixture setup/verification.
                if let Ok(parent) = std::env::var("OPENCORE_DESKTOP_TARGET_PARENT").unwrap().parse::<u32>() {
                    let _ = AllowSetForegroundWindow(parent);
                }
            }
            LRESULT(if allowed { 1 } else { 0 })
        }
        WM_ACTIVATE if wparam.0 & 0xffff != 0 => {
            // WM_ACTIVATE reports the active window of this input queue. It
            // can occur while the entire application remains in the background.
            // Keep that evidence, and separately count real foreground activation.
            // https://devblogs.microsoft.com/oldnewthing/20131016-00/?p=2913
            TARGET_LOCAL_ACTIVATIONS.fetch_add(1, Ordering::SeqCst);
            if GetForegroundWindow() == hwnd { TARGET_ACTIVATIONS.fetch_add(1, Ordering::SeqCst); }
            record_target_event("WM_ACTIVATE active", wparam.0);
            record_target_state();
            DefWindowProcW(hwnd, message, wparam, lparam)
        }
        WM_ACTIVATE => {
            record_target_event("WM_ACTIVATE inactive", wparam.0);
            record_target_state();
            DefWindowProcW(hwnd, message, wparam, lparam)
        }
        WM_WINDOWPOSCHANGING => {
            TARGET_Z_CHANGES.fetch_add(1, Ordering::SeqCst);
            let flags = if lparam.0 == 0 { 0 } else { (*(lparam.0 as *const WINDOWPOS)).flags.0 };
            record_target_event("WM_WINDOWPOSCHANGING", flags as usize);
            record_target_state();
            DefWindowProcW(hwnd, message, wparam, lparam)
        }
        WM_NULL => {
            record_target_state();
            LRESULT(0)
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
        record_target_state();
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
    own_button: isize,
    foreground_event_start: AtomicUsize,
    process: OwnedTargetProcess,
    directory: FixtureDirectory,
    _cover_window: OwnedCoverWindow,
}

struct OwnedTargetProcess {
    child: std::process::Child,
    job: Option<crate::child_guard::ProcessJob>,
}

impl Drop for OwnedTargetProcess {
    fn drop(&mut self) {
        self.job.take();
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

struct FixtureDirectory(std::path::PathBuf);

impl Drop for FixtureDirectory {
    fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
}

struct OwnedCoverWindow {
    window: isize,
    thread: Option<thread::JoinHandle<()>>,
}

struct OwnedForegroundHook(windows_uia::Win32::UI::Accessibility::HWINEVENTHOOK);

impl Drop for OwnedForegroundHook {
    fn drop(&mut self) {
        unsafe { let _ = windows_uia::Win32::UI::Accessibility::UnhookWinEvent(self.0); }
    }
}

impl Drop for OwnedCoverWindow {
    fn drop(&mut self) {
        unsafe { let _ = PostMessageW(hwnd(self.window), WM_CLOSE, WPARAM(0), LPARAM(0)); }
        if let Some(thread) = self.thread.take() { let _ = thread.join(); }
    }
}

unsafe extern "system" fn cover_proc(hwnd: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match message {
        FIXTURE_EVENT_BARRIER => {
            // Do not synthesize a foreground event. A name-change marker on
            // our disposable cover only drains the observer's event queue.
            windows_uia::Win32::UI::Accessibility::NotifyWinEvent(EVENT_OBJECT_NAMECHANGE,
                windows_uia::Win32::Foundation::HWND(hwnd.0), OBJID_CLIENT.0, wparam.0 as i32);
            LRESULT(0)
        }
        WM_COMMAND if wparam.0 & 0xffff == 201 && wparam.0 >> 16 == 0 => {
            SELF_BUTTON_CLICKS.fetch_add(1, Ordering::SeqCst);
            LRESULT(0)
        }
        WM_CLOSE => {
            let _ = DestroyWindow(hwnd);
            PostQuitMessage(0);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, message, wparam, lparam),
    }
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

#[test]
#[ignore = "Disposable external target entry used only by the GitHub Actions native fixture"]
fn target_process_fixture() {
    assert_eq!(std::env::var("GITHUB_ACTIONS").as_deref(), Ok("true"));
    let directory = std::path::PathBuf::from(std::env::var_os("OPENCORE_DESKTOP_TARGET_DIRECTORY").unwrap());
    assert!(directory.is_dir());
    TARGET_DIRECTORY.set(directory.clone()).unwrap();
    record_target_state();
    let _dpi = crate::desktop_capture::PhysicalDpiScope::new().unwrap();
    unsafe {
        let target = CreateWindowExW(
            WINDOW_EX_STYLE::default(),
            w!("STATIC"),
            w!("OpenCore Background Target Fixture"),
            WS_OVERLAPPEDWINDOW,
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
        let _ = ShowWindow(target, SW_SHOWNOACTIVATE);
        record_target_state();
        let handles = TargetHandles { process: std::process::id(), target: target.0 as isize,
            button: button.0 as isize, edit: edit.0 as isize, canvas: canvas.0 as isize, list: list.0 as isize };
        std::fs::write(directory.join("ready.json"), serde_json::to_vec(&handles).unwrap()).unwrap();
        let mut message = MSG::default();
        while GetMessageW(&mut message, None, 0, 0).0 > 0 {
            let _ = TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
}

impl Fixture {
    fn new() -> Self {
        assert_eq!(std::env::var("GITHUB_ACTIONS").as_deref(), Ok("true"),
            "Native fixture tests run only in GitHub Actions");
        SELF_BUTTON_CLICKS.store(0, Ordering::SeqCst);
        FOREGROUND_EVENTS.lock().unwrap().clear();
        EVENT_BARRIER_REQUEST.store(0, Ordering::SeqCst);
        EVENT_BARRIER_DONE.store(0, Ordering::SeqCst);
        let directory = FixtureDirectory(std::env::temp_dir().join(format!("opencore-desktop-target-{}", uuid::Uuid::new_v4())));
        std::fs::create_dir(&directory.0).unwrap();
        let (sender, receiver) = mpsc::sync_channel(1);
        let thread = thread::spawn(move || unsafe {
            let _dpi = crate::desktop_capture::PhysicalDpiScope::new().unwrap();
            let cover = CreateWindowExW(WINDOW_EX_STYLE::default(), w!("STATIC"),
                w!("OpenCore Foreground Cover Fixture"), WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                40, 40, 520, 440, None, None, HINSTANCE::default(), None).unwrap();
            SetWindowLongPtrW(cover, GWLP_WNDPROC, cover_proc as *const () as isize);
            EVENT_COVER.store(cover.0 as usize, Ordering::SeqCst);
            let hook = windows_uia::Win32::UI::Accessibility::SetWinEventHook(
                EVENT_SYSTEM_FOREGROUND, EVENT_OBJECT_NAMECHANGE, None, Some(foreground_event), 0, 0, WINEVENT_OUTOFCONTEXT);
            assert!(!hook.0.is_null(), "the pumping cover thread must observe every foreground transition");
            let _foreground_hook = OwnedForegroundHook(hook);
            let own_button = CreateWindowExW(WINDOW_EX_STYLE::default(), w!("BUTTON"),
                w!("Rejected OpenCore-owned target fixture"), WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                20, 20, 230, 36, cover, HMENU(201usize as *mut std::ffi::c_void),
                HINSTANCE::default(), None).unwrap();
            let _ = SetForegroundWindow(cover);
            sender.send((cover.0 as isize, own_button.0 as isize)).unwrap();
            let mut message = MSG::default();
            while GetMessageW(&mut message, None, 0, 0).0 > 0 {
                let _ = TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        });
        let (cover, own_button) = receiver.recv_timeout(Duration::from_secs(10)).unwrap();
        let cover_window = OwnedCoverWindow { window: cover, thread: Some(thread) };
        wait_for("parent foreground cover setup", || unsafe { GetForegroundWindow() } == hwnd(cover));

        let mut command = std::process::Command::new(std::env::current_exe().unwrap());
        command.args(["--exact", "windows_control::native_tests::target_process_fixture", "--ignored", "--nocapture"])
            .env("OPENCORE_DESKTOP_TARGET_DIRECTORY", &directory.0)
            .env("OPENCORE_DESKTOP_TARGET_PARENT", unsafe { windows::Win32::System::Threading::GetCurrentProcessId() }.to_string())
            .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null())
            .stderr(std::fs::File::create(directory.0.join("process.log")).unwrap());
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
        let mut process = OwnedTargetProcess { child: command.spawn().unwrap(), job: None };
        process.job = Some(crate::child_guard::ProcessJob::new(&process.child).unwrap());
        let deadline = Instant::now() + Duration::from_secs(10);
        let handles: TargetHandles = loop {
            if let Some(handles) = std::fs::read(directory.0.join("ready.json")).ok()
                .and_then(|bytes| serde_json::from_slice(&bytes).ok()) { break handles; }
            if let Some(status) = process.child.try_wait().unwrap() {
                panic!("Disposable target exited before creating controls: {status}; {}",
                    std::fs::read_to_string(directory.0.join("process.log")).unwrap_or_default());
            }
            assert!(Instant::now() < deadline, "Disposable external target never became ready");
            thread::sleep(Duration::from_millis(10));
        };
        assert_eq!(handles.process, process.child.id());
        assert_ne!(handles.process, unsafe { windows::Win32::System::Threading::GetCurrentProcessId() });
        let mut target_process = 0;
        unsafe { GetWindowThreadProcessId(hwnd(handles.target), Some(&mut target_process)); }
        assert_eq!(target_process, process.child.id(), "fixture target must belong to the exact owned child");
        unsafe {
            SetWindowPos(hwnd(cover), HWND_TOP, 40, 40, 520, 440, SWP_NOACTIVATE | SWP_SHOWWINDOW).unwrap();
            AllowSetForegroundWindow(process.child.id()).unwrap();
        }
        Self { target: handles.target, cover, button: handles.button, edit: handles.edit,
            canvas: handles.canvas, list: handles.list, own_button, process,
            foreground_event_start: AtomicUsize::new(0),
            _cover_window: cover_window, directory }
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

    fn state(&self) -> TargetState {
        assert!(unsafe { IsWindow(hwnd(self.target)) }.as_bool(), "Disposable target exited; {}",
            std::fs::read_to_string(self.directory.0.join("process.log")).unwrap_or_default());
        serde_json::from_slice(&std::fs::read(self.directory.0.join("state.json")).unwrap())
            .expect("external target must publish a complete atomic evidence snapshot")
    }

    fn flag(&self, name: &str) {
        std::fs::write(self.directory.0.join(name), []).unwrap();
    }

    fn phase(&self, name: &str) {
        std::fs::write(self.directory.0.join("phase"), name).unwrap();
    }

    fn flush_desktop(&self) {
        // Cross-input-queue activation is asynchronous. Drain both windows
        // before sampling counters; polling GetForegroundWindow alone does
        // not prove that activation/deactivation notifications have completed.
        // https://devblogs.microsoft.com/oldnewthing/20161118-00/?p=94745
        for window in [self.target, self.cover] {
            let sent = unsafe { SendMessageTimeoutW(hwnd(window), WM_NULL, WPARAM(0), LPARAM(0),
                SMTO_ABORTIFHUNG | SMTO_BLOCK | SMTO_ERRORONEXIT, 1000, None) };
            assert_ne!(sent.0, 0, "fixture window must answer its activation queue barrier: {}", window_description(hwnd(window)));
        }
        let sequence = EVENT_BARRIER_REQUEST.fetch_add(1, Ordering::SeqCst) + 1;
        assert!(sequence <= i32::MAX as usize);
        unsafe { PostMessageW(hwnd(self.cover), FIXTURE_EVENT_BARRIER, WPARAM(sequence), LPARAM(0)).unwrap(); }
        wait_for("sequential foreground-event observer barrier", || EVENT_BARRIER_DONE.load(Ordering::SeqCst) >= sequence);
    }

    fn attempt_activation(&self) -> bool {
        // The activation API runs in the external target process, which is
        // allowed to activate outside a protected dispatch. A parent-process
        // call would not test LockSetForegroundWindow's external caller policy.
        let mut outcome = 0usize;
        let sent = unsafe { SendMessageTimeoutW(hwnd(self.target), FIXTURE_ACTIVATE, WPARAM(0), LPARAM(0),
            SMTO_ABORTIFHUNG | SMTO_BLOCK | SMTO_ERRORONEXIT, 1000, Some(&mut outcome)) };
        assert_ne!(sent.0, 0, "the controlled external target must answer its activation probe");
        self.flush_desktop();
        if outcome != 0 {
            let events = FOREGROUND_EVENTS.lock().unwrap().clone();
            let start = self.foreground_event_start.load(Ordering::SeqCst);
            assert!(events[start..].iter().any(|event| event.window == self.target),
                "a successful deliberate activation must be detected by the real foreground-event observer: {events:?}");
        }
        outcome != 0
    }

    fn restore_cover(&self) {
        self.phase("deliberate cover restoration");
        assert!(unsafe { SetForegroundWindow(hwnd(self.cover)) }.as_bool());
        wait_for("foreground cover restore", || unsafe { GetForegroundWindow() } == hwnd(self.cover));
        // A successful target probe grants activation to the parent, replacing
        // the previous target grant. Refresh eligibility for the exact owned
        // target before the next protected or released probe. This does not
        // unlock foreground protection or bypass the activation veto.
        // https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-allowsetforegroundwindow
        let mut target_process = 0;
        unsafe { GetWindowThreadProcessId(hwnd(self.target), Some(&mut target_process)); }
        assert_eq!(target_process, self.process.child.id(), "only the exact owned target may receive fixture activation eligibility");
        unsafe { AllowSetForegroundWindow(target_process).expect("the foreground cover must refresh the owned target's activation eligibility"); }
        self.flush_desktop();
        // Only deliberate test activation/restoration creates a new baseline.
        // Never rebase after a background action or discovery phase.
        self.foreground_event_start.store(FOREGROUND_EVENTS.lock().unwrap().len(), Ordering::SeqCst);
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
        self.flush_desktop();
        let current = unsafe { GetForegroundWindow() };
        let state = self.state();
        let events = FOREGROUND_EVENTS.lock().unwrap().clone();
        let observed: Vec<_> = events[self.foreground_event_start.load(Ordering::SeqCst)..].iter()
            .map(|event| (event.window, event.time)).collect();
        assert!(observed.iter().all(|(window, _)| *window == foreground.0 as isize),
            "{operation}: every foreground transition must remain on the cover, including brief transitions; events={observed:?}; target={state:?}");
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
            state.activations,
            activations,
            "{operation}: target must not be briefly activated in the foreground; {}; evidence={state:?}",
            window_description(hwnd(self.target)),
        );
        assert_eq!(
            state.z_changes,
            z_changes,
            "{operation}: target must not be temporarily exposed or reordered; {}; evidence={state:?}",
            window_description(hwnd(self.target)),
        );
    }

    fn edit_text(&self) -> String {
        let mut text = [0u16; 512];
        let mut len = 0usize;
        let sent = unsafe { SendMessageTimeoutW(hwnd(self.edit), WM_GETTEXT, WPARAM(text.len()), LPARAM(text.as_mut_ptr() as isize),
            SMTO_ABORTIFHUNG | SMTO_BLOCK | SMTO_ERRORONEXIT, 1000, Some(&mut len)) };
        assert_ne!(sent.0, 0, "cross-process edit verification must complete");
        String::from_utf16_lossy(&text[..len])
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

    fixture.phase("headless startup only");
    let mut startup = crate::desktop_helper::helper_command().unwrap();
    startup.env("OPENCORE_DESKTOP_HELPER_TEST_MODE", "startup_only");
    let started = runtime.block_on(crate::desktop_helper::execute("inspect".into(), button.clone(), startup))
        .expect("headless fixture startup must complete without a desktop action");
    assert_eq!(started["startupOnly"], true);
    fixture.assert_desktop_unchanged("headless startup only", foreground, cursor, activations, z_changes);
    fixture.assert_covered(button);

    fixture.phase("cold headless accessibility discovery");
    let discovered = runtime.block_on(crate::windows_control::command("inspect".into(), button.clone()))
        .expect("headless fixture must discover its covered accessible controls");
    assert!(discovered["elements"].as_array().unwrap().iter()
        .any(|element| element["name"] == "Background fixture action"));
    fixture.assert_desktop_unchanged("cold headless accessibility discovery", foreground, cursor, activations, z_changes);
    fixture.assert_covered(button);

    // Exercise the actual parent/helper handshake. The external target's real
    // notification handler attempts to activate its own window while the
    // foreground OpenCore process protects the dispatch.
    fixture.phase("headless button discovery, dispatch and teardown");
    let local_activations = fixture.state().local_activations;
    let clicks = fixture.state().button_clicks;
    fixture.flag("try-foreground");
    let delegated = runtime.block_on(crate::windows_control::command("interact".into(), button.clone()))
        .expect("headless helper must invoke the covered fixture control");
    assert_eq!(delegated["activated"], true);
    assert_eq!(delegated["inputMode"], "window-message");
    assert!(!fixture.state().foreground_allowed,
        "foreground protection must deny the external target notification handler's activation attempt");
    assert_eq!(fixture.state().button_clicks, clicks + 1);
    fixture.assert_desktop_unchanged("headless button dispatch", foreground, cursor, activations, z_changes);
    fixture.assert_covered(button);
    let evidence = fixture.state();
    if evidence.local_activations != local_activations {
        println!("headless button local WM_ACTIVATE notifications {}->{}; foreground-event history and actual foreground activation remained unchanged; evidence={evidence:?}",
            local_activations, evidence.local_activations);
    }

    fixture.phase("headless text discovery, dispatch and teardown");
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
        fixture.phase(if cancel { "canceled headless dispatch" } else { "expired headless dispatch" });
        let activations = fixture.state().activations;
        let z_changes = fixture.state().z_changes;
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
            // Drop/return itself is the exit barrier: eventual termination
            // after foreground protection was released is insufficient.
            let mut code = 259u32; // STILL_ACTIVE returned by GetExitCodeProcess.
            unsafe { GetExitCodeProcess(process.0, &mut code).unwrap(); }
            assert_ne!(code, 259, "the owned blocked desktop helper survived its protection teardown");
        });
        assert!(started.elapsed() < Duration::from_secs(10), "blocked manual dispatch retained its lock too long");
        fixture.assert_desktop_unchanged("blocked helper teardown", foreground, cursor, activations, z_changes);
        fixture.phase("deliberate released helper activation probe");
        assert!(fixture.attempt_activation(),
            "helper teardown must release its foreground lock; this activation is fixture verification only");
        wait_for("released helper foreground verification", || unsafe { GetForegroundWindow() } == hwnd(fixture.target));
        fixture.restore_cover();
    }
}

fn verify_authorized_background_controls(fixture: &Fixture, button: &Value) {
    use crate::computer_access::{self, Access, Policy};
    let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
    let store = std::sync::Arc::new(crate::store::EventStore::open(&fixture.directory.0.join("permissions.sqlite3")).unwrap());
    let target = computer_access::window_identity(fixture.target as i64).unwrap();
    assert_eq!(target.pid, fixture.process.child.id(), "permissions must resolve the exact owned external executable");
    let original = fixture.state();
    let foreground = unsafe { GetForegroundWindow() };
    let cursor = cursor_position().unwrap();
    fixture.phase("authorized disabled and unknown app admission");
    runtime.block_on(async {
        let listed = super::command_authorized("list".into(), json!({}), store.clone()).await.unwrap();
        assert_eq!(listed["computerUseEnabled"], false);
        assert!(listed["windows"].as_array().unwrap().is_empty());
        assert!(super::command_authorized("interact".into(), button.clone(), store.clone()).await.unwrap_err().contains("disabled"));
        computer_access::save(&store, Policy { enabled: true, ..Policy::default() }).unwrap();
        assert!(super::command_authorized("inspect".into(), button.clone(), store.clone()).await.unwrap_err().contains("requires permission"));
        computer_access::grant(&store, &target).unwrap();
        let listed = super::command_authorized("list".into(), json!({}), store.clone()).await.unwrap();
        let windows = listed["windows"].as_array().unwrap();
        assert!(!windows.iter().any(|row| row["windowId"] == 0), "whole-desktop capture cannot enforce executable permissions");
        assert!(windows.iter().any(|row| row["windowId"] == fixture.target as i64 && row["permission"] == "allowed"));
        assert_eq!(listed["backgroundCapabilities"]["canvasInput"], false);
        let inspected = super::command_authorized("inspect".into(), button.clone(), store.clone()).await.unwrap();
        assert!(inspected["elements"].as_array().unwrap().iter().any(|row| row["name"] == "Background fixture action"));
    });
    fixture.assert_desktop_unchanged("authorized read admission", foreground, cursor, original.activations, original.z_changes);
    assert_eq!(fixture.state().button_clicks, original.button_clicks, "denied admission and reading must send no input");
    fixture.assert_covered(button);

    fixture.phase("authorized covered button mutation");
    fixture.flag("try-foreground");
    let clicked = runtime.block_on(super::command_authorized("interact".into(), button.clone(), store.clone())).unwrap();
    assert_eq!(clicked["activated"], true);
    assert_eq!(clicked["inputMode"], "window-message");
    assert_eq!(clicked["backgroundVerified"], true);
    assert_eq!(fixture.state().button_clicks, original.button_clicks + 1);
    assert!(!fixture.state().foreground_allowed, "the permission-checked helper must retain the foreground activation veto");
    fixture.assert_desktop_unchanged("authorized covered button mutation", foreground, cursor, original.activations, original.z_changes);
    fixture.assert_covered(button);

    fixture.phase("authorized covered text mutation");
    let mut edit = fixture.args(fixture.edit);
    edit["text"] = json!("permission-checked background text");
    let updated = runtime.block_on(super::command_authorized("commit_text".into(), edit.clone(), store.clone())).unwrap();
    assert_eq!(updated["updated"], true);
    assert_eq!(updated["inputMode"], "window-message");
    assert_eq!(fixture.edit_text(), "permission-checked background text");
    fixture.assert_desktop_unchanged("authorized covered text mutation", foreground, cursor, original.activations, original.z_changes);
    fixture.assert_covered(&edit);

    fixture.phase("authorized unsupported canvas and revoked permission");
    let canvas = fixture.args(fixture.canvas);
    assert!(runtime.block_on(super::command_authorized("interact".into(), canvas, store.clone())).is_err());
    assert_eq!(fixture.state().canvas_clicks, original.canvas_clicks, "authorization cannot invent background canvas support");
    // Pause a real owned helper after admission but before DispatchReady.
    // Invoke the production authorization protocol directly so this assertion
    // proves the final recheck, independently of the outer cancellation watcher.
    let discovery = fixture.directory.0.join("authorization-discovered");
    let release = fixture.directory.0.join("authorization-release");
    let dispatched = fixture.directory.0.join("authorization-dispatched");
    let mut paused = button.clone();
    paused["discoveryReceipt"] = json!(discovery.to_string_lossy());
    paused["discoveryRelease"] = json!(release.to_string_lossy());
    paused["dispatchReceipt"] = json!(dispatched.to_string_lossy());
    let mut helper = crate::desktop_helper::helper_command().unwrap();
    helper.env("OPENCORE_DESKTOP_HELPER_TEST_MODE", "wait_before_dispatch");
    runtime.block_on(async {
        let mut operation = Box::pin(crate::desktop_helper::execute_authorized_for_test(
            "interact".into(), paused, helper, store.clone(), target.clone()));
        let deadline = Instant::now() + Duration::from_secs(3);
        while !discovery.is_file() {
            tokio::select! {
                result = &mut operation => panic!("permission fixture ended before discovery: {result:?}"),
                _ = tokio::time::sleep(Duration::from_millis(10)) => {}
            }
            assert!(Instant::now() < deadline, "permission helper did not reach discovery");
        }
        let mut denied = computer_access::load(&store).unwrap();
        denied.apps.iter_mut().find(|app| computer_access::identity(&app.path) == computer_access::identity(&target.path)).unwrap().access = Access::Deny;
        computer_access::save(&store, denied).unwrap();
        std::fs::write(&release, []).unwrap();
        let error = operation.await.unwrap_err();
        assert!(error.contains("denied"), "DispatchReady must recheck revocation: {error}");
    });
    assert!(!dispatched.exists(), "a permission revoked after admission must not reach helper dispatch");
    let blocked = runtime.block_on(super::command_authorized("interact".into(), button.clone(), store.clone())).unwrap_err();
    assert!(blocked.contains("denied"), "{blocked}");
    let listed = runtime.block_on(super::command_authorized("list".into(), json!({}), store.clone())).unwrap();
    assert!(!listed["windows"].as_array().unwrap().iter().any(|row| row["windowId"] == fixture.target as i64));
    assert_eq!(fixture.state().button_clicks, original.button_clicks + 1, "revoked permissions must block new mutation");
    fixture.assert_desktop_unchanged("authorized canvas refusal and revocation", foreground, cursor, original.activations, original.z_changes);
    fixture.assert_covered(button);
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
    // Windows lets the foreground process activate its own windows while
    // locked. Reject that unsupported ownership case before any dispatch.
    let mut own = fixture.args(fixture.own_button);
    own["windowId"] = json!(fixture.cover);
    own["elementId"] = json!(0);
    own["text"] = json!("must not reach an OpenCore-owned control");
    own["direction"] = json!("down");
    let called = AtomicBool::new(false);
    let rejected = platform::manual_dispatch(&own, || {
        called.store(true, Ordering::SeqCst);
        Ok(())
    }).unwrap_err();
    assert!(rejected.contains("OpenCore-owned"), "{rejected}");
    assert!(!called.load(Ordering::SeqCst), "unsupported same-process dispatch must not run");
    for action in ["interact", "invoke", "set_value", "set_at", "commit_text", "commit_enter", "scroll_at"] {
        let error = platform::run(action, &own).unwrap_err();
        assert!(error.contains("OpenCore-owned"), "{action}: {error}");
    }
    let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
    let error = runtime.block_on(crate::windows_control::command("interact".into(), own.clone())).unwrap_err();
    assert!(error.contains("OpenCore-owned"), "the parent must reject its own window before helper execution: {error}");
    own["windowId"] = json!(fixture.own_button);
    assert!(super::validate_manual_target("interact", &own).unwrap_err().contains("OpenCore-owned"));
    assert_eq!(SELF_BUTTON_CLICKS.load(Ordering::SeqCst), 0, "no owned control may receive input");

    // The activation API must run in the external target, not in the current
    // foreground process. It is denied during dispatch, then succeeds after
    // the same production guard releases, proving this is a genuine attempt.
    let manual = fixture.args(fixture.button);
    fixture.flush_desktop();
    let cover = unsafe { GetForegroundWindow() };
    let probe_cursor = cursor_position().unwrap();
    let probe_state = fixture.state();
    fixture.assert_covered(&manual);
    fixture.phase("protected direct external activation probe");
    let activated = platform::manual_dispatch(&manual, || {
        Ok(fixture.attempt_activation())
    }).expect("manual dispatch must protect its foreground process");
    assert!(!activated, "the dispatch must deny an external target's foreground activation");
    fixture.assert_desktop_unchanged("protected direct activation probe", cover, probe_cursor,
        probe_state.activations, probe_state.z_changes);
    fixture.assert_covered(&manual);
    let exact_error: Result<(), String> = platform::manual_dispatch(&manual, || Err("fixture dispatch error".into()));
    assert_eq!(exact_error, Err("fixture dispatch error".into()));
    fixture.phase("deliberate failed-dispatch activation probe");
    assert!(fixture.attempt_activation(),
        "a failed dispatch must release its lock; this activation is fixture verification only");
    wait_for("released dispatch foreground verification", || unsafe { GetForegroundWindow() } == hwnd(fixture.target));
    fixture.restore_cover();

    // No input is authorized until the private marker proves the DLL runs in
    // the exact external GUI thread. Stall that queue, then inspect both
    // the closure and real control to prove failed acknowledgement sends none.
    fixture.phase("unacknowledged activation protection");
    fixture.flush_desktop();
    let preflight_state = fixture.state();
    let preflight_cursor = cursor_position().unwrap();
    unsafe { PostMessageW(hwnd(fixture.target), FIXTURE_STALL_QUEUE, WPARAM(0), LPARAM(0)).unwrap(); }
    wait_for("target queue stall", || fixture.state().queue_stalled);
    let sent = AtomicBool::new(false);
    let started = Instant::now();
    let error = platform::manual_dispatch(&manual, || {
        sent.store(true, Ordering::SeqCst);
        Ok(())
    }).unwrap_err();
    fixture.flag("resume-queue");
    assert!(error.contains("did not confirm") && error.contains("no input was sent"), "{error}");
    assert!(!sent.load(Ordering::SeqCst), "unacknowledged protection must never run the mutation");
    assert!(started.elapsed() < Duration::from_secs(2), "hook acknowledgement must be bounded");
    wait_for("target queue resumes", || !fixture.state().queue_stalled);
    fixture.assert_desktop_unchanged("failed activation acknowledgement", cover, preflight_cursor,
        preflight_state.activations, preflight_state.z_changes);
    fixture.assert_covered(&manual);
    assert_eq!(fixture.state().button_clicks, preflight_state.button_clicks);
    assert!(unsafe { GetPropW(hwnd(fixture.target), w!("OpenCore.ManualActivationLease.v1")) }.0.is_null());
    assert!(unsafe { GetPropW(hwnd(fixture.target), w!("OpenCore.ManualActivationAck.v1")) }.0.is_null());
    fixture.phase("deliberate failed-preflight activation probe");
    assert!(fixture.attempt_activation(),
        "failed preflight must release its hook and foreground lock; foreground={}; target={:?}",
        window_description(unsafe { GetForegroundWindow() }), fixture.state());
    fixture.restore_cover();

    // Retain the actual hook and stale properties while its monotonic lease
    // expires. No foreground lock is held here, so successful real activation
    // proves expiry itself releases the veto even if its parent stops cleanup.
    use windows::Win32::System::SystemInformation::GetTickCount64;
    fixture.phase("activation lease expires while hook remains installed");
    fixture.flush_desktop();
    let expiry_state = fixture.state();
    let expiry_cursor = cursor_position().unwrap();
    let stale = crate::desktop_focus_guard::ActivationVeto::acquire(fixture.target).unwrap();
    let stale_token = unsafe { GetPropW(hwnd(fixture.target), w!("OpenCore.ManualActivationLease.v1")) }.0 as usize as u64;
    assert_ne!(stale_token, 0);
    wait_for("activation lease expiry", || unsafe { GetTickCount64() } > (stale_token >> 16));
    assert_eq!(unsafe { GetPropW(hwnd(fixture.target), w!("OpenCore.ManualActivationLease.v1")) }.0 as usize as u64, stale_token);
    fixture.assert_desktop_unchanged("idle activation lease expiry", cover, expiry_cursor,
        expiry_state.activations, expiry_state.z_changes);
    fixture.assert_covered(&manual);
    assert!(fixture.attempt_activation(), "an expired lease must permit ordinary target activation while its hook remains installed");
    fixture.restore_cover();

    // A fresh action recovers expired metadata. Dropping the old guard must
    // neither erase the new lease/ACK nor permit its protected activation.
    fixture.phase("recover an expired activation lease");
    fixture.flush_desktop();
    let recovered_state = fixture.state();
    let recovered_cursor = cursor_position().unwrap();
    let fresh = super::ManualForegroundGuard::acquire(&manual).unwrap().unwrap();
    // Unhook can return while the acknowledgement callback finishes. Drain
    // the target before comparing metadata across the older guard's teardown.
    fixture.flush_desktop();
    let fresh_token = unsafe { GetPropW(hwnd(fixture.target), w!("OpenCore.ManualActivationLease.v1")) };
    let fresh_ack = unsafe { GetPropW(hwnd(fixture.target), w!("OpenCore.ManualActivationAck.v1")) };
    assert_ne!(fresh_token.0 as usize as u64, stale_token);
    assert!(!fresh_ack.0.is_null());
    drop(stale);
    assert_eq!(unsafe { GetPropW(hwnd(fixture.target), w!("OpenCore.ManualActivationLease.v1")) }, fresh_token);
    assert_eq!(unsafe { GetPropW(hwnd(fixture.target), w!("OpenCore.ManualActivationAck.v1")) }, fresh_ack);
    assert!(!fixture.attempt_activation(), "fresh protection must survive an expired guard's teardown");
    drop(fresh);
    fixture.assert_desktop_unchanged("recovered activation protection", cover, recovered_cursor,
        recovered_state.activations, recovered_state.z_changes);
    fixture.assert_covered(&manual);
    assert!(unsafe { GetPropW(hwnd(fixture.target), w!("OpenCore.ManualActivationLease.v1")) }.0.is_null());
    assert!(unsafe { GetPropW(hwnd(fixture.target), w!("OpenCore.ManualActivationAck.v1")) }.0.is_null());
    fixture.phase("deliberate recovered-guard release probe");
    assert!(fixture.attempt_activation(), "successful protection teardown must permit normal activation");
    fixture.restore_cover();
    fixture.phase("direct background controls and accessibility discovery");

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
    let activations = fixture.state().activations;
    let z_changes = fixture.state().z_changes;
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
        fixture.state().button_clicks == 1
    });
    assert_eq!(
        fixture.state().button_source,
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
        fixture.state().button_clicks == 2
    });
    assert_eq!(
        fixture.state().button_source,
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
        fixture.state().canvas_clicks,
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
        fixture.state().activations,
        activations,
        "target must not be briefly activated"
    );
    assert_eq!(
        fixture.state().z_changes,
        z_changes,
        "target must not be temporarily exposed or reordered"
    );
    println!("background native fixture: button invoked, text updated, list scrolled {}->{}, unsupported canvas/Enter rejected; foreground={:#x}, cursor=({}, {}), no target activation or z changes", before_scroll, after_scroll, foreground.0 as usize, cursor.0, cursor.1);

    verify_headless_helper(&fixture, &button, foreground, cursor, activations, z_changes);
    verify_authorized_background_controls(&fixture, &button);

    // Change desktop input from another thread only after the real notification
    // handler begins. Its completed button outcome must survive the warning,
    // and the background action must not restore the independent cursor input.
    let cursor_before_delay = cursor_position().unwrap();
    let clicks_before_delay = fixture.state().button_clicks;
    let activations = fixture.state().activations;
    let z_changes = fixture.state().z_changes;
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
    fixture.flag("delay-button");
    fixture.phase("completed background action with independent cursor input");
    let evidence_directory = fixture.directory.0.clone();
    let independent_input = thread::spawn(move || {
        wait_for("delayed button handler entry", || {
            std::fs::read(evidence_directory.join("state.json")).ok()
                .and_then(|bytes| serde_json::from_slice::<TargetState>(&bytes).ok())
                .is_some_and(|state| state.handler_entered)
        });
        unsafe { SetCursorPos(independent_cursor.0, independent_cursor.1) }
            .expect("independent fixture cursor input");
        assert_eq!(cursor_position(), Some(independent_cursor));
        std::fs::write(evidence_directory.join("input-complete"), []).unwrap();
    });
    let completed = platform::run("interact", &button);
    independent_input
        .join()
        .expect("independent fixture input thread");
    let completed = completed
        .expect("completed button activation must not become an error after independent input");
    assert!(
        fixture.state().handler_saw_input,
        "independent input must occur before the successful handler returns"
    );
    assert_eq!(
        fixture.state().button_clicks,
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
