# Models, phone access and temporary chat implementation plan

> Execute inline using the implementation skill. The user has asked to continue and finish without another design approval round.

**Goal:** Extend the installed OpenCore app while preserving its existing chats, native executable and permanent 483,328-byte bootstrap.

**Architecture:** Keep the recovered native app and extend its existing managed Python bridge. Freeze reviewed Hub metadata for built-in presets. Phone access uses a paired, encrypted direct connection to the same PC app; cellular access requires a reachable private-network address. Temporary chat uses an in-memory ECHO archive and a local model only.

**Tech stack:** Existing Python standard-library helper, React UI patches, native Java Android client, Windows window capture/input, TLS with certificate pinning.

## Constraints and review focus

- No cloud storage or payload relay, no chat/credential/weight deletion, no modification of external Python environments.
- Preserve every verified quant; never fabricate options or count drafts/projectors as models.
- Downloads and generation readiness are distinct. Unsupported architectures must state the actual requirement.
- Pairing expires, credentials are revocable, remote access defaults off, media paths cannot escape approved roots.
- A real phone message and media check are required before claiming end-to-end phone success.

## Tasks

- [x] Add safe Hub search with prefix/word mode, task/library/author/license/sort filters; regression tests cover anonymous/public and explicit private searches, hostile inputs and stale requests. Replace gray importer controls and expose token settings in the browser.
- [x] Add complete image pipeline, speech archive and LoRA plans; exclude standalone MTP drafts. Freeze requested repositories and verified exact-checkpoint conversions; retain Cyber-Tiel's existing family. Test immutable pins, completeness and no fake readiness.
- [x] Reproduce Phonon re-preparation. Reuse verified, unchanged managed runtime dependencies; do not auto-install speech during page mounting. Keep explicit repair and microphone failure paths. Test missing/corrupt/changed dependencies.
- [ ] Implement direct encrypted PC/phone pairing and app streaming, revocation, bounded input and generated-media access. Build a signed APK; test TLS/auth/replay/path traversal and actual capture. Verify on a paired phone when available.
- [x] Implement temporary chat with ECHO in memory, close/cancel cleanup and no native conversation persistence. Test that no archive, chat or request content is written to disk.
- [ ] Run relevant backend, rendered UI and packaging checks. Review changes and pin preservation. Push the authorized update and deliver the APK plus the unchanged permanent setup, with any unverified phone/network limits stated accurately.

## Verification status

The reviewer’s four actionable findings were fixed and re-reviewed: free remote port, actual native backend selection, cancellation before HTTP headers and unstarted streams, and private-search reset after token removal. Backend, rendered UI and native data-slot checks pass. The signed APK is built. Actual phone streaming/message/media and cellular VPN reachability remain pending user validation. Newly added image/speech packages retain explicit runtime requirements; downloading them does not certify their unsupported architectures.
