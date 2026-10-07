# Platform Completion Implementation Plan

> **For agentic workers:** Use superpowers:dispatching-parallel-agents for independent owned modules; the root agent integrates and verifies the release. The user has explicitly authorized immediate execution.

**Goal:** Integrate unpublished work and complete the actionable 0.2.128 audit gaps in a published, installed, verified release.

**Architecture:** Reuse the existing Codex app-server, Tauri core, scheduler, immutable file ledger and single-instance process. Add managed setup receipts and a tray background lifecycle. Refresh side-chat parent context through real model inputs.

**Tech Stack:** React, TypeScript, Rust, Tauri 2, SQLite, Python and GitHub Actions.

**Spec:** ../specs/2026-10-07-platform-completion-design.md

## Global Constraints

- Native compilation/tests and installer/app builds run only in GitHub Actions.
- Preserve appearance, stored data, weights, existing settings and approval policies.
- Port useful older changes into current code; do not blindly merge old snapshots.
- Keep modules isolated across workers; root owns shared registration and app integration.
- Never treat model metadata, dependency checks or screenshots as successful inference.

## Review Focus

- Revocation between UI admission and native dispatch must prevent the action.
- Equal precision packages may contain different model components and must stay selectable.
- Failed/cancelled side-chat admission must retain undelivered parent context.
- Closing the main window and updater restart must not duplicate jobs or leave workers orphaned.
- Low disk space or concurrent source changes must not create corrupt or falsely complete snapshots.

## Tasks

### 1. Computer and browser access

**Files:** computer_access.rs, windows_control.rs, desktop_helper.rs, browser_bridge.rs, Chrome extension, AutomationSettings and targeted tests. Root integrates lib.rs/api.ts/App.tsx.

- [x] Test persisted executable identity, stale revision/revocation, browser disconnect and stale/covered DOM references.
- [x] Port the compatible 9a8e3e3 functionality while retaining current border/focus behavior.
- [x] Run interpreted tests locally and native tests in CI; review real dispatch integration.

### 2. Model controls, context and unpublished fixes

**Files:** ModelLibrary, StudioJobs, AssetsStudio, SideChat, store side-chat APIs, chat_stream.rs, connector_config.rs and tests.

- [x] Test memory-mode changes and same-precision package identity; implement distinct selectors.
- [x] Test parent-context deltas, nested branches, preserved independent messages and failed admissions.
- [x] Add refresh_side_chat_context, side_chat_context_update and delivery acknowledgement; root injects into Codex turn inputs.
- [x] Port streaming preview and connector post-write validation with regression cases.

### 3. Managed setup and speech

**Files:** runtime_setup.rs, resources/runtime-setup, StudioModelSetup, testing_labs.rs, speech.rs and speech workers/tests.

- [x] Test supported recipes, persisted status, cancellation, invalid paths and dependency failures.
- [x] Implement managed environment discovery/setup and explicit model inference qualification receipts.
- [x] Connect setup's managed Python path to studio execution; supply official VM/device provisioning paths.
- [x] Qualify both Phonon precisions and measure startup/wake; retain saved preference. CPU cache qualification compares all 723 tensor keys; GPU/VM/device qualification remains distinct.

### 4. Persistent background lifecycle

**Files:** background_host.rs, BackgroundJobs, lib.rs, tauri.conf.json and tests.

- [x] Test close/quit/update decisions, hidden CLI startup and safe quoted login registration.
- [x] Add persisted configuration/status, tray Open/Quit, hidden startup and close interception.
- [x] Keep single-instance activation and update shutdown correct; expose autosaved controls in Jobs.
- [ ] Verify an actual worker completes after the installed app window closes.

### 5. Durable complete output history

**Files:** workspace_ledger.rs/tests, SpacesView/tests, workspaces.ts and lib.rs.

- [x] Test large output survives original deletion, immutable hashing, disk/source failure and paged listing.
- [x] Stream-copy generated outputs and backfill surviving recorded outputs once without rewriting history.
- [x] Expose pagination/coverage and retain existing external browser/file preview behavior.

### 6. Release and installed verification

- [x] Review all changes against the spec and audit every old local diff for useful missing behavior.
- [x] Run frontend/interpreted tests and noEmit typecheck; push and run GitHub native/build gates. Final review regressions require the latest native gate before merge.
- [ ] Fix concrete CI failures, attach PR, merge approved passing release and verify published installer.
- [ ] Update installed app; verify data preservation and the real user-facing paths.
- [ ] Report measured outcomes and any remaining external prerequisites accurately.
