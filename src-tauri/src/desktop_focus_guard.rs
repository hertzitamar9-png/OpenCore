//! Activation-only veto installed on one external application's GUI thread.
//! The DLL is a packaged first-party resource whose bytes are bound to this
//! executable. An unconfirmed, incompatible or protected target receives no input.
#![cfg(windows)]

use std::io::Read;
use std::os::windows::{ffi::OsStrExt, fs::OpenOptionsExt};
use std::sync::{atomic::{AtomicU64, Ordering}, Arc, OnceLock};
use std::time::{Duration, Instant};
use windows::core::{s, w, PCWSTR};
use windows::Win32::Foundation::{CloseHandle, FreeLibrary, HANDLE, HINSTANCE, HMODULE, HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::{GetProcAddress, LoadLibraryExW, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR, LOAD_LIBRARY_SEARCH_SYSTEM32};
use windows::Win32::System::SystemInformation::{GetTickCount64, IMAGE_FILE_MACHINE, IMAGE_FILE_MACHINE_AMD64, IMAGE_FILE_MACHINE_UNKNOWN};
use windows::Win32::System::Threading::{GetCurrentProcess, GetCurrentProcessId, IsWow64Process2, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION};
use windows::Win32::UI::WindowsAndMessaging::{GetAncestor, GetPropW, GetWindowThreadProcessId, IsWindow, PostMessageW,
    RegisterWindowMessageW, RemovePropW, SetPropW, SetWindowsHookExW, UnhookWindowsHookEx, GA_ROOT, HHOOK, WH_CBT, WH_GETMESSAGE};

const LEASE_MS: u64 = 2500;
const PREFLIGHT_TIMEOUT: Duration = Duration::from_millis(500);
const MAX_DLL_BYTES: usize = 1024 * 1024;
const EXPECTED_DLL: &[u8] = include_bytes!("../resources/desktop/opencore-focus-guard.dll");
type CbtProc = unsafe extern "system" fn(i32, WPARAM, LPARAM) -> LRESULT;

fn lease_token(deadline: u64, sequence: u64) -> Result<u64, String> {
    deadline.checked_mul(1 << 16).map(|expiry| expiry | (sequence & 0xffff))
        .ok_or_else(|| "Cannot represent the activation protection deadline; no input was sent".into())
}

struct HookLibrary {
    module: isize,
    callback: CbtProc,
    acknowledgement: CbtProc,
    // Prevent resource replacement between verification and mapping. The
    // module is pinned for this parent process's lifetime; no remote frees.
    _file: std::fs::File,
}

impl Drop for HookLibrary {
    fn drop(&mut self) { unsafe { let _ = FreeLibrary(HMODULE(self.module as *mut core::ffi::c_void)); } }
}

fn resource_path() -> Result<std::path::PathBuf, String> {
    #[cfg(test)]
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("resources/desktop/opencore-focus-guard.dll");
    #[cfg(not(test))]
    let path = std::env::current_exe().map_err(|error| error.to_string())?.parent()
        .ok_or("Cannot locate the installed activation guard")?.join("desktop/opencore-focus-guard.dll");
    path.canonicalize().map_err(|error| format!("The installed desktop activation guard is unavailable; no input was sent: {error}"))
}

fn load_library() -> Result<Arc<HookLibrary>, String> {
    static LIBRARY: OnceLock<Result<Arc<HookLibrary>, String>> = OnceLock::new();
    LIBRARY.get_or_init(|| {
        let path = resource_path()?;
        let mut file = std::fs::OpenOptions::new().read(true).share_mode(1).open(&path)
            .map_err(|error| format!("Cannot verify the installed activation guard; no input was sent: {error}"))?;
        let mut bytes = Vec::new();
        (&mut file).take(MAX_DLL_BYTES as u64 + 1).read_to_end(&mut bytes).map_err(|error| error.to_string())?;
        if bytes.len() > MAX_DLL_BYTES || bytes.as_slice() != EXPECTED_DLL {
            return Err("The installed activation guard does not match this OpenCore executable; no input was sent. Reinstall the matching OpenCore package.".into());
        }
        let mut wide: Vec<u16> = path.as_os_str().encode_wide().collect();
        wide.push(0);
        let module = unsafe { LoadLibraryExW(PCWSTR(wide.as_ptr()), None,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32) }
            .map_err(|error| format!("Windows could not load background activation protection; no input was sent: {error}"))?;
        let parsed = (|| {
            let abi = unsafe { GetProcAddress(module, s!("OpenCoreDesktopHookAbi")) }.ok_or("The activation guard ABI export is missing")?;
            let abi: unsafe extern "system" fn() -> u32 = unsafe { std::mem::transmute(abi) };
            if unsafe { abi() } != 2 { return Err("The activation guard ABI is unsupported".to_string()); }
            let callback = unsafe { GetProcAddress(module, s!("OpenCoreDesktopCbtProc")) }.ok_or("The activation guard callback is missing")?;
            let callback: CbtProc = unsafe { std::mem::transmute(callback) };
            let acknowledgement = unsafe { GetProcAddress(module, s!("OpenCoreDesktopAckProc")) }.ok_or("The activation guard acknowledgement callback is missing")?;
            let acknowledgement: CbtProc = unsafe { std::mem::transmute(acknowledgement) };
            Ok(Arc::new(HookLibrary { module: module.0 as isize, callback, acknowledgement, _file: file }))
        })();
        if parsed.is_err() { unsafe { let _ = FreeLibrary(module); } }
        parsed
    }).clone()
}

struct ProcessHandle(HANDLE);
impl Drop for ProcessHandle {
    fn drop(&mut self) { unsafe { let _ = CloseHandle(self.0); } }
}

fn architecture(process: HANDLE) -> Result<IMAGE_FILE_MACHINE, String> {
    let (mut emulated, mut native) = (IMAGE_FILE_MACHINE_UNKNOWN, IMAGE_FILE_MACHINE_UNKNOWN);
    unsafe { IsWow64Process2(process, &mut emulated, Some(&mut native)) }
        .map_err(|error| format!("Cannot verify the selected application's architecture; no input was sent: {error}"))?;
    Ok(if emulated == IMAGE_FILE_MACHINE_UNKNOWN { native } else { emulated })
}

/// Handles are stored as integers so the parent can await bounded helper I/O
/// without retaining a thread-bound COM object or borrowed native callback.
pub(crate) struct ActivationVeto {
    hook: isize,
    ack_hook: isize,
    window: isize,
    process: u32,
    thread: u32,
    token: u64,
    lease_owned: bool,
    _library: Arc<HookLibrary>,
}

impl ActivationVeto {
    pub(crate) fn acquire(selected: isize) -> Result<Self, String> {
        let selected = HWND(selected as *mut core::ffi::c_void);
        if !unsafe { IsWindow(selected) }.as_bool() {
            return Err("The selected window is unavailable; no input was sent".into());
        }
        let window = unsafe { GetAncestor(selected, GA_ROOT) };
        let mut process = 0;
        let thread = unsafe { GetWindowThreadProcessId(window, Some(&mut process)) };
        if thread == 0 || process == 0 || process == unsafe { GetCurrentProcessId() } {
            return Err("Choose an external application window for protected background input; no input was sent".into());
        }
        let handle = ProcessHandle(unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, process) }
            .map_err(|error| format!("This protected application's ownership cannot be verified for background input; no input was sent: {error}"))?);
        if architecture(handle.0)? != IMAGE_FILE_MACHINE_AMD64
            || architecture(unsafe { GetCurrentProcess() })? != IMAGE_FILE_MACHINE_AMD64 {
            return Err("Protected manual background input currently supports matching x64 applications. This application's architecture is unsupported; no input was sent.".into());
        }
        let library = load_library()?;
        static SEQUENCE: AtomicU64 = AtomicU64::new(1);
        let sequence = SEQUENCE.fetch_add(1, Ordering::SeqCst);
        let mut veto = Self { hook: 0, ack_hook: 0, window: window.0 as isize, process, thread,
            token: lease_token(unsafe { GetTickCount64() } + LEASE_MS, sequence)?,
            lease_owned: false, _library: library };
        if !veto.owns_window() {
            return Err("The selected application's GUI thread changed before protection; no input was sent".into());
        }
        let prior = unsafe { GetPropW(window, w!("OpenCore.ManualActivationLease.v1")) }.0 as usize as u64;
        if prior != 0 {
            if prior >> 16 > unsafe { GetTickCount64() } {
                return Err("This window already has an activation protection lease; no input was sent".into());
            }
            // A dead parent's metadata can outlive its automatically removed
            // hook. Expiry permits ordinary activation and a later fresh lease.
            unsafe { let _ = RemovePropW(window, w!("OpenCore.ManualActivationLease.v1")); }
            unsafe { let _ = RemovePropW(window, w!("OpenCore.ManualActivationAck.v1")); }
        }
        if !veto.owns_window() {
            return Err("The selected application's window changed before protection; no input was sent".into());
        }
        unsafe { let _ = RemovePropW(window, w!("OpenCore.ManualActivationAck.v1")); }
        unsafe { SetPropW(window, w!("OpenCore.ManualActivationLease.v1"), HANDLE(veto.token as usize as *mut core::ffi::c_void)) }
            .map_err(|error| format!("Windows denied activation protection for this application; no input was sent: {error}"))?;
        veto.lease_owned = true;
        if !veto.owns_window() {
            return Err("The selected application's window changed while arming protection; no input was sent".into());
        }
        let hook = unsafe { SetWindowsHookExW(WH_CBT, Some(veto._library.callback),
            HINSTANCE(veto._library.module as *mut core::ffi::c_void), thread) }
            .map_err(|error| format!("This application does not allow protected background activation; no input was sent: {error}"))?;
        veto.hook = hook.0 as isize;
        let ack_hook = unsafe { SetWindowsHookExW(WH_GETMESSAGE, Some(veto._library.acknowledgement),
            HINSTANCE(veto._library.module as *mut core::ffi::c_void), thread) }
            .map_err(|error| format!("This application does not allow background protection preflight; no input was sent: {error}"))?;
        veto.ack_hook = ack_hook.0 as isize;
        let marker = unsafe { RegisterWindowMessageW(w!("OpenCore.ManualActivationPreflight.v1")) };
        if marker == 0 || !veto.owns_window() {
            return Err("Cannot arm activation protection preflight for the selected window; no input was sent".into());
        }
        // GetMsgProc is invoked when GetMessage/PeekMessage retrieves this
        // marker. Its callback reads only this message's scalar fields, proves
        // the exact root/thread/token, then consumes it as WM_NULL.
        unsafe { PostMessageW(window, marker, WPARAM(veto.token as usize), LPARAM(0)) }
            .map_err(|error| format!("Cannot confirm activation protection in the selected application's GUI thread; no input was sent: {error}"))?;
        let limit = Instant::now() + PREFLIGHT_TIMEOUT;
        loop {
            if Instant::now() >= limit || !veto.owns_window() {
                return Err("This application did not confirm protected background activation. Background input is unsupported here; no input was sent.".into());
            }
            let ack = unsafe { GetPropW(window, w!("OpenCore.ManualActivationAck.v1")) }.0 as usize as u64;
            if ack == veto.token { break; }
            std::thread::sleep(Duration::from_millis(2));
        }
        if !veto.owns_window() || Instant::now() >= limit {
            return Err("Activation protection preflight exceeded its deadline or the selected application's GUI thread changed; no input was sent".into());
        }
        // Leave only the activation callback installed during real input.
        // Failed unhook refuses approval and Drop retries both hook cleanups.
        unsafe { UnhookWindowsHookEx(ack_hook) }
            .map_err(|error| format!("Cannot finish activation protection preflight; no input was sent: {error}"))?;
        veto.ack_hook = 0;
        // Preflight consumes part of the original lease. Renew immediately
        // before approval so the existing two-second dispatch deadline fits.
        let renewed = lease_token(unsafe { GetTickCount64() } + LEASE_MS, sequence)?;
        unsafe { SetPropW(window, w!("OpenCore.ManualActivationLease.v1"), HANDLE(renewed as usize as *mut core::ffi::c_void)) }
            .map_err(|error| format!("Cannot renew background activation protection; no input was sent: {error}"))?;
        veto.token = renewed;
        if !veto.owns_window() {
            return Err("The selected application's window changed before background dispatch; no input was sent".into());
        }
        Ok(veto)
    }

    fn owns_window(&self) -> bool {
        let window = HWND(self.window as *mut core::ffi::c_void);
        let mut process = 0;
        unsafe { IsWindow(window) }.as_bool()
            && unsafe { GetAncestor(window, GA_ROOT) } == window
            && unsafe { GetWindowThreadProcessId(window, Some(&mut process)) } == self.thread
            && process == self.process
    }
}

