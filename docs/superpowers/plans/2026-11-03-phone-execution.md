# Phone execution implementation plan

> Execute inline using superpowers:executing-plans, with test-driven-development. Prior user authorization covers work on main and publishing updates.

**Goal:** make Android usable offline on first install and persist an explicit Phone or Paired PC execution choice.

**Architecture:** preserve the PC MobileActivity and secure pairing. Add an independent local Java/WebView backend with private SQLite chats and verified Hugging Face GGUF downloads, a pinned bundled llama.cpp native library, and common execution settings.

**Tech stack:** Java 8 Android API 26+, JNI/C++17, llama.cpp, SQLite, WebView, Node browser QA, Python packaging.

**Spec:** ../specs/2026-11-03-phone-execution-design.md

## Global constraints

Preserve APK identity/signing key, phone pairing and installed PC data. No automatic PC/remote fallback. No universal compatibility or phone-test claims without evidence. No running downloaded repository code. Bound allocations/network responses; keep tokens on the selected device. The Windows permanent Setup stays unchanged.

## Review focus

- App recreated mid-generation: no dangling callback or stale stream; release native memory safely.
- PC unavailable or pairing revoked: allow Phone choice without destructive re-pairing.
- Large, split, auxiliary or hostile Hub files: validate pin/path/size/hash and explain unsupported packages before download.
- Cancel/retry/delete races: one owner per native model/download and no partially installed files.
- Device setting and credentials: never synchronize routing or phone token to PC preferences.

## Tasks

- [x] 1. ExecutionPolicy and GGUF metadata tests fail then pass; implement exact routing, bounds and safe file identity.
- [x] 2. Native runtime: pin source/hash/license, cross-compile Android arm64, implement cancellable token streaming and truthful native/template/context errors; test real neutral generation.
- [x] 3. Phone backend: private chats, encrypted Hub token, pinned model discovery, cancellable verified install and safe unload/delete; test critical lifecycle and admission behavior.
- [x] 4. Phone UI: accessible Chats/Models/Settings, native bridge only for trusted app assets, live token stats, persistent explicit routing; browser-check phone widths and offline launch.
- [x] 5. Integrate existing pairing and remote UI settings; tests for default Phone, preserved PC pairing, no silent reroute and revocation.
- [ ] 6. Build signed APK with original certificate, verify native/assets/source receipt, run client and regressions, publish through main/release and verify public APK/feed. Report untested physical-device behavior explicitly.

Verification and review: 517 Vitest assertions and 56 Node regressions pass. Android native JVM routing/metadata, ELF/hash/page alignment, catalog identity and mobile shim tests pass. Bundled browser flow passes at 360/393/430 px, including failure/cancel/retry and duplicate-submit protection; the original remote PC interface passes with its execution-settings control. The identical JNI worker generated 31 real tokens from pinned SmolLM2 and passed Stop, context-overflow and unload checks on Windows. Fresh review found and verified fixes for unavailable-PC fallback, stale observer replies and disabled quant controls. The signed APK is verified against the prior certificate and its embedded source map matches the reviewed Android files. Physical Android inference remains a required external device test; no phone throughput/thermal claim is made.
