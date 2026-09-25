"""Measure visual grounding: does the predicted click land inside the target?

usage: eval_grounding.py BENCH MODEL [--limit N] [--max-pixels P] [--out FILE]

BENCH  screenspot-v2 | screenspot-pro | osworld-g   (under models/reflex/data/bench)
MODEL  goclick:DIR | holo:DIR | server:URL          (server = llama-server with an mmproj)

Every model gets the same image and instruction, and a prediction counts only
when the point falls inside the target box (or polygon, for OSWorld-G).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import random
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path

from PIL import Image

BENCH_ROOT = Path(__file__).resolve().parents[3] / "models" / "reflex" / "data" / "bench"

# H Company's documented localization prompt for Holo3/Holo3.1 (hub.hcompany.ai).
HOLO_SCHEMA = {
    "properties": {
        "x": {"description": "X coordinate as integer in [0, 1000]", "maximum": 1000, "minimum": 0,
              "title": "X", "type": "integer"},
        "y": {"description": "Y coordinate as integer in [0, 1000]", "maximum": 1000, "minimum": 0,
              "title": "Y", "type": "integer"},
    },
    "required": ["x", "y"], "title": "VisualLocalizerOutput", "type": "object",
}


def holo_prompt(target: str) -> str:
    return ("Localize an element on the GUI image according to the provided target "
            "and output a click position.\n"
            f" * You must output a valid JSON following the format: {HOLO_SCHEMA}\n"
            f" Your target is:\n{target}")


def parse_holo(text: str) -> tuple[float, float] | None:
    x = re.search(r'"?x"?\s*[:=]\s*(-?\d+(?:\.\d+)?)', text)
    y = re.search(r'"?y"?\s*[:=]\s*(-?\d+(?:\.\d+)?)', text)
    if not x or not y:
        return None
    return float(x[1]) / 1000.0, float(y[1]) / 1000.0


# ---------------------------------------------------------------- benchmarks

def load_screenspot_v2():
    root = BENCH_ROOT / "screenspot-v2"
    samples = []
    for platform in ("desktop", "mobile", "web"):
        for row in json.loads((root / f"screenspot_{platform}_v2.json").read_text(encoding="utf-8")):
            x, y, w, h = row["bbox"]
            samples.append({"image": root / "screenspotv2_image" / row["img_filename"],
                            "instruction": row["instruction"], "box": (x, y, x + w, y + h),
                            "group": f"{platform}/{row['data_type']}"})
    return samples


def load_screenspot_pro():
    root = BENCH_ROOT / "screenspot-pro"
    samples = []
    for path in sorted((root / "annotations").glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            samples.append({"image": root / "images" / row["img_filename"],
                            "instruction": row["instruction"], "box": tuple(row["bbox"]),
                            "group": f"{row['group']}/{row['ui_type']}"})
    return samples


def load_osworld_g():
    import pyarrow.parquet as pq
    table = pq.read_table(BENCH_ROOT / "osworld-g" / "data" / "test-00000-of-00001.parquet")
    samples = []
    for row in table.to_pylist():
        coords = row["box_coordinates"]
        if row["box_type"] == "bbox":
            x, y, w, h = coords
            shape = {"box": (x, y, x + w, y + h)}
        elif len(coords) == 4:  # "polygon" rows in this release are corner boxes (x1, y1, x2, y2)
            shape = {"box": tuple(coords)}
        else:
            shape = {"polygon": list(zip(coords[0::2], coords[1::2]))}
        samples.append({"image_bytes": row["image"]["bytes"], "instruction": row["instruction"],
                        **shape, "group": row["box_type"]})
    return samples


def load_hebrew_web(per_image=20):
    """Held-out Hebrew web pages from collect_web_grounding.mjs, at most 20 targets per page."""
    root = BENCH_ROOT.parent / "grounding-web"
    by_image = defaultdict(list)
    for line in (root / "eval.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        by_image[row["image"]].append(row)
    samples, rng = [], random.Random(0)
    for image, rows in sorted(by_image.items()):
        for row in rng.sample(rows, min(per_image, len(rows))):
            samples.append({"image": root / image, "instruction": row["name"], "box": tuple(row["box"]),
                            "group": f"{row['lang']}/{row['kind']}"})
    return samples


def load_hebrew_desktop():
    """Held-out Windows apps in the user's Hebrew UI from collect_desktop_grounding.py."""
    root = BENCH_ROOT.parent / "grounding-desktop"
    samples = []
    for line in (root / "eval.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        samples.append({"image": root / row["image"], "instruction": row["name"], "box": tuple(row["box"]),
                        "group": f"{row['lang']}/{row['controlType']}"})
    return samples


BENCHES = {"screenspot-v2": load_screenspot_v2, "screenspot-pro": load_screenspot_pro,
           "osworld-g": load_osworld_g, "hebrew-web": load_hebrew_web, "hebrew-desktop": load_hebrew_desktop}


def inside(sample, px, py) -> bool:
    if "box" in sample:
        x1, y1, x2, y2 = sample["box"]
        return x1 <= px <= x2 and y1 <= py <= y2
    polygon, hit = sample["polygon"], False
    for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1]):
        if (ay > py) != (by > py) and px < (bx - ax) * (py - ay) / (by - ay) + ax:
            hit = not hit
    return hit


# -------------------------------------------------------------------- models

