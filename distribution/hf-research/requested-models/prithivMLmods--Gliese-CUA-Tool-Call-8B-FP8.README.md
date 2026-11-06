---
license: apache-2.0
tags:
- text-generation-inference
- pytorch
- agent
- tool-calling
- ui-localization
- computer-use-agent
- gui-actioning
- multimodal-agent
- screen-understanding
- task-automation
- desktop-agent
- ui-vqa
- layout
- fp8
- W8A8
base_model:
- prithivMLmods/Gliese-CUA-Tool-Call-8B
language:
- en
pipeline_tag: image-text-to-text
library_name: transformers
---

![1](https://cdn-uploads.huggingface.co/production/uploads/65bb837dbfb878f46c77de4c/OcVRNSs1JhlVtLRcQfrIg.png)

# **Gliese-CUA-Tool-Call-8B-FP8**

> **Gliese-CUA-Tool-Call-8B-FP8** is an FP8-compressed variant built on top of **prithivMLmods/Gliese-CUA-Tool-Call-8B**. This edition applies **BF16 · FP8 (F8_E4M3)** precision formats to significantly reduce memory footprint and improve inference throughput while preserving the structured tool-calling and multimodal agent capabilities of the original 8B architecture. The base **Gliese-CUA-Tool-Call-8B** model is a Computer Use Agent (CUA) multimodal system derived from Qwen2.5-VL-7B-Instruct. It is designed for GUI understanding, UI localization, and action execution across web, desktop, and mobile environments. The model emphasizes visual grounding, intent-driven action generation, and UI-based question answering, enabling reliable interaction with real-world software interfaces. It is optimized for agentic tool calling, producing structured outputs that can be directly executed by downstream systems.

> [!important]
> FP8 (8-bit floating point) weight and activation quantization using hardware acceleration on GPUs – [FP8 W8A8](https://docs.vllm.ai/en/stable/features/quantization/fp8/). Quantization W8A8 FP8-dynamic recipe – [examples](https://github.com/vllm-project/llm-compressor/tree/main/examples/quantization_w8a8_fp8).

## Key Highlights

* **BF16 · FP8 (F8_E4M3) Compression**: Transformer Engine based FP8 quantization reduces VRAM usage and improves inference efficiency while maintaining structured action accuracy.
* **Computer Use Agent Architecture**: Designed for multimodal GUI reasoning and environment interaction.
* **Agentic Tool Calling**: Generates structured, machine-executable tool calls suitable for automation pipelines.
* **Visual Grounding**: Accurately interprets UI elements, layout hierarchies, and on-screen context.
* **Intent-Driven Actioning**: Converts natural language instructions into executable UI actions.
* **Cross-Platform Support**: Applicable across web apps, desktop software, and mobile interfaces.
* **Optimized Deployment**: FP8 compression enables more efficient deployment on compatible GPU architectures with reduced memory overhead.

## Quick Start with Transformers

```python id="gliesecua8bfp8"
from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info
import torch

# Load the FP8 CUA Tool Call model
model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
    "prithivMLmods/Gliese-CUA-Tool-Call-8B-FP8",
    torch_dtype="auto",
    device_map="auto"
)

processor = AutoProcessor.from_pretrained(
    "prithivMLmods/Gliese-CUA-Tool-Call-8B-FP8"
)

messages = [
    {
        "role": "user",
        "content": [
            {
                "type": "image",
                "image": "screenshot.png",
            },
            {"type": "text", "text": "Click the settings icon and open the preferences menu."},
        ],
    }
]

text = processor.apply_chat_template(
    messages, tokenize=False, add_generation_prompt=True
)

image_inputs, video_inputs = process_vision_info(messages)

inputs = processor(
    text=[text],
    images=image_inputs,
    videos=video_inputs,
    padding=True,
    return_tensors="pt",
).to("cuda")

generated_ids = model.generate(**inputs, max_new_tokens=256)

generated_ids_trimmed = [
    out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
]

output_text = processor.batch_decode(
    generated_ids_trimmed,
    skip_special_tokens=True,
    clean_up_tokenization_spaces=False
)

print(output_text)
```

## Intended Use

* **GUI Automation Research**: Studying multimodal grounding in software environments.
* **Agentic Systems Development**: Building structured tool-calling pipelines.
* **UI-Based Question Answering**: Extracting information from complex interface layouts.
* **Web and Desktop Automation**: Executing structured action sequences in live applications.
* **Human-Computer Interaction Research**: Evaluating intent-to-action alignment in multimodal agents.

## Limitations & Risks

> **Important**: This model generates executable structured actions.

* **Execution Risk**: Generated tool calls may trigger real actions if connected to live systems. Proper validation layers are strongly recommended.
* **Context Sensitivity**: Performance depends heavily on screenshot clarity and UI complexity.
* **Hardware Requirements**: FP8 requires compatible GPU hardware support for optimal performance.