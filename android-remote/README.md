# OpenCore for Android 3.3

Fresh installs open **Phone** mode without a PC or sign-in. Phone chats, model files, execution preferences and encrypted Hugging Face credentials are stored privately on Android. No model downloads or inference run at launch.

Version 3.2 fixes startup before native decoding has produced its first speed reading. Missing metrics serialize as zero rather than Android JSON's invalid NaN. The production snapshot serializer is tested against the real Android JSON implementation for fresh startup, saved-data payloads and unchanged real telemetry; no chat or pairing reset is required.

Version 3.3 starts a phone with no downloaded model in an explicit idle state. It does not select the PC's DuoCore default. Validated installed Native aliases and imported model IDs remain selected across reloads; stale or missing files recover to another installed model or idle. Chat Core checks whether its group service is available before opening workspace polls and actions. Its Phone view offers the existing execution-device chooser for the paired PC's real groups; Android does not yet host the group service itself.

Phone and Paired PC use the same original desktop OpenCore interface, tabs, composer, Approval/Effort controls, settings and model browser. There is no separate redesigned Phone interface. Phone ships a verified copy of the desktop frontend for offline use; Paired PC fetches that PC's current frontend. Only the execution backend and device wording differ. Android system bars, display cutouts and the keyboard inset the outer native container rather than cover app controls. The bundled desktop bytes are pinned by `shared-ui/manifest.json`.

In **Models**, browse the bundled OpenCore catalog or search Hugging Face by words or `owner/model`. The catalog and Hub search each have 100-result pages. Download a complete GGUF that fits the device. Each file is pinned to a commit, exact length and publisher SHA-256. The bundled ARM64 llama.cpp CPU runtime validates its architecture/template when it loads and streams native output, context/token counts and elapsed time. Stop interrupts native decoding; unload releases the model. Context/output/temperature settings are persistent. Phone inference currently supports compatible standalone text GGUFs, not arbitrary safetensors, image diffusion, vision projectors, split files or LoRA-only adapters. A model's size and runtime compatibility can require the PC.

**Settings → Execution device → Choose Phone or Paired PC** is available in both interfaces and on the PC connection recovery screen. Scan and approve a PC QR code to enable the PC option. Pairing alone does not select it. This choice is private to the phone and stays saved across restarts; connection failure never migrates a prompt to another device. Switching keeps separate phone and PC chats/models. Existing Android installations keep their signing identity and can be updated over the old APK without uninstalling.

## Paired PC mode

In Paired PC mode the Android client opens the same OpenCore interface as the PC, reshaped into a phone layout with a labeled bottom navigation bar. Chats, settings, models, tools, voice transcription, and generation commands run in the OpenCore app on the paired PC. Generated previews appear in the app; selected PC generated files can be opened on the phone. The phone does not mirror or control the Windows desktop.

1. Install and open `OpenCore.Remote.apk` on Android 8 or later.
2. Keep OpenCore open on the PC. In **Settings → Remote access**, choose **Reveal QR code**.
3. Scan it from the Android app and approve the PC name. The phone saves the pairing in Android Keystore and reconnects automatically.
4. Choose **Paired PC** in execution settings. Use the bottom tabs to open the same views as OpenCore on the PC. The PC must stay awake and OpenCore must remain running.

For access away from the home network, connect the PC and phone to the same private VPN before pairing. This direct connection does not use a cloud relay. Keep pairing codes private; removing the phone in OpenCore settings revokes its access.

The phone app requests camera access only to scan the pairing QR code. It requests microphone access only when voice capture is used. Android's document picker lets you attach files or send a bounded folder copy to the PC. The paired OpenCore desktop process handles commands, generated content, local model access, and app preferences; only OpenCore preference keys are synchronized.

Build with an installed Android SDK and JDK:

```powershell
python android-remote/build.py --sdk C:/path/to/Android/Sdk
```

The bundled native library is pinned in `native/runtime.json` and checked against `native/binaries.json`, including its source hashes and 16 KB ELF segment alignment. To rebuild it with the pinned NDK installed, run `python android-remote/native/build_runtime.py --sdk C:/path/to/Android/Sdk`, then rebuild the APK. The upstream MIT license is bundled. Native verification uses the identical JNI worker with real SmolLM2 weights on the host; it is not evidence of performance on a physical Android phone. Phone validation must include an offline local request as well as a paired-PC request.

Keep the signing key at `.opencore/distribution-keys` for compatible in-place Android updates. Release verification still needs a real Android phone to confirm pairing, a known chat request, and opening generated media.
