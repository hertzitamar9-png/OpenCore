# OpenCore AutoLoop core

Experimental, training-free **physical decoder-block repetition** integrated from the user-provided AutoLoop v0.2 ZIP. This package contains no GUI, drive scanner, external-runtime discovery, downloader, optimizer, PyTorch dependency, or training code. `SOURCE_README.md` is the retained historical upstream documentation, not the current integration instructions. `provenance.json` records the exact original ZIP and file hashes. The archive supplied no standalone license file; none is invented here. Original module notices are retained.

The manager must provide an owned output directory and its pinned OpenCore llama-server path. Defaults are CPU only, two threads, three candidates (maximum eight), 4K requested evaluation context, deterministic seed 1234 and 2,048 maximum answer tokens. The published GGUF context caps the requested evaluation window. No existing inference process is reused or stopped. This core was verified with miniature synthetic GGUFs, answer fixtures and process doubles, **not a measured real-model quality improvement**.

## API

```python
from autoloop_vendor import Engine, Settings, inspect_model, benchmark_schema

capabilities = inspect_model(model_path)  # header only; no hash/build/execution
settings = Settings(
    model=model_path,
    output_dir=owned_run_directory,
    llama_server=pinned_absolute_runtime_path,
    runtime_sha256=expected_runtime_sha256,
    gpu_layers=0,
    threads=2,
)
report = Engine(settings, progress=on_event, cancel=cancellation_event).run()
```

`Settings` requires `model`, `output_dir`, `llama_server`. Other fields are `benchmark=''`, `max_variants=3`, `context=4096`, `gpu_layers=0`, `threads=2`, `max_tokens=2048`, `startup_timeout=240`, `request_timeout=240`, `seed=117`, `experimental_damping=False`, `runtime_sha256=''`, `baseline_only=False`, `multimodal=False`, `vision_compatibility=None`, `companion=''`, `minimum_dev_net=2`, `minimum_holdout=16`, `max_holdout_regressions=0`. Timeout values are seconds. The manager supplies the overall job budget through its cancellation event. Calls are synchronous and belong on its background worker.

`inspect_model(path)` returns `file`, `bytes`, `architecture`, `blockCount`, `contextTokens` (unknown is `None`), `quantTypes`, `requiresVisionCompatibility`, `supported` and `reason`, plus legacy descriptive fields. Eligibility means only that structural surgery may be attempted, not that a checkpoint has loaded or improved. The seven allowlisted decoder names are `llama`, `qwen2`, `qwen3`, `mistral`, `gemma2`, `phi3`, `gemma3`. The parser rejects split GGUFs, unsupported architectures, heterogeneous block geometries, expert metadata/tensors and recognized recurrent/hybrid blocks. Plain repetition preserves tensor quantization bytes; mixed tensor precisions are permitted. Unsupported damping encodings fail explicitly.

Multimodal surgery is blocked when the caller marks a multimodal package or the header contains recognized vision metadata, unless manager-supplied `vision_compatibility` has `validated=True`, matching `sourceSha256`, `runtimeSha256`, and a nonempty `companionSha256` list containing the selected companion's real SHA-256. This is a trusted manager assertion, never an assertion taken from model text. It establishes package compatibility only; text scores do not establish image/video quality. `baseline_only=True` can evaluate text prompts on other runtime-supported GGUFs without attempting structural surgery.

`benchmark_schema()`, `load_questions(path)` and `validate_questions(list)` expose objective benchmark validation. Questions use bounded string `prompt`, string `answer`, and `kind` equal to `exact` or `number`. There must be 20–10,000 unique normalized prompts; numerical answers must be finite. The default 48 questions are elementary arithmetic, exact-answer facts and short reasoning. This is **not HumanEval, human evaluation, general coding evaluation or universal intelligence**.

## Evidence and acceptance

The development/holdout split is deterministic and disjoint. Development results choose one candidate; only that winner receives confirmation questions, once. Acceptance requires at least two net development wins, at least sixteen held-out questions, strict held-out improvement, the configured regression budget (zero by default), and a one-sided exact paired-binomial probability at most 0.05. A gain of one or four questions with no losses fails; five wins with no losses passes the significance boundary. This gate is evidence only for the frozen benchmark, not a guarantee outside it.

All completed answers and scores are durably appended to `answers.jsonl`, including partial cancelled/failed evaluations. The benchmark manifest has both its actual file-byte SHA-256 and canonical-content SHA-256. Full source/runtime hashes are checked before and after the run, and the frozen benchmark and answer journal are checked for changes. The original is never written, restored or deleted. Candidates live in an exclusive owned workspace and cleanup removes only successfully created, tracked files. Publication cannot overwrite an existing destination. Interrupted process recovery remains the manager's responsibility inside its owned run directory.

The progress callback receives JSON dictionaries with `schema`, `event`, `timestamp`, and event-specific fields. Events include `hashing-source`, `runtime-start`, `question` (`candidate`, `split`, `completed`, `total`, `correct`), `candidate-start`, `copy-progress`, and `candidate-evaluated`. Callback failures stop the run. Cancellation is checked during copying, hashing, startup and between questions; a runner monitor terminates only the child it created during blocked inference. The private loopback server uses an ephemeral API-key file, no shell, no proxy, and a restricted child environment. Diagnostics and exceptions redact that key and bound their tail.

The returned report and `autoloop-report.json` have status `accepted`, `rejected`, `baseline_complete`, `cancelled`, or `failed`. Invalid settings and receipt-name collisions can raise before the run starts. Important fields are `source` and `runtime` (`path`, `before`, `after`), `capabilities`, `evaluation`, `benchmark`, `rawResponses`, `baseline`, `variants`, `confirmation`, `runtimeSessions`, `acceptedArtifact`, `selected`, `error`, `decision`, and exact `acceptance` gates.

An accepted artifact contains `path`, `sha256`, `bytes`, `configPath`, `configSha256`, `sourceSha256`, `runtimeSha256`, `benchmarkSha256`, `architecture`, `inferenceVerified` and `qualityScope`. The manager must independently verify these identities, require actual inference proof, preserve the original context cap, and perform normal managed-runtime verification before registering a derived chat model. Synthetic injected test backends explicitly leave `inferenceVerified=False`; their accepted test artifacts are not proof of real model quality. Rejected or uncertain runs retain reports, not candidate weights.
