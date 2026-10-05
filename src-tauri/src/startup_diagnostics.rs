use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

const MAX_LOG_BYTES: u64 = 512 * 1024;

#[derive(Clone)]
pub struct StartupDiagnostics {
    log_path: PathBuf,
    write_lock: Arc<Mutex<()>>,
}

impl StartupDiagnostics {
    pub fn new() -> Self {
        Self::at_path(default_log_path())
    }

    fn at_path(log_path: PathBuf) -> Self {
        Self {
            log_path,
            write_lock: Arc::new(Mutex::new(())),
        }
    }

    pub fn path(&self) -> &Path {
        &self.log_path
    }

    pub fn record(&self, stage: &str, message: &str) -> bool {
        let Ok(_guard) = self.write_lock.lock() else {
            eprintln!("OpenCore startup log lock was poisoned");
            return false;
        };
        match self.write_record(stage, message) {
            Ok(()) => true,
            Err(error) => {
                eprintln!("Could not write OpenCore startup log: {error}");
                false
            }
        }
    }

    pub fn show_startup_error(&self, stage: &str, message: &str) {
        let text = if self.record(stage, message) {
            format!(
                "OpenCore could not finish starting.\n\n{message}\n\nStartup details were saved to:\n{}",
                self.log_path.display()
            )
        } else {
            format!(
                "OpenCore could not finish starting.\n\n{message}\n\nOpenCore could not write its startup log. Expected path:\n{}",
                self.log_path.display()
            )
        };

        #[cfg(windows)]
        {
            use windows::core::PCWSTR;
            use windows::Win32::Foundation::HWND;
            use windows::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};

            let text: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
            let title: Vec<u16> = "OpenCore startup error"
                .encode_utf16()
                .chain(std::iter::once(0))
                .collect();
            unsafe {
                let _ = MessageBoxW(
                    HWND::default(),
                    PCWSTR(text.as_ptr()),
                    PCWSTR(title.as_ptr()),
                    MB_OK | MB_ICONERROR,
                );
            }
        }

        #[cfg(not(windows))]
        eprintln!("{text}");
    }

    fn write_record(&self, stage: &str, message: &str) -> std::io::Result<()> {
        if let Some(parent) = self.log_path.parent() {
            fs::create_dir_all(parent)?;
        }
        if fs::metadata(&self.log_path).is_ok_and(|metadata| metadata.len() >= MAX_LOG_BYTES) {
            let previous = self.log_path.with_file_name("startup.previous.log");
            let _ = fs::remove_file(&previous);
            fs::rename(&self.log_path, previous)?;
        }

        let stage = stage.replace(['\r', '\n'], " ");
        let message = message.replace(['\r', '\n'], " ");
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.log_path)?;
        writeln!(
            file,
            "{} [{}] {}",
            chrono::Utc::now().to_rfc3339(),
            stage,
            message
        )
    }
}

fn default_log_path() -> PathBuf {
    let root = std::env::var_os("LOCALAPPDATA")
        .or_else(|| std::env::var_os("APPDATA"))
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    root.join("OpenCore").join("logs").join("startup.log")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn records_startup_stage_and_error_details() {
        let root = std::env::temp_dir().join(format!("opencore-startup-{}", uuid::Uuid::new_v4()));
        let diagnostics = StartupDiagnostics::at_path(root.join("logs").join("startup.log"));

        diagnostics.record("database_open_failed", "database is locked");

        let text = std::fs::read_to_string(diagnostics.path()).unwrap();
        assert!(text.contains("database_open_failed"));
        assert!(text.contains("database is locked"));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn keeps_startup_log_bounded_and_preserves_one_previous_copy() {
        let root = std::env::temp_dir().join(format!("opencore-startup-{}", uuid::Uuid::new_v4()));
        let log_path = root.join("logs").join("startup.log");
        std::fs::create_dir_all(log_path.parent().unwrap()).unwrap();
        std::fs::write(&log_path, vec![b'x'; MAX_LOG_BYTES as usize + 1]).unwrap();
        let diagnostics = StartupDiagnostics::at_path(log_path.clone());

        diagnostics.record("launch", "starting");

        assert!(std::fs::metadata(&log_path).unwrap().len() < MAX_LOG_BYTES);
        assert_eq!(
            std::fs::metadata(log_path.with_file_name("startup.previous.log"))
                .unwrap()
                .len(),
            MAX_LOG_BYTES + 1
        );
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn reports_when_the_startup_log_cannot_be_written() {
        let root = std::env::temp_dir().join(format!("opencore-startup-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let blocked_directory = root.join("not-a-directory");
        std::fs::write(&blocked_directory, b"block directory creation").unwrap();
        let diagnostics = StartupDiagnostics::at_path(blocked_directory.join("startup.log"));

        assert!(!diagnostics.record("launch", "starting"));

        let _ = std::fs::remove_dir_all(root);
    }
}