impl Drop for ActivationVeto {
    fn drop(&mut self) {
        // Unhook first. Windows may finish an already-running callback after
        // this returns, so the library remains process-pinned and callbacks
        // reference only expiring kernel window properties, never Rust state.
        if self.ack_hook != 0 { unsafe { let _ = UnhookWindowsHookEx(HHOOK(self.ack_hook as *mut core::ffi::c_void)); } }
        if self.hook != 0 { unsafe { let _ = UnhookWindowsHookEx(HHOOK(self.hook as *mut core::ffi::c_void)); } }
        if self.lease_owned && self.owns_window() {
            let window = HWND(self.window as *mut core::ffi::c_void);
            let lease = unsafe { GetPropW(window, w!("OpenCore.ManualActivationLease.v1")) }.0 as usize as u64;
            if lease == self.token {
                unsafe { let _ = RemovePropW(window, w!("OpenCore.ManualActivationLease.v1")); }
                unsafe { let _ = RemovePropW(window, w!("OpenCore.ManualActivationAck.v1")); }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::lease_token;

    #[test]
    fn equal_clock_ticks_do_not_reuse_an_acknowledgement() {
        let first = lease_token(42_000, 1).unwrap();
        let second = lease_token(42_000, 2).unwrap();
        assert_ne!(first, second);
        assert_eq!(first >> 16, 42_000);
        assert_eq!(second >> 16, 42_000);
        assert!(lease_token(u64::MAX, 1).is_err());
    }
}
