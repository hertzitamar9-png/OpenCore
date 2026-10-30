---
library_name: transformers
pipeline_tag: text-generation
language:
- en
tags:
- hobbylm
- custom_code
- mixture-of-experts
- instruction-tuned
license: apache-2.0
base_model: harims95/hobbylm-1B
---

<div align="center">
<img src="https://huggingface.co/harims95/hobbylm-1B/resolve/main/assets/fuellabs-banner.jpg" width="100%" alt="Fuel Labs" />
<h1>HobbyLM-1B Instruct</h1>
<p><b>The instruction-tuned HobbyLM-1B: a 1.037B-parameter sparse mixture-of-experts model with approximately 305M parameters active per token.</b><br/>
HobbyLM by <a href="https://fuellabs.in" target="_blank">Fuel Labs</a></p>
<p>
<a href="https://huggingface.co/spaces/harims95/hobbylm-1b-chat" target="_blank">Online Demo</a> |
<a href="https://huggingface.co/harims95/hobbylm-1B" target="_blank">Base Model</a> |
<a href="https://huggingface.co/harims95/hobbylm-1B-gguf" target="_blank">GGUF</a> |
<a href="https://huggingface.co/harims95/hobbylm-1B-mlx" target="_blank">MLX</a> |
<a href="https://github.com/fuellabs-ai/hobbylm-1b/releases/tag/v1.0.0" target="_blank">Windows App</a> |
<a href="https://github.com/fuellabs-ai/hobbylm-1b" target="_blank">GitHub</a>
</p>
</div>

## Overview

