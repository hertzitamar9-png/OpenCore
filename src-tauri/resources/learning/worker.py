"""Owned, checkpoint-sized Unsloth training process.

The standard-library policy layer is also usable without training dependencies.
Training loads a frozen local Transformers snapshot, never changes that snapshot,
and writes separate PEFT adapters with real optimizer checkpoints. A checkpoint
event becomes actionable only after this process exits and the native GPU lease
has been released. Official API references are in requirements.json.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import traceback

from config import DEFAULT_CONFIG, finite_number, validate_config

SCHEMA_VERSION = 1
MAX_DATA_BYTES = 512 * 1024**2
MAX_RECORD_BYTES = 1024**2
MAX_RECORDS = 100000
DEPENDENCIES = ("unsloth", "unsloth-zoo", "torch", "transformers", "trl", "peft", "datasets", "accelerate")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, "item"):
        return json_safe(value.item())
    return str(value)


def canonical(value):
    return json.dumps(json_safe(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path, guard=None):
    before = path.stat()
    result = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 * 1024**2):
            if guard:
                guard()
            result.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"source changed while hashing: {path}")
    return result.hexdigest()


def read_json(path):
    def nonfinite(value):
        raise ValueError(f"non-finite JSON literal {value} in {path}")
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        result = json.load(handle, parse_constant=nonfinite)
    if not isinstance(result, dict):
        raise ValueError(f"JSON object required: {path}")
    return result


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(json_safe(value), handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_json(path, value):
    with Path(path).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical(value).decode("utf-8") + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def source_ids(row):
    result = set()
    for key in ("sourceId", "sourceConversationId"):
        value = row.get(key)
        if isinstance(value, str) and value:
            result.add(value)
    values = row.get("sourceIds", [])
    if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
        raise ValueError("sourceIds must contain nonempty strings")
    result.update(values)
    return sorted(result)


def validate_messages(value):
    if not isinstance(value, list) or not value:
        raise ValueError("messages must be a nonempty array")
    for item in value:
        if not isinstance(item, dict) or item.get("role") not in ("system", "user", "assistant", "tool", "developer") or not isinstance(item.get("content"), str):
            raise ValueError("only text messages with role and content are supported")
    if value[-1]["role"] != "assistant" or not value[-1]["content"].strip():
        raise ValueError("SFT messages must end with a nonempty assistant answer")
    return value


def normalize_record(row, method, line):
    if not isinstance(row, dict):
        raise ValueError(f"record {line} must be a JSON object")
    if method == "dpo":
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("prompt", "chosen", "rejected")) or row.get("chosen") == row.get("rejected"):
            raise ValueError(f"DPO record {line} needs nonempty prompt and distinct chosen/rejected answers")
        payload = {key: row[key] for key in ("prompt", "chosen", "rejected")}
    elif "messages" in row:
        payload = {"messages": validate_messages(row["messages"])}
    elif "prompt" in row or "completion" in row:
        if not isinstance(row.get("prompt"), str) or not isinstance(row.get("completion"), str) or not row["completion"].strip():
            raise ValueError(f"SFT record {line} needs prompt and nonempty completion")
        payload = {key: row[key] for key in ("prompt", "completion")}
    elif isinstance(row.get("text"), str) and row["text"].strip():
        payload = {"text": row["text"]}
    else:
        raise ValueError(f"SFT record {line} needs messages, prompt/completion, or text")
    return {"line": line, "payload": payload, "payloadSha256": digest(payload), "sourceIds": source_ids(row), "provenance": {key: item for key, item in row.items() if key not in payload}}


def load_records(path, method):
    if path.stat().st_size > MAX_DATA_BYTES:
        raise ValueError(f"dataset exceeds explicit {MAX_DATA_BYTES} byte input limit: {path}")
    result = []
    with path.open("rb") as handle:
        for number, raw in enumerate(handle, 1):
            if len(raw) > MAX_RECORD_BYTES:
                raise ValueError(f"record {number} exceeds {MAX_RECORD_BYTES} byte limit")
            if not raw.strip():
                continue
            try:
                row = json.loads(raw.decode("utf-8-sig"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON")))
                result.append(normalize_record(row, method, number))
            except (UnicodeError, ValueError) as error:
                raise ValueError(f"invalid dataset {path.name} line {number}: {error}") from error
            if len(result) > MAX_RECORDS:
                raise ValueError(f"dataset exceeds {MAX_RECORDS} record input limit")
    if not result:
        raise ValueError(f"dataset is empty: {path}")
    return result


def is_within(path, parent):
    return path == parent or parent in path.parents


def inspect_model_snapshot(path, config):
    """Hardware-independent snapshot checks; never import or load model code."""
    path = Path(path).expanduser().resolve()
    if not path.is_dir() or not (path / "config.json").is_file():
        raise ValueError("modelPath must be a local Transformers checkpoint directory with config.json and weights; a GGUF/catalog entry is not trainable")
    model_config = read_json(path / "config.json")
    if model_config.get("quantization_config") and config["precision"] == "bf16-lora":
        raise ValueError("BF16 LoRA requires unquantized source weights; selected source has quantization_config")
    if not isinstance(model_config.get("model_type"), str) or not model_config["model_type"]:
        raise ValueError("model config.json must declare model_type")
    if model_config.get("is_encoder_decoder") or model_config.get("vision_config") or model_config.get("audio_config"):
        raise ValueError("this worker supports text causal language models; encoder-decoder and multimodal sources need a separate qualified worker")
    weights = list(path.glob("*.safetensors")) + list(path.glob("pytorch_model*.bin"))
    if not weights or all(item.name.startswith("adapter_") for item in weights):
        raise ValueError("Transformers base checkpoint weights are missing; adapters and GGUF files alone are insufficient")
    for index_path in (path / "model.safetensors.index.json", path / "pytorch_model.bin.index.json"):
        if index_path.exists():
            mapping = read_json(index_path).get("weight_map")
            if not isinstance(mapping, dict) or not mapping or any(not isinstance(name, str) or not isinstance(shard, str) for name, shard in mapping.items()):
                raise ValueError(f"invalid weight_map in {index_path.name}")
            for shard in sorted(set(mapping.values())):
                target = (path / shard).resolve()
                if not is_within(target, path) or not target.is_file() or target.stat().st_size == 0:
                    raise ValueError(f"model snapshot has missing, empty or escaping indexed shard: {shard}")
    if any(item.stat().st_size == 0 for item in weights):
        raise ValueError("model snapshot contains an empty weight file")
    tokenizer_files = ("tokenizer.json", "tokenizer.model", "spiece.model", "vocab.txt")
    if not any((path / name).is_file() for name in tokenizer_files) and not all((path / name).is_file() for name in ("vocab.json", "merges.txt")):
        raise ValueError("local tokenizer artifacts are missing; offline training cannot download a tokenizer")
    return {"path": path, "config": model_config, "weights": weights, "weightBytes": sum(item.stat().st_size for item in weights)}


def model_identity(path, config, guard=None):
    snapshot = inspect_model_snapshot(path, config)
    path, model_config = snapshot["path"], snapshot["config"]
    files = []
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path)
        if not item.is_file() or any(part in (".git", ".cache", "__pycache__", "unsloth_compiled_cache") for part in relative.parts) or item.name.endswith((".lock", ".tmp")):
            continue
        files.append({"path": relative.as_posix(), "sha256": file_hash(item, guard), "bytes": item.stat().st_size})
    return {"path": str(path), "modelType": model_config.get("model_type"), "architectures": model_config.get("architectures", []), "files": files, "sha256": digest(files)}


def prepare_request(request, guard=None):
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    required = ("runId", "modelPath", "datasetManifest", "trainPath", "validationPath", "outputDir", "config")
    for key in required:
        if key not in request:
            raise ValueError(f"request.{key} is required")
    if not isinstance(request["runId"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", request["runId"]):
        raise ValueError("runId must be a stable 1-128 character identifier")
    config = validate_config(request["config"])
    paths = {}
    for key in required[1:6]:
        if not isinstance(request[key], str) or not request[key]:
            raise ValueError(f"request.{key} must be a nonempty local path")
        paths[key] = Path(request[key]).expanduser().resolve()
    output = paths["outputDir"]
    model_path = paths["modelPath"]
    if is_within(output, model_path) or is_within(model_path, output):
        raise ValueError("outputDir and base model directory must not overlap")
    if any(is_within(paths[key], output) for key in ("datasetManifest", "trainPath", "validationPath")):
        raise ValueError("outputDir must not contain the immutable input dataset")
    manifest = read_json(paths["datasetManifest"])
    datasets = {}
    rows = {}
    for split, key in (("train", "trainPath"), ("validation", "validationPath")):
        entry = manifest.get(split, manifest.get("files", {}).get(split, {}))
        if not isinstance(entry, dict):
            raise ValueError(f"dataset manifest {split} must be an object")
        expected = entry.get("sha256", manifest.get(split + "Sha256"))
        actual = file_hash(paths[key], guard)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected) or expected.lower() != actual:
            raise ValueError(f"dataset {split} hash mismatch: expected {expected}, observed {actual}")
        rows[split] = load_records(paths[key], config["method"])
        declared = entry.get("sourceIds", manifest.get(split + "SourceIds", []))
        if not isinstance(declared, list) or any(not isinstance(item, str) or not item for item in declared):
            raise ValueError(f"manifest {split}.sourceIds must contain nonempty source IDs")
        observed = {item for row in rows[split] for item in row["sourceIds"]}
        if declared and not observed.issubset(set(declared)):
            raise ValueError(f"dataset {split} source IDs absent from manifest")
        if not declared and any(not row["sourceIds"] for row in rows[split]):
            raise ValueError(f"dataset {split} is missing source group provenance")
        groups = sorted(set(declared) | observed)
        datasets[split] = {"path": str(paths[key]), "sha256": actual, "sourceIds": groups, "records": len(rows[split]), "normalizedSha256": digest([row["payloadSha256"] for row in rows[split]])}
    source_overlap = set(datasets["train"]["sourceIds"]) & set(datasets["validation"]["sourceIds"])
    if source_overlap:
        raise ValueError("train/validation source group overlap: " + ", ".join(sorted(source_overlap)[:10]))
    content_overlap = {row["payloadSha256"] for row in rows["train"]} & {row["payloadSha256"] for row in rows["validation"]}
    if content_overlap:
        raise ValueError("train/validation content overlap: " + ", ".join(sorted(content_overlap)[:10]))
    model = model_identity(model_path, config, guard)
    worker_files = {name: file_hash(Path(__file__).with_name(name), guard) for name in ("worker.py", "config.py", "requirements.json")}
    identity = {"schemaVersion": SCHEMA_VERSION, "runId": request["runId"], "outputDir": str(output), "config": config, "configSha256": digest(config), "model": model, "datasetManifest": {"path": str(paths["datasetManifest"]), "sha256": file_hash(paths["datasetManifest"], guard)}, "datasets": datasets, "worker": {"files": worker_files, "sha256": digest(worker_files)}, "plannedMaximumSteps": total_training_steps(len(rows["train"]), config)}
    return {**identity, "identitySha256": digest(identity), "counts": {key: len(value) for key, value in rows.items()}, "rows": rows}


def frozen_manifest(prepared):
    return {key: value for key, value in prepared.items() if key != "rows"}


def verify_resume_identity(frozen, current):
    keys = ("schemaVersion", "runId", "outputDir", "config", "configSha256", "model", "datasetManifest", "datasets", "worker", "plannedMaximumSteps")
    if frozen.get("identitySha256") != digest({key: frozen.get(key) for key in keys}):
        raise ValueError("frozen run manifest identity hash is invalid")
    if frozen.get("identitySha256") != current.get("identitySha256"):
        raise ValueError("resume identity mismatch: source weights, dataset bytes/provenance, run paths, configuration or worker source changed; start a separate run")


def total_training_steps(samples, config):
    if type(samples) is not int or samples <= 0:
        raise ValueError("training requires at least one usable sample")
    per_epoch = math.ceil(math.ceil(samples / config["batchSize"]) / config["gradientAccumulation"])
    return min(config["maxSteps"], max(1, math.ceil(per_epoch * config["epochs"])))


def next_chunk(previous_step, total_steps, interval):
    if any(type(value) is not int for value in (previous_step, total_steps, interval)) or previous_step < 0 or total_steps <= previous_step or interval <= 0:
        raise ValueError("chunk requires a saved step below totalSteps and a positive checkpoint interval")
    return {"startStep": previous_step, "endStep": min(previous_step + interval, total_steps), "totalSteps": total_steps}


def execution_phase(previous_step, total_steps, interval):
    if type(previous_step) is not int or previous_step < 0 or previous_step > total_steps:
        raise ValueError("saved optimizer step is outside the frozen total step schedule")
    if previous_step == total_steps:
        return {"phase": "evaluation", "startStep": previous_step, "endStep": total_steps, "totalSteps": total_steps}
    return {"phase": "training", **next_chunk(previous_step, total_steps, interval)}


def evaluate_gates(baseline, candidate, config, invalid_training=False):
    comparisons = []
    def gate(name, passed, observed, threshold, operator, **details):
        comparisons.append({"gate": name, "passed": bool(passed), "observed": json_safe(observed), "threshold": threshold, "operator": operator, **json_safe(details)})
    samples = min(baseline.get("samples", 0), candidate.get("samples", 0))
    gate("evaluation-samples", finite_number(samples) and samples >= config["minEvaluationSamples"], samples, config["minEvaluationSamples"], ">=")
    invalid = [f"{name}.{key}" for name, values in (("baseline", baseline), ("candidate", candidate)) for key, value in values.items() if not finite_number(value)]
    gate("finite-metrics", not invalid and not invalid_training, invalid + (["training.loss"] if invalid_training else []), "all finite", "finite")
    objective = "eval_loss" if config["method"] == "sft" else "preference_margin"
    direction = "lower" if config["method"] == "sft" else "higher"
    before, after = baseline.get(objective), candidate.get(objective)
    valid = finite_number(before) and finite_number(after)
    improvement = (before - after if direction == "lower" else after - before) if valid else None
    threshold = config["minimumImprovement"]
    reaches_threshold = valid and (improvement >= threshold or math.isclose(improvement, threshold, rel_tol=1e-12, abs_tol=0))
    gate("heldout-improvement", valid and improvement > 0 and reaches_threshold, improvement, threshold, ">0 and >=", metric=objective, direction=direction, baseline=before, candidate=after, relativeThresholdTolerance=1e-12)
    regressions = list(config["regressionGates"])
    if config["method"] == "dpo" and not any(item["metric"] == "chosen_nll" for item in regressions):
        regressions.append({"metric": "chosen_nll", "direction": "lower", "maximumRegression": config["maxRegression"]})
    for regression in regressions:
        name = regression["metric"]
        before, after = baseline.get(name), candidate.get(name)
        valid = finite_number(before) and finite_number(after)
        amount = ((after - before) if regression["direction"] == "lower" else (before - after)) if valid else None
        gate("regression:" + name, valid and amount <= regression["maximumRegression"], amount, regression["maximumRegression"], "<=", baseline=before, candidate=after, direction=regression["direction"])
    return {"status": "accepted" if all(item["passed"] for item in comparisons) else "rejected", "objective": objective, "comparisons": comparisons}


def validate_checkpoint(path, expected_step=None):
    path = Path(path).resolve()
    state = read_json(path / "trainer_state.json")
    step = state.get("global_step")
    if type(step) is not int or step <= 0 or (expected_step is not None and step != expected_step):
        raise ValueError(f"checkpoint global step mismatch: expected {expected_step}, observed {step}")
    for name in ("optimizer.pt", "scheduler.pt"):
        if not (path / name).is_file() or (path / name).stat().st_size == 0:
            raise ValueError(f"checkpoint lacks real {name} state: {path}")
    if not any(item.stat().st_size for item in path.glob("rng_state*.pth")):
        raise ValueError(f"checkpoint lacks RNG state: {path}")
    if not any((path / name).is_file() and (path / name).stat().st_size for name in ("adapter_model.safetensors", "adapter_model.bin", "model.safetensors", "pytorch_model.bin")):
        raise ValueError(f"checkpoint lacks model/adapter weights: {path}")
    return {"path": str(path), "step": step, "trainerStateSha256": file_hash(path / "trainer_state.json")}


class EventSink:
    def __init__(self, output, run_id, stream=None):
        self.output, self.run_id = Path(output), run_id
        self.output.mkdir(parents=True, exist_ok=True)
        self.stream = stream if stream is not None else sys.stdout

    def emit(self, event, step=0, metrics=None, checkpoint=None, status=None, **details):
        value = {"event": event, "timestamp": utc_now(), "runId": self.run_id, "step": int(step), "metrics": json_safe(metrics or {}), "checkpoint": str(checkpoint) if checkpoint else None, **json_safe(details)}
        if status:
            value["status"] = status
        append_json(self.output / "events.jsonl", value)
        self.stream.write(canonical(value).decode("utf-8") + "\n")
        self.stream.flush()
        return value


def seal_checkpoint(path, step, identity, invalid_training=False):
    path = Path(path)
    result = validate_checkpoint(path, step)
    files = [{"path": item.relative_to(path).as_posix(), "sha256": file_hash(item), "bytes": item.stat().st_size} for item in sorted(path.rglob("*")) if item.is_file() and item.name != "learning-checkpoint.json"]
    result.update({"identitySha256": identity, "files": files, "savedAt": utc_now(), "invalidTrainingMetrics": bool(invalid_training)})
    atomic_json(path / "learning-checkpoint.json", result)
    return result


def verify_checkpoint_seal(path, identity):
    path = Path(path).resolve()
    seal = read_json(path / "learning-checkpoint.json")
    if seal.get("identitySha256") != identity:
        raise ValueError("checkpoint source/data/config identity mismatch")
    validate_checkpoint(path, seal.get("step"))
    actual_names = {item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file() and item.name != "learning-checkpoint.json"}
    if actual_names != {item["path"] for item in seal.get("files", [])}:
        raise ValueError("checkpoint file hash manifest changed")
    for entry in seal["files"]:
        target = (path / entry["path"]).resolve()
        if not is_within(target, path) or file_hash(target) != entry["sha256"]:
            raise ValueError(f"checkpoint file hash mismatch: {entry['path']}")
    return seal


def directory_bytes(path):
    return sum(item.stat().st_size for item in Path(path).rglob("*") if item.is_file())


class BudgetExceeded(RuntimeError):
    pass


class RunCancelled(RuntimeError):
    pass


class RunBudget:
    def __init__(self, output, config, previous_active=0, started=None):
        self.output, self.config = Path(output), config
        if not finite_number(previous_active) or previous_active < 0:
            raise ValueError("receipt activeSeconds must be finite and nonnegative")
        self.started = time.monotonic() if started is None else started
        self.previous_active = previous_active
        self.cancelled = False

    @property
    def active_seconds(self):
        return self.previous_active + time.monotonic() - self.started

    def check(self, reserve_bytes=0):
        if self.cancelled or (self.output / "cancel.requested").exists():
            raise RunCancelled("owned training cancellation requested")
        if self.active_seconds >= self.config["maxMinutes"] * 60:
            raise BudgetExceeded(f"total active time budget exhausted: {self.active_seconds:.6f}s >= {self.config['maxMinutes'] * 60}s")
        used = directory_bytes(self.output) if self.output.exists() else 0
        if used + reserve_bytes > self.config["maxDiskBytes"]:
            raise BudgetExceeded(f"run disk budget exceeded: {used} bytes + {reserve_bytes} checkpoint reservation > {self.config['maxDiskBytes']}")
        if reserve_bytes:
            import shutil
            free = shutil.disk_usage(self.output).free
            if free < reserve_bytes:
                raise BudgetExceeded(f"filesystem free space {free} is below checkpoint reservation {reserve_bytes}")


def process_alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # os.kill(pid, 0) is unsafe on Windows. Query the process handle instead.
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Access denied is conservatively live.
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@contextmanager
def run_lock(output, run_id):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / ".worker.lock"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        old = read_json(path)
        if old.get("runId") != run_id or process_alive(old.get("pid")):
            raise ValueError("owned learning run is already running or lock has an unrelated owner")
        archived = output / (".worker.lock.interrupted-" + str(time.time_ns()))
        path.rename(archived)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"runId": run_id, "pid": os.getpid(), "createdAt": utc_now()}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        if path.exists() and read_json(path).get("pid") == os.getpid():
            path.unlink()


@contextmanager
def raw_logs(output):
    """Capture Python and native file-descriptor output; stdout is JSON protocol only."""
    sys.stdout.flush()
    sys.stderr.flush()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
    saved_out, saved_err = os.dup(1), os.dup(2)
    protocol = os.fdopen(os.dup(saved_out), "w", encoding="utf-8", buffering=1)
    log_out = os.open(Path(output) / "stdout.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    log_err = os.open(Path(output) / "stderr.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.dup2(log_out, 1)
        os.dup2(log_err, 2)
        yield protocol
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        for descriptor in (saved_out, saved_err, log_out, log_err):
            os.close(descriptor)
        protocol.close()


def encode(tokenizer, text):
    return list(tokenizer(text, add_special_tokens=False, truncation=False)["input_ids"])


def with_eos(tokens, tokenizer):
    eos = tokenizer.eos_token_id
    return tokens + ([eos] if eos is not None and (not tokens or tokens[-1] != eos) else [])


def tokenize_records(rows, tokenizer, max_length, method, guard=None):
    usable, exclusions = [], []
    for row in rows:
        if guard:
            guard()
        payload = row["payload"]
        if method == "dpo":
            prompt = encode(tokenizer, payload["prompt"])
            # TRL 0.23/0.24 tokenize_row appends EOS unconditionally. Match the
            # actual trainer, including an already supplied EOS in raw text.
            chosen = encode(tokenizer, payload["chosen"]) + [tokenizer.eos_token_id]
            rejected = encode(tokenizer, payload["rejected"]) + [tokenizer.eos_token_id]
            lengths = [len(prompt) + len(chosen), len(prompt) + len(rejected)]
            item = {"prompt_input_ids": prompt, "chosen_input_ids": chosen, "rejected_input_ids": rejected, "payload": payload, "row": row}
            target_count = min(len(chosen), len(rejected))
        else:
            if "messages" in payload:
                messages = payload["messages"]
                full = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
                prefix = tokenizer.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True)
                tokens, prompt_tokens = encode(tokenizer, full), encode(tokenizer, prefix)
                if tokens[:len(prompt_tokens)] != prompt_tokens:
                    raise ValueError(f"chat template prefix is not stable for record {row['line']}; cannot safely mask the final assistant answer")
                prompt_count = len(prompt_tokens)
            elif "text" in payload:
                tokens, prompt_count = encode(tokenizer, payload["text"]), 0
            else:
                prompt_tokens = encode(tokenizer, payload["prompt"])
                tokens = encode(tokenizer, payload["prompt"] + payload["completion"])
                if tokens[:len(prompt_tokens)] != prompt_tokens:
                    raise ValueError("SFT prompt token prefix changes when joined to completion; use a native chat template or an explicit stable text boundary")
                prompt_count = len(prompt_tokens)
            tokens = with_eos(tokens, tokenizer)
            labels = [-100] * prompt_count + tokens[prompt_count:]
            lengths, target_count = [len(tokens)], sum(value != -100 for value in labels[1:])
            item = {"input_ids": tokens, "attention_mask": [1] * len(tokens), "labels": labels, "row": row}
        if max(lengths) > max_length or target_count < 1:
            exclusions.append({"line": row["line"], "sourceIds": row["sourceIds"], "payloadSha256": row["payloadSha256"], "reason": "sequence-too-long" if max(lengths) > max_length else "no-supervised-tokens", "tokenLengths": lengths, "maxSeqLength": max_length})
        else:
            usable.append(item)
    return usable, exclusions


def verify_trainer_dataset(dataset, frozen_records, method, guard=None):
    """Reject trainer preparation/masking changes before the first update."""
    if len(dataset) != len(frozen_records):
        raise ValueError("trainer token identity differs: prepared sample count changed")
    keys = ("prompt_input_ids", "chosen_input_ids", "rejected_input_ids") if method == "dpo" else ("input_ids", "attention_mask", "labels")
    for index, expected in enumerate(frozen_records):
        if guard:
            guard()
        actual = dataset[index]
        for key in keys:
            if key not in actual or list(actual[key]) != expected[key]:
                raise ValueError(f"trainer token identity differs at sample {index}, {key}; truncation, retokenization or supervision drift is forbidden")


def package_versions():
    versions = {}
    for name in (*DEPENDENCIES, "bitsandbytes", "torchvision", "xformers", "triton-windows", "triton", "torchao", "numpy", "safetensors", "tokenizers", "huggingface-hub"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def required_package_versions(platform=None):
    """Read this worker's exact direct pins without importing training libraries."""
    requirements = read_json(Path(__file__).with_name("requirements.json"))
    platform = sys.platform if platform is None else platform
    pins = {}
    for entry in [requirements["torch"]["requirement"], *requirements["torch"].get("companions", []), *requirements["packages"]]:
        requirement, separator, marker = entry.partition(";")
        if separator:
            # The owned manifest uses only these explicit platform equalities.
            # Refuse a new unsupported marker rather than silently ignoring it.
            match = re.fullmatch(r"sys_platform\s*==\s*(['\"])([^'\"]+)\1", marker.strip())
            if not match:
                raise ValueError(f"unsupported dependency platform marker: {marker}")
            if platform != match[2]:
                continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;]+)", requirement.strip())
        if not match or "*" in match[2]:
            raise ValueError(f"dependency must have one exact version pin: {entry}")
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        version = match[2]
        if name in pins and pins[name] != version:
            raise ValueError(f"conflicting dependency pins for {name}")
        pins[name] = version
    return pins


