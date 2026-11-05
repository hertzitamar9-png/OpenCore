# Permanent Android shell and live web UI

Implements the source architecture requested in [OpenCore #23](https://github.com/hertzitamar9-png/OpenCore/issues/23). No APK was built, installed, published or downloaded while implementing this change. The committed `distribution/mobile/OpenCore.Remote.apk` remains the existing binary; it does not contain these new Java classes.

**Older installations need one native shell migration, using the same package and signing identity.** Java code, permissions, native libraries and bridge capabilities cannot be updated through web assets. Subsequent compatible HTML, JS, CSS, catalog presentation and PC-hosted feature changes use the same installed shell. New native inference architectures or Android permissions still require a deliberate APK update.

## Delivery and trust

- The default UI source is the existing paired PC. `Connection` verifies its pinned TLS certificate, sends the existing bearer credential and refuses redirects. Optional **Execution device settings → Interface update source** saves a separate HTTPS origin, certificate fingerprint and bearer token in encrypted Android preferences. It does not replace pairing or select a different inference device. No public service or cloud account is configured automatically.
- `GET /info` advertises `uiRevision`, the SHA-256 of the exact manifest bytes. Authenticated `POST /uimanifest` takes `{ "revision": "<sha256>" }`. Authenticated `POST /ui` takes `{ "revision": "<sha256>", "path": "assets/..." }`. The server rejects a changed revision rather than combining releases. A trusted assets-only HTTPS host must implement these three routes; it needs no chats, models, `/session` or inference service.
- Schema 1 manifests declare `shellApi: 1`, `modes: ["pc", "phone"]`, and each file's exact byte length, SHA-256 and MIME type. The manifest is authenticated by the pinned TLS connection; it is not an independently signed public release manifest. The revision hash and each file hash are checked before activation and when cached files are read. Unsupported shell APIs/modes and native/Dex/APK files are rejected.
- `distribution/mobile_ui.py` discovers the current build's JS/CSS chunks, including lazy imports, and includes the mobile web bridge and CSS. The complete bundle is bounded to 128 files, 8 MiB per file and 32 MiB total. Updating only one file without publishing its manifest cannot activate a partial revision.

## Cache, reload and reconnect

`LiveUiStore` writes only public UI resources under Android private `files/remote-ui/<mode>/<source-hash>/`. It stages and verifies every asset, fsyncs files, then atomically renames the complete revision and state file. Pairing, phone preferences, chats, credentials, downloaded weights and native libraries remain in their existing locations. Cache cleanup is confined to this store and does not follow symlinks.

`LiveUi` checks on foreground and about every 20 seconds while resumed. It keeps the current page on one complete verified bundle while another downloads. The web bridge confirms the page is ready and idle before activation; drafts, visible dialogs/menus/browser panels, pending commands and generation defer reload. Native checks also defer during Phone generation/loading/downloads and file/permission workflows. Activation opens a trial revision. `phone-ready` commits it as last-known-good; a startup failure or 45-second foreground deadline restores the previous working revision. A process killed during a trial rolls back on next launch. Rejected revisions are remembered to prevent a reload loop. Superseded staged bundles are pruned; the current and previous good revisions are retained. If no usable cache exists, the hash-verified bundled UI is used.

Paired PC reconnects replace the native message channel and restore event subscriptions **without reloading an already-ready page**. Unfinished commands are rejected and never replayed automatically. This retains the existing React document, composer text and attachments across a temporary disconnect. Stale session failures cannot invalidate a newer connection. A UI revision change uses the idle handshake and a fresh PC session so old events cannot enter the new document.

Legacy APK foreground watchers and background downloads/installations are disabled, including cancellation of the old persisted job. The web update action and the legacy `phone-app-update` bridge message both check UI assets only. Explicitly labeled native-shell maintenance buttons retain the manual signed APK update path for actual native changes. They are not the routine update mechanism.

## Modes and privacy

| Mode | Execution and state | What a live UI update can change | Offline |
| --- | --- | --- | --- |
| Paired PC | Existing paired PC chats, models, tools and inference; Android browser/device features still use the phone | Web presentation and features implemented by that PC backend, within shell API 1 | Cached/bundled interface opens; PC commands need the PC. No automatic execution-device fallback. |
| Phone | Existing phone chats, models, credentials and bundled JNI/LiteRT runtime | Web presentation and features supported by the existing `PhoneAppBridge` | Last good UI and compatible downloaded local models remain usable without the UI server. PC-only group/tool services remain unavailable. |

An optional HTTPS UI source does not receive a prompt, model file, chat archive or inference request from this updater. Its executable UI must be trusted as the paired PC is trusted; selecting it is an explicit user action. No data or inference is migrated to a VM or cloud.

## Reproducible host tests

On Windows with the existing JDK 21 and Android SDK installed:

```powershell
npm ci --no-audit --no-fund
npm run build -- --outDir distribution/.output/live-ui/candidate-dist
$env:OPENCORE_LIVE_UI_DIST = (Resolve-Path distribution/.output/live-ui/candidate-dist).Path
python -m unittest discover -s android-remote/tests -p test_live_ui.py -v
node --test android-remote/tests/mobile-shim.test.cjs
node android-remote/tests/live-ui-render.cjs
python -m unittest discover -s android-remote/tests -p test_phone_startup.py -v
$env:PYTHONPATH = (Resolve-Path src-tauri/resources/runtime-setup/hf-extension).Path
python -m unittest discover -s distribution/hf-extension/tests -p test_mobile_ui.py -v
python -m unittest discover -s distribution/hf-extension/tests -p test_mobile_bridge.py -v
python -m unittest discover -s distribution/hf-extension/tests -p test_remote_private.py -v
```

The integration uses the actual `LiveUiStore`, `Connection`, PC `RemoteHandler`, a temporary TLS identity and bearer authorization. It publishes two versions of a backend web feature, obtains different manifest/index hashes through unchanged compiled client classes, exercises rollback and offline reopening independently for both modes, rejects wrong credentials/certificates and verifies a private-data sentinel is unchanged. Its request ledger permits only `/info`, `/uimanifest` and `/ui`. The existing repository APK SHA-256 is checked before/after; the test never runs that APK. A desktop Chromium follow-up renders the cached original React application with the production shim, interacts with the changed feature and checks draft deferral using fixture native responses.

Artifacts: `distribution/.output/live-ui/evidence/results.json`, `rendered.json` and separate Phone/PC screenshots. These explicitly record `physicalAndroidVerified: false`. No APK request or installer invocation occurs in this host harness. This evidence proves the store, transport, backend and web behavior; **it is not proof of UI delivery to an installed Android APK**.

The existing `phone-ui.cjs` suite also passes with the committed Android shared UI at widths 360, 393 and 430 in both modes. On the freshly built desktop frontend its older navigation test times out looking for a `More` button. The new live-update feature/rendering test passes against that freshly built frontend; the broader suite's navigation mismatch remains to be updated separately.

## Remaining Android acceptance

No Android device/emulator was available in this checkout. Before release, use an isolated Android test installation containing the migrated shell, with the original package/signing identity. Record the installed APK path/hash and versionCode **after the one-time migration**, then establish pairing and known chats in each mode. For ordinary updates after this baseline:

1. Publish UI revision A, interact with it, then publish revision B on the PC while the APK stays installed. Record both manifest/index hashes, screenshots and the installed APK hash/version again. Confirm B appears automatically on foreground/idle without an APK request, install dialog or PackageInstaller session.
2. Repeat separately in Phone and Paired PC. Verify a known on-device request while offline in Phone mode and a known PC request in Paired PC mode. Check the actual chats, pairing and model files are unchanged. Browser fixtures do not establish native inference behavior.
3. Keep a draft, attached files and a running request during an update; verify reload defers. Disconnect/reconnect the PC; confirm the draft remains and no command executes twice. Switch modes and return from background during an update.
4. Interrupt asset transfers, tamper with an asset, publish an incompatible manifest and a revision that fails startup, and kill the process during a trial. Confirm offline last-known-good/rollback, no revision loop and no mixed assets. Test older PCs without the new UI routes and an explicitly configured HTTPS assets-only source.
5. Inspect network and Android installer/package events throughout this interval. The manual native maintenance action must not be invoked. Repeat after denying notification/install permissions; routine UI updates must still work.

Only that device-side run can complete the unchanged-installed-APK acceptance requirement. This branch intentionally does not build or publish the one-time migration APK.
