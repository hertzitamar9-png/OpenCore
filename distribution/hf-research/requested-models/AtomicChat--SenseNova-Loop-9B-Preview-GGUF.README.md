---
license: apache-2.0
base_model:
- sensenova/SenseNova-Loop-9B-Preview
base_model_relation: quantized
pipeline_tag: text-generation
library_name: gguf
tags:
- gguf
- sensenova
- looped-transformer
- quantized
---

# SenseNova-Loop-9B-Preview-GGUF

GGUF quantizations of [sensenova/SenseNova-Loop-9B-Preview](https://huggingface.co/sensenova/SenseNova-Loop-9B-Preview) — a looped multimodal model (looped decoder + manifold-constrained hyper-connections) from SenseNova. By AtomicChat.

> [!IMPORTANT]
> **New architecture — needs a patched llama.cpp.** SenseNova-Loop is a `sensenova_loop` model: a Qwen3.5-class text backbone whose layers 4-27 are executed twice (56 layer computations from 32 stored layers) with two-stream manifold-constrained hyper-connections (mHC, Sinkhorn routing). Upstream llama.cpp, LM Studio and Ollama **do not support this architecture yet**, so these files will not load on a stock build. They were produced and verified with a llama.cpp build that adds SenseNova-Loop support, on top of `ggml-org/llama.cpp` commit `5047748f11` (upstream support PR pending). These are **text-only** GGUFs for now; the vision tower (`mmproj`) is a follow-up.

## Files

Baseline preset quants (no importance matrix, no layout tuning) for a fast first release; improved quants to follow. The mHC routers are kept in F32 (routing is precision-sensitive).

| File | bits/weight | Size on disk | Role |
|---|---|---|---|
| `SenseNova-Loop-9B-Preview-BF16.gguf` | 16.0 | 16.7 GB | reference / highest fidelity |
| `SenseNova-Loop-9B-Preview-Q8_0.gguf` | ~8.5 | 8.9 GB | near-lossless, fits comfortably in 16 GB |
| `SenseNova-Loop-9B-Preview-Q6_K.gguf` | ~6.6 | 6.9 GB | high quality |
| `SenseNova-Loop-9B-Preview-Q5_K_M.gguf` | ~5.7 | 6.0 GB | balanced |
| `SenseNova-Loop-9B-Preview-Q4_K_M.gguf` | ~4.9 | 5.2 GB | smallest, runs on ~8 GB-class GPUs |

## Architecture notes

- Text backbone is Qwen3.5-class: hybrid attention (three Gated-DeltaNet linear-attention layers per one full-attention layer), QK-norm, attention output gate, interleaved M-RoPE with `partial_rotary_factor` 0.25, dense SwiGLU FFN, vocab 248320.
- Looped schedule: `loop_start=4`, `loop_end=28`, executed twice; each visit keeps its own attention KV and GDN recurrent state.
- mHC: two parallel residual streams over the looped region, with a dense router (sigmoid pre/post gates + a Sinkhorn-normalized doubly-stochastic mix) replacing the plain residual add. Router weights are F32.
- MTP draft block and the vision tower are not included in these text GGUFs.

## Running

Build a llama.cpp with SenseNova-Loop support, then:

```bash
llama-cli -m SenseNova-Loop-9B-Preview-Q6_K.gguf -ngl 99 -c 4096 -st \
  -p "What is the capital of France? Answer in one short sentence."
```

The chat template (with thinking) is embedded in the GGUF. Verified greedy output on BF16: coherent reasoning, code and multilingual generations (e.g. the train-speed word problem resolves to 80 km/h; "capital of France" -> Paris).

## Atomic Chat

Atomic Chat support: in progress (the engine needs this architecture; port underway).

## Credits & license

Base model: [sensenova/SenseNova-Loop-9B-Preview](https://huggingface.co/sensenova/SenseNova-Loop-9B-Preview), Apache License 2.0 — see `LICENSE`. Quantized and published by AtomicChat.
