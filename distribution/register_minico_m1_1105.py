"""Reproducible, revision-pinned Minico M1 1105 GGUF catalog admission.

Model: https://huggingface.co/Mincofficial/Minico-M1-1105
Published quantizations: https://huggingface.co/Mincofficial/Minico-M1-1105-GGUF
Hashes and exact file sizes were read from the publisher's LFS tree at REV.
Weights are NOT bundled or fetched by this script.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "src-tauri/resources/model-catalog.json"
REPO = "Mincofficial/Minico-M1-1105-GGUF"
SOURCE = "Mincofficial/Minico-M1-1105"
REV = "eee16357785998f7fa035e543eef1b902d77d94d"
PARAMS = 354483968
QUANTS = {
    "q4": ("Q4_K_M", "Minico-M1-1105-Q4_K_M.gguf", 229316992, "2b0456131f1efeac1f6a7af85ea4541b6d12b20bcd94af628dfc64ef88460125"),
    "q6": ("Q6_K", "Minico-M1-1105-Q6_K.gguf", 293386624, "62442983679b78d5bc55fe75830380a8a25bccc6f6e190ea58d8df4d6c12a483"),
    "q8": ("Q8_0", "Minico-M1-1105-Q8_0.gguf", 379222176, "d8a514f7cf15ee2b32c89864c4046e7c8bd4e4dc262ae1c732d2b7af8ab2bef2"),
    "f16": ("F16", "Minico-M1-1105-F16.gguf", 711489696, "6d001d6d8c99daf7309f5162176e77a5439621fec64ed7c9f833a4db8aad3e4d"),
    "bf16": ("BF16", "Minico-M1-1105-BF16.gguf", 711489696, "dd19f3109cdd075540554d9214e66155993ba128b9f1788d41b6178b872657c4"),
}


def register(catalog):
    existing_models = {row["id"]: row for row in catalog["models"]}
    existing_artifacts = {row["id"] for row in catalog["artifacts"]}
    for key, (precision, filename, size, digest) in QUANTS.items():
        root = f"minico-m1-1105-{key}"
        artifact_id = f"{root}-weight"
        relative = f"models/library/{root}/{filename}"
        if artifact_id not in existing_artifacts:
            catalog["artifacts"].append(dict(
                id=artifact_id, path=relative, repo=REPO, revision=REV,
                filename=filename, sha256=digest, bytes=size))
        note = ("Publisher-pinned Minico M1 1105 (LFM2.5 350M finetune). "
                "Uses OpenCore's existing LFM2 GGUF execution path; local inference, "
                "reasoning-effort control, quality and speed have not been validated. "
                "No weights are bundled or downloaded automatically. "
                "LFM Open License v1.0 applies; review terms before commercial use.")
        for mode in ("echo", "native"):
            model_id = root + ("-native" if mode == "native" else "")
            if model_id in existing_models:
                existing_models[model_id]["contextTokens"] = 128000 if mode == "native" else 32768
                if mode == "echo":
                    existing_models[model_id]["echoWorkingWindowTokens"] = 32768
                continue
            row = dict(
                id=model_id, label=f"Minico M1 1105 · {precision}" + (" · Native" if mode == "native" else ""),
                description="Minico M1 1105 · compact reasoning model",
                precision=precision, contextTokens=128000 if mode == "native" else 32768,
                artifacts=[artifact_id], weightArtifacts=[artifact_id],
                license="lfm1.0", experimental=True, note=note,
                selectable=True, installable=True, runtimeReady=True,
                category="text", backend="gguf",
                sourceUrl=f"https://huggingface.co/{REPO}/tree/{REV}",
                setupUrl=f"https://huggingface.co/{SOURCE}",
                inputModalities=["text"], outputModalities=["text"],
                brainRole="controller", parameterCount=PARAMS,
                runtimeModelPath=relative, memoryMode=mode,
            )
            if mode == "native":
                row["variantOf"] = root
            else:
                row["echoWorkingWindowTokens"] = 32768
            catalog["models"].append(row)
    return catalog


if __name__ == "__main__":
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    register(catalog)
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("Registered Minico M1 1105 GGUF: " + ", ".join(quant[0] for quant in QUANTS.values()) + "; Native and ECHO.")
