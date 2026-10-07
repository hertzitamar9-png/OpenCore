# Learning Studio Implementation Plan

> **For agentic workers:** Use superpowers:dispatching-parallel-agents for the isolated domains below. Root integrates and independently reviews the release. The user requested implementation now; proceed under that existing authorization.

**Goal:** Ship raw local learning records, real Unsloth training and checkpoint continuations, three configuration modes and the reported adaptive layout fixes.

**Architecture:** Add a local immutable learning ledger and checkpoint-sized training workers to the existing Tauri core, GPU lease and durable background scheduler. Reuse the normal assistant for auto configuration and review. Preserve the existing appearance and saved data.

**Tech Stack:** React/TypeScript, Rust/SQLite, Python/Unsloth/TRL/Transformers, GitHub Actions.

**Spec:** ../specs/2026-10-07-learning-studio-design.md

## Global Constraints

- No local native compilation, native tests, dependency installation or application packaging.
- Raw source records and rejected candidates are retained; inference/model claims require actual runtime evidence.
- All modes share validated finite configuration, explicit precision and time/disk/checkpoint caps.
- Preserve existing settings, approvals, source folders, model weights and gradient styling.

## Review Focus

- Corrections or changed source content cannot overwrite the original raw record or silently enter both train and holdout.
- Resuming a checkpoint cannot duplicate optimizer steps, lose metrics or accept stale baseline/data hashes.
- A checkpoint review must release the GPU before the assistant loads and resume only after its turn ends.
- Large raw output must stay paged and complete; previews must declare bounds and allow exact export.
- Narrow/large windows and open sidebar must keep lower controls visible without changing saved font preferences.

### 1. Raw learning ledger

**Files:** learning_store.rs, learning_store_tests.rs, focused EventStore extension if required.

**Interfaces:** Exact `LearningStore` methods in spec; camelCase JSON pages `{records,total,nextCursor}`; complete raw values, source identity, RFC3339 timestamps, evidence labels; export paths and frozen manifest.

- [x] Write native regression cases for exact multiline text, source updates, failure retention, pagination and grouped holdout split.
- [x] Implement timeline/ECHO synchronization, raw detail/query/annotation and JSONL/CSV/dataset export.
- [x] Parse with rustfmt locally; run all native tests only in GitHub Actions. Release 0.2.137 passed 500 native tests (17 ignored).

### 2. Unsloth worker

**Files:** resources/learning/{worker.py,config.py,test_learning_worker.py,requirements.json}.

**Interfaces:** CLI and request/event/receipt shapes in spec. Config keys: method, precision, epochs, maxSteps, learningRate, loraRank, loraAlpha, loraDropout, batchSize, gradientAccumulation, maxSeqLength, optimizer, checkpointEvery, seed, maxMinutes, maxDiskBytes, minimumImprovement.

- [x] Test invalid/non-finite config, raw events, evaluation comparison/rejection and chunk boundary/resume bookkeeping with dependency-free fixtures.
- [x] Implement real Unsloth loading, SFT/DPO, data/manifest verification, baseline and candidate evaluation, durable raw telemetry/checkpoints and chunk termination.
- [x] Run interpreted tests and probe existing compatible environments without installing dependencies locally. Record exact runtime qualification limits. All 54 worker tests pass on Windows; the local CUDA optimizer path remains unqualified because installed package versions differ from the pinned stack.

### 3. Responsive desktop fixes

**Files:** styles.css, WorkspacePanel.tsx/.css, WindowTitleBar.tsx, accessible selector helper and related tests/e2e.

- [x] Reproduce fixed content widths, nested grid height clipping and native gray selection source.
- [x] Add bounded fluid scaling/reflow, matching window controls and themed selector behavior while preserving preferences and gradient.
- [x] Test resizing/sidebar geometry through the existing browser QA path; avoid local production builds. Windows CI passes all seven live adaptive cases and existing capture/appearance/studio layout checks.

### 4. Native orchestration, tools and Studio

**Files:** learning.rs, learningApi.ts, LearningStudio.tsx/.css/tests, lib.rs, codex_harness.rs, App.tsx, message-time.ts and studio/setup integration.

- [x] Implement durable owned chunk runner, GPU release, checkpoint/final prompt jobs, resume/cancel/interrupted receipts and update/Quit shutdown.
- [x] Register one desktop command and common agent tool; add automatic source sync and exact dated activity queries.
- [x] Add three configuration modes and a real dedicated assistant conversation with model selection; records, runs, loss/checkpoint/raw views.
- [x] Integrate supported runtime setup with model installation/generation; keep unsupported capability errors accurate.
- [x] Verify interpreted/frontend/type checks, then GitHub native tests and packaged integration. Release 0.2.137 passed 458 frontend, 54 Node integration, 54 learning-worker and 21 Phonon checks; packaged learning/speech resources were verified in the installed app context.

### 5. Release and installed verification

- [x] Independent review of data gates, raw preservation, lifecycle and layout; fix concrete findings.
- [x] Push/attach PR, merge passing source, verify public release assets and install through updater. PRs #15 and #16 merged; public signed release 0.2.137 was installed through the Update action.
- [x] Verify automatic reopen, existing data/music preservation, job continuation and resized installed UI; report exact remaining external prerequisites. The app reopened automatically, data/settings/music preservation receipts passed, and the bounded verification worker completed. Real CUDA optimizer qualification remains pending; it is not implied by the passing fixture tests.

Release evidence: https://github.com/hertzitamar9-png/OpenCore/actions/runs/37656532900 . Subsequent updates remain user-initiated; checking availability does not download or install an update.
