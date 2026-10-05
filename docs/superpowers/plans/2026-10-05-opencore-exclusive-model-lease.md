# OpenCore Exclusive Local-Model Lease Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce one active local model owner across Codex parent turns, subagents/reviewers, speech, music, image, 3D/animation, runtime maintenance, and background-job handoff.

**Architecture:** Add one fair lease coordinator shared by local inference and GPU-backed job entry points. A Codex local turn retains a lease identity while its app-server tool loop runs, and local provider requests under that identity are serialized; a studio handoff completes/cancels the turn and releases its lease before studio weights are loaded. Hosted OpenAI requests do not acquire the local model lease.

**Tech Stack:** Rust/Tokio semaphore and RAII leases, Axum gateway, Tauri cancellation, SQLite studio queue, Tokio tests.

**Spec:** `docs/superpowers/specs/2026-10-05-opencore-codex-app-server-design.md`; depends on transport and approval plans. Provider classification comes from `2026-10-05-opencore-model-providers.md`.

## Global Constraints

- At most one local model lease is active; distinct local requests are serialized under that lease.
- Primary turn, local subagent/reviewer inference, speech, studio jobs, updates, and model lifecycle changes use the same coordinator.
- The lease is released on completion, cancellation, crash, failed preflight, and app shutdown through RAII/cancellation cleanup.
- A studio handoff must not keep the chat model loaded while a long-running generation executes or while waiting for its completion.
- Do not stop an unrelated/external model process; preserve current preflight ownership checks.
- Build installers only through GitHub Actions; local unit tests are allowed.

## Review Focus

- Handoff races with a still-running chat/subagent request: prove the studio cannot acquire early and no stale local request can execute after release.
- Two local studio/speech jobs arrive together: prove FIFO admission and one loaded model.
- Cancellation or panic occurs while holding a lease: prove RAII releases it without unlocking another owner's lease.
- Hosted OpenAI conversation overlaps a local job: prove hosted calls do not load or reserve local weights.
- Runtime update/model switch overlaps inference: prove maintenance waits or returns a scoped busy result without killing unrelated processes.

---

### Task 1: Implement the shared lease coordinator

**Files:**
- Create: `src-tauri/src/model_lease.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/src/gateway.rs`
- Modify: `src-tauri/src/runtime.rs`
- Test: `src-tauri/src/model_lease.rs` unit tests

**Interfaces:**
- Produces: `ModelLeaseCoordinator::acquire(owner: LeaseOwner) -> Result<ModelLease, LeaseError>`, `ModelLease::id()`, `ModelLease::serialize_inference()`, `ModelLease::release()`, and a read-only `snapshot()` for diagnostics.
- `LeaseOwner` variants: `CodexTurn { conversation_id, turn_id }`, `StudioJob { job_id, model_id }`, `Speech { session_id, model_id }`, and `Maintenance { operation_id }`.
- Consumes: shared `AppCore` and gateway state; local turn lease identity is passed only in private provider headers.

- [ ] Add failing tests named `different_owners_never_hold_a_local_model_lease_together`, `same_turn_subagent_requests_are_serialized`, `lease_drop_cancel_and_panic_release_exactly_once`, `stale_lease_identity_cannot_infer_after_handoff`, and `lease_waiters_are_admitted_fairly`.
- [ ] Run `cargo test --manifest-path src-tauri/Cargo.toml model_lease::tests -- --nocapture`; confirm the coordinator is absent.
- [ ] Implement an owner-aware RAII lease, FIFO waiting, per-owner inference serialization, cancellation-aware acquire, opaque unguessable IDs, and a gateway check that rejects local inference requests with absent/stale lease identity.
- [ ] Run focused tests and `cargo test --manifest-path src-tauri/Cargo.toml --lib`; use deterministic barriers rather than sleep-based race assertions.
- [ ] Commit as `feat: add exclusive local model lease coordinator`.

### Task 2: Route Codex and local model gateway calls through the lease

**Files:**
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/src/codex_harness.rs`
- Modify: `src-tauri/src/codex_app_server.rs`
- Modify: `src-tauri/src/gateway.rs`
- Modify: `src-tauri/resources/codex/codex-config.mjs`
- Test: `src-tauri/src/gateway.rs` tests and app-server integration test

**Interfaces:**
- Consumes: coordinator from Task 1 and provider identity from the provider plan.
- Produces: lease-scoped local provider header and a `CodexTurn` lease held until the app-server turn ends; every child/reviewer inference call shares the ID but passes through the serialized inference gate.

- [ ] Add failing tests named `local Codex turn holds lease until final event or interrupt`, `local subagent and reviewer calls share and serialize under parent lease`, `OpenAI provider does not acquire local lease`, and `studio cannot start until all turn inference calls drain`.
- [ ] Run `cargo test --manifest-path src-tauri/Cargo.toml gateway::tests -- --nocapture` and `node scripts/test-codex-agent-runtime.mjs`; confirm concurrent local requests can pass without owner checks.
- [ ] Acquire the local owner lease at turn start, propagate only its opaque ID to the loopback provider, check and serialize at the inference boundary, and release it after terminal event plus drain of all child requests. For hosted provider selection, bypass local lease explicitly.
- [ ] Run focused Rust and Actions integration tests; require no request to cross a released lease boundary.
- [ ] Commit as `feat: lease local inference to Codex turns`.

### Task 3: Unify studios, speech, updates, and handoff cleanup

**Files:**
- Modify: `src-tauri/src/studio_jobs.rs`
- Modify: `src-tauri/src/speech.rs`
- Modify: `src-tauri/src/app_update.rs`
- Modify: `src-tauri/src/runtime.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `tests/claude-bridge.test.mjs` (existing continuation contract only where reusable)
- Test: relevant Rust module unit tests

**Interfaces:**
- Consumes: coordinator from Tasks 1–2.
- Produces: studio/speech/update operations that acquire the exclusive lease, release prior weights only after acquiring valid ownership, and publish an `opencore-studio://` handoff only after the Codex turn lease is released.

- [ ] Add failing tests named `queued studio waits for chat turn and unloads only OpenCore-owned local runtime`, `speech reservation blocks studio and releases after transcription`, `studio cancellation releases its lease and notifies the originating conversation once`, `background wait holds no local model lease`, and `runtime update cannot race local inference`.
- [ ] Run `cargo test --manifest-path src-tauri/Cargo.toml studio_jobs::tests -- --nocapture` and `cargo test --manifest-path src-tauri/Cargo.toml speech::tests -- --nocapture`; confirm remaining independent reservations/races.
- [ ] Replace duplicate global GPU booleans and polling-only checks with the shared lease where applicable; preserve preflight/ownership boundaries, queued job status, continuation IDs, and exactly-once completion receipts.
- [ ] Run focused suites, `npm test`, and `cargo test --manifest-path src-tauri/Cargo.toml --lib`; add a CI integration case that checks chat release precedes model load and no idle weight remains while background work runs.
- [ ] Commit as `feat: coordinate model leases across studios and speech`.
