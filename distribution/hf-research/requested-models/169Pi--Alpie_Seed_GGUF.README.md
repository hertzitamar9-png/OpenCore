---
license: apache-2.0
base_model: 169Pi/Alpie_seed
library_name: gguf
pipeline_tag: image-text-to-text
tags:
  - alpie
  - 169pi
  - gguf
  - quantized
  - local-inference
  - vision-language
  - multimodal
  - reasoning
  - coding
  - llama.cpp
  - 9b
  - apache-2.0
---

<div align="center">

# Alpie Seed 9B GGUF

**Run Alpie Seed locally.**

<a href="https://huggingface.co/169Pi/Alpie_seed"><img src="https://img.shields.io/badge/Primary_Model-Alpie_Seed_9B-FFD21E?style=flat-square&labelColor=1a1a2e" alt="Primary Model"></a>
<a href="https://ollama.com/169pi/"><img src="https://img.shields.io/badge/Ollama-169pi%2Falpie-00C2A8?style=flat-square&labelColor=1a1a2e" alt="Ollama"></a>
<a href="https://www.kaggle.com/models/alpie/alpie-seed-9b"><img src="https://img.shields.io/badge/Kaggle-alpie--seed--9b-20BEFF?style=flat-square&logo=kaggle&logoColor=white" alt="Kaggle"></a>
<img src="https://img.shields.io/badge/Runtime-llama.cpp-0EA5E9?style=flat-square" alt="Runtime">

<a href="https://github.com/169Pi"><img src="https://img.shields.io/badge/GitHub-169Pi-181717?style=flat-square&logo=github" alt="GitHub"></a>
<a href="https://www.linkedin.com/company/169pi"><img src="https://img.shields.io/badge/LinkedIn-169pi-0A66C2?style=flat-square&logo=linkedin&logoColor=white" alt="LinkedIn"></a>
<a href="https://www.instagram.com/169pi.ai/"><img src="https://img.shields.io/badge/Instagram-169pi.ai-E4405F?style=flat-square&logo=instagram&logoColor=white" alt="Instagram"></a>
<a href="https://www.youtube.com/@169pi"><img src="https://img.shields.io/badge/YouTube-169pi-FF0000?style=flat-square&logo=youtube&logoColor=white" alt="YouTube"></a>
<a href="https://x.com/169Pi_ai"><img src="https://img.shields.io/badge/X-169Pi_ai-000000?style=flat-square&logo=x&logoColor=white" alt="X"></a>

<video controls playsinline preload="metadata" width="354" height="199" style="width:354px; height:199px; max-width:100%; object-fit:contain; background:#000;" src="https://huggingface.co/169Pi/Alpie_Seed_GGUF/resolve/main/alpie_offline_mobile_demo.mp4"></video>

