---
license: apache-2.0
language:
- en
base_model:
- Qwen/Qwen2.5-VL-7B-Instruct
pipeline_tag: image-text-to-text
library_name: transformers
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
---

![1](https://cdn-uploads.huggingface.co/production/uploads/65bb837dbfb878f46c77de4c/QRwco4M2LW8m98aFwcIn7.png)

# **Gliese-CUA-Tool-Call-8B**

> **Gliese-CUA-Tool-Call-8B** is a Computer Use Agent (CUA) multimodal model based on **Qwen2.5-VL-7B-Instruct**, designed for GUI understanding, UI localization, and action execution across web, desktop, and mobile environments. It focuses on visual grounding, intent driven actioning, and UI based question answering (VQA), enabling reliable interaction with real world software interfaces. The model is optimized for agentic tool calling, producing structured actions that can be directly executed by downstream systems.

# Key Capabilities

* **GUI Localization and Visual Grounding**
  Precisely identifies UI elements such as buttons, text fields, menus, icons, dialogs, and dynamic components across diverse layouts and resolutions.

* **Parsed Action Prediction with Visualization**
  Predicts low level UI actions such as clicks, types, scrolls, and drags with explicit coordinates. Actions can be visualized on the image using crosshairs and labels for transparent grounding and debugging.

* **Structured Tool Calling**
  Outputs actions as structured JSON tool calls wrapped inside `<tool_call>` blocks, enabling precise and deterministic interaction for agentic tool calling pipelines.

* **Action Planning and Execution**
  Translates natural language instructions into step wise UI actions with consistent reasoning across multi step workflows.

* **UI Based Question Answering (VQA)**
  Answers questions grounded in the current screen state, including element states, content verification, and workflow guidance.

* **Cross Platform Computer Use**
  Operates consistently across web applications, desktop software, and mobile interfaces with robust visual understanding.

* **Multi Step Task Automation**
  Handles long horizon tasks such as form filling, settings configuration, dashboard navigation, and tool driven workflows.

* **Context Aware Interaction**
  Maintains task context across screen transitions and state changes for reliable end to end task completion.

# Model Usage Demo 💻

| Feature | Model / Repository | Quick Demo |
|-------|-------------------|------------|
| **Agentic Tool-Calling** | https://huggingface.co/prithivMLmods/Gliese-CUA-Tool-Call-8B/tree/main | https://github.com/PRITHIVSAKTHIUR/Gliese-CUA-Tool-Call-8B-Demo |
| **CUA Localization** | https://huggingface.co/prithivMLmods/Gliese-CUA-Tool-Call-8B/tree/main/Localization-8B | https://github.com/PRITHIVSAKTHIUR/Gliese-CUA-Tool-Call-8B-Localization-Demo |

---

# Installation [Demo]

## Gliese-CUA-Tool-Call-8B-Demo 

1. Clone the repository:
   ```
   git clone https://github.com/PRITHIVSAKTHIUR/Gliese-CUA-Tool-Call-8B-Demo.git
   cd Gliese-CUA-Tool-Call-8B-Demo
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
   
3. Start the application:
   ```
   python app.py
   ```

## Gliese-CUA-Tool-Call-8B-Localization-Demo


1. Clone the repository:
   ```
   git clone https://github.com/PRITHIVSAKTHIUR/Gliese-CUA-Tool-Call-8B-Localization.git
   cd Gliese-CUA-Tool-Call-8B-Localization
   ```

2. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

3. Start the application:
   ```
   python app.py
   ```
   
---

# Quick Start with Transformers

```python
from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info

model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
    "prithivMLmods/Gliese-CUA-Tool-Call-8B",
    torch_dtype="auto",
    device_map="auto"
)

processor = AutoProcessor.from_pretrained("prithivMLmods/Gliese-CUA-Tool-Call-8B")

messages = [
    {
        "role": "user",
        "content": [
            {"type": "image", "image": "<SCREENSHOT_OR_UI_IMAGE>"},
            {"type": "text", "text": "Enable dark mode from the settings menu."},
        ],
    }
]

text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
image_inputs, video_inputs = process_vision_info(messages)

inputs = processor(
    text=[text],
    images=image_inputs,
    videos=video_inputs,
    padding=True,
    return_tensors="pt",
)
inputs = inputs.to("cuda")

generated_ids = model.generate(**inputs, max_new_tokens=1024)
generated_ids_trimmed = [
    out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
]

output_text = processor.batch_decode(
    generated_ids_trimmed,
    skip_special_tokens=True,
    clean_up_tokenization_spaces=False,
)
print(output_text)
```

# Intended Use

* Agentic computer and GUI control via tool calling
* UI localization and element grounding with coordinates
* Structured action generation for RPA systems
* Automated form filling and workflow execution
* UI based question answering and verification
* Web, desktop, and mobile agent frameworks
* Accessibility and productivity assistants

# Limitations

* Performance may degrade on heavily animated or visually obfuscated UIs
* Very low resolution or blurred screenshots can reduce localization accuracy
* Extremely long horizon tasks may require external planners or tool orchestration
* Highly custom or non standard rendered interfaces may need task specific adaptation

# References

* Qwen2.5-VL Technical Report:
  https://huggingface.co/papers/2502.13923

* YaRN: Efficient Context Window Extension  
  https://arxiv.org/pdf/2309.00071

* Qwen2 VL: High Resolution Perception  
  https://arxiv.org/pdf/2409.12191

* Model Soups: Averaging Weights of Multiple Fine-Tuned Models Improves Accuracy Without Increasing Inference Time  
  https://arxiv.org/abs/2203.05482

* Model Stock: All We Need Is Just a Few Fine-Tuned Models  
  https://arxiv.org/abs/2403.19522