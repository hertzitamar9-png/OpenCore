# OpenCore workspace and background jobs implementation plan

> **For agentic workers:** Use the approved user scope and the workspace-jobs design. Independent modules can run in parallel; root owns integration. Keep existing history and use the actual installed app for launch verification.

**Goal:** A working installed OpenCore with durable schedules/hooks/workers and a transparent unified file/browser/computer/side-chat workspace.

**Architecture:** Reuse the pinned Codex harness and studio GPU ownership. Add independent SQLite background and file ledgers and a shared right panel. Preserve existing tools, models and app data.

**Tech stack:** Rust/Tauri/Tokio/rusqlite, React/TypeScript, existing Axum gateway, Vitest, GitHub Actions.

**Spec:** docs/superpowers/specs/2026-10-06-workspace-jobs-design.md

## Global constraints

- Keep the repository private and one installed app.
- Preserve model weights, environments, conversations and source history.
- Native builds and Rust tests run only on GitHub Actions.
- No inference model stays loaded just to poll a timer or external process.
- Background permissions inherit the exact originating permission mode.
- Loopback webhooks require a random bearer token and stable event IDs.
- Never advertise coverage outside the snapshot traversal/byte limits.

## Review focus

- Duplicate webhook delivery must not execute a task twice.
- A trigger arriving during active user inference must queue without stopping that inference.
- Deleting a file must preserve its last before snapshot for review.
- A symlink inside a workspace must not let previews read outside it.
- A side chat must not duplicate external harness thread mappings or write into the main timeline.

## Task 1: Installed launch recovery

- [x] Inspect actual shortcuts, version, native windows and Windows errors.
- [x] Verify v0.2.110 release/installer hash and back up executable/chat database.
- [x] Upgrade the existing directory without deleting app data.
- [x] Observe desktop launch and Start menu close/reopen; record startup readiness.
- [ ] Repeat the same launch verification after the final feature release.

## Task 2: Durable schedules, events and workers

Files: scheduler.rs, scheduler_cron.rs, scheduler_worker.rs, background-jobs.ts, BackgroundJobs.tsx and focused tests.

- [ ] Tests for once/interval/cron next due, UTC/local boundaries and rejection.
- [ ] SQLite atomic task/run/event claims; missed runs coalesce; restart marks live workers interrupted.
- [ ] Named event filters and every-N-step triggers with exact-once event admission.
- [ ] Hidden owned workers, logs, real exit codes, cancellation and shutdown.
- [ ] Prompt wake through root adapter; GPU idle queue and runtime release.
- [ ] Jobs UI with schedules, scripts, triggers, histories, webhook endpoint/example and execution conditions.

## Task 3: File history, snapshots, diff counts and Spaces

Files: workspace_ledger.rs and supporting focused modules/tests, workspaces.ts, SpacesView.tsx, FileChangesReceipt.tsx.

- [ ] Tests for actual create/edit/delete counts, binary output records and immutable before/after previews.
- [ ] Confined snapshot traversal, no symlinks/dependency trees, limits and visible coverage.
- [ ] SQLite latest task/list/search/diff/preview commands and durable content-addressed snapshots.
- [ ] Spaces gallery/table with chat origins, versions, previews and snapshots.
- [ ] Latest response change receipt hides during active work and opens files in the right panel.

## Task 4: Unified right panel and section layout

Files: App.tsx, AssistantConversation.tsx, NativeBrowserPanel.tsx, DesktopPanel.tsx, WorkspacePanel.tsx and focused styling/tests.

- [ ] Common compact rail/header actions in every view; Jobs and Spaces routes.
- [ ] Resizable Files/Browser/Computer/Side chat panel shared across sections.
- [ ] Embedded browser and computer layout; hide native browser on tab change.
- [ ] File links/previews route into Files without competing dialogs.
- [ ] Side-chat controls use branch creation, existing send/cancel/stream, actual parent context ceiling.
- [ ] UI tests for opening/switching/closing, receipts and no overlap at narrow desktop width.

## Task 5: Integration and installed delivery

Root files: lib.rs, codex_harness.rs, gateway.rs, store.rs as necessary, focused side-chat tests.

- [ ] Register managers/native APIs/tools and authenticated loopback webhook route.
- [ ] Capture before/after chat task changes and studio outputs; emit trigger events after real completion.
- [ ] Copy main history/project into a separate persisted side-chat branch, share workspace and ECHO parent references.
- [ ] Wake tasks using actual harness/system evidence; wait for existing GPU work, retain approval mode and release inference.
- [ ] Local frontend suite/typecheck and browser preview; independent whole-branch review.
- [ ] Actions native CI/build; private main merge, signed release installation and actual shortcut verification.
- [ ] Report verified launch evidence, implemented capabilities and material limitations.
