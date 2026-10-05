# Agent platform backend

`src-tauri/src/agent_platform.rs` owns agent configuration, library discovery and derived evidence. The caller owns approval decisions, Tauri command registration and the `opencore-agent-settings-changed` UI event. Execute mutation actions after the existing approval bridge accepts them.

## Public interfaces

| Function | Result |
| --- | --- |
| `configuration(&EventStore)` | `Result<PlatformConfig, String>` with local connection values |
| `save_configuration(&EventStore, PlatformConfig)` | Validated persisted `PlatformConfig` |
| `redacted_configuration(&PlatformConfig)` | Model-safe `Value` |
| `redacted_tool_arguments(name, &Value)` | Sanitized logging/approval copy; execute the untouched arguments |
| `tool_specs()` | `Vec<Value>` of app_control, agent_memory and skill_library definitions |
| `execute(&EventStore, &Path, &str, &Value)` | Actual action result or an error |
| `skills(&PlatformConfig)` / `plugins(&PlatformConfig)` | Metadata vectors |
| `activity(&Path, &str, usize)` / `memories(&Path, &str, usize)` | Literal search results; 1 to 100 records |
| `memory_history(&Path, scope, kind, key, limit)` | Version history including superseded records |
| `record_activity(&Path, &ActivityEvent)` | Durable redacted event; repeat identical IDs are idempotent |
| `instruction_text(&PlatformConfig)` | OpenCore guidance, additive user prompt and skill metadata |
| `mcp_configuration(&PlatformConfig)` | Actual custom Codex `mcp_servers` map to merge with the application bridge |

`ActivityEvent::new(category, action, summary, source, details)` fills UUID and current UTC timestamp. Public fields also include `conversation_id` and `project_id`; callers attach those identifiers when known. The `source` identifies the actual command, job, user request, file or receipt that established the event. The database normalizes timestamps to UTC and rejects conflicting reuse of an event ID.

Configuration uses the plan's camelCase JSON schema, with one additive field: `disabledPlugins: string[]`, defaulting to `[]`. All omitted configuration/appearance/MCP fields receive defaults. Unknown configuration fields are rejected. `McpServer` has required nonempty id/name, default enabled true, nullable command/url/bearerTokenEnvVar, args `[]`, env `{}`, startupTimeoutSec 30 and toolTimeoutSec 600. Verification is the validated string no/default/long/max.

## Settings actions and receipts

`app_control` get/status returns `{configuration, identity: "OpenCore", secretsRedacted: true}`. The local settings command reads `configuration()` directly; its env values remain editable in the user's own UI.

```json
{"action":"set","source":"conversation/settings-request","settings":{"compactAtTokens":180000,"verification":"long","appearance":{"fontSize":18}}}
```

A set recursively patches appearance fields, validates the entire resulting configuration and enabled plugin MCP transports, then persists the same EventStore setting the UI reads. Its receipt contains `persisted`, `changed`, redacted `configuration`, `changes: [{field,before,after}]`, UTC `timestamp`, and nullable `activityId`. A ledger failure returns a warning with the saved result, because the configuration write already succeeded. Concurrent configuration mutations are serialized.

Bounds: compactAtTokens 1024..3000000, repairAttempts 0..10, user prompt 32768 UTF-8 bytes, each source-directory list 32 entries, disabled IDs 512, explicit plus enabled plugin MCP servers 64, startup timeout 1..600 seconds and tool timeout 1..3600 seconds. Appearance defaults to dark, #7c5cff, system font, size 14, comfortable spacing, no reduced motion or high contrast. Font size is 10..24; accent is #RRGGBB; font family is a bounded plain name. Serialized configuration and tool arguments are each bounded to 1 MiB.

MCP servers require exactly one transport. Stdio uses command/args/env and rejects bearerTokenEnvVar. HTTP(S) uses URL/bearerTokenEnvVar, rejects stdio args/env, embedded URL credentials and fragments. IDs and server names are unique ignoring case; opencore is reserved. HTTP bearer values come from the existing app-server process environment. This module does not install authentication values into the host environment.

Tool views and receipts mask env values, secret command arguments and URL query values. Sending a previously returned redacted placeholder for an existing connection preserves its actual secret; a placeholder cannot create a new credential. The resulting MCP configuration intentionally contains actual local values for the runtime and must not be included in model prompts.

Use `redacted_tool_arguments()` before serializing tool arguments into timeline/ECHO or approval display text. It accepts the OpenCore MCP name prefixes and masks env values, secret flag arguments, URL credentials/query values (including URLs in args), opaque answer/answers fields, and secret field values/defaults. For user-input/MCP elicitation response method names, it also masks content/proof. Redaction happens before turning nested arguments into JSON strings; a generic transcript text redactor cannot discover ordinary credentials inside opaque JSON strings. This function returns a copy and grants no permission.

## Skills and portable plugins

Seven builtin skill bodies are compiled into the module from `resources/agent-platform/skills`: game, web, full-stack, mobile, desktop, MCP and plugin development. Initial instructions contain at most 48 enabled names/descriptions, each description limited to 220 characters. `skill_library` list returns metadata and discovery warnings; read loads one enabled skill by discovered ID. An arbitrary path is never accepted as a read identifier.

Custom skill roots are explicit absolute directories. Discovery reads bounded UTF-8 SKILL.md files, extracts YAML name/description, traverses at most four child levels and shares a 4096-entry budget. The library is limited to 256 skills and 128 plugins. Missing or malformed entries produce warnings. Metadata-only `skills()` and `plugins()` wrappers expose valid entries and each rejected plugin's warnings; tool list actions also expose general discovery warnings.