class GoClick:
    def __init__(self, path: str):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(path, trust_remote_code=True, local_files_only=True)
        self.model = AutoModelForCausalLM.from_pretrained(path, trust_remote_code=True, local_files_only=True,
                                                          torch_dtype=torch.float16).cuda().eval()
        # Transformers 5 drops two tied embeddings from this Transformers 4 checkpoint.
        shared = self.model.language_model.model.shared.weight
        self.model.language_model.model.encoder.embed_tokens.weight = shared
        self.model.language_model.model.decoder.embed_tokens.weight = shared

    def predict(self, image: Image.Image, instruction: str):
        prompt = f"Where is the {instruction} element? (Output the center coordinates of the target)"
        inputs = self.processor(images=image, text=prompt, return_tensors="pt", do_resize=True)
        inputs = {k: v.cuda().to(self.torch.float16) if v.dtype.is_floating_point else v.cuda()
                  for k, v in inputs.items()}
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, do_sample=False, num_beams=1, max_new_tokens=16, use_cache=False)
        text = self.processor.tokenizer.batch_decode(out, skip_special_tokens=False)[0]
        match = re.search(r"<loc_(\d+)>\s*,\s*<loc_(\d+)>", text)
        return (int(match[1]) / 1000.0, int(match[2]) / 1000.0) if match else None, text


class Holo:
    def __init__(self, path: str, max_pixels: int | None):
        import torch
        from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(path, local_files_only=True)
        if max_pixels:
            self.processor.image_processor.size = {"longest_edge": max_pixels, "shortest_edge": 65536}
        self.model = Qwen3_5ForConditionalGeneration.from_pretrained(
            path, dtype=torch.bfloat16, device_map="cuda", attn_implementation="sdpa", local_files_only=True).eval()

    def predict(self, image: Image.Image, instruction: str):
        messages = [{"role": "user", "content": [{"type": "image", "image": image},
                                                 {"type": "text", "text": holo_prompt(instruction)}]}]
        inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                    return_dict=True, return_tensors="pt").to("cuda")
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, do_sample=False, max_new_tokens=24,
                                      pad_token_id=self.processor.tokenizer.eos_token_id)
        text = self.processor.batch_decode(out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0]
        return parse_holo(text), text


class Server:
    """A llama-server with the model's mmproj: the same path OpenCore uses."""

    def __init__(self, url: str, max_pixels: int | None):
        import requests
        self.session, self.url, self.max_pixels = requests.Session(), url.rstrip("/"), max_pixels

    def predict(self, image: Image.Image, instruction: str):
        if self.max_pixels and image.width * image.height > self.max_pixels:
            scale = (self.max_pixels / (image.width * image.height)) ** 0.5
            image = image.resize((max(32, int(image.width * scale)), max(32, int(image.height * scale))),
                                 Image.Resampling.BICUBIC)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        body = {"messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data}},
                    {"type": "text", "text": holo_prompt(instruction)}]}],
                "temperature": 0, "max_tokens": 24, "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_schema", "json_schema": {"name": "point", "schema": HOLO_SCHEMA}}}
        reply = self.session.post(self.url + "/v1/chat/completions", json=body, timeout=300).json()
        text = reply["choices"][0]["message"]["content"]
        return parse_holo(text), text


def make_model(spec: str, max_pixels: int | None):
    kind, _, target = spec.partition(":")
    if kind == "goclick":
        return GoClick(target)
    if kind == "holo":
        return Holo(target, max_pixels)
    if kind == "server":
        return Server(target, max_pixels)
    raise SystemExit(f"unknown model kind: {kind}")


# ---------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bench", choices=sorted(BENCHES))
    parser.add_argument("model")
    parser.add_argument("--limit", type=int, default=0, help="random subset of this size (seed 0)")
    parser.add_argument("--max-pixels", type=int, default=0, help="downscale larger screenshots (Holo only)")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    samples = BENCHES[args.bench]()
    if args.limit and args.limit < len(samples):
        samples = random.Random(0).sample(samples, args.limit)
    model = make_model(args.model, args.max_pixels or None)

    per_group, latencies, rows, failures = defaultdict(lambda: [0, 0]), [], [], 0
    started = time.time()
    for index, sample in enumerate(samples):
        image = Image.open(sample["image"] if "image" in sample else io.BytesIO(sample["image_bytes"])).convert("RGB")
        begin = time.perf_counter()
        point, raw = model.predict(image, sample["instruction"])
        latencies.append((time.perf_counter() - begin) * 1000)
        hit = False
        if point is None:
            failures += 1
        else:
            hit = inside(sample, point[0] * image.width, point[1] * image.height)
        per_group[sample["group"]][0] += hit
        per_group[sample["group"]][1] += 1
        rows.append({"i": index, "group": sample["group"], "instruction": sample["instruction"], "hit": hit,
                     "point": point, "raw": raw[:120], "ms": round(latencies[-1], 1),
                     "size": [image.width, image.height]})
        if (index + 1) % 50 == 0:
            done = sum(g[0] for g in per_group.values()) / (index + 1)
            print(f"{index + 1}/{len(samples)} acc={done:.3f} median_ms={statistics.median(latencies):.0f}", flush=True)

    total_hits = sum(g[0] for g in per_group.values())
    summary = {"bench": args.bench, "model": args.model, "max_pixels": args.max_pixels or None,
               "n": len(samples), "accuracy": round(total_hits / len(samples), 4), "unparsed": failures,
               "median_ms": round(statistics.median(latencies), 1),
               "p90_ms": round(sorted(latencies)[int(0.9 * (len(latencies) - 1))], 1),
               "minutes": round((time.time() - started) / 60, 1),
               "groups": {k: round(v[0] / v[1], 4) for k, v in sorted(per_group.items())},
               "group_n": {k: v[1] for k, v in sorted(per_group.items())}}
    print(json.dumps(summary, indent=1))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=0), encoding="utf-8")


if __name__ == "__main__":
    main()