def package_pin_blockers(versions, required=None):
    required = required_package_versions() if required is None else required
    blockers = []
    for name, expected in required.items():
        actual = versions.get(name)
        if actual is None:
            blockers.append(f"missing required package: {name}")
        elif actual != expected:
            blockers.append(f"package version mismatch: {name} expected {expected}, observed {actual}")
    return blockers


def configure_training_cache(output):
    # Keep generated training caches in the budgeted owned run. This mutates
    # only this worker process's environment, never the user's environment.
    cache = Path(output) / "runtime-cache"
    variables = {"TRITON_CACHE_DIR": "triton", "TORCHINDUCTOR_CACHE_DIR": "inductor", "TORCH_EXTENSIONS_DIR": "extensions", "HF_DATASETS_CACHE": "datasets", "XDG_CACHE_HOME": "xdg", "HF_HOME": "huggingface"}
    for key, child in variables.items():
        path = cache / child
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)


def probe():
    versions = package_versions()
    required = required_package_versions()
    dependency_blockers = package_pin_blockers(versions, required)
    blockers = list(dependency_blockers)
    hardware = {"cudaAvailable": False, "bf16Supported": False, "devices": []}
    try:
        import torch
        hardware.update({"cudaAvailable": torch.cuda.is_available(), "cudaVersion": torch.version.cuda})
        if hardware["cudaAvailable"]:
            # Some Torch releases default to software-emulated BF16. The
            # product promises native BF16 and binds training to logical GPU0.
            capability = torch.cuda.get_device_capability(0)
            hardware["bf16Supported"] = capability[0] >= 8 and torch.cuda.is_bf16_supported()
            for index in range(torch.cuda.device_count()):
                free, total = torch.cuda.mem_get_info(index)
                hardware["devices"].append({"index": index, "name": torch.cuda.get_device_name(index), "totalBytes": total, "freeBytes": free, "computeCapability": list(torch.cuda.get_device_capability(index))})
        else:
            blockers.append("CUDA is unavailable; this worker requires an NVIDIA CUDA device")
        if not hardware["bf16Supported"]:
            blockers.append("native BF16 compute is unavailable; the worker will not silently switch to FP16 or quantization")
    except Exception as error:
        blockers.append(f"torch import/hardware probe failed: {type(error).__name__}: {error}")
        hardware["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
    core_import = {"status": "not-run"}
    if not blockers:
        environment = os.environ.copy()
        environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"})
        program = "from unsloth import FastLanguageModel\nfrom trl import SFTConfig, SFTTrainer, DPOConfig, DPOTrainer\nfrom transformers import TrainerCallback\nprint('OPENCORE_LEARNING_CORE_IMPORT_OK')"
        try:
            child = subprocess.run([sys.executable, "-c", program], capture_output=True, encoding="utf-8", errors="replace", timeout=45, env=environment, check=False)
            core_import = {"status": "ready" if child.returncode == 0 else "failed", "exitCode": child.returncode, "stdout": child.stdout, "stderr": child.stderr}
            if child.returncode:
                detail = child.stderr.strip().splitlines()[-1] if child.stderr.strip() else f"exit code {child.returncode}"
                blockers.append("Unsloth/TRL core import failed: " + detail)
        except (OSError, subprocess.TimeoutExpired) as error:
            core_import = {"status": "failed", "error": {"type": type(error).__name__, "message": str(error)}}
            blockers.append(f"Unsloth/TRL core import failed: {type(error).__name__}: {error}")
    return {"schemaVersion": SCHEMA_VERSION, "event": "probe", "timestamp": utc_now(), "status": "blocked" if blockers else "ready", "ready": not blockers, "trainingReady": not blockers, "python": sys.executable, "pythonVersion": sys.version, "platform": sys.platform, "packages": versions, "requiredPackages": required, "packagePinsMatched": not dependency_blockers, "hardware": hardware, "coreImport": core_import, "blockers": blockers, "capabilities": {"methods": ["sft", "dpo"], "precisions": ["bf16-lora", "qlora-4bit"], "qloraDependenciesPresent": versions["bitsandbytes"] is not None}, "qualification": "exact direct package pins including CUDA build suffixes, Torch CUDA/BF16, and real Unsloth/TRL imports; selected architecture and optimizer updates require an actual training run", "library": {"name": "unsloth", "license": "Apache-2.0 core library", "studioUiIncluded": False}}


def setup_spec(root):
    requirements = read_json(Path(__file__).with_name("requirements.json"))
    root = Path(root).resolve()
    environment = root / "venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return {"root": str(root), "environment": str(environment), "python": str(python), "packages": requirements["packages"], "torch": requirements["torch"], "requirementsSha256": file_hash(Path(__file__).with_name("requirements.json")), "license": "Apache-2.0 core library"}


def setup(root, output):
    """Only invoked after an explicit native setup/train action; no shell execution."""
    spec = setup_spec(root)
    if not (3, 11) <= sys.version_info[:2] < (3, 13):
        raise ValueError("supported isolated setup requires an existing Python 3.11 or 3.12 interpreter")
    root = Path(spec["root"])
    root.mkdir(parents=True, exist_ok=True)
    receipt = {"schemaVersion": SCHEMA_VERSION, "status": "setting-up", "startedAt": utc_now(), **spec}
    marker = root / "environment-owner.json"
    if marker.exists():
        if read_json(marker).get("owner") != "opencore-learning":
            raise ValueError("setup root belongs to an unrelated environment")
    elif any(root.iterdir()):
        raise ValueError("setup requires an empty dedicated environment root")
    python = Path(spec["python"])
    probe_path = root / "probe.json"
    commands = []
    if not python.exists():
        commands.append([sys.executable, "-m", "venv", spec["environment"]])
    cuda_packages = [spec["torch"]["requirement"], *spec["torch"].get("companions", [])]
    commands.extend([[str(python), "-m", "pip", "install", "--disable-pip-version-check", "--index-url", spec["torch"]["indexUrl"], *cuda_packages], [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--extra-index-url", spec["torch"]["indexUrl"], *cuda_packages, *spec["packages"]], [str(python), "-m", "pip", "check"], [str(python), str(Path(__file__).resolve()), "probe", "--output", str(probe_path)]])
    with run_lock(root, "environment-setup"):
        # Do not overwrite another invocation's durable setup progress before
        # acquiring the exclusive owner lock.
        atomic_json(marker, {"owner": "opencore-learning", "createdAt": utc_now(), "spec": spec})
        atomic_json(output, receipt)
        try:
            if python.exists():
                with (root / "setup.log").open("ab") as log:
                    existing = subprocess.run(commands[-1], stdout=log, stderr=subprocess.STDOUT, timeout=120, check=False)
                if existing.returncode == 0:
                    existing_probe = read_json(probe_path)
                    reuse_blockers = package_pin_blockers(existing_probe.get("packages", {}))
                    if existing_probe.get("ready") and existing_probe.get("trainingReady") and not reuse_blockers:
                        receipt.update({"status": "ready", "reused": True, "probe": existing_probe, "finishedAt": utc_now()})
                        atomic_json(output, receipt)
                        return 0
                    receipt["reuseBlockers"] = reuse_blockers or ["existing environment probe is not training ready"]
            for command in commands:
                receipt["currentCommand"] = command
                atomic_json(output, receipt)
                print(canonical({"event": "setup-progress", "timestamp": utc_now(), "status": "setting-up", "command": command}).decode(), flush=True)
                with (root / "setup.log").open("ab") as log:
                    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1800, check=False)
                if result.returncode:
                    raise RuntimeError(f"isolated setup command exited {result.returncode}; full output: {root / 'setup.log'}")
            receipt.update({"status": "ready", "reused": False, "probe": read_json(probe_path), "finishedAt": utc_now()})
            atomic_json(output, receipt)
            return 0
        except Exception as error:
            receipt.update({"status": "failed", "finishedAt": utc_now(), "error": {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}, "logPath": str(root / "setup.log")})
            atomic_json(output, receipt)
            print(canonical({"event": "setup-failed", "timestamp": utc_now(), "status": "failed", "error": receipt["error"]}).decode(), flush=True)
            return 1


def collate_causal(features, tokenizer, torch):
    longest = max(len(item["input_ids"]) for item in features)
    pad = tokenizer.pad_token_id
    if pad is None:
        pad = tokenizer.eos_token_id
    if pad is None:
        raise ValueError("tokenizer has neither a padding token nor an EOS token")
    return {
        "input_ids": torch.tensor([item["input_ids"] + [pad] * (longest - len(item["input_ids"])) for item in features], dtype=torch.long),
        "attention_mask": torch.tensor([[1] * len(item["input_ids"]) + [0] * (longest - len(item["input_ids"])) for item in features], dtype=torch.long),
        "labels": torch.tensor([item["labels"] + [-100] * (longest - len(item["labels"])) for item in features], dtype=torch.long),
    }


def preference_observation(chosen_nll, chosen_tokens, rejected_nll, rejected_tokens):
    """Sigmoid DPO scores summed completion log probabilities, not mean NLL."""
    chosen_logp, rejected_logp = -chosen_nll * chosen_tokens, -rejected_nll * rejected_tokens
    return {"chosen_nll": chosen_nll, "rejected_nll": rejected_nll, "chosen_logp": chosen_logp, "rejected_logp": rejected_logp, "preference_margin": chosen_logp - rejected_logp, "tokens": chosen_tokens + rejected_tokens}


def evaluate_model(model, tokenizer, records, method, budget, sink, output, name, torch, invocation):
    """Deterministic teacher-forced heldout NLL; no generated text self-grading."""
    model.eval()
    device = next(model.parameters()).device
    total_loss, total_tokens, margins, chosen_losses, rejected_losses = 0.0, 0, [], [], []
    observations = Path(output) / (name + "-examples.jsonl")

    def measure(tokens, labels):
        count = sum(item != -100 for item in labels[1:])
        if count <= 0:
            raise ValueError("held-out sample has no supervised next-token targets")
        batch = collate_causal([{"input_ids": tokens, "labels": labels}], tokenizer, torch)
        batch = {key: value.to(device) for key, value in batch.items()}
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
            result = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"], use_cache=False)
            # Model-provided losses can include MoE/router auxiliary terms.
            # Score only exact supervised next-token probabilities for both
            # held-out SFT NLL and the summed completion log-probs used by DPO.
            logits = result.logits[..., :-1, :].float()
            labels = batch["labels"][..., 1:]
            nll = torch.nn.functional.cross_entropy(logits.reshape(-1, logits.shape[-1]), labels.reshape(-1), ignore_index=-100, reduction="mean")
        value = float(nll.detach().float().cpu().item())
        del result, batch, logits, labels, nll
        return value, count

    for index, item in enumerate(records):
        budget.check()
        if method == "dpo":
            prompt = item["prompt_input_ids"]
            chosen, rejected = item["chosen_input_ids"], item["rejected_input_ids"]
            positive, positive_count = measure(prompt + chosen, [-100] * len(prompt) + chosen)
            negative, negative_count = measure(prompt + rejected, [-100] * len(prompt) + rejected)
            chosen_losses.append(positive)
            rejected_losses.append(negative)
            metrics = preference_observation(positive, positive_count, negative, negative_count)
            margins.append(metrics["preference_margin"])
            total_tokens += positive_count + negative_count
        else:
            loss, count = measure(item["input_ids"], item["labels"])
            total_loss += loss * count
            total_tokens += count
            metrics = {"eval_loss": loss, "tokens": count}
        append_json(observations, {"timestamp": utc_now(), "invocationId": invocation, "sample": index, "line": item["row"]["line"], "payloadSha256": item["row"]["payloadSha256"], "sourceIds": item["row"]["sourceIds"], "provenance": item["row"]["provenance"], "metrics": metrics})
    if method == "dpo":
        count = len(margins)
        result = {"samples": count, "tokens": total_tokens, "chosen_nll": sum(chosen_losses) / count, "rejected_nll": sum(rejected_losses) / count, "preference_margin": sum(margins) / count, "preference_accuracy": sum(value > 0 for value in margins) / count}
    else:
        result = {"samples": len(records), "tokens": total_tokens, "eval_loss": total_loss / total_tokens}
    sink.emit(name + "-evaluation", metrics=result, invocationId=invocation, observationsPath=str(observations))
    return result


def load_baseline(path, identity, tokenized_identity, expected_hash):
    if not isinstance(expected_hash, str) or file_hash(Path(path)) != expected_hash:
        raise ValueError("baseline file hash changed across optimizer checkpoint resume")
    record = read_json(path)
    if record.get("identitySha256") != identity or record.get("tokenizedSha256") != tokenized_identity:
        raise ValueError("baseline source/data/tokenizer identity is stale")
    return record["metrics"]


def training_arguments(cls, config, total_steps, output, method):
    # max_steps is the frozen whole-run schedule. The callback stops a chunk;
    # changing max_steps for every process would reset the LR schedule on resume.
    arguments = {
        "output_dir": str(output), "num_train_epochs": config["epochs"], "max_steps": total_steps,
        "per_device_train_batch_size": config["batchSize"], "gradient_accumulation_steps": config["gradientAccumulation"],
        "learning_rate": config["learningRate"], "optim": config["optimizer"], "lr_scheduler_type": "linear",
        "warmup_steps": 0, "seed": config["seed"], "data_seed": config["seed"],
        "bf16": True, "fp16": False, "gradient_checkpointing": True,
        "logging_strategy": "steps", "logging_steps": 1, "logging_nan_inf_filter": False,
        "save_strategy": "steps", "save_steps": config["checkpointEvery"], "save_total_limit": None,
        "save_only_model": False, "save_safetensors": True, "report_to": [], "push_to_hub": False,
        "dataloader_num_workers": 0, "dataloader_pin_memory": False, "remove_unused_columns": True,
        "ignore_data_skip": False, "load_best_model_at_end": False,
    }
    parameters = inspect.signature(cls).parameters
    if "eval_strategy" in parameters:
        arguments["eval_strategy"] = "no"
    elif "evaluation_strategy" in parameters:
        arguments["evaluation_strategy"] = "no"
    if method == "sft":
        arguments.update({"packing": False, "dataset_kwargs": {"skip_prepare_dataset": True}})
        if "max_length" in parameters:
            arguments["max_length"] = config["maxSeqLength"]
        elif "max_seq_length" in parameters:
            arguments["max_seq_length"] = config["maxSeqLength"]
        else:
            raise RuntimeError("installed SFTConfig cannot enforce maxSeqLength")
    else:
        arguments.update({"beta": config["dpoBeta"], "max_length": config["maxSeqLength"], "max_prompt_length": None, "max_completion_length": None, "precompute_ref_log_probs": False, "loss_type": "sigmoid", "disable_dropout": False})
    missing = sorted(set(arguments) - set(parameters))
    if missing and not any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
        raise RuntimeError("installed trainer config lacks required arguments: " + ", ".join(missing))
    return cls(**arguments)


def verify_loaded_precision(model, torch, precision):
    quantized = bool(getattr(model, "is_loaded_in_4bit", False))
    if precision == "bf16-lora" and quantized:
        raise RuntimeError("precision violation: quantized weights loaded for requested BF16 LoRA")
    if precision == "qlora-4bit" and not quantized:
        raise RuntimeError("precision violation: requested QLoRA source was not loaded in 4-bit")
    half = [name for name, parameter in model.named_parameters() if parameter.dtype == torch.float16]
    if half:
        raise RuntimeError("precision violation: FP16 tensors found for requested BF16 compute: " + ", ".join(half[:10]))
    if precision == "bf16-lora":
        reduced = [name for name, parameter in model.named_parameters() if parameter.dtype not in (torch.bfloat16, torch.float32)]
        if reduced:
            raise RuntimeError("precision violation: reduced/integer base tensors found for requested BF16 LoRA: " + ", ".join(reduced[:10]))
    modules = []
    for name, item in model.named_modules():
        if type(item).__name__ == "Linear4bit":
            if getattr(item, "compute_dtype", None) != torch.bfloat16:
                raise RuntimeError(f"precision violation: QLoRA module {name} does not use requested BF16 compute")
            state = getattr(getattr(item, "weight", None), "quant_state", None)
            modules.append({"name": name, "computeDtype": str(item.compute_dtype), "quantizationType": getattr(state, "quant_type", None)})
    if precision == "qlora-4bit" and not modules:
        raise RuntimeError("precision violation: no actual bitsandbytes Linear4bit modules found for requested QLoRA")
    return modules


def load_training_model(prepared, runtime, sink, budget):
    # Unsloth must be imported before Transformers/TRL to apply its own patches.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["WANDB_DISABLED"] = "true"
    # TRL's SFT entropy/accuracy and DPO log-probability computations consume
    # logits. Unsloth's default loss-only return cannot satisfy those contracts.
    os.environ["UNSLOTH_RETURN_LOGITS"] = "1"
    os.environ["UNSLOTH_COMPILE_LOCATION"] = str(Path(prepared["outputDir"]) / "unsloth_compiled_cache")
    from unsloth import FastLanguageModel
    import torch
    config = prepared["config"]
    if config["precision"] == "qlora-4bit" and runtime["packages"]["bitsandbytes"] is None:
        raise RuntimeError("explicit QLoRA requires bitsandbytes; install the supported isolated environment")
    budget.check()
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=prepared["model"]["path"], max_seq_length=config["maxSeqLength"],
        dtype=torch.bfloat16, load_in_4bit=config["precision"] == "qlora-4bit",
        load_in_8bit=False, full_finetuning=False, trust_remote_code=False,
        local_files_only=True, use_exact_model_name=True, device_map={"": 0},
    )
    if hasattr(tokenizer, "tokenizer"):
        tokenizer = tokenizer.tokenizer
    if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
        tokenizer.pad_token = tokenizer.eos_token
    if tokenizer.eos_token_id is None:
        raise ValueError("selected tokenizer has no EOS token; unsupported training source")
    tokenizer.padding_side = "right"
    modules = {name.rsplit(".", 1)[-1] for name, _ in model.named_modules()}
    targets = [name for name in ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj") if name in modules]
    if not targets:
        raise ValueError("selected architecture has no supported LoRA projection targets")
    model = FastLanguageModel.get_peft_model(model, r=config["loraRank"], target_modules=targets,
        lora_alpha=config["loraAlpha"], lora_dropout=config["loraDropout"], bias="none",
        use_gradient_checkpointing="unsloth", random_state=config["seed"], use_rslora=False,
    )
    quantization_modules = verify_loaded_precision(model, torch, config["precision"])
    quantized = bool(getattr(model, "is_loaded_in_4bit", False))
    dtypes = {}
    trainable = 0
    for _, parameter in model.named_parameters():
        name = str(parameter.dtype)
        dtypes[name] = dtypes.get(name, 0) + parameter.numel()
        if parameter.requires_grad:
            trainable += parameter.numel()
    if trainable == 0:
        raise RuntimeError("Unsloth created no trainable LoRA parameters")
    model.config.use_cache = False
    sink.emit("model-loaded", metrics={"trainableParameters": trainable}, precision=config["precision"], tensorDtypes=dtypes, targetModules=targets, quantized=quantized, quantizationModules=quantization_modules, returnLogits=True)
    budget.check()
    return model, tokenizer, torch, FastLanguageModel, trainable


def checkpoint_callback(base_class, chunk, prepared, budget, sink, receipt, invocation, reservation):
    class Boundary(base_class):
        invalid_training = bool(receipt.get("invalidTrainingMetrics", False))
        stop_reason = None
        checkpoint = None

        def on_train_begin(self, args, state, control, **kwargs):
            if state.global_step != chunk["startStep"] or state.max_steps != chunk["totalSteps"]:
                raise ValueError(f"trainer resume schedule mismatch: state step {state.global_step}, total {state.max_steps}, expected {chunk}")
            budget.check(reservation)
            sink.emit("training-started", state.global_step, invocationId=invocation, chunk=chunk)

        def on_step_begin(self, args, state, control, **kwargs):
            budget.check(reservation)

        def on_log(self, args, state, control, logs=None, **kwargs):
            sink.emit("log", state.global_step, logs or {}, invocationId=invocation)
            if any(isinstance(value, (float, int)) and not finite_number(value) for value in (logs or {}).values()):
                self.invalid_training = True
                self.stop_reason = "non-finite-training-metric"
                control.should_save = True
                control.should_training_stop = True
            return control

        def on_step_end(self, args, state, control, **kwargs):
            try:
                budget.check(reservation)
            except (BudgetExceeded, RunCancelled) as error:
                self.stop_reason = str(error)
                self.cancelled = isinstance(error, RunCancelled)
            if state.global_step >= chunk["endStep"] or self.stop_reason:
                control.should_save = True
                control.should_training_stop = True
            return control

        def on_save(self, args, state, control, **kwargs):
            path = Path(args.output_dir) / ("checkpoint-" + str(state.global_step))
            self.invalid_training = self.invalid_training or bool(receipt.get("invalidTrainingMetrics", False))
            self.checkpoint = seal_checkpoint(path, state.global_step, prepared["identitySha256"], invalid_training=self.invalid_training)
            receipt.update({"lastStep": state.global_step, "checkpoint": str(path), "checkpointSeal": self.checkpoint, "invalidTrainingMetrics": self.invalid_training, "activeSeconds": budget.active_seconds, "updatedAt": utc_now()})
            atomic_json(Path(prepared["outputDir"]) / "receipt.json", receipt)
            sink.emit("checkpoint-saved", state.global_step, checkpoint=path, invocationId=invocation, sealPath=str(path / "learning-checkpoint.json"))
    return Boundary()


def train_optimizer_chunk(model, tokenizer, torch, fast_model, train_records, chunk, prepared, receipt, budget, sink, invocation, resume_path, trainable):
    output, config = Path(prepared["outputDir"]), prepared["config"]
    # Adapter, FP32 Adam states, RNG state and candidate export reservation.
    reservation = trainable * 24 + 64 * 1024**2
    budget.check(reservation)
    from transformers import TrainerCallback
    from datasets import Dataset
    callback = checkpoint_callback(TrainerCallback, chunk, prepared, budget, sink, receipt, invocation, reservation)
    if config["method"] == "sft":
        from trl import SFTConfig, SFTTrainer
        arguments = training_arguments(SFTConfig, config, chunk["totalSteps"], output, "sft")
        dataset = Dataset.from_list([{key: value for key, value in item.items() if key != "row"} for item in train_records])
        trainer_class, extra = SFTTrainer, {"data_collator": lambda features: collate_causal(features, tokenizer, torch)}
    else:
        from unsloth import PatchDPOTrainer
        PatchDPOTrainer()
        from trl import DPOConfig, DPOTrainer
        arguments = training_arguments(DPOConfig, config, chunk["totalSteps"], output, "dpo")
        dataset = Dataset.from_list([item["payload"] for item in train_records])
        trainer_class, extra = DPOTrainer, {"ref_model": None}
    parameters = inspect.signature(trainer_class).parameters
    tokenizer_key = "processing_class" if "processing_class" in parameters else "tokenizer"
    fast_model.for_training(model)
    trainer = trainer_class(model=model, args=arguments, train_dataset=dataset, callbacks=[callback], **{tokenizer_key: tokenizer}, **extra)
    verify_trainer_dataset(trainer.train_dataset, train_records, config["method"], budget.check)
    sink.emit("trainer-data-verified", chunk["startStep"], metrics={"samples": len(train_records)}, method=config["method"], truncationAllowed=False, invocationId=invocation)
    atomic_json(output / "trainer-arguments.json", arguments.to_dict())
    receipt["status"] = "running"
    atomic_json(output / "receipt.json", receipt)
    trainer.train(resume_from_checkpoint=str(resume_path) if resume_path else None)
    actual_step = int(trainer.state.global_step)
    checkpoint = output / ("checkpoint-" + str(actual_step))
    if not callback.checkpoint or callback.checkpoint["step"] != actual_step:
        raise RuntimeError("trainer returned without saving the actual optimizer/scheduler/RNG checkpoint")
    verify_checkpoint_seal(checkpoint, prepared["identitySha256"])
    receipt.update({"lastStep": actual_step, "checkpoint": str(checkpoint), "invalidTrainingMetrics": callback.invalid_training})
    if callback.stop_reason and not callback.invalid_training:
        if getattr(callback, "cancelled", False):
            raise RunCancelled(callback.stop_reason)
        raise BudgetExceeded(callback.stop_reason)
    return actual_step, callback.invalid_training


def execute_training(prepared, request, runtime, sink, receipt, budget, invocation):
    output = Path(prepared["outputDir"])
    config = prepared["config"]
    previous = 0
    resume_path = request.get("resumeCheckpoint")
    if resume_path:
        resume_path = Path(resume_path).resolve()
        if not is_within(resume_path, output) or resume_path.parent != output:
            raise ValueError("resumeCheckpoint must be a direct checkpoint directory of this owned run")
        seal = verify_checkpoint_seal(resume_path, prepared["identitySha256"])
        receipt["invalidTrainingMetrics"] = bool(receipt.get("invalidTrainingMetrics", False)) or bool(seal.get("invalidTrainingMetrics", False))
        previous = seal["step"]
        saved_steps = [read_json(path).get("step", 0) for path in output.glob("checkpoint-*/learning-checkpoint.json")]
        if saved_steps and previous != max(saved_steps):
            raise ValueError("resumeCheckpoint is stale; resume the latest sealed optimizer checkpoint")
        if receipt.get("lastStep", 0) > previous:
            raise ValueError("resumeCheckpoint would duplicate already saved optimizer steps")
    elif any(output.glob("checkpoint-*/trainer_state.json")):
        raise ValueError("an optimizer checkpoint already exists; explicit resumeCheckpoint is required")
    model, tokenizer, torch, fast_model, trainable = load_training_model(prepared, runtime, sink, budget)
    train_records, train_exclusions = tokenize_records(prepared["rows"]["train"], tokenizer, config["maxSeqLength"], config["method"], budget.check)
    evaluation_records, eval_exclusions = tokenize_records(prepared["rows"]["validation"], tokenizer, config["maxSeqLength"], config["method"], budget.check)
    tokenized_identity = {"train": digest([{key: value for key, value in row.items() if key != "row"} for row in train_records]), "validation": digest([{key: value for key, value in row.items() if key != "row"} for row in evaluation_records]), "trainExclusions": train_exclusions, "validationExclusions": eval_exclusions, "supervision": "text all tokens; prompt/completion response only; messages final assistant answer only; DPO response only", "truncationAllowed": False}
    tokenized_hash = digest(tokenized_identity)
    data_path = output / "tokenized-manifest.json"
    if data_path.exists() and read_json(data_path).get("sha256") != tokenized_hash:
        raise ValueError("tokenizer/data identity changed across checkpoint resume")
    atomic_json(data_path, {"sha256": tokenized_hash, **tokenized_identity, "counts": {"train": len(train_records), "validation": len(evaluation_records)}})
    receipt["usableCounts"] = {"train": len(train_records), "validation": len(evaluation_records)}
    receipt["tokenizationManifest"] = str(data_path)
    sink.emit("data-tokenized", previous, metrics=receipt["usableCounts"], exclusionCounts={"train": len(train_exclusions), "validation": len(eval_exclusions)}, tokenizationManifest=str(data_path))
    if not train_records:
        raise ValueError("no usable training samples remain after exact token-length validation")
    if len(evaluation_records) < config["minEvaluationSamples"]:
        gate = {"status": "rejected", "comparisons": [{"gate": "evaluation-samples", "passed": False, "observed": len(evaluation_records), "threshold": config["minEvaluationSamples"], "operator": ">="}]}
        receipt.update({"status": "rejected", "gates": gate, "reason": "insufficient held-out evaluation; no optimizer updates performed"})
        return
    total = total_training_steps(len(train_records), config)
    chunk = execution_phase(previous, total, config["checkpointEvery"])
    receipt.update({"chunk": chunk, "totalSteps": total})
    baseline_path = output / "baseline.json"
    if previous:
        baseline = load_baseline(baseline_path, prepared["identitySha256"], tokenized_hash, receipt.get("baselineSha256"))
    else:
        with model.disable_adapter():
            baseline = evaluate_model(model, tokenizer, evaluation_records, config["method"], budget, sink, output, "baseline", torch, invocation)
        atomic_json(baseline_path, {"identitySha256": prepared["identitySha256"], "tokenizedSha256": tokenized_hash, "measuredAt": utc_now(), "metrics": baseline, "invocationId": invocation})
        receipt["baselineSha256"] = file_hash(baseline_path)
    receipt["baseline"] = json_safe(baseline)
    if any(not finite_number(value) for value in baseline.values()):
        receipt.update({"status": "rejected", "gates": evaluate_gates(baseline, {}, config), "reason": "non-finite baseline metrics; no optimizer updates performed"})
        return
    if chunk["phase"] == "evaluation":
        # The optimizer already finished before an interruption. Load its actual
        # saved adapter and evaluate it without executing another training step.
        loaded = model.load_adapter(str(resume_path), adapter_name="default", is_trainable=True)
        if getattr(loaded, "missing_keys", []) or getattr(loaded, "unexpected_keys", []):
            raise ValueError(f"saved optimizer checkpoint adapter keys do not match the frozen model: {loaded}")
        model.set_adapter("default")
        actual_step, invalid_training = previous, receipt.get("invalidTrainingMetrics", False)
        receipt.update({"lastStep": previous, "checkpoint": str(resume_path)})
        sink.emit("evaluation-resumed", previous, checkpoint=resume_path, invocationId=invocation)
    else:
        actual_step, invalid_training = train_optimizer_chunk(model, tokenizer, torch, fast_model, train_records, chunk, prepared, receipt, budget, sink, invocation, resume_path, trainable)
    if actual_step < total and not invalid_training:
        receipt["status"] = "checkpoint-ready"
        receipt["reviewRequired"] = True
        return
    budget.check()
    candidate = evaluate_model(model, tokenizer, evaluation_records, config["method"], budget, sink, output, "candidate", torch, invocation)
    gates = evaluate_gates(baseline, candidate, config, invalid_training)
    candidate_dir = output / "candidate"
    if candidate_dir.exists():
        candidate_dir = output / ("candidate-" + invocation)
    budget.check(trainable * 8 + 32 * 1024**2)
    candidate_dir.mkdir()
    model.save_pretrained(str(candidate_dir), safe_serialization=True)
    tokenizer.save_pretrained(str(candidate_dir))
    candidate_files = [{"path": item.relative_to(candidate_dir).as_posix(), "sha256": file_hash(item), "bytes": item.stat().st_size} for item in sorted(candidate_dir.rglob("*")) if item.is_file()]
    artifact = {"artifactType": "peft-lora-adapter", "path": str(candidate_dir), "baseModelPath": prepared["model"]["path"], "baseModelSha256": prepared["model"]["sha256"], "sourceManifest": str(output / "run-manifest.json"), "precision": config["precision"], "files": candidate_files, "sha256": digest(candidate_files), "createdAt": utc_now(), "evaluationScope": "configured disjoint local held-out teacher-forced objective only; no general intelligence claim", "gates": gates}
    atomic_json(candidate_dir / "learning-artifact.json", artifact)
    receipt.update({"status": gates["status"], "candidate": json_safe(candidate), "gates": gates, "artifact": artifact, "candidatePath": str(candidate_dir), "reviewRequired": False})
    budget.check()


def train(request):
    invocation_started, invocation_started_at = time.monotonic(), utc_now()
    # Validate path separation before creating files or redirecting raw output.
    if not isinstance(request, dict) or not isinstance(request.get("outputDir"), str) or not isinstance(request.get("runId"), str):
        raise ValueError("train requires runId and outputDir")
    output = Path(request["outputDir"]).expanduser().resolve()
    model_path = Path(request.get("modelPath", ".")).expanduser().resolve()
    if is_within(output, model_path) or is_within(model_path, output):
        raise ValueError("outputDir and base model directory must not overlap")
    for key in ("datasetManifest", "trainPath", "validationPath"):
        if isinstance(request.get(key), str) and is_within(Path(request[key]).resolve(), output):
            raise ValueError("outputDir must not contain immutable input data")
    with run_lock(output, request["runId"]), raw_logs(output) as protocol:
        sink = EventSink(output, request["runId"], protocol)
        old_receipt = read_json(output / "receipt.json") if (output / "receipt.json").exists() else {}
        invocation = str(time.time_ns()) + "-" + str(os.getpid())
        if old_receipt.get("status") in ("accepted", "rejected"):
            error = {"type": "ValueError", "message": "a terminal evaluated run cannot be resumed or overwritten; use a separate runId/outputDir"}
            append_json(output / "invocations.jsonl", {"invocationId": invocation, "timestamp": utc_now(), "status": "request-rejected", "error": error})
            sink.emit("request-rejected", old_receipt.get("lastStep", 0), status=old_receipt["status"], invocationId=invocation, error=error)
            return 1
        receipt = {**old_receipt, "schemaVersion": SCHEMA_VERSION, "runId": request["runId"], "status": "preparing", "startedAt": old_receipt.get("startedAt", utc_now()), "updatedAt": utc_now(), "invocationId": invocation, "lastStep": old_receipt.get("lastStep", 0), "paths": {"events": str(output / "events.jsonl"), "stdout": str(output / "stdout.log"), "stderr": str(output / "stderr.log"), "manifest": str(output / "run-manifest.json")}}
        receipt.pop("error", None)
        receipt.pop("finishedAt", None)
        receipt.pop("invocationEndedAt", None)
        receipt["reviewRequired"] = False
        budget = RunBudget(output, DEFAULT_CONFIG, old_receipt.get("activeSeconds", 0), invocation_started)
        receipt.update({"invocationStartedAt": invocation_started_at, "previousActiveSeconds": budget.previous_active, "activeSeconds": budget.active_seconds, "activeTimeDefinition": "cumulative worker wall time including validation/probe/load/train/evaluation; excludes time waiting between invocations"})
        old_handlers = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda number, frame: setattr(budget, "cancelled", True))
        sink.emit("invocation-started", receipt["lastStep"], status="preparing", invocationId=invocation)
        try:
            config = validate_config(request.get("config", {}))
            budget.config = config
            receipt.update({"config": config, "timeBudgetSeconds": config["maxMinutes"] * 60})
            atomic_json(output / "receipt.json", receipt)
            budget.check()
            prepared = prepare_request(request, budget.check)
            manifest_path = output / "run-manifest.json"
            if manifest_path.exists():
                verify_resume_identity(read_json(manifest_path), prepared)
            else:
                if request.get("resumeCheckpoint"):
                    raise ValueError("cannot resume without the original frozen run manifest")
                atomic_json(manifest_path, {**frozen_manifest(prepared), "createdAt": utc_now()})
            receipt["identitySha256"] = prepared["identitySha256"]
            receipt["config"] = config
            receipt["counts"] = prepared["counts"]
            receipt["plannedMaximumSteps"] = prepared["plannedMaximumSteps"]
            receipt["activeSeconds"] = budget.active_seconds
            atomic_json(output / "receipt.json", receipt)
            if prepared["counts"]["validation"] < config["minEvaluationSamples"]:
                receipt.update({"status": "rejected", "reason": "insufficient raw held-out samples; no runtime probe or optimizer updates performed", "gates": {"status": "rejected", "comparisons": [{"gate": "evaluation-samples", "passed": False, "observed": prepared["counts"]["validation"], "threshold": config["minEvaluationSamples"], "operator": ">="}]}})
            else:
                configure_training_cache(output)
                runtime = probe()
                receipt["environment"] = runtime
                atomic_json(output / ("probe-" + invocation + ".json"), runtime)
                if not runtime["trainingReady"]:
                    raise RuntimeError("training runtime is blocked: " + "; ".join(runtime["blockers"]))
                budget.check()
                if request.get("resumeCheckpoint") and old_receipt.get("environment", {}).get("packages") != runtime["packages"]:
                    raise ValueError("resume environment package versions changed; use the original environment")
                execute_training(prepared, request, runtime, sink, receipt, budget, invocation)
            # Detect edits during training as well as between processes before
            # exposing a checkpoint or an evaluated candidate to the assistant.
            verify_resume_identity(prepared, prepare_request(request, budget.check))
        except RunCancelled as error:
            receipt.update({"status": "cancelled", "error": {"type": type(error).__name__, "message": str(error)}})
            traceback.print_exc(file=sys.stderr)
        except (Exception, KeyboardInterrupt) as error:
            receipt.update({"status": "cancelled" if isinstance(error, KeyboardInterrupt) else "failed", "error": {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}})
            traceback.print_exc(file=sys.stderr)
        finally:
            receipt.update({"updatedAt": utc_now(), "invocationEndedAt": utc_now(), "activeSeconds": budget.active_seconds, "diskBytes": directory_bytes(output)})
            if receipt["status"] != "checkpoint-ready":
                receipt["finishedAt"] = utc_now()
            atomic_json(output / "receipt.json", receipt)
            append_json(output / "invocations.jsonl", {"invocationId": invocation, "endedAt": receipt["invocationEndedAt"], "status": receipt["status"], "lastStep": receipt["lastStep"], "activeSeconds": receipt["activeSeconds"], "checkpoint": receipt.get("checkpoint"), "error": receipt.get("error")})
            sink.emit("checkpoint-ready" if receipt["status"] == "checkpoint-ready" else "finished", receipt["lastStep"], metrics=receipt.get("candidate", {}), checkpoint=receipt.get("checkpoint"), status=receipt["status"], invocationId=invocation, receiptPath=str(output / "receipt.json"), error=receipt.get("error"), gates=receipt.get("gates"))
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
        return 0 if receipt["status"] in ("checkpoint-ready", "accepted", "rejected") else 1


def plan(request):
    """Pure stdlib preflight; failure is data, not an environment install request."""
    result = {"schemaVersion": SCHEMA_VERSION, "createdAt": utc_now(), "status": "invalid", "valid": False, "errors": [], "warnings": [], "runtimeQualification": "not performed; plan validates local files and policy without importing training dependencies", "defaults": DEFAULT_CONFIG}
    errors = result["errors"]
    if not isinstance(request, dict):
        errors.append({"field": "request", "code": "invalid-request", "message": "request must be a JSON object"})
        return result
    for key in ("runId", "modelPath", "datasetManifest", "trainPath", "validationPath", "outputDir", "config"):
        if key not in request:
            errors.append({"field": key, "code": "required", "message": f"request.{key} is required"})
        elif key not in ("config", "runId"):
            if not isinstance(request[key], str) or not request[key]:
                errors.append({"field": key, "code": "invalid-path", "message": "a nonempty local path is required"})
            elif key != "outputDir" and not Path(request[key]).expanduser().exists():
                errors.append({"field": key, "code": "missing-path", "message": f"local input does not exist: {request[key]}"})
    if "runId" in request and (not isinstance(request["runId"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", request["runId"])):
        errors.append({"field": "runId", "code": "invalid-run-id", "message": "runId must be a stable 1-128 character identifier"})
    try:
        if "config" in request:
            result["config"] = validate_config(request["config"])
    except ValueError as error:
        errors.append({"field": "config", "code": "invalid-config", "message": str(error)})
    if errors:
        return result
    try:
        prepared = prepare_request(request)
        result.update(frozen_manifest(prepared))
        if prepared["counts"]["validation"] < prepared["config"]["minEvaluationSamples"]:
            errors.append({"field": "validationPath", "code": "insufficient-holdout", "message": f"held-out samples {prepared['counts']['validation']} < minEvaluationSamples {prepared['config']['minEvaluationSamples']}; no environment installation or optimizer updates are needed"})
        else:
            result.update({"status": "planned", "valid": True})
        result["warnings"].append("Exact tokenizer lengths and supported architecture are validated in the qualified training environment before optimizer updates; truncation is forbidden.")
        result["plannedMaximumSteps"] = total_training_steps(prepared["counts"]["train"], prepared["config"])
    except (ValueError, OSError) as error:
        message = str(error)
        field = "modelPath" if any(word in message.lower() for word in ("model", "weight", "tokenizer", "transformers", "shard", "gguf", "quantiz")) else "dataset"
        if "outputDir" in message:
            field = "outputDir"
        errors.append({"field": field, "code": "input-validation-failed", "message": message})
    return result


def model_geometry(model_config):
    """Conservative decoder geometry; never infer parameters from file names."""
    aliases = {"hidden": ("hidden_size", "n_embd"), "layers": ("num_hidden_layers", "n_layer"), "heads": ("num_attention_heads", "n_head"), "vocab": ("vocab_size",)}
    geometry = {}
    for name, keys in aliases.items():
        value = next((model_config[key] for key in keys if key in model_config), None)
        if type(value) is not int or value <= 0:
            raise ValueError(f"model config lacks a positive integer {'/'.join(keys)}; memory recommendation cannot be grounded in architecture")
        geometry[name] = value
    hidden, heads = geometry["hidden"], geometry["heads"]
    geometry["intermediate"] = model_config.get("intermediate_size", model_config.get("n_inner")) or hidden * 4
    geometry["kvHeads"] = model_config.get("num_key_value_heads", heads)
    geometry["headDim"] = model_config.get("head_dim", hidden // heads)
    geometry["experts"] = model_config.get("num_local_experts", model_config.get("num_experts", model_config.get("n_routed_experts", 1)))
    if any(type(value) is not int or value <= 0 for value in geometry.values()):
        raise ValueError("model decoder dimensions must be positive integers")
    q_width, kv_width = heads * geometry["headDim"], geometry["kvHeads"] * geometry["headDim"]
    intermediate, experts = geometry["intermediate"], geometry["experts"]
    # Gated FFN and all experts, including inactive experts. Non-gated models
    # are deliberately overestimated; runtime observes actual trainable tensors.
    matrices = [(hidden, q_width), (hidden, kv_width), (hidden, kv_width), (q_width, hidden)] + [(hidden, intermediate), (hidden, intermediate), (intermediate, hidden)] * experts
    embeddings = hidden * geometry["vocab"]
    parameters = geometry["layers"] * (sum(a * b for a, b in matrices) + 4 * hidden) + embeddings * (1 if model_config.get("tie_word_embeddings") is True else 2)
    return {**geometry, "parameters": parameters, "embeddingParameters": embeddings, "loraParametersPerRank": geometry["layers"] * sum(a + b for a, b in matrices)}


def recommendation_estimates(snapshot, geometry, config):
    parameters, embeddings = geometry["parameters"], geometry["embeddingParameters"]
    base_bytes = max(snapshot["weightBytes"], parameters * 2)
    if config["precision"] == "qlora-4bit":
        base_bytes = math.ceil(max(0, parameters - embeddings) * 0.625 + embeddings * 2)
        if snapshot["config"].get("quantization_config"):
            base_bytes = max(base_bytes, snapshot["weightBytes"])
    trainable = geometry["loraParametersPerRank"] * config["loraRank"]
    sequences = config["batchSize"] * (2 if config["method"] == "dpo" else 1)
    length = config["maxSeqLength"]
    activations = sequences * length * (geometry["hidden"] * geometry["layers"] * 8 + geometry["hidden"] * 48 + geometry["vocab"] * 8)
    optimizer = trainable * 24
    checkpoint_bytes = trainable * 20 + 64 * 1024**2
    retained = math.ceil(config["maxSteps"] / config["checkpointEvery"])
    disk_bytes = retained * checkpoint_bytes + trainable * 8 + 32 * 1024**2 + 256 * 1024**2
    return {"modelWeightBytes": snapshot["weightBytes"], "estimatedParameters": parameters, "baseGpuBytes": base_bytes, "estimatedTrainableParameters": trainable, "adapterOptimizerGpuBytes": optimizer, "activationAndLogitsGpuBytes": activations, "trainingGpuBytes": math.ceil((base_bytes + optimizer + activations) * 1.10), "checkpointBytes": checkpoint_bytes, "maximumRetainedCheckpoints": retained, "runDiskBytes": disk_bytes, "totalTimeBudgetSeconds": config["maxMinutes"] * 60, "measuredStepsPerSecond": None, "predictedDurationSeconds": None}


def recommended_configuration(request, hardware=None):
    """Transparent local heuristic. Preserves precision and all user ceilings."""
    result = {"schemaVersion": SCHEMA_VERSION, "createdAt": utc_now(), "status": "invalid", "canStart": False, "requiresProbe": True, "requiresPlan": True, "errors": [], "changes": [], "assumptions": [], "warnings": [], "estimates": {}, "recommendationVersion": 1}
    try:
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        original = validate_config(request.get("config", {}))
        result["config"] = original.copy()
        model_path = request.get("modelPath")
        if not isinstance(model_path, str) or not model_path:
            raise ValueError("modelPath is required for an architecture-grounded recommendation")
        snapshot = inspect_model_snapshot(model_path, original)
        geometry = model_geometry(snapshot["config"])
        goal = request.get("goal", "balanced")
        if not isinstance(goal, str) or len(goal) > 2048:
            raise ValueError("goal must be text with at most 2048 characters")
        normalized = goal.lower().strip()
        profile = "quick" if any(word in normalized for word in ("quick", "fast", "test", "smoke")) else "quality" if any(word in normalized for word in ("quality", "best", "accurate")) else "balanced"
        result["goalProfile"] = profile
        config = result["config"]

        def change(key, value, reason):
            if config[key] != value:
                previous = config[key]
                config[key] = value
                result["changes"].append({"field": key, "from": previous, "to": value, "reason": reason})

        # These are cautious starting points, never predictions of learning or
        # wall-clock performance. Unknown free-form goals use balanced policy.
        change("batchSize", 1, "single local GPU starting batch; accumulation supplies effective batch")
        change("loraRank", min(original["loraRank"], 8 if profile == "quick" else 16), "limit adapter and retained optimizer checkpoint memory")
        change("loraAlpha", min(original["loraAlpha"], config["loraRank"] * 2), "keep suggested alpha at no more than twice suggested rank")
        context_limit = snapshot["config"].get("max_position_embeddings", snapshot["config"].get("n_positions", original["maxSeqLength"]))
        if type(context_limit) is not int or context_limit < 32:
            context_limit = original["maxSeqLength"]
        change("maxSeqLength", min(original["maxSeqLength"], context_limit, 512 if profile == "quick" else 2048 if profile == "quality" else 1024), "bound sequence memory and respect declared model context; longer samples are excluded, never truncated")
        step_cap = min(20 if profile == "quick" else 200 if profile == "quality" else 100, max(1, int(original["maxMinutes"] * 4)))
        change("maxSteps", min(original["maxSteps"], step_cap), "bounded initial experiment policy; four optimizer steps per budget minute is a cap, not a speed estimate")
        change("checkpointEvery", min(original["checkpointEvery"], config["maxSteps"], 10 if profile == "quick" else 25), "review at bounded real optimizer checkpoints")
        result["assumptions"].extend(["Precision, maxMinutes, maxDiskBytes, epochs and acceptance thresholds are preserved exactly; suggested maxSteps never exceeds the user ceiling.", "All attention and gated MLP projections are assumed LoRA targets; all MoE experts count toward storage, including inactive experts.", "BF16 base memory uses the larger of serialized weight bytes and decoder parameter estimate times two; QLoRA uses 0.625 bytes per non-embedding parameter and BF16 embeddings.", "Activation/logit estimate assumes gradient checkpointing, BF16 activations, FP32 logit workspaces, 24 bytes per trainable parameter for adapter/optimizer work and 10 percent allocation headroom.", "Disk estimate retains every optimizer checkpoint plus a separate candidate, 64 MiB checkpoint overhead and 256 MiB generated runtime cache allowance; caches and architecture-specific overhead can exceed estimates.", "Time is a hard cumulative active worker budget including validation, probes, model loading, every training chunk and evaluation. No steps-per-second or duration measurement exists; native process deadlines enforce the ceiling.", "Recommendations do not validate or change source data, measure quality, approve a candidate, download weights or install dependencies. Run plan and a live environment probe before Train."])
        hardware = request.get("hardware", {}) if hardware is None else hardware
        if not isinstance(hardware, dict):
            raise ValueError("hardware must be a probe hardware object")
        for key in ("cudaAvailable", "bf16Supported"):
            if key in hardware and type(hardware[key]) is not bool:
                raise ValueError(f"hardware.{key} must be a boolean when known")
        devices = hardware.get("devices", [])
        if not isinstance(devices, list) or any(not isinstance(device, dict) for device in devices):
            raise ValueError("hardware.devices must be an array of device objects")
        device = next((item for item in devices if item.get("index", 0) == 0), None)
        available = None
        if device:
            total, free = device.get("totalBytes"), device.get("freeBytes")
            for key, value in (("totalBytes", total), ("freeBytes", free)):
                if value is not None and (type(value) is not int or value < 0):
                    raise ValueError(f"hardware device {key} must be a nonnegative integer")
            if free is not None and total is not None and free > total:
                raise ValueError("hardware device freeBytes cannot exceed totalBytes")
            available = free if free is not None else total
            if free is None and total is not None:
                result["warnings"].append("GPU free memory is unknown; total VRAM is only an upper bound and must be refreshed after inference unloads.")
        reserve = max(1024**3, math.ceil((available or 0) * 0.15))
        usable = max(0, available - reserve) if available is not None else None
        estimates = recommendation_estimates(snapshot, geometry, config)
        while usable is not None and estimates["trainingGpuBytes"] > usable and config["maxSeqLength"] > 128:
            change("maxSeqLength", max(128, config["maxSeqLength"] // 2), "reduce estimated activations to fit currently reported GPU memory; exact tokenizer exclusions remain visible")
            estimates = recommendation_estimates(snapshot, geometry, config)
        result["estimates"] = {**estimates, "availableGpuBytes": available, "reservedGpuBytes": reserve, "usableGpuBytes": usable, "geometry": geometry}
        result["model"] = {"path": str(snapshot["path"]), "modelType": snapshot["config"]["model_type"], "configSha256": file_hash(snapshot["path"] / "config.json"), "weightBytes": snapshot["weightBytes"]}
        blocked = False
        if hardware.get("cudaAvailable") is not True or hardware.get("bf16Supported") is not True:
            blocked = True
            result["warnings"].append("CUDA or native BF16 support is unavailable or unknown; a live qualified probe is required. Precision has not been changed.")
        if usable is None:
            blocked = True
            result["warnings"].append("GPU memory is unknown; no model-fit claim can be made.")
        elif estimates["trainingGpuBytes"] > usable:
            blocked = True
            result["warnings"].append("Estimated GPU memory exceeds the currently available budget even after bounded context reduction. Select an explicitly different precision/source or a larger GPU.")
        if estimates["runDiskBytes"] > config["maxDiskBytes"]:
            blocked = True
            result["warnings"].append("Estimated retained checkpoints and candidate exceed the user disk budget; increase that explicit budget or choose a smaller adapter/step schedule.")
        if snapshot["config"].get("quantization_config"):
            result["warnings"].append("The source is already quantized; training will additionally verify actual 4-bit tensors and the requested compute precision.")
        result.update({"status": "blocked" if blocked else "recommended", "canStart": not blocked})
    except (ValueError, OSError, OverflowError) as error:
        result["errors"].append({"field": "recommendation", "code": "invalid-input", "message": str(error)})
        result.update({"status": "invalid", "canStart": False})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    probe_parser = commands.add_parser("probe")
    probe_parser.add_argument("--output", required=True)
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--request", required=True)
    plan_parser.add_argument("--output", required=True)
    recommend_parser = commands.add_parser("recommend")
    recommend_parser.add_argument("--request", required=True)
    recommend_parser.add_argument("--output", required=True)
    train_parser = commands.add_parser("train")
    train_parser.add_argument("--request", required=True)
    setup_parser = commands.add_parser("setup")
    setup_parser.add_argument("--root", required=True)
    setup_parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ("plan", "recommend"):
            result = plan(read_json(args.request)) if args.command == "plan" else recommended_configuration(read_json(args.request))
            atomic_json(args.output, result)
            print(canonical({"event": "planned" if args.command == "plan" and result["valid"] else "recommended" if args.command == "recommend" else "plan-invalid", "timestamp": utc_now(), "status": result["status"], "output": args.output, "errors": result["errors"]}).decode())
            return 0 if result["status"] in ("planned", "recommended") else 2
        if args.command == "probe":
            result = probe()
            atomic_json(args.output, result)
            print(canonical(result).decode())
            return 0 if result["status"] == "ready" else 2
        if args.command == "setup":
            return setup(args.root, args.output)
        return train(read_json(args.request))
    except (Exception, KeyboardInterrupt) as error:
        traceback.print_exc(file=sys.stderr)
        if args.command in ("plan", "recommend"):
            atomic_json(args.output, {"schemaVersion": SCHEMA_VERSION, "createdAt": utc_now(), "status": "invalid", "valid": False, "canStart": False, "errors": [{"field": "request", "code": "unreadable-request", "message": str(error)}]})
            return 2
        print(canonical({"event": "failed", "timestamp": utc_now(), "step": 0, "metrics": {}, "checkpoint": None, "status": "failed", "error": {"type": type(error).__name__, "message": str(error)}}).decode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
