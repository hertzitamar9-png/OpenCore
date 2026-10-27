# OpenCore for Android

The Android client opens the same OpenCore interface as the PC, reshaped into a phone layout with a labeled bottom navigation bar. Chats, settings, models, tools, voice transcription, and generation commands run in the OpenCore app on the paired PC. Generated previews appear in the app; selected PC generated files can be opened on the phone. The phone does not mirror or control the Windows desktop.

1. Install and open `OpenCore.Remote.apk` on Android 8 or later.
2. Keep OpenCore open on the PC. In **Settings → Remote access**, choose **Reveal QR code**.
3. Scan it from the Android app and approve the PC name. The phone saves the pairing in Android Keystore and reconnects automatically.
4. Use the bottom tabs to open the same views as OpenCore on the PC. The PC must stay awake and OpenCore must remain running.

For access away from the home network, connect the PC and phone to the same private VPN before pairing. This direct connection does not use a cloud relay. Keep pairing codes private; removing the phone in OpenCore settings revokes its access.

The phone app requests camera access only to scan the pairing QR code. It requests microphone access only when voice capture is used. Android's document picker lets you attach files or send a bounded folder copy to the PC. The paired OpenCore desktop process handles commands, generated content, local model access, and app preferences; only OpenCore preference keys are synchronized.

Build with an installed Android SDK and JDK:

```powershell
python android-remote/build.py --sdk C:/path/to/Android/Sdk
```

Keep the signing key at `.opencore/distribution-keys` for compatible in-place Android updates. Release verification still needs a real Android phone to confirm pairing, a known chat request, and opening generated media.
