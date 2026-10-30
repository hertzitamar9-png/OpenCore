# OpenCore desktop app

This repository contains the OpenCore Windows desktop app, its browser extension, Reflex scripts, and the ECHO runtime scripts needed by the installer. It does not include language-model weights, Reflex weights, personal conversation data, or local installation files.

## Install

Download the Windows installer from [OpenCore releases](https://github.com/hertzitamar9-png/OpenCore/releases/latest). Model downloads are selected inside the app after installation.

**OpenCore Setup.exe** uses the official OpenCore logo and includes Python, pip bootstrapping, Node helpers and the native inference engine. It installs WebView2 when needed. Supported model-specific dependencies prepare inside the app when a model is installed.

## Build locally

Follow the current [Windows distribution build instructions](distribution/README.md). The release workflow is the complete reference for preparing the pinned native baseline, building the recovered frontend, testing the bundled runtime and creating the branded NSIS installer.

The newer native checkout was lost. The current distribution preserves the verified 0.2.305 native baseline and builds the recovered frontend, catalog and managed runtime changes from committed sources in `distribution/`. The historical native source in this repository must not replace the newer baseline until its missing changes have been restored. The distribution instructions explain this recovery boundary and the source files used by releases.

## Build on each push

`.github/workflows/build.yml` builds the new app package on every push to `main`, reuses the exact permanent **OpenCore Setup.exe** and its signature, then publishes the completed GitHub Release after verification passes. Download Setup once and keep it: it always retrieves the latest app when opened, while installed copies update inside OpenCore. Installed copies check the public update feed on startup and every five minutes. They display **Update ready** when a newer distribution is available. Press **Update ready** once to download and install. That same button shows **Updating…** and blocks repeat clicks while the update runs. Checking for updates never downloads or installs a package. The app closes briefly while Windows replaces its files and then reopens. Chats and local model files are preserved. Public releases need no GitHub account or CLI. The installer title and filename stay unversioned; internal versions order updates correctly.

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
and stop before free disk space falls below 100 GB. Shared weights are removed only
after the last installed variant is uninstalled. Chats and ECHO archives are preserved.
Public Hugging Face repositories need no token. For private or gated repositories,
add a read token in Settings; your Hugging Face account must have access to the model.
Core Python is bundled, and supported model-specific dependencies prepare automatically.
Runtime compatibility depends on the model and device. Compatible Flint, Raon and
LFM3 GGUF packages prepare their pinned Windows engines and verify generation after
installation. The five LFM3 checkpoints share 75 real package choices; specialized
publisher formats retain their actual platform requirements. ECHO text generation
tests do not certify audio or video execution. Computer-use checkpoints have their
own local next-action prediction interface and real inference checks; see the
distribution instructions for their scope and verification receipts.

The four new profiles use two complete copies of the same pinned DavidAU LFM model:

| Profile | Inference | Active context |
| --- | --- | --- |
| DualCore KV | Independent drafts and blind cross-reviews | 131,072 |
| DualCore ECHO | Independent drafts and reviews with archive retrieval; incremental F16 KV while decoding | 131,072 |
| FusionCore KV | One coupled token loop through two full towers | 131,072 |
| FusionCore ECHO | Coupled loop with archive retrieval; incremental F16 KV while decoding | 131,072 |

The supplied upstream repository has no BF16 release, so these profiles use the
authorized Q8_0 fallback. All four share one 3.12 GB weight file on disk; two weight
sets are loaded for inference. Their native context allocations and short generation,
streaming, and tool-call tests passed on a 12 GB RTX 4070. These checks do not qualify
near-capacity prompts or prove general coding quality. Full receipts are in
`tests/evidence/lfm-four-profiles-qualification-2026-09-26.json`.

FusionCore's hidden-feedback gate is untuned. It is an experimental coupled runtime,
not a newly trained dense checkpoint. ECHO makes an exact archive searchable beyond
the active window; it does not provide infinite simultaneous attention. The LFM ECHO
profiles retain incremental F16 KV state while generating and clear request state
between turns; they do not recompute the full prefix at every token. Details and
weight-license notices are
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
