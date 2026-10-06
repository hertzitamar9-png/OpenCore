//! Processes OpenCore starts (llama-server, ECHO, Reflex) must not outlive it. A crash or a
//! forced exit used to leave the model server holding ~10 GB of VRAM. Every child joins one
//! Windows job object with kill-on-close, so the OS ends them when OpenCore's handle closes.

use std::process::Child;

/// A runtime owns its own nested job so Stop ends its complete process tree,
/// without ending speech or other services that share the app lifetime job.
pub struct ProcessJob {
    #[cfg(windows)]
    handle: usize,
}

impl ProcessJob {
    pub fn new(child: &Child) -> Result<Self, String> {
        #[cfg(windows)]
        {
            use std::os::windows::io::AsRawHandle;
            Self::from_windows_handle(child.as_raw_handle())
        }
        #[cfg(not(windows))]
        {
            let _ = child;
            Ok(Self {})
        }
    }

    #[cfg(windows)]
    pub(crate) fn for_async_child(child: &tokio::process::Child) -> Result<Self, String> {
        Self::from_windows_handle(child.raw_handle().ok_or("The desktop helper has no owned process handle")?)
    }

    #[cfg(windows)]
    fn from_windows_handle(child: std::os::windows::io::RawHandle) -> Result<Self, String> {
        unsafe {
            use windows::Win32::Foundation::{CloseHandle, HANDLE};
            use windows::Win32::System::JobObjects::{
                AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
                SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            };
            let job = CreateJobObjectW(None, windows::core::PCWSTR::null()).map_err(|e| e.to_string())?;
            let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let configured = SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                &limits as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32)
                .and_then(|_| AssignProcessToJobObject(job, HANDLE(child)));
            if let Err(error) = configured {
                let _ = CloseHandle(job);
                return Err(format!("Could not own the runtime process tree: {error}"));
            }
            Ok(Self { handle: job.0 as usize })
        }
    }
}

impl Drop for ProcessJob {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            use windows::Win32::Foundation::{CloseHandle, HANDLE};
            let _ = CloseHandle(HANDLE(self.handle as *mut core::ffi::c_void));
        }
    }
}

/// Reuse the signed app bundle's CUDA and VC dependencies for optional runtimes.
/// Keep the user's global PATH unchanged and avoid another half-gigabyte copy.
pub fn inference_dependencies(command: &mut std::process::Command, resources: Option<&std::path::Path>) {
    if let Some(directory) = resources.map(|root| root.join("doucode/runtime"))
        .filter(|path| path.join("cublas64_13.dll").is_file()) {
        let mut paths = vec![directory];
        if let Some(existing) = std::env::var_os("PATH") { paths.extend(std::env::split_paths(&existing)); }
        if let Ok(value) = std::env::join_paths(paths) { command.env("PATH", value); }
    }
}

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
