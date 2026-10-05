# OpenCore with the Codex App Server

**Date:** 2026-10-05  
**Status:** Draft for user review  
**Scope:** Replace OpenCore's current Codex SDK wrapper with the official Codex app-server protocol and make Codex's native agent controls work inside the existing OpenCore application.

## Goal

OpenCore remains the single desktop application and keeps its local models, ECHO memory, model library, projects, music studio, Game Dev Studio, browser/computer tools, and queued background jobs. Its coding/chat runtime should use the official, version-pinned Codex agent runtime and protocol rather than a thin SDK call surrounded by a separate OpenCore approval flow.

The same Codex runtime must be selectable with either the local OpenCore inference provider or an explicitly connected OpenAI provider. The local OpenCore model remains the default and stays available. Choosing an OpenAI model is an opt-in per conversation and must never silently replace the local model.

The approval experience should be driven by Codex app-server approval requests. OpenCore will render those requests in its own UI, using the decisions and permission details supplied by Codex, instead of deciding tool acceptance in the current custom permission RPC.

## What “the same Codex harness” means

OpenCore will embed the official open-source Codex app-server runtime and use its public, version-matched JSON-RPC protocol for agent turns. This gives OpenCore Codex's agent orchestration and the supported app-server workflows. OpenCore is still its own product UI; this does not copy Codex Desktop's source, branding, private services, or hosted product shell.

The app-server protocol is the contract. A feature is called Codex-compatible only after it works through the pinned protocol and OpenCore's UI. Experimental or development-only protocol features remain off until they become production-supported and pass OpenCore's tests. A version-pinned runtime and generated schema are upgraded together through CI; the app will not fetch an untested Codex runtime on each launch.

The OpenAI Agents API is not the primary local runtime. It runs a managed/cloud harness and sandbox, whereas OpenCore needs local workspace, browser/computer, ECHO, and studio access. Hosted OpenAI models will instead be an optional model provider for the same local Codex app-server runtime. The separately managed Agents API can be reconsidered as a distinct cloud execution mode if OpenCore later adds an explicit remote workspace feature.

## Recommended architecture

### 1. One Codex app-server control plane

Replace the `@openai/codex-sdk` `startThread` / `resumeThread` / `runStreamed` bridge with a persistent `codex app-server --listen stdio://` JSON-RPC client. Use the app-server schema generated from the exact bundled Codex release. Keep the native OpenCore process as the owner of credentials, app data, workspace policy, and Tauri IPC; the React UI must never speak to app-server or see provider credentials directly.

The host should initialize the protocol once per server connection, correlate JSON-RPC requests and server-initiated approval requests, stream notifications, and recover from process exit by resuming saved threads. It must keep a health/version record and reject incompatible schema/runtime combinations at startup.

### 2. Provider selection per conversation

Each conversation records its Codex thread ID, project/workspace, provider ID, selected model, and policy profile. New threads use one of:

- **OpenCore local** — the existing OpenCore Responses-compatible gateway. It keeps ECHO routing and local-model behavior.
- **OpenAI API** — an API key entered by the user and stored in the operating-system credential store.
- **ChatGPT plan** — optional Sign in with ChatGPT for eligible open-source/local apps, only after the user explicitly consents to plan usage. Use the official dynamic registration, PKCE, state/nonce, ID-token/scope validation, refresh, and sign-out flow. Store no tokens in the webview, logs, telemetry, or source. If the required scope is not granted, the provider remains unavailable.

Provider changes apply to a new conversation or an explicit fork/continue action. The app must show a clear notice before conversation content is sent to OpenAI. It must not reuse credentials from the user's separate Codex installation. Provider catalogs are suggestions; the app verifies actual access with a request and reports the returned error accurately.

### 3. Codex-native approvals in OpenCore's UI

Codex owns approval policy and emits approval requests. OpenCore renders the actual action and available decisions inline with the active turn:

- Commands show the command, working directory, reason, parsed actions, and any requested filesystem/network access.
- File changes show paths and a reviewable diff before the decision.
- OpenCore MCP tools show the tool name and arguments. Configure the private OpenCore MCP server to use Codex's prompt-based MCP approval path and implement MCP elicitation so the approval request returns through app-server to the active conversation.
- Decisions follow the protocol's available options: accept once, accept for the session, decline, cancel, and supported permission/policy amendments. The UI renders only decisions the server offers and treats the completed item as the final result.