Plugin roots can be configured directly or as immediate children of a configured directory. Recognized manifest names are opencore-plugin.json, plugin.json, .codex-plugin/plugin.json and .claude-plugin/plugin.json. The latter locations support portable metadata only; installation hooks and arbitrary native plugin APIs are outside this format.

```json
{
  "id": "example-tools",
  "name": "Example Tools",
  "description": "A portable sourced-evidence skill",
  "version": "1.0.0",
  "skills": ["skills"],
  "mcpServers": []
}
```

Skill references are confined relative directories or SKILL.md paths. Parent traversal and canonical paths escaping the plugin root are rejected. Directory symlinks are skipped; leaf files are checked again when read. A portable relative MCP executable is resolved inside the plugin; runtime names such as node remain system command names. `plugin-file:bin/server.mjs` resolves an argument to a canonical confined file. Other ordinary arguments remain data for the configured runtime. Discovery never runs hooks, commands or installation scripts.

Enabled valid plugin MCP definitions enter the actual next app-server configuration. Disabling a plugin suppresses its skills and transports. Save rejects duplicate names across explicit settings and enabled plugins; the application bridge remains separately protected. A saved setting or successful discovery is not connection evidence: verify an actual runtime tool call after startup.

The example under `docs/examples/agent-platform-plugin` is a valid local skill-only plugin and performs no external action.

## Sourced evidence

`agent-platform.sqlite3` under app data stores activity and memory separately from EventStore and raw ECHO. It uses WAL, parameterized statements, indexes for timestamp/current scope/kind/key/version history, and FTS5 trigram indexes for activity and memory. The bundled rusqlite SQLite dependency enables FTS5. No operation edits model weights or raw ECHO.

`agent_memory` record requires key, content and source, with kind fact/lesson (default fact), scope (default global), and optional evidence. A transaction supersedes the previous active record with the same scope/kind/key and inserts a new UUID and increasing version. Current searches return active records; history retains source, timestamps, evidence and supersedes linkage. Results explicitly carry `evidenceOnly: true`. A source string is provenance provided by the caller, not independent proof that its assertion is correct.

Search queries remain literal substrings with at most 512 UTF-8 bytes, not SQL wildcard or FTS expressions. Query parameters are bound; `%`, quotes and SQL/FTS-shaped text have no special meaning. Queries of three or more Unicode characters use one escaped quoted FTS phrase to find candidates, followed by the former `instr(lower(document), lower(query))` exact substring check. This retains the former SQLite case behavior while avoiding a full history scan for longer rare terms. Queries of one or two characters use the compatible literal scan; empty queries return recent records. Returned rows remain limited to 100. Broad matches, short queries, sorting and the initial migration still have costs proportional to matching/stored data; no fixed latency bound or production-scale benchmark is claimed.

Activity search now covers its original summary/source/category/action sequence, event ID/time, conversation/project identifiers, and all primitive values and field names in sanitized bounded details. Job/model IDs, prompt text, settings values and artifact paths are therefore discoverable even when absent from the summary. Details are at most 128 KiB, each optional scope identifier at most 4096 bytes, and the search document at most 256 KiB. Full nested strings are flattened as actual text so JSON quoting does not conceal literal prompt substrings. Search never indexes masked credential values.

On the first open, an immediate transaction upgrades existing ledgers, fills activity search documents in batches of 32 events, builds both FTS indexes, and commits the schema version. It leaves original event JSON and raw ECHO bytes untouched. An interrupted/failed migration rolls back rather than exposing a partial search index. SQL triggers maintain indexes in the same write/delete statement or memory supersession transaction as the source rows. External-content indexes avoid another full content copy, and only active facts appear in memory searches; superseded facts remain in keyed history.

`app_control` activity searches the activity ledger. delete_activity and `agent_memory` delete remove only the selected derived row and return a dated persistence receipt. Memory deletion remains available for settings management when recall is disabled. Deleting a newer fact does not reactivate an obsolete version. Deleting a derived row never deletes raw ECHO. When activity is enabled, a deletion leaves a minimal deletion receipt rather than retaining the removed content.

## Verification record

Focused Rust test source was authored before implementation for invalid settings, persistence/receipts, connection-secret redaction, placeholder preservation, actual MCP transport conversion, reserved names, sourced versioning, literal query bounds, memory disable/delete behavior, plugin activation/collisions, progressive disclosure, and traversal. Additional tests cover sanitized argument/answer copies, indexed Studio detail/scope search, literal quotes and punctuation, index deletion/supersession integrity, and backfilling older activity/memory without rewriting events or ECHO. A Unix-only symlink test supplements portable traversal checks.

Per the authorized build restriction, local cargo tests and installer builds are deferred to GitHub Actions (`cargo test --lib`). Local rustfmt parsing/format checks verify syntax and formatting only. An in-memory SQLite 3.53.1 smoke check executed the actual source DDL and search SQL for detail/scope queries, punctuation/quote literals, memory supersession, deletion and external-content index integrity; it passed. This SQL check does not establish Rust compilation/test passes or equivalence to the shipped bundled SQLite build. Runtime connection success and model adherence to a skill also remain separate evidence. Builtin references require separate agent-use evaluation; runtime tests of body discovery are not skill-quality evidence.

Implementation rulings: use a separate derived ledger to preserve ECHO; expose disabledPlugins with serde defaults for real plugin control; support static portable plugin metadata instead of executing foreign hook/install formats; activate enabled portable MCP definitions in the runtime map as requested. Root integration emits UI events and provides approvals; this module never grants itself permission.
