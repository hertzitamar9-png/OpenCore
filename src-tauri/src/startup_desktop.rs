//! Enter the user's desktop process context before opening any app data.
//!
//! A launch from a packaged host can inherit filesystem redirection even when
//! GetCurrentPackageFullName reports no identity. Use the real desktop shell as
//! the process-creation parent instead of inferring filesystem policy from that
//! API. No conversation paths or package names are changed here.

#[derive(Debug, PartialEq, Eq)]
pub enum StartupDisposition {
    StartHere,
    Relaunched,
}

pub fn prepare() -> Result<StartupDisposition, String> {
    #[cfg(windows)]
    return windows_impl::prepare();
    #[cfg(not(windows))]
    Ok(StartupDisposition::StartHere)
}

/// Report failures without starting diagnostics or touching redirected files.
pub fn show_error(error: &str) {
    let message = format!("OpenCore could not start in your Windows desktop session.\n\nClose this message and open OpenCore from your desktop shortcut.\n\nDetails: {error}");
    #[cfg(windows)]
    {
        use windows::core::PCWSTR;
        use windows::Win32::Foundation::HWND;
        use windows::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
        let text: Vec<u16> = message.encode_utf16().chain(std::iter::once(0)).collect();
        let title: Vec<u16> = "OpenCore startup error"
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect();
        unsafe {
            let _ = MessageBoxW(
                HWND::default(),
                PCWSTR(text.as_ptr()),
                PCWSTR(title.as_ptr()),
                MB_ICONERROR | MB_OK,
            );
        }
    }
    #[cfg(not(windows))]
    eprintln!("{message}");
}

#[cfg(any(windows, test))]
const RELAUNCH_GUARD: &str = "OPENCORE_DESKTOP_RELAUNCH";

#[cfg(any(windows, test))]
#[derive(Debug, PartialEq, Eq)]
enum LaunchDecision {
    StartHere,
    Relaunch,
}

#[cfg(any(windows, test))]
fn launch_decision(
    guard: Option<&str>,
    shell_pid: u32,
    actual_parent: Option<u32>,
) -> Result<LaunchDecision, String> {
    if shell_pid == 0 {
        return Err("The Windows desktop shell is unavailable".into());
    }
    let Some(guard) = guard else {
        return Ok(LaunchDecision::Relaunch);
    };
    let expected = guard
        .strip_prefix("shell:")
        .and_then(|pid| pid.parse::<u32>().ok());
    if expected == Some(shell_pid) && actual_parent == Some(shell_pid) {
        Ok(LaunchDecision::StartHere)
    } else {
        Err("The desktop relaunch did not have the expected Windows shell parent; startup was stopped before opening app data".into())
    }
}

#[cfg(any(windows, test))]
fn environment_name(entry: &[u16]) -> &[u16] {
    // Windows also keeps per-drive working directories as entries like =C:=... .
    let start = usize::from(entry.first() == Some(&(b'=' as u16)));
    let end = entry
        .iter()
        .enumerate()
        .skip(start)
        .find(|(_, value)| **value == b'=' as u16)
        .map(|(index, _)| index)
        .unwrap_or(entry.len());
    &entry[..end]
}

#[cfg(any(windows, test))]
fn is_guard_name(name: &[u16]) -> bool {
    name.len() == RELAUNCH_GUARD.len()
        && name
            .iter()
            .zip(RELAUNCH_GUARD.bytes())
            .all(|(actual, expected)| {
                let folded = if (b'a' as u16..=b'z' as u16).contains(actual) {
                    *actual - 32
                } else {
                    *actual
                };
                folded == expected as u16
            })
}

/// Copy the original Windows environment without changing the parent's block.
/// The comparator is Windows ordinal ordering in production, allowing pure
/// tests to exercise preservation and guard replacement without spawning.
#[cfg(any(windows, test))]
fn relaunch_environment(
    environment: &[u16],
    guard: &str,
    mut compare_names: impl FnMut(&[u16], &[u16]) -> Result<std::cmp::Ordering, String>,
) -> Result<Vec<u16>, String> {
    if !environment.ends_with(&[0, 0]) || guard.contains('\0') {
        return Err("The desktop relaunch environment is invalid".into());
    }
    let contents = &environment[..environment.len() - 2];
    let mut entries = Vec::new();
    if !contents.is_empty() {
        for entry in contents.split(|value| *value == 0) {
            if entry.is_empty() {
                return Err("The desktop relaunch environment contains an empty entry".into());
            }
            if !is_guard_name(environment_name(entry)) {
                entries.push(entry);
            }
        }
    }
    let guard_entry: Vec<u16> = format!("{RELAUNCH_GUARD}={guard}").encode_utf16().collect();
    entries.push(&guard_entry);
    let mut compare_error = None;
    entries.sort_by(|left, right| {
        match compare_names(environment_name(left), environment_name(right)) {
            Ok(order) => order,
            Err(error) => {
                compare_error.get_or_insert(error);
                std::cmp::Ordering::Equal
            }
        }
    });
    if let Some(error) = compare_error {
        return Err(error);
    }
    let mut result = Vec::new();
    for entry in entries {
        result.extend_from_slice(entry);
        result.push(0);
    }
    result.push(0);
    Ok(result)
}

