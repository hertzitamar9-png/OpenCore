---
library_name: transformers
license: apache-2.0
license_link: LICENSE
pipeline_tag: image-text-to-text
---

<img width="600px" src="assets/title.png">

# Groot-19B-A3B-261023

**Groot** is a family of open-weight reasoning models for complex real-world tasks. The models are compact and easy to deploy, strong on reasoning and tool-using workloads, and efficient with token budgets.

Groot-19B-A3B-261023 has 19B total parameters and activates about 3B per token. It starts from Qwen3.6-35B-A3B and was post-trained on **fewer than 0.1B tokens of open-source data**. It is the smallest model in its comparison group, yet it scores higher than the larger Qwen3.6-35B-A3B and Gemma-4-26B-A4B, and it gives shorter answers than both.

**Where it runs.** We release BF16, **FP8**, **NVFP4** and **GGUF (Q4_K_M)** weights. The device fits below are estimated from memory capacity. BF16 runs on a single 48GB or 80GB GPU (RTX 6000 Ada, A100, H100). FP8 and NVFP4 run the full 256K context on one RTX 5090. GGUF runs on everyday consumer cards: 128K on a 16GB RTX 4060 Ti, 256K on a 24GB RTX 4090 or 3090. It also runs 256K on 128GB unified-memory machines: NVIDIA DGX Spark and other GB10 systems, RTX Spark laptops, AMD Strix Halo mini PCs and Mac M5 Max. Compared with Qwen3.6-35B-A3B: on a 16GB card Groot runs 128K while Qwen3.6 does not load, and on an RTX 5090 Groot runs 256K while Qwen3.6 runs 64K.

## Model size vs. capability

The chart compares total model parameters with the unweighted average across all 16 benchmarks. Smaller models appear farther left and higher average scores appear higher. Groot-19B-A3B uses 45.7% fewer total parameters than Qwen3.6-35B-A3B and 26.9% fewer than Gemma-4-26B-A4B, while averaging 41.44 compared with 38.08 and 30.05. This places it ahead of both larger models on the size and capability trade-off.

<img width="900px" src="assets/scatter_19b.png">

## Benchmarks

**Accuracy.** Groot-19B-A3B-261023 beats both Qwen3.6-35B-A3B and Gemma-4-26B-A4B on 12 of the 16 benchmarks, for example Terminal Bench 2.1 (52.10 vs 43.82 / 26.52), NL2Repo-Bench (35.65 vs 28.77 / 16.55) and LongBench v2 (62.03 vs 48.12 / 47.99).

**Token budget.** Across these 16 evaluation runs, Groot-19B-A3B has the shortest average output among all 12 measured models: 23.1% fewer output tokens than Qwen3.6-35B-A3B and 65.6% fewer than Gemma-4-26B-A4B. Read the token totals together with the accuracy results above, since a smaller token budget is useful only when task quality is maintained.

<img width="100%" src="assets/bars_19b.png">

<p style="margin-top:12px;font-size:7px;opacity:0.7">
* Each benchmark was evaluated in thinking mode at its default reasoning effort. Unless otherwise stated, sampling uses temp=1.0, top_p=0.95, top_k=20, and the model's default context window of 256K tokens.<br/>
* Terminal-Bench 2.1: Evaluated with the Claude Code agent; max_output_tokens=128K, max_turns=500, 12h timeout; temp=1.0, top_p=0.95, top_k=20.<br/>
* ALE Agent: Evaluated with ale_claude on top of Claude Code 2.1.170; max_output_tokens=128K, 4h timeout (extended up to 8h for specific long-running tasks); temp=1.0, top_p=0.95, top_k=20.<br/>
* Toolathlon Verified: Reported as pass@1, averaged over 3 independent runs; temp=1.0, top_p=0.95, top_k=20.<br/>
* AutomationBench: Evaluated on AutomationBench v1.0.6; max_turns=50; temp=1.0, top_p=0.95, top_k=20.<br/>
* JobBench: max_tokens=200K, 600s timeout; temp=1.0, top_p=0.95, top_k=20.<br/>
* DeepSWE 1.1: Evaluated with the mini-swe-agent scaffold; 11h timeout; temp=1.0, top_p=0.95, top_k=20.<br/>
* SWE-bench Pro: Evaluated with the SWE-agent scaffold; temp=1.0, top_p=0.95, top_k=20.<br/>
* NL2Repo-Bench: Evaluated with the Claude Code agent; max_turns=1000, 80K context window; temp=1.0, top_p=0.95, top_k=20.<br/>
* HLE (with tools): Evaluated with gpt-5.5 as the judge model; max_iterations=1000, 6h timeout, 256K context window; temp=1.0, top_p=0.95, top_k=20.<br/>
</p>

