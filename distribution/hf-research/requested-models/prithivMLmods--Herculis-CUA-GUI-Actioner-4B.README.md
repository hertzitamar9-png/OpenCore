---
license: other
language:
- en
base_model:
- Qwen/Qwen2.5-VL-3B-Instruct
pipeline_tag: image-text-to-text
library_name: transformers
tags:
- text-generation-inference
- agent
- pytorch
- computer-use-agent
- gui-actioning
- ui-localization
- multimodal-agent
- screen-understanding
- task-automation
- desktop-agent
- ui-vqa
- layout
---

![1](https://cdn-uploads.huggingface.co/production/uploads/65bb837dbfb878f46c77de4c/cXVEsVO3qQemXOLn763Mj.png)

# **Herculis-CUA-GUI-Actioner-4B**

> **Herculis-CUA-GUI-Actioner-4B** is a Computer Use Agent (CUA) multimodal model designed for GUI understanding, UI localization, and action execution across web, desktop, and mobile environments. It focuses on visual grounding, intent driven actioning, and UI based question answering (VQA), enabling reliable interaction with real world software interfaces. The model is optimized for efficient inference while maintaining strong accuracy on complex UI workflows.

# Key Capabilities

* **GUI Localization and Visual Grounding**
  Precisely identifies UI elements such as buttons, text fields, menus, icons, dialogs, and dynamic components across diverse layouts and resolutions.

* **Action Planning and Execution**
  Translates natural language instructions into structured UI actions such as click, type, scroll, drag, select, and navigate, with step wise reasoning.

* **UI Based Question Answering (VQA)**
  Answers questions grounded in the current screen state, including element states, content verification, and workflow guidance.

* **Cross Platform Computer Use**
  Operates across web applications, desktop software, and mobile interfaces with consistent behavior and robust visual understanding.

* **Multi Step Task Automation**
  Handles long horizon tasks such as form filling, settings configuration, dashboard navigation, and tool driven workflows.

* **Robust Visual Parsing**
  Understands complex UI structures including tables, modals, nested menus, toolbars, charts, and responsive layouts.

* **Context Aware Interaction**
  Maintains task context across screens and state changes for reliable end to end task completion.

---

# Quick Run as a Gradio + Transformers App 🤗

## **Usage [Setup and Run Instructions]**

### 1. Clone the repository

```
!git clone https://github.com/PRITHIVSAKTHIUR/Herculis-CUA-GUI-Actioner-4B-Demo.git
```

### 2. Move into the project directory

```
%cd Herculis-CUA-GUI-Actioner-4B-Demo
```

### 3. Install dependencies

Make sure you have Python 3.10 or higher installed.

```
!pip install -r requirements.txt
```

### 4. Run the application

```
!python app.py
# -> After running the app, click on “Running on public URL” to open the app in a new window.
```

# Example Output Inference

> Localize: Locate the `microsoft/Fara-7B` model.

| Type         | Preview |
|--------------|---------|
| Input Image  | ![Input Image](https://huggingface.co/prithivMLmods/Herculis-CUA-GUI-Actioner-4B/resolve/main/example/example-image.png) |
| Output Image | ![Output Image](https://huggingface.co/prithivMLmods/Herculis-CUA-GUI-Actioner-4B/resolve/main/example/output-image.webp) |

---

# Quick Start with Transformers

```python
from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info

model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
    "prithivMLmods/Herculis-CUA-GUI-Actioner-4B",
    torch_dtype="auto",
    device_map="auto"
)

processor = AutoProcessor.from_pretrained("prithivMLmods/Herculis-CUA-GUI-Actioner-4B")

messages = [
    {
        "role": "user",
        "content": [
            {"type": "image", "image": "<SCREENSHOT_OR_UI_IMAGE>"},
            {"type": "text", "text": "Click the settings icon and enable dark mode."},
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

* Natural language driven computer and GUI control
* UI localization and element grounding
* Automated form filling and workflow execution
* UI based question answering and verification
* Web, desktop, and mobile agent systems
* RPA style automation with visual grounding
* Assistive agents for accessibility and productivity

# Benchmarks and Strengths

* Strong performance on UI localization and action grounding tasks
* High accuracy on UI based VQA and screen understanding
* Robust generalization across unseen applications and layouts
* Efficient compute profile for scalable deployment

# Limitations

* Performance may degrade on heavily animated or highly obfuscated UIs
* Extremely low resolution or blurred screenshots can reduce localization accuracy
* Very long horizon tasks may require external planning or tool support
* Non standard or highly custom rendered interfaces may need adaptation 