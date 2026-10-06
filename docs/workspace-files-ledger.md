# Workspace file history

The file ledger is separate from conversation storage and model weights. `WorkspaceLedger::new(app_data_root)` creates `workspace-files/ledger.sqlite3` and immutable SHA-256 objects under `workspace-files/objects`. Authored files are captured before and after a task, including failed or cancelled tasks. A persisted before capture lets reopening recover interrupted partial changes.

## Native integration

- `begin_turn(conversation: &str, turn: &str, workspace: impl AsRef<Path>) -> Result<TurnCapture, String>`
- `finish_turn(capture: TurnCapture, status: &str) -> Result<Value, String>`
- `register_outputs(conversation: &str, job: &str, paths: &[PathBuf]) -> Result<Value, String>`
- `command(args: Value) -> Result<Value, String>`

Use the exact submission ID for the task and any live outputs that belong to it. Latest changes merge the workspace capture with live output records for that conversation and submission ID. The task's final status is retained. Outputs from an older task cannot displace a newer task's receipt. Old observations indexed from evidence are excluded from task receipts. Emit `opencore-file-changes` after finishing or registering changes.

## Commands

| Action | Arguments | Result |
| --- | --- | --- |
| `list` | `conversationId?`, `search?`, `limit?` | `{files, coverage}` across versions and chats |
| `changes` | `conversationId`, `turnId?` | `{turnId, files, added, removed, coverage, status, timestamp}` |
| `preview` | `id`, `version?: before/after` | `{name, mime, text?, dataUrl?, sha256, size, snapshotAvailable}` |
| `diff` | `id` | `{path, before?, after?, added, removed, binary, coverage}` |
| `index` | `workspace`, `conversationId?`, `turnId?` | Current versions with indexed provenance and no invented change counts |
| `index` | `paths` or `entries`, `conversationId?`, `jobId`, `source`, `live?` | Existing or new output observations |

Each output entry is `{path: absolutePhysicalPath, name?: originalFilename, mime?: originalMime}`. `source` is `published`, `studio`, or `indexed`. Original names and MIME types allow published UUID `.bin` objects to preview correctly. Old evidence defaults to indexed observations; set `live: true` only for actual newly completed outputs. Indexing never invents a before version or historical line changes. The root native command can intercept `index` without a workspace or output list to enumerate its persisted studio and publication evidence; the ledger itself has no access to those stores.

Record IDs identify database rows. Caller-supplied preview paths and hashes are unsupported. Preview verifies the recorded hash before returning bytes. An unavailable saved snapshot is never replaced with current source content. HTML previews use an iframe with an empty sandbox and `referrerPolicy="no-referrer"`.

## Coverage and storage

Captures exclude dependency/build/VCS directories, symlinks, Windows reparse points, and known model weight formats. Default limits are 50,000 entries, 48 directory levels, 4 MiB per snapshot, and 64 MiB of captured file content. File contents are copied once per distinct hash. Capture omissions are reported in `coverage`. An omitted or unreadable after version is not treated as deletion. A path is classified as created only when its absence is proved by the before capture.

UTF-8 line counts use exact shortest edit distance, retaining newline differences. The diff has a 200,000-line traversal limit and a shared 20 million-operation budget per task. When exact counts exceed these limits, counts remain unavailable and the reason appears in coverage. Binary outputs have null counts. The UI displays totals for counted files alongside coverage.

Studio/published outputs up to 4 MiB can have saved snapshots. Larger outputs use confined source references with full streamed hashes up to a 256 MiB read budget; model weights and larger outputs are reference-only with unavailable hashes. Source references are previewed only within the 12 MiB preview limit and only if current bytes still match the recorded hash. They are labelled separately from saved snapshots.

## Frontend

`workspaces.ts` provides the `workspace_files` native API and event subscription without polling inference. `SpacesView` searches saved history and routes files and known originating chats to callbacks. `FileSnapshotView` provides before/after/diff views. `FileChangesReceipt` hides the prior result while a task is active and suppresses stale fetches when that task ends.

Focused frontend verification covers receipt hiding, failed partial files, binary output counts, stale requests, deleted before previews, sandboxed HTML, and Spaces navigation/search. Native tests cover edits, deletion and restart snapshots, Unicode, binary outputs, coverage limits, confinement, corrupted objects, interrupted recovery, publication metadata, live output merging, and source reference verification. Rust/native execution is reserved for GitHub Actions.
