---
library_name: transformers
pipeline_tag: image-text-to-text
license: apache-2.0
language:
  - en
  - zh
tags:
  - vla
  - cua
  - computer-use
  - qwen3-vl
base_model: Qwen/Qwen3-VL-4B
---

# Mano-CUA-2.0-4B

**Mano-CUA** is the Computer Use Agent model under the [Mano](https://github.com/Mininglamp-AI/Mano-P) open-source model series. It is a GUI-VLA (Visual Language Agent) model designed specifically for edge devices, capable of autonomously completing complex desktop GUI operations through visual understanding.

This is the **fp16 full-precision** version. For the MLX 8-bit quantized version optimized for Apple Silicon, see [Mano-CUA-2.0-4B-MLX-8bit](https://huggingface.co/Mininglamp-2718/Mano-CUA-2.0-4B-MLX-8bit).

## Main Capabilities

- **Complex GUI Automation**: Autonomously complete complex interface operations containing hundreds of interactive elements
- **Cross-System Data Integration**: Extract and integrate multi-source data through pure visual interaction without API interfaces
- **Long-Task Planning Execution**: Support enterprise-level business process automation of dozens to hundreds of steps
- **Intelligent Report Generation**: Automatically generate structured documents such as data analysis reports and work summaries


## Technical Background

Mano-CUA builds upon the complete technical framework of the Mano project (see [Mano Technical Report](https://arxiv.org/abs/2509.17336)), employing the Mano-Action bidirectional self-reinforcement learning method, three-stage progressive training (SFT → Offline Reinforcement Learning → Online Reinforcement Learning), "think-act-verify" loop reasoning mechanism, and a closed-loop data circulation system to achieve high-precision GUI understanding and operation capabilities. The edge version is optimized through mixed-precision quantization, visual token pruning, and edge inference adaptation, enabling large-scale parameter models to run efficiently on edge devices like Mac mini/MacBook/computing sticks.


## Quick Start

### Requirements

- macOS with Apple Silicon (M1+)
- Python >= 3.12

### Installation

```bash
pip install transformers torch torchvision qwen-vl-utils
```

### Single-Step Demo

```python
from transformers import Qwen3VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info
from PIL import Image

# 1. Load model
model = Qwen3VLForConditionalGeneration.from_pretrained(
    "Mininglamp-2718/Mano-CUA-2.0-4B",
    torch_dtype="auto",
    device_map="auto",
)
processor = AutoProcessor.from_pretrained("Mininglamp-2718/Mano-CUA-2.0-4B")

# 2. Load a screenshot
img = Image.open("screenshot.png")
ratio = 1280 / img.width
img = img.resize((1280, int(img.height * ratio)), Image.LANCZOS)

# 3. Build prompt
task = "Click the search bar and type hello"

prompt_text = f"""You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
<action>action</action>

## Action Space
open_app(app_name='') # Open an application by name.
open_url(url='') # Open a URL in the browser.
click(start_box='<|box_start|>(x1,y1)<|box_end|>')
type(content='') # type the content.
hotkey(key='') # Trigger a keyboard shortcut.
scroll(start_box='<|box_start|>(x1,y1)<|box_end|>', direction='down or up or right or left', amount='scroll_amount')
drag(start_box='<|box_start|>(x1,y1)<|box_end|>', end_box='<|box_start|>(x3,y3)<|box_end|>')
wait(duration='') # Sleep for specified duration (in seconds).
finish() # The task is completed.
stop(reason='') # If the item can not found in the image, give the reason

## User Instruction
{task}"""

messages = [
    {{"role": "system", "content": "You are a helpful assistant."}},
    {{"role": "user", "content": [
        {{"type": "image", "image": img}},
        {{"type": "text", "text": prompt_text}},
    ]}},
]

# 4. Run inference
text_input = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
image_inputs, video_inputs = process_vision_info(messages)
inputs = processor(
    text=[text_input], images=image_inputs, videos=video_inputs,
    padding=True, return_tensors="pt",
).to(model.device)

output_ids = model.generate(**inputs, max_new_tokens=512, temperature=0.0, do_sample=False)
output_ids = output_ids[:, inputs.input_ids.shape[1]:]
output = processor.batch_decode(output_ids, skip_special_tokens=True)[0]

print(output)
```

### Output Format

The model outputs structured XML:

```xml
<think>The search bar is at the top of the page...</think>
<action_desp>Click the search bar to focus it</action_desp>
<action>click(start_box='<|box_start|>(500,38)<|box_end|>')</action>
```

Coordinates are normalized to `[0, 1000]` range. To convert to pixel coordinates:

```python
pixel_x = int(x / 1000 * screen_width)
pixel_y = int(y / 1000 * screen_height)
```


## Full Action Space

| Action       | Syntax                                                       | Description                |
| ------------ | ------------------------------------------------------------ | -------------------------- |
| open_app     | `open_app(app_name='')`                                      | Open an application        |
| open_url     | `open_url(url='')`                                           | Open a URL                 |
| click        | `click(start_box='<\|box_start\|>(x,y)<\|box_end\|>')`       | Left click                 |
| doubleclick  | `doubleclick(start_box='<\|box_start\|>(x,y)<\|box_end\|>')` | Double click               |
| triple_click | `triple_click(start_box='<\|box_start\|>(x,y)<\|box_end\|>')` | Triple click (select line) |
| right_single | `right_single(start_box='<\|box_start\|>(x,y)<\|box_end\|>')` | Right click                |
| hover        | `hover(start_box='<\|box_start\|>(x,y)<\|box_end\|>')`       | Mouse hover                |
| type         | `type(content='text')`                                       | Type text                  |
| hotkey       | `hotkey(key='cmd+c')`                                        | Keyboard shortcut          |
| hotkey_click | `hotkey_click(start_box='<\|box_start\|>(x,y)<\|box_end\|>', key='shift')` | Modifier + click           |
| scroll       | `scroll(start_box='<\|box_start\|>(x,y)<\|box_end\|>', direction='down', amount='3')` | Scroll                     |
| drag         | `drag(start_box='<\|box_start\|>(x1,y1)<\|box_end\|>', end_box='<\|box_start\|>(x2,y2)<\|box_end\|>')` | Drag and drop              |
| wait         | `wait(duration='2')`                                         | Wait (seconds)             |
| finish       | `finish()`                                                   | Task completed             |
| stop         | `stop(reason='...')`                                         | Task infeasible            |
| call_user    | `call_user()`                                                | Request human help         |


## Other Versions

| Version | Repo | Description |
|---------|------|-------------|
| fp16 (this) | [Mano-CUA-2.0-4B](https://huggingface.co/Mininglamp-2718/Mano-CUA-2.0-4B) | Full precision, for archival / re-quantization / GPU inference |
| MLX-8bit | [Mano-CUA-2.0-4B-MLX-8bit](https://huggingface.co/Mininglamp-2718/Mano-CUA-2.0-4B-MLX-8bit) | MLX 8-bit quantized, recommended for Apple Silicon local inference |


## Contact

- Website: [https://github.com/Mininglamp-AI/Mano-P](https://github.com/Mininglamp-AI/Mano-P)
- Email: model@mininglamp.com