Remove the separate `permission` RPC and current generic “Approve this tool?” decision path after the native command, file-change, network, and MCP approval cases pass integration tests. Do not have Codex auto-approve the private MCP server just to hide a second prompt. OpenCore may keep confirmation screens for independent product actions that are not Codex tool calls, such as deleting model files or revoking an account.

The current four-mode slider is replaced by Codex-compatible permission profiles backed by actual app-server settings. The normal profile is workspace-scoped write access with on-request escalation and user review; an optional Codex auto-review profile uses the supported approval reviewer; full access stays an explicit, warned profile. Existing settings are migrated conservatively: old per-chat/global “allow everything” flags never silently grant broader permissions under the new runtime. The exact profile names and mapping are defined in the implementation plan from the pinned schema.

### 4. Thread, workspace, and review controls

Map app-server events into OpenCore's existing conversation timeline and add Codex-compatible controls for the public API that the pinned release exposes, including:

- create, resume, list, read, fork, archive, and interrupt threads;
- queue, reorder, and start follow-up input; steer an active regular turn; cancel it;
- show plans, message/reasoning summaries where permitted, tool activity, command output, file changes, usage, and terminal status;
- start Codex's reviewer for uncommitted changes, a base branch, or a commit;
- discover and enable/disable Codex skills and supported MCP servers in the selected workspace;
- preserve Codex multi-agent controls, while enforcing OpenCore's single-local-model lease.

OpenCore's workspace/project manager remains the product surface. The app-server protocol does not itself document a worktree lifecycle API, so worktree creation/cleanup must be implemented as a separate host-side project operation and passed as the thread `cwd`; it must not be represented as an app-server feature. Cloud/remote-host work, hosted ChatGPT Apps, and Codex Desktop-only UI features are outside this integration unless a stable supported API is added later.

### 5. ECHO and single-model handoff

ECHO remains OpenCore's archive and retrieval system. The OpenCore model gateway continues to attach its conversation-scoped ECHO behavior when the local provider is selected; Codex rollouts remain the agent execution transcript. A durable ID mapping and sync rules prevent duplicate or conflicting timelines.

Preserve the product rule that only one local model is active at a time. Add one model lease shared by the primary Codex turn, reviewer/subagent calls to a local provider, and local music/image/3D/speech jobs. If a Codex task asks a studio model to generate, enqueue the job, end the active Codex model turn, release the local model, and show the user the destination studio. Completion notifications may wake the conversation after the studio releases its model; no model process remains resident just to wait. Extra local subagent calls are serialized through the lease; hosted OpenAI calls do not load local model weights.

## Conversation and history migration

The application must not discard OpenCore messages, ECHO records, Codex rollouts, skills, permissions, or existing projects. Reuse the existing Codex home only after verifying that the pinned app-server can read the current SDK-created rollout format. If it cannot, import the existing Codex transcript as read-only source history and start a new app-server thread linked to the original OpenCore conversation. Preserve original files and IDs; do not rewrite a saved transcript in place.

Store the OpenCore conversation ID to app-server thread ID and provider/workspace mapping transactionally. On crash or disconnect, resume the exact saved thread and reconcile notifications before allowing another turn. A new app-server release must not silently migrate or compact existing histories.

## Security and privacy

- Keep the app-server listener on private stdio; do not expose an unauthenticated TCP server.
- Keep OpenAI credentials in the OS keychain/credential vault, with explicit connect, disconnect, and account selection controls.
- Never send a local conversation to a hosted provider until the user selects it and sees the data-routing notice.
- Keep project paths, ECHO scopes, and model-provider data isolated per conversation.
- Keep sandbox policy and approval policy independent; a UI label must match the actual effective policy.
- Default to workspace scope. Full-disk and network access require an explicit Codex-compatible profile and warning.
- Apply the single-local-model lease across parent, reviewer, subagents, and studio jobs.
- Do not ship user credentials, machine paths, ECHO archives, downloaded weights, or benchmark data in the installer or repository.

## Alternatives considered