---

## Model Overview

- **Type**: Natively multimodal Mixture-of-Experts causal language model
- **Total Parameters**: ~19B, plus a 0.4B MTP layer
- **Activated Parameters**: ~3B per token
- **Layers**: 40 (10 full-attention, 30 linear-attention)
- **Hidden Size**: 2048
- **Experts**: 128 routed + 1 shared, 8 routed experts activated per token
- **Context Length**: 262,144 natively, extensible to ~1M tokens with YaRN
- **Precision**: BF16, with FP8, NVFP4 and GGUF releases available
- **Multi-Token Prediction**: 1 layer

Available editions:

| Edition | Precision | Size on disk |
| :--- | :--- | :--- |
| `Groot-19B-A3B-261023` | BF16 | 38.9 GB |
| `Groot-19B-A3B-261023-FP8` | FP8 (E4M3, dynamic) | 20.9 GB |
| `Groot-19B-A3B-261023-NVFP4` | NVFP4 experts, FP8 attention | 13.5 GB |
| `Groot-19B-A3B-261023-GGUF` | Q4_K_M | 11.7 GB |

---

## Quickstart

**Groot-19B-A3B-261023** is a mixture-of-experts model with ~19B total parameters and ~3B activated parameters per token.

The model operates in **thinking mode by default**: the assistant turn opens with a `<think> … </think>` block before the final answer. It has a default context length of **262,144** tokens and natively supports tool calling.

The serving recipes below enable a **reasoning parser** (so the chain-of-thought is returned in a separate `reasoning_content` field) and a **tool-call parser** (so `<tool_call>` blocks are surfaced as OpenAI-style `tool_calls`).

Serving Groot-19B-A3B-261023 requires recent runtimes:

- **Transformers** ≥ 5.8.1
- **vLLM** ≥ 0.19.1
- **SGLang** ≥ 0.5.10

Recommended sampling parameters:

- **General tasks (thinking mode)**: `temperature=1.0, top_p=0.95, top_k=20, min_p=0.0, presence_penalty=1.5, repetition_penalty=1.0`
- **Precise coding tasks (e.g., WebDev)**: `temperature=0.6, top_p=0.95, top_k=20, min_p=0.0, presence_penalty=0.0, repetition_penalty=1.0`
- **Instruct (or non-thinking) mode**: `temperature=0.7, top_p=0.80, top_k=20, min_p=0.0, presence_penalty=1.5, repetition_penalty=1.0`

> Please note that the support for sampling parameters varies according to inference frameworks.

---

### Serving Groot-19B-A3B-261023

The BF16 weights (about 39 GB) fit on a single 48GB or 80GB GPU. The recipes below use **2× 80GB GPUs** to reserve memory for KV cache at the full 256K context. The GPU count you need depends on context length, KV cache and framework overhead; adjust `--tensor-parallel-size` / `--tp-size` to match your hardware.

> If you run into out-of-memory (OOM) errors, consider reducing the context window. Because the model relies on extended context for complex tasks, we recommend keeping at least **128K** tokens to preserve thinking capabilities.

#### SGLang

SGLang is a fast serving framework for large language models. `sglang>=0.5.10` is recommended, which can be installed in a fresh environment with:

```shell
uv pip install sglang[all]
```

The following commands create API endpoints at `http://localhost:8000/v1`:

- **Standard Version**: an endpoint with maximum context length 262,144 tokens using tensor parallel on 2 GPUs.

```shell
python -m sglang.launch_server \
    --model-path Groot/Groot-19B-A3B-261023 \
    --served-model-name Groot-19B-A3B-261023 \
    --host 0.0.0.0 --port 8000 \
    --tp-size 2 \
    --mem-fraction-static 0.85 \
    --context-length 262144 \
    --reasoning-parser qwen3
```

- **Tool Use**: to support tool calling.

```shell
python -m sglang.launch_server \
    --model-path Groot/Groot-19B-A3B-261023 \
    --served-model-name Groot-19B-A3B-261023 \
    --host 0.0.0.0 --port 8000 \
    --tp-size 2 \
    --mem-fraction-static 0.85 \
    --context-length 262144 \
    --reasoning-parser qwen3 \
    --tool-call-parser qwen3_coder
```

- **Multi-Token Prediction (MTP)**: the recommended configuration for MTP.

