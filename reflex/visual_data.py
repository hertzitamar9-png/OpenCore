"""Validate screenshot/action episodes before training a desktop action policy.

Input: ROOT/episodes.jsonl, one task episode per line. Each step pairs the
screen *before* an action with one typed action. Recent actions are reconstructed
from earlier steps, so evaluation must split entire episodes, never steps.
This module deliberately does not execute actions or train a model.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import argparse
import json
import math

from PIL import Image


class DatasetError(ValueError):
    pass


ACTION_FIELDS = {
    "click": {"x", "y"},
    "drag": {"x", "y", "end_x", "end_y", "duration_ms"},
    "key": {"keys"},
    "type": {"text"},
    "wait": {"duration_ms"},
    "done": set(),
}
OPTIONAL_FIELDS = {"click": {"button"}}
SPLITS = {"train", "eval", "test"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _positive_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise DatasetError(f"{name} must be nonempty text")


def _coordinate(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise DatasetError("normalized coordinate must be a finite number from 0 to 1")


def _duration(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 10_000:
        raise DatasetError("duration_ms must be between 0 and 10000")


def _action(action):
    if not isinstance(action, dict) or action.get("type") not in ACTION_FIELDS:
        raise DatasetError("unsupported action type; executable code is not an action")
    kind = action["type"]
    required = ACTION_FIELDS[kind]
    allowed = required | OPTIONAL_FIELDS.get(kind, set()) | {"type"}
    if not required <= action.keys() or action.keys() - allowed:
        raise DatasetError(f"{kind} action fields do not match the typed schema")
    for key in ("x", "y", "end_x", "end_y"):
        if key in action:
            _coordinate(action[key])
    if "duration_ms" in action:
        _duration(action["duration_ms"])
    if kind == "click" and action.get("button", "left") not in {"left", "right", "middle"}:
        raise DatasetError("unsupported mouse button")
    if kind == "key" and (not isinstance(action["keys"], list) or not action["keys"]
                          or any(not isinstance(k, str) or not k.strip() for k in action["keys"])):
        raise DatasetError("keys must be a nonempty list of key names")
    if kind == "type":
        _positive_text(action["text"], "typed text")


def _screenshot(root: Path, path: str) -> None:
    if not isinstance(path, str) or not path or Path(path).is_absolute():
        raise DatasetError("screenshot path must be relative to dataset root")
    root = root.resolve()
    image_path = (root / path).resolve()
    if not image_path.is_relative_to(root) or image_path.suffix.lower() not in IMAGE_SUFFIXES:
        raise DatasetError("screenshot path escapes dataset root or is not an image")
    if not image_path.is_file():
        raise DatasetError(f"missing screenshot: {path}")
    try:
        with Image.open(image_path) as image:
            image.verify()
        with Image.open(image_path) as image:
            if image.width < 2 or image.height < 2:
                raise DatasetError(f"invalid screenshot size: {path}")
    except (OSError, SyntaxError) as error:
        raise DatasetError(f"invalid screenshot: {path}") from error


def validate_dataset(root: str | Path) -> dict:
    root = Path(root).resolve()
    manifest = root / "episodes.jsonl"
    if not manifest.is_file():
        raise DatasetError(f"missing episodes.jsonl: {root}")
    seen_ids: set[str] = set()
    image_splits: dict[str, str] = {}
    actions: Counter[str] = Counter()
    splits: Counter[str] = Counter()
    apps: Counter[str] = Counter()
    steps_total = 0
    with manifest.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                episode = json.loads(line)
                if not isinstance(episode, dict):
                    raise DatasetError("episode must be an object")
                for field in ("episode_id", "source", "app", "instruction"):
                    _positive_text(episode.get(field), field)
                episode_id = episode["episode_id"]
                if episode_id in seen_ids:
                    raise DatasetError(f"duplicate episode_id: {episode_id}")
                seen_ids.add(episode_id)
                split = episode.get("split")
                if split not in SPLITS:
                    raise DatasetError("split must be train, eval, or test")
                if split == "train" and "osworld" in episode["source"].casefold().replace("-", ""):
                    raise DatasetError("benchmark leakage: OSWorld-derived episodes cannot enter training")
                steps = episode.get("steps")
                if not isinstance(steps, list) or not steps:
                    raise DatasetError("steps must be a nonempty list")
                if any(not isinstance(step, dict) for step in steps):
                    raise DatasetError("each step must be an object")
                for step in steps:
                    _screenshot(root, step.get("screenshot"))
                    image_path = str((root / step["screenshot"]).resolve())
                    previous_split = image_splits.setdefault(image_path, split)
                    if previous_split != split:
                        raise DatasetError("screenshot appears in multiple splits")
                    _action(step.get("action"))
                    actions[step["action"]["type"]] += 1
                splits[split] += 1
                apps[episode["app"]] += 1
                steps_total += len(steps)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                raise DatasetError(f"line {line_number}: {error}") from error
    if not seen_ids:
        raise DatasetError("dataset has no episodes")
    return {"episodes": len(seen_ids), "steps": steps_total,
            "splits": dict(sorted(splits.items())), "apps": dict(sorted(apps.items())),
            "action_types": dict(sorted(actions.items()))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="directory containing episodes.jsonl and screenshots")
    args = parser.parse_args()
    print(json.dumps(validate_dataset(args.root), indent=2))


if __name__ == "__main__":
    main()
