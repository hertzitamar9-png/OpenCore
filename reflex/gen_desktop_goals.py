"""OpenCore writes the goals that teach Reflex to pick Windows controls.

usage: gen_desktop_goals.py desktop_uia.json out_goals.jsonl [llama-server url]

For every actionable control it asks the big model (running on llama-server) for
three natural English requests and one Hebrew request that mean "use exactly this
control", plus the label's English meaning. Labels may be in any language.
"""
import json
import re
import sys
import time
import urllib.request

ACTIONABLE = {"Button", "SplitButton", "MenuItem", "Edit", "ComboBox", "CheckBox", "RadioButton",
              "TabItem", "ListItem", "TreeItem", "Hyperlink", "Slider", "Document"}
BIDI = re.compile("[‎‏‪-‮⁦-⁩]")
PROMPT = """A Windows app window titled "{title}" has a {kind} control labelled "{name}"{value}.
Write three different short requests a person might type in English to make an assistant use exactly this control (for example "save my work" for a Save button, "go back" for a Back button). Then write one such request in Hebrew.
Reply with only JSON: {{"meaning": "English meaning of the label", "en": ["...", "...", "..."], "he": "..."}}"""


def clean(text):
    return BIDI.sub("", text or "").strip()


def actionable(window):
    for element in window["elements"]:
        name = clean(element.get("name"))
        if element.get("controlType") in ACTIONABLE and name and element.get("enabled", True):
            yield element, name


def ask(url, prompt):
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": 220, "temperature": 0.7,
            "chat_template_kwargs": {"enable_thinking": False}, "reasoning_format": "none"}
    request = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read())["choices"][0]["message"].get("content") or ""


def main():
    windows = json.load(open(sys.argv[1], encoding="utf-8"))
    out = open(sys.argv[2], "w", encoding="utf-8")
    url = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8891"
    done, failed, started = 0, 0, time.time()
    for w_index, window in enumerate(windows):
        seen = set()
        for element, name in actionable(window):
            key = (element["controlType"], name)
            if key in seen:
                continue
            seen.add(key)
            value = clean(element.get("value"))
            prompt = PROMPT.format(title=clean(window["title"]), kind=element["controlType"], name=name,
                                   value=' with value "%s"' % value if value else "")
            try:
                text = ask(url, prompt)
                data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
                goals = [g for g in data.get("en", []) if isinstance(g, str) and g.strip()][:3]
                if isinstance(data.get("he"), str) and data["he"].strip():
                    goals.append(data["he"].strip())
                if not goals:
                    raise ValueError("no goals")
            except Exception:
                failed += 1
                continue
            out.write(json.dumps({"window": w_index, "elementId": element["elementId"], "name": name,
                                  "controlType": element["controlType"], "meaning": data.get("meaning", ""),
                                  "goals": goals}, ensure_ascii=False) + "\n")
            out.flush()
            done += 1
            if done % 20 == 0:
                print("  %d controls, %d failed, %.0fs" % (done, failed, time.time() - started), flush=True)
    print("done: %d controls with goals, %d failed, %.0fs" % (done, failed, time.time() - started), flush=True)


if __name__ == "__main__":
    main()
