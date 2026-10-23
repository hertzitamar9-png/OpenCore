"""Pure Hugging Face model planning. Repository code is never imported or executed."""
import hashlib
import json
from pathlib import PurePosixPath
import re
from urllib.parse import unquote, urlsplit

HUB = "https://huggingface.co"
HEX40 = re.compile(r"[0-9a-fA-F]{40}\Z")
HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z")
REPO_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}\Z")
QUANT = re.compile(r"(?<![A-Z0-9])((?:IQ|Q)[1-9][0-9]?(?:_[A-Z0-9]+)*|BF16|FP16|F16|FP32|F32)(?![A-Z0-9])", re.I)
SHARD = re.compile(r"[-_](\d{1,6})-of-(\d{1,6})(?=\.[^.]+$)", re.I)
AUXILIARY = re.compile(r"(?:^|[/_.-])(mmproj|imatrix|importance[-_]?matrix|draft|optimizer|optim|training|trainer|checkpoint[-_]?step|scheduler|rng[-_]?state)(?:[/_.-]|$)", re.I)
RESERVED = {"CON", "PRN", "AUX", "NUL", *("COM" + str(i) for i in range(1, 10)), *("LPT" + str(i) for i in range(1, 10))}
SUPPORT = {"config.json", "generation_config.json", "tokenizer.json", "tokenizer.model", "tokenizer_config.json",
           "special_tokens_map.json", "added_tokens.json", "vocab.json", "vocab.txt", "merges.txt",
           "preprocessor_config.json", "processor_config.json", "image_processor_config.json", "chat_template.jinja"}


def parse_repository(value):
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError("Enter a Hugging Face model repository or URL")
    value = value.strip()
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.netloc.lower() != "huggingface.co" or parsed.query or parsed.fragment:
            raise ValueError("Only https://huggingface.co model URLs are accepted")
        parts = unquote(parsed.path).strip("/").split("/")
        if len(parts) < 2 or (len(parts) > 2 and (parts[2] not in ("tree", "resolve", "blob") or len(parts) < 4)):
            raise ValueError("Enter the owner/model repository URL")
        value = "/".join(parts[:2])
    parts = value.split("/")
    if len(parts) != 2 or parts[0].lower() in ("datasets", "spaces"):
        raise ValueError("Use a model repository in owner/model form")
    if any(not REPO_PART.fullmatch(p) or ".." in p or "--" in p or p.endswith((".", "-")) for p in parts):
        raise ValueError("Invalid Hugging Face model repository")
    return "/".join(parts)