*Alpie Seed running fully offline on a phone, tested in [PocketPal](https://github.com/a-ghorbani/pocketpal-ai).*

[alpie_offline_mobile_demo.mp4](https://huggingface.co/169Pi/Alpie_Seed_GGUF/blob/main/alpie_offline_mobile_demo.mp4)

</div>

---

## About Alpie Seed GGUF

This repository provides GGUF quantized versions of **Alpie Seed 9B**, the open-weight vision-language model developed by **169Pi AI**.

Designed for local deployment, these releases make Alpie Seed accessible across desktops, workstations, and supported mobile hardware. Multiple quantization levels allow developers and researchers to choose the right balance between model size, memory requirements, inference performance, and output quality.

## Highlights

- **9B Parameters:** Based on [Alpie Seed 9B](https://huggingface.co/169Pi/Alpie_seed).
- **Multiple Quantizations:** 2-bit, 3-bit, and 4-bit options.
- **Lower Perplexity than Qwen:** In our perplexity comparison, Alpie Seed scores lower (better) than Qwen at both BF16 and Q4. See [Perplexity: Alpie Seed vs Qwen](#perplexity-alpie-seed-vs-qwen).
- **Local Inference:** Run the model on compatible hardware.
- **Vision-Language:** Image understanding and visual reasoning.
- **Reasoning and Coding:** Mathematical problem-solving, code generation, and debugging.
- **Long Context:** Native 262K context window in the base model.
- **Open License:** Apache License 2.0.

## Available On

Alpie Seed is also available on the following platforms:

| Platform | Link |
|---|---|
| Hugging Face (GGUF) | [169Pi/Alpie_Seed_GGUF](https://huggingface.co/169Pi/Alpie_Seed_GGUF) |
| Ollama | [ollama.com/169pi/alpie-seed](https://ollama.com/169pi/alpie-seed) |
| Kaggle | [kaggle.com/models/alpie/alpie-seed-9b](https://www.kaggle.com/models/alpie/alpie-seed-9b) |

### Ollama

Run Alpie Seed directly from the Ollama library:

```bash
ollama run 169pi/alpie-seed
```

Available tags:

| Name | Size | Context | Input |
|---|---:|---:|---|
| `alpie-seed:latest` | 5.6 GB | 256K | Text |
| `alpie-seed:q2` | 3.8 GB | 256K | Text |
| `alpie-seed:q3` | 4.6 GB | 256K | Text |
| `alpie-seed:vision` | 6.6 GB | 256K | Text, Image |

To run a specific tag:

```bash
ollama run 169pi/alpie-seed:q2
ollama run 169pi/alpie-seed:vision
```

### Kaggle

Alpie Seed 9B is available on Kaggle Models: https://www.kaggle.com/models/alpie/alpie-seed-9b

## Quantization Formats

Different quantization levels provide different trade-offs between model size, memory requirements, inference performance, and output quality.

| Quantization | Description | Recommended Use |
|---|---|---|
| Q2 | Lowest memory footprint | Memory-constrained devices |
| Q3 | Intermediate memory and quality trade-off | Moderate-memory devices |
| Q4 | Higher-fidelity quantized option | Desktop and workstation inference |

Actual file sizes and memory requirements depend on the specific GGUF variant, context length, and runtime configuration.

Refer to the repository file list to select the appropriate model.

## Capabilities

Alpie Seed inherits the capabilities of its base model, including:

- Multi-step reasoning and problem-solving
- Mathematical reasoning
- Code generation and debugging
- Technical explanations and assistance
- Image understanding and visual reasoning
- OCR and document understanding
- Chart and visual information interpretation
- Long-context processing up to 262K tokens

The 262K context window is the native context capability of the base model. Actual usable context depends on runtime support, configuration, and available memory.

Multimodal functionality requires a compatible runtime and the corresponding vision components.

---

## Quickstart

### 1. Download the Model

Install the Hugging Face CLI:

```bash
pip install -U huggingface_hub
```

Download the repository:

```bash
hf download 169Pi/Alpie_Seed_GGUF --local-dir ./Alpie_Seed_GGUF
```

### 2. Select Your Quantization

Navigate to the downloaded directory:

```bash
cd Alpie_Seed_GGUF
ls -lh
```

Select the Q2, Q3, or Q4 GGUF file according to your available memory.

### 3. Run with llama.cpp

Install llama.cpp using its official installation instructions:

https://github.com/ggml-org/llama.cpp

For basic text generation:

```bash
llama-cli \
  -m ./your-selected-model.gguf \
  -p "Explain the difference between supervised fine-tuning and reinforcement learning." \
  -n 256
```

Replace `your-selected-model.gguf` with the actual model filename.

For interactive conversation:

```bash
llama-cli \
  -m ./your-selected-model.gguf \
  -cnv
```

### GPU Acceleration

For NVIDIA GPU inference, enable GPU layer offloading:

```bash
llama-cli \
  -m ./your-selected-model.gguf \
  -ngl 99 \
  -cnv
```

Adjust the number of GPU layers according to available VRAM.

For CPU-only inference:

```bash
llama-cli \
  -m ./your-selected-model.gguf \
  -ngl 0 \
  -cnv
```

### 4. Multimodal Inference (Image Understanding)

Alpie Seed supports vision inference using a compatible multimodal llama.cpp build and its corresponding vision projector.

Download the compatible vision projector from the repository if provided.

Example:

```bash
llama-cli \
  -m ./your-selected-model.gguf \
  --mmproj ./alpie-mmproj-f16.gguf \
  --image ./example.jpg \
  -p "Describe the contents of this image."
```

The language model and vision projector must be compatible with each other.

Refer to the [llama.cpp multimodal documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md) for additional configuration.

### 5. Run with Ollama

Install Ollama:

https://ollama.com/download

To run the hosted model directly from the Ollama library, see [Available On](#ollama) above:

```bash
ollama run 169pi/alpie-seed
```

Alternatively, to import a local GGUF file, create a file named `Modelfile` in your model directory:

```dockerfile
FROM ./your-selected-model.gguf

PARAMETER temperature 0.7
PARAMETER num_ctx 8192
```

Import the model:

```bash
ollama create alpie-seed -f Modelfile
```

Run it:

```bash
ollama run alpie-seed
```

Verify the imported model:

```bash
ollama show alpie-seed
```

For additional information, see the [Ollama GGUF import documentation](https://docs.ollama.com/import).

---

## Perplexity: Alpie Seed vs Qwen

### What is perplexity (PPL)?

Perplexity measures how well a language model predicts text it is shown. For every word (token) in a passage, the model assigns a probability to what comes next. If it gave a high probability to the word that actually came next, it was not "surprised." Perplexity averages that surprise across the whole passage.

A handy way to read it: **perplexity is roughly the number of equally likely next words the model is torn between at each step.** A PPL of 9 means the model is, on average, about as uncertain as if it were picking between 9 equally likely options.

### Why is lower better?

- **Lower PPL = fewer choices = more confident, more accurate predictions** of real text.
- **Higher PPL = more choices = the model is more confused** by the text.

The graph below shows the idea on a toy example. Both models see "The capital of France is ___". The model on the left puts most of its probability on the right answer (low perplexity). The model on the right spreads its probability across many words (high perplexity).

```text
Prompt: "The capital of France is ___"   (illustrative example)

LOW perplexity: model is confident
  Paris   ████████████████████████████████████ 0.86
  London  █                                    0.03
  Rome    █                                    0.03
  Berlin  █                                    0.02
  Madrid  █                                    0.02
  Tokyo   █                                    0.02
  Cairo   █                                    0.01
  Delhi   █                                    0.01

HIGH perplexity: model is unsure
  Paris   ████████                             0.20
  London  ███████                              0.16
  Rome    ██████                               0.14
  Berlin  █████                                0.13
  Madrid  █████                                0.12
  Tokyo   ████                                 0.10
  Cairo   ███                                  0.08
  Delhi   ███                                  0.07
```

### Results

We compared mean perplexity for Alpie Seed and Qwen at two precisions: **BF16** (full-precision reference) and **Q4** (4-bit quantized).

```text
Mean perplexity (lower is better)   [bars start at 8.75, not 0]

BF16 (full precision)
  Qwen       ████████████████████████████████████████ 9.0251
  Alpie Seed █████████████████████████████████████    9.0065   <- 0.21% lower (better)

Q4 (4-bit quantized)
  Qwen       ███████████████████████████████████████  9.0215
  Alpie Seed █████████████████████                    8.8921   <- 1.43% lower (better)
```

| Precision | Qwen | Alpie Seed | Alpie Seed vs Qwen |
|---|---:|---:|---:|
| BF16 | 9.0251 | **9.0065** | 0.21% lower (better) |
| Q4 | 9.0215 | **8.8921** | 1.43% lower (better) |

**Takeaway:** Alpie Seed reaches a lower perplexity than Qwen at BF16 and at Q4, so it predicts the evaluation text more confidently at both precisions. The gap is small at BF16 and larger at Q4.

### How to read this fairly

- **The bars in the chart start at 8.75, not 0**, so small differences are visible. The differences are real but modest, as the percentages in the table show.
- **Compare Alpie against Qwen at the same precision.** The Alpie Q4 score (8.8921) is lower than its own BF16 reference (9.0065). Quantization normally keeps perplexity the same or raises it slightly, so this likely reflects that the BF16 reference checkpoint may not match the checkpoint the Q4 file was built from. It should not be read as "Q4 is better than BF16."
- **Perplexity is not a task benchmark.** It measures next-token prediction on a specific text set. It does not by itself prove better reasoning, coding, or instruction following.
- **Perplexity values are only comparable when the text and settings are the same.** The numbers in this section are not comparable to the separate NF4 retention numbers further below, which were measured on a different corpus.

---

## Model Evaluation Benchmarks

The following results summarize the reported Alpie Seed evaluation measurements. Results are separated by precision and evaluation configuration to avoid mixing model evaluation with standalone GGUF serving performance.

### Evaluation Metrics Summary

| Metric | What is reported | Why it matters | Alpie Seed result |
|---|---|---|---|
| Model weight size | Actual on-disk size in GB | Establishes the storage requirement. | 17.555 GB model snapshot size. This is a snapshot-size measurement, not a verified weights-only size. |
| Peak GPU memory | Memory consumption during inference | Shows whether the model fits on target hardware. | BF16 benchmark: 26.252 GB peak allocated GPU memory. NF4 retention run: 25.379 GB (both-model evaluation; not a standalone NF4 serving measurement). |
| Time to first token | TTFT in milliseconds | Measures initial response latency. | 36.66 ms, measured from the first streamer output; not a pure model-kernel TTFT. |
| Generation speed | Output tokens per second | Measures interactive generation performance. | 41.37 output tokens/second (BF16), end-to-end generation measurement. |
| Prompt processing speed | Input tokens processed per second | Useful for long prompts and document workloads. | 9,796.22 input tokens/second (BF16). |
| Long-context memory | Memory and latency at different context lengths | Establishes whether long-context support is practical. | 1K: 17.865 GB, 0.464 s; 4K: 18.676 GB, 0.715 s; 8K: 19.758 GB, 1.123 s; 16K: 21.922 GB, 1.904 s; 32K: 26.252 GB, 3.738 s. 32K passed; 262K was not tested. |
| Quantization retention | Accuracy before and after quantization | Shows how much capability is preserved. | Text-corpus proxy evaluation (295,893 tokens): BF16 PPL 12.7178; NF4 PPL 13.2436; PPL increase 4.13%; mean KL divergence 0.062325; top-1 agreement 88.40%. Model footprint: BF16 17.527 GB, NF4 7.232 GB (58.73% reduction). |

### Inference Performance

<caption>Reported BF16 benchmark results</caption>

| Metric | Alpie Seed Result |
|---|---:|
| Time to First Token (TTFT) | 36.66 ms |
| Prompt Processing Speed | 9,796.22 tokens/sec |
| Generation Speed | 41.37 tokens/sec |
| Peak GPU Memory | 26.252 GB |
| Model Snapshot Size | 17.555 GB |

**Measurement notes:**

- Generation speed is an end-to-end measurement.
- TTFT is measured from the first streamer output and is not a pure model-kernel TTFT.
- The 17.555 GB figure represents the reported model snapshot size, not a verified weights-only file size.

### Quantization Retention

<caption>BF16 versus NF4 evaluation on a 295,893-token text corpus</caption>

| Metric | BF16 | NF4 |
|---|---:|---:|
| Perplexity (PPL) | 12.7178 | 13.2436 |
| Model Footprint | 17.527 GB | 7.232 GB |

Additional quantization measurements:

| Metric | Result |
|---|---:|
| Perplexity Increase | 4.13% |
| Mean KL Divergence | 0.062325 |
| Top-1 Agreement | 88.40% |
| Model Footprint Reduction | 58.73% |

These results indicate the measured retention characteristics of the NF4 configuration relative to BF16 on the specified evaluation corpus. Here, a perplexity increase of 4.13% means the 4-bit model is only slightly less confident than the full-precision model while using about 59% less memory.

### GPU Memory and Long-Context Evaluation

<caption>Reported BF16 memory and latency measurements</caption>

| Context Length | Peak GPU Memory | Latency |
|---|---:|---:|
| 1K | 17.865 GB | 0.464 s |
| 4K | 18.676 GB | 0.715 s |
| 8K | 19.758 GB | 1.123 s |
| 16K | 21.922 GB | 1.904 s |
| 32K | 26.252 GB | 3.738 s |

The 32K evaluation completed successfully.

**Context window:** Alpie Seed has a native 262K-token context window. The measurements above cover up to 32K tokens; the full 262K context was not tested in this particular memory evaluation.

### Benchmark Interpretation

- BF16 measurements represent the reported inference benchmark configuration.
- NF4 peak GPU memory of 25.379 GB comes from a combined two-model evaluation and must not be interpreted as standalone NF4 serving memory.
- Quantization retention measurements are separate from the Q2, Q3, and Q4 GGUF releases.
- The Alpie Seed vs Qwen perplexity comparison is a separate run from the NF4 retention measurements and uses different text, so the two sets of perplexity values should not be compared with each other.
- The measurements should not be interpreted as universal performance guarantees for every device or runtime.

For the complete model evaluation and additional benchmark information, visit the [primary Alpie Seed model card](https://huggingface.co/169Pi/Alpie_seed).

---

## Local Deployment and Intended Use

Alpie Seed GGUF is designed for:

- Local experimentation and research.
- Offline and private on-device workflows.
- Coding and reasoning assistants.
- Multimodal application development.
- Edge AI and resource-constrained deployment experiments.
- Evaluation of quantization and inference trade-offs.

### Limitations

- Quantization can affect model quality and output consistency.
- Performance varies significantly across hardware and runtime configurations.
- Long-context capability depends on runtime support and available memory.
- Vision functionality requires compatible multimodal runtime support and the corresponding model assets.
- Mobile performance depends on memory availability, inference backend, and thermal constraints.
- Lower perplexity indicates better next-token prediction on the evaluation text, not guaranteed better performance on every task.
- Outputs should be independently validated, particularly in high-impact applications.

## License

Released under the **Apache License 2.0**, consistent with the primary Alpie Seed model.

## Citation

If you use Alpie Seed in your research or projects, please cite:

```bibtex
@misc{alpie_seed_9b_2026,
  title        = {Alpie Seed 9B},
  author       = {{169Pi AI}},
  year         = {2026},
  howpublished = {\url{https://huggingface.co/169Pi/Alpie_seed}}
}
```