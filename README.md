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

`.github/workflows/build.yml` tests and builds a Windows installer on every push to `main`. Each successful run publishes a new versioned release in this private repository. GitHub access is required to download those releases. Installed copies do not yet install private updates automatically; that requires an authenticated updater endpoint.

The runtime looks for model assets under `OPENCORE_HOME` or the user's OpenCore installation directory. A successful app build verifies the desktop code and installer packaging, not model inference quality.
