# TwinCore experimental Q6 runtime

TwinCore owns the complete **Nanbeige 4.2-3B** and **K2-Horizon-3.7B** Q6_K
checkpoints. Both decoders run at each generated step; a trainable bridge sends
feedback in both directions, uses both native heads and produces one next-token
distribution. **DuoCore** remains the separate candidate selection runtime.

## Current state

The Q6 decoder boundary, adapter trainer, artifact checks, streaming interface and
cancellation contracts are implemented. CPU fixtures and the shared GGML head
probe test their mathematics and lifecycle. The two exact local Q6 files have
been recovered; their content identities are in `q6_identity.py`.

An actual full-checkpoint CPU probe also ran both native towers at a 1,024-token
working capacity, produced finite gradients for all seven bridge parameter
groups, streamed the Hebrew letter alef, stopped at EOS and released both models.
This used initialized adapters and does not qualify GPU placement or coding
quality. The prepared corpus's 512 training and 64 validation examples passed
both real tokenizers: 107,500 target tokens, at most 368 per answer, and maximum
complete native prefixes of 500/436 tokens. No adapter training has run yet.

**TwinCore is not yet an activated app model.** Actual full-Q6 GPU loading,
adapter training, held-out behavior, the native server/Claude Agent SDK tool loop,
full HumanEval and then full public LiveBench are separate remaining gates.
The running LFM benchmark is not modified by this implementation.

The server requires a trained, hash-bound adapter and a resource qualification
for the requested GPU and execution configuration. There is no untrained or NF4
fallback. An initialized adapter, changed checkpoint/runtime/alignment, incomplete
AdamW state, or substituted numerical DLL is rejected.
The server checks the saved receipt, tensors, alignment, geometry and current
coupling sources on CPU before allocating either full checkpoint. A damaged,
stale or resume-only training state cannot trigger a full model load.

## Resource and precision accounting

- Complete pinned Q6 files: **7,757,006,368 bytes** combined. No second copies are
  created by the native build or training code.
- Native inventory counts **4,169,800,704 Nanbeige parameters** and
  **5,058,255,360 K2 parameters**, including complete embeddings and heads.
  Their total is 9,228,056,064; decoder size labels alone are not that total.
- Default experimental working context: **1,024 tokens**, with a **32–8,192**
  supported configuration range. The default conservative plan includes
  335,544,320 bytes for both 16-bit attention caches and a 1 GiB scratch reserve:
  **9,166,292,512 bytes** before loading. This estimate is not a measured fit.
- Both complete decoder matrix sets and both heads must be measured on the GPU.
  CPU fallback cannot qualify as a successful 12 GB run.
- The small bridge and AdamW state use CPU Torch; the native backend owns GPU
  memory. Numerical DLLs actually mapped by the process are checked by hash.
- Every output keeps at least **200,000,000,000 bytes** free on disk.

The `--recompute` profile clears attention state between steps. The native decoder
still needs temporary attention buffers during a forward pass. ECHO's persistent
archive/retrieval is separate; this runtime does not provide simultaneous
three-trillion-token attention or a three-trillion-token response.

## Training and qualification

Build against the existing pinned native source/import libraries:

```powershell
python scripts/fusion/build_native.py --probe
python -m pytest tests/fusion -q
```

Use an existing CPU Torch environment or the CPU dependencies in
`requirements-native.txt`. Resource preflight itself imports no tensor framework.
`qualify_native.py --preflight-only` can refuse before loading either checkpoint.
Actual qualification additionally needs both full checkpoint paths and measures
placements, a complete teacher-forced forward/backward probe and release behavior.
Each phase is saved before native work; an interrupted load/decode/release remains
unqualified. Confirmed native model release is required in the final receipt.

`train_native.py` requires that actual qualification, an exact corpus SHA-256,
explicit train/validation splits and a fresh adapter output. Only bridge weights
are trained. Native decoder features and previous-step feedback are detached;
gradients through the frozen head projection are exact for its dequantized linear
operation. This is **truncated feedback training**, not end-to-end base training.
Held-out token loss cannot establish general coding quality.

`scripts/fusion/run_training.py` runs those two stages sequentially. It stops
before training if resources are unavailable, the GPU changes, or the probe
does not confirm both complete pinned Q6 towers and their release. It keeps
separate console logs, the qualification receipt and a `sequence.json` status.
After the training child exits it checks the small CPU adapter tensors, exact
corpus/driver/qualification identities and completed epoch schedule. The final
status is `adapter_validated_unbenchmarked`; benchmark and app activation remain
separate gates. It does not queue another model or start a benchmark.

For example, from the app repository, with existing files and a fresh output:

