//! Copy SQLite files without opening the source through SQLite: even a read-only
//! SQLite connection can update a source WAL index. Only the private copy is read
//! by SQLite. Windows byte-range locks coordinate with the standard SQLite VFS.
use super::{check_cancelled, unchanged, BUSY_CHAT_DATABASE, MAX_DATABASE_BYTES};
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

const SOURCE_CHANGED: &str = "The source database changed while it was being copied. Retry after the current save finishes, or import a JSON/JSONL export.";

pub(super) struct DatabaseSnapshot {
    root: PathBuf,
    pub(super) path: PathBuf,
}
impl Drop for DatabaseSnapshot {
    fn drop(&mut self) {
        if let Ok(entries) = fs::read_dir(&self.root) {
            for entry in entries.flatten() {
                let _ = fs::remove_file(entry.path());
            }
        }
        let _ = fs::remove_dir(&self.root);
    }
}
pub(super) fn sqlite_sidecar(path: &Path, suffix: &str) -> PathBuf {
    let mut name = path.as_os_str().to_os_string();
    name.push(suffix);
    PathBuf::from(name)
}

#[cfg(not(windows))]
pub(super) fn snapshot_database(
    _path: &Path,
    cancelled: &dyn Fn() -> bool,
) -> Result<DatabaseSnapshot, String> {
    check_cancelled(cancelled)?;
    Err("SQLite import requires Windows snapshot locks. Use a JSON/JSONL export on this platform.".into())
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::io::{Seek, SeekFrom};
    use std::os::windows::fs::OpenOptionsExt;
    use std::os::windows::io::AsRawHandle;
    use std::time::{Duration, Instant};

    // SQLite's standard Windows VFS reserves the main-file lock page at 1 GiB.
    // Leave PENDING_BYTE free: Windows briefly takes it exclusively even when
    // starting a read. Shared RESERVED + SHARED bytes exclude rollback writers
    // and EXCLUSIVE-mode WAL clients while allowing normal readers to connect.
    const RESERVED_BYTE: u32 = 0x4000_0001;
    const RESERVED_AND_SHARED_BYTES: u32 = 511;
    // https://www.sqlite.org/walformat.html#wal_locks and SQLite os_win.c:
    // write/checkpoint/recovery occupy bytes 120..122; DMS at 128 prevents SHM
    // reinitialization. Read-mark slots stay free and SHM is never mapped/written.
    const WAL_MUTATION_LOCKS: u32 = 120;
    const SHM_LIFECYCLE_LOCK: u32 = 128;

    #[repr(C)]
    struct Overlapped {
        internal: usize,
        internal_high: usize,
        offset: u32,
        offset_high: u32,
        event: *mut std::ffi::c_void,
    }
    impl Overlapped {
        fn at(offset: u32) -> Self {
            Self {
                internal: 0,
                internal_high: 0,
                offset,
                offset_high: 0,
                event: std::ptr::null_mut(),
            }
        }
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn LockFileEx(
            file: *mut std::ffi::c_void,
            flags: u32,
            reserved: u32,
            bytes_low: u32,
            bytes_high: u32,
            overlapped: *mut Overlapped,
        ) -> i32;
        fn UnlockFileEx(
            file: *mut std::ffi::c_void,
            reserved: u32,
            bytes_low: u32,
            bytes_high: u32,
            overlapped: *mut Overlapped,
        ) -> i32;
    }

    struct SourceFile {
        file: File,
        locks: Vec<(u32, u32)>,
    }
    impl SourceFile {
        fn open(path: &Path, exclude_write_handles: bool) -> std::io::Result<Self> {
            Ok(Self {
                file: fs::OpenOptions::new()
                    .read(true)
                    // Deny deletion/renaming throughout capture. Normal live
                    // read/write handles are compatible with FILE_SHARE_WRITE.
                    .share_mode(if exclude_write_handles { 1 } else { 3 })
                    .open(path)?,
                locks: Vec::new(),
            })
        }
        fn lock_shared(&mut self, offset: u32, size: u32) -> std::io::Result<()> {
            let mut overlapped = Overlapped::at(offset);
            // LOCKFILE_FAIL_IMMEDIATELY only: a shared lock works with a read-only
            // handle and conflicts with SQLite's exclusive mutation locks.
            if unsafe { LockFileEx(self.file.as_raw_handle(), 1, 0, size, 0, &mut overlapped) } == 0
            {
                return Err(std::io::Error::last_os_error());
            }
            self.locks.push((offset, size));
            Ok(())
        }
    }
    impl Drop for SourceFile {
        fn drop(&mut self) {
            for &(offset, size) in self.locks.iter().rev() {
                let mut overlapped = Overlapped::at(offset);
                unsafe {
                    let _ = UnlockFileEx(self.file.as_raw_handle(), 0, size, 0, &mut overlapped);
                }
            }
            // Closing the owned File also releases locks if an unlock failed.
        }
    }

    struct DatabaseSources {
        files: Vec<(SourceFile, PathBuf)>,
        _shm: Option<SourceFile>,
        _closed_main: Option<SourceFile>,
    }
    fn optional_source(path: &Path, strict: bool) -> std::io::Result<Option<SourceFile>> {
        match SourceFile::open(path, strict) {
            Ok(source) => Ok(Some(source)),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error),
        }
    }
    fn lock_sources(path: &Path) -> std::io::Result<DatabaseSources> {
        let mut main = SourceFile::open(path, false)?;
        main.lock_shared(RESERVED_BYTE, RESERVED_AND_SHARED_BYTES)?;
        let mut header = [0; 20];
        main.file.read_exact(&mut header)?;
        main.file.seek(SeekFrom::Start(0))?;

        let journal = sqlite_sidecar(path, "-journal");
        if let Some(mut source) = optional_source(&journal, false)? {
            if source.file.metadata()?.len() > 512 {
                let mut prefix = [0; 8];
                source.file.read_exact(&mut prefix)?;
                if prefix.iter().any(|byte| *byte != 0) {
                    return Err(std::io::Error::new(std::io::ErrorKind::InvalidData,
                        "The source database has an unfinished rollback transaction. Open it in its app to recover, or import a JSON/JSONL export."));
                }
            }
        }

        let wal_path = sqlite_sidecar(path, "-wal");
        let shm_path = sqlite_sidecar(path, "-shm");
        let mut shm = optional_source(&shm_path, false)?;
        if let Some(source) = &mut shm {
            source.lock_shared(SHM_LIFECYCLE_LOCK, 1)?;
            source.lock_shared(WAL_MUTATION_LOCKS, 3)?;
        }
        // A detached WAL pair has no SHM lock file. Do not create one at the
        // source: retain the old writer-exclusion guard in this case. Otherwise
        // a live WAL writer could create/reuse a WAL outside our SHM locks.
        let closed_main = if shm.is_none() && (header[18..20] == [2, 2] || wal_path.exists()) {
            Some(SourceFile::open(path, true)?)
        } else {
            None
        };
        let wal = optional_source(&wal_path, closed_main.is_some())?;
        let mut files = vec![(main, path.to_path_buf())];
        if let Some(wal) = wal {
            files.push((wal, wal_path));
        }
        Ok(DatabaseSources {
            files,
            _shm: shm,
            _closed_main: closed_main,
        })
    }
    fn is_busy(error: &std::io::Error) -> bool {
        matches!(error.raw_os_error(), Some(32) | Some(33))
    }
    pub(super) fn capture(
        path: &Path,
        cancelled: &dyn Fn() -> bool,
    ) -> Result<DatabaseSnapshot, String> {
        let started = Instant::now();
        let mut sources = loop {
            check_cancelled(cancelled)?;
            match lock_sources(path) {
                Ok(sources) => break sources,
                Err(error) if is_busy(&error) && started.elapsed() < Duration::from_secs(2) => {
                    std::thread::sleep(Duration::from_millis(40));
                }
                Err(error) if is_busy(&error) => return Err(format!("{BUSY_CHAT_DATABASE} Source: {}. Windows error: {error}", path.display())),
                Err(error) => {
                    return Err(format!(
                        "Cannot acquire a read-only database snapshot of {}: {error}", path.display()
                    ));
                }
            }
        };
        check_cancelled(cancelled)?;
        let initial: Vec<_> = sources
            .files
            .iter()
            .map(|(source, _)| source.file.metadata().map_err(|error| error.to_string()))
            .collect::<Result<_, _>>()?;
        let total = initial.iter().try_fold(0u64, |total, metadata| {
            if !metadata.is_file() {
                return Err("The database and its WAL must be regular files.");
            }
            total
                .checked_add(metadata.len())
                .ok_or("Database size is outside the supported range.")
        })?;
        if total > MAX_DATABASE_BYTES {
            return Err("The source database and WAL exceed the 512 MiB snapshot limit. Export selected conversations as JSONL instead.".into());
        }
        let root =
            std::env::temp_dir().join(format!("opencore-chat-snapshot-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root)
            .map_err(|error| format!("Cannot prepare a read-only database snapshot: {error}"))?;
        let snapshot = DatabaseSnapshot {
            path: root.join("history.sqlite3"),
            root,
        };
        for (index, ((source, _), before)) in sources.files.iter_mut().zip(&initial).enumerate() {
            let target = if index == 0 {
                snapshot.path.clone()
            } else {
                sqlite_sidecar(&snapshot.path, "-wal")
            };
            let mut output = File::create_new(target).map_err(|error| error.to_string())?;
            let mut copied = 0u64;
            let mut buffer = [0u8; 64 * 1024];
            loop {
                check_cancelled(cancelled)?;
                let count = source
                    .file
                    .read(&mut buffer)
                    .map_err(|error| error.to_string())?;
                if count == 0 {
                    break;
                }
                copied += count as u64;
                if copied > before.len() {
                    return Err(SOURCE_CHANGED.into());
                }
                output
                    .write_all(&buffer[..count])
                    .map_err(|error| error.to_string())?;
            }
            if copied != before.len()
                || !unchanged(
                    before,
                    &source.file.metadata().map_err(|error| error.to_string())?,
                )
            {
                return Err(SOURCE_CHANGED.into());
            }
        }
        for ((source, source_path), before) in sources.files.iter().zip(initial) {
            if !unchanged(
                &before,
                &source.file.metadata().map_err(|error| error.to_string())?,
            ) || !unchanged(
                &before,
                &fs::metadata(source_path).map_err(|error| error.to_string())?,
            ) {
                return Err(SOURCE_CHANGED.into());
            }
        }
        if sources.files.len() == 1 && sqlite_sidecar(path, "-wal").exists() {
            return Err(SOURCE_CHANGED.into());
        }
        Ok(snapshot)
    }
}

#[cfg(windows)]
pub(super) fn snapshot_database(
    path: &Path,
    cancelled: &dyn Fn() -> bool,
) -> Result<DatabaseSnapshot, String> {
    windows::capture(path, cancelled)
}
