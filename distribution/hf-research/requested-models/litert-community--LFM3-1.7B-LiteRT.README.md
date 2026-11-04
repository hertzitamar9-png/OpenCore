---
license: other
license_name: lfm1.0
license_link: https://huggingface.co/litert-community/LFM3-1.7B-LiteRT/blob/main/LICENSE
base_model: LiquidAI/LFM3-1.7B
base_model_relation: quantized
pipeline_tag: text-generation
library_name: litert-lm
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
tags:
- litert
- litert-lm
- tflite
- on-device
- edge
- liquid
- lfm3
- conversational
---

# LFM3-1.7B — LiteRT-LM

This repository is a conversion of [LiquidAI/LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B) to LiteRT-LM, made with litert-torch (commit f61d0b7 of its main branch) and litert-lm-builder 0.18.1 for LiteRT-LM 0.18.1: on a Galaxy S26 GPU the mixed int4 file decodes 32.42 tokens/s after a 1,024-token prompt (mean of two runs, 2026-10-29).

[LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B) is Liquid AI's text model for local agents and specialized applications: 1.73B parameters in 24 layers (13 gated short convolutions, 5 sliding-window attention, 6 global attention), a 128,000-token vocabulary, 16 languages. By default it thinks in a `<think>` block before it answers, and it writes tool calls in an XML format. The source card does not recommend it for agentic coding or complex multi-step reasoning. Liquid AI also publishes [GGUF](https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF), [MLX](https://huggingface.co/LiquidAI/LFM3-1.7B-MLX-4bit), [ONNX](https://huggingface.co/LiquidAI/LFM3-1.7B-ONNX), [FP8](https://huggingface.co/LiquidAI/LFM3-1.7B-FP8) and [NVFP4](https://huggingface.co/LiquidAI/LFM3-1.7B-NVFP4) checkpoints of this model.

## Files

| File | What is inside | Size | sha256 |
|---|---|---|---|
| `LFM3-1.7B_mixed_int4_ekv2048.litertlm` | MLP gate and up projections in int4 (blockwise, 32 weights per block); every other weight in int8 per channel (attention, short convolutions, MLP down projection, the shared embedding / output table). KV cache of 2,048 tokens. | 1,441,620,928 B (1.44 GB) | `0400d8ffdb00e4bbe1209120e2c7baf05322a8c900839b0a94cc28f599011196` |
| `LFM3-1.7B_mixed_int4_ekv4096.litertlm` | The same weights, KV cache of 4,096 tokens | 1,441,629,120 B (1.44 GB) | `327486bf01d0b1eefd727b7aadec8627a2b8db143349262857ac98403109d72b` |
| `LFM3-1.7B_int8_ekv2048.litertlm` | Every weight in int8 per channel. KV cache of 2,048 tokens. | 1,801,549,264 B (1.80 GB) | `fb8af4d6cfb71d0f381a2af23f14733c3adbd93171b251560ee899276926001a` |
| `LFM3-1.7B_int8_ekv4096.litertlm` | The same weights, KV cache of 4,096 tokens | 1,801,557,456 B (1.80 GB) | `8b83651037fcec23f232e8f4e670683a44cc7097a4d5f74a1abddeb7bc0dae27` |
| `LICENSE` | The source model's license file, unchanged | 10,574 B | |
| `litertlm_manifest.json` | Machine-readable description of the four files | 24.5 kB | |

Every file has prefill signatures of 1,024, 256, 64, 16, 4 and 1 tokens, the source repository's `tokenizer.json` byte for byte, the chat template described under Conversion notes, a thought channel (`<think>` … `</think>`) and the sampler top-k 50 at temperature 0.1 (the source card's General setting, without its repetition penalty). No file declares an activation type, so on a GPU LiteRT-LM runs them with fp16 activations.

Which file:

