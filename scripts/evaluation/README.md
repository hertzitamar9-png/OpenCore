# Reproduce OpenCore model evaluations

These tools capture actual local API answers and replay them through unchanged
official scorers. They do not start models, download model weights, or measure
the desktop agent harness. A prepared dataset or a passing control fixture is
not a model score.

## Qualified datasets and environment

- HumanEval: all 164 tasks, dataset revision
  `7dce6050a7d6d172f3cc5c32aa97f52fa1a2e544`.
- LiveBench: all 1,000 tasks in the dated public 2024-11-25 release, six
  categories. The historical 2024-07-26 release is also accepted for replay.
  Current private questions and agentic tasks are outside this scope.
- Python 3.12, Inspect AI 0.3.270, Inspect Evals 0.22.0. Task source hashes and
  dataset revisions are checked before preparation. Official Python sources are
  also checked against installed wheel RECORD hashes and bound into the dataset
  provenance, including scorer helpers outside the task module. LiveBench's external scorer
  is pinned to upstream commit `1c4c65530fb69f797f7e6101c367b51c05f8cb64`.
- Linux is required for unchanged LiveBench coding grading (`SIGALRM`). The
  recorded runs use WSL Ubuntu 24.04 and `sandbox="local"`.

Create a CPU grading environment and install the pinned dependencies:

```sh
python3.12 -m venv .venv-evaluation
.venv-evaluation/bin/python -m pip install -r scripts/evaluation/requirements.txt
.venv-evaluation/bin/python -m pip install --no-deps 'livebench @ git+https://github.com/LiveBench/LiveBench.git@1c4c65530fb69f797f7e6101c367b51c05f8cb64'
```

LiveBench is installed with `--no-deps` because only its CPU scorers are needed.
Its optional model-training and agent dependencies are not part of this workflow.

## Prepare prompts

Run from the repository root. Preparation may fetch the pinned public datasets
into the Hugging Face dataset cache. Set `HF_HUB_OFFLINE=1` to use existing cached
copies. No canonical answers or tests are sent to the inference endpoint.

```sh
.venv-evaluation/bin/python scripts/evaluation/prepare_benchmark.py --benchmark humaneval --output scripts/evaluation/runs/humaneval
.venv-evaluation/bin/python scripts/evaluation/prepare_benchmark.py --benchmark livebench --release-date 2024-11-25 --output scripts/evaluation/runs/livebench-20241125
```

Preparation writes prompts, prompt hashes, tests/target/metadata hashes, and
source provenance. It rejects missing tasks, duplicate IDs, changed versions,
changed source files, and partial category sets. It refuses to overwrite existing
evidence. Recorded historical runs must retain their original input/provenance
files; newly prepared files have a new binding and must not replace them.

## Capture a loaded local model

Capture and identity recording run on the host of the loaded model, with access
to its actual local artifact paths. For the Windows desktop runtime, use Windows
Python for these two steps; preparation and grading can run in WSL. The example
below assumes the endpoint and model files are accessible to the calling host.

An identity JSON must name the actual API model and set `evidence_kind` to
`real_model`. Its `artifacts` and `runtime_files` list actual local paths,
byte sizes, and SHA-256 hashes. Retain checkpoint repository/revision/precision,
the launch configuration, and the number of candidates/towers in that identity.
For LFM profiles, `record_lfm_identity.py` verifies the pinned checkpoint hash,
queries the active profile, and records the runtime files. Pass `--resources`
when the endpoint uses installed app resources instead of this repository.

```sh
python scripts/evaluation/record_lfm_identity.py --profile fusioncore-kv --checkpoint /actual/model/path/LFM2.5-2.6B-Q3.8-TBrilliance-NEO-MAX-Q8_0.gguf --url http://127.0.0.1:18610 --output model-identity.json --resources /actual/runtime/resources
```

The caller must identify the resources actually used to launch that endpoint;
this recorder does not attest arbitrary remote processes. A model name alone
is insufficient. Recording source hashes performs CPU file reads, not GPU
generation.

For a qualified **TwinCore Q6** endpoint, use `record_twincore_identity.py`.
It checks both complete GGUFs, the trained adapter and fresh validation receipt,
the matching full-GPU qualification, actual loaded numerical libraries and GPU
placement, and source files against the compiled native build. The endpoint's
`/props` must disclose those identities. It rejects unfinished training
checkpoints and imports no tensor framework or model weights into memory.

