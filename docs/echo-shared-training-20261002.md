# ECHO shared-expert training qualification

This is a bounded coding specialization of the previous UltraData candidate,
not a new foundation model or a claim of world-leading performance.

## Actual weight update

The complete-answer supervised run updates 4,258,704 parameters in twelve
existing BF16 arrays: the gate/up/down shared A/B factors and their composition
arrays. All private expert factors, backbone, router, MTP, workflow stages and
auxiliary heads remain frozen. Export preserves the original Q6_K/F32 backbone
and BF16 experts; it does not requantize or replace the source checkpoint.

Training uses FP32 optimizer masters and fresh BF16 compute views. Cached
autograd views are not reused across updates or gradient-checkpoint replay.
Complete response tokens, including EOS, are supervised at their preceding
prediction positions using the native prompt/decode expert routing.

| Recorded setting | Value |
| --- | ---: |
| Verified training functions | 96 |
| Complete validation functions | 16 |
| Held-out native coding tasks | 32 |
| Epochs / optimizer updates | 1 / 96 |
| Learning rate / batch size | 0.0003 / 1 |
| Prompt / answer limits, without truncation | 256 / 256 tokens |
| Active optimizer time | 519.625 seconds |
| Peak allocated training VRAM | 10,024,707,072 bytes |

The sample comes from `openbmb/UltraData-Code`, revision
`85182d829f2ce7ea07cca72ebfc509deea1d9f5f`, Python L3 shard 1/147.
Only one row group is range-read. Selection is frozen before model inference,
exact duplicate prompts/solutions and the earlier pilot sample are excluded,
and references must pass their literal contracts in the pinned offline grader.
Raw references, rejected examples and their verification receipts are retained.
This is not comprehensive pretraining or dataset decontamination.

## Local paired result

| Measurement | Previous candidate | Shared-expert candidate |
| --- | ---: | ---: |
| All contracts correct | 18/32 | 21/32 |
| Mean individual-contract reward | 0.576389 | 0.726563 |
| Complete validation cross-entropy | 0.213362 | 0.194406 |
| Native coding decode speed | 29.895 tokens/s | 29.837 tokens/s |

There are five paired task improvements and two regressions. This finite
same-source test measures local specialization, not broad coding superiority.
Lower loss alone cannot qualify the candidate; native accuracy, contract reward
and at least 20 tokens/s are mandatory. Automatic promotion is disabled.

## Runtime qualification

The one-thread runtime measured 18.006 tokens/s on its sustained coding probe
without unrelated GPU work. Four threads measured 25.799 tokens/s on that same
probe, with unchanged checkpoint, KV precision and CPU KV residency. All five
next-token top-1 results and their top-64 log probabilities matched exactly
between the two thread settings. The app's ECHO profile now uses four threads.

The training view passed all five native next-token probes. A separate
23-token continuation passed the existing decode/teacher-forcing parity gate:
mean selected-token log-probability error 0.027417, maximum 0.382300. These are
bounded parity checks, not proof of equivalence at every possible context length.

## Identity and evidence

- Original ECHO SHA-256:
  `261ef6c572bf9916f9ea5097bc156da0ee0ef6d631d52cf59dbcf293f416b7ae`.
- Previous UltraData candidate SHA-256:
  `4551c5333bb6287f0222e15a4d1e3a969df04cb7a69833125f5b3aa80239b91a`.
- Shared-expert candidate SHA-256:
  `688020b023c888b898c8014b239a984d33a5d5e6e4b5865d1c0f6e3d6b03f9c3`.
- Prepared complete data SHA-256:
  `e3458d5f3e7e7c9f6dd60e1f9dc4cee32b23dc744bad293bcfcb8eb235923384`.

The export changed twelve BF16 tensors and verified 465 other tensors unchanged.
Original and previous candidate hashes still match. Run evidence is outside Git
under `artifacts/training/echo-shared-20261002`: `shared-result.json`,
`shared-protocol.json`, `data-protocol.json`, native captures, parity receipts,
source snapshots and the resumable optimizer checkpoint. Model weights and
raw data are not committed.

## Complete public HumanEval result

| Checkpoint | Correct | Pass@1 | Median native decode |
| --- | ---: | ---: | ---: |
| Previous UltraData candidate | 130/164 | 79.2683% | 26.998 tokens/s |
| Shared-expert candidate | 129/164 | 78.6585% | 29.046 tokens/s |

Eight tasks improved and nine regressed. The paired exact McNemar p-value is
1.0; these results do not establish a broad coding improvement. The new weight
candidate is not promoted. The local 32-task improvement is reported separately
and does not override the complete public result.

All 164 responses from both checkpoints were scored in the same offline,
read-only, non-root Linux image with the unchanged pinned official Inspect
scorer. The previous candidate's regrade matches its earlier per-task result.
Positive controls passed 164/164 and negative controls passed 0/164. Both model
runs completed with zero evaluation errors. The new capture generated 43,927
tokens in 1,649.049 seconds; one response reached the output cap. The checkpoint
and both immutable ancestors were rehashed after capture and still match.

The matched protocol uses one answer per task, temperature zero, unchanged
runtime libraries and expert stages, identical prompts and chat template,
and 4,096 output tokens. It measures single-turn Python function correctness,
not long-history ECHO recall or complete coding agents. Timing measurements
come from separate runs and are not a controlled causal test of training speed.

Evidence remains outside Git under
`artifacts/evaluations/echo-shared-human164-20261002`: `comparison.json`,
`protocol.json`, the complete captures, official scoring logs, grader controls,
source snapshots and container isolation receipts. The input SHA-256 is
`54a2f8fba2824b111eda74e110d5b1354c134bebe2ed9fbfdfaa4e6aa0398b99`;
the grader image is
`sha256:ae9b065afe8207a4bb2974504d64085dca23459ebfd4fa30ed8ff9364c209ac6`.
