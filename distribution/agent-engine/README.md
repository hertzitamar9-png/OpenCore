# OpenCore Agent

OpenCore Agent builds the local agent engine from the Apache-2.0 app-server source at
`a956835d020762cb2b570053af06f643a11c0ecc`. The source snapshot contains the upstream
license and notice. The OpenCore entry point and patch in this directory modify the
upstream app-server entry point, its local-model instructions, and recovery of missing
OpenCore session state. The upstream shell, file patching, sandbox, approvals, MCP,
thread storage, streaming and subagent implementations run inside the compiled engine.

The Windows host uses the existing v2 IPC and on-disk session format. The private
installation path remains compatible with the recovered Windows host. OpenCore selects
the model and adds its developer instructions, ECHO, user preferences and enabled app
tools. An OpenCore chat uses the local gateway and the private state directory supplied
by the host. External chat import is a separate connector.

Build on Windows x64 with Python 3.12, Git, MSVC and Rust 1.95.0.

```powershell
python distribution/agent_engine.py --output distribution/.output/agent-engine --cache $env:RUNNER_TEMP/opencore-agent-build
```

Cargo uses the pinned crate versions. The script verifies the linked Windows V8 inputs
against the hashes in the source snapshot. The installer needs the resulting engine
and its Windows helpers. Building or running it does not require the Codex CLI or a
Codex account.
