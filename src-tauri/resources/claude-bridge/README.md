# OpenCore Claude Code bridge

This Mods plugin connects an external Claude Code session to the running
OpenCore app. It uses Anthropic's extension API; it does not modify the Claude
Code binary or change the inference provider.

## Automatic setup

OpenCore installs and pairs the bundled bridge automatically on startup. Its
bundled Claude executable registers a local marketplace and installs the plugin
at user scope using the supported plugin management commands. No download,
installation button or special launch command is needed. Open Claude Code
normally from your project while OpenCore is open. Existing Claude sessions
load a newly installed or updated plugin the next time they start.

Requires Claude Code 2.1.287 or newer with Mods enabled. The API is early access:
availability and policy restrictions can vary. Use a supported Claude release;
do not override managed policy. Only OpenCore's marketplace and plugin entries
are configured; existing settings, authentication and provider profiles are
preserved. App updates refresh the paired plugin cache automatically. A later
explicit disable in Claude is preserved. Errors are shown in Connectors and
configuration retries automatically. Local-model compatibility remains a
separate check.

## Implemented behavior

- Before a prompt enters the model, automatically recall a bounded selection
  of hash-verified ECHO source excerpts from this workspace/project. Sources are
  clearly marked as historical evidence, with page IDs for full-page reads.
  Short follow-ups also use a bounded recent user task to resolve their subject.
- Register `echo_search`, `echo_read` and `studio_use` tools.
- Save prompts, completed answers, tool calls/results and error metadata into
  OpenCore's normal timeline. Imports are incremental and retries idempotent.
- Queue installed Music/Assets Studio models through the existing job manager.
  Generation waits for the Claude turn to end and the app's GPU reservation.
  A host timer checks only owned jobs and submits one result notification when
  a job terminates, without running an LLM during the wait.
- Show paired connection/turn status in Connectors and refresh a displayed
  conversation as bridge events arrive.

Examples:

```text
/opencore-bridge:music Write an upbeat song about AI
/opencore-bridge:assets /3d A low-poly lighthouse
/opencore-bridge:assets /image A painted forest map
```

Only an engine-attested user/SDK prompt enables new generation for that
category. Recalled text, peer messages and plugin completion notifications do
not authorize it. Models must already be installed and their studio runtimes
configured; the bridge reports setup errors instead of simulating generation.

## Boundaries and recovery

The bridge uses only `127.0.0.1:8812`, a generated pairing token and scoped
workspace/session identities. Browser-origin calls are rejected. Page reads
enforce existing project scope and verify hashes. Secret redaction runs before
events enter the app database. Source plugin files never contain real tokens.

An unavailable app does not replace ordinary Claude tool results. Failed
capture is reported, and up to 64 pending events can be retried in the same
plugin process with their original workspace identity. Session closure before
delivery can lose pending events; existing transcript import remains available
for recovery. Studio job identifiers persist in the plugin store; resuming the
same Claude session in the same workspace restores completion notifications
without generating again. Other sessions cannot claim these notifications.
Open the studio to inspect results while Claude is closed. A disconnected active-turn lease
expires after 90 seconds; idle heartbeats cannot reacquire it.

ECHO recall needs the existing archive/indexing runtime and a configured archive.
Unavailable indexing leaves saved activity in the timeline and reports a
diagnostic. This plugin does not provide unbounded simultaneous GPU attention.

## Verification

`npm test` covers hook behavior plus the UI. `cargo test --lib` covers scoping,
authorization, archive provenance, durable deduplication and GPU wait leases.
The native Claude Mods runner additionally loads this actual module in its
sandbox and checks dispatch. Run it against an isolated copy of this folder,
with `hooks/local-config.mjs` containing a **fake** token (e.g. `test-only`):

```text
claude plugin validate <isolated-plugin-copy>
claude plugin test <isolated-plugin-copy>
```

The native tests mock all host I/O and require no model inference or GPU. They
verify extension compatibility, not a paid Claude session or real studio
generation. API declarations can be regenerated with `/plugin-types`.

The local verification used Claude Code 2.1.288. Mods validation and native
dispatch tests passed alongside the app's frontend/backend suites. An
authenticated production session and actual studio generation through Claude
still require a live smoke test; the mocked tests do not establish that result.
