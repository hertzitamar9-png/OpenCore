"""Materialize the requested text models from a reviewed immutable pin manifest.

This reads local evidence only; it downloads no weights and never invents a
quantization. Separate MTP/template and importance-matrix packages keep their
own labels even when they use the same nominal precision.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "src-tauri/resources/requested-text-model-pins.json"
CATALOG = ROOT / "src-tauri/resources/model-catalog.json"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def materialize(catalog: dict, pins: dict) -> None:
    artifacts = {item["id"]: item for item in catalog["artifacts"]}
    models = {item["id"]: item for item in catalog["models"]}
    for package in pins["packages"]:
        root = package["modelId"]
        primary = package.get("defaultFilename")
        files = sorted(package["files"], key=lambda item: (item["filename"] != primary, item["filename"]))
        source_only = package.get("sourceOnly", False)
        shared_ids = []
        for file in files:
            if not re.fullmatch(r"[0-9a-f]{64}", file["sha256"]) or file["bytes"] <= 0:
                raise ValueError(f"Unqualified file: {file['filename']}")
            suffix = hashlib.sha256(file["filename"].encode()).hexdigest()[:8]
            aid = f"{root}-{suffix}-{file['sha256'][:8]}"
            artifact = {"id": aid, "path": f"models/library/{root}/{file['filename']}",
                        "repo": file.get("repo", package["repo"]),
                        "revision": file.get("revision", package["revision"]),
                        "filename": file["filename"], "sha256": file["sha256"], "bytes": file["bytes"]}
            if aid in artifacts and artifacts[aid] != artifact:
                raise ValueError(f"Immutable artifact changed: {aid}")
            if aid not in artifacts:
                catalog["artifacts"].append(artifact)
                artifacts[aid] = artifact
            shared_ids.append(aid)
            if source_only:
                continue
            precision = file["precision"]
            model_id = root if file["filename"] == primary else f"{root}-{slug(precision)}-{file['sha256'][:6]}"
            model = {"id": model_id, "label": package["label"],
                     "description": package["description"], "precision": precision,
                     "contextTokens": 16_384, "artifacts": [aid], "weightArtifacts": [aid],
                     "license": package["license"], "experimental": True,
                     "note": package["note"] + " " + file.get("note", ""),
                     "selectable": True, "category": "text", "backend": "gguf",
                     "runtimeReady": True, "installable": True, "memoryMode": "echo",
                     "sourceUrl": f"https://huggingface.co/{artifact['repo']}/tree/{artifact['revision']}",
                     "setupUrl": "https://github.com/ggml-org/llama.cpp",
                     "runtimeModelPath": artifact["path"]}
            if model_id != root:
                model["variantOf"] = root
            add_pair(catalog, models, model, root)
        if source_only:
            prepared = package.get("preparedRuntime")
            model = {"id": root, "label": package["label"], "description": package["description"],
                     "precision": "Published MLX 4-bit", "contextTokens": 16_384 if prepared else 0,
                     "artifacts": shared_ids, "weightArtifacts": [aid for aid in shared_ids
                         if artifacts[aid]["filename"] == "model.safetensors"],
                     "license": package["license"], "experimental": True, "note": package["note"],
                     "selectable": False, "category": "text", "backend": "gguf" if prepared else "external",
                     "runtimeReady": False, "installable": True, "memoryMode": "echo",
                     "sourceUrl": f"https://huggingface.co/{package['repo']}/tree/{package['revision']}",
                     "setupUrl": package["setupUrl"]}
            if prepared:
                model["preparedRuntime"] = copy.deepcopy(prepared)
                model["runtimeModelPath"] = prepared["path"]
            add_pair(catalog, models, model, root)


def add_pair(catalog: dict, models: dict, echo: dict, root: str) -> None:
    native = copy.deepcopy(echo)
    native.update({"id": f"{echo['id']}-native", "variantOf": root, "memoryMode": "native"})
    for item in (echo, native):
        if item["id"] in models:
            if models[item["id"]] != item:
                raise ValueError(f"Existing profile differs: {item['id']}")
            continue
        catalog["models"].append(item)
        models[item["id"]] = item


if __name__ == "__main__":
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    materialize(catalog, json.loads(PINS.read_text(encoding="utf-8")))
    CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
