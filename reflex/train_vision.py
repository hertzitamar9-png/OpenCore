"""Fine-tune Reflex Vision (Holo3.1-0.8B) on screen grounding with LoRA.

usage: train_vision.py BASE_DIR OUT_DIR --data DIR[:WEIGHT] [--data ...] [--steps N]

Each --data DIR holds train.jsonl and images/ from collect_web_grounding.mjs. Every
example is one screenshot, the documented Holo localization prompt for one control, and
the control's center as {"x":..,"y":..} on the model's 0-1000 grid, so training uses
exactly the format OpenCore sends at run time. Accuracy on the held-out Hebrew set and
on English ScreenSpot-v2 is measured before training and at every checkpoint.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import torch
from PIL import Image

from eval_grounding import holo_prompt, inside, load_hebrew_desktop, load_hebrew_web, load_screenspot_v2, parse_holo

ROLE_WORDS = {"button": "button", "link": "link", "tab": "tab", "menuitem": "menu item", "checkbox": "checkbox",
              "radio": "option", "option": "option", "switch": "switch", "combobox": "drop-down",
              "searchbox": "search box",
              # Windows UI Automation control types from collect_desktop_grounding.py
              "tabitem": "tab", "listitem": "item", "hyperlink": "link", "edit": "text field", "treeitem": "item",
              "radiobutton": "option", "splitbutton": "button", "dataitem": "item", "headeritem": "column header",
              "text": "text", "slider": "slider", "spinner": "spinner"}
TAG_WORDS = {"a": "link", "button": "button", "input": "field", "select": "drop-down", "textarea": "text box",
             "summary": "section"}
TRANSLATIONS: dict[str, str] = {}  # Hebrew label -> English meaning (--translations)


def describe(row: dict, rng: random.Random) -> str:
    """How a caller names a target: the bare label, or the label with its kind."""
    word = ROLE_WORDS.get(row.get("role")) or TAG_WORDS.get(row.get("tag"), "element")
    if row.get("tag") == "input" and row.get("type") in ("submit", "button", "reset", "image"):
        word = "button"
    if row.get("tag") == "input" and row.get("type") in ("checkbox", "radio"):
        word = "checkbox" if row["type"] == "checkbox" else "option"
    if row.get("kind") == "icon" and word == "link":
        word = "icon"
    english = TRANSLATIONS.get(row["name"])
    if english and rng.random() < (0.5 if row.get("kind") == "icon" else 0.25):
        # A caller that reasons in English names a Hebrew control by its meaning.
        return rng.choice([f"the {english} {word}", f"{english} {word}", f"the {word} for {english}"])
    pick = rng.random()
    if pick < 0.5:
        return row["name"]
    if pick < 0.85:
        return f'the "{row["name"]}" {word}'
    return f'{word} labeled "{row["name"]}"'


def answer_for(row: dict) -> str:
    width, height = row["size"]
    x1, y1, x2, y2 = row["box"]
    x = min(1000, max(0, round(1000 * (x1 + x2) / 2 / width)))
    y = min(1000, max(0, round(1000 * (y1 + y2) / 2 / height)))
    return json.dumps({"x": x, "y": y}, separators=(",", ":"))


def load_sources(specs: list[str]) -> list[tuple[list[tuple[Path, list[dict]]], float]]:
    sources = []
    for spec in specs:
        path, _, weight = spec.rpartition(":") if spec.count(":") > 1 else (spec, "", "")
        root = Path(path or spec)
        by_image: dict[str, list[dict]] = {}
        for line in (root / "train.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            by_image.setdefault(row["image"], []).append(row)
        images = [(root / image, rows) for image, rows in sorted(by_image.items()) if (root / image).is_file()]
        sources.append((images, float(weight or 1.0)))
        print(f"{root.name}: {len(images)} screenshots, {sum(len(r) for _, r in images)} targets, weight {weight or 1}")
    return sources


def epoch_examples(sources, per_image: int, rng: random.Random) -> list[tuple[Path, dict]]:
    """Up to per_image targets from every screenshot; sources are repeated by weight."""
    examples = []
    for images, weight in sources:
        take = []
        for image, rows in images:
            take += [(image, row) for row in rng.sample(rows, min(per_image, len(rows)))]
        whole, part = int(weight), weight - int(weight)
        examples += take * whole + rng.sample(take, int(len(take) * part))
    rng.shuffle(examples)
    return examples


class Trainer:
    def __init__(self, base: str, max_pixels: int, rank: int):
        from peft import LoraConfig, get_peft_model
        from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration
        self.processor = AutoProcessor.from_pretrained(base, local_files_only=True)
        self.processor.image_processor.size = {"longest_edge": max_pixels, "shortest_edge": 65536}
        model = Qwen3_5ForConditionalGeneration.from_pretrained(
            base, dtype=torch.bfloat16, device_map="cuda", attn_implementation="sdpa", local_files_only=True)
        model.config.use_cache = False
        config = LoraConfig(
            r=rank, lora_alpha=2 * rank, lora_dropout=0.05, bias="none",
            target_modules=r".*(language_model.*\.(q_proj|k_proj|v_proj|o_proj|in_proj_qkv|in_proj_z|out_proj|"
                           r"gate_proj|up_proj|down_proj)|visual.*\.(qkv|proj|linear_fc1|linear_fc2))$")
        self.model = get_peft_model(model, config)
        self.model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        self.model.enable_input_require_grads()
        self.model.print_trainable_parameters()

    def batch(self, image: Image.Image, prompt: str, answer: str | None):
        user = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": prompt}]}]
        head = self.processor.apply_chat_template(user, tokenize=True, add_generation_prompt=True,
                                                  return_dict=True, return_tensors="pt")
        if answer is None:
            return head.to("cuda")
        full = self.processor.apply_chat_template(
            user + [{"role": "assistant", "content": [{"type": "text", "text": answer}]}],
            tokenize=True, return_dict=True, return_tensors="pt")
        size = head["input_ids"].shape[1]
        if not torch.equal(full["input_ids"][0, :size], head["input_ids"][0]):
            raise RuntimeError("chat template: the prompt is not a prefix of the training sequence")
        labels = full["input_ids"].clone()
        labels[:, :size] = -100
        full["labels"] = labels
        return full.to("cuda")

    def loss(self, image, prompt, answer) -> torch.Tensor:
        return self.model(**self.batch(image, prompt, answer)).loss

    @torch.no_grad()
    def accuracy(self, samples: list[dict]) -> float:
        self.model.eval()
        hits = 0
        for sample in samples:
            image = Image.open(sample["image"]).convert("RGB")
            inputs = self.batch(image, holo_prompt(sample["instruction"]), None)
            out = self.model.generate(**inputs, do_sample=False, max_new_tokens=24,
                                      pad_token_id=self.processor.tokenizer.eos_token_id)
            text = self.processor.batch_decode(out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0]
            point = parse_holo(text)
            hits += bool(point and inside(sample, point[0] * image.width, point[1] * image.height))
        self.model.train()
        return hits / max(1, len(samples))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("out")
    parser.add_argument("--data", action="append", required=True, help="DIR or DIR:WEIGHT")
    parser.add_argument("--steps", type=int, default=0, help="optimizer steps (0 = one pass)")
    parser.add_argument("--accumulate", type=int, default=16)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--rank", type=int, default=32)
    parser.add_argument("--per-image", type=int, default=6)
    parser.add_argument("--max-pixels", type=int, default=2560 * 1440)
    parser.add_argument("--eval-every", type=int, default=100)
    parser.add_argument("--eval-n", type=int, default=150)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--translations", default="", help="JSON map of Hebrew labels to English")
    args = parser.parse_args()
    if args.translations:
        TRANSLATIONS.update(json.loads(Path(args.translations).read_text(encoding="utf-8")))
        print(f"{len(TRANSLATIONS)} label translations", flush=True)

    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sources = load_sources(args.data)
    # Hebrew score = held-out web pages plus every held-out Windows app sample.
    hebrew = random.Random(1).sample(load_hebrew_web(), args.eval_n) + load_hebrew_desktop()
    english = random.Random(1).sample(load_screenspot_v2(), args.eval_n)

    trainer = Trainer(args.base, args.max_pixels, args.rank)
    params = [p for p in trainer.model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)
    examples = epoch_examples(sources, args.per_image, rng)
    total = args.steps or math.ceil(len(examples) / args.accumulate)
    warmup = max(10, total // 20)
    schedule = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda step: min(1.0, (step + 1) / warmup)
                                                 * 0.5 * (1 + math.cos(math.pi * min(step, total) / total)))
    log = (out / "log.jsonl").open("a", encoding="utf-8")
    best = {"hebrew": trainer.accuracy(hebrew), "english": trainer.accuracy(english), "step": 0}
    print(f"step 0: hebrew {best['hebrew']:.3f} english {best['english']:.3f}", flush=True)
    log.write(json.dumps(best) + "\n")
    baseline_english = best["english"]

    step, seen, started, running = 0, 0, time.time(), []
    trainer.model.train()
    while step < total:
        if seen >= len(examples):
            examples, seen = epoch_examples(sources, args.per_image, rng), 0
        for _ in range(args.accumulate):
            image_path, row = examples[seen % len(examples)]
            seen += 1
            image = Image.open(image_path).convert("RGB")
            loss = trainer.loss(image, holo_prompt(describe(row, rng)), answer_for(row)) / args.accumulate
            loss.backward()
            running.append(loss.item() * args.accumulate)
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        optimizer.step()
        schedule.step()
        optimizer.zero_grad(set_to_none=True)
        step += 1
        if step % 10 == 0:
            rate = step * args.accumulate / (time.time() - started)
            print(f"step {step}/{total} loss {sum(running) / len(running):.4f} lr {schedule.get_last_lr()[0]:.2e} "
                  f"{rate:.2f} ex/s", flush=True)
            running = []
        if step % args.eval_every == 0 or step == total:
            score = {"step": step, "hebrew": trainer.accuracy(hebrew), "english": trainer.accuracy(english)}
            print(f"step {step}: hebrew {score['hebrew']:.3f} english {score['english']:.3f}", flush=True)
            log.write(json.dumps(score) + "\n")
            log.flush()
            # Keep the best Hebrew checkpoint that has not given up English accuracy.
            if score["hebrew"] > best["hebrew"] and score["english"] >= baseline_english - 0.02:
                best = score
                trainer.model.save_pretrained(out / "best")
                print(f"saved best adapter at step {step}", flush=True)
    trainer.model.save_pretrained(out / "last")
    (out / "best.json").write_text(json.dumps(best), encoding="utf-8")
    print("best", best, flush=True)


if __name__ == "__main__":
    main()