#[cfg(windows)]
mod windows_impl {
    use super::*;
    use std::ffi::c_void;
    use std::os::windows::ffi::OsStrExt;
    use windows::core::{PCWSTR, PWSTR};
    use windows::Win32::Foundation::{CloseHandle, ERROR_NO_MORE_FILES, HANDLE};
    use windows::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    use windows::Win32::System::Threading::{
        CreateProcessW, DeleteProcThreadAttributeList, InitializeProcThreadAttributeList,
        OpenProcess, ResumeThread, TerminateProcess, UpdateProcThreadAttribute,
        WaitForSingleObject, CREATE_NO_WINDOW, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT,
        EXTENDED_STARTUPINFO_PRESENT, LPPROC_THREAD_ATTRIBUTE_LIST, PROCESS_CREATE_PROCESS,
        PROCESS_INFORMATION, PROC_THREAD_ATTRIBUTE_PARENT_PROCESS, STARTUPINFOEXW,
    };
    use windows::Win32::UI::WindowsAndMessaging::{GetShellWindow, GetWindowThreadProcessId};

    #[link(name = "kernel32")]
    extern "system" {
        fn GetCommandLineW() -> *const u16;
        fn GetEnvironmentStringsW() -> *mut u16;
        fn FreeEnvironmentStringsW(environment: *mut u16) -> i32;
        fn CompareStringOrdinal(
            left: *const u16,
            left_len: i32,
            right: *const u16,
            right_len: i32,
            ignore_case: i32,
        ) -> i32;
    }

    struct OwnedHandle(HANDLE);
    impl Drop for OwnedHandle {
        fn drop(&mut self) {
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }

    struct AttributeList {
        list: LPPROC_THREAD_ATTRIBUTE_LIST,
        _storage: Vec<usize>,
    }
    impl AttributeList {
        fn new() -> Result<Self, String> {
            let mut bytes = 0;
            let _ = unsafe {
                InitializeProcThreadAttributeList(
                    LPPROC_THREAD_ATTRIBUTE_LIST::default(),
                    1,
                    0,
                    &mut bytes,
                )
            };
            if bytes == 0 {
                return Err(format!(
                    "Could not size desktop process attributes: {}",
                    std::io::Error::last_os_error()
                ));
            }
            let mut storage = vec![0usize; bytes.div_ceil(std::mem::size_of::<usize>())];
            let list = LPPROC_THREAD_ATTRIBUTE_LIST(storage.as_mut_ptr().cast());
            unsafe { InitializeProcThreadAttributeList(list, 1, 0, &mut bytes) }.map_err(
                |error| format!("Could not initialize desktop process attributes: {error}"),
            )?;
            Ok(Self {
                list,
                _storage: storage,
            })
        }
    }
    impl Drop for AttributeList {
        fn drop(&mut self) {
            unsafe {
                DeleteProcThreadAttributeList(self.list);
            }
        }
    }

