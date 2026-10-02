//! Read-only OS process wait. Keeps no model resident and never stops the observed process.
#[cfg(windows)]
pub struct ProcessWatch {
    handle: usize,
    pub created: u64,
}
#[cfg(windows)]
impl ProcessWatch {
    pub fn open(pid: u32) -> Result<Self, String> {
        use windows::Win32::{
            Foundation::{CloseHandle, FILETIME},
            System::Threading::{
                GetProcessTimes, OpenProcess, PROCESS_ACCESS_RIGHTS,
                PROCESS_QUERY_LIMITED_INFORMATION,
            },
        };
        if pid == 0 {
            return Err("Choose a nonzero local process ID".into());
        }
        let handle = unsafe {
            OpenProcess(
                PROCESS_ACCESS_RIGHTS(PROCESS_QUERY_LIMITED_INFORMATION.0 | 0x00100000),
                false,
                pid,
            )
        }
        .map_err(|e| e.to_string())?; // SYNCHRONIZE: wait without modifying the process.
        let (mut created, mut exited, mut kernel, mut user) = (
            FILETIME::default(),
            FILETIME::default(),
            FILETIME::default(),
            FILETIME::default(),
        );
        if let Err(error) =
            unsafe { GetProcessTimes(handle, &mut created, &mut exited, &mut kernel, &mut user) }
        {
            let _ = unsafe { CloseHandle(handle) };
            return Err(error.to_string());
        }
        Ok(Self {
            handle: handle.0 as usize,
            created: ((created.dwHighDateTime as u64) << 32) | created.dwLowDateTime as u64,
        })
    }
    pub fn exit_code(&self) -> Result<Option<u32>, String> {
        use windows::Win32::{
            Foundation::{HANDLE, WAIT_OBJECT_0, WAIT_TIMEOUT},
            System::Threading::{GetExitCodeProcess, WaitForSingleObject},
        };
        let handle = HANDLE(self.handle as *mut _);
        match unsafe { WaitForSingleObject(handle, 0) } {
            WAIT_TIMEOUT => return Ok(None),
            WAIT_OBJECT_0 => {}
            _ => return Err("Process wait failed".into()),
        };
        let mut code = 0;
        unsafe { GetExitCodeProcess(HANDLE(self.handle as *mut _), &mut code) }
            .map_err(|e| e.to_string())?;
        Ok(Some(code))
    }
}
#[cfg(windows)]
impl Drop for ProcessWatch {
    fn drop(&mut self) {
        let _ = unsafe {
            windows::Win32::Foundation::CloseHandle(windows::Win32::Foundation::HANDLE(
                self.handle as *mut _,
            ))
        };
    }
}
#[cfg(not(windows))]
pub struct ProcessWatch {
    pub created: u64,
}
#[cfg(not(windows))]
impl ProcessWatch {
    pub fn open(_: u32) -> Result<Self, String> {
        Err("Local process waits currently require Windows".into())
    }
    pub fn exit_code(&self) -> Result<Option<u32>, String> {
        Err("Local process waits currently require Windows".into())
    }
}
#[cfg(all(test, windows))]
mod tests {
    use super::*;
    #[test]
    fn observes_real_exit_code_without_terminating_process() {
        use std::os::windows::process::CommandExt;
        let mut child = std::process::Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-Command",
                "Start-Sleep -Milliseconds 300; exit 7",
            ])
            .creation_flags(0x08000000)
            .spawn()
            .unwrap();
        let watcher = ProcessWatch::open(child.id()).unwrap();
        assert_ne!(watcher.created, 0);
        assert_eq!(watcher.exit_code().unwrap(), None);
        child.wait().unwrap();
        assert_eq!(watcher.exit_code().unwrap(), Some(7));
    }
}
