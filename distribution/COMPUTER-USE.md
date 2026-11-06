# Computer use in OpenCore

OpenCore retains the upstream Codex app-server agent loop. Cua Driver is an
optional upstream desktop driver, connected through MCP. This integration does
not install a plugin or change settings in the user's separate Codex, Claude,
DeepSeek, or ZCode applications.

Open **Settings → Computer use → Cua Driver** and choose **Install and connect**.
The reviewed Windows x86-64 package downloads 35,951,555 bytes and installs
98,194,894 bytes. The source revision, archive and individual file hashes live
in `cua-driver.json`; its MIT license accompanies the runtime configuration.
Installation progress is visible. Reconnecting reuses the installed runtime.

Enable computer use and allow the intended app in the same settings section.
The driver receives a capability manifest based on the owner's saved executable
permissions. App and window inventory is available; screenshots and input stay
bound to permitted applications. Script execution, arbitrary file access,
clipboard access, application termination, and whole-desktop input are not
exposed by this connection. Input is restricted to background delivery.

The adapter reads the owner's current permissions before tool calls and watches
for revocation. Stop, disconnect, cancellation, permission changes, and transport
shutdown stop the owned driver process. Input already dispatched to Windows
cannot be rolled back. Disconnection also removes this server from the app's
MCP configuration. Other servers and application permissions are retained.

The model receives the upstream schemas and canonical results, including
screenshots when requested. Screenshot use needs an image-capable inference
route; accessibility text can be used without one. A connected driver does not
make a text-only model understand images or prove an autonomous task succeeded.

## Verification

The packaged UI is exercised by `qa-computer-driver.cjs` at desktop and phone
widths. It checks explicit setup, byte progress, duplicate-install prevention,
reconnection, preservation of other MCP servers, and disconnection.

`test_computer_driver.py` covers executable permissions, foreground and desktop
input rejection, invalid downloads, permission revocation, cancellation of queued
requests, and transport closure. The native fixture in
`fixtures/cua-driver-window.cs` was also exercised through the actual upstream
Windows runtime and the production MCP adapter: a background UIA button action
changed its counter from 0 to 1, a separate executable was refused, and disconnect
blocked the existing connection. This is a driver integration test, not a model
coding benchmark or a guarantee that every application supports background input.

## Upstream references

- [Cua Driver](https://github.com/trycua/cua/tree/cua-driver-rs-v0.40.2/libs/cua-driver)
- [DeepSeek's Cua MCP integration](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/experimental/computer-use-cua-driver-mcp)
- [ZCode source-build scope](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/README.md)
