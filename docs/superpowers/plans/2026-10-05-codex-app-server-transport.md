# Codex App-Server Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the SDK wrapper with the pinned official Codex app-server stdio protocol while keeping OpenCore's local Responses model, ECHO, MCP tools, and existing conversation timeline working.

**Architecture:** Package one matching Codex CLI/app-server release and its protocol schema; let Rust own the child process, JSON-RPC correlation, lifecycle, persistence, and Tauri event mapping. Keep one app-server process per isolated OpenCore conversation/provider/workspace mapping across multiple turns, so per-conversation gateway headers and credentials remain scoped while the local model itself is unloaded whenever its lease is released. Preserve source conversations and resume saved app-server thread IDs; import old SDK rollouts read-only if the pinned runtime cannot resume them.

**Tech Stack:** Rust/Tokio, Node.js package preparation, JSON-RPC 2.0 over private stdio, SQLite via rusqlite, Tauri events, Vitest/Node tests, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-05-opencore-codex-app-server-design.md`

## Global Constraints

- OpenCore local Responses inference remains the default provider and does not call OpenAI inference.
- App-server listens on private stdio; do not expose an unauthenticated TCP endpoint.
- Bundle the app-server executable and protocol schema from the same pinned Codex release; reject mismatches before starting a turn.
- Keep conversation/project/provider/thread mappings transactional and preserve existing source history.
- Do not put credentials, model weights, ECHO archives, benchmark data, or machine paths in Git or installers.
- Build the desktop installer only in GitHub Actions; local unit tests and static checks are allowed.

## Review Focus

- Server exits during initialize or mid-turn: prove precise error reporting, child cleanup, and resumability in `src-tauri/src/codex_app_server.rs` tests.
- JSON-RPC responses arrive out of order or malformed: prove correlation and fail-closed behavior in the client tests.
- Existing SDK thread data is not readable by the chosen app-server: prove migration leaves source files and timeline intact in `store.rs` tests.
- A provider/schema version changes while persisted threads exist: prove the app refuses incompatible work without rewriting thread history.
- User cancellation races with process exit: prove exactly one terminal result and no orphaned child in Rust cancellation tests.

---

### Task 1: Pin and verify the bundled app-server runtime

**Files:**
- Modify: `src-tauri/resources/codex/package.json`
- Modify: `src-tauri/resources/codex/package-lock.json`
- Modify: `scripts/prepare-codex-harness.mjs`
- Modify: `scripts/codex-package-version.mjs`
- Create: `src-tauri/resources/codex/protocol/app-server.schema.json`
- Create: `scripts/test-codex-app-server-schema.mjs`
- Modify: `.github/workflows/build.yml`
- Test: `tests/codex-package-version.test.mjs`

**Interfaces:**
- Produces: `codexRuntimeManifest()` returning the exact CLI version, platform binary package/version, and app-server schema revision/hash used by runtime startup.
- Consumes: the existing platform-suffixed package-version helper and the matching upstream release source.

- [ ] Add failing tests named `app-server executable and checked-in schema must match one pinned release` and `unsupported app-server version fails the read-only startup probe`.
- [ ] Run `node --test tests/codex-package-version.test.mjs scripts/test-codex-app-server-schema.mjs`; confirm both new assertions fail before implementation.
- [ ] Replace the SDK-only package pin with the pinned Codex CLI package/runtime, update preparation to verify CLI, native platform package, and schema metadata as one version tuple, and add a read-only stdio initialize probe that makes no model request.
- [ ] Run `node scripts/prepare-codex-harness.mjs` and `node --test tests/codex-package-version.test.mjs scripts/test-codex-app-server-schema.mjs`; both must pass and a deliberate schema hash mismatch must be rejected.
- [ ] Add a GitHub Actions step after runtime preparation that runs the probe against the bundled Windows binary; keep installer builds in Actions only.
- [ ] Commit as `build: pin Codex app-server runtime and schema`.

### Task 2: Implement the Rust JSON-RPC process client

**Files:**
- Create: `src-tauri/src/codex_app_server.rs`
- Modify: `src-tauri/src/lib.rs`
- Test: `src-tauri/src/codex_app_server.rs` unit tests

**Interfaces:**
- Produces: `CodexAppServerPool::get_or_start(key, config) -> Result<Arc<CodexAppServer>, AppServerError>`, `CodexAppServer::request(method, params) -> Result<Value, AppServerError>`, `next_event() -> Result<Option<ServerMessage>, AppServerError>`, `respond(id, result)`, `interrupt(thread_id)`, and `shutdown()`.
- `key` is the stable tuple of OpenCore conversation ID, canonical workspace identity, provider ID, and pinned runtime/schema hash. The pool retains the child for subsequent turns, caps live children at four with least-recently-used eviction of idle servers, and closes a child on conversation archive, provider replacement, eviction, or app shutdown. Restarting an evicted app-server resumes its saved thread and never removes its history.
- `ServerMessage` distinguishes JSON-RPC response, server request, notification, process exit, and protocol error. Request IDs are scoped to one child connection.
- Consumes: Task 1's verified executable/schema paths; no network listener.

- [ ] Add failing Rust tests named `json_rpc_responses_correlate_when_replies_arrive_out_of_order`, `server_requests_are_not_mistaken_for_notifications`, `malformed_or_unknown_messages_fail_closed`, `interrupt_cancels_pending_requests_and_reaps_child`, `process_exit_mid_request_returns_recoverable_error_without_losing_thread_id`, `schema_version_mismatch_prevents_initialize`, `conversation_pool_reuses_only_the_matching_scoped_server`, and `pool_caps_live_children_at_four_and_evicts_only_idle_server`.
- [ ] Run `cargo test --manifest-path src-tauri/Cargo.toml codex_app_server::tests -- --nocapture`; confirm the new client behavior is absent.
- [ ] Implement the async stdio writer, bounded line reader, request-ID map, concurrent response routing, server-request delivery, protocol negotiation, child ownership, diagnostics redaction, cancellation, deterministic shutdown, and per-conversation pool lifecycle. The process may persist between turns, but the configured local model process/weights must still be released by the lease layer.
- [ ] Run the focused Rust tests and `cargo test --manifest-path src-tauri/Cargo.toml --lib`; require all to pass.
- [ ] Commit as `feat: add native Codex app-server JSON-RPC client`.

### Task 3: Route OpenCore turns and preserve thread/history identity

**Files:**
- Modify: `src-tauri/src/codex_harness.rs`
- Modify: `src-tauri/src/store.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/resources/codex/codex-config.mjs`
- Modify: `src-tauri/resources/codex/codex-events.mjs` or its Rust replacement
- Modify: `src-tauri/resources/codex/runner.mjs` (remove SDK turn ownership after parity passes)
- Test: `tests/codex-events.test.mjs`
- Test: `src-tauri/src/store.rs` and `src-tauri/src/codex_harness.rs` unit tests

**Interfaces:**
- Consumes: `CodexAppServer` from Task 2.
- Produces: a durable mapping keyed by OpenCore conversation ID and workspace identity containing app-server thread ID, provider ID, model ID, schema/runtime version, and migration state.
- OpenCore remains the sole writer of its user-visible timeline; app-server rollout events are linked evidence, not a second duplicate chat transcript.

- [ ] Add failing tests named `local OpenCore Responses provider streams app-server turns into one OpenCore timeline`, `app-server local turns preserve automatic ECHO recall and conversation scope`, `saved app-server thread resumes after app restart`, `legacy SDK rollout remains readable when app-server import is unsupported`, and `repeated migration preserves original files and timeline IDs`.
- [ ] Run `npm exec vitest run src/App.test.tsx` and `cargo test --manifest-path src-tauri/Cargo.toml store::tests -- --nocapture`; confirm migration and stream assertions fail.
- [ ] Add transactional thread/provider/workspace mappings, event-to-timeline projection for text/reasoning/tool/file/usage/terminal events, local-provider configuration, stream persistence, and explicit legacy-history linkage. Never overwrite/delete SDK-era rollout files.
- [ ] Start and resume threads through app-server `thread/start` / `thread/resume` from the pinned schema; fail with a user-readable recovery state on unsupported history, version mismatch, or process crash.
- [ ] Run focused tests, `npm test`, and `cargo test --manifest-path src-tauri/Cargo.toml --lib`; require no duplicate messages and no changed legacy source hashes.
- [ ] Commit as `feat: route OpenCore turns through Codex app-server`.

### Task 4: Gate the real bundled path in GitHub Actions

**Files:**
- Modify: `scripts/test-codex-agent-runtime.mjs`
- Modify: `.github/workflows/build.yml`
- Test: `tests/codex-events.test.mjs`, `tests/codex-package-version.test.mjs`, Rust app-server tests

**Interfaces:**
- Consumes: the pinned runtime, Rust process client, and mapped OpenCore event surface from Tasks 1–3.
- Produces: one CI gate that starts the exact packaged app-server with a fake local Responses endpoint and the real packaged OpenCore MCP server.

- [ ] Add a failing integration assertion named `packaged app-server streams, calls one MCP tool, cancels, and resumes the same local thread without restarting its scoped server`.
- [ ] Run `node scripts/test-codex-agent-runtime.mjs`; require the test to fail against the current SDK bridge before wiring the app-server path.
- [ ] Exercise initialize, local tool-free response, MCP tool call, cancellation, resume, version guard, and child cleanup without contacting hosted inference.
- [ ] In Actions run `npm test`, `cargo test --lib`, the app-server integration gate, and the Windows installer build; do not run `npm run desktop:build` locally.
- [ ] Commit as `test: gate the packaged Codex app-server path`.
