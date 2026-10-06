use super::{SyncReport, SYNC_CANCELLED};
use crate::{chat_import, connector_config, store::EventStore};
use std::path::PathBuf;

pub(super) fn sync_with_cancellation(
    store: &EventStore,
    id: &str,
    progress: &mut dyn FnMut(&SyncReport),
    cancelled: &dyn Fn() -> bool,
) -> Result<SyncReport, String> {
    let folder = store
        .get_setting(&format!("agent_connector_source_folder_{id}"))?
        .map(PathBuf::from)
        .map(Ok)
        .unwrap_or_else(|| match id {
            "opencode" => connector_config::opencode_history_root(),
            "hermes" => connector_config::hermes_history_root(),
            _ => Err(format!("History sync is not supported for {id}")),
        })?;
    if !folder.is_absolute() || !folder.is_dir() {
        return Err("The original agent profile folder is missing. Choose an existing source folder for this connector.".into());
    }
    let (file, format) = if id == "opencode" {
        (folder.join("opencode.db"), "opencode")
    } else {
        (folder.join("state.db"), "hermes")
    };
    if !file.is_file() {
        return Err(format!("No {id} session database was found in the selected profile. Choose its data folder, or import a native JSON/JSONL export."));
    }
    let convert = |report: &chat_import::ImportReport| SyncReport {
        current: report.current,
        total: report.total,
        imported: report.imported,
        updated: report.updated,
        skipped: report.skipped,
        failed: report.failed,
        folders_found: report
            .conversations
            .iter()
            .filter(|chat| matches!(chat.folder_status.as_str(), "linked" | "available"))
            .count(),
        folders_unresolved: report
            .conversations
            .iter()
            .filter(|chat| !matches!(chat.folder_status.as_str(), "linked" | "available"))
            .count(),
    };
    let report = chat_import::import_file_with_cancellation(
        store,
        &file,
        format,
        cancelled,
        &mut |report| progress(&convert(report)),
    )
    .map_err(|error| {
        if error == chat_import::IMPORT_CANCELLED {
            SYNC_CANCELLED.into()
        } else {
            error
        }
    })?;
    for conversation in &report.conversations {
        if let Some(error) = &conversation.error {
            store.log(
                "warn",
                "history",
                &format!("{id}: {}: {error}", conversation.title),
            );
        }
        for warning in &conversation.warnings {
            store.log(
                "warn",
                "history",
                &format!("{id}: {}: {warning}", conversation.title),
            );
        }
    }
    if report.cancelled {
        return Err(SYNC_CANCELLED.into());
    }
    Ok(convert(&report))
}
