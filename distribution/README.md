# OpenCore Windows distribution

Every successful push to `main` runs `.github/workflows/build.yml`, verifies the current UI/runtime repairs, creates **OpenCore Setup.exe**, signs it with the existing Tauri signing key, and publishes a stable GitHub release plus `latest.json`.

The installer filename and caption do not contain a version. Internal release versions are `0.3.<Actions run number>` so all new releases are newer than existing `0.2.305` installations. Windows Apps shows the distribution version, and the app header compares that version with the signed update feed. Checking metadata never downloads or installs a package. The user opens **Update ready**, then chooses **Download update**. The app reopens after an updater installation.

Setup, uninstaller, app shortcuts and the browser extension use the official OpenCore logo. `sync-branding.py` copies the canonical 32/128px icon assets into the extension and refreshes the pairing page logo and favicon before packaging.

## Recovery boundary

The newer native checkout was lost before this recovery. The repository's historical native source was last published as 0.2.142 and cannot replace the newer installed native behavior. `baseline.json` therefore pins the verified original 0.2.305 native executable and clean runtime resources as GitHub release inputs. No chats, credentials, browser profiles, accounts, managed environments or downloaded model weights are included.

`recovered-dist/` is the exact recovered frontend source. `build-ui-repair.cjs` applies reviewed function-level patches, adds the managed Hugging Face extension, and emits the current frontend. `patch-executable.cjs` checks the original native hash, asset addresses, lengths, compressed budgets, and native catalog hashes before replacing only declared data slots. Native machine code remains unchanged. `resource-map.json` maps installed helpers to their canonical source in `src-tauri/resources/`, `reflex/` and `chrome-extension/`; runtime tests import those same files so a separate snapshot cannot drift from the shipped code. The original native executable's file version remains 0.2.305; the distribution version and commit are recorded in installed `build-info.json` and displayed by the current frontend.

Until the missing native source is restored, **the distribution sources in this directory are the frontend inputs used for release**, and `resource-map.json` selects canonical managed resources from the normal source folders. The historical `src/` and `src-tauri/src/` remain available for source restoration; edits there alone cannot alter the preserved native executable. The pipeline refuses native-source changes without a matching restored baseline rather than silently publishing an executable that ignores those changes. Do not switch the release back to compiling the older checkout without porting and verifying the missing newer functionality.

## Build

On Windows with Node, Python and NSIS available, install the root and `distribution/` npm dependencies, set `OPENCORE_RELEASE_VERSION`, then run `python distribution/prepare.py --inputs <new-input-folder>`. That fetches the pinned public inputs, verifies hashes, extracts to a disposable folder and overlays committed source. Use `distribution/package.py --help` to generate or compile the full installer. The workflow is the complete command reference.

The installer includes a clean Python 3.12.10 standard library and ensurepip, bundled Node helpers and the native inference engine. It installs Microsoft WebView2 if missing. Supported model-specific managed dependencies prepare when that model is installed. Model weights are separate downloads. Flint and Raon can be downloaded, but their publisher runtimes are not integrated into this build.

`/S /TEST /D=<new-absolute-folder>` extracts app and clean Python into an isolated verification directory. It skips user-profile runtime writes, registry, shortcuts, WebView2 installation, app launch and uninstaller creation. `qa-share-installer.py` compares all extracted files to the package manifest and runs the clean interpreter. The regular installer and uninstaller use the official logo from `src-tauri/icons/`.

Updater behavior follows the [Tauri updater protocol](https://v2.tauri.app/plugin/updater/). Release publication occurs only after the artifact signature and isolated extraction checks pass.
