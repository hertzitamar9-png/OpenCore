# Learning Studio and adaptive desktop layout

The user authorized implementation, integration and publication now. Preserve all existing chats, models, projects, preferences and the restored gradient appearance. Native tests, compilation and packaging run only in GitHub Actions.

## User outcome

Learning Studio brings exact ECHO records, conversation events and studio receipts into an organized local ledger. Nothing in the archive is replaced by a summary. Failed, corrected and unverified records remain visible. Export raw JSONL/CSV and separately prepare a versioned training dataset with source IDs, hashes and exact timestamps. Dataset preparation reports exclusions and token-length limits explicitly.

Three modes share one durable run system:

1. Manual exposes model source, SFT/DPO, BF16 LoRA or explicitly selected 4-bit QLoRA, epochs/step caps, learning rate, rank/alpha/dropout, batch/accumulation, context length, optimizer, checkpoint interval, seed, evaluation criteria and storage/time budgets.
2. AI configuration lets the selected assistant propose the same validated configuration from the goal, available hardware and time budget. The user can review and start it.
3. Auto tuning is a chat with a selectable existing model profile. The assistant prepares data and configuration, starts bounded training, follows checkpoints and evaluation, and reports the completed outcome. It asks about desired quality/time/goal when needed, not unexplained hyperparameters.

Use the real Apache-2.0 Unsloth Python training library and Transformers/TRL. Do not copy the separate Unsloth Studio AGPL UI. A catalog entry or installed GGUF alone is not a trainable Transformers checkpoint. Probe the selected source/interpreter and retain exact unsupported-architecture/hardware errors. Do not silently change requested BF16 precision to 4-bit. Reuse compatible existing environments; the released app can prepare an isolated supported environment without manual runtime path configuration.

## Evaluation and lifecycle

Every run has a frozen source/data/config manifest. Training and held-out sources must be disjoint. Persist baseline and candidate measurements, step/loss history, checkpoints, full stdout/stderr, duration, source hashes, timestamps and exact gate comparisons. Non-finite metrics, insufficient evaluation, unchanged/worse held-out objective or failed configured regression gates reject the candidate. Preserve rejected artifacts and state exactly which gate failed, expected threshold and observed value. A passing gate is evidence for that objective, not general intelligence. Existing models are never overwritten; accepted candidates are separate selectable artifacts with explicit provenance.

Training runs in checkpoint-sized owned processes. Saving a review checkpoint ends the process, releases GPU reservations and emits a durable named event for the originating assistant with its saved model/workspace/approval settings. The assistant reviews raw receipts and requests continuation. The next chunk resumes the actual checkpoint only after that assistant turn is finished and inference has unloaded. No text-model progress polling. Manual runs can automatically continue chunks without assistant review. Completion/failure/cancellation also emits a final continuation. The job persists waiting/rejected/interrupted states across restarts; it cannot claim completion while work is queued. Quit/update cancels owned training processes and preserves resumable evidence.

## Common app access and transparency

Expose Learning Studio through native commands and the same `learning_use` tool for every agent profile, retaining existing approvals. Existing settings/computer/browser/studio tools remain available. Add raw activity/history queries for dated studio outputs so questions such as latest song use recorded evidence. Store RFC3339 UTC and show full local year/month/day/hour/minute/second with exact UTC available in raw details. Keep prompts short and teach the capabilities through tool schemas.

## Responsive UI fixes

Derive a bounded fluid UI scale from the live viewport, preserve user font preferences, and reflow panels instead of stretching every layout. Remove narrow fixed content widths on large windows, keep usable small-window minimums, and prevent nested flex/grid children from extending past the footer. Files/Browser/Computer/Side chat tabs and their lower content must remain reachable at all supported sizes. Match maximize/restore button dimensions, hover and icon weight to the titlebar controls. Replace native gray selector menus with themed, accessible controls where they cause the reported mismatch. Supported runtime setup becomes part of the install/generate flow; manual connection remains an advanced option and unsupported adapters have precise requirements.

## Interfaces and ownership

- `learning_store.rs`: `LearningStore::new(root: PathBuf) -> Result<Arc<Self>, String>`, `sync_sources(&EventStore, &Path) -> Result<Value,String>`, `ingest(&Value)`, `query(&Value)`, `annotate(&Value)`, `export_dataset(&Value)`. Store commands use camelCase JSON. Dataset export returns `manifestPath`, `trainPath`, `validationPath`, counts and exclusions.
- `resources/learning/worker.py`: CLI `probe --output PATH`, `plan --request PATH --output PATH`, `train --request PATH`. A request includes `runId`, `modelPath`, `datasetManifest`, `trainPath`, `validationPath`, `outputDir`, `config`, optional `resumeCheckpoint`. Emit JSON lines with `event`, `timestamp`, `step`, `metrics`, `checkpoint` and final `status`. Write durable `receipt.json`, append-only `events.jsonl`, raw logs. Chunk completion uses `checkpoint-ready`; final uses `accepted`, `rejected`, `failed` or `cancelled`. No automatic replacement of the base model.
- `learning.rs`: root-owned native process lifecycle, GPU arbitration, resume/cancel, source ingestion and scheduler/agent continuations. `learning_command` handles desktop requests; `learning_use` handles approved agent requests.
- `LearningStudio.tsx`, `learningApi.ts`, `LearningStudio.css`: root-owned studio UI and dedicated real assistant chat, records/data/config/runs/raw views.
- Layout worker owns `styles.css`, `WorkspacePanel.tsx/.css`, `WindowTitleBar.tsx` and related regression cases, excluding `App.tsx` shared integration.

## Verification

Interpreted tests exercise archive byte preservation, pagination, CSV/JSONL escaping, failed records, timestamp precision, source-group holdout separation, config bounds, candidate rejection, actual chunk-resume orchestration, cancellation and sidebar geometry. Native tests and packaging run in GitHub. Qualify the actual Unsloth path when compatible local prerequisites exist; otherwise retain the exact blocker and do not describe a fixture as successful model training. Verify a published installed update, automatic reopen, data preservation and the resized UI before declaring the release finished.