```sh
python scripts/evaluation/record_twincore_identity.py --profile twincore-kv --nanbeige /actual/models/Nanbeige_Nanbeige4.2-3B-Q6_K.gguf --k2 /actual/models/K2-Horizon-4B-Q6_K.gguf --adapter /actual/trained-adapter --qualification /actual/full-gpu-qualification.json --dll /actual/native/twincore.dll --runtime /actual/numerical-runtime --resources /actual/runtime/resources --context 8192 --url http://127.0.0.1:18620 --output twincore-identity.json
python scripts/evaluation/snapshot_model_source.py --identity twincore-identity.json --output twincore-source-snapshot
python scripts/evaluation/benchmark_capture.py --url http://127.0.0.1:18620 --model opencore-twincore-q6-kv --inputs scripts/evaluation/runs/humaneval/humaneval-inputs.json --identity twincore-identity.json --output scripts/evaluation/runs/twincore-kv/captures --max-tokens 4096
```

Use the exact context/rank/seed qualified by the GPU receipt; this example does
not establish an 8K fit or a larger context capacity. Select `twincore-echo` and
its corresponding recompute qualification for ECHO; the identity then requests
a fresh conversation for each benchmark sample. Full model qualification,
training and TwinCore benchmark scores remain pending. Finish all requested
HumanEval profiles before starting their full public LiveBench captures.

The full HumanEval run requests 4,096 output tokens. A 1,024-token TwinCore
resource probe cannot be used for that capture: native admission reserves the
prompt and the entire requested response. Prepare qualification and training
with `--context 8192`, then measure its actual GPU fit before loading the server.
Stored-template rendering of all 164 pinned prompts puts the largest UTF-8
prefix plus a 128-token special-token margin and the output budget at 5,929;
actual token admission is still checked by both native tokenizers. Conservative
Q6 accounting at 8K requires 11,515,102,752 GPU bytes. This is a load estimate,
not proof of fit, measured long-context quality, or million-token capacity.