1. **Use Codex app-server for both local and hosted providers — recommended.** It keeps the official Codex runtime and approval protocol while preserving local ECHO and the existing OpenCore UI. It needs a real JSON-RPC client, provider/account management, and event-to-timeline integration.
2. **Keep the TypeScript SDK and add Codex-style approval dialogs.** This is smaller, but leaves OpenCore's own per-tool decision RPC in the execution path and does not expose the richer thread/control APIs as a product feature. It does not meet the requested “same harness” target.
3. **Use OpenAI's managed Agents API as the main runtime.** It supplies OpenAI-managed sessions, orchestration, compaction, recovery, sandbox execution, and tools, but changes the execution model to a managed/cloud environment and complicates local model inference, local workspaces, and the single-model studio handoff. It is not the local-first default.

## Implementation sequence

1. Pin and package Codex app-server with a matching generated protocol schema; add a read-only protocol probe and version mismatch guard.
2. Build and test the native JSON-RPC client, event router, reconnect/resume flow, and thread-ID/history migration.
3. Route local OpenCore model turns through app-server and prove tool-free text, tool use, streaming, cancellation, ECHO behavior, and persisted resume.
4. Move shell, file, network, and OpenCore MCP approvals to Codex-native server requests; remove the old custom tool permission request only after end-to-end gates pass.
5. Surface thread controls, queues/steering, diffs/review, skills/MCP status, and local multi-agent behavior.
6. Add optional OpenAI API-key and ChatGPT-plan providers with secure storage, user consent, token lifecycle, and external-data notice.
7. Reconnect music/Game Dev Studio jobs through the single-model lease and verify that the main Codex turn exits before studio inference starts and only resumes after the studio releases the model.
8. Verify package/update integration and all builds through GitHub Actions only; ship only after the same CI run proves protocol, UI, security, and recovery tests.

## Acceptance tests

- Local OpenCore is still the default provider; choosing it makes no outbound OpenAI inference request.
- An OpenAI provider is unusable until the user explicitly connects it; credentials never enter frontend storage, logs, or telemetry.
- The local provider can stream a response, use ECHO and app tools, stop/cancel, resume the same thread after restart, and preserve the OpenCore timeline without duplicate messages.
- Commands, file edits, network escalation, and OpenCore MCP actions each produce the correct native approval event; malformed, stale, cross-thread, or canceled requests fail closed.
- The diff shown in an approval is the diff Codex is actually asking to apply; completed/declined/canceled status is rendered from the server's terminal event.
- Only server-supplied available decisions are shown. Session grants remain bound to the same thread/workspace and never expand beyond requested permissions.
- Fork, archive, queue, steering, interruption, review, skill discovery, and subagent events behave as documented by the pinned protocol.
- Concurrent local parent/subagent/reviewer/studio requests are serialized and never violate the one-local-model lease. A background studio task releases the primary model and does not leave a waiting model loaded.
- Existing SDK-era transcripts and OpenCore data remain recoverable; migration can be repeated safely and does not erase source history.
- Provider auth expiry, revoked consent, quota exhaustion, server crash, schema mismatch, and network failure produce specific recoverable UI states without silently switching providers.
- The full install build is run and verified in GitHub Actions, not locally.

## Known boundaries at design time

- “Same harness” means the same version-pinned, open-source Codex agent runtime through its documented app-server API; it does not make OpenCore the Codex Desktop product or grant private desktop/cloud features.
- OpenAI ChatGPT-plan usage is optional, requires explicit user authorization and the required granted scope, and shares the account's applicable plan usage limits. API-key billing is separate.
- Some Codex protocol endpoints and approval features are experimental or evolve quickly. Only capabilities in the pinned production protocol are enabled; protocol upgrades require regenerated schemas and CI coverage.
- Worktree lifecycle remains an OpenCore host feature unless Codex app-server later exposes a stable worktree API.
- Local model inference quality, context limits, ECHO recall quality, and tool correctness remain properties of the selected model/runtime and must be measured separately; using the Codex harness does not upgrade the underlying local model.

## Official references

- [Codex app-server protocol and approvals](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
- [Codex app-server schema and configuration](https://github.com/openai/codex/blob/main/codex-rs/core/config.schema.json)
- [Use ChatGPT plan authorization with Codex app-server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)
- [Sign in with ChatGPT for open-source apps](https://developers.openai.com/siwc/token-sharing-open-source)
- [OpenAI Agents API managed Codex harness](https://developers.openai.com/api/docs/guides/agents-api/overview)
