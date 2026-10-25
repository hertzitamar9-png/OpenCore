# Permanent OpenCore Setup

`OpenCore Setup.exe` is the one reusable downloader. Keep these exact signed bytes across app releases. Its file version identifies the downloader itself; it does not choose or limit the app version.

On every launch it discovers the latest stable app through GitHub, downloads and verifies that signed package, and starts installation. Installed apps update through their own **Update ready** button. Users do not need to replace or download Setup for each app update.

`pin.json` records the existing verified artifact and signature. The release workflow copies them with `permanent_bootstrap.py`; publication rejects changed setup or signature bytes. It signs only the new full app package. The public download address stays `https://github.com/hertzitamar9-png/OpenCore/releases/latest/download/OpenCore.Setup.exe`.

Do not rebuild this file when changing the app version. `build-bootstrap.py` and its C# source remain available for separate, explicitly requested bootstrap maintenance and recovery. They are not part of automatic app releases.
