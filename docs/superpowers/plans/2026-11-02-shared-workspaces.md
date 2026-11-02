# Shared Workspaces Implementation Plan

> **For agentic workers:** Use systematic debugging and test-first focused changes. Independent ECHO, activity-rendering and catalog verticals may run concurrently; root owns group helpers/UI and integration. No branch or worktree changes.

**Goal:** Make groups useful for collaborative local/API agent projects while preserving ECHO progress and complete visible activity.

**Architecture:** Extend the existing GroupStore/GroupManager with channel-scoped persistence, deterministic routing and owner-local project bindings. Reuse the native API provider selection and execution path. Keep the recovered native binary and permanent installer unchanged except verified existing data/frontend slots.

**Tech Stack:** Python sqlite3/HTTP/TLS, JavaScript React, Node tests, Python unittest, packaged-UI Playwright checks.

**Spec:** docs/superpowers/specs/2026-11-02-shared-workspaces-design.md

## Global Constraints

- Work on main; preserve chats/models/credentials and unrelated untracked files.
- No automatic connector, external history import, app restart or paid API request.
- No cloud relay. Peers receive no local path, API key or native-app capability.
- Fixed compressed asset slots: index 77,142; App 314,878; catalog 364,753 bytes.
- Verify genuine released files and signatures before claiming publication.

## Review Focus

- An unauthorized member must not read a private channel through messages, task plans or agent context.
- Replayed messages and results must not run models twice; agent replies must not trigger bots recursively.
- Renaming/reconfiguring an agent must not reset another task's capabilities or select a different API model.
- Project selection must use the local owner's saved binding; a remote path/project ID must never choose local files.
- A checkpoint, completion, navigation or stale snapshot must not remove a tool already shown.

### Task 1: ECHO no-progress safeguards

**Files:** echo context/runner guard sources and focused tests; claude runner hook integration owned by root.

- [x] Reproduce repeated failed actions and large-result evidence lost during trimming.
- [x] Add durable bounded exact fingerprints, pre-action stop and explicit failure handling.
- [x] Check changed inputs/successful mutations/live polling remain allowed.
- [x] Run ECHO and agent guard regression suites; inspect terminal event evidence.

### Task 2: Visible activity preservation

**Files:** distribution/context-activity-patches.cjs, generation-preview.js and regression/render tests.

- [x] Reproduce checkpoint/done clearing a visible tool or overwriting newer output.
- [x] Preserve merged tool lifecycles and output through stream completion/navigation.
- [x] Verify actual bundled renderer paths, not only standalone helper tests.

### Task 3: Complete mode coverage and requested models

**Files:** model_modes.py, model-modes.js, catalog source/research inputs, focused tests.

- [x] Count all catalog families and conversational text-capable families by category.
- [x] Reproduce missing descriptors/controls, including generative multimodal models.
- [x] Add exact requested publisher identities, genuine quant packages and runtime limitations.
- [x] Generate shared catalogs once after all source inputs are ready, then verify family/mode/artifact equivalence.

### Task 4: Channel, project, direct-message and reply policy backend

**Files:** group_workspace.py (new), group_chats.py, test_group_workspace.py (new), test_groups.py.

- [x] Write failing tests for channel isolation, private DMs, shared projects, reply policies, duplicate delivery, cancellation and migration.
- [x] Add additive SQLite schema/migration and default General channel.
- [x] Implement accessible channel views; scope task context/results/plans to channels.
- [x] Implement validated per-agent human-message routing; no automatic agent-to-agent cycles.
- [x] Keep project bindings and API profile configuration local; sanitize advertisements.
- [x] Run real multi-manager pinned-TLS and HTTP authorization regressions.

### Task 5: Local/API worker and workspace UI

**Files:** src/group-chats.js/.css, distribution/group-ui-patches.cjs, test-group-worker.cjs, qa-groups.cjs.

- [x] Write failing tests for configured API model readiness, exact model IDs, owner bindings, cancellation and stale live progress.
- [x] Discover configured enabled API models via api_provider_list and installed local models via list_model_library.
- [x] Register dedicated chat, initialize its local project and submit with owner-selected instructions/approvals; never focus the window.
- [x] Add channels/projects, DMs, reply controls and named agent configuration to responsive workspace.
- [x] Validate desktop/phone workflows, overflow, tap targets and state persistence in actual generated UI.

### Task 6: Integration and release

**Files:** workflow tests/resources, docs/shared-groups.md, release notes and source UI projection if needed.

- [x] Run focused and full required test suites; review shared changes and compression sizes.
- [x] Build the exact Windows frontend and patched executable from the pinned baseline.
- [x] Commit reviewed explicit files to main and publish via the existing verified workflow.
- [ ] Verify isolated install/startup, changed-file updater, signed public assets and latest feed; do not change/restart the user's current app.

Final review corrections: worker submission rechecks its exact active lease after model loading; failed tasks retain earlier tool progress; lease heartbeats query authoritative task state instead of the newest UI task page. All three have red/green regressions. Packaged Native/ECHO cards, Chat Core desktop/phone workflow and all catalog preset checks passed against the final 0.3.255 candidate.
