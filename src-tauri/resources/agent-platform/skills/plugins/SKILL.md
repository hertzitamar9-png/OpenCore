---
name: plugin-development
description: Use when creating, adding, disabling, or inspecting a portable OpenCore plugin that supplies SKILL.md references or MCP servers.
---

# Plugin development

A portable plugin is a directory with an opencore-plugin.json manifest and optional skill files and MCP transports. Include a stable id, display name, description, version, relative skills paths, and mcpServers definitions. Custom SKILL.md files have YAML name and description metadata, then their instructions.

Discovery is read only. OpenCore reads bounded UTF-8 files and follows confined relative references. It never runs installation hooks. Parent traversal, absolute plugin file references, and symlink escapes are rejected. Keep executable plugin paths inside the plugin directory; system runtime names such as node may remain command names. Mark a confined script argument with plugin-file:path/to/server.mjs.

Add the plugin directory through the approved app_control setting change. Inspect skill_library plugins and its warnings, then load a skill by its discovered id. Enabled valid plugin MCP transports are merged into the actual next Codex app-server configuration. Disabling the plugin suppresses both its skills and its MCP transports. Configuration conflicts require correcting the server names before saving.

Verify behavior with controlled files: valid metadata appears, an enabled skill body loads on demand, rejected references cannot expose external files, and discovery creates no command side effects. Test the real transport separately after connection. Preserve external licenses when distributing copied code or resources.