This repository holds the released **instruction-tuned** HobbyLM-1B at its root, so it loads directly without a subfolder. It is the model behind the [online demo](https://huggingface.co/spaces/harims95/hobbylm-1b-chat).

- **What it does:** follows simple instructions, answers short questions, rewrites and extracts text, and makes single function calls in a Python-list format.
- **Lineage:** final annealed base ([`hobbylm-1B`](https://huggingface.co/harims95/hobbylm-1B)) → 2K and 4K context extension → short function-calling stage → instruction tuning (3,450 steps).
- **Context:** configured and trained at **4,096 tokens**. Reliable retrieval across that window is **not** demonstrated (see Limitations).
- **Precision:** **float32 only.** Converting the router to bf16 changes which experts are selected.

## Quickstart

`trust_remote_code=True` is required. Tested with transformers 4.46.3 and torch 2.4.1 (CPU); the model code requires `4.44.0 <= transformers < 4.47.0`.

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

repo = "harims95/hobbylm-1B-instruct"
rev = "0506eed260c353a259712705fa2eb11662f1f0ac"   # verified revision
tok = AutoTokenizer.from_pretrained(repo, revision=rev)
model = AutoModelForCausalLM.from_pretrained(repo, revision=rev, trust_remote_code=True, torch_dtype=torch.float32).eval()

messages = [{"role": "user", "content": "What is the capital of France?"}]
text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
ids = tok(text, return_tensors="pt")
out = model.generate(**ids, max_new_tokens=64, do_sample=False, pad_token_id=50256)
print(tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True))
# The capital of France is Paris.
```

The output shown is the recorded greedy output at this revision.

### Prompt format

The chat template in `tokenizer_config.json` is plain text. Roles are `system`, `user`, `assistant` and `tool`. Each message starts with `<|role|>` on its own line; messages are separated by a newline; the generation prompt is `<|assistant|>`. Generation ends at `<|endoftext|>` (50256).

```
<|user|>
What is the capital of France?
<|assistant|>
```

**Generation defaults** (`generation_config.json`): greedy decoding (`do_sample=false`), up to 256 new tokens.

### More recorded examples (greedy)

```
User: Extract the name, department, and years of experience from this sentence as JSON:
      "Priya Nair, a senior engineer in the Infrastructure department, has 8 years of experience."
HobbyLM: {"name": "Priya Nair", "department": "Infrastructure", "years": 8}
```

```
User: Rewrite this sentence to remove the redundancy: "At this point in time, we are currently
      reviewing the proposal document that was submitted to us."
HobbyLM: We are currently reviewing the proposal document that was submitted to us.
```

These are single recorded outputs, not averages; overall results are below.

## Evaluation (this instruction-tuned model)

All scores are for **this instruction-tuned model** (HobbyLM-1B Instruct), in percent. The base model's scores are on the [base model card](https://huggingface.co/harims95/hobbylm-1B), which also shows both models side by side with standard errors and the full evaluation setup.

| Benchmark | Metric · shots | Instruct |
|---|---|---:|
| HellaSwag | acc_norm · 0-shot | 46.64 |
| ARC-Easy | acc_norm · 0-shot | 54.12 |
| ARC-Challenge | acc_norm · 0-shot | 28.41 |
| PIQA | acc_norm · 0-shot | 68.88 |
| WinoGrande | acc · 0-shot | 50.75 |
| OpenBookQA | acc_norm · 0-shot | 36.20 |
| BoolQ | acc · 0-shot | 53.36 |
| SIQA | acc · 0-shot | 40.33 |
| CommonsenseQA | acc · 0-shot | 18.18 |
| SciQ | acc / acc_norm · 0-shot | 82.70 / 77.10 |
| MMLU | acc · 0-shot | 24.78 |
| BFCL simple (simple_python) | AST accuracy · 0-shot | 59.00 |
| BFCL multiple | AST accuracy · 0-shot | 56.00 |
| BFCL irrelevance | irrelevance (official rule) · 0-shot | 2.50 |
| IFEval, prompt-level strict / loose | accuracy · 0-shot | 22.92 / 24.95 |
| IFEval, instruction-level strict / loose | accuracy · 0-shot | 37.77 / 40.53 |

- **Setup:** lm-evaluation-harness 0.4.13, float32, all examples of each split. BFCL v4 single-turn with the model's trained tool-calling prompt layout. IFEval: all 541 prompts, official scoring, this chat template, greedy, 2,048-token response budget.
- **Weights evaluated:** the same weights as this repository (published at the time as `harims95/hobbylm-1b-broad-sft-3450-hf` @ `ddf46d8f`).
- **Near chance:** CommonsenseQA (20% baseline) and MMLU (25% baseline).
- **BFCL irrelevance 2.50%:** in BFCL's prompt layout, the model almost always calls a tool, even when none fits the request.
- **IFEval:** 135 of 541 responses reached the 2,048-token cap, mostly through repetition; 2 prompts overlap the final instruction-tuning mix and are included.

## Limitations

- **Small model:** answers are often wrong or repetitive, and multi-turn chat is weak. Not a factual reference.
- **No safety tuning and no preference tuning.** Outputs can be wrong, biased or harmful.
- **Function calling:** single calls in the trained format only; weak at declining when no tool fits.
- **Context:** 4,096 tokens is the configured and trained length. Its 4K parent **failed** a 4K retrieval certification, so long-context recall is not demonstrated.
- **Precision:** float32 only. bf16 lowers router top-1 agreement to 62% and full-model output agreement from 97.6% to 61%.
- **Tokenizer:** GPT-2 BPE, English-centric.
- **Runtimes:** Hugging Face's automatic "Use this model" snippets, vLLM and SGLang are not validated. For CPU use, see the [GGUF files](https://huggingface.co/harims95/hobbylm-1B-gguf), which need the patched runtimes from the [v1.0.0 release](https://github.com/fuellabs-ai/hobbylm-1b/releases/tag/v1.0.0).

## Files

`model.safetensors` (float32), `config.json`, `generation_config.json`, `configuration_hobbylm.py`, `modeling_hobbylm.py`, `tokenizer.json`, `vocab.json`, `merges.txt`, `special_tokens_map.json`, `tokenizer_config.json` (with the chat template), `LICENSE`, `NOTICE`. SHA-256 of every file: `SHA256SUMS.txt`.

## License

**Apache License 2.0** (`LICENSE`) for the weights and the model code, a Hugging Face port of the HobbyLM architecture and original model code by Harish ([github.com/harishsg993010/HobbyLM](https://github.com/harishsg993010/HobbyLM)), released with its author's permission. The GPT-2 tokenizer files are MIT (OpenAI). Training data keeps its own terms; attributions and the open training-data licence question are in `NOTICE` and the [base model card](https://huggingface.co/harims95/hobbylm-1B#training-data-licence-notes).

## Credits

Built by **Hariharan** and **Prabhurajhan** at [Fuel Labs](https://fuellabs.in). Architecture based on [HobbyLM](https://github.com/harishsg993010/HobbyLM) by **Harish**.