    struct EnvironmentStrings(*mut u16);
    impl Drop for EnvironmentStrings {
        fn drop(&mut self) {
            unsafe {
                let _ = FreeEnvironmentStringsW(self.0);
            }
        }
    }
    fn environment(guard: &str) -> Result<Vec<u16>, String> {
        let pointer = unsafe { GetEnvironmentStringsW() };
        if pointer.is_null() {
            return Err(format!(
                "Could not read the Windows process environment: {}",
                std::io::Error::last_os_error()
            ));
        }
        let owned = EnvironmentStrings(pointer);
        // GetEnvironmentStringsW owns a valid UTF-16 block terminated by two
        // NULs. Preserve its entries, including hidden drive-directory values.
        let original = unsafe {
            let mut length = 0;
            while *owned.0.add(length) != 0 || *owned.0.add(length + 1) != 0 {
                length += 1;
            }
            std::slice::from_raw_parts(owned.0, length + 2)
        };
        relaunch_environment(original, guard, |left, right| {
            let left_len = i32::try_from(left.len())
                .map_err(|_| "A Windows environment name is too long".to_string())?;
            let right_len = i32::try_from(right.len())
                .map_err(|_| "A Windows environment name is too long".to_string())?;
            match unsafe {
                CompareStringOrdinal(left.as_ptr(), left_len, right.as_ptr(), right_len, 1)
            } {
                1 => Ok(std::cmp::Ordering::Less),
                2 => Ok(std::cmp::Ordering::Equal),
                3 => Ok(std::cmp::Ordering::Greater),
                _ => Err(format!(
                    "Could not order the Windows environment: {}",
                    std::io::Error::last_os_error()
                )),
            }
        })
    }

