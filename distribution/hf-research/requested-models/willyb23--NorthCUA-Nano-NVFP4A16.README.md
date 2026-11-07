---
license: apache-2.0
base_model: beipo-ai/NorthCUA-Nano
base_model_relation: quantized
library_name: transformers
pipeline_tag: image-text-to-text
tags:
- northcua
- computer-use
- agents
- qwen3_5
- nvfp4
- fp4
- compressed-tensors
- vllm
---

# NorthCUA-Nano NVFP4A16

An **NVFP4** quantization of [beipo-ai/NorthCUA-Nano](https://huggingface.co/beipo-ai/NorthCUA-Nano), the 9B dense
computer-use model built on Qwen3.5. The MLP and full-attention projections of the language model are stored in
NVFP4; everything else is BF16. That brings the checkpoint from **18.8 GB to 11.2 GB**: vLLM holds the weights in
10.5 GiB of GPU memory, and on an RTX 5090 a single stream decodes 1.7 times faster than the BF16 original.

> **Grounding accuracy drops.** On ScreenSpot-Pro this checkpoint scores 13.73% against 18.28% for the BF16 original,
> a significant loss (see [Benchmarks](#benchmarks)); math and instruction following are close to the original. If
> click accuracy matters more to you than memory and speed, use the BF16 original. The larger
> [NorthCUA-Mini NVFP4A16](https://huggingface.co/willyb23/NorthCUA-Mini-NVFP4A16) shows no measurable loss.

This is an unofficial community quantization. It is not affiliated with or endorsed by the NorthCUA team or Qwen.
For what the model does and how to use it as an agent, read the
[original model card](https://huggingface.co/beipo-ai/NorthCUA-Nano).

## What was quantized

| Part | Format |
|:--|:--|
| Language model MLP (gate, up and down projections; 32 layers) | **NVFP4**: FP4 (E2M1) weights, one FP8 (E4M3) scale per 16 weights, one FP32 global scale per matrix |
| Full-attention projections (q, k, v, o; 8 layers) | **NVFP4** |
| Linear-attention layers (Gated DeltaNet; 24 layers), norms, embeddings, LM head | BF16 (unchanged) |
| Vision encoder and merger | BF16 (unchanged) |
| Activations | BF16 (weight-only quantization: "A16") |

- Format: [compressed-tensors](https://github.com/vllm-project/compressed-tensors) `nvfp4-pack-quantized`, the same
  layout `llm-compressor` writes for its `NVFP4A16` scheme, so vLLM loads it with no extra flags.
- Method: data-free round-to-nearest using compressed-tensors' own NVFP4 functions, starting from the published BF16
  weights at revision `317dd35`. No calibration data was used.
- Matrices that vLLM fuses share one global scale: q, k and v in each layer, and gate and up in each layer.
- Measured relative weight error (Frobenius norm): 9.49% on average and 9.55% at most over all 128 quantized matrices,
  which is typical for NVFP4 round-to-nearest.

## Running it with vLLM

Tested with vLLM 0.32.0 on one RTX 5090 (32 GB). Serve it with Docker:

```bash
docker run --gpus all --ipc=host -p 8000:8000 vllm/vllm-openai:v0.32.0 \
  willyb23/NorthCUA-Nano-NVFP4A16 \
  --max-model-len 65536 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder
```

or, with vLLM installed (`pip install vllm==0.32.0`), the same flags after `vllm serve willyb23/NorthCUA-Nano-NVFP4A16`.
vLLM takes 92% of the GPU's memory by default; add `--gpu-memory-utilization 0.8` (or lower) if something else uses
the card. vLLM picks its Marlin FP4 kernel for this format by itself.

The examples below were run exactly as written against that server with the `openai` Python client
(`pip install openai`):

```python
import base64
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")
MODEL = "willyb23/NorthCUA-Nano-NVFP4A16"

# 1. Ask about a screenshot (thinking off for a short, direct answer)
image = base64.b64encode(open("screenshot.png", "rb").read()).decode()
resp = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}},
        {"type": "text", "text": "What does this dialog ask, and which buttons does it offer?"},
    ]}],
    max_tokens=256,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)
print(resp.choices[0].message.content)

# 2. Thinking on (the default): the reasoning comes back in its own field
resp = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "A train covers 240 km in 3 hours. How far does it go in 5 hours?"}],
    max_tokens=2048,
)
print(resp.choices[0].message.reasoning)   # vLLM 0.32 returns the thinking as `reasoning`
print(resp.choices[0].message.content)     # "... 80 km/h × 5 h = 400 km."

# 3. A tool call
tools = [{"type": "function", "function": {
    "name": "get_weather", "description": "Current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
resp = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "What's the weather in Paris?"}],
    tools=tools,
    max_tokens=1024,
)
print(resp.choices[0].message.tool_calls[0].function)   # get_weather, arguments '{"city": "Paris"}'
```

For computer use, follow the original card. Run the model with the official
[NorthCUA Harness](https://github.com/china-qijizhifeng/NorthCUA-Harness), and keep every GUI coordinate in the
**normalized 0–1000** space (`normalized_1000`). The Harness converts them to pixels.

### Faster generation with speculative decoding (DFlash)

Nano generates up to about twice as fast with [z-lab/Qwen3.5-9B-DFlash](https://huggingface.co/z-lab/Qwen3.5-9B-DFlash),
a 2.6 GB DFlash drafter made for the base model, Qwen3.5-9B. The drafter proposes several tokens at a time, and Nano
checks them all in one step, so the output still comes from Nano. Add this to the command above (vLLM 0.32 supports
DFlash natively, without `--trust-remote-code`):

```bash
  --speculative-config '{"method": "dflash", "model": "z-lab/Qwen3.5-9B-DFlash", "num_speculative_tokens": 7}'
```

Measured 2026-11-06 on one RTX PRO 6000 Blackwell with vLLM 0.32.0, with a local copy of the drafter at revision
`5fc3b3d`. Greedy decoding; tokens per second after the first token:

| Output | Without drafter | DFlash, 7 tokens |
|:--|--:|--:|
| Computer-use step (screenshot + `click` tool, thinking on), 4 prompts | 155–161 | 184–276 |
| Word problems, thinking on | 158 | 503–551 |
| A short Python function | 158 | 419 |
| Copying a text with small edits, thinking off | 158 | 770 |
| All 8 prompts, one at a time | 158 | **354 (2.25×)** |
| The same prompts, 8 in flight (total) | 715 | **1,203 (1.68×)** |

With 15 drafted tokens, single requests got a little faster still (407 tokens/s) but 8 requests in flight got slower
(1,064), so 7 is the better general setting.

**Accuracy is unchanged within measurement error.** The same benchmarks as below, rerun with the drafter on: ScreenSpot-Pro
14.23% (13.73% without it; paired McNemar p = 0.13), GSM8K 81.20% (81.65%; p = 0.39), IFEval prompt-level strict
67.47% (68.95%; p = 0.27). Verifying several drafted tokens at once uses slightly different GPU arithmetic than
generating one at a time, so a greedy output can change where two tokens are nearly tied. Without the drafter, the
same request always gave the same text.

What did not help: NorthCUA-Nano ships without the multi-token-prediction (MTP) layer of Qwen3.5, and grafting the
base model's MTP layer back gave at most 1.08× (it was not trained on Nano's outputs). Prompt-lookup (n-gram) drafting
gave 1.07×.

### Other inference engines

| Engine | Status |
|:--|:--|
| vLLM 0.32.0 | tested (above) |
| SGLang | not tested yet. It reads compressed-tensors checkpoints, but this NVFP4 weight-only layout has not been tried |
| llama.cpp, Ollama | these load GGUF files, not this format. For them, use a GGUF of the original, such as [mradermacher/NorthCUA-Nano-GGUF](https://huggingface.co/mradermacher/NorthCUA-Nano-GGUF) |
| TensorRT-LLM | not tested. Its NVFP4 support is built around NVIDIA's ModelOpt checkpoint layout, not this one |

## Benchmarks

Run 2026-11-06 against the BF16 original, with identical requests to both versions: vLLM 0.32.0, greedy decoding,
thinking off (`enable_thinking: false`). "Paired p" is an exact McNemar test on the questions where the two versions
disagree; below 0.05 is a real difference.

| Benchmark | This NVFP4 checkpoint | BF16 original | Paired p |
|:--|--:|--:|--:|
| ScreenSpot-Pro, all 1,581 (GUI grounding) | 13.73% | **18.28%** | < 0.001 |
| ScreenSpot-Pro, text targets (977) | 18.63% | 24.97% | |
| ScreenSpot-Pro, icon targets (604) | 5.79% | 7.45% | |
| GSM8K, 1,319 (5-shot, flexible extract) | 81.65% | **83.78%** | 0.047 |
| GSM8K, strict match | 68.23% | 60.96% | |
| IFEval, 541 (prompt-level strict) | 68.95% | 69.32% | 0.91 |
| IFEval, instruction-level strict | 76.38% | 76.62% | |

The quantization costs a quarter of the original's grounding hits (4.55 points, p < 0.001; 95% interval about −6.0
to −3.1 points). GSM8K is inconclusive: 2 points lower under the flexible extraction (p 0.047) but 7 points higher
under strict matching. Instruction following is unchanged. A test version with the attention projections left in
BF16 matched the original on GSM8K (83.62% against 83.78%) but stayed low on grounding (14.23%), so the grounding loss
comes from the quantized MLPs. A calibrated quantization is the next thing to try.

How they were run:
- **ScreenSpot-Pro** ([likaixin/ScreenSpot-Pro](https://huggingface.co/datasets/likaixin/ScreenSpot-Pro), one prediction
  per screenshot, no zoom or crop): a system prompt with the coordinate rules of the NorthCUA Harness (x, y normalized
  to 0–1000 relative to the screenshot) and the Harness's `click` tool as a native tool. Scored by ScreenSpot-Pro's
  rule (the click must fall inside the target box). The NorthCUA authors did not publish their exact grounding prompt,
  so the absolute numbers here are not comparable with their report; the comparison between the two versions is.
- **GSM8K and IFEval**: [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) 0.4.13 through
  the chat-completions API, with the chat template and few-shot examples as turns, up to 2,048 new tokens.

## Tests

Run 2026-11-05 with vLLM 0.32.0 on one RTX 5090 (32 GB), greedy decoding. The BF16 column is the original
checkpoint, served the same way with the same prompts.

| Check | This NVFP4 checkpoint | BF16 original |
|:--|:--|:--|
| Arithmetic probe (17 × 23, thinking off) | 391 | 391 |
| 10 word problems with exact answers (thinking on) | 10/10 | 10/10 |
| 8 short factual questions | 8/8 | 8/8 |
| Tool call (`get_weather`, `qwen3_coder` parser) | correct name and arguments | correct name and arguments |
| A fact hidden in a 25,249-token prompt | found | found |
| Single-stream decode, 512 tokens including prefill | **161 tokens/s** | 93 tokens/s |
| Click points on 5 synthetic screenshots | within 4 units (of 1000) of BF16's point on all 3 screens where BF16 gave one | 3 points, 2 empty replies |

The screenshot check used a plain prompt ("where should I click to ...? reply with (x, y) in 0–1000"), not the
NorthCUA Harness the model is trained for. Both versions missed some targets with that prompt. The row compares the
two versions with each other, not with the targets.

These are smoke tests of this quantized checkpoint, not a re-run of the benchmarks in the NorthCUA report; the
benchmarks above measure its accuracy against the BF16 original.

## Files

The shard boundaries of the original are kept. `checksums.sha256` covers every file
(`sha256sum -c checksums.sha256`).

## License and attribution

Apache-2.0, as for the original. `LICENSE` and `NOTICE` are the original files, unchanged.

**Changes made:** the language model's MLP and full-attention projection weights of NorthCUA-Nano were converted to
NVFP4 (each `.weight` replaced by `.weight_packed`, `.weight_scale` and `.weight_global_scale`), and a
`quantization_config` was added to `config.json`. All other weights, the tokenizer, the processor configuration and
the chat template are unchanged.

If you use the model, cite the NorthCUA technical report (see the original model card).
