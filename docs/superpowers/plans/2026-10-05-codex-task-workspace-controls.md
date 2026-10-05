# Codex Task and Workspace Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the stable Codex app-server thread, turn, queue, review, skills, and MCP workflows inside OpenCore's existing chat/project UI.

**Architecture:** OpenCore remains the workspace and project owner and calls only methods present in the pinned schema. Codex owns thread/turn/queue semantics and emits the event stream; host-side worktree creation and cleanup remain OpenCore project operations.

**Tech Stack:** Rust/Tokio app-server client, SQLite, Tauri, React/TypeScript, Vitest and Rust tests.

**Spec:** `docs/superpowers/specs/2026-10-05-opencore-codex-app-server-design.md`; depends on `2026-10-05-codex-app-server-transport.md` and `2026-10-05-codex-native-approvals.md`.

## Global Constraints

- Expose only stable methods present in the pinned app-server protocol; experimental methods remain disabled.
- Preserve OpenCore project paths and never imply app-server provides worktree lifecycle APIs.
- Keep Codex thread IDs mapped to OpenCore conversation/project/provider identity.
- Preserve the existing OpenCore conversation and ECHO archive as the product history source.
- Enforce workspace trust and project skill settings on create, resume, and fork.
- Build installers only in GitHub Actions; local unit tests are allowed.

## Review Focus

- Archived or missing thread: show a recoverable state without erasing OpenCore history in Rust tests.
- Queued input during cancellation or process restart: prove order and exactly-once persistence in store tests.
- Fork into a different workspace/provider: prove scope does not silently cross in the UI/controller tests.
- Skill/MCP catalog unavailable: preserve conversation controls and report the missing capability without blocking text chat.
- Unsupported experimental method: prove the UI hides/disables it in capability tests.

---

### Task 1: Add typed controls over the pinned app-server protocol

**Files:**
- Create: `src-tauri/src/codex_controls.rs`
- Modify: `src-tauri/src/codex_app_server.rs`
- Modify: `src-tauri/src/store.rs`
- Test: `src-tauri/src/codex_controls.rs` unit tests

**Interfaces:**
- Produces: `list_threads`, `read_thread`, `fork_thread`, `archive_thread`, `queue_turn`, `steer_turn`, `interrupt_turn`, `start_review`, `list_skills`, `list_mcp_servers`, and `set_skill_enabled` wrappers that validate methods/parameters against the pinned schema.
- Consumes: `CodexAppServer::request`, durable thread map, and server capability/version record from the transport plan.

- [ ] Add failing tests named `thread_controls_send_pinned_method_and_validate_thread_scope`, `archived_thread_error_preserves_opencore_history`, `queued_turn_order_survives_restart_and_starts_once`, `fork_copies_provider_and_workspace_only_when_explicitly_requested`, `review_uses_requested_base_or_uncommitted_scope`, and `unsupported_method_returns_capability_unavailable`.
- [ ] Run `cargo test --manifest-path src-tauri/Cargo.toml codex_controls::tests -- --nocapture`; confirm wrappers do not exist yet.
- [ ] Implement the control layer using exact request/response types from the pinned schema. Keep queue ordering and pending state transactional in SQLite; reject a thread ID whose stored workspace/provider mapping does not match the active operation.
- [ ] Run the focused Rust tests and `cargo test --manifest-path src-tauri/Cargo.toml --lib`; require duplicate queue starts and cross-scope fork requests to fail.
- [ ] Commit as `feat: add typed Codex thread and turn controls`.

### Task 2: Surface thread lifecycle and active-turn controls

**Files:**
- Modify: `src/api.ts`
- Modify: `src/types.ts`
- Modify: `src/AssistantConversation.tsx`
- Modify: `src/App.tsx`
- Modify: `src/App.test.tsx`
- Modify: `src-tauri/src/lib.rs`

**Interfaces:**
- Consumes: the typed Rust commands from Task 1.
- Produces: accessible UI actions for list/read/fork/archive, queue/reorder/start, steer, and interrupt; each carries the active OpenCore conversation ID and app-server thread mapping.

- [ ] Add failing tests named `composer can queue reorder and start follow-up input`, `active turn can be steered or interrupted and reports server terminal state`, `fork creates a distinct OpenCore conversation linked to the Codex source thread`, and `archive never removes OpenCore conversation or ECHO history`.
- [ ] Run `npm exec vitest run src/App.test.tsx`; confirm lifecycle/turn controls are absent.
- [ ] Add thread/turn actions to existing conversation controls and map server notifications into the existing timeline without creating a second message stream. Keep command output, file changes, usage, and terminal state sourced from the server event.
- [ ] Run `npm exec vitest run src/App.test.tsx`; all stale-thread and cancellation UI tests must pass.
- [ ] Commit as `feat: surface Codex task and thread controls`.

### Task 3: Add review, skill, MCP, and workspace operations

**Files:**
- Modify: `src-tauri/src/codex_controls.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/src/store.rs`
- Modify: `src/App.tsx`
- Modify: `src/AssistantConversation.tsx`
- Modify: `src/App.test.tsx`

**Interfaces:**
- Consumes: typed controls and conversation UI from Tasks 1–2.
- Produces: branch/commit/uncommitted review actions; skill and MCP discovery/settings; OpenCore-owned worktree operations that pass a validated `cwd` to `thread/start` and `thread/fork`.

- [ ] Add failing tests named `review request shows its exact base and terminal review result`, `skill discovery respects projectSkillsEnabled`, `MCP server controls reflect app-server status and do not autoapprove tools`, and `worktree path is created and cleaned by OpenCore host rather than app-server`.
- [ ] Run `cargo test --manifest-path src-tauri/Cargo.toml codex_controls::tests -- --nocapture` and `npm exec vitest run src/App.test.tsx`; confirm those surfaces are unimplemented.
- [ ] Implement only stable protocol capabilities. Keep plugin install/uninstall and other development-only endpoints unavailable. Delegate directory/worktree lifecycle to OpenCore's project service and treat its paths as untrusted until canonicalized and scope-checked.
- [ ] Run `npm test` and `cargo test --manifest-path src-tauri/Cargo.toml --lib`; verify skill disablement and project isolation with temporary workspaces.
- [ ] Commit as `feat: add Codex review and workspace integrations`.
