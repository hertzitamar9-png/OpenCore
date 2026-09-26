# TwinCore experimental runtime

This module is the start of the **single-stream coupled TwinCore** path:
Nanbeige 4.2-3B and K2-Horizon-3.7B both run on every token step, exchange
hidden-state feedback, and contribute to one next-token distribution.

The existing **DuoCore** runtime is separate: it runs two candidate answers and
selects one. Its code, telemetry, and app label now use DuoCore consistently.

TwinCore is not a ready selectable model yet:

- The pinned Nanbeige checkpoint is not installed locally. Its verified source
  is about 7.76 GiB; this change did not download it.
- The bridge is randomly initialized and untrained. A short smoke test cannot
  establish answer quality.
- The active context limit is an experimental 1,024-token default, with an
  8,192-token code limit. The reference decoder recomputes full prefixes and
  has no production KV cache or ECHO integration.
- The HTTP server binds only to localhost and is not wired into the app's model
  selector. Do not advertise it as a finished or fused-quality model.

The checkpoint manifest pins the public Nanbeige model code and weights at an
immutable revision. Its LFS files are checked with SHA-256; small repository
files are checked against their Git blob SHA-1 before custom model code loads.
