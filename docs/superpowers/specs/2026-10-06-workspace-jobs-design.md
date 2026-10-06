# OpenCore workspace and background work

## User outcome

Restore the actual installed shortcut launch first. Keep one installed OpenCore and preserve chats, models, settings and environments. Then make work transparent: schedules and event triggers can wake the agent without keeping inference loaded; files, snapshots and real line changes are visible; browser, computer use and side chat share one right workspace panel. Keep the existing dark visual style and reduce the difference between conversation and other navigation surfaces.

## Verified launch repair

The installed version was 0.2.107. The verified Actions release 0.2.110 was installed into the same directory after backing up its executable and conversation database. Installer SHA-256: 845bc9954c4151e57399f22a3c56336b03372a30dac99de9fa7a1f5569099cfb. Desktop and Start menu shortcuts both use that executable with the installed directory as working directory. Both launch paths and a clean close/reopen were observed. The intermittent exit was not reproduced on demand; a successful launch is evidence about these attempts, not proof of every possible startup failure. Keep diagnostics and verify the final build through the same installed path.

## Background work

Use a durable SQLite scheduler under app data, separate from model weights and conversation storage. Support one-shot UTC time, fixed intervals, five-field cron expressions with UTC or local timezone, and named events. Events have stable IDs; duplicate delivery cannot produce duplicate runs. An event trigger can match structured fields and run every N steps, for example training.checkpoint at step 500, 1000, 1500. Schedules coalesce missed occurrences into one pending run after reopening. UI states identify queued, running, completed, failed, cancelled and interrupted work with exact timestamps and errors.

Actions are an agent prompt in the originating chat or an explicitly configured command worker. Workers store their command and working directory, launch hidden, retain stdout/stderr logs, expose cancellation, and own their process tree. A worker may emit authenticated loopback events. Expose a local webhook with a per-install random bearer token, body limit and event deduplication; never expose it on the network. Show the endpoint and copyable event example in Jobs. No inference polling: only app timers and process/event code run while the agent waits. A prompt run waits until studio, speech, active chats and GPU reservations are idle, loads its saved model profile, invokes the actual existing harness with the existing permission mode, and releases inference after completion. Background actions never silently upgrade approval permissions.

The scheduler runs while OpenCore is open. Reopening recovers schedules and interrupted run evidence. App quit/update cancels owned workers and records interruption. Do not install a permanent OS service or silently alter Windows startup. State these execution conditions in the UI.

## File history and Spaces

Persist exact before/after versions of authored workspace files and studio output references in a separate ledger. Capture a workspace before and after each chat task, including failed or cancelled tasks that already changed files. Compute real line additions/removals; distinguish created, modified, deleted and binary output files. Store immutable content-addressed snapshots with hashes. Existing code-workspace/project versions, published artifacts and studio outputs are linked to their originating chat/job where evidence exists; do not fabricate old history.

Use traversal and byte limits, skip dependency/build/VCS trees and symlinks, and report coverage omissions. Never read outside the actual workspace via path traversal or symlinks. Preview stored text/HTML/images with confined IDs and explicit before/after selection. HTML previews are sandboxed and do not execute in the app's origin. Every record includes conversation, task/job, source path, timestamp, MIME, size and snapshot availability. History is searchable across chats, and a chat-filtered view lives in the workspace panel. Existing outputs can be indexed without re-generation.

Show the latest task's file changes below its response/receipt when that task ends: filename, +added, -removed and totals. Hide the previous result while a new task is active. Clicking a file opens its snapshot/diff in the right panel. Keep failed/cancelled partial changes inspectable.

## Unified workspace UI

Keep the compact navigation rail across all sections. Use a common restrained header with clear section title, Update control and Workspace access. Avoid exposing unrelated runtime debug controls on every studio page. Add Jobs and Spaces navigation entries.

The resizable right workspace panel has Files, Browser, Computer and Side chat tabs. File previews and browser links open there; computer use has an embedded layout, with the same existing controls. Panels must not overlap the conversation composer or use competing floating windows. Hide native browser views when switching tabs or closing the panel. Preserve original browser behavior and DOM bounds placement.