    fn process_parent(pid: u32) -> Result<u32, String> {
        let snapshot = OwnedHandle(
            unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) }
                .map_err(|error| format!("Could not verify the desktop process parent: {error}"))?,
        );
        let mut entry = PROCESSENTRY32W {
            dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };
        unsafe { Process32FirstW(snapshot.0, &mut entry) }
            .map_err(|error| format!("Could not read the process snapshot: {error}"))?;
        loop {
            if entry.th32ProcessID == pid {
                return Ok(entry.th32ParentProcessID);
            }
            entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
            match unsafe { Process32NextW(snapshot.0, &mut entry) } {
                Ok(()) => {}
                Err(error)
                    if error.code()
                        == windows::core::HRESULT::from_win32(ERROR_NO_MORE_FILES.0) =>
                {
                    break
                }
                Err(error) => {
                    return Err(format!("Could not continue the process snapshot: {error}"))
                }
            }
        }
        Err("The desktop process was missing from the process snapshot".into())
    }

    struct OwnedChild {
        info: PROCESS_INFORMATION,
        terminate_on_drop: bool,
    }
    impl OwnedChild {
        fn activate(&mut self) -> Result<(), String> {
            let previous = unsafe { ResumeThread(self.info.hThread) };
            if previous == 1 {
                Ok(())
            } else if previous == u32::MAX {
                Err(format!(
                    "Could not activate the desktop app process: {}",
                    std::io::Error::last_os_error()
                ))
            } else {
                Err(format!(
                    "The desktop app process had an unexpected suspension count ({previous})"
                ))
            }
        }
        fn detach(mut self) {
            self.terminate_on_drop = false;
        }
    }
    impl Drop for OwnedChild {
        fn drop(&mut self) {
            unsafe {
                if self.terminate_on_drop {
                    let _ = TerminateProcess(self.info.hProcess, 1);
                    let _ = WaitForSingleObject(self.info.hProcess, 5_000);
                }
                let _ = CloseHandle(self.info.hThread);
                let _ = CloseHandle(self.info.hProcess);
            }
        }
    }

    fn spawn_with_parent(
        executable: &std::path::Path,
        command_line: &mut [u16],
        parent: &OwnedHandle,
        parent_pid: u32,
    ) -> Result<OwnedChild, String> {
        let executable: Vec<u16> = executable
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        let current_directory = std::env::current_dir().map_err(|error| {
            format!("Could not preserve the OpenCore working directory: {error}")
        })?;
        let current_directory: Vec<u16> = current_directory
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        let environment = environment(&format!("shell:{parent_pid}"))?;
        let attributes = AttributeList::new()?;
        // Microsoft documents that this attribute inherits the selected
        // parent's process token and device map. The value and its handle remain
        // alive until the attribute list is deleted.
        // https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
        unsafe {
            UpdateProcThreadAttribute(
                attributes.list,
                0,
                PROC_THREAD_ATTRIBUTE_PARENT_PROCESS as usize,
                Some((&parent.0 as *const HANDLE).cast::<c_void>()),
                std::mem::size_of::<HANDLE>(),
                None,
                None,
            )
        }
        .map_err(|error| {
            format!("Could not select the Windows desktop process context: {error}")
        })?;
        let mut startup = STARTUPINFOEXW::default();
        startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
        startup.lpAttributeList = attributes.list;
        let mut info = PROCESS_INFORMATION::default();
        unsafe {
            CreateProcessW(
                PCWSTR(executable.as_ptr()),
                PWSTR(command_line.as_mut_ptr()),
                None,
                None,
                false,
                EXTENDED_STARTUPINFO_PRESENT
                    | CREATE_UNICODE_ENVIRONMENT
                    | CREATE_SUSPENDED
                    | CREATE_NO_WINDOW,
                Some(environment.as_ptr().cast()),
                PCWSTR(current_directory.as_ptr()),
                &startup.StartupInfo,
                &mut info,
            )
        }
        .map_err(|error| format!("Could not relaunch OpenCore in the desktop session: {error}"))?;
        let child = OwnedChild {
            info,
            terminate_on_drop: true,
        };
        if process_parent(child.info.dwProcessId)? != parent_pid {
            return Err(
                "The newly created process did not inherit the requested desktop parent".into(),
            );
        }
        Ok(child)
    }

    pub(super) fn prepare() -> Result<StartupDisposition, String> {
        let shell = unsafe { GetShellWindow() };
        let mut shell_pid = 0;
        if !shell.is_invalid() {
            unsafe {
                GetWindowThreadProcessId(shell, Some(&mut shell_pid));
            }
        }
        let guard = std::env::var_os(RELAUNCH_GUARD);
        let guard_text = guard
            .as_deref()
            .map(|value| {
                value
                    .to_str()
                    .ok_or("The desktop relaunch guard is invalid")
            })
            .transpose()?;
        let parent = if guard.is_some() {
            Some(process_parent(std::process::id())?)
        } else {
            None
        };
        match launch_decision(guard_text, shell_pid, parent)? {
            LaunchDecision::StartHere => {
                // Only this child environment is modified. Background workers
                // and updater restarts must receive a fresh normalization check.
                std::env::remove_var(RELAUNCH_GUARD);
                Ok(StartupDisposition::StartHere)
            }
            LaunchDecision::Relaunch => {
                let desktop = OwnedHandle(
                    unsafe { OpenProcess(PROCESS_CREATE_PROCESS, false, shell_pid) }.map_err(
                        |error| {
                            format!("Could not access the Windows desktop process context: {error}")
                        },
                    )?,
                );
                let executable = std::env::current_exe().map_err(|error| {
                    format!("Could not locate the running OpenCore executable: {error}")
                })?;
                let pointer = unsafe { GetCommandLineW() };
                if pointer.is_null() {
                    return Err("Could not read the original OpenCore command line".into());
                }
                // Copy the original UTF-16 command line verbatim, preserving
                // quotes, protocol URLs, CLI arguments, and non-Unicode paths.
                let mut command_line = unsafe { PCWSTR(pointer).as_wide() }.to_vec();
                command_line.push(0);
                let mut child =
                    spawn_with_parent(&executable, &mut command_line, &desktop, shell_pid)?;
                child.activate()?;
                child.detach();
                Ok(StartupDisposition::Relaunched)
            }
        }
    }

    #[cfg(test)]
    pub(super) fn test_parent_attribute() -> Result<(), String> {
        use windows::Win32::Foundation::WAIT_OBJECT_0;
        use windows::Win32::System::Threading::GetExitCodeProcess;
        let pid = std::process::id();
        let original_guard = std::env::var_os(RELAUNCH_GUARD);
        let parent = OwnedHandle(
            unsafe { OpenProcess(PROCESS_CREATE_PROCESS, false, pid) }
                .map_err(|error| error.to_string())?,
        );
        let executable = std::path::PathBuf::from(
            std::env::var_os("SystemRoot").ok_or("SystemRoot is missing")?,
        )
        .join("System32")
        .join("cmd.exe");
        let expected_directory = std::env::current_dir().map_err(|error| error.to_string())?;
        let mut command: Vec<u16> = format!("\"{}\" /D /C if \"%OPENCORE_DESKTOP_RELAUNCH%\"==\"shell:{pid}\" (if \"%CD%\"==\"{}\" (exit /b 0) else (exit /b 74)) else (exit /b 73)", executable.display(), expected_directory.display()).encode_utf16().chain(std::iter::once(0)).collect();
        let mut child = spawn_with_parent(&executable, &mut command, &parent, pid)?;
        assert_eq!(process_parent(child.info.dwProcessId)?, pid);
        assert_eq!(std::env::var_os(RELAUNCH_GUARD), original_guard);
        child.activate()?;
        if unsafe { WaitForSingleObject(child.info.hProcess, 5_000) } != WAIT_OBJECT_0 {
            return Err("The desktop-context test child did not exit".into());
        }
        let mut exit_code = 0;
        unsafe { GetExitCodeProcess(child.info.hProcess, &mut exit_code) }
            .map_err(|error| error.to_string())?;
        assert_eq!(
            exit_code, 0,
            "the child must receive the scoped guard environment and original working directory"
        );
        assert_eq!(std::env::var_os(RELAUNCH_GUARD), original_guard);
        child.detach();
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn block(entries: &[&str]) -> Vec<u16> {
        let mut result = Vec::new();
        for entry in entries {
            result.extend(entry.encode_utf16());
            result.push(0);
        }
        if result.is_empty() {
            result.push(0);
        }
        result.push(0);
        result
    }

    fn ascii_order(left: &[u16], right: &[u16]) -> Result<std::cmp::Ordering, String> {
        fn fold(value: u16) -> u16 {
            if (b'a' as u16..=b'z' as u16).contains(&value) {
                value - 32
            } else {
                value
            }
        }
        Ok(left
            .iter()
            .copied()
            .map(fold)
            .cmp(right.iter().copied().map(fold)))
    }

    #[test]
    fn normalizes_a_first_launch_even_when_the_caller_is_already_the_shell() {
        assert_eq!(
            launch_decision(None, 42, Some(42)).unwrap(),
            LaunchDecision::Relaunch
        );
    }

    #[test]
    fn only_a_guarded_child_of_the_current_desktop_shell_can_start_the_app() {
        assert_eq!(
            launch_decision(Some("shell:42"), 42, Some(42)).unwrap(),
            LaunchDecision::StartHere
        );
        assert!(launch_decision(Some("shell:42"), 42, Some(99)).is_err());
        assert!(launch_decision(Some("shell:42"), 42, None).is_err());
    }

    #[test]
    fn failed_normalization_stops_instead_of_relaunching_again() {
        for guard in ["", "1", "shell:0", "shell:99", "shell:no", "shell:42:again"] {
            assert!(
                launch_decision(Some(guard), 42, Some(42)).is_err(),
                "{guard}"
            );
        }
        assert!(launch_decision(None, 0, None).is_err());
        assert!(launch_decision(Some("shell:42"), 0, Some(42)).is_err());
    }

    #[test]
    fn child_environment_preserves_unicode_drive_entries_and_embedded_equals() {
        let source = block(&[
            "=C:=C:\\work",
            "APPDATA=C:\\Users\\hertz\\AppData\\Roaming",
            "NOTE=a=b=שלום",
            "Z_LAST=end",
        ]);
        let original = source.clone();
        let actual = relaunch_environment(&source, "shell:42", ascii_order).unwrap();
        assert_eq!(
            source, original,
            "the parent environment must not be changed"
        );
        assert_eq!(
            actual,
            block(&[
                "=C:=C:\\work",
                "APPDATA=C:\\Users\\hertz\\AppData\\Roaming",
                "NOTE=a=b=שלום",
                "OPENCORE_DESKTOP_RELAUNCH=shell:42",
                "Z_LAST=end"
            ])
        );
    }

    #[test]
    fn child_environment_replaces_a_stale_guard_case_insensitively() {
        let source = block(&[
            "A=first",
            "opencore_desktop_relaunch=shell:9",
            "OPENCORE_DESKTOP_RELAUNCH=bad",
            "Z=last",
        ]);
        assert_eq!(
            relaunch_environment(&source, "shell:42", ascii_order).unwrap(),
            block(&["A=first", "OPENCORE_DESKTOP_RELAUNCH=shell:42", "Z=last"])
        );
        assert_eq!(
            relaunch_environment(&block(&[]), "shell:42", ascii_order).unwrap(),
            block(&["OPENCORE_DESKTOP_RELAUNCH=shell:42"])
        );
    }

    #[test]
    fn invalid_environment_or_ordering_fails_before_child_creation() {
        assert!(relaunch_environment(&[], "shell:42", ascii_order).is_err());
        assert!(relaunch_environment(&[b'A' as u16, 0], "shell:42", ascii_order).is_err());
        assert!(
            relaunch_environment(&block(&["A=1"]), "shell:42", |_, _| Err(
                "ordinal comparison failed".into()
            ))
            .is_err()
        );
    }

    #[cfg(windows)]
    #[test]
    fn native_parent_attribute_selects_the_requested_parent_and_child_environment() {
        windows_impl::test_parent_attribute().unwrap();
    }
}
