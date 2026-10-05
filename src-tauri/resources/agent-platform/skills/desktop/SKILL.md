---
name: desktop-development
description: Use when building or changing native desktop applications, Tauri commands, durable settings, Windows integration, or desktop testing.
---

# Desktop development

Inspect the application's state ownership and platform boundaries. Keep commands typed and validate them in the backend. A setting change should persist, return a concrete receipt, and update the visible control from authoritative backend state.

Use app_control for OpenCore's supported agent settings. Custom instructions add to the application's OpenCore identity and permission rules. Appearance changes remain validated structured values. Approval choices continue through the application's approval bridge.

Keep credentials in the local settings surface and redact them from agent reads and receipts. Preserve models, projects, raw ECHO history, and active work when changing configuration. Keep background workers and GPU reservations tied to durable jobs and release them when work ends.

For Windows filesystem operations, use exact paths and native PowerShell operations. Confirm the resolved target stays inside the intended directory before recursive removal or moving. Launch background helpers with hidden windows; launch a user-requested interactive app visibly.

Verify the affected path in the actual desktop application or configured testing PC. Test persistence by reopening the settings store or restarting the app when the path requires it. Report build results, UI results, and unavailable test devices separately. Package installers through the project's approved build workflow.
