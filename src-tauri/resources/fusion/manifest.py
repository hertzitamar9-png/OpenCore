"""Verify pinned local Fusion source and weights before loading model code."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _safe_name(value: str) -> bool:
    return bool(value) and Path(value).name == value and value not in (".", "..")


def verify_checkpoint(source_root: Path, manifest: dict, model_key: str) -> Path:
    """Verify pinned files and ensure the index names the full shard set.

    Large Xet/LFS files use their published SHA-256. Small files use the
    repository's immutable Git blob SHA-1, which also pins custom model code.
    """
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported Fusion checkpoint manifest schema")
    try:
        entry = manifest["models"][model_key]
    except (KeyError, TypeError) as error:
        raise ValueError(f"Fusion manifest has no {model_key} checkpoint") from error
    folder = entry.get("folder")
    if not isinstance(folder, str) or not _safe_name(folder):
        raise ValueError("Fusion checkpoint folder must be a single safe name")
    revision = entry.get("revision")
    if not isinstance(revision, str) or len(revision) != 40 or not all(
        char in "0123456789abcdef" for char in revision.lower()
    ):
        raise ValueError("Fusion checkpoint must pin a full 40-character revision")
    if not folder.endswith(revision):
        raise ValueError("Fusion source folder must end with its pinned revision")
    root = source_root / folder
    if not root.is_dir():
        raise ValueError(f"Fusion checkpoint folder missing: {root}")
    files = entry.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Fusion checkpoint manifest has no files")
    for name, expected in files.items():
        if not isinstance(name, str) or not _safe_name(name):
            raise ValueError("Fusion manifest contains an unsafe filename")
        path = root / name
        if not path.is_file():
            raise ValueError(f"Fusion checkpoint file missing: {name}")
        if path.resolve().parent != root.resolve():
            raise ValueError(f"Fusion checkpoint file escapes its folder: {name}")
        size = path.stat().st_size
        if size != expected["bytes"]:
            raise ValueError(f"Fusion checkpoint size mismatch: {name}")
        sha256 = expected.get("sha256")
        git_blob_sha1 = expected.get("git_blob_sha1")
        if bool(sha256) == bool(git_blob_sha1):
            raise ValueError(f"Fusion manifest needs exactly one file digest: {name}")
        digest = hashlib.sha256() if sha256 else hashlib.sha1()
        if git_blob_sha1:
            digest.update(f"blob {size}\0".encode("ascii"))
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(block)
        wanted = sha256 or git_blob_sha1
        if digest.hexdigest() != wanted:
            raise ValueError(f"Fusion checkpoint hash mismatch: {name}")
    index_path = root / "model.safetensors.index.json"
    if index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index_shards = set(index["weight_map"].values())
        manifest_shards = {name for name in files if name.endswith(".safetensors")}
        if index_shards != manifest_shards or not index_shards:
            raise ValueError("Fusion checkpoint shard set does not match its index")
    else:
        raise ValueError("Fusion checkpoint weight index missing")
    return root
