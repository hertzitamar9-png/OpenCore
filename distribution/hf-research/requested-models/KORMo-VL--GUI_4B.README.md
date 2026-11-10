---
library_name: transformers
pipeline_tag: image-text-to-text
language:
- ko
- en
tags:
- gui-agent
- web-agent
- computer-use
---

# GUI_4B

A 4B vision-language web agent fine-tuned from Qwen3.5-4B. Given a task, the current screenshot and the browser tab state, it reasons in a `<think>` block and outputs **exactly one tool call** per step.

## Evaluation

| Model | Online-Mind2Web KO | Online-Mind2Web EN | ScreenSpot-v2 EN | ScreenSpot-v2 KO |
|---|---|---|---|---|
| Qwen3.5-4B (no fine-tuning) | 13.5 | 20.0 | 77.3 | 74.9 |
| UI-TARS-1.5-7B | 20.2 | 22.3 | 79.2 | 77.4 |
| Fara-1.5-4B | 7.7 | 20.7 | **90.3** | **84.7** |
| **GUI_4B (this model)** | **38.8** | **31.7** | 83.4 | 77.8 |

- **Online-Mind2Web:** success rate (%) on live websites, with 312 Korean tasks and 300 English tasks. Each trajectory is judged by an LLM judge using the WebVoyager judge prompt on the last 50 screenshots. Missing or failed rollouts count as failures.
- **ScreenSpot-v2 (English and Korean):** click accuracy (%).
- **Same harness for all models:** every model was run with the same harness, the same judge and the same task set.

## Files

| File | Purpose |
|---|---|
| `system_prompt.md` | System prompt used in training. Use it verbatim. |
| `tools.json` | The 21 tool definitions used in training. Pass them as `tools`. |
| `chat_template.jinja` | Chat template (Qwen3.5 XML tool-call format, thinking on). |

## Serving with vLLM

```bash
vllm serve KORMo-VL/GUI_4B \
  --served-model-name GUI_4B \
  --max-model-len 131072 \
  --limit-mm-per-prompt '{"image": 1}' \
  --trust-remote-code
```

The tool call is parsed from the raw text (see [Parsing the output](#parsing-the-output)), so no `--tool-call-parser` is needed.

## Prompt format

One request per step, in this message layout:

1. `system`: the contents of `system_prompt.md`.
2. `user`: the task and the first observation, with the screenshot.
3. Then, for each step taken so far: an `assistant` message (the model's raw output, including `<think>`) and a `tool` message (the execution result plus the new observation).

Only the **latest** observation carries a screenshot. Earlier observations are text only.

**Observation text** (the screenshot goes after `screenshot:`):

```
<observation>
screen size: 1280 x 1000
Current tab: 0
Available tabs:
- Tab 0 (active): https://careers.walmart.com/us/en/home - Walmart Careers

url: https://careers.walmart.com/us/en/home
screenshot:
[IMAGE]
</observation>
```

**First user message:**

```
<user_request>
Find support services jobs in Bentonville, in the state of Arkansas.
</user_request>
<observation>
...
</observation>
```

**Tool message after each action:**

```
Succeed: `click` executed.
<observation>
...
</observation>
```

### Notes

- **Coordinates:** `x`, `y`, `x2` and `y2` are normalized to **0–1000** relative to the screenshot. Convert them to pixels with `px = x / 1000 * width`.
- **Scrolling:** `scroll_dy` and `scroll_dx` are in **screenshot pixels**, not normalized. Positive values scroll down or right.
- **Viewport:** training used **1280×720** for Korean tasks and **1280×1000** for English tasks.
- **URLs:** keep them percent-encoded, as the browser reports them. Do not decode Korean characters. URLs longer than 300 characters are cut to the first 300 characters plus `…`.
- **Tab titles:** add ` - {title}` (at most 50 characters) after the tab URL for English tasks. For Korean tasks, use the URL only.

## Example (OpenAI-compatible client)

```python
import base64, json
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")
SYSTEM = open("system_prompt.md").read()
TOOLS = json.load(open("tools.json"))

def observation(url, title, png_bytes, w=1280, h=1000):
    img = base64.b64encode(png_bytes).decode()
    text = (f"<observation>\nscreen size: {w} x {h}\nCurrent tab: 0\nAvailable tabs:\n"
            f"- Tab 0 (active): {url} - {title}\n\nurl: {url}\nscreenshot:\n")
    return [{"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}},
            {"type": "text", "text": "\n</observation>"}]

task = "Find support services jobs in Bentonville, in the state of Arkansas."
obs = observation("https://careers.walmart.com/us/en/home", "Walmart Careers",
                  open("screenshot.png", "rb").read())
messages = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": [{"type": "text", "text": f"<user_request>\n{task}\n</user_request>\n"}] + obs},
]

resp = client.chat.completions.create(
    model="GUI_4B", messages=messages, tools=TOOLS, max_tokens=4096,
    temperature=0.6, top_p=0.95,
    extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": True}},
)
raw = resp.choices[0].message.content
print(raw)
```

For the next step, append `{"role": "assistant", "content": raw}`. Then append a `tool` message: `Succeed: \`click\` executed.\n` followed by the new observation. Replace the screenshots in older observations with text only.

## Parsing the output

The model replies with a `<think>` block followed by a single XML tool call:

```
<think>The search bar is visible. I'll click it to start the search.
</think>

<tool_call>
<function=click>
<parameter=x>
428
</parameter>
<parameter=y>
471
</parameter>
</function>
</tool_call>
```

```python
import re, json

def parse_tool_call(raw):
    m = re.search(r"<function=([^>\s]+)>(.*?)</function>", raw, re.S)
    if not m:
        return None
    args = {}
    for k, v in re.findall(r"<parameter=([^>\s]+)>\n?(.*?)\n?</parameter>", m.group(2), re.S):
        try:
            args[k] = json.loads(v)
        except json.JSONDecodeError:
            args[k] = v
    return {"name": m.group(1), "arguments": args}

parse_tool_call(raw)  # {'name': 'click', 'arguments': {'x': 428, 'y': 471}}
```

## Action space (21 tools)

| Tool | Arguments |
|---|---|
| `click`, `double_click`, `right_click`, `hover`, `focus` | `x`, `y` (0–1000) |
| `drag` | `x`, `y`, `x2`, `y2` (0–1000) |
| `type` | `text`: typed into the focused input |
| `hotkey` | `keys`: list, e.g. `["Enter"]`, `["Control", "a"]` |
| `scroll` | `scroll_dy`, optional `scroll_dx` (pixels) |
| `wait` | `seconds` |
| `goto` | `url` |
| `go_back`, `go_forward` | none |
| `clear` | none: clears the focused input |
| `new_tab`, `close_tab` | none |
| `switch_tab` | `index` (0-based) |
| `finished` | optional `answer`: task done |
| `fail` | none: task cannot be completed |
| `unknown` | none: no reliable action |
| `EXIT` | none: explicit final termination |

See `tools.json` for the full schemas.
