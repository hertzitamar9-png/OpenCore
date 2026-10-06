"""Refresh selectable GGUF quantization profiles from pinned Hugging Face metadata.

This fetches repository manifests only. It never downloads model weights.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "src-tauri" / "resources" / "model-catalog.json"
QUANTIZATIONS = (
    "IQ4_NL", "IQ4_XS", "IQ3_XXS", "IQ3_XS", "IQ3_S", "IQ3_M", "IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ2_M",
    "I1-Q5_K_M", "IQ1_S", "IQ1_M", "Q8_K_XL", "Q8_K_L", "Q8_0", "Q7_K", "Q6_K_XL", "Q6_K_L", "Q6_K", "Q5_K_XL",
    "Q5_K_L", "Q5_K_M", "Q5_K_S", "Q5_1", "Q5_0", "Q4_K_XL", "Q4_K_L", "Q4_K_M", "Q4_K_S", "Q4_1",
    "Q4_0", "Q3_K_XL", "Q3_K_L", "Q3_K_M", "Q3_K_S", "Q2_K_XL", "Q2_K_L", "Q2_K_S", "Q2_K", "TQ2_0", "TQ1_0",
    "PQ2_0", "PTQ1_0", "Q6_K_S", "NVFP4", "MXFP4_MOE", "MXFP4", "FP8_E5M2", "FP8_E4M3", "FP8", "BF16", "F16", "F32",
)
QUANT_PATTERN = re.compile(r"(?:^|[-_.])(" + "|".join(re.escape(item) for item in QUANTIZATIONS) + r")$", re.IGNORECASE)
MTP_SUFFIX = re.compile(r"[-_.](LOW[-_]MTP|MTP)$", re.IGNORECASE)
SHARD_SUFFIX = re.compile(r"[-_.](\d{5})-of-(\d{5})$", re.IGNORECASE)
MTP_BEFORE_QUANT = re.compile(r"(?:^|[-_.])(LOW[-_]MTP|MTP)(?:[-_.])", re.IGNORECASE)
SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def quantization_for_filename(filename: str, intrinsic_mtp: bool = False) -> tuple[str, str, tuple[int, int] | None] | None:
    """Return (display quant, unsharded stem, shard metadata) for model GGUF files."""
    if not filename.lower().endswith(".gguf"):
        return None
    lowered = filename.lower()
    if any(marker in lowered for marker in ("mmproj", "imatrix", "mtp-head", "mtp_head", "tokenizer")):
        return None
    stem = filename[:-5]
    shard = SHARD_SUFFIX.search(stem)
    shard_info = (int(shard.group(1)), int(shard.group(2))) if shard else None
    if shard:
        stem = stem[:shard.start()]
    mtp = MTP_SUFFIX.search(stem)
    mode = ""
    if mtp:
        mode = " LOW-MTP" if mtp.group(1).lower().startswith("low") else " MTP"
        stem = stem[:mtp.start()]
    found = QUANT_PATTERN.search(stem)
    if not found:
        return None
    if not mode and not intrinsic_mtp:
        before = MTP_BEFORE_QUANT.search(stem[:found.start(1)])
        if before:
            mode = " LOW-MTP" if before.group(1).lower().startswith("low") else " MTP"
    quant = found.group(1).upper().replace("_MOE", "_MOE")
    return f"{quant}{mode}", stem[:found.start()], shard_info


def fetch_repository(repo: str) -> dict:
    request = urllib.request.Request(
        f"https://huggingface.co/api/models/{repo}?blobs=true",
        headers={"User-Agent": "OpenCore-model-catalog/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.load(response)
    if not re.fullmatch(r"[0-9a-f]{40}", value.get("sha", "")):
        raise RuntimeError(f"Hub response for {repo} did not include an immutable commit SHA")
    return value


def artifact_id(root_id: str, filename: str, checksum: str) -> str:
    suffix = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:8]
    return f"{root_id}-variant-{suffix}-{checksum[:8].lower()}"


def profile_id(root_id: str, precision: str, checksum: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", precision.lower()).strip("-")
    return f"{root_id}-{slug}-{checksum[:6].lower()}"


def _valid_file(sibling: dict) -> bool:
    lfs = sibling.get("lfs") or {}
    return (isinstance(sibling.get("size"), int) and sibling["size"] > 0
            and isinstance(lfs.get("sha256"), str) and bool(SHA256.fullmatch(lfs["sha256"])))


def source_gguf_artifact(catalog: dict, model: dict) -> dict | None:
    """Find the pinned primary GGUF artifact, excluding projectors and metadata sidecars."""
    files = [artifact for artifact in catalog["artifacts"]
             if artifact["id"] in model.get("artifacts", [])
             and artifact.get("filename", "").lower().endswith(".gguf")
             and not any(marker in artifact.get("filename", "").lower()
                         for marker in ("mmproj", "imatrix", "mtp-head", "mtp_head", "tokenizer"))]
    runtime_path = model.get("runtimeModelPath")
    return next((artifact for artifact in files if artifact["path"] == runtime_path), files[0] if files else None)


def refreshable_parents(catalog: dict) -> list[tuple[dict, dict]]:
    result = []
    for model in catalog["models"]:
        if model.get("variantOf") and not model.get("refreshQuantizations"):
            continue
        regular_text_gguf = (model.get("selectable") and model.get("category") == "text"
                             and model.get("backend") == "gguf" and model.get("memoryMode", "echo") == "echo")
        if not regular_text_gguf and not model.get("refreshQuantizations"):
            continue
        source = source_gguf_artifact(catalog, model)
        if source:
            result.append((model, source))
    return result


def add_repository_variants(catalog: dict, parent: dict, repository: dict) -> int:
    revision = repository["sha"]
    parent_id = parent["id"]
    siblings = repository.get("siblings", [])
    source_artifact = source_gguf_artifact(catalog, parent)
    if not source_artifact or source_artifact["repo"] != repository.get("id"):
        raise RuntimeError(f"Repository metadata does not match the pinned source for {parent_id}")
    source_filename = Path(source_artifact["filename"]).name
    source_parse = quantization_for_filename(source_filename, intrinsic_mtp="mtp" in parent_id.lower())
    expected_stem = source_parse[1] if source_parse else source_filename[:-5]
    grouped: dict[tuple[str, str], list[dict]] = {}
    for sibling in siblings:
        filename = sibling.get("rfilename", "")
        parsed = quantization_for_filename(filename, intrinsic_mtp="mtp" in parent_id.lower())
        if parsed is None or not _valid_file(sibling):
            continue
        precision, unsharded_stem, shard = parsed
        if Path(unsharded_stem).name.casefold() != expected_stem.casefold():
            continue
        grouped.setdefault((precision, unsharded_stem), []).append(sibling)

    model_by_id = {model["id"]: model for model in catalog["models"]}
    parent_artifacts = {artifact["id"]: artifact for artifact in catalog["artifacts"]}
    directory = Path(parent.get("runtimeModelPath") or source_artifact["path"]).parent.as_posix()
    projector_id = None
    projector_path = parent.get("visionProjectorPath")
    if projector_path:
        projector_id = next((item["id"] for item in catalog["artifacts"] if item["path"] == projector_path), None)

    added = 0
    for (precision, _), siblings_for_model in sorted(grouped.items()):
        siblings_for_model.sort(key=lambda item: item["rfilename"])
        shard_info = [SHARD_SUFFIX.search(item["rfilename"][:-5]) for item in siblings_for_model]
        if any(shard_info):
            counts = {int(match.group(2)) for match in shard_info if match}
            indices = {int(match.group(1)) for match in shard_info if match}
            if len(counts) != 1 or len(siblings_for_model) != next(iter(counts)) or indices != set(range(1, next(iter(counts)) + 1)):
                continue
        checksum = siblings_for_model[0]["lfs"]["sha256"].lower()
        model_id = profile_id(parent_id, precision, checksum)
        if precision.upper() == parent.get("precision", "").upper():
            continue
        existing_model = model_by_id.get(model_id)
        # Preserve existing immutable profiles rather than mutating a shipped pin.
        if not existing_model and any(model.get("variantOf") == parent_id and model.get("precision", "").upper() == precision.upper()
                                      for model in catalog["models"]):
            continue
        artifact_ids = []
        primary_paths = []
        for sibling in siblings_for_model:
            filename = sibling["rfilename"]
            sha = sibling["lfs"]["sha256"].lower()
            aid = artifact_id(parent_id, filename, sha)
            rel_path = f"{directory}/variants/{filename}"
            previous = parent_artifacts.get(aid)
            if previous and (previous["sha256"] != sha or previous["path"] != rel_path):
                raise RuntimeError(f"Artifact id collision while adding {parent_id}/{filename}")
            if not previous:
                artifact = {
                    "id": aid, "path": rel_path, "repo": source_artifact["repo"],
                    "revision": revision, "filename": filename, "sha256": sha, "bytes": sibling["size"],
                }
                catalog["artifacts"].append(artifact)
                parent_artifacts[aid] = artifact
            artifact_ids.append(aid)
            primary_paths.append(rel_path)
        if projector_id and projector_id not in artifact_ids:
            artifact_ids.append(projector_id)
        if existing_model:
            existing_model["weightArtifacts"] = artifact_ids.copy()
            native_existing = model_by_id.get(f"{model_id}-native")
            if native_existing:
                native_existing["weightArtifacts"] = artifact_ids.copy()
            continue
        label = f"{parent['label']} · {precision}"
        is_text_gguf = parent.get("category") == "text" and parent.get("backend") == "gguf"
        experimental = copy.deepcopy(parent)
        experimental.update({
            "id": model_id, "label": label, "description": f"Published {precision} GGUF variant of {parent['label']}.",
            "precision": precision, "artifacts": artifact_ids,
            "note": (f"Published GGUF file(s), pinned to {revision}. MTP variants use a separate predictive format; speculative speed and quality are not verified. "
                     "VRAM values are estimates; download sizes come from Hub file metadata." if "MTP" in precision else
                     f"Published GGUF file(s), pinned to {revision}. Exact download size is based on Hub file metadata; VRAM is an estimate."),
            "runtimeModelPath": primary_paths[0], "variantOf": parent_id,
            "weightArtifacts": artifact_ids.copy(),
        })
        if parent.get("category") == "image":
            experimental["note"] = (f"Published {precision} GGUF denoiser only, pinned to {revision}. "
                                    "Also requires its matching text encoder and VAE plus a compatible image runtime; "
                                    "app image runtime setup is separate and local generation is unverified.")
        if is_text_gguf:
            experimental["memoryMode"] = "echo"
        if projector_path:
            experimental["visionProjectorPath"] = projector_path
        catalog["models"].append(experimental)
        model_by_id[model_id] = experimental
        if is_text_gguf:
            native = copy.deepcopy(experimental)
            native["id"] = f"{model_id}-native"
            native["label"] = f"{label} · Native"
            native["description"] = f"{parent['label']} {precision} variant with native context; ECHO retrieval is disabled."
            native["memoryMode"] = "native"
            catalog["models"].append(native)
            model_by_id[native["id"]] = native
        added += 1
    return added


def refresh_catalog(catalog: dict, repositories: dict[str, dict]) -> int:
    added = 0
    seen_repos: set[str] = set()
    for parent, artifact in refreshable_parents(catalog):
        if artifact.get("repo") in seen_repos:
            continue
        repo = artifact["repo"]
        seen_repos.add(repo)
        if repo not in repositories:
            continue
        added += add_repository_variants(catalog, parent, repositories[repo])
    return added


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    parents = refreshable_parents(catalog)
    repositories = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {}
        for parent, artifact in parents:
            if artifact and artifact["repo"] not in pending:
                pending[artifact["repo"]] = pool.submit(fetch_repository, artifact["repo"])
        for repo, future in pending.items():
            try:
                repositories[repo] = future.result()
            except Exception as error:
                print(f"Could not refresh {repo}: {error}", file=sys.stderr)
                return 1
    added = refresh_catalog(catalog, repositories)
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Catalog refreshed from {len(repositories)} Hugging Face repository manifests; added {added} quantization profiles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
