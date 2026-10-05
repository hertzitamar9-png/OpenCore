# OpenCore and OpenAI Codex Provider Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user explicitly choose the OpenCore local model or a connected OpenAI model for a Codex app-server conversation, with credentials protected by the operating system and no silent routing changes.

**Architecture:** Provider and model selection are stored per OpenCore conversation and passed at thread creation/fork. OpenCore remains the default local Responses provider; API-key and eligible Sign in with ChatGPT credentials are opt-in, stored only in the OS credential vault, and require an external-data notice before any conversation content is sent.

**Tech Stack:** Rust/Tauri, OS credential-vault library/API, Codex app-server provider configuration and account protocol, React/TypeScript, mocked OAuth/API integration tests, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-05-opencore-codex-app-server-design.md`; depends on `2026-10-05-codex-app-server-transport.md` and uses `2026-10-05-codex-task-workspace-controls.md` for provider-aware fork/thread behavior.

## Global Constraints

- OpenCore local remains the default; no OpenAI inference request occurs for local conversations.
- Provider choice is explicit per conversation/fork; never silently switch on authentication, quota, or network failure.
- Keep API keys and OAuth/refresh tokens out of webview storage, logs, timeline, telemetry, and Git.
- ChatGPT-plan sign-in is optional and available only where the official eligibility/scope flow succeeds; do not reuse another Codex installation's credentials or access ChatGPT conversations.
- Show a clear provider/data-routing notice before first external transmission of a conversation.
- Build installers only through GitHub Actions; local tests use fake authorization/provider endpoints.

## Review Focus

- Missing or denied OAuth scope: provider remains unavailable and local selection still works.
- Expired/revoked credentials, quota exhaustion, or provider outage: show a specific error and never silently route to a different provider.
- Provider switch on an existing conversation: require explicit new/fork action and retain old provider/thread mapping.
- Redirect, CSRF state, PKCE verifier, nonce, or invalid ID-token: reject sign-in and keep credentials unchanged.
- Sign-out or secret-store failure: remove/revoke only the matching app credential and report failure without logging token material.

---

### Task 1: Persist provider identity and secure API-key credentials

**Files:**
- Create: `src-tauri/src/codex_provider.rs`
- Modify: `src-tauri/Cargo.toml`
- Modify: `src-tauri/src/store.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src/api.ts`
- Modify: `src/types.ts`
- Modify: `src/App.test.tsx`

**Interfaces:**
- Produces: `CodexProvider { id, display_name, model_id, auth_kind }`, `provider_status()`, `save_api_key(provider_id, secret)`, `remove_provider(provider_id)`, and conversation-scoped provider/model persistence.
- Secrets are read/written only by the Rust OS-credential-vault adapter; frontend commands receive status and opaque provider IDs, never secret values after save.

- [ ] Add failing tests named `new conversations default to OpenCore local Responses provider`, `API key is stored only in OS credential vault`, `provider status and errors never serialize a secret`, and `provider choice is immutable for an existing Codex thread`.
- [ ] Run `cargo test --manifest-path src-tauri/Cargo.toml codex_provider::tests -- --nocapture` and `npm exec vitest run src/App.test.tsx`; confirm provider state and secret boundary tests fail.
- [ ] Add a version-pinned OS vault dependency or platform credential-store adapter and transactional provider/model columns for thread mapping. Persist only nonsecret IDs, status, and consent state in SQLite.
- [ ] Run focused Rust/React tests, `cargo test --manifest-path src-tauri/Cargo.toml --lib`, and `npm test`; inspect serialized status and logs to confirm no token/key string appears.
- [ ] Commit as `feat: add secure per-conversation Codex providers`.

### Task 2: Configure OpenAI API-key provider with explicit routing consent

**Files:**
- Modify: `src-tauri/src/codex_provider.rs`
- Modify: `src-tauri/src/codex_app_server.rs`
- Modify: `src-tauri/src/store.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src/App.tsx`
- Modify: `src/AssistantConversation.tsx`
- Modify: `src/App.test.tsx`

**Interfaces:**
- Consumes: provider store and vault from Task 1.
- Produces: a new-thread/fork flow that creates an app-server provider profile and returns a confirmed endpoint/model identity before the first hosted turn.

- [ ] Add failing tests named `hosted provider requires routing notice before first transmission`, `provider failure never silently falls back to local or hosted inference`, `provider change forks instead of mutating an existing thread`, and `hosted model requests bypass the local model lease`.
- [ ] Run `npm exec vitest run src/App.test.tsx` and the Rust provider tests; confirm routing has no consent gate yet.
- [ ] Configure the API-key provider through the pinned app-server's documented provider/config path; keep local `opencore` config available unchanged. On authentication/quota/network errors, stop the turn and present the exact provider error.
- [ ] Run fake-endpoint integration tests proving the local provider makes zero hosted calls and the hosted provider sends no request before consent.
- [ ] Commit as `feat: add explicit OpenAI provider routing`.

### Task 3: Add optional eligible Sign in with ChatGPT lifecycle

**Files:**
- Modify: `src-tauri/src/codex_provider.rs`
- Modify: `src-tauri/src/codex_app_server.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src/App.tsx`
- Modify: `src/App.test.tsx`
- Modify: `.github/workflows/build.yml`

**Interfaces:**
- Consumes: secure vault and provider selection from Tasks 1–2.
- Produces: opt-in ChatGPT-plan connect/status/sign-out actions using official dynamic registration, PKCE, state/nonce, granted-scope validation, token refresh, and exact app-server account/provider RPCs supported by the pinned schema.

- [ ] Add failing tests named `ChatGPT plan is unavailable without eligibility or required granted scope`, `OAuth state nonce PKCE redirect and ID-token claims are all validated`, `sign-out clears only OpenCore's stored ChatGPT credential`, and `expired authorization blocks hosted requests until explicit reconnect succeeds`.
- [ ] Run focused Rust and React tests with a fake authorization server; confirm the OAuth path is not available.
- [ ] Implement explicit consent and the supported sign-in lifecycle. If the pinned server lacks the required documented account flow, keep the feature disabled with a precise explanation rather than copying credentials from another app.
- [ ] Add Actions tests for invalid/denied scopes, expiry, refresh, cancellation, and secret redaction; run `npm test` and `cargo test --lib` before installer packaging.
- [ ] Commit as `feat: add optional Sign in with ChatGPT provider`.
