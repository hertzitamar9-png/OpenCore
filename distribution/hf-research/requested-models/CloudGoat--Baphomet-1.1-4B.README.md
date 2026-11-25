---
language:
- en
license: apache-2.0
library_name: transformers
pipeline_tag: image-text-to-text
base_model:
- Qwen/Qwen3.5-4B
- InternScience/Agents-A1-4B
- Jackrong/DeepSeek-V4-Pro-Qwen3.5-4B
- empero-ai/Qwen3.8-4B-Distill
- Jackrong/Qwopus3.5-4B-Coder
- Jackrong/Qwopus3.5-4B-v3
- BAAI/AREX-Turbo
- TokenRhythm/NeoHorse-1-4B
- allenai/tmax-4b
base_model_relation: merge
tags:
- merge
- evogm
- evolutionary-merge
- task-vector
- linear-merge
- qwen3_5
- vision-language
---

<div align="center">

# Baphomet-1.1-4B

<p>Linear merge of 8 task-vectors on Qwen3.5-4B, with per-donor coefficients found by an evolutionary search (EvoGM)</p>

</div>

<div align="center">
<table>
<tr>
<th>Base</th>
<th>Method</th>
<th>Task-Vectors</th>
<th>Output</th>
</tr>
<tr>
<td><code>Qwen/Qwen3.5-4B</code></td>
<td><code>linear</code>, λ searched with <a href="https://github.com/JiangTao97/evogm">EvoGM</a></td>
<td>8 vectors, searched λ (Σλ ≈ 1.139)</td>
<td><code>safetensors ×2</code> (bf16)</td>
</tr>
</table>
</div>

> 日本語: Baphomet-1.0 と同じ 8 個のタスクベクトルを、EvoGM の進化的探索で求めた係数 λ で線形マージしたモデルです。

---

## 1. Overview

