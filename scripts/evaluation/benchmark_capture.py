"""Capture actual loopback API completions for later unchanged benchmark grading."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def load_inputs(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if value.get("schema") != 1 or not value.get("rows"):
        raise ValueError("Inputs must have schema 1 and nonempty rows")
    seen = set()
    for row in value["rows"]:
        identity = str(row["id"])
        if identity in seen:
            raise ValueError(f"Duplicate input ID: {identity}")
        seen.add(identity)
        if hashlib.sha256(row["prompt"].encode("utf-8")).hexdigest() != row["prompt_sha256"]:
            raise ValueError(f"Prompt hash mismatch: {identity}")
    return value


def completion(response):
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("Response must contain exactly one selected choice")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if content is None and message.get("tool_calls"):
        return ""  # A real unexpected tool call is an incorrect text answer, not a fabricated answer.
    if message.get("role") != "assistant" or not isinstance(content, str):
        raise ValueError("Response has no assistant completion text")
    if not choices[0].get("finish_reason"):
        raise ValueError("Response has no completion finish reason")
    return content


def validate_records(inputs, records, complete, request_binding=None):
    expected = {str(row["id"]): row for row in inputs["rows"]}
    seen = set()
    conversations = set()
    for row in records:
        identity = str(row["id"])
        if identity in seen:
            raise ValueError(f"Duplicate capture ID: {identity}")
        seen.add(identity)
        if identity not in expected or row["prompt_sha256"] != expected[identity]["prompt_sha256"]:
            raise ValueError(f"Captured prompt hash mismatch: {identity}")
        if hashlib.sha256(json_bytes(row["response"])).hexdigest() != row["response_sha256"]:
            raise ValueError(f"Response hash mismatch: {identity}")
        completion(row["response"])
        if request_binding and request_binding.get('request_isolation') == 'fresh_conversation_per_sample':
            payload = row.get('request')
            if not isinstance(payload, dict) or hashlib.sha256(json_bytes(payload)).hexdigest() != row.get('request_sha256'):
                raise ValueError(f'Isolated request hash mismatch: {identity}')
            conversation = payload.get('conversation_id')
            if not isinstance(conversation, str) or not conversation or conversation in conversations:
                raise ValueError(f'Benchmark conversation missing or reused: {identity}')
            conversations.add(conversation)
            messages = []
            if inputs.get('system_prompt') is not None:
                messages.append({'role': 'system', 'content': inputs['system_prompt']})
            messages.append({'role': 'user', 'content': expected[identity]['prompt']})
            if (payload.get('messages') != messages or payload.get('model') != request_binding['model']
                    or payload.get('temperature') != 0 or payload.get('stream') is not False
                    or payload.get('max_tokens') != request_binding['max_tokens']):
                raise ValueError(f'Isolated request differs from the official input or generation binding: {identity}')
    if complete and seen != set(expected):
        raise ValueError("Capture is incomplete; every official task needs one answer")


def validate_identity(manifest, capture_dir, identity_path=None):
    schema = manifest.get('schema')
    if schema not in (1, 2):
        raise ValueError('Unknown capture identity schema')
    identity = manifest.get('identity') or {}
    binding = manifest.get('binding') or {}
    if (identity.get('model') != binding.get('model')
            or identity.get('evidence_kind') not in ('real_model', 'fixture')):
        raise ValueError('Embedded identity differs from the bound model or evidence kind')
    snapshot = manifest.get('identity_snapshot')
    if schema == 2 and snapshot != 'identity.json':
        raise ValueError('New capture requires its exact identity snapshot')
    sources = []
    if snapshot is not None:
        if snapshot != 'identity.json':
            raise ValueError('Invalid capture identity snapshot path')
        sources.append(capture_dir / snapshot)
    if identity_path is not None:
        sources.append(identity_path)
    for source in sources:
        if not source.is_file():
            raise ValueError('Recorded identity snapshot or original identity file is missing')
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != binding.get('identity_sha256'):
            raise ValueError('Exact identity file hash differs from the capture binding')
        if json.loads(data.decode('utf-8-sig')) != identity:
            raise ValueError('Embedded identity differs from the hash-bound identity snapshot')
    return {'exact_identity_file_hash_verified': bool(sources),
            'scope': ('Exact retained/original identity bytes and embedded model match'
                      if sources else 'Historic embedded model match only; original identity required for real-model grading')}


def load_grading_capture(inputs_path, capture_dir, identity_path=None):
    inputs = load_inputs(inputs_path)
    manifest = json.loads((capture_dir / "capture-manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("status") != "complete":
        raise ValueError("Only a complete capture can be graded")
    if file_hash(inputs_path) != manifest["binding"]["inputs_sha256"]:
        raise ValueError("Grading input file hash mismatch")
    manifest['identity_validation'] = validate_identity(manifest, capture_dir, identity_path)
    path = capture_dir / "responses.jsonl"
    records = [json.loads(line) for line in path.read_bytes().splitlines()]
    validate_records(inputs, records, complete=True, request_binding=manifest['binding'])
    if file_hash(path) != manifest["responses_sha256"]:
        raise ValueError("Grading response file hash mismatch")
    if manifest["samples"] != len(records) or manifest["completed"] != len(records):
        raise ValueError("Manifest sample counts differ from the full capture")
    return inputs, manifest, records


def write_manifest(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def append(path, value):
    with path.open("ab") as handle:
        handle.write(json_bytes(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def request(url, payload=None):
    req = urllib.request.Request(url, data=json_bytes(payload) if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=7200 if payload is not None else 10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Preserve a bounded server diagnostic while retaining HTTPError's
        # OSError type for existing readiness polling. Never retry a generation.
        detail = error.read(4096).decode('utf-8', errors='replace')
        raise urllib.error.HTTPError(error.url, error.code,
                                     f'{error.reason}; response: {detail}',
                                     error.headers, None) from error


def capture(base_url, model, inputs_path, identity_path, output, max_tokens):
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1") or parts.username:
        raise ValueError("Benchmark capture accepts only a local loopback HTTP endpoint")
    base_url = base_url.rstrip("/")
    if max_tokens < 1:
        raise ValueError("Evaluation token budget must be explicit and positive")
    inputs = load_inputs(inputs_path)
    identity_bytes = identity_path.read_bytes()
    identity = json.loads(identity_bytes.decode('utf-8-sig'))
    if identity.get("model") != model or identity.get("evidence_kind") not in ("real_model", "fixture"):
        raise ValueError("Identity must name the requested model and evidence kind")
    artifacts = identity.get("artifacts") or []
    if not artifacts:
        raise ValueError("Model identity must include hash-bound artifacts")
    for artifact in [*artifacts, *identity.get("runtime_files", [])]:
        path = Path(artifact["path"])
        if path.stat().st_size != artifact["bytes"] or file_hash(path) != artifact["sha256"]:
            raise ValueError(f"Artifact hash mismatch: {path.name}")
    health = request(base_url + "/health")
    props = request(base_url + "/props")
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "capture-manifest.json"
    responses_path = output / "responses.jsonl"
    errors_path = output / "errors.jsonl"
    bound = {"inputs_sha256": file_hash(inputs_path), "identity_sha256": hashlib.sha256(identity_bytes).hexdigest(),
             "model": model, "max_tokens": max_tokens, "temperature": 0, "stream": False}
    isolation = identity.get('request_isolation')
    if isolation is not None:
        if isolation != 'fresh_conversation_per_sample':
            raise ValueError('Unknown benchmark request isolation mode')
        bound['request_isolation'] = isolation
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if previous and previous["binding"] != bound:
        raise ValueError("Capture binding differs; refusing to mix checkpoints or inputs")
    if previous:
        validate_identity(previous, output, identity_path)
    if responses_path.exists() and not previous:
        raise ValueError("Existing capture has no identity manifest")
    records = [json.loads(line) for line in responses_path.read_bytes().splitlines()] if responses_path.exists() else []
    validate_records(inputs, records, complete=bool(previous and previous["status"] == "complete"), request_binding=bound)
    if previous and previous["status"] == "complete":
        if file_hash(responses_path) != previous["responses_sha256"]:
            raise ValueError("Completed response file hash mismatch")
        return previous
    responses_path.touch(exist_ok=True)
    snapshot_path = output / 'identity.json'
    if snapshot_path.exists() and snapshot_path.read_bytes() != identity_bytes:
        raise ValueError('Existing identity snapshot differs; refusing to replace evidence')
    if not snapshot_path.exists():
        snapshot_path.write_bytes(identity_bytes)
    manifest = {"schema": 2, 'identity_snapshot': 'identity.json', "status": "in_progress", "benchmark": inputs["benchmark"],
                "binding": bound, "identity": identity, "health": health, "properties": props,
                "model_quality_measured": False, "samples": len(inputs["rows"]),
                "completed": len(records), "started": previous["started"] if previous else datetime.now(timezone.utc).isoformat()}
    write_manifest(manifest_path, manifest)
    done = {str(row["id"]) for row in records}
    for row in inputs["rows"]:
        if str(row["id"]) in done:
            continue
        if shutil.disk_usage(output).free < 200000000000:
            raise RuntimeError("Storage reserve below 200 GB; capture stopped")
        messages = []
        if inputs.get("system_prompt") is not None:
            messages.append({"role": "system", "content": inputs["system_prompt"]})
        messages.append({"role": "user", "content": row["prompt"]})
        payload = {"model": model, "messages": messages, "temperature": 0,
                   "max_tokens": max_tokens, "stream": False}
        if isolation:
            payload['conversation_id'] = 'benchmark-' + uuid.uuid4().hex
        started = time.perf_counter()
        try:
            response = request(base_url + "/v1/chat/completions", payload)
            completion(response)
            if response.get("model") != model:
                raise ValueError("Response model differs from the bound requested model")
            record = {"id": str(row["id"]), "prompt_sha256": row["prompt_sha256"],
                      "response": response, "response_sha256": hashlib.sha256(json_bytes(response)).hexdigest(),
                      "seconds": time.perf_counter() - started, "recorded": datetime.now(timezone.utc).isoformat()}
            if isolation:
                record.update(request=payload, request_sha256=hashlib.sha256(json_bytes(payload)).hexdigest())
            append(responses_path, record)
            records.append(record)
            manifest["completed"] = len(records)
            write_manifest(manifest_path, manifest)
            print(json.dumps({"id": row["id"], "completed": len(records), "total": manifest["samples"],
                              "seconds": round(record["seconds"], 3)}), flush=True)
        except Exception as error:
            append(errors_path, {"id": str(row["id"]), "error": f"{type(error).__name__}: {error}",
                                 "recorded": datetime.now(timezone.utc).isoformat()})
            manifest["status"] = "partial"
            write_manifest(manifest_path, manifest)
            raise
    validate_records(inputs, records, complete=True, request_binding=bound)
    manifest.update(status="complete", responses_sha256=file_hash(responses_path),
                    finished=datetime.now(timezone.utc).isoformat())
    write_manifest(manifest_path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args()
    try:
        result = capture(args.url, args.model, args.inputs, args.identity, args.output, args.max_tokens)
        print(json.dumps({"status": result["status"], "completed": result["completed"]}), flush=True)
        return 0
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
