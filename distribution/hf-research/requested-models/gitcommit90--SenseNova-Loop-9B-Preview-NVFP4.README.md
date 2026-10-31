---
license: apache-2.0
base_model: sensenova/SenseNova-Loop-9B-Preview
base_model_relation: quantized
library_name: transformers
pipeline_tag: image-text-to-text
language:
- en
- zh
tags:
- nvfp4
- modelopt
- quantized
- multimodal
- looped-transformer
- mhc
- mtp
- custom_code
---

# SenseNova-Loop-9B-Preview-NVFP4

> **Unofficial community quantization.** The original model, architecture and weights are by **SenseNova**:
> [sensenova/SenseNova-Loop-9B-Preview](https://huggingface.co/sensenova/SenseNova-Loop-9B-Preview)
> (revision `27abea779b1e6634b136805b6519371739d551f7`), licensed Apache-2.0. We did not train this model.
> This repository contains modified weights (post-training quantization only — no fine-tuning, QAT or distillation).

A calibrated, packed **NVFP4** (ModelOpt) checkpoint of SenseNova-Loop-9B-Preview with the **MTP head kept BF16**
so MTP speculative decoding works, validated on an NVIDIA DGX Spark (GB10, SM121) with **native W4A4 FP4 GEMMs**.

## Downloads and companion source

- **Weights and complete model files:** [https://huggingface.co/gitcommit90/SenseNova-Loop-9B-Preview-NVFP4](https://huggingface.co/gitcommit90/SenseNova-Loop-9B-Preview-NVFP4)
- **Runtime, quantization recipe, evaluation harness and charts:** [https://github.com/gitcommit90/sensenova-loop-9b-nvfp4-dgx-spark](https://github.com/gitcommit90/sensenova-loop-9b-nvfp4-dgx-spark)

**Updated October 31: runtime v2 is included below, alongside the preserved October 30 functional-release baseline.**
Weights live on Hugging Face; the GitHub companion does not duplicate the 9.38 GB checkpoint.

## Runtime v2 — final optimization results (October 31)

**Same 9.38 GB NVFP4 weights; new runtime kernels and a smaller MTP draft vocabulary.**
No retraining, recalibration, reduced loop passes, or changed KV/state precision. MTP is supported in all profiles.
The target still verifies over the full vocabulary. Only greedy decoding was evaluated.

[![Final runtime v2 single-user decode, concurrency and TTFT](figures/runtime-v2-performance.png)](figures/runtime-v2-performance.pdf)

| Final matched measurement | Frozen NVFP4 + MTP | v2 exact + MTP | v2 fast + MTP |
| --- | ---: | ---: | ---: |
| Single-request decode, ~1K input (tok/s, median) | 12.4 | **27.3** | **28.9** |
| Single-request code decode, ~90 input (tok/s, median) | 16.0 | **33.4** | **35.9** |
| Single-request document decode, ~4K input (tok/s, median) | 14.3 | **30.6** | **33.3** |
| Eight-request aggregate, ~1K input (tok/s) | 64.3 | **117.2** | **119.4** |
| Eight-request code aggregate (tok/s) | 97.4 | **172.7** | **196.7** |
| Single-request TTFT, ~1K input (seconds, median) | 0.74 | 0.51 | **0.32** |

Exactly 256 output tokens; unique prompts; no prefix-cache hits; zero errors in these final performance runs.
**Aggregate throughput is combined across requests, not each user's speed.** These are the final matched runs,
not the higher preliminary screening peaks. Full sample counts, token lengths and raw measurements:
[`runtime-v2/RESULTS.md`](runtime-v2/RESULTS.md) and [`runtime-v2/runs/`](runtime-v2/runs/).

### Which profile should I use?

- **`MHC_MODE=exact` — recommended conservative profile:** fused Sinkhorn with reference operation order;
  cuBLAS routing projection and reference merge retained. Reference routing math matched bitwise in differential
  tests; the 12-case final feature check matched the frozen output. This is not a universal promise of identical
  generations under every batch/hardware configuration.
- **`MHC_MODE=fast` — faster, numerically different:** also fuses routing projection/merge. FP32 is preserved,
  but summation order changes and greedy text can diverge. Choose this explicitly if that trade-off suits you.
  The supplied v2 launcher's inherited default is `fast`; commands below explicitly select `exact`.
- **`runtime/` — frozen v1:** preserved for reproduction and rollback. Both v2 profiles use the same pinned image.

### Quality: report the losses and uncertainty, not just the speedup

[![Runtime v2 quality results and paired uncertainty](figures/runtime-v2-quality.png)](figures/runtime-v2-quality.pdf)

On the same 300-item suite, frozen v1 scored **251/300 (83.7%)**, v2 exact **249/300 (83.0%)**,
and v2 fast **247/300 (82.3%)**. Paired-bootstrap 95% delta intervals vs frozen:
exact **−0.67 pp [−3.00, +1.67]**; fast **−1.33 pp [−4.33, +1.67]**.
These small, noisy subsets do **not** establish losslessness or full-benchmark equivalence.

**Fast's first MMLU-Pro run fell from 70.0% to 58.3% (−11.7 pp)** and truncations rose from 14 to 20 across the suite.
Those exceed the original first-run per-task and ref+3 truncation tripwires. Fast MMLU-Pro reruns scored 71.7% and
65.0%; frozen reruns scored 70.0% and 65.0%. All runs are retained. Variability and the token budget complicate
interpretation; they do not erase the first-run regression. See [`RESULTS.md`](runtime-v2/RESULTS.md).

The improvements come from **fused FP32 mHC/Sinkhorn kernels** and a **65,536-token MTP draft shortlist** selected
from training-split corpora. The draft is restricted; the verifying target is not. The exact kernel preserves all
20 configured normalization iterations. No learned model weights changed.

Source, numerical tests, optimized launcher, token-map builder, and experiment ledger are in
[`runtime-v2/`](runtime-v2/). Numerical tests and final feature/quality/performance results are included.
Figures follow [figures4papers](https://github.com/ChenLiu-1996/figures4papers), with
[`figures/plot_runtime_v2.py`](figures/plot_runtime_v2.py), PNG previews and vector PDF/SVG exports.

## Original quantization baseline (October 30; retained)

[![Original BF16 versus NVFP4 performance baseline](figures/performance-summary.png)](figures/performance-summary.pdf)

[![Original quantization quality comparison](figures/quality-summary.png)](figures/quality-summary.pdf)

These older figures compare BF16 to the **original v1** NVFP4 runtime, not runtime v2.
Their source script remains [`figures/plot_benchmarks.py`](figures/plot_benchmarks.py).

## Provenance
| Item | Value |
| --- | --- |
| Base model | `sensenova/SenseNova-Loop-9B-Preview` @ `27abea779b1e6634b136805b6519371739d551f7` (weights stored FP32 upstream; loaded as BF16 backbone + FP32 mHC, as the upstream card prescribes) |
| Quantization tool | NVIDIA Model Optimizer `nvidia-modelopt==0.48.0rc1` (`NVFP4_DEFAULT_CFG`, `max` calibration), PyTorch 2.11.0+cu130, Transformers 5.18.0 |
| Format | NVFP4: E2M1 weights, group size 16, FP8-E4M3 block scales, FP32 per-tensor global scale (`weight_scale_2`) and static activation global scale (`input_scale`); activations quantized to NVFP4 at runtime (W4A4); KV cache **not** quantized (BF16) |
| Export | 2026-10-30, `export_hf_checkpoint`, single `model.safetensors` (9.38 GB) |
| Hardware | NVIDIA DGX Spark: GB10 (SM121), 128 GB unified memory, driver 580.159.03, CUDA 13.0 |

## Changes from the original (Apache-2.0 §4(b) notice)
- Decoder linear-layer weights quantized to NVFP4 (see exclusions); `config.json` gains `quantization_config`;
  `hf_quant_config.json` added. Upstream FP32-stored tensors that were not quantized are stored as BF16, except the
  mHC routing parameters, which remain FP32.
- `mtp.*` (MTP head) is included in `model.safetensors` as BF16 (upstream shipped it as a separate FP32 `mtp.safetensors`).
- Tokenizer, processor configs, chat template, generation config and `modeling_sensenova_loop.py` are byte-identical
  to the pinned upstream revision (see `SHA256SUMS`).
- `runtime-v2/` adds fused routing kernels and a 64K draft token map, with no checkpoint weight changes.
- `runtime/` contains SGLang source files from the open SGLang PR
  [#47281](https://github.com/sgl-project/sglang/pull/47281) (Apache-2.0, sgl-project) plus two small patches of ours
  (`runtime/patches/`), needed to serve this checkpoint.

## Architecture-specific handling
- **Looped block (layers 4–27 run twice, shared weights):** calibration ran through the real two-pass forward; every
  looped layer was observed on both visits for every sample, so each shared quantizer's amax is the max over both
  visits. One physical copy of each layer is stored (32 layers, not 56); per-visit KV/recurrent state remains
  independent in the runtime.
- **mHC routers** (`mhc_attn`, `mhc_ffn`): FP32 parameters and FP32 routing math (Sinkhorn, 20 iterations), untouched.
- No scale folding / smoothing / graph transforms were applied (plain max calibration), so nothing assumes a single visit.

## Precision exclusions
| Module group | Kept in | Reason |
| --- | --- | --- |
| Vision encoder + merger (`model.visual*`) | BF16 | fragile, small share of compute |
| `lm_head`, `embed_tokens` | BF16 | output/embedding sensitivity |
| mHC routing (`mhc_attn`, `mhc_ffn`, 96 tensors) | FP32 | required by the architecture |
| Gated-DeltaNet `in_proj_a`, `in_proj_b`, `conv1d`, `A_log`, `dt_bias`, norms | BF16 / FP32 | recurrent-state gates, tiny |
| All RMSNorms | BF16 | not linear |
| MTP head (`mtp.*`, 15 tensors) | BF16 | draft head; keeps MTP acceptance |
| **Quantized (NVFP4):** full-attention `q/k/v/o_proj`, GDN `in_proj_qkv`, `in_proj_z`, `out_proj`, MLP `gate/up/down_proj` in all 32 layers (200 linears) | NVFP4 | |

Parameters quantized: **73.4 %** of the backbone loaded by Transformers (6.91 B of 9.41 B), 71.6 % including the MTP
head. Checkpoint size: **9.38 GB** (upstream: 38.1 GB FP32 on disk ≈ 19.3 GB as BF16). Weight memory in SGLang:
8.8 GB (vs 17.5 GiB BF16 weights in Transformers).

## Calibration data
256 chat-formatted examples (`calib_v1`, sha256 `9a4d2112ef71820a…`, see `quantization/calib_v1.jsonl.sha256`), rendered
with the model's own chat template/processor: 98,458 tokens incl. 39 images. Calibration data is not redistributed.

| Share | Source | Split / revision | Licence |
| --- | --- | --- | --- |
| 30 % chat (en/zh/es/ca) | OpenAssistant/oasst2 | train @ 179dd21f | Apache-2.0 |
| 20 % code | google-research-datasets/mbpp | full/train @ 4bb6404f | CC-BY-4.0 |
| 20 % math (half with `<think>` traces) | openai/gsm8k | main/train @ 740312ad | MIT |
| 15 % tool-calling trajectories | glaiveai/glaive-function-calling-v2 | @ e7f4b645 | Apache-2.0 |
| 15 % image QA | lmms-lab/textvqa | train shard 0 @ 9c0699cd | CC-BY-4.0 (images: OpenImages, CC-BY-2.0) |

Disjoint from all evaluation items (different splits/datasets; 13-gram overlap check found only shared tool-schema boilerplate).

## Quality vs BF16 reference (same SGLang build and flags, greedy, thinking off)
Small fixed suite (300 items; data hashes in `evaluation/eval_data_MANIFEST.json`). Gates were declared before
any NVFP4 result was seen (`evaluation/QUALITY_GATES.md`). Paired-bootstrap 95 % CI for the delta. These are
**subsets**, not full-benchmark scores; differences inside the CI are "not detectable at this n", not "lossless".

| Task | n | BF16 | NVFP4 | Δ (pp) [95 % CI] |
| --- | --- | --- | --- | --- |
| IFEval subset (prompt-level strict, re-implemented checker) | 60 | 78.3 | 75.0 | −3.3 [−13.3, +6.7] |
| GSM8K test subset | 80 | 92.5 | 95.0 | +2.5 [−2.5, +7.5] |
| MMLU-Pro test subset | 60 | 65.0 | 68.3 | +3.3 [−6.7, +13.3] |
| HumanEval subset (pass@1) | 40 | 82.5 | 80.0 | −2.5 [−7.5, 0.0] |
| Function calling (20 hand-written BFCL-style cases) | 20 | 95.0 | 95.0 | 0.0 [−15, +15] |
| TextVQA val subset | 40 | 90.0 | 90.0 | 0.0 [−7.5, +7.5] |
| **All (micro)** | **300** | **82.7** | **83.0** | **+0.3 [−3.0, +4.0]** |

Tool calls: 100 % well-formed for both. 0 request errors. Truncations at the max-token limit: 14 (NVFP4) vs 19 (BF16).
With MTP enabled the NVFP4 model scored GSM8K 96.3 % (n=80), HumanEval 80.0 % (n=40), tools 90 % (n=20); MTP is
lossless in principle at temperature 0, small differences come from batch-dependent numerics.

## Original v1 performance (DGX Spark GB10, SGLang, same flags for all rows)
Prompt 1,051 tokens, exactly 256 output tokens (`ignore_eos`), greedy, unique prompts (no prefix-cache hits),
`--mem-fraction-static 0.45`, CUDA graphs on. No MTP rows: 16 requests per level; MTP rows: 8 requests per level.
Aggregate output tokens/s (per-request decode tok/s p50 in parentheses):

| Concurrency | BF16 | NVFP4 W4A4 | BF16 + MTP | **NVFP4 W4A4 + MTP** | NVFP4 W4A16 (Marlin) |
| --- | --- | --- | --- | --- | --- |
| 1 | 5.75 (5.8) | 9.39 (9.6) | 7.51 (7.6) | **12.21 (12.6)** | 10.98 (11.4) |
| 2 | 11.44 | 17.61 | 14.77 | **24.79** | – |
| 4 | 21.86 | 33.48 | 26.81 | **42.51** | – |
| 8 | 39.93 (5.5) | 64.79 (9.3) | 45.70 (7.0) | **64.43 (12.2)** | 58.17 (8.8) |

TTFT p50 at c=1: 0.75 s BF16 → 0.58 s NVFP4. 0 errors / OOMs at any level. MTP mean accepted length (3 draft
steps): **2.93 NVFP4 vs 2.95 BF16** on the eval tasks; on the synthetic perf prompts ≈2.0, which is why MTP gives
no extra aggregate throughput at c=8 there. KV-cache capacity at the same memory budget: 407 k tokens (NVFP4) vs
308 k (BF16).

**Kernel evidence (native FP4):** a torch-profiler trace of NVFP4 serving shows the quantized linears executing
`cutlass3x_sm120_bstensorop_…gemm_block_scaled_ue4m3xe2m1_ue4m3xe2m1…` (block-scaled FP4×FP4 tensor-core GEMM,
dispatched via the FlashInfer cuDNN backend) with activations quantized by `quantize_with_block_size` — i.e. true
W4A4, not dequantize-to-BF16 (`evaluation/kernel_summary.txt`). The W4A16 column uses the same checkpoint with
Marlin weight-only kernels; it is faster for single-stream decode on GB10 but slower under concurrency.

## Hardware & software requirements
- NVIDIA Blackwell GPU with FP4 tensor cores. **Tested only on DGX Spark (GB10, SM121)**. Weights need ≈9 GB plus
  KV/recurrent-state memory.
- Serving: SGLang image `lmsysorg/sglang:nightly-dev-cu13-20261029-c10e0e17`
  (`sha256:78142f9f4b7fb3ee27f59065615dc7bb263f643ed256c9299c3861086ade4d47`) with the files in `runtime/overlay`
  (SGLang PR #47281 @ `336c54ac1e721197806d94b6929f567446a59693` + our patches) mounted over the image.
  As of 2026-10-30 we found no engine supporting `sensenova_loop` + NVFP4 out of the box (checked: SGLang main,
  LightLLM main — model supported but no NVFP4, vLLM 0.27.1 — no `sensenova_loop`).
- Plain Transformers (`trust_remote_code=True`) loads the original BF16 model but **cannot** execute this packed NVFP4
  checkpoint or MTP; use the SGLang runtime above.

## Usage
Docker and the NVIDIA container runtime are required. Use **one** of the following paths.

**From the GitHub companion** (weights are downloaded once to an ignored local directory):
```bash
git clone https://github.com/gitcommit90/sensenova-loop-9b-nvfp4-dgx-spark.git
cd sensenova-loop-9b-nvfp4-dgx-spark
python3 -m venv .venv-download
.venv-download/bin/pip install 'huggingface_hub>=1,<2'
.venv-download/bin/hf download gitcommit90/SenseNova-Loop-9B-Preview-NVFP4 --local-dir models/SenseNova-Loop-9B-Preview-NVFP4
# Verify the downloaded release before serving:
(cd models/SenseNova-Loop-9B-Preview-NVFP4 && sha256sum -c SHA256SUMS)
MHC_MODE=exact bash runtime-v2/serve_sglang.sh models/SenseNova-Loop-9B-Preview-NVFP4
```

**From a complete Hugging Face snapshot** containing `model.safetensors`:
```bash
# from the snapshot root
MHC_MODE=exact bash runtime-v2/serve_sglang.sh . # conservative optimized W4A4 + MTP, 127.0.0.1:30080
curl -s http://127.0.0.1:30080/v1/models
python3 runtime/probe.py http://127.0.0.1:30080/v1 sensenova-loop-9b-nvfp4 [image.jpg]   # text / thinking / tool / image smoke test
# options: --no-mtp ; W4A16: SGLANG_DISABLE_SILU_FP4_QUANT_FUSION=1 runtime/serve_sglang.sh . --fp4-gemm-backend marlin
```
OpenAI-compatible API; thinking is toggled with `chat_template_kwargs: {"enable_thinking": false|true}`; tools use the
`qwen3_coder` parser (the model's XML `<tool_call><function=…>` format).

### Profile selection and rollback
```bash
MHC_MODE=fast bash runtime-v2/serve_sglang.sh /path/to/model  # fastest; see numerical/quality caveats
MHC_MODE=exact bash runtime-v2/serve_sglang.sh /path/to/model # conservative optimized
bash runtime/serve_sglang.sh /path/to/model                 # frozen v1 rollback
```
The launcher replaces only its named model container. No extra quantization step is required.
Benchmark reproduction is documented in [`runtime-v2/bench/README.md`](runtime-v2/bench/README.md).

## Limitations
- Upstream is a *Preview* model; all upstream limitations and intended-use terms apply.
- The serving path depends on an **unmerged** SGLang PR plus our patches, pinned to one nightly image; it was tested
  only on GB10. Pipeline/tensor parallelism and other GPUs were not tested.
- Evaluation is small (300 items, thinking off, contexts ≤ ~3 K tokens). Long context (> 32 K), video input, thinking-mode
  quality at scale and non-English quality were not evaluated. Only greedy decoding was benchmarked.
- The frozen v1 mHC path uses many small FP32 kernels. Runtime v2 fuses those operations; the remaining profile is more GEMM/output-head bound.
- Runtime v2 fast is not bitwise equivalent to v1 and did not pass every original first-run quality tripwire. Exact is the conservative option.
- The draft vocabulary shortlist was built from training corpora; no broad sampling-equivalence or full-context quality study was performed.
- In this SGLang build, `--fp4-gemm-backend marlin` crashes unless `SGLANG_DISABLE_SILU_FP4_QUANT_FUSION=1` is set
  (upstream bug: the fused SiLU+FP4-quant path is enabled regardless of backend).
- Quantization may change behaviour on inputs unlike the calibration mixture.

## Files
- `model.safetensors`, `config.json`, `hf_quant_config.json` — NVFP4 checkpoint (+ BF16 MTP head, FP32 mHC).
- Upstream (unchanged): `tokenizer*.json`, `chat_template.jinja`, `*processor_config.json`, `generation_config.json`, `modeling_sensenova_loop.py`.
- `runtime-v2/` — optimized profiles, Triton source, draft token map, final results and differential tests.
- `runtime/` — original tested launcher, SGLang overlay and patches, probe script, compatibility notes.
- `quantization/` — quantization/verification scripts, stats, structural verification report, environment lock, calibration manifest.
- `evaluation/` — quality gates, eval summaries, paired comparison, perf JSONs, kernel trace summary, reference checks.
- `SHA256SUMS` — checksums of every file.

## Licence
Apache-2.0, inherited from the base model (`LICENSE`, verbatim from `OpenSenseNova/SenseNova-Loop` @ `a521b0af`; the
upstream repository has no NOTICE file). SGLang files in `runtime/overlay` are Apache-2.0 (sgl-project).

## Citation
Please cite the original model:
```bibtex
@misc{sensenova2026loop,
  title        = {{SenseNova-Loop-9B-Preview}},
  author       = {{SenseNova}},
  year         = {2026},
  howpublished = {Hugging Face model repository},
  url          = {https://huggingface.co/sensenova/SenseNova-Loop-9B-Preview}
}
```
