# Shared Agent Groups Implementation Plan

> **For agentic workers:** Implement tasks in sequence; independent slash, connector and catalog repairs have separate file ownership.

**Goal:** Add shared friend/model groups with per-PC execution, visible task plans, explicit permissions, and real transport.

**Architecture:** A Python group service persists group state under OpenCore's managed root. Remote group capabilities use the existing TLS listener. Each client maintains its own membership and sharing preferences. A background desktop worker invokes the native chat agent for claimed local jobs; agent tools update the shared plan and can delegate to advertised peers.

**Tech Stack:** Python standard library SQLite/HTTP/TLS, existing Tauri native commands, recovered React frontend and the model-catalog feature installer.

**Spec:** `docs/superpowers/specs/2026-11-01-shared-agent-groups-design.md`

## Global constraints

- Default sharing and connectors off; per-PC permissions remain under that PC owner's control.
- Preserve unrelated chats, installed model files and permanent setup bytes.
- Separate group credentials from mobile device credentials.
- No arbitrary native command relay through group APIs.
- Do not claim internet reachability or real model execution from a UI fixture alone.

## Review focus

- Replayed task requests must not execute twice.
- A revoked member loses read and execute access immediately.
- A guest cannot change another PC's models or permission mode.
- A busy PC queues tasks; different PCs may execute concurrently.
- A disconnected worker cannot leave a task permanently shown as running.

### Task 1: Group state and authenticated transport

- [ ] Add `src-tauri/resources/runtime-setup/hf-extension/group_chats.py` for SQLite state, invitations, membership, messages, shared agents, tasks, progress and task-plan tools.
- [ ] Test owner/member isolation, expiring invites, revocation, idempotent messages, scoped worker claims, task leases and delegation budgets in `test_group_chats.py`.
- [ ] Add a pinned-TLS client and local joined-group manager; test multiple clients against real HTTP/TLS handlers.
- [ ] Wire scoped `/groups` routes into `hf_bridge.py` and the existing remote listener; keep mobile authorization separate.

### Task 2: Native worker and agent tools

- [ ] Add a background desktop worker that publishes only explicitly shared installed models, waits for an idle local runtime and claims one task at a time.
- [ ] Run through `start_profile` and `send_chat_message` with locally chosen approvals in a dedicated conversation, stream that conversation's activity, and return the final result.
- [ ] Add scoped group task/message/delegation tools to the existing agent runners using locally registered per-task capabilities; test that a missing or invalid capability enables no group tools.
- [ ] Verify simultaneous work on two independent worker clients, and denied/approved tool behavior.

### Task 3: Group UI and delivery

- [ ] Add a reusable catalog feature installer for group UI and worker initialization with a small recovered-App mount.
- [ ] Implement groups, invitations, joins, members/agents, mentions, task plans, progress, and owner-only local model/approval controls.
- [ ] Exercise desktop/phone layouts and actual message/claim/result flows with rendered tests.
- [ ] Integrate independent bug repairs, check native asset budgets, publish on main, verify signed public artifacts and report installed/update state accurately.