def safe_filename(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value or value.startswith("/"):
        raise ValueError("Unsafe repository filename")
    parts = value.split("/")
    for part in parts:
        if part in ("", ".", "..") or part.endswith((".", " ")) or any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            raise ValueError("Unsafe repository filename")
        if part.split(".")[0].upper() in RESERVED:
            raise ValueError("Unsafe Windows repository filename")
    return value


def stable_id(repo, key):
    return "hf-" + hashlib.sha256((repo.lower() + "\0" + key.lower()).encode("utf-8")).hexdigest()[:24]


def _pin(repo, revision, item, role):
    name = safe_filename(item.get("rfilename"))
    lfs = item.get("lfs") or {}
    size = lfs.get("size", item.get("size"))
    sha256 = lfs.get("sha256", lfs.get("oid", ""))
    if isinstance(sha256, str) and sha256.startswith("sha256:"):
        sha256 = sha256[7:]
    blob = item.get("blobId", item.get("oid", ""))
    # For LFS files the Git blob is a pointer, not the downloaded file.
    digest_ok = bool(isinstance(sha256, str) and HEX64.fullmatch(sha256))
    blob_ok = not lfs and isinstance(blob, str) and HEX40.fullmatch(blob)
    size_ok = type(size) is int and 0 <= size <= 100 * 1024 ** 4
    return {"repository": repo, "revision": revision, "filename": name, "bytes": size if size_ok else 0,
            "sha256": sha256.lower() if digest_ok else None, "gitBlobSha1": blob.lower() if blob_ok else None,
            "role": role, "pinned": bool(size_ok and (digest_ok or blob_ok))}


def _shard_problem(names):
    groups = {}
    for name in names:
        match = SHARD.search(name)
        if match:
            index, total = map(int, match.groups())
            key = SHARD.sub("", name)
            group = groups.setdefault(key, {"indices": set(), "totals": set()})
            group["indices"].add(index)
            group["totals"].add(total)
    for group in groups.values():
        if len(group["totals"]) != 1:
            return "The repository declares inconsistent shard counts"
        total = next(iter(group["totals"]))
        if total < 1 or total > 20000 or group["indices"] != set(range(1, total + 1)):
            return "The repository is missing one or more checkpoint shards"
    return None


def _quantization(name, config, format_name):
    match = QUANT.search(name.upper())
    if match:
        return match.group(1).upper()
    settings = config.get("quantization_config") or {}
    if isinstance(settings, dict) and settings:
        method = str(settings.get("quant_method", "quantized")).upper()[:40]
        bits = settings.get("bits")
        return method + (" " + str(bits) + "-bit" if type(bits) is int else "")
    dtype = config.get("torch_dtype", config.get("dtype", ""))
    return {"bfloat16": "BF16", "float16": "FP16", "float32": "FP32"}.get(str(dtype), "Original" if format_name != "gguf" else "GGUF")


def build_plan(metadata):
    if not isinstance(metadata, dict):
        raise ValueError("Invalid Hub metadata")
    repo = parse_repository(metadata.get("id", metadata.get("modelId", "")))
    revision = metadata.get("sha", "")
    if not isinstance(revision, str) or not HEX40.fullmatch(revision):
        raise ValueError("The Hub did not provide an immutable commit pin")
    revision = revision.lower()
    siblings = metadata.get("siblings")
    if not isinstance(siblings, list) or len(siblings) > 20000:
        raise ValueError("Repository file metadata is absent or exceeds the supported limit")
    files, seen = [], set()
    for item in siblings:
        if not isinstance(item, dict):
            raise ValueError("Invalid Hub file metadata")
        name = safe_filename(item.get("rfilename"))
        if name.casefold() in seen:
            raise ValueError("Repository filenames collide on Windows")
        seen.add(name.casefold())
        files.append(item)
    config = metadata.get("config") or {}
    if not isinstance(config, dict):
        config = {}
    groups = {}
    companions = [i for i in files if "mmproj" in PurePosixPath(i["rfilename"]).name.lower() and i["rfilename"].lower().endswith(".gguf")]
    for item in files:
        name = item["rfilename"]
        path = PurePosixPath(name)
        if AUXILIARY.search(name):
            continue
        extension = path.suffix.lower()
        if extension == ".gguf":
            stem = SHARD.sub("", name)
            key, format_name = "gguf:" + stem, "gguf"
        elif extension in (".safetensors", ".bin", ".onnx", ".ggml", ".pt", ".pth"):
            format_name = {".safetensors": "safetensors", ".bin": "pytorch", ".onnx": "onnx"}.get(extension, extension[1:])
            key = format_name + ":" + str(path.parent)
        else:
            continue
        group = groups.setdefault(key, {"format": format_name, "subdirectory": "" if str(path.parent) == "." else str(path.parent), "items": []})
        group["items"].append(item)
    variants = []
    for key, group in sorted(groups.items()):
        items = sorted(group["items"], key=lambda x: x["rfilename"])
        format_name, subdir = group["format"], group["subdirectory"]
        quant = _quantization(key, config, format_name)
        pins = [_pin(repo, revision, item, "model") for item in items]
        if format_name == "gguf":
            candidates = [i for i in companions if str(PurePosixPath(i["rfilename"]).parent) == (subdir or ".")]
            if candidates:
                # Prefer one full precision projector; including every projector can multiply disk use.
                candidates.sort(key=lambda i: (not bool(re.search(r"(?:bf16|f16|fp16)", i["rfilename"], re.I)), i["rfilename"]))
                pins.append(_pin(repo, revision, candidates[0], "companion"))
        else:
            support_dirs = {".", subdir or "."}
            for item in files:
                path = PurePosixPath(item["rfilename"])
                if str(path.parent) in support_dirs and (path.name in SUPPORT or path.name.endswith((".safetensors.index.json", ".bin.index.json", ".tiktoken"))):
                    pins.append(_pin(repo, revision, item, "support"))
        reason = _shard_problem([i["rfilename"] for i in items])
        if any(not pin["pinned"] for pin in pins):
            reason = reason or "The Hub did not provide a file size and checksum for every required file"
        runtime_kind = "gguf" if format_name == "gguf" else "transformers" if format_name == "safetensors" else None
        runtime = {"adapter": runtime_kind, "available": False, "inferenceVerified": False,
                   "reason": "Runtime discovery and an actual generation are required" if runtime_kind else "No verified managed runtime adapter supports this format"}
        variants.append({"id": stable_id(repo, key), "repository": repo, "revision": revision, "variantKey": key,
            "name": repo.split("/", 1)[1] + " · " + quant + (" · " + subdir if subdir else ""),
            "quantization": quant, "format": format_name, "subdirectory": subdir,
            "bytes": sum(p["bytes"] for p in pins), "files": pins, "installable": reason is None,
            "reason": reason, "modelType": config.get("model_type"), "architectures": config.get("architectures", []),
            "runtime": runtime, "private": metadata.get("private") is True, "gated": bool(metadata.get("gated")),
            "sourceUrl": HUB + "/" + repo, "status": "not-installed"})
    # Key and commit pin bind Add to the metadata that was actually reviewed.
    identity = json.dumps({"repository": repo, "revision": revision, "variants": variants}, sort_keys=True, separators=(",", ":"))
    return {"planId": hashlib.sha256(identity.encode("utf-8")).hexdigest(), "repository": repo, "revision": revision,
            "private": metadata.get("private") is True, "gated": bool(metadata.get("gated")),
            "pipelineTag": metadata.get("pipeline_tag"), "variants": variants,
            "warning": None if variants else "No supported main model files were found; repository code is never executed"}
