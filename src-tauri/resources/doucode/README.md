# DuoCore: K2 + Nanbeige candidate selection

DuoCore runs two separate Q6_K language models. K2-Horizon and Nanbeige receive the same conversation and tool schema, independently produce a complete candidate, then evaluate both candidates. The higher average blind score is selected as the one response. This is the existing two-model selector; it does not merge weights and is distinct from the planned TwinCore fused model.

## One turn

1. K2 and Nanbeige generate in parallel. The K2 draft streams provisionally while the final choice is pending.
2. If both produce the same valid response, that response is selected. If only one candidate passes structural checks, the valid candidate is selected.
3. If candidates differ, K2 and Nanbeige independently score candidate A and candidate B against the full request and available tools. Candidate ordering is randomized for the two reviews. The app averages the two scores and selects the higher result.
4. Tool names and JSON arguments are checked against the offered tool schema before selection. OpenCore approval remains in control of tool execution.

There is no layer or weight fusion, latent bridge, Laya model, or additional judge. The two base checkpoints keep their own tokenizers and model architectures. Reported reviewer confidence is uncalibrated and is not a probability that an answer is correct. The scores are an experimental selection signal and still need benchmark calibration.

## Optional weight downloads

- K2-Horizon-3.7B, Q6_K: backbones/k2/K2-Horizon-4B-Q6_K.gguf
- Nanbeige4.2-3B, Q6_K: backbones/nanbeige/Nanbeige_Nanbeige4.2-3B-Q6_K.gguf

Together the Q6_K files occupy 7.22 GiB (7,757,006,368 bytes). Their SHA-256 hashes and pinned source revisions are in `resources/model-catalog.json`. Model weights are Q6_K; the KV cache remains Q4_0 in system RAM. They are loaded by two llama.cpp servers behind one OpenAI-compatible DuoCore endpoint. The application selects one runtime profile at a time. No weights ship with the app.

## Required inference runtime

The stock llama.cpp binary previously bundled with OpenCore does not recognize either GGUF architecture. The required Windows CUDA runtime is built from the model-author fork [MBZUAI-IFM/llama.cpp](https://github.com/MBZUAI-IFM/llama.cpp), branch `model/K2Horizon`, pinned at commit `42adf019f76013dac873b5b43950d54d5ab27216`. That revision contains both the K2-Horizon and Nanbeige loaders. The built runtime is bundled in `runtime/` beside this package and is separate from OpenCore's normal Qwen-compatible runtime. Required cuBLAS and Microsoft runtime DLLs are bundled too; this adds about 560 MiB and includes the NVIDIA CUDA license notices.

The local build targets compute capability 8.9 for the RTX 4070, plus compute 7.5 PTX for forward-compatible JIT. `runtime/build-info.json` records the source revision, build settings, and SHA-256 hashes. The app build checks these files before packaging.

## CPU and GPU behavior

Before starting both servers, the app budgets GPU layers first, then estimates host RAM for the CPU-resident layers and Q4_0 KV cache. It keeps a 5 GiB system-RAM reserve. On the current 20-logical-processor computer, each server is capped at four CPU threads, eight across the pair. The thread count scales down on smaller CPUs. CPU-offloaded layers trade generation speed for fitting the pair on smaller GPUs.

The shipped config requests a 65,536-token active window. A fresh Q6_K paired load and structured tool-call test passed on the RTX 4070: 16.83 seconds to ready and 7,898 MiB total GPU memory used. Automatic placement selected 30/36 K2 and 19/22 Nanbeige GPU layers. This qualifies startup and a short request, not a near-capacity prompt. ECHO stores searchable exact history outside the active model window; it does not create simultaneous attention over an unbounded sequence.

## Verification status

The Q6_K files were SHA-256 verified against pinned Hub revisions. The real two-worker load, generation and tool-call checks are recorded in `tests/evidence/duocore-q6-runtime-2026-09-26.json`. Candidate scores and confidence are uncalibrated. General answer quality, coding benchmarks, near-capacity long prompts, and comparative performance against the original OpenCore model still require measured runs. A successful selection does not prove correctness.
