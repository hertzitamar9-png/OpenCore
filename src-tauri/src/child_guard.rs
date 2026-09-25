//! Processes OpenCore starts (llama-server, ECHO, Reflex) must not outlive it. A crash or a
//! forced exit used to leave the model server holding ~10 GB of VRAM. Every child joins one
//! Windows job object with kill-on-close, so the OS ends them when OpenCore's handle closes.

use std::process::Child;

#[cfg(windows)]
pub fn adopt(child: &Child) {
    use std::os::windows::io::AsRawHandle;
    adopt_handle(child.as_raw_handle());
}

#[cfg(windows)]
pub fn adopt_handle(handle: std::os::windows::io::RawHandle) {
    use std::sync::OnceLock;
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    static JOB: OnceLock<Option<usize>> = OnceLock::new();
    let job = JOB.get_or_init(|| unsafe {
        let job = CreateJobObjectW(None, windows::core::PCWSTR::null()).ok()?;
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(job, JobObjectExtendedLimitInformation,
            &limits as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32).ok()?;
        Some(job.0 as usize) // the handle stays open for OpenCore's lifetime on purpose
    });
    if let Some(job) = job {
        unsafe {
            let _ = AssignProcessToJobObject(HANDLE(*job as *mut core::ffi::c_void),
                HANDLE(handle));
        }
    }
}

#[cfg(not(windows))]
pub fn adopt(_child: &Child) {}

/// Let a helper process bring a window forward. Only the foreground process may grant this,
/// which OpenCore is while the user is sending a request.
#[cfg(windows)]
pub fn allow_foreground_handoff() {
    use windows::Win32::UI::WindowsAndMessaging::{AllowSetForegroundWindow, ASFW_ANY};
    unsafe { let _ = AllowSetForegroundWindow(ASFW_ANY); }
}

#[cfg(not(windows))]
pub fn allow_foreground_handoff() {}