- **GPU**: the mixed int4 files decode faster (Galaxy S26: 32.42 against 28.86 tokens/s with the 2,048-token cache). The int8 files stay closer to the source model: on a Mac GPU (the LiteRT runtime's Metal path) their top token matched the source model's in float32 at 95.7 % of 2,682 test positions, the mixed int4 file's at 79.9 % (Accuracy).
- **Cache length**: on the Pixel 8a GPU the 4,096-token files decode faster than the 2,048-token ones (15.13 against 12.77 tokens/s for mixed int4); on the Galaxy S26 the 2,048-token files are the faster ones. Take a 4,096-token file for long thinking.
- **CPU**: take a mixed int4 file. It decodes faster (Galaxy S26 CPU: 35.53 against 31.06 tokens/s for the int8 2,048 weights in the same session, Performance) and answered all 8 check questions on every CPU we ran, where the int8 files missed the rhyme. The int8 files on a CPU also agree less with the source model than on a GPU (top-token agreement 88.7 % on the Mac CPU against 95.7 % on its GPU; the mixed int4 file: 77.9 % on the Mac CPU).

## Usage

One question with the LiteRT-LM command-line tool, thinking off:

```bash
pip install -U huggingface_hub
hf download litert-community/LFM3-1.7B-LiteRT LFM3-1.7B_mixed_int4_ekv2048.litertlm --local-dir .
uvx --from litert-lm==0.18.1 litert-lm run LFM3-1.7B_mixed_int4_ekv2048.litertlm --backend gpu --cache no --thinking false --prompt "What is the capital of Japan? Answer briefly." < /dev/null
```

Without `--thinking false` the model thinks first and the tool prints the thought between `[thought]` and `[/thought]` before the answer. `--backend cpu` runs it on the CPU.

The same from Python (`pip install litert-lm==0.18.1`), and a tool call: the model writes the call as text in its XML format, and the app reads it and sends the result back as a `tool` message.

```python
import re
import litert_lm

MODEL = "LFM3-1.7B_mixed_int4_ekv2048.litertlm"
NO_THINKING = litert_lm.ThinkingConfig(enable_thinking=False)


def text(reply):
    return "".join(part["text"] for part in reply["content"] if part.get("type") == "text")


# One Engine per conversation (Limitations).
with litert_lm.Engine(MODEL, backend=litert_lm.Backend.GPU(), cache_dir=":nocache") as engine:
    conversation = engine.create_conversation(thinking_config=NO_THINKING)
    print(text(conversation.send_message("What is the capital of Japan? Answer briefly.")))

weather = {"type": "function", "function": {
    "name": "get_weather", "description": "Current weather in a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}
with litert_lm.Engine(MODEL, backend=litert_lm.Backend.GPU(), cache_dir=":nocache") as engine:
    conversation = engine.create_conversation(extra_context={"tools": [weather]}, thinking_config=NO_THINKING)
    reply = text(conversation.send_message("What is the weather in Paris right now?"))
    call = re.search(r"<tool_call>\s*<function=([^\n>]+)>(.*?)</function>\s*</tool_call>", reply, re.S)
    name = call.group(1).strip()
    args = dict(re.findall(r"<parameter=([^>\n]+)>\n?(.*?)\n?</parameter>", call.group(2), re.S))
    print(name, args)
    result = "Paris: 18 °C, light rain"  # run the tool with name and args in your app
    print(text(conversation.send_message({"role": "tool", "content": result})))
```

Thinking is on by default, as in the source model. Leave out `thinking_config` (or pass `enable_thinking=True`) to turn it on; the thought then comes back in `reply["channels"]["thought"]` and the answer in `reply["content"]`. Pass `litert_lm.Backend.CPU()` to run on the CPU.

On Android, the Galaxy S26 and Pixel 8a numbers below come from `litert_lm_advanced_main` built from the LiteRT-LM v0.18.1 tag, with `--backend=gpu` or `--backend=cpu`.

[offline_planner](https://github.com/google-ai-edge/litert-samples/tree/main/samples/litert_lm/offline_planner) in google-ai-edge/litert-samples is an Android app that turns one typed sentence into actions on the app's own calendar and outbox with `LFM3-1.7B_mixed_int4_ekv2048.litertlm` on the GPU (LiteRT-LM's Kotlin API 0.18.1): the app parses the model's XML tool calls and shows each action, with what it would change, before you tap Run.

## Performance

**Galaxy S26** (SM-S942Q, Android 16), `litert_lm_advanced_main` built from the LiteRT-LM v0.18.1 tag, `--benchmark --benchmark_prefill_tokens=1024 --benchmark_decode_tokens=256 --max_num_tokens=2048`; each row is the mean of two runs in one session (2026-10-29).

| File | Backend | Prefill 1,024 (tok/s) | Decode 256 (tok/s) | Time to first token (s) | Peak memory (MB) |
|---|---|---:|---:|---:|---:|
| mixed int4, 2,048 cache | GPU | 1,308.5 | **32.42** | 0.81 | 1,002–1,012 |
| int8, 2,048 cache | GPU | 1,314.8 | 28.86 | 0.81 | 1,012–1,040 |
| mixed int4, 4,096 cache | GPU | 1,160.3 | 29.93 | 0.92 | 896–912 |
| int8, 4,096 cache | GPU | 1,177.5 | 26.63 | 0.91 | 1,036–1,056 |
| mixed int4, 2,048 cache | CPU, 4 threads | 316.1 | **35.53** | 3.33 | 2,211–2,212 |
| mixed int4, 4,096 cache | CPU, 4 threads | 275.1 | 28.16 | 3.81 | 2,640–2,678 |

**Pixel 8a** (Tensor G3, Mali-G715, Android 16), the same binary and flags (2026-10-29):

| File | Backend | Prefill 1,024 (tok/s) | Decode 256 (tok/s) | Time to first token (s) | Peak memory (MB) |
|---|---|---:|---:|---:|---:|
| mixed int4, 4,096 cache | GPU | 266.0 | **15.13** | 3.92 | 1,173–1,181 |
| int8, 4,096 cache | GPU | 264.7 | 13.48 | 3.94 | 1,186–1,201 |
| mixed int4, 2,048 cache | GPU | 283.7 | 12.77 | 3.69 | 904–909 |
| int8, 2,048 cache | GPU | 281.3 | 11.71 | 3.73 | 923–945 |
| mixed int4, 2,048 cache | CPU, 4 threads | 111.8 | **14.73** | 9.23 | 1,966–1,971 |
| mixed int4, 4,096 cache | CPU, 4 threads | 91.2 | 12.38 | 11.32 | 2,265–2,268 |

- Peak memory is the runtime's own "Peak system ram usage" line (the process's VmHWM). The value is in MiB. Every GPU run ran entirely on the GPU (one partition per signature, all 7 signatures).
- The phones held their CPU clock below the maximum during the CPU runs (the CPU clock read about once a second during the run): in 9 of 12 and 9 of 11 samples (Galaxy S26, mixed int4 2,048), 14 of 25 and 18 of 24 (Pixel 8a, mixed int4 2,048). The CPU rows are sustained numbers, not a cool phone's first seconds.
- The int8 files were not timed on the phone CPUs (on a CPU, use a mixed int4 file). In the same Galaxy S26 session as the mixed int4 2,048 CPU row, a copy of the int8 2,048 file that differs only in its sampler setting (greedy) decoded 31.06 tokens/s.
- The Pixel 8a GPU decodes the 4,096-token files faster: in one session that alternated greedy copies of the four files (A B C D D C B A), the 4,096-token files decoded 1.18× (mixed int4) and 1.19× (int8) as fast as the 2,048-token files, while prefill ran at 0.94×.
- An NPU file is not part of this release.

## Accuracy

GSM8K, the first 100 test questions (thinking on: the first 50), 0-shot with a final `#### <number>` line asked for, greedy, one fresh process per question, LiteRT-LM 0.18.1 on a Mac M4 Max. The reference is the source model in transformers, bfloat16. These runs used earlier builds of the same files that differ only in the sampler setting (the model graph and tokenizer are byte-identical), and the sampler was set to greedy for the runs.

| | Thinking off, max 1,024 tokens | Thinking on, max 3,584 tokens, 4,096-token files |
|---|---|---|
| Source model, bfloat16 | 85 / 100 | 48 / 50 |
| mixed int4, Mac GPU | 87, 87, 87 (three runs) | 48 |
| mixed int4, Mac CPU | 87, 90, 90 | not run |
| int8, Mac GPU | 93, 92, 93 | 47, 47, 47 |
| int8, Mac CPU | 86 (one run) | not run |

- Eight short check questions (two sums, the capital of Japan, an opposite, the days in a week, "thank you" in French, 0.9 against 0.11, a rhyme), thinking off, each file's own sampler: every file 8 of 8 on the Mac GPU, the Galaxy S26 GPU and the Pixel 8a GPU; on the CPUs (Mac, Galaxy S26, Pixel 8a) the mixed int4 files 8 of 8 and the int8 files 7 of 8 (they miss the rhyme). The source model in bfloat16: 8 of 8.
- Top-token agreement with the source model in float32, on 2,682 positions of 17 test texts (the 8 questions with thinking off and on, and a 1,584-token passage of the license with a question): int8 95.7 % on a Mac GPU and 88.7 % on the Mac CPU, mixed int4 (2,048) 79.9 % and 77.9 %. The source model in bfloat16 scores 98.2 % on the same positions.
- With thinking off, the mixed int4 files are not always clean. On every device we ran, one of the 8 check answers came back as `17 + 25 = 42.</think>42` (an end-of-thought tag with no thought), and a second answer opened a full `<think>` block before answering: the French question on the Mac, Galaxy S26 and Pixel 8a GPUs, the 0.9-against-0.11 question on the two phone CPUs (the Mac CPU had only the first). The int8 files did neither.
- A tool round trip with the int8 4,096-token weights (Mac CPU, thinking off and on, a tool whose value the model cannot guess): the model wrote the XML call, and after the app sent the value back, the answer held it. A three-turn conversation with the same weights recalled a turn-1 code word in turn 3 in 6 of 6 processes. The tool call of the Python example above ran on the mixed int4 2,048 file on a Mac GPU.

## Limitations

- Create one Engine per conversation: on LiteRT-LM, a conversation started on an Engine that already served another one continues from that conversation's running state ([LiteRT-LM issue #3165](https://github.com/google-ai-edge/LiteRT-LM/issues/3165)). This model keeps a running short-convolution state.
- Tool calls come back as text in the model's XML format. LiteRT-LM 0.18.1 parses no tool call from these files (they declare the generic model type), so the app parses them (Usage).
- The chat template drops the thought of every earlier assistant turn. The source template keeps the thought of the turns after the latest user message, and the source card asks to keep the reasoning of the current tool-use turn; here it is not kept.
- Thinking off is not always clean with the mixed int4 files (Accuracy); the source card itself calls `enable_thinking=False` inconsistent for reasoning-heavy tasks.
- Context is 2,048 or 4,096 tokens, prompt and thought included (the source model: 256K). On the 50 GSM8K questions with thinking on, one reply of the mixed int4 4,096 file was still thinking at the 3,584-token cap.
- The files carry no repetition penalty (the source card's General setting uses 1.1). In Python, `send_message` takes `repetition_penalty_config=litert_lm.RepetitionPenaltyConfig(repetition_penalty=1.1)`.
- The int8 files on a CPU (above). An NPU file is not part of this release.

## Conversion notes

- Source: [LiquidAI/LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B) at revision `40ebe0aa`.
- Decoder: exported with litert-torch at commit f61d0b7 (torch 2.14.0, transformers 5.20.2) through a small LFM3 extension: the source repository's own modeling code with litert-torch's LFM2 short-convolution path and a guard that keeps padded prefill slots out of the convolution state. On 17 test texts (2,683 positions) the extension's PyTorch module matched the source model in transformers, float32, within 1.91e-3 in every logit, with the same top token at all 2,682 positions that are not near ties, prefilled at once, token by token and in 128-token pieces. The graph has no GPU composites: LiteRT-LM 0.18.1's GPU executor keeps the convolution state between steps only for a graph without them.
- Quantization: ai-edge-quantizer-nightly 0.10.0.dev20261023 on the float32 graph. int8 = every weight int8 per channel (`dynamic_wi8_afp32`). Mixed int4 = int4 blockwise-32 on the MLP gate and up projections (with a floor of 2^-14 on the block scales, which no scale reached), int8 per channel on every other weight. Why this mix: int4 on more of the weights (every layer's projections, or all but the attention or the short-convolution ones; blocks of 16, 32 or 64) matched the source model's top token at 67.11–74.83 % of the test positions on a Mac GPU, and int4 on all three MLP projections missed one of the 8 check questions at blocks of 32. Blocks of 16 on the MLP projections matched more often (83.04 % with the gate and up projections) but decoded within 1 % of it on the Galaxy S26 GPU (30.76 and 32.48 against 32.25 tokens/s in one session), and XNNPACK, LiteRT-LM's CPU path, takes no int4 block under 32.
- Template: the source chat template with three edits. Earlier assistant turns render without their thought wherever they sit: LiteRT-LM keeps a conversation's earlier prompt as a prefix of the next one, so a turn must render the same after a later user message. The `{%- generation -%}` / `{%- endgeneration -%}` tags are removed. `.get("…")` lookups are written as `["…"]`. LiteRT-LM 0.18.1's own rendering equals transformers' rendering of the source template for one turn, three turns and a tool round trip, thinking off and on, with two differences: the start token, which the runtime adds itself, and the thought of the assistant turn after the last user message in the tool round trip. The source template as it is does not render on LiteRT-LM 0.18.1.
- Reproduce: [`lfm3_work/convert_lfm3_17b.sh`](https://github.com/john-rocky/hf-to-litertlm/blob/main/lfm3_work/convert_lfm3_17b.sh) in github.com/john-rocky/hf-to-litertlm converts the source to these four files. Run as published on 2026-10-29, it rebuilt all four with every section byte for byte; the file sha256 differs from run to run because the packer writes a new id and creation time into the file header.
- A conversion recipe in google-ai-edge/litert-samples, [`models/lfm/lfm3_1_7b`](https://github.com/google-ai-edge/litert-samples/tree/main/models/lfm/lfm3_1_7b), builds the four files with the stock litert-torch exporter and one out-of-tree `ModelExtension` file, and compares each with the file here section by section (the weights are byte-equal; the tensor names differ).

## License

LFM Open License v1.0, the same license as the source model ([LICENSE](https://huggingface.co/litert-community/LFM3-1.7B-LiteRT/blob/main/LICENSE)). The files here are converted from LiquidAI/LFM3-1.7B: the weights are quantized to int8 and int4 as described above, and the chat template is changed as described under Conversion notes. Section 5 of the license does not license commercial use by an organization with annual revenue of 10 million US dollars or more (a qualified non-profit's non-commercial or research use is exempt).
