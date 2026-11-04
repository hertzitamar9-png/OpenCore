---
license: other
license_name: lfm1.0
license_link: LICENSE
language:
- en
- ar
- zh
- fr
- de
- it
- ja
- ko
- pt
- es
- vi
- th
- id
- hi
- ru
- pl
datasets:
- mondk/Ultra-Trio-Unfiltered
base_model:
- LiquidAI/LFM3-1.7B
pipeline_tag: text-generation
tags:
- lfm3
- claude
- stepbystep
- agent
- toolcall
- code
- msh
---

# LFM3 1.7B Claude Code

hi, I’ve fine-tuned the LiquidAI/LFM3-1.7B model over several rounds using the `mondk/Ultra-Trio-Unfiltered` dataset:

+ Round 1: Tool usage
+ Round 2: Improving benchmark scores
+ Round 3: RL (the model generates its own questions and answers)

safetensors: mondk/LFM3-1.7B-Claude-Code-Safetensors

---
## 1. Results
| Benchmark | Model | Correct | Wrong | Pass rate |
|---|---|---|---|---|
| BFCL v4 | LFM3 1.7B Claude Code | 27 | 3 | **90.0%** |
| BFCL v4 | LFM3 1.7B | 25 | 5 | **83.3%** |
| LCB v6 | LFM3 1.7B Claude Code | 2 | 10 | **16.7%** |
| LCB v6 | LFM3 1.7B | 1 | 11 | **8.3%** |
| TerminalBench | LFM3 1.7B Claude Code | 0 | 4 | **0.0%** |
| TerminalBench | LFM3 1.7B | 0 | 4 | **0.0%** |
| SWE | LFM3 1.7B Claude Code | 0 | 4 | **0.0%** |
| SWE | LFM3 1.7B | 0 | 4 | **0.0%** |

**Method**
- BFCL: function calls matched against gold.
- LCB: final code run against all gold tests; original outputs kept where the model finished its answer.
- TerminalBench and SWE: correct only if resolved (all verifier tests pass).
- Both models: f16, temperature: 0, thinking: default. Max tokens: 1200 (BFCL), 2048 (LCB), 2560 (TerminalBench and SWE). Max tool calls: 9.

**Limitations**
- Sample size is small: 30 BFCL questions, 12 LCB questions, 4 TerminalBench questions and 4 SWE questions. Results may differ slightly from other evaluations.
- Some BFCL answers that use JSON or prose instead of the required [func(args)] format were counted as correct if the call content was right.
- TerminalBench and SWE come from noodcon/TerminalBench-DockerLess and noodcon/SWE-DockerLess, Docker-free versions of the official benchmarks. Docker was too complex to set up here, so these may not match the official benchmarks.

# 2. Process metrics
| Benchmark | Model | Format errors | Tool calls | Time (s) | Characters generated | Estimated tokens |
|---|---|---|---|---|---|---|
| TerminalBench | LFM3 1.7B Claude Code | 7 | 19 | 156.2 | 12,591 | ~3,150 |
| TerminalBench | LFM3 1.7B | 13 | 3 | 984.5 | 103,391 | ~25,850 |
| SWE | LFM3 1.7B Claude Code | 4 | 36 | 329.5 | 24,279 | ~6,070 |
| SWE | LFM3 1.7B | 12 | 0 | 90.5 | 8,018 | ~2,005 |

ty!