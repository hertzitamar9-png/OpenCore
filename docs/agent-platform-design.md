# OpenCore platform: capabilities and evidence

The application remains private. The inference checkpoint is independent of the harness: adding tools, memory or verification does not itself make a model more intelligent or establish a coding benchmark improvement.

## Implemented architecture

- The pinned **OpenAI Codex app-server 0.160.0** owns native command execution, patches, workspace/thread state and agent orchestration. OpenCore supplies its local Responses provider, app tools and approval UI. MCP stdio and Streamable HTTP connections are merged into the real server configuration. Existing bypass selects `dangerFullAccess`; other modes retain their sandbox and dialogs.
- Identity, editable additive instructions, compaction, verification, enabled skills/plugins, MCP, appearance and memory controls use persistent backend settings. The agent `app_control` tool uses the same state as Settings. A save returns actual before/after changes and refreshes the UI. Context thresholds apply on the next agent request, with headroom clamped to the model's actual window.
- Development skill names/descriptions are initially available. `skill_library read` loads full instructions only when needed. Custom SKILL.md folders and portable skill/MCP plugin manifests are supported. Discovery is bounded and does not run arbitrary installation hooks. DeepSeek JavaScript plugins are not binary-compatible with this manifest; wrap their tools through MCP instead.
- ECHO remains exact archived history. A separate SQLite ledger stores sourced versioned facts/lessons and real app/studio activity. FTS5 searches normal-length literal queries across summaries, bounded request details and scope identifiers; short queries use a literal scan. The model searches these on demand. A song receipt includes the submitted lyrics/style/settings, creation/completion time, output paths and job status. Recalled content is evidence, not instructions. Failed/queued jobs are distinguishable from completed outputs.
- **No / Default / Long / Max** adds zero/one/two/three completion review turns after changed work. Reviews use the original request and host-recorded tool evidence, ask for in-place repairs, and report limitations. Unchanged questions do not incur extra tool turns. A studio handoff skips review to release the text model and allow the one-GPU queue to proceed. The repair-attempt value is model guidance, not a guaranteed scheduler hard cap.
- Existing VirtualBox and Android SDK profiles expose real launch/status/inspect/screenshot actions. Android supports APK installation, app launches, taps, keys and text. PC guest apps/tests require Guest Additions and configured guest credentials. Screenshots are attached when small enough; larger files are inspected with the native image tool. Capturing a screenshot does not automatically certify behavior.
- Media Studio supplies distinct controls for video, TTS, reference voices, OCR, omni/audio and offline policy inference. Seventy-four new catalog entries add ten publisher identities per new category and bring existing media categories to at least five. Forty-two optional downloadable entries share 38 verified publisher bundles; the remainder link explicit setup sources. Registered Python runner adapters execute through the existing durable GPU queue and report actual outputs. SDKs, compatible model weights, environments and OS images must exist before those adapters can generate; the UI reports missing setup. The main agent can discover catalog entries and configure an existing validated worker through studio_use; configuring a worker does not prove inference succeeded.
- Input questions run without blocking server notifications. A native server resolution removes the matching dialog; stale requests use the correct typed cancellation response. Sensitive settings arguments are redacted before timeline/approval serialization. Effective plugin definitions and inherited credential digests refresh the app-server child environment when they change.

## Source research

The implementation adopts compatible concepts using original OpenCore code rather than concatenating other agents' system prompts:

| Source | Useful concept | OpenCore implementation |
|---|---|---|
| [Codex app-server](https://developers.openai.com/codex/app-server/) and [skills](https://developers.openai.com/codex/skills/) | Durable threads, native approvals, MCP and progressive disclosure | Keep actual app-server; add scoped app tools, input dialogs and small skill metadata index |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Modular plugin architecture | Portable skills/MCP plugins with explicit enablement and transport validation; no second competing agent loop |
| [Hermes memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/) | Separate curated facts from searchable history | Sourced fact/lesson versions + activity search alongside exact ECHO archives |
| [ZAI Open-AutoGLM](https://github.com/zai-org/Open-AutoGLM) | Observe/act with Android devices via ADB | Structured device testing and actual screenshots inside the existing Codex orchestration |

## Limits to report

This does not include proprietary Codex desktop services or Claude model weights/training. No claim that OpenCore is the world's best harness is established. Model cards prove repository identity and metadata, not local runtime performance. Catalog downloads do not silently fetch weights. Gated/unverified model bundles are explicit setup entries. Offline robotics predictions do not control hardware automatically. Missing hypervisors, devices or SDKs are reported as missing setup.

Installers are built in GitHub Actions. Frontend checks and native Rust/integration tests remain separate evidence from an end-to-end local model task; claims of model skill adherence require a real inference run.