```shell
python -m sglang.launch_server \
    --model-path Groot/Groot-19B-A3B-261023 \
    --served-model-name Groot-19B-A3B-261023 \
    --host 0.0.0.0 --port 8000 \
    --tp-size 2 \
    --mem-fraction-static 0.85 \
    --context-length 262144 \
    --reasoning-parser qwen3 \
    --speculative-algo NEXTN \
    --speculative-num-steps 3 \
    --speculative-eagle-topk 1 \
    --speculative-num-draft-tokens 4
```

#### vLLM

vLLM is a high-throughput and memory-efficient inference and serving engine for LLMs. `vllm>=0.19.1` is recommended:

```shell
uv pip install vllm --torch-backend=auto
```

- **Standard Version**: an endpoint with maximum context length 262,144 tokens using tensor parallel on 2 GPUs.

```shell
vllm serve Groot/Groot-19B-A3B-261023 \
    --served-model-name Groot-19B-A3B-261023 \
    --host 0.0.0.0 --port 8000 \
    --tensor-parallel-size 2 \
    --max-model-len 262144 \
    --gpu-memory-utilization 0.90 \
    --enable-prefix-caching \
    --reasoning-parser qwen3
```

- **Tool Call**: to support tool use.

```shell
vllm serve Groot/Groot-19B-A3B-261023 \
    --served-model-name Groot-19B-A3B-261023 \
    --host 0.0.0.0 --port 8000 \
    --tensor-parallel-size 2 \
    --max-model-len 262144 \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    --reasoning-parser qwen3
```

- **Multi-Token Prediction (MTP)**: the recommended configuration for MTP.

```shell
vllm serve Groot/Groot-19B-A3B-261023 \
    --served-model-name Groot-19B-A3B-261023 \
    --host 0.0.0.0 --port 8000 \
    --tensor-parallel-size 2 \
    --max-model-len 262144 \
    --reasoning-parser qwen3 \
    --speculative-config '{"method":"qwen3_next_mtp","num_speculative_tokens":2}'
```

#### KTransformers

KTransformers is a flexible framework for LLM inference with CPU-GPU heterogeneous computing, which works well for MoE models under limited GPU memory. Point `--model-path` at `Groot/Groot-19B-A3B-261023` and follow the official KTransformers documentation for MoE models.

#### Hugging Face Transformers

Hugging Face Transformers ships a *lightweight* server that is handy for quick testing and moderate-load deployment. The latest `transformers` is required:

```shell
pip install "transformers[serving]"
```

Then run `transformers serve` to launch a server with API endpoints at `http://localhost:8000/v1`; it will place the model on accelerators if available:

```shell
transformers serve Groot/Groot-19B-A3B-261023 --port 8000 --continuous-batching
```

You can also load the model locally with Transformers:

```python
from transformers import AutoModelForCausalLM, AutoTokenizer

model_name = "Groot/Groot-19B-A3B-261023"

tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    dtype="auto",
    device_map="auto",
)

messages = [{"role": "user", "content": "Give me a short introduction to large language models."}]
text = tokenizer.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True,
)
model_inputs = tokenizer([text], return_tensors="pt").to(model.device)

generated_ids = model.generate(
    **model_inputs,
    max_new_tokens=8192,
    temperature=1.0,
    top_p=0.95,
    top_k=20,
)
output_ids = generated_ids[0][len(model_inputs.input_ids[0]):]
print(tokenizer.decode(output_ids, skip_special_tokens=True))
```

#### Long Context (YaRN Extension)

The model natively handles context windows of up to 262,144 tokens. When a task's combined input and output must go beyond this limit, we suggest extending the effective window with RoPE scaling. YaRN is built into both vLLM and SGLang, and a scaling factor of 4.0 grows the usable window to roughly 1M tokens.

You can turn YaRN on in either of two ways:

- **Edit the checkpoint's `config.json`.** Add a `rope_scaling` block to the model configuration:

```json
{
    "rope_scaling": {
        "rope_type": "yarn",
        "factor": 4.0,
        "original_max_position_embeddings": 262144
    }
}
```

- **Override at launch time.** Leave the checkpoint untouched and extend the serve commands above with the equivalent flags.

```shell
# vLLM
VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 vllm serve Groot/Groot-19B-A3B-261023 \
    --hf-overrides '{"rope_scaling": {"rope_type": "yarn", "factor": 4.0, "original_max_position_embeddings": 262144}}' \
    --max-model-len 1000000
```

```shell
# SGLang
SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN=1 python -m sglang.launch_server \
    --model-path Groot/Groot-19B-A3B-261023 \
    --json-model-override-args '{"rope_scaling": {"rope_type": "yarn", "factor": 4.0, "original_max_position_embeddings": 262144}}' \
    --context-length 1000000
```