Baphomet-1.1-4B is the successor of [Baphomet-1.0-4B](https://huggingface.co/CloudGoat/Baphomet-1.0-4B). It uses the same base model and the same 8 donor task-vectors (`T = W_donor − W_base`), but instead of one uniform weight (0.1375 for every donor in 1.0), each donor gets its own coefficient λᵢ found by an evolutionary search.

```text
W_merged = W_base + Σ λᵢ · Tᵢ
```

On a 180-item paired test (MMLU-Pro / GPQA-Diamond / BFCL non-live, 8192-token thinking cap), Baphomet-1.1 scored **79.4%**, against 77.8% for Baphomet-1.0 and 70.0% for Qwen3.5-4B. The gain over Qwen3.5-4B is statistically significant. The gain over Baphomet-1.0 is **not** (see Section 6). It also thinks shorter: mean output 1,834 tokens vs 3,180 for 1.0.

---

## 2. Merge configuration

### 2.1 Coefficients (λ, donor order as searched)

| # | Donor | λ |
|:---:|:---|---:|
| 1 | [`InternScience/Agents-A1-4B`](https://huggingface.co/InternScience/Agents-A1-4B) | 0.011589 |
| 2 | [`Jackrong/DeepSeek-V4-Pro-Qwen3.5-4B`](https://huggingface.co/Jackrong/DeepSeek-V4-Pro-Qwen3.5-4B) | 0.203923 |
| 3 | [`empero-ai/Qwen3.8-4B-Distill`](https://huggingface.co/empero-ai/Qwen3.8-4B-Distill) | 0.590137 |
| 4 | [`Jackrong/Qwopus3.5-4B-Coder`](https://huggingface.co/Jackrong/Qwopus3.5-4B-Coder) | 0.001337 |
| 5 | [`Jackrong/Qwopus3.5-4B-v3`](https://huggingface.co/Jackrong/Qwopus3.5-4B-v3) | 0.297193 |
| 6 | [`BAAI/AREX-Turbo`](https://huggingface.co/BAAI/AREX-Turbo) | 0.032303 |
| 7 | [`TokenRhythm/NeoHorse-1-4B`](https://huggingface.co/TokenRhythm/NeoHorse-1-4B) | 0.002979 |
| 8 | [`allenai/tmax-4b`](https://huggingface.co/allenai/tmax-4b) | −0.000742 |

Most of the weight sits on three donors: Qwen3.8-4B-Distill (0.59), Qwopus3.5-4B-v3 (0.30) and DeepSeek-V4-Pro-Qwen3.5-4B (0.20). Full-precision values (float32):

```json
[0.011588579043745995, 0.203922837972641, 0.5901365876197815, 0.0013365253107622266,
 0.2971934676170349, 0.032303329557180405, 0.002979394979774952, -0.0007424817304126918]
```

### 2.2 How the weights are computed

- `Tᵢ = W_donorᵢ − W_base` in fp32, per tensor. Each task-vector is stored as fp8 e4m3 with a per-row absmax scale (`scale = rowmax|T| / 448`). Tensors where a donor equals the base exactly are skipped.
- `W = W_base + Σ λᵢ · dequant(Tᵢ)`, accumulated in fp32 in donor order, then cast back to each base tensor's dtype (bf16; the `A_log` / norm tensors the base stores in fp32 stay fp32).
- A tensor a donor does not have contributes 0. Agents-A1-4B and AREX-Turbo have no MTP tensors. NeoHorse-1-4B and tmax-4b are text-only. NeoHorse's keys were remapped `model.*` → `model.language_model.*`.
- Key names, shapes, dtypes and the 2-shard layout are identical to `Qwen/Qwen3.5-4B`. Tokenizer, chat template and processor configs are copied unchanged from the base.

The fp8 task-vector storage changes the merged bf16 weights by at most about 1 bf16 ulp in some elements, compared with an exact fp32 merge. These are the same weights that were evaluated. Note that vLLM loaded them as fp16 for evaluation.

### 2.3 Search (EvoGM)

The coefficients were searched with a port of **EvoGM** ("Learning to Merge LLMs via Evolutionary Generative Optimization", ICML 2026, [github.com/JiangTao97/evogm](https://github.com/JiangTao97/evogm)). It uses dual MLP generators trained on winner/loser splits of the population, λᵢ ∈ [−1, 1], and basis-shift rounds.

- **Round 1:** 40 candidates, each scored on a 110-item fitness set (40 MMLU-Pro + 30 GPQA + 40 BFCL non-live). Accuracy is computed per task and then averaged. Generation used thinking mode with a 4096-token cap, and truncated answers count as wrong.
- **Rounds 2–3 (IRT-accelerated):** 64 more candidates were scored on a 40-item subset chosen for Fisher information, with the rest of the 110-item score estimated by a 2PL IRT model. Clearly weak candidates were stopped early. The best estimates were re-scored on all 110 items, and the winner is chosen by the exact 110-item score.
- **Fitness (110 items, 4096 cap):** Baphomet-1.0 λ 0.603 → best of round 1 0.722 → round 2 0.733 → **Baphomet-1.1 0.764**.
- The fitness items are disjoint from the test items in Section 6.
- **Compute:** about 13.7 kernel-hours on Kaggle T4×2 (round 1: 7.1 h, rounds 2–3: 6.5 h), using vLLM 0.33.0.

---

## 3. Model specifications

Same architecture as the base (excerpt from `config.json`).

| Item | Value |
|:---|:---|
| Architecture | `Qwen3_5ForConditionalGeneration` |
| Text layers | 32 layers, hidden size `2560`, heads `16Q / 4KV (GQA)`, `head_dim 256` |
| Attention layer types | Repeating pattern of 3 `linear_attention` layers followed by 1 `full_attention` layer |
| FFN | `intermediate 9216`, `SiLU`, `tie_word_embeddings: true` |
| MTP | 1 layer (`mtp_num_hidden_layers: 1`) |
| Vocabulary size | `248,320` (`Qwen2Tokenizer`, BPE) |
| Context length | `262,144` |
| Dtype | `bfloat16` (fp32 for `A_log` / norm tensors, as in the base) |

---

## 4. Included files

| File | Description |
|:---|:---|
| `model.safetensors-00001-of-00002.safetensors`, `model.safetensors-00002-of-00002.safetensors` | Model weights (bf16, 9.3 GB total), same layout as the base |
| `model.safetensors.index.json` | Shard index |
| `config.json` | Model configuration (base) |
| `generation_config.json` | Default sampling as used in evaluation |
| `tokenizer.json` / `vocab.json` / `merges.txt` / `tokenizer_config.json` / `chat_template.jinja` | Tokenizer and chat template (base) |
| `preprocessor_config.json` / `video_preprocessor_config.json` | Processor configuration (base) |

---

## 5. Usage

### 5.1 vLLM

```bash
vllm serve CloudGoat/Baphomet-1.1-4B --max-model-len 32768
```

### 5.2 Transformers

```python
from transformers import AutoModelForImageTextToText, AutoProcessor

model_id = "CloudGoat/Baphomet-1.1-4B"
model = AutoModelForImageTextToText.from_pretrained(model_id, dtype="auto", device_map="auto")
processor = AutoProcessor.from_pretrained(model_id)

messages = [{"role": "user", "content": [{"type": "text", "text": "Explain task arithmetic in two sentences."}]}]
inputs = processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=True,
                                       return_dict=True, return_tensors="pt").to(model.device)
out = model.generate(**inputs, max_new_tokens=4096, do_sample=True, temperature=0.6, top_p=0.95, top_k=20)
print(processor.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=False))
```

### 5.3 Recommended sampling

Every evaluation below used thinking mode (`enable_thinking=True`, the default) with **`temperature=0.6, top_p=0.95, top_k=20`**. This matches the Qwen3.5 recommendation for thinking mode on precise tasks. For general chat, the Qwen3.5 card also suggests `temperature=1.0, top_p=0.95, top_k=20, presence_penalty=1.5`. That setting was not evaluated for this merge.

---

## 6. Benchmark

180-item paired test: the same items, prompts and settings for all three models. The items are MMLU-Pro (n=80), GPQA-Diamond (n=50) and BFCL non-live AST (n=50). Generation used thinking mode with an 8192-token cap and `temperature=0.6, top_p=0.95, top_k=20, seed=0`, with one sample per item. Truncated answers count as wrong. Results come from vLLM 0.33.0 on T4 (fp16).

| Model | Overall | MMLU-Pro | GPQA-Diamond | BFCL non-live | Truncated | Mean output tokens |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| Qwen/Qwen3.5-4B | 70.0% | 71.3% | 42.0% | 96.0% | 22.8% | 3,983 |
| Baphomet-1.0-4B | 77.8% | 77.5% | 58.0% | 98.0% | 15.6% | 3,180 |
| **Baphomet-1.1-4B** | **79.4%** | **81.2%** | **62.0%** | 94.0% | 10.0% | 1,834 |

Paired exact McNemar tests (overall, 180 items):

| Comparison | 1.1 right / other wrong | 1.1 wrong / other right | p |
|:---|:---:|:---:|:---:|
| vs Baphomet-1.0-4B | 10 | 7 | 0.63 (not significant) |
| vs Qwen3.5-4B | 20 | 3 | < 0.001 |

### Caveats

- **Small test.** With 180 items, one item is worth 0.56 points. The +1.7-point difference vs Baphomet-1.0 is within noise. The only robust claim is the improvement over the base model.
- **Single sample at temperature 0.6.** Scores from one seeded sample per item vary between runs.
- **Winner's curse.** λ was chosen as the best of more than 100 candidates on a 110-item fitness set. The fitness score (0.764) is therefore optimistic. The held-out test items in this table were not used in the search.
- BFCL dropped slightly (94% vs 98% for 1.0; 2 items).
- None of the donors changes the vision tower or the MTP layer, so those tensors are identical to Qwen3.5-4B. Image input was not evaluated.

---

## 7. Limitations

- The model is the result of a linear merge, and there is no guarantee that characteristics of individual donor models are preserved.
- It may inherit the license terms and biases of each donor and the base model. Determine the scope of use after reviewing the license of each model.
- Only the three benchmarks above were measured.
- Use for final decisions in high-risk domains such as medical or legal matters is not intended.

---

## 8. Credits

| Role | Model / work |
|:---|:---|
| Base | [Qwen/Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) |
| Donors | InternScience/Agents-A1-4B, Jackrong/DeepSeek-V4-Pro-Qwen3.5-4B, empero-ai/Qwen3.8-4B-Distill, Jackrong/Qwopus3.5-4B-Coder, Jackrong/Qwopus3.5-4B-v3, BAAI/AREX-Turbo, TokenRhythm/NeoHorse-1-4B, allenai/tmax-4b |
| Predecessor | [CloudGoat/Baphomet-1.0-4B](https://huggingface.co/CloudGoat/Baphomet-1.0-4B) |
| Search method | EvoGM: Learning to Merge LLMs via Evolutionary Generative Optimization (ICML 2026), [github.com/JiangTao97/evogm](https://github.com/JiangTao97/evogm) |
| References | [O. K. Hitit, C. Sankar, Y. K. Kim, A. Risteski, and A. Elgohary, "A systematic study of in-the-wild model merging for large language models," arXiv:2511.21437, 2025.](https://arxiv.org/pdf/2511.21437) |
