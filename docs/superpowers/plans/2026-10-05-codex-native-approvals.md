# Codex-Native Approval Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace OpenCore's separate generic tool-permission RPC and four-mode slider with the approval requests, available choices, and permission state emitted by the pinned Codex app-server.

**Architecture:** The Rust app-server client correlates approval server requests and MCP elicitation with the active conversation. OpenCore renders the precise command, file diff, network ask, or MCP action and resolves only options present in that request; the old permission RPC is removed after end-to-end parity passes.

**Tech Stack:** Rust/Tokio JSON-RPC, Tauri events/commands, React/TypeScript, app-server schema snapshot, Node tests and Vitest.

**Spec:** `docs/superpowers/specs/2026-10-05-opencore-codex-app-server-design.md`; depends on `2026-10-05-codex-app-server-transport.md`.

## Global Constraints

- Do not show or execute an approval choice the pinned server did not offer.
- The default is workspace-scoped write access with on-request escalation and user review.
- Existing `allow-all` settings must never become broader grants after migration.
- Keep sandbox policy separate from approval policy; labels must match effective server settings.
- Keep approval payloads scoped to the originating connection, thread, workspace, and request ID.
- Build installers only through GitHub Actions; local unit tests are allowed.

## Review Focus

- Late, malformed, or cross-thread answer: prove the request is rejected and the operation remains denied in Rust tests.
- Declined/canceled request: prove no MCP tool or command is executed in Node and Rust tests.
- File approval: prove the displayed diff and target path are the exact server-provided change in UI tests.
- Permission migration: prove legacy broad flags cannot silently grant write, network, or full-disk access.
- App-server disconnect while a prompt is open: prove the prompt closes safely and pending state is released.

---

### Task 1: Normalize native approval requests and responses

**Files:**
- Create: `src-tauri/src/codex_approval.rs`
- Modify: `src-tauri/src/codex_app_server.rs`
- Modify: `src-tauri/src/models.rs`
- Test: `src-tauri/src/codex_approval.rs` unit tests

**Interfaces:**
- Produces: serializable `CodexApprovalRequest { request_id, connection_id, thread_id, conversation_id, kind, action, cwd, paths, diff, reason, available_decisions }` and `CodexApprovalDecision { decision, permission_amendments }`.
- Consumes: only the pinned server-request shapes from Task 1's schema; stores pending request state on the Rust side.

- [ ] Add failing tests named `command_approval_preserves_server_choices_and_requested_permissions`, `file_approval_carries_the_exact_diff_and_paths`, `network_escalation_is_bound_to_the_requested_origin`, `unknown_choice_is_rejected`, `stale_or_cross_thread_approval_is_denied`, and `cancelled_server_request_releases_pending_state`.
- [ ] Run `cargo test --manifest-path src-tauri/Cargo.toml codex_approval::tests -- --nocapture`; confirm all six behaviors fail before implementation.
- [ ] Parse the pinned command, file-change, and network request variants; validate offered decisions, scope identifiers, and request lifetime before responding through `CodexAppServer::respond`.
- [ ] Run the focused Rust suite and `cargo test --manifest-path src-tauri/Cargo.toml --lib`; require malformed and stale inputs to fail closed.
- [ ] Commit as `feat: model Codex approval requests natively`.

### Task 2: Add request-specific approval UI

**Files:**
- Modify: `src/AssistantConversation.tsx`
- Modify: `src/api.ts`
- Modify: `src/types.ts`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/tauri.conf.json` (Tauri command registration only if required)
- Modify: `src/App.test.tsx`
- Modify: `src/styles.css`

**Interfaces:**
- Consumes: `CodexApprovalRequest` from Task 1, delivered as `opencore-codex-approval-request`.
- Produces: `resolve_codex_approval(requestId, decision, amendments)`; this command returns an error for absent, stale, or unavailable choices.

- [ ] Add failing UI tests named `renders the exact command and only server-offered approval choices`, `renders file-change diff before asking for approval`, `shows network origin and requested scope before escalation`, `closes a pending approval when its app-server thread ends`, and `app-server disconnect clears prompt and denies unresolved request`.
- [ ] Run `npm exec vitest run src/App.test.tsx`; confirm the native approval requests cannot be rendered or answered.
- [ ] Replace the generic “Approve this tool?” dialog with typed request cards in the active conversation. Keep keyboard focus, accessible labels, full diff review, explicit decline/cancel, and a clear final server result.
- [ ] Run `npm exec vitest run src/App.test.tsx`; all request variants and stale-answer cases must pass.
- [ ] Commit as `feat: render Codex-native approval requests`.

### Task 3: Migrate permission profiles and retire custom RPC

**Files:**
- Modify: `src-tauri/resources/codex/codex-config.mjs`
- Modify: `src-tauri/resources/codex/mcp-protocol.mjs`
- Modify: `src-tauri/resources/codex/tool-relay.mjs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/src/models.rs`
- Modify: `src/AssistantConversation.tsx`
- Modify: `src/types.ts`
- Modify: `tests/codex-config.test.mjs`
- Modify: `tests/codex-mcp.test.mjs`
- Modify: `src/App.test.tsx`

**Interfaces:**
- Consumes: native approval UI and JSON-RPC response path from Tasks 1–2.
- Produces: schema-backed permission profiles `workspace-review`, `read-only`, `auto-review`, and `full-access`; the last stays explicit and warned.

- [ ] Add failing tests named `MCP call waits for app-server elicitation and executes only after acceptance`, `MCP decline never reaches the tool dispatcher`, `legacy allow-all setting migrates to workspace-review`, and `permission profile labels match effective sandbox and approval policy`.
- [ ] Run `node --test tests/codex-config.test.mjs tests/codex-mcp.test.mjs` and `npm exec vitest run src/App.test.tsx`; confirm old custom approval behavior fails these tests.
- [ ] Route OpenCore MCP elicitation through app-server and the same typed approval state; change the MCP approval configuration to prompt mode supported by the pinned protocol. Remove `pending_approvals`, `ask_tool_approval`, `resolve_tool_approval`, the frontend `resolve_tool_approval` call, and the `permission` RPC only after command, file, network, and MCP integration gates all pass.
- [ ] Migrate stored old modes transactionally: `allow-all` becomes `workspace-review`, `allow-chat` becomes `workspace-review`, read-only stays read-only, and no old value silently enables full access. Apply the same safe mapping to pending queued turns.
- [ ] Run focused Node/React/Rust tests, `npm test`, and `cargo test --manifest-path src-tauri/Cargo.toml --lib`; Actions must also pass the packaged approval integration gate before installer packaging.
- [ ] Commit as `feat: replace custom tool permissions with Codex approvals`.
