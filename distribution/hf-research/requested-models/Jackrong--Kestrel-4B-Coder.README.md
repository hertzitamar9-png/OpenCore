---
library_name: transformers
base_model: unsloth/Qwen3.5-4B
base_model_relation: finetune
license: apache-2.0
language:
- en
- zh
- ko
- ja
- ru
- es
pipeline_tag: image-text-to-text
tags:
- qwen3_5
- unsloth
- merged
- sft
- distillation
- reasoning
- code-generation
- coder
- tool-use
- function-calling
- agent
- mtp
- multi-token-prediction
- speculative-decoding
- local-inference
- edge-computing
- on-device
- gguf
- llama.cpp
- vision
- multimodal
- safetensors
---

# 🦅 Kestrel-4B-Coder

![Kestrel-4B-Coder — a compact local coding model fine-tuned from Qwen3.5-4B](assets/model_hero.png)

**Kestrel-4B-Coder** is a compact coding and agent-focused fine-tune of [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B), prepared from [Unsloth Qwen3.5-4B](https://huggingface.co/unsloth/Qwen3.5-4B). Its focus is **useful intelligence closer to the device**: practical coding, compact thinking, and tool-assisted workflows that can run on personal hardware.

The release is available as a [merged checkpoint](https://huggingface.co/Jackrong/Kestrel-4B-Coder) and a [GGUF model with bundled MTP weights](https://huggingface.co/Jackrong/Kestrel-4B-Coder-GGUF). The coding results below use **Q8_0 GGUF**.

**Local today, broader edge deployment ahead.** The current agent results come from Apple Silicon with LM Studio. The development direction is to make capable coding agents more accessible across constrained devices, with broader hardware testing, efficient quantization, and stronger workspace reliability.

## 💡 1. The Kestrel Philosophy — Make Each Token Count

Kestrel puts practical coding first: understand the task, use the right tool, produce a useful edit, and check the result. Fine-tuned from **Qwen3.5-4B**, it brings focused coding and tool-use behavior to devices with tighter memory and compute budgets.

The working loop is simple: **Read → Reason → Call Tools → Edit → Test**. Each turn adds to the time a developer waits for a result. Compact thinking aims to reduce unnecessary generation and keep the next action clear, especially when an agent repeats this loop many times.

On an edge device, each generated token competes for memory bandwidth, compute time, and power. The design goal is a model that spends its reasoning budget on the next useful action, helping developers build responsive local assistants without relying on remote inference for every turn.

> [!IMPORTANT]
> **Useful code is the goal.** Brevity helps when the resulting code remains correct. Kestrel pairs compact thinking with code quality and practical tool use.

## 🧪 2. Model Preparation and Collaboration

The model was prepared by merging the coding adaptation into **unsloth/Qwen3.5-4B**. The merged release retains **MTP/NextN weights** and the inherited vision components. Its tokenizer and chat template come from the release adapter; use the included template for chat and tool interactions. The evaluations in this card cover text, code, and agent behavior.

**Built with community support.** Thank you to [Kyle Hessling · @KyleHessling1 on X](https://x.com/KyleHessling1) for collaboration, testing, feedback, and hardware support, and to [Unsloth](https://unsloth.ai/) for efficient fine-tuning tooling. See the [Unsloth framework on GitHub](https://github.com/unslothai/unsloth).

## 📚 3. Dataset and Distillation Recipe

The training corpus contains approximately **150K high-quality code and complex reasoning examples**, selected to teach a compact model how to solve programming problems and act on tool feedback.

- **Agent interaction and tool use (~40,000 examples).** Distilled from **Qwen3.8-27B**, this component covers multi-turn tool calls, environment state tracking, self-correction, and reflection on task and tool boundaries.
- **Dense algorithms and code generation.** Distilled from **Qwen3.8-Flash-Next** and **Qwen3.8-Max**, these examples emphasize complex algorithm implementation, rigorous syntax, edge-case handling, and refactoring.
- **Logical reasoning and instruction tuning.** Selected high-quality examples from the **nvidia/Nemotron** family reinforce multi-step reasoning and strict instruction following.

The quality-control pipeline applies **strict AST-based syntax validation to applicable code, deduplication and cleaning, and sandbox execution filters**. These checks support a corpus centered on executable solutions, coherent tool interactions, and efficient developer workflows.

## 📊 4. Coding Quality — HumanEval and HumanEval+

The author-run results for **Kestrel-4B-Coder**, **Qwopus3.5-9B-Coder-Flash**, and **Qwen3.5-9B** cover **164 programming tasks, five samples per task, and 820 generations per model**. Their reported configuration uses **Q8_0 GGUF**, **llama.cpp CUDA**, **MTP speculative decoding with maximum draft length 2**, **temperature 0.2**, and **top-p 0.95**.

![HumanEval and HumanEval+ comparison, ordered Kestrel-4B-Coder, Qwen3.5-4B, Qwopus3.5-9B-Coder-Flash, and Qwen3.5-9B; 4B columns are pale green and 9B columns pale purple](assets/coding_quality.png)

<details>
<summary>Text version of the coding results</summary>

<table>
  <thead>
    <tr>
      <th style="background: #f1f5f9; color: #334155;">Metric</th>
      <th style="background: #d1fae5; color: #065f46;">Kestrel-4B-Coder</th>
      <th style="background: #d1fae5; color: #475569;">Qwen3.5-4B</th>
      <th style="background: #ede9fe; color: #4c1d95;">Qwopus3.5-9B-Coder-Flash</th>
      <th style="background: #ede9fe; color: #475569;">Qwen3.5-9B</th>
    </tr>
  </thead>
  <tbody>
    <tr><td>HumanEval Pass@1</td><td style="background: #ecfdf5; color: #047857;"><strong>83.66%</strong></td><td style="background: #ecfdf5; color: #475569; font-weight: 400;">72.56%</td><td style="background: #f5f3ff; color: #6d28d9;"><strong>91.83%</strong></td><td style="background: #f5f3ff; color: #475569; font-weight: 400;">85.24%</td></tr>
    <tr><td>HumanEval Pass@5</td><td style="background: #ecfdf5; color: #047857;"><strong>94.51%</strong></td><td style="background: #ecfdf5; color: #475569; font-weight: 400;">89.63%</td><td style="background: #f5f3ff; color: #6d28d9;"><strong>98.17%</strong></td><td style="background: #f5f3ff; color: #475569; font-weight: 400;">97.56%</td></tr>
    <tr><td>HumanEval+ Pass@1</td><td style="background: #ecfdf5; color: #047857;"><strong>79.51%</strong></td><td style="background: #ecfdf5; color: #475569; font-weight: 400;">69.88%</td><td style="background: #f5f3ff; color: #6d28d9;"><strong>88.05%</strong></td><td style="background: #f5f3ff; color: #475569; font-weight: 400;">80.37%</td></tr>
    <tr><td>HumanEval+ Pass@5</td><td style="background: #ecfdf5; color: #047857;"><strong>92.07%</strong></td><td style="background: #ecfdf5; color: #475569; font-weight: 400;">76.83%</td><td style="background: #f5f3ff; color: #6d28d9;"><strong>96.95%</strong></td><td style="background: #f5f3ff; color: #475569; font-weight: 400;">95.12%</td></tr>
    <tr><td>HumanEval correct samples</td><td style="background: #ecfdf5; color: #047857;"><strong>686 / 820</strong></td><td style="background: #ecfdf5; color: #475569; font-weight: 400;">595 / 820</td><td style="background: #f5f3ff; color: #6d28d9;"><strong>753 / 820</strong></td><td style="background: #f5f3ff; color: #475569; font-weight: 400;">699 / 820</td></tr>
    <tr><td>HumanEval+ correct samples</td><td style="background: #ecfdf5; color: #047857;"><strong>652 / 820</strong></td><td style="background: #ecfdf5; color: #475569; font-weight: 400;">573 / 820</td><td style="background: #f5f3ff; color: #6d28d9;"><strong>722 / 820</strong></td><td style="background: #f5f3ff; color: #475569; font-weight: 400;">659 / 820</td></tr>
  </tbody>
</table>

</details>

Kestrel reports **83.66% HumanEval Pass@1** and **79.51% HumanEval+ Pass@1**, alongside **72.56%** and **69.88%** for Qwen3.5-4B. Its **94.51% HumanEval Pass@5** and **92.07% HumanEval+ Pass@5** extend coverage across multiple attempts. The two 9B columns provide additional context, with Qwopus3.5-9B-Coder-Flash retaining the highest reported scores in this table.

For the author-run results, Pass@1 is the reported single-attempt success estimate using five samples per task. Pass@5 summarizes whether the five attempts include a passing solution. Correct-sample counts refer to individual generations.

## ⚡ 5. Efficiency — Local Compute and the Path Ahead

The 4B footprint makes local deployment a central part of the model's purpose. The aim is to bring coding assistance and agent control to the machine where the work happens: a laptop, a personal workstation, or a smaller system with a limited resource budget.

### Current deployment foundation

- **Local inference and local tools.** The HermesAgent-20 results demonstrate a local model setup on **Apple Silicon with LM Studio**. The released GGUF also provides a path to compatible llama.cpp deployments, letting the model participate in coding and tool loops close to the user's workspace.
- **An adjustable memory budget.** Quantization, context length, and offload settings provide practical deployment controls. A **<6 GB RAM/VRAM footprint** remains a target for suitable lower-bit configurations and modest contexts, rather than a measured peak-memory result. The currently available Q8_0 release does not establish that total-memory target; weights, context/KV cache, runtime buffers, and any projector all contribute to usage.
- **Bundled MTP.** A compatible backend can use the retained MTP head for speculative drafting. The coding evaluation uses a maximum draft length of **2**. Throughput benefits depend on the device, workload, acceptance rate, and runtime; this card does not yet report a measured 4B speedup.

### Development direction

Future development is aimed at extending this local foundation into more dependable edge agents:

1. **Broader device measurements.** Evaluate peak memory, first-token latency, sustained generation speed, and power use across more local hardware, with explicit model, quantization, and context settings.
2. **Efficient deployment choices.** Explore lower-bit releases and device-specific MTP settings while checking that coding and tool-use quality remain useful.
3. **Stronger workspace execution.** Improve stored procedures, file coordination, and recovery in multi-step tasks, guided by the capability gaps in HermesAgent-20.

These are development priorities. The current evidence covers the reported coding evaluations and Apple Silicon agent runs; broader device coverage, energy efficiency, and future quantizations remain work to validate.

## 🍎 6. Practical Agent Evaluation — HermesAgent-20

[**BenchLocal**](https://github.com/stevibe/BenchLocal) provides the **HermesAgent-20** suite for evaluating practical agent workflows. The reported local environment is an **Apple Silicon Mac with LM Studio**.

The overall composite score is **70 for Kestrel-4B-Coder**, compared with **63 for Qwen3.5-4B**. The supplied baseline screenshot identifies its model as `qwen3.5-4b-mlx via LM Studio` and shows one run with **20 / 20 result records**.

![HermesAgent-20 overall comparison: Qwen3.5-4B scores 63 and Kestrel-4B-Coder scores 70](assets/benchlocal_scores.png)

The breakdown shows higher reported **Memory & Recall** and **Delegation, Recovery & Boundaries** scores for Kestrel, with equal **Scheduling & Delivery** scores. Qwen scores higher on **Skills & Procedural Memory** and **Workspace Orchestration**, which provide clear targets for future development.

![HermesAgent-20 capability comparison for Qwen3.5-4B and Kestrel-4B-Coder](assets/benchlocal_capabilities.png)

<details>
<summary>Text version of the HermesAgent-20 comparison</summary>

| Overall / capability | Qwen3.5-4B | Kestrel-4B-Coder |
| :--- | :---: | :---: |
| **HermesAgent-20 overall** | **63** | **70** |
| Memory & Recall | 63 | **88** |
| Delegation, Recovery & Boundaries | 21 | **76** |
| Scheduling & Delivery | 75 | **75** |
| Skills & Procedural Memory | 88 | **68** |
| Workspace Orchestration | 70 | **45** |

</details>

These are **BenchLocal composite scores, not pass rates**. The overall score is reported separately from the five selected dimensions. The baseline screenshot's 20 / 20 label counts completed result records, rather than successful scenarios. The baseline uses MLX; the exact Kestrel backend and quantization, matched run settings, and independent execution-verifier results are not documented in the supplied summary. Treat this as an observational local comparison, rather than a controlled measurement of the fine-tuning effect.

Thank you to [**Steven Lei (`stevenlei`)**](https://github.com/stevenlei) for creating [BenchLocal](https://github.com/stevibe/BenchLocal).

## 🚀 7. Getting Started

Install the [Hugging Face CLI](https://huggingface.co/docs/huggingface_hub/guides/cli), then download the Q8_0 model. For a private repository, first authenticate with an account that has access using `hf auth login`.

```bash
python -m pip install -U huggingface_hub

hf download Jackrong/Kestrel-4B-Coder-GGUF \
  Kestrel-4B-Coder-MTP-Q8_0.gguf \
  --local-dir ./Kestrel-4B-Coder-GGUF
```

Use a recent [llama.cpp build](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) with Qwen3.5 and **draft-MTP** support. This starts a local OpenAI-compatible server with the coding sampling settings and a maximum draft length of two:

```bash
llama-server \
  --model ./Kestrel-4B-Coder-GGUF/Kestrel-4B-Coder-MTP-Q8_0.gguf \
  --alias Kestrel-4B-Coder \
  --spec-type draft-mtp \
  --spec-draft-n-max 2 \
  --ctx-size 8192 \
  --n-gpu-layers 99 \
  --jinja \
  --reasoning on \
  --temp 0.2 \
  --top-p 0.95 \
  --host 127.0.0.1 \
  --port 8080
```

The **8,192-token context is a starting example**, not a benchmark-reproduction setting. Adjust context and GPU offload to your device. CUDA requires a CUDA-enabled build; Apple Silicon uses a compatible Metal build. Keep the included chat template and pass sampling settings explicitly when calling the server. Use thinking enabled for the reported coding setup. MTP requires both the bundled weights and backend support; check the server startup log to confirm it is active.

For image input, also download the repository's exact projector filename and add `--mmproj ./Kestrel-4B-Coder-GGUF/mmproj-F16.gguf` to a compatible multimodal server:

```bash
hf download Jackrong/Kestrel-4B-Coder-GGUF \
  mmproj-F16.gguf \
  --local-dir ./Kestrel-4B-Coder-GGUF
```

The projector is optional for text-only coding. Vision quality is not evaluated in this card. For local agent use through LM Studio, select the model in the application and configure your agent to use its local server; MTP availability depends on the selected runtime.

## 🧭 8. Intended Use and Limitations

Intended uses include coding assistance on personal devices, local debugging, small refactors, tool-assisted development, and compact agent experiments. The longer-term direction is practical edge agents that work within explicit memory and compute budgets. Review generated changes and run tests before relying on them.

HumanEval measures bounded programming tasks and does not establish repository-scale software engineering ability. HermesAgent-20 points to weaker workspace orchestration and procedural-memory scores relative to the baseline. Difficult multi-file tasks, long interaction histories, and ambiguous tool feedback can still lead to mistakes or unsuccessful actions.

The reported results apply to their stated settings and scoring policies. They do not establish universal coding accuracy, long-context reliability, vision quality, or safe autonomous operation. Wider edge-device compatibility, sub-6 GB peak memory, and energy savings require device-specific validation. Quantization and runtime choices can affect behavior; the 4B results should not be extrapolated from the 9B model's measured reasoning savings.

## 🙏 9. Acknowledgements and Citation

Thanks to the [Qwen team](https://huggingface.co/Qwen/Qwen3.5-4B) for the foundation model; [Unsloth](https://unsloth.ai/) and its [open-source framework](https://github.com/unslothai/unsloth) for fine-tuning tools; [Kyle Hessling](https://x.com/KyleHessling1) for collaboration and support on Kestrel; and [NVIDIA](https://huggingface.co/nvidia) for the Nemotron data ecosystem. We also thank [EvalPlus](https://github.com/evalplus/evalplus), [llama.cpp](https://github.com/ggml-org/llama.cpp), [LM Studio](https://lmstudio.ai/), and [BenchLocal](https://github.com/stevibe/BenchLocal).

**Base-model license:** Apache-2.0. See the release repository's license and provenance information for the distributed artifacts.

```bibtex
@misc{jackrong2026kestrel4bcoder,
  title        = {Kestrel-4B-Coder},
  author       = {Jackrong},
  year         = {2026},
  howpublished = {Hugging Face},
  url          = {https://huggingface.co/Jackrong/Kestrel-4B-Coder}
}
```
