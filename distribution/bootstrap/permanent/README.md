# Permanent OpenCore Setup

`OpenCore Setup.exe` is the one reusable downloader. Keep these exact executable bytes across future app releases. This replacement checks installed versions and rejects releases older than 0.3.243, which could recreate the external Claude bridge.

On launch it discovers the latest stable release through GitHub. For a fresh installation it downloads and verifies the signed full package. For an older installed app it downloads and verifies the signed incremental updater. When the app is current it offers to open it without downloading again. If the installed version cannot be determined safely, it offers to open the app instead of reinstalling it. Installed apps can also update through their own **Update ready** button. Users do not need to replace Setup for each app update.

`pin.json` records the exact executable. The release workflow copies it with `permanent_bootstrap.py`, signs it in CI with the existing update key, and checks the resulting signature. Publication rejects changed executable bytes or an invalid signature. The public download address stays `https://github.com/hertzitamar9-png/OpenCore/releases/latest/download/OpenCore.Setup.exe`.

Do not rebuild this file when changing the app version. `build-bootstrap.py` and its C# source remain available for separate, explicitly requested bootstrap maintenance and recovery. They are not part of automatic app releases.
