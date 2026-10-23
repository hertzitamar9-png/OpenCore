Download **OpenCore Setup.exe** below. The installer uses the OpenCore logo and includes Python, pip bootstrapping, bundled Node helpers and the native inference runtime. It installs WebView2 if needed. Model files and supported model-specific dependencies are prepared inside the app.

This release includes shared installation progress and duplicate-install protection, managed pip repair, the requested model quantizations, the Hugging Face importer and optional token setting, connector tab/control fixes, removal of the duplicate generic Phonon card, and the gray Session / GPU / VRAM / speed footer.

OpenCore checks for releases without downloading them. When an update is available, choose **Update ready**, then **Download update**. Updates preserve the installation folder and reopen the app. Your chats, credentials and model downloads are separate from the installer.

Runtime compatibility remains model-specific. Flint and Raon packages are download-only until their publisher runtimes are integrated. The current distribution preserves the verified 0.2.305 native baseline; see `distribution/README.md` for the source recovery boundary.