Side chat branches the main chat's current persisted context, uses the same selected model/context ceiling and shares its project/workspace. It has separate visible messages and can run only when another inference/studio task is idle. It must not feed side messages into the main chat without an explicit user action. Label it as a branch; the history snapshot is taken when created. Import the exact copied ECHO context under the branch's own stable source IDs so later parent activity cannot bypass that snapshot; preserve the actual context limit and permission settings, and support opening the branch as a full chat.

## Integration contracts

Root integration owns lib.rs, gateway routing, tool registration, native commands and side-chat branch creation. Parallel contributors own scheduler modules and Jobs UI, file-ledger modules and Spaces/change UI, or existing UI layout respectively. No contributor edits shared integration files.

- Scheduler: `BackgroundManager::new(root: PathBuf) -> Result<Arc<_>, String>`; async `execute(core, app, args, context: Option<BackgroundContext>) -> Result<Value, String>`; `tool_spec() -> Value`; manager `attach_app(core, app)`, `shutdown().await`, `emit(event: Value) -> Result<Value, String>`. Context carries the exact ChatSendRequest, model profile and workspace. Manager is `core.background`.
- Agent wake adapter: root supplies `resume_scheduled_job(core, app, request, run_id, evidence) -> boxed Send future<Result<ChatSendResult,String>>`, using the actual chat harness and system evidence entries.
- Background native API: `background_command(args)`; frontend `backgroundCommand(args)` in background-jobs.ts. List/status return `{tasks, runs, webhook:{url,token}, execution:{appMustBeOpen:true}}`. Jobs component `<BackgroundJobs conversationId?, onNotice />`.
- Files: `WorkspaceLedger::new(root) -> Result<Arc<_>, String>`; `begin_turn(conversation, turn, workspace) -> Result<TurnCapture,String>`; `finish_turn(capture, status) -> Result<Value,String>`; `register_outputs(conversation, job, paths) -> Result<Value,String>`; `command(args) -> Result<Value,String>`; manager is `core.files`.
- Files native API: `workspace_files(args)`; args actions `list`, `changes`, `preview`, `diff`, `index`. List returns `{files:[FileRecord], coverage:[string]}`; changes returns `{turnId, files:[FileRecord], added, removed, coverage}` for latest task. Preview returns `{name,mime,text?,dataUrl?,sha256,size}`. Diff returns `{path,before?,after?,added,removed}`.
- `FileRecord`: id, conversationId, turnId, path, change (created/modified/deleted/output), added/removed (nullable for binary), beforeHash/afterHash, timestamp, size, mime, source, snapshotAvailable.
- Frontend workspaces.ts exports `workspaceFiles(args)`, types and `FileChangesReceipt({conversationId, active, onOpen})`; SpacesView accepts `{conversationId?, onNotice, onOpenConversation?}`.
- Side chat native API: `create_side_chat(conversation_id)` -> `{conversationId,parentId,title,contextTokens,sharedWorkspace:true}`; `send_side_chat_message(request)` reuses normal chat sending. Parent history is copied once with its actual roles/content, source mapping excluded. Existing send/cancel/stream APIs serve branch turns. Scope metadata must be persisted.
- Events: `opencore-background-changed`, `opencore-file-changes`, existing `opencore-generation` and `opencore-studio-job` are used by UI refreshes.

## Verification and release

Test cron boundaries, invalid schedules, duplicate events, step intervals, durable recovery, worker failure/cancellation, and no permission escalation. Test file create/edit/delete, real line counts, snapshots after deletion, confinement, byte/traversal coverage and binary outputs. Test receipt hiding, panel switching/resize, side-chat isolation and consistent navigation. Run Rust/native builds only on GitHub Actions. Run local frontend/type tests and a browser UI preview, then Actions CI. Install the signed final release into the same directory and verify desktop and Start menu launches again. Do not claim actual model quality, unavailable inference backends or a permanent unattended service from these tests.