The prepared TwinCore corpus comes from the existing local coding corpus,
whose builder names [Magicoder-Evol-Instruct-110K](https://huggingface.co/datasets/ise-uiuc/Magicoder-Evol-Instruct-110K)
and [CodeFeedback-Filtered-Instruction](https://huggingface.co/datasets/m-a-p/CodeFeedback-Filtered-Instruction).
It contains 512 training and 64 validation records with exact local source-line,
prompt, answer and corpus hashes. The original upstream download revisions and
row IDs were not retained. A later streaming verification matched all 576 exact
prompts and reconstructed answers against these immutable snapshots:

- Magicoder: `b0079beaa0361d82412520b873715bee59cc7dd4`, raw JSONL SHA-256
  `99d8bab4c443050e5bb4bc339f709de8bcccf81cb83e15ef8d53369c5d8dc495`.
- CodeFeedback: `a08c213a9748c66c15d0225814be80a2e77adf4a`, raw JSONL SHA-256
  `6dd3f7797cd86a7e437de660ad259c50835812f8e309d893f09de77b2ee80063`.

Both complete files were hashed during streaming, with 267,709 source records.
The verification keeps per-example source record indexes and reconstruction
hashes without retaining the full downloaded files. This establishes exact
matches at the reviewed snapshots; historical download revisions remain unknown.
CodeFeedback's authors explicitly disclose model-generated material; this
training data must not be described as human-only or free of distillation.
Syntax validation and lexical benchmark screening do not establish answer
correctness or complete semantic decontamination.

```sh
.venv-evaluation/bin/python scripts/evaluation/benchmark_capture.py --url http://127.0.0.1:18610 --model 'FusionCore KV' --inputs scripts/evaluation/runs/humaneval/humaneval-inputs.json --identity model-identity.json --output scripts/evaluation/runs/fusioncore-kv/captures --max-tokens 4096
```

The benchmark uses temperature zero, one selected answer per task, and an
explicit 4,096-token budget. Two-candidate profiles still use additional compute
and must be labeled accordingly. An unfinished answer remains an unfinished
answer; the capture tool never substitutes reasoning, a canonical answer, or a
retry-generated answer. Server errors stop capture with a bounded error body.
Rerunning the command resumes only a matching capture and preserves existing
answers. New captures retain the exact `identity.json` bytes, and replay verifies
that file hash against the binding and embedded model metadata. It accepts loopback HTTP endpoints only and enforces the user's 200 GB
free-space reserve.

For ECHO profiles, set `request_isolation` to
`fresh_conversation_per_sample` in the identity. The tool records a distinct
conversation ID and the exact request hash for each task. This avoids leaking
earlier benchmark tasks into later requests.

## Grade saved answers

First stop the runtime owned by the benchmark to release the GPU. Grading uses
CPU replay; `mockllm/recorded-api-answers` is a transport for saved actual model
answers and performs no inference.

```sh
.venv-evaluation/bin/python scripts/evaluation/grade_captured_benchmark.py --benchmark humaneval --inputs scripts/evaluation/runs/humaneval/humaneval-inputs.json --captures scripts/evaluation/runs/fusioncore-kv/captures --output scripts/evaluation/runs/fusioncore-kv/graded
```

For LiveBench, select `--benchmark livebench` and its original prepared inputs.
The release date must match the recorded provenance. Grading checks all task
identities, full capture counts, responses, prompts, targets, tests, and grading
metadata. Only a complete successful real-model replay marks
`model_quality_measured=true`. Raw answers, per-task scores, finish reasons,
timings, and executed-code logs remain separate evidence files.

## Validate infrastructure without model inference

```sh
.venv-evaluation/bin/python -m unittest discover -s scripts/evaluation -p 'test_*.py'
.venv-evaluation/bin/python scripts/evaluation/validate_livebench_scoring.py --output scripts/evaluation/runs/scorer-controls --release-date 2024-11-25
```

The controls check known passing and failing answers across all six categories.
They are explicitly marked as non-model evidence. `snapshot_model_source.py`
can retain the exact small Python/C++/header/JSON/build sources and native
wrappers listed in a runtime identity. It verifies source hashes and the 200 GB
reserve before creating a snapshot, and never copies model weights.

## Result scope

Completed local results as of 2026-09-27, all with zero evaluation errors:

| Inference profile | HumanEval pass@1 | Generation configuration |
| --- | --- | --- |
| Single LFM Q8 baseline | 113/164 (68.90%) | One model, one draft |
| FusionCore KV | 137/164 (83.54%) | Two complete towers, one fused stream; untuned feedback |
| DualCore KV | 143/164 (87.20%) | Two drafts, then two blind reviews of up to 384 tokens each |

Each draft has the same 4,096-token evaluation budget. DualCore uses more
inference compute than the single-model baseline; these scores do not establish
an equal-compute improvement. DualCore reached the length limit on nine tasks.
These are full public HumanEval scores for
the specified inference configurations, not evidence of general coding,
infinite attention, trained coupling, or agent quality. Other profiles and model
LiveBench scores remain pending. Their live progress is not a final score.

Exact saved answers and full score evidence for those two completed runs are
under [results/2026-09-27](results/2026-09-27). Its evidence manifest records every
copied file hash. Git preserves the original bytes, including line endings.
Historical source identities and machine paths remain as recorded; they must
not be rewritten to resemble newly prepared provenance. The baseline identity's
`complete_towers=1` records its actual profile; nested checkpoint metadata also
describes the paired model family's default two-copy configuration.

The completed DualCore KV run is retained separately under
[results/2026-09-27-dualcore-kv](results/2026-09-27-dualcore-kv).
An independent CPU replay matched all 164 original per-task scores and verified
the exact original identity file hash. Its earlier interrupted 32-answer run is
not included in this completed result. The model source snapshot, original and
replayed summaries, raw answers and full execution log remain hash-bound.

Replay the published FusionCore capture without loading model weights:

```sh
.venv-evaluation/bin/python scripts/evaluation/grade_captured_benchmark.py --benchmark humaneval --inputs scripts/evaluation/results/2026-09-27/humaneval-inputs.json --captures scripts/evaluation/results/2026-09-27/fusioncore-kv/captures --identity scripts/evaluation/results/2026-09-27/fusioncore-kv/model-identity.json --output scripts/evaluation/runs/published-fusioncore-replay
```

The historical manifests bind the task module and recorded prompt/test hashes.
New preparations additionally bind all installed official Python source files.
Replaying historic answers with stronger infrastructure does not change their
original provenance or constitute new model inference. Historical real-model
captures require their original `--identity` file for replay; its byte hash and
parsed metadata must match the capture binding.
