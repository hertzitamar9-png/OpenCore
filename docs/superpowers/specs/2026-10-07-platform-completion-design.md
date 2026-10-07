# OpenCore release completion

The user authorized completing every gap identified in the 0.2.128 audit and shipping the result. Preserve the installed application's appearance, conversations, projects, models, source folders and existing settings. Compile native code and package the application only in GitHub Actions.

## Deliverables

1. Port executable-scoped computer permissions and precise Chrome DOM actions from the older unpublished branch. Enforce revocations at dispatch, keep the static aligned activity border and existing foreground guard, and expose explicit enable/stop and browser reconnect controls. Unsupported host controls report their actual capabilities; foreground interaction remains an explicit choice.
2. Separate memory mode and quantization/package selection without merging genuinely different equal-precision packages. Keep installed-category filtering and both Phonon runtime precisions. Port useful uncommitted streaming and connector verification changes without reverting subsequent release fixes.
3. Side chat inherits later parent messages before each turn. Preserve independent branch messages and settings. Send each new inherited delta to the actual persistent agent thread; acknowledge it only after turn admission succeeds.
4. Supply a cancellable managed runtime setup pipeline, pinned supported recipes, dependency and hardware diagnostics, and durable setup receipts. Distinguish dependency readiness from recorded successful inference. Provide VM/device setup operations using official tools, explicit license acceptance and available host virtualization. No readiness claim substitutes for a successful process or inference result.
5. Keep scheduled jobs running when the main window is closed using a visible tray agent, optional user-scoped Windows login startup, one application instance and an explicit Quit action. Hidden startup must not flash or steal focus. Updates must still cancel owned processes and restart safely. This is a per-user background process in the signed-in session, with no administrator/system service requirement.
6. Preserve generated output content as immutable streamed snapshots, including large media. Backfill all surviving recorded outputs once, expose paging and coverage, and preserve source references when disk space prevents a copy. Historical content without any surviving copy is reported unavailable.
7. Reduce Phonon repeat startup using prepared runtime/standby behavior while preserving selected precision, saved preference and cancellation. Record measured latency rather than promise instantaneous loading.
8. Publish a release only after frontend/native tests, layout/desktop guards and integration gates pass. Verify installed update, tray worker continuation, model selection, setup progress, chat context synchronization and file history. Record supported model/runtime coverage honestly; hardware/license-constrained or absent architectures cannot be described as verified.

## Architecture and alternatives

Use the existing single-instance Tauri process as the background agent, with its window hidden and a tray menu. A separate system service would run under a different account and complicate GPU, desktop, credentials and approvals. A second unmanaged scheduler risks duplicate claims and database/process ownership, so retain one core and one scheduler.

Retain the current durable job and file stores. Add small dedicated modules for background lifecycle and managed setup instead of a competing harness. Keep context synchronization append-only and source tagged. Large media snapshotting streams through SHA-256 rather than buffering the entire file.

## Verification boundaries

Windows app accessibility, OS license requirements, missing model implementations and lost historical bytes are external constraints. Improve available paths and report precise prerequisites; never invent universal input, recovered content or inference evidence. A catalog listing, successful import or build is not a completed generation.
