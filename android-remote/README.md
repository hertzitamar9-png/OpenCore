# OpenCore Remote

The Android client connects directly to the running Windows OpenCore app. The PC performs every operation. Its live app view is streamed over TLS, and generated media can be received by the paired phone. There is no OpenCore cloud account, analytics endpoint or payload relay.

1. Install `OpenCore.Remote.apk` on an Android phone running Android 8 or later. Open the phone app.
2. On the PC, open **OpenCore → Settings → Remote access** and turn access on. Keep OpenCore open and the PC awake.
3. For a first connection, put the phone and PC on the same Wi-Fi. Choose a suggested address on the PC and select **Create pairing code**.
4. On the phone, choose **Scan pairing code**, allow camera access and point the camera at the PC's code. If scanning is unavailable, open the pairing-text fallback on the PC, transfer the text to the phone and choose **Connect to PC**. The code expires after two minutes and can be used once.
5. The phone displays the same live OpenCore window. Tap to click, swipe to scroll and pinch to zoom. Select a text field on the PC view, then use **Type** to send text. **Files** opens generated outputs, **Game** shows game controls, and **Help** opens the interactive tour.
6. On the PC, **Your phones** shows connection status and lets you revoke access. You can also turn remote access off there.

The PC must remain awake with OpenCore open. Cellular/away-from-home access needs an existing private network route to the PC (for example a direct WireGuard VPN). This implementation does not provision a VPN, change router settings, expose an unauthenticated public service, or use OpenAI's remote-control infrastructure. A phone on cellular cannot reach a private home IP without that route. Windows Firewall may require the user to allow the selected port on their private network.

Pairing pins the exact PC certificate; the client rejects a different certificate even with the same IP address. Phone credentials are encrypted with Android Keystore, Android backup is disabled, and screenshots of the phone app are blocked. PC stores only hashes of paired credentials. Pairing text is single use and expires after two minutes. Remote access defaults off.

Only app-created output directories are exposed to Files. Models, databases, credentials and arbitrary paths are excluded. Images can be viewed in the app; other files can be opened in a user-chosen phone app. Complete games run on the PC through the live view so all their local assets are available.

Build using the official Android SDK and a JDK:

```powershell
python android-remote/build.py --sdk C:/path/to/Android/Sdk
```

The signing key is created once in the current user's `.opencore/distribution-keys` directory, outside the repository. Keep it for compatible APK updates. Its password is protected by Windows DPAPI. No signing credentials belong in Git.

Release verification must include an actual paired phone sending a known test message, viewing a generated image and opening a generated artifact. PC-only fixtures do not establish mobile-network reachability or real phone success.
