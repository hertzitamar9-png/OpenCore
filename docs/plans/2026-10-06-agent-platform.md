# OpenCore agent platform

## Authorized outcome

Extend the private application around its pinned Codex app-server. Preserve ECHO archives, projects, installed models, approval choices and the one-GPU studio queue. Keep model weights optional; build release installers only in GitHub Actions.

## Design

1. A persistent platform configuration supplies identity, editable instructions, No/Default/Long/Max verification, appearance, custom skill/plugin directories, MCP connections and testing profiles. It is shared by settings UI and agent tools. Changes emit an event so active UI controls reflect actual backend state.
2. Skills use names/descriptions initially and load bodies on demand. Builtin development skills coexist with custom SKILL.md and portable plugin manifests. Plugin discovery never executes installation hooks. Enabled MCP transports are supplied to the actual app-server.
3. An indexed activity ledger records actual app changes and studio jobs, alongside durable sourced facts/lessons. ECHO retains exact raw history. Memory results remain evidence, not executable instructions.
4. Agent settings tools mutate approved structured settings, not arbitrary frontend strings. Existing Codex approval boundaries apply. Bypass preserves actual danger-full-access. Meaningful host changes are included in completion receipts.
5. No checks disables added review turns. Default, Long and Max run bounded additional review turns for changed work, preserving original acceptance criteria and existing project behavior. Review never reports a test as passed without tool evidence, and studio handoffs skip extra GPU work.
6. Testing profiles connect existing VirtualBox PCs or Android emulator/ADB devices. They expose structured launch, inspect, screenshot, install and control actions with receipts. No VM images or mobile SDKs are downloaded automatically.
7. Extend the model catalog with verified primary-source entries in video, speech synthesis, voice, OCR, omni and policy categories. Runtime availability is explicit. Category-specific forms use the durable studio queue; unknown runtimes require setup before generation.

## Interfaces

Backend `agent_platform` owns configuration, skills/plugins, memories and activity. Public functions: `configuration(&EventStore)`, `save_configuration(&EventStore, PlatformConfig)`, `tool_specs()`, `execute(&EventStore, &Path, &str, &Value)`, `record_activity(&Path, &ActivityEvent)`, `instruction_text(&PlatformConfig)`, `mcp_configuration(&PlatformConfig)`.

`PlatformConfig` (camelCase JSON): `systemPrompt:string`, `compactAtTokens:u32` (200000), `verification:"no"|"default"|"long"|"max"` (default), `repairAttempts:u16` (3), `skillDirectories:string[]`, `pluginDirectories:string[]`, `disabledSkills:string[]`, `mcpServers:McpServer[]`, `activityEnabled:bool`, `memoryEnabled:bool`, `appearance:AppearanceConfig`. MCP fields: id/name, enabled, command/args/env or url/bearerTokenEnvVar, startupTimeoutSec/toolTimeoutSec. Appearance: theme (dark/light/system), accentColor, fontFamily, fontSize, density (comfortable/compact), reducedMotion, highContrast. Identity is OpenCore by default; custom prompt is additive.

Commands owned by root integration: `agent_platform_configuration`, `agent_platform_save_configuration`, `agent_platform_skills`, `agent_platform_plugins`, `agent_platform_activity`, `agent_platform_memories`, `agent_platform_action`. Tool names `app_control`, `agent_memory`, `skill_library`. Tools return redacted configuration; UI can edit its own connection secrets. Mutations pass the existing approval bridge. Tauri event `opencore-agent-settings-changed` has the full configuration for the local UI (no remote delivery).

Frontend new module `src/agent-platform.ts` shares this schema; `AgentPlatformSettings.tsx` renders new panels without editing App.tsx. Root connects global configuration, shared compaction changes, navigation and tool execution.

## Verification

- Baseline frontend suite, then focused tests for configuration validation, source confinement, memory and activity persistence, MCP conversion, settings synchronization, verification selection and forms.
- TypeScript no-emit check and full frontend tests locally; Rust tests and installer packaging in Actions.
- Browser inspect settings, model library, studio forms and changed navigation; test persisted settings and evidence search through actual commands when installed runtime is available.
- Preserve public/private boundaries; no superiority claims without evaluation. Runtime adapters requiring missing SDKs or weights are labeled with the exact setup requirement.

## Work split

Root: actual app-server integration, review loop, approvals, testing lab tools, App/API wiring and final verification.
Backend platform domain: new agent_platform.rs and its tests/resources only.
Frontend settings domain: new agent-platform.ts, AgentPlatformSettings.tsx, CSS and tests only.
Catalog/media domain: catalog manifest, studio_jobs category handling, composer skills and category forms. Shared App/lib/API/types wiring stays with root.
