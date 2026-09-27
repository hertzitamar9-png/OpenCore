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

`.github/workflows/build.yml` tests and builds a signed Windows installer on every push to `main`, then publishes a versioned release in this private repository. Installed copies check the private release feed 12 seconds after launch and every five minutes. The app shows actual download progress in its own window, then launches the signed updater quietly and reopens OpenCore automatically. Windows requires the running app to exit briefly so its files can be replaced; the separate installer window stays hidden. Updates are applied only while the model runtime and chats are idle. Because the repository is private, the same Windows account must have GitHub CLI installed and authenticated with access to this repository (`gh auth login`). The updater keeps the credential in Rust memory and does not expose it to the webview. If GitHub CLI is missing or unauthenticated, OpenCore reports that private updates are unavailable.

The runtime looks for model assets under `OPENCORE_HOME` or the user's OpenCore installation directory. A successful app build verifies the desktop code and installer packaging, not model inference quality.

## Chrome extension

Use the Chrome setup controls in OpenCore to open the bundled `chrome-extension`
folder and copy its pairing code. In Chrome's Extensions page, enable Developer
mode, load that folder as an unpacked extension, then paste the code into the
OpenCore Browser Control popup and select **Connect**. The extension controls tabs
in the Chrome profile where it is installed. Its debugger permission enables
page inspection, input, screenshots and DevTools evaluation.

The app saves its pairing identity in its local database, so future restarts keep
the same code. The popup reports **Connected** only after the authenticated socket
opens; failed handshakes and an eight-second timeout show an error. Upgrading from
a build that used temporary codes requires pairing once again. When extension
files change, use **Reload** on its Chrome Extensions card to load the new worker.

## Optional model library

Open **Models** to install, uninstall, or select a model. Nothing downloads on startup.
Downloads use immutable Hugging Face revisions, check file sizes and SHA-256 hashes,
and stop before free disk space falls below 200 GB. Shared weights are removed only
after the last installed variant is uninstalled. Chats and ECHO archives are preserved.
Private model repositories require an existing Hugging Face token (`HF_TOKEN` or
the token saved by `hf auth login`). Python is required for the local runtime scripts;
Whisper and Reflex additionally require their Python inference dependencies.

The four new profiles use two complete copies of the same pinned DavidAU LFM model:

| Profile | Inference | Active context |
| --- | --- | --- |
| DualCore KV | Independent drafts and blind cross-reviews | 131,072 |
| DualCore ECHO | Independent brains with prefix recomputation and archive retrieval | 32,768 |
| FusionCore KV | One coupled token loop through two full towers | 131,072 |
| FusionCore ECHO | Coupled loop with prefix recomputation and archive retrieval | 8,192 |

The supplied upstream repository has no BF16 release, so these profiles use the
authorized Q8_0 fallback. All four share one 3.12 GB weight file on disk; two weight
sets are loaded for inference. Their native context allocations and short generation,
streaming, and tool-call tests passed on a 12 GB RTX 4070. These checks do not qualify
near-capacity prompts or prove general coding quality. Full receipts are in
`tests/evidence/lfm-four-profiles-qualification-2026-09-26.json`.

FusionCore's hidden-feedback gate is untuned. It is an experimental coupled runtime,
not a newly trained dense checkpoint. ECHO makes an exact archive searchable beyond
the active window; it does not provide infinite simultaneous attention. The LFM ECHO
profiles recompute prefixes rather than retain a cache between token steps, but still
allocate transient attention/state buffers. Details and weight-license notices are
in `src-tauri/resources/lfm/README.md`.

DuoCore remains the K2 + Nanbeige candidate selector. The separate older TwinCore
PyTorch fusion path remains experimental and is not a ready selectable model.

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
