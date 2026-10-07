# Installed updater relaunch regression

The installed 0.2.128 updater downloaded and installed signed release 0.2.133.
The downloaded installer digest matched the published release asset, but no OpenCore process
remained after installation. Starting the shortcut opened a usable 0.2.133 app.
The actual app-context startup log contains the manual launch at 12:38:08 UTC
and no earlier 0.2.133 launch. Desktop-host reads of LocalAppData returned an
older redirected log; the verification worker collected the real log.

Tauri's NSIS template delegates its silent `/R` relaunch to `RunAsUser`. Add a
current-user postinstall launch after resource and registry writes. It observes
the existing silent/passive and `/R` choices, preserves `/ARGS`, and invokes the
quoted executable directly. OpenCore's existing desktop entry guard and
single-instance handling still apply. Interactive installation keeps its normal
finish-page launch choice.

The real installed worker completed after closing the window. Reopening reused
the same process and window. A second 60-second worker was interrupted after
27.6 seconds by application exit; its command process and Python descendant
both stopped. The follow-up installer must still be packaged in GitHub Actions
and verified through the installed updater before the relaunch is called fixed.