> Open-source runtimes implement YaRN *statically*: the same scaling factor is applied to every request regardless of its length, which can slightly hurt quality on ordinary-length inputs. Only enable `rope_scaling` when your workload genuinely needs the longer window, and size `factor` to match it. The target window is roughly `factor` × 262,144, so if your requests top out around 524,288 tokens, `factor: 2.0` is the better setting.

---

### Using Groot-19B-A3B-261023 via the Chat Completions API

Once a vLLM or SGLang server is running, talk to it with any OpenAI-compatible client. Before starting, make sure the SDK is installed and the API base URL and API key are configured:

```shell
pip install -U openai

# Set the following accordingly
export OPENAI_BASE_URL="http://localhost:8000/v1"
export OPENAI_API_KEY="EMPTY"
```

#### Text Input

```python
from openai import OpenAI

# Configured by environment variables
client = OpenAI()

messages = [
    {"role": "user", "content": "Type \"I love Groot\" backwards"},
]

chat_response = client.chat.completions.create(
    model="Groot-19B-A3B-261023",
    messages=messages,
    max_tokens=81920,
    temperature=1.0,
    top_p=0.95,
    presence_penalty=1.5,
    extra_body={
        "top_k": 20,
    },
)
print("Chat response:", chat_response)
```

#### Instruct (or Non-Thinking) Mode

The model thinks by default before responding. You can obtain a direct response without thinking by configuring the API parameters:

```python
from openai import OpenAI
client = OpenAI()

messages = [
    {"role": "user", "content": "Where is the Eiffel Tower?"}
]

chat_response = client.chat.completions.create(
    model="Groot-19B-A3B-261023",
    messages=messages,
    max_tokens=32768,
    temperature=0.7,
    top_p=0.8,
    presence_penalty=1.5,
    extra_body={
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": False},
    },
)
print("Chat response:", chat_response)
```

> The model does not support in-conversation soft switches such as `/think` or `/nothink`; thinking can only be controlled through the API parameters shown above.

#### Preserve Thinking

By default, only the thinking blocks generated in handling the latest user message are retained, resulting in a pattern commonly known as interleaved thinking. The model can also preserve and leverage thinking traces from historical messages, which you can enable with the `preserve_thinking` option:

```python
from openai import OpenAI
client = OpenAI()

messages = [...]

chat_response = client.chat.completions.create(
    model="Groot-19B-A3B-261023",
    messages=messages,
    max_tokens=32768,
    temperature=0.7,
    top_p=0.8,
    presence_penalty=1.5,
    extra_body={
        "top_k": 20,
        "chat_template_kwargs": {"preserve_thinking": True},
    },
)
print("Chat response:", chat_response)
```

This capability is particularly beneficial for agent scenarios, where maintaining full reasoning context can enhance decision consistency and, in many cases, reduce overall token consumption by minimizing redundant reasoning. It also improves KV cache utilization in both thinking and non-thinking modes.

#### Tool Calling

The model emits well-formed function calls that the server parses into the standard `tool_calls` field:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]

response = client.chat.completions.create(
    model="Groot-19B-A3B-261023",
    messages=[{"role": "user", "content": "What is the weather in Paris right now?"}],
    tools=tools,
    tool_choice="auto",
    temperature=0.6,
    top_p=0.95,
    max_tokens=2048,
    extra_body={"top_k": 20},
)

message = response.choices[0].message
# reasoning_content holds the <think> trace; content holds the final answer.
print("reasoning:", getattr(message, "reasoning_content", None))
print("answer:", message.content)

tool_call = message.tool_calls[0]
print(tool_call.function.name, tool_call.function.arguments)
# -> get_weather {"city": "Paris"}
```

---

### Agentic Usage

Groot-19B-A3B-261023 excels in tool calling and agentic coding. It exposes an OpenAI-compatible endpoint and works out of the box with standard agent frameworks and coding CLIs:

```bash
# Any OpenAI-compatible coding CLI / agent framework: just point it at your endpoint.
export OPENAI_BASE_URL="http://localhost:8000/v1"
export OPENAI_API_KEY="EMPTY"
export OPENAI_MODEL="Groot-19B-A3B-261023"
```

---

## Citation

If you use this model in your work, please cite it as:

```bibtex
@misc{groot2026,
  title  = {Scaling Down LLMs for Broader Deployment},
  author = {The Groot Team},
  year   = {2026},
}
```
