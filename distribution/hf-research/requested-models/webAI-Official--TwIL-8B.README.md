---
language:
  - en
library_name: transformers
pipeline_tag: text-generation
base_model: ibm-granite/granite-4.2-8b
license: other
license_name: webai-non-commercial-license-ver.-1.0
license_link: https://huggingface.co/webAI-Official/TwIL-8B/blob/main/LICENSE.md
tags:
  - granite
  - granite-4.2
  - twil
  - reasoning
  - math
  - code
  - instruction-following
  - reinforcement-learning
  - grpo
  - gguf
---

# TwIL-8B

An 8.8B reasoning model for **math, code and instruction following**, post-trained from
[`ibm-granite/granite-4.2-8b`](https://huggingface.co/ibm-granite/granite-4.2-8b) with
full-parameter supervised fine-tuning and GRPO/DAPO reinforcement learning. It ships in two
variants that share one architecture and tokenizer: **balanced** (the default) and **verbose**.
See [Variants](#variants).

Against open models of similar size and up to 3.5x larger, TwIL-8B posts the highest MATH-500
(98.2) and LiveCodeBench (81.6 †) scores in the comparison and leads Qwen3.5-9B on IFBench
(+9.9 †). It is level with Qwen3.5-9B on contest math while using fewer tokens per problem. It
trails on knowledge-heavy benchmarks: GPQA Diamond, MMLU-Pro † and HLE †.

![TwIL-8B math, knowledge, instruction-following and code benchmarks against Qwen3.5-9B, Gemma 4 12B and Gemma 4 31B](benchmarks.png)

*TwIL-8B scores are for the balanced variant. † marks benchmarks measured on the verbose variant,
which have not yet been run on balanced. Competitor scores are published figures and were not
re-run; see [Evaluation protocol](#evaluation-protocol).*

## Highlights

* **Highest MATH-500 and AIME 2025 in the comparison.** MATH-500 98.2 against 96.6 for
  Qwen3.5-9B and 87.9 for Gemma 4 31B; AIME 2025 88.3 against 87.1 for Gemma 4 31B.
* **Best LiveCodeBench in the group.** 81.6 † against 80.0 for Gemma 4 31B, 72.0 for Gemma 4 12B
  and 65.6 for Qwen3.5-9B (+16.0). It is within 2.9–6.2 points of GPT-5.5, Claude Opus 4.8 and
  Grok 4.3.
* **Strong instruction following.** IFBench 74.4 † is 9.9 points above Qwen3.5-9B, level with
  Gemma 4 12B (74.0) and 1.6 behind Gemma 4 31B. It is also 12.2 points above Claude Opus 4.8
  and 1.5 behind GPT-5.5. IFEval 91.8 is level with Qwen3.5-9B (91.5).
* **Level with Qwen3.5-9B on contest math, ahead of Gemma 4 12B.** Across the four contests both
  publish, the averages are within 0.4 points. TwIL-8B leads on HMMT Nov 2025 (+3.8) and HMMT
  Feb 2025 (+1.8) and trails on AIME 2026 (−4.2) and HMMT Feb 2026 (−3.0). On AIME 2026 it is
  10.8 points above Gemma 4 12B and within 1 point of Gemma 4 31B.
* **Fewer tokens per problem.** About 19.5k output tokens per AIME 2026 problem against 31.2k
  for Qwen3.5-9B (38% fewer), and 32.9k against 40.6k on HMMT Feb 2026 (19% fewer). It also
  uses fewer tokens than Claude Opus 4.8 on AIME 2026 (21.0k), though at lower accuracy.
* **88–93% of the frontier on reasoning.** It reaches 93% of the best frontier score on
  LiveCodeBench †, 92% on IFBench †, 90% on HMMT Nov 2025 and 88% on AIME 2026 and HMMT Feb 2025.
  HMMT Feb 2026 is the exception, at 69%.
* **Runs locally.** 8.79B parameters in bf16 (16.38 GiB), with a 4.98 GiB Q4_K_M GGUF for CPU or
  modest-VRAM GPUs.

It is **not** a knowledge model. GPQA Diamond (70.2) trails Qwen3.5-9B by 11.5 and Gemma 4 31B
by 14.1, and MMLU-Pro † and HLE † follow the same pattern. It also remains verbose next to the
frontier: GPT-5.5 solves AIME 2026 with about 5k tokens per problem. There is no safety or
preference tuning beyond what Granite 4.2 carries. See [Limitations](#limitations-and-caveats).

## Model Details

| Property | Value |
| --- | --- |
| Model ID | `webAI-Official/TwIL-8B` |
| Base model | [`ibm-granite/granite-4.2-8b`](https://huggingface.co/ibm-granite/granite-4.2-8b) |
| Total parameters | 8.79B (8,791,592,960) |
| Architecture | Granite decoder-only dense transformer (`GraniteForCausalLM`); 40 layers, hidden size 4096, 32 attention heads / 8 KV heads |
| Input / output | Text / text |
| Language | English |
| Tokenizer vocabulary size | 100,352 |
| Context window | 131,072 tokens (inherited from the base model) |
| Variants | `balanced` (default, repository root) and `verbose` (`verbose/` folder) |
| Checkpoint precision | bfloat16 (16.38 GiB per variant), plus GGUF builds |
| Post-training | Full-parameter SFT → GRPO/DAPO RL (verbose) → length-aware RL (balanced) |
| Reasoning format | Emits a `<think> … </think>` block before the answer (default chat template) |
| Evaluated decoding | High-effort pass@1, thinking enabled |
| Specialisation | Contest math, code and instruction following |
| License | webAI Non-Commercial License ver. 1.0 (base model: Apache License 2.0, IBM) |

### Variants

Both variants use the same architecture, tokenizer, chat template and generation config. They
differ only in how much they reason before answering.

| Variant | Safetensors | GGUF | Use it for |
| --- | --- | --- | --- |
| **balanced** (default) | `model-balanced.safetensors` (repository root) | `TwIL-8B-Q4_K_M-balanced.gguf` | General use. Shorter reasoning traces and a lower cost per answer. |
| **verbose** | `verbose/model-verbose.safetensors` | `TwIL-8B-F16-verbose.gguf`, `TwIL-8B-Q8_0-verbose.gguf`, `TwIL-8B-Q4_K_M-verbose.gguf` | Hard contest-style problems where accuracy matters more than token cost. Longer reasoning traces. |

`from_pretrained("webAI-Official/TwIL-8B")` loads **balanced**. Pass `subfolder="verbose"` to
load **verbose** (see [Usage](#usage)).

## Benchmarks

### Against open peers

| Benchmark | **TwIL-8B** (8.8B) | Qwen3.5-9B (9.7B) | Gemma 4 12B (12B) | Gemma 4 31B (31B) |
| --- | ---: | ---: | ---: | ---: |
| ***Math*** | | | | |
| MATH-500 | **98.2** | 96.6 | — | 87.9 |
| AIME 2025 | **88.3** | — | — | 87.1 |
| AIME 2026 | 88.3 | **92.5** | 77.5 | 89.2 |
| HMMT Feb 2025 | 85.0 | 83.2 | — | **88.7** |
| HMMT Feb 2026 | 68.2 | 71.2 | — | **77.2** |
| HMMT Nov 2025 | 86.7 | 82.9 | — | **87.5** |
| ***Science & knowledge*** | | | | |
| GPQA Diamond | 70.2 | 81.7 | 78.8 | **84.3** |
| MMLU-Pro † | 76.5 | 82.5 | 77.2 | **85.2** |
| HLE † | 12.6 | 14.9 | 5.2 | **19.5** |
| ***Instruction following*** | | | | |
| IFEval | 91.8 | 91.5 | 97.2 | **98.9** |
| IFBench † | 74.4 | 64.5 | 74.0 | **76.0** |
| ***Code*** | | | | |
| LiveCodeBench † | **81.6** | 65.6 | 72.0 | 80.0 |
| HumanEval † | 93.2 | 93.9 | — | **94.5** |
| HumanEval+ † | 88.3 | 87.2 | — | **91.5** |
| MBPP † | 93.7 | — | — | — |
| MBPP+ † | 79.1 | — | — | — |
| CRUXEval † | 97.1 | — | — | — |

† Measured on the verbose variant; not yet run on balanced. — means no published result could be
found. Bold marks the best score in each row where at least two models report one. All scores are
percentages.

TwIL-8B leads three rows outright (MATH-500, AIME 2025 and LiveCodeBench †). It is the strongest
model here at its own size on code and instruction following: against Qwen3.5-9B it is ahead on
LiveCodeBench † by 16.0 and IFBench † by 9.9, and the two are level on IFEval, HumanEval † and
HumanEval+ †. Contest math is close to even with Qwen3.5-9B (two contests each way), and ahead of
Gemma 4 12B by 10.8 on the one contest it publishes.

Gemma 4 31B, at about 3.5x the parameters, leads most remaining rows. TwIL-8B is ahead of it on
MATH-500, AIME 2025 and LiveCodeBench †, within one point on AIME 2026 and HMMT Nov 2025, and
behind on knowledge, instruction following and the hardest contest set (HMMT Feb 2026, −9.0).
MBPP †, MBPP+ † and CRUXEval † have no comparable published competitor numbers.

Notes on the competitor figures:

* Gemma 4 31B's HMMT scores were measured by the Qwen team (Qwen3.6-27B model card), not by Google.
  Its MATH-500 (87.9) and AIME 2025 come from Red Hat's lighteval runs of the unquantized model;
  the MATH-500 figure is unusually low and may reflect answer extraction.
* Google's IFEval figures for Gemma 4 (97.2 and 98.9) do not state the metric variant. Red Hat
  measured 94.6 prompt-level strict for Gemma 4 31B.
* HumanEval and HumanEval+ comparators are community runs. The Gemma 4 31B run used a 512-token
  cap, which leaves no room for thinking. The Qwen3.5-9B run had thinking enabled.
* Competitor LiveCodeBench scores are v6. Qwen3.5-9B's AIME 2026 and HMMT Feb 2026 scores are from
  MathArena; its only public AIME 2025 results are non-thinking runs and are excluded.

### Against frontier models

GPT-5.5, Claude Opus 4.8 and Grok 4.3 at their highest published reasoning effort. The last column
is TwIL-8B's score as a share of the best frontier score on that row.

| Benchmark | **TwIL-8B** | GPT-5.5 (xhigh) | Claude Opus 4.8 (max) | Grok 4.3 (high) | Share of best frontier |
| --- | ---: | ---: | ---: | ---: | ---: |
| AIME 2026 | 88.3 | 100.0 | 100.0 | — | 88% |
| HMMT Feb 2025 | 85.0 | 96.5 | 96.5 | — | 88% |
| HMMT Feb 2026 | 68.2 | 98.5 | 95.5 | — | 69% |
| HMMT Nov 2025 | 86.7 | 96.5 | 96.5 | — | 90% |
| GPQA Diamond | 70.2 | 93.6 | 93.6 | 90.1 | 75% |
| MMLU-Pro † | 76.5 | 88.1 | 89.6 | 85.8 | 85% |
| HLE † | 12.6 | 45.8 | 48.7 | 37.2 | 26% |
| IFBench † | 74.4 | 75.9 | 62.2 | 81.3 | 92% |
| LiveCodeBench † | 81.6 | 85.3 | 87.8 | 84.5 | 93% |

TwIL-8B is closest to the frontier on **instruction following and code**. It beats Claude Opus 4.8
on IFBench † by 12.2 points and is within 2.9–6.2 points of all three models on LiveCodeBench †.
Contest math lands at 88–90% of the frontier, with HMMT Feb 2026, the hardest set, as the outlier
at 69%. **Knowledge is the largest gap**: GPQA Diamond trails by about 20–23 points and HLE † by 25–36.
These benchmarks reward stored knowledge, which tends to scale with parameter count more than with
reasoning-focused training.

These three are the most recent frontier releases with measured results on this benchmark set.
Newer releases (GPT-6.1 Sol, Claude Opus 5.5 and Grok 4.7) no longer report AIME, HMMT or GPQA.
Their only measured overlap is Artificial Analysis HLE: 52.9, 61.4 and 43.1 respectively.

### Head-to-head

Averages are taken only over benchmarks where both models have a score, so the benchmark count
differs by row and the averages are not comparable across rows.

| Opponent | Shared benchmarks | Wins–losses | Shared average (TwIL-8B vs opponent) | TwIL-8B leads on | TwIL-8B trails on |
| --- | ---: | :---: | :---: | --- | --- |
| Qwen3.5-9B | 13 | 7–6 | 78.1 vs 77.6 | LiveCodeBench † +16.0, IFBench † +9.9, HMMT Nov 2025 +3.8, HMMT Feb 2025 +1.8, MATH-500 +1.6 | GPQA Diamond −11.5, MMLU-Pro † −6.0, AIME 2026 −4.2, HMMT Feb 2026 −3.0 |
| Gemma 4 12B | 7 | 4–3 | 70.8 vs 68.8 | AIME 2026 +10.8, LiveCodeBench † +9.6, HLE † +7.4 | GPQA Diamond −8.6, IFEval −5.4 |
| Gemma 4 31B | 14 | 3–11 | 78.8 vs 82.0 | MATH-500 +10.3, LiveCodeBench † +1.6, AIME 2025 +1.2 | GPQA Diamond −14.1, HMMT Feb 2026 −9.0, MMLU-Pro † −8.7, IFEval −7.1 |
| GPT-5.5 | 9 | 0–9 | 71.5 vs 86.7 | — | HLE † −33.2, HMMT Feb 2026 −30.3, GPQA Diamond −23.4 |
| Claude Opus 4.8 | 9 | 1–8 | 71.5 vs 85.6 | IFBench † +12.2 | HLE † −36.1, HMMT Feb 2026 −27.3, GPQA Diamond −23.4 |
| Grok 4.3 | 5 | 0–5 | 63.1 vs 75.8 | — | HLE † −24.6, GPQA Diamond −19.9, MMLU-Pro † −9.3 |

### Tokens per problem

Mean output tokens per problem, with accuracy. Competitor figures are from MathArena; frontier
counts are rounded to the nearest 1k.

| Contest | **TwIL-8B** | Qwen3.5-9B | Claude Opus 4.8 | GPT-5.5 |
| --- | --- | --- | --- | --- |
| AIME 2026 | **19.5k** · 88.3% | 31.2k · 92.5% | 21.0k · 100.0% | 5.0k · 100.0% |
| HMMT Feb 2026 | **32.9k** · 68.2% | 40.6k · 71.2% | 30.0k · 95.5% | 8.0k · 98.5% |

Against Qwen3.5-9B, the closest open peer by size, TwIL-8B uses 38% fewer tokens on AIME 2026
and 19% fewer on HMMT Feb 2026, for 4.2 and 3.0 fewer points respectively. GPT-5.5 remains far
more token-efficient. Harnesses and tokenizers differ, so treat this comparison as directional.

## Usage

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

model_id = "webAI-Official/TwIL-8B"
variant = None  # balanced (default); use "verbose" for the verbose variant

tok = AutoTokenizer.from_pretrained(model_id, subfolder=variant or "")
model = AutoModelForCausalLM.from_pretrained(
    model_id, subfolder=variant or "", dtype=torch.bfloat16, device_map="auto"
)

messages = [{"role": "user", "content":
             "How many positive integers n < 1000 are divisible by 3 but not by 5? "
             "Reason step by step, then give the final integer."}]
inputs = tok.apply_chat_template(
    messages, add_generation_prompt=True,
    return_tensors="pt", return_dict=True,
).to(model.device)

out = model.generate(**inputs, max_new_tokens=32768)
print(tok.decode(out[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True))
```

Use a Transformers release with Granite 4.2 support. `return_dict=True` matters on Transformers
5.x, where `apply_chat_template` returns a `BatchEncoding` rather than a bare tensor.

The chat template opens a `<think>` block by default, so the model reasons before it answers.
Give it a large generation budget: on contest math, balanced averages about 19.5k tokens per AIME
2026 problem and 33k per HMMT Feb 2026 problem, and verbose writes more. A short budget truncates
the reasoning and scores far worse. The shipped `generation_config.json` samples with
`temperature=1.0` and `top_p=0.95`. The template also accepts `enable_thinking=False` and
`reasoning_effort="low"` through `chat_template_kwargs`. Both lower latency, but low-effort runs
score substantially lower and none of the numbers on this card use them.

To download a single variant:

```bash
# balanced
hf download webAI-Official/TwIL-8B --exclude "verbose/*" --exclude "*.gguf" --local-dir TwIL-8B-balanced
# verbose
hf download webAI-Official/TwIL-8B --include "verbose/*" --local-dir TwIL-8B
```

### GGUF / llama.cpp

| File | Variant | Quant | Size |
| --- | --- | --- | --- |
| `TwIL-8B-Q4_K_M-balanced.gguf` | balanced | Q4_K_M (importance matrix) | 4.98 GiB |
| `TwIL-8B-Q4_K_M-verbose.gguf` | verbose | Q4_K_M | 4.98 GiB |
| `TwIL-8B-Q8_0-verbose.gguf` | verbose | Q8_0 | 8.70 GiB |
| `TwIL-8B-F16-verbose.gguf` | verbose | F16 | 16.38 GiB |

```bash
llama-cli -hf webAI-Official/TwIL-8B --hf-file TwIL-8B-Q4_K_M-balanced.gguf -cnv -c 40960 -n 32768
```

Name the file with `--hf-file`, because each quant exists for more than one variant. Keep the
context and generation budget large for the same reason as above. The model uses a ChatML-style
template with `<|im_end|>` as EOS. All scores on this card were measured on the bf16 weights,
not on the GGUF builds, so expect small deviations, most likely at Q4_K_M.

## How it was built

1. **Supervised fine-tuning.** Full-parameter SFT of `ibm-granite/granite-4.2-8b` on reasoning
   traces covering math, STEM and instruction following.
2. **Reinforcement learning.** GRPO/DAPO RL on math, code, STEM and instruction-following
   prompts. This produces the **verbose** variant.
3. **Length-aware RL.** A further RL stage with a length-aware reward that favours shorter
   reasoning traces, measured against per-domain length references for math, code, STEM and
   instruction following. This produces the **balanced** variant.

The math, STEM and instruction-following training pools were decontaminated with 13-gram matching
against AIME (2025, 2026 and earlier years), HMMT Feb 2025 and Feb 2026, MATH-500, GPQA Diamond,
MMLU-Pro, HLE, IFEval, IFBench and LiveCodeBench. Both variants ship as full merged models, not
LoRA adapters.

## Limitations and caveats

* **Knowledge.** GPQA Diamond, MMLU-Pro † and HLE † trail larger open models and the frontier by
  wide margins.
* **Coverage.** Rows marked † were measured on the verbose variant and have not yet been run on
  balanced. SWE-bench, SuperGPQA, tool use and agentic tasks were not evaluated.
* **Cross-harness comparisons.** Competitor numbers come from each vendor's model card or from
  third-party evaluations, with their own prompts, sampling and token budgets. The AIME and HMMT
  sets have 30–33 problems, so one problem is worth about 3 points and gaps of that size are
  within noise.
* **HLE scoring.** TwIL-8B's HLE uses a multiple-choice scoring rule. Competitor HLE scores are
  text-only and judge-graded, so that comparison is approximate.
* **LiveCodeBench version.** Competitor scores are v6. The version and date window of TwIL-8B's
  run are not recorded, so confirm them before quoting that gap.
* **Contamination.** HMMT Nov 2025 and the HumanEval, MBPP and CRUXEval suites were not in the
  decontamination set, and decontamination of the code training data is not recorded.
* **Token cost.** Even balanced spends about 19.5k tokens per AIME 2026 problem, roughly 4x
  GPT-5.5.
* **Not a general assistant.** There is no additional safety, preference or tool-use training
  beyond what Granite 4.2 carries.

## Evaluation protocol

* **TwIL-8B:** internal high-effort pass@1 with thinking enabled, on the same harness for every
  benchmark and both variants. Scores are shown as percentages (0.982 → 98.2).
* **Competitors:** reproduced from the public sources below, accessed 3 November 2026. No
  competitor benchmark was re-run. Frontier models are at their highest published reasoning
  effort. Model-predicted estimates on aggregator sites are excluded.
* **Naming:** "GPQA" means GPQA Diamond. AIME 2025 and AIME 2026 are the single-year sets.

## Relationship to TwIL-LM

TwIL-8B belongs to the same webAI TwIL family as
[TwIL-LM3](https://huggingface.co/webAI-Official/TwIL-LM3) and
[TwIL-LM3-Pro](https://huggingface.co/webAI-Official/TwIL-LM3-Pro). Those are 3B formal-logic
models; TwIL-8B targets contest math, code and instruction following at 8B, on IBM's Granite 4.2
8B base. Like them, it ships as a full model on `main`, loaded directly with
`AutoModelForCausalLM`.

## License and attribution

Released under the **webAI Non-Commercial License ver. 1.0**; see `LICENSE.md` in this repository.

The base model, [`ibm-granite/granite-4.2-8b`](https://huggingface.co/ibm-granite/granite-4.2-8b),
is Copyright IBM Corporation and is distributed under the Apache License 2.0; its license text is
retained as `apache-2.0-LICENSE.txt` and all credit for the base model goes to IBM. Apache 2.0
permits distributing derivative works under different terms provided attribution is preserved,
which is what the pair of license files in this repository does.

## Sources

Competitor figures on this card come from:

* [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4) (Google, thinking mode):
  Gemma 4 12B and 31B AIME 2026, GPQA Diamond, MMLU-Pro, HLE and LiveCodeBench v6.
* [Gemma 4 Technical Report, Table 5](https://arxiv.org/abs/2607.02770): Gemma 4 IFEval and IFBench.
* [Qwen3.6-27B model card](https://huggingface.co/Qwen/Qwen3.6-27B), Gemma4-31B baseline column:
  Gemma 4 31B HMMT Feb 2025, Nov 2025 and Feb 2026.
* [RedHatAI/gemma-4-31B-it-FP8-dynamic](https://huggingface.co/RedHatAI/gemma-4-31B-it-FP8-dynamic),
  BF16 baseline: Gemma 4 31B MATH-500 and AIME 2025.
* [EvalPlus submission #300](https://github.com/evalplus/evalplus/issues/300): Gemma 4 31B HumanEval
  and HumanEval+.
* [Qwen3.5-9B model card](https://huggingface.co/Qwen/Qwen3.5-9B) (thinking mode): GPQA Diamond,
  MMLU-Pro, IFEval, IFBench, HMMT Feb 2025, HMMT Nov 2025 and LiveCodeBench v6.
* [MathArena, Qwen3.5-9B](https://matharena.ai/models/qwen_qwen3_5_9b): AIME 2026 and HMMT Feb 2026
  accuracy and tokens per problem.
* [SOTA2 MATH-500 leaderboard](https://www.sota2.com/research/sota/math-on-math-500-score):
  Qwen3.5-9B MATH-500.
* [Artificial Analysis via OpenRouter, Qwen3.5 9B](https://openrouter.ai/qwen/qwen3.5-9b:batch):
  Qwen3.5-9B HLE.
* [nivvis/Qwen3.5-9B-EQ-v5.1](https://huggingface.co/nivvis/Qwen3.5-9B-EQ-v5.1-GGUF), vanilla
  baseline row: Qwen3.5-9B HumanEval and HumanEval+.
* [Introducing GPT-5.5](https://openai.com/index/introducing-gpt-5-5/) and the
  [Claude Opus 4.8 System Card](https://www.anthropic.com/claude-opus-4-8-system-card): GPQA Diamond.
* MathArena via [BenchLeader AIME 2026](https://www.benchleader.com/benchmarks/matharena_aime_2026),
  [BenchLeader HMMT Feb 2026](https://www.benchleader.com/benchmarks/matharena_hmmt_feb_2026) and
  [The Aggregate HMMT Feb 2025](https://theaggregate.ai/benchmark?slug=matharena-hmmt-feb-2025):
  GPT-5.5 and Claude Opus 4.8 contest accuracy and tokens per problem.
* [GLM-5.2 model card](https://huggingface.co/zai-org/GLM-5.2): GPT-5.5 and Claude Opus 4.8 HMMT
  Nov 2025.
* benchgap.net for [GPT-5.5](https://benchgap.net/model/gpt-5-5),
  [Claude Opus 4.8](https://benchgap.net/model/claude-opus-4-8) and
  [Grok 4.3](https://benchgap.net/model/grok-4-3) (measured rows only): Artificial Analysis HLE and
  IFBench, and Vals AI MMLU-Pro and LiveCodeBench.
* [Artificial Analysis via OpenRouter, Grok 4.3](https://openrouter.ai/x-ai/grok-4.3): Grok 4.3
  GPQA Diamond, HLE and IFBench.
* benchgap.net for the newest frontier releases, [GPT-6.1 Sol](https://benchgap.net/model/gpt-6-1-sol),
  [Claude Opus 5.5](https://benchgap.net/model/claude-opus-5-5) and
  [Grok 4.7](https://benchgap.net/model/grok-4-7): Artificial Analysis HLE.
