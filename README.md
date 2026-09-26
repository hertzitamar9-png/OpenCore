# OpenCore desktop app

This repository contains the OpenCore Windows desktop app, its browser extension, Reflex scripts, and the ECHO runtime scripts needed by the installer. It does not include language-model weights, Reflex weights, personal conversation data, or local installation files.

## Build locally

Install Node.js, Rust, and the Windows prerequisites for Tauri 2. Then run:

```powershell
npm ci
npm test
npm run build
Set-Location src-tauri
cargo test --lib
Set-Location ..
npm run desktop:build
```

The NSIS installer is written under `src-tauri/target/release/bundle/nsis/`.

## Build on each push

`.github/workflows/build.yml` tests and builds a signed Windows installer on every push to `main`, then publishes a versioned release in this private repository. Installed copies check the private release feed 12 seconds after launch and every five minutes. The updater downloads and installs a signed release only while the model runtime and chats are idle, then restarts OpenCore. Because the repository is private, the same Windows account must have GitHub CLI installed and authenticated with access to this repository (`gh auth login`). The updater keeps the credential in Rust memory and does not expose it to the webview. If GitHub CLI is missing or unauthenticated, OpenCore reports that private updates are unavailable.

The runtime looks for model assets under `OPENCORE_HOME` or the user's OpenCore installation directory. A successful app build verifies the desktop code and installer packaging, not model inference quality.

## Reflex Vision

Computer use sees the screen through Reflex Vision, H Company's Holo3.1-0.8B (Apache-2.0, 0.85B parameters, built on Qwen3.5-0.8B). Its weights are not in this repository; they live beside the main model:

```text
%USERPROFILE%\OpenCore\reflex\vision\reflex-vision-0.8b-q8_0.gguf
%USERPROFILE%\OpenCore\reflex\vision\reflex-vision-0.8b-mmproj-f16.gguf
```

The bundled `runtime\llama-server.exe` serves them on port 8816 only after a `desktop_use` or `reflex_use` call, and unloads them after two idle minutes. `reflex_use` uses it for `see`, `ground` and `ground_click`. Window captures skip the invisible resize border that click coordinates include, so `desktop_capture::frame_origin` shifts every image point into window coordinates.

- `reflex/eval_grounding.py` measures grounding on ScreenSpot-v2, ScreenSpot-Pro, OSWorld-G and the held-out Hebrew sets.
- `reflex/collect_web_grounding.mjs` and `reflex/collect_desktop_grounding.py` collect grounding data without clicking anything.
- `reflex/train_vision.py` fine-tunes the model with LoRA.