```powershell
python scripts/fusion/run_training.py `
  --nanbeige "C:\path\Nanbeige_Nanbeige4.2-3B-Q6_K.gguf" `
  --k2 "C:\path\K2-Horizon-4B-Q6_K.gguf" `
  --corpus "C:\path\corpus.jsonl" --corpus-sha256 "<exact SHA-256>" `
  --output "C:\path\fresh-training-run" --context 1024
```

Add `--prepare-only` to save commands and current source/corpus identities
without querying the GPU, importing a tensor framework or starting a child.
Use a new output directory to execute later. A prepared manifest is not a
resource qualification or completed training artifact. If training fails,
its bounded `adapter.checkpoints` store is preserved; supply that path as
`--resume` with a fresh sequence output to recover. Interrupting the sequence
terminates only its own child process and retains earlier checkpoints.

Training saves a resume-only state every eight completed examples by default
(`--checkpoint-every`) and after the last example. Each state binds the adapter,
AdamW moments, exact sample order and shuffle RNG cursor. The store keeps the
last two successfully saved states; a failed write leaves the previous pointer
usable. Deletion is limited to verified directories created by that store, and
the 100 GB free-space reserve applies to each save.

To recover, pass either a saved step directory or its parent checkpoint store
to `--resume`, and use a fresh `--output` name. The corpus, epoch count, seed and
target budget must match the checkpoint. Completed examples are not repeated.
The resumed run must finish a new held-out evaluation before saving an adapter
that inference may load; an old validation snapshot cannot qualify that adapter.

The trainer checks every complete answer and both native prefix sequences before
decoder or optimizer work. Its default target budget is 512 tokens; smaller
budgets that would truncate the corpus are rejected. SentencePiece's standalone
dummy space is excluded without removing real answer indentation or line breaks.
Each checkpoint's stored Jinja template is rendered in an immutable sandbox,
including generation blocks. Formatted prefixes are tokenized without adding
duplicate BOS tokens. The current experimental stream uses the templates'
explicit disabled-thinking option for complete-answer supervision; it does not
claim a tested effort-dependent reasoning mode.

The native server runs as `python -m fusion.serve_fusion` from the resources
directory with `--nanbeige`, `--k2`, `--adapter`, `--qualification`, `--dll` and
`--runtime`. It binds only to localhost. It streams text deltas and parses explicit
reasoning sections when present,
returns registered structured tool calls after schema validation, and accepts
`POST /cancel`; client disconnect also requests cancellation. GPU cancellation
is cooperative between decode batches and head chunks; it does not promise
instantaneous kernel preemption. Current decoding is deterministic (`temperature=0`).

`/props` also reports the loaded adapter/qualification digests, full checkpoint
binding, measured GPU placement and actually mapped numerical libraries.
`scripts/evaluation/record_twincore_identity.py` verifies these against local
artifacts and compiled source before benchmark capture. Its source snapshots
include the native header and build file. This prepares reproducible evaluation;
it does not establish a trained model or a benchmark score.

The Transformers checkpoint manifest/reference remains for architecture tests.
It is not used by the Q6 server and does not make its optional dependency tests
evidence of actual full-model GPU qualification.

## OpenCore Fusion Qwen 9B + K2-Horizon 3.7B reference

`opencore_fusion_sources.json` pins a separate BF16 source pair. The tiny
Transformers contract test loads both model architectures from reduced configs
and exercises one coupled output step plus cached/full-prefix equivalence. The
shared `CoupledFusion` reference has bidirectional low-rank final-hidden
projections and a sparse exact-decoded-surface logit alignment; it emits one
stream from the first model's tokenizer. This bridge is initialized, not
trained. The pinned tokenizers have 128,094 exact single-token surface pairs:
51.59% of Qwen's output-logit rows and 51.11% of K2's. Those percentages measure
vocabulary-ID coverage, not how often aligned tokens occur in text. K2 currently
provides no projected logit evidence for unmatched Qwen token IDs, so this is
not full-vocabulary fusion. The source manifest binds the measurement to both
tokenizer hashes and records the method.

The full BF16 pair is about 29.4 GB before runtime memory, so it cannot fit in a
12 GB GPU. No production loader, trained adapter, app model entry, native 1M
profile, ECHO profile, HumanEval score, or LiveBench score exists for this pair.
Do not treat the tiny architecture/cache test as full-weight runtime, quality,
speed, or hardware-fit evidence.

## Source packaging

`scripts/publish-model-packages.py` includes the native headers and CMake file
in its deterministic source inventory. Importing that inventory performs no
remote operation; publishing happens only through the script's entry point.
Generated build/runtime directories and weight tensors remain excluded. The
experimental TwinCore HF package remains source only until trained artifacts
and the runtime/evaluation gates are qualified.
