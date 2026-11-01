# Permanent OpenCore Setup

`OpenCore Setup.exe` is the one reusable downloader. Keep these exact executable bytes across future app releases. This one-time replacement rejects releases older than 0.3.243, which could recreate the external Claude bridge.

On every launch it discovers the latest stable app through GitHub, downloads and verifies that signed package, and starts installation. Installed apps update through their own **Update ready** button. Users do not need to replace or download Setup for each app update.

`pin.json` records the exact executable. The release workflow copies it with `permanent_bootstrap.py`, signs it in CI with the existing update key, and checks the resulting signature. Publication rejects changed executable bytes or an invalid signature. The public download address stays `https://github.com/hertzitamar9-png/OpenCore/releases/latest/download/OpenCore.Setup.exe`.

Do not rebuild this file when changing the app version. `build-bootstrap.py` and its C# source remain available for separate, explicitly requested bootstrap maintenance and recovery. They are not part of automatic app releases.
