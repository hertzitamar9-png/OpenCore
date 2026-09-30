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

SPEED_PROBE_PROMPT = (
    "SPEED_QUALIFICATION: Explain how to debug a slow local language-model response. "
    "Write one useful, detailed answer of roughly 100 to 140 words so the visible answer "
    "is long enough for a stable throughput measurement."
)
SPEED_PROBE_MIN_TOKENS = 50
SPEED_PROBE_MIN_TOKENS_PER_SECOND = 20.0
SPEED_PROBE_MAX_TOKENS = 256
ECHO_PROFILES = frozenset({"echo", "native1m", "unsloth-echo", "doucode",
                           "dualcore-echo", "fusioncore-echo"})


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


def validate_records(inputs, records, complete, request_binding=None, allowed_identity_hashes=None):
    expected = {str(row["id"]): row for row in inputs["rows"]}
    seen = set()
    conversations = set()
    allowed_identity_hashes = set(allowed_identity_hashes or ())
    for row in records:
        identity = str(row["id"])
        if identity in seen:
            raise ValueError(f"Duplicate capture ID: {identity}")
        seen.add(identity)
        if identity not in expected or row["prompt_sha256"] != expected[identity]["prompt_sha256"]:
            raise ValueError(f"Captured prompt hash mismatch: {identity}")
        if hashlib.sha256(json_bytes(row["response"])).hexdigest() != row["response_sha256"]:
            raise ValueError(f"Response hash mismatch: {identity}")
        if allowed_identity_hashes:
            source_identity = row.get('runtime_identity_sha256')
            if source_identity is None and len(allowed_identity_hashes) == 1:
                source_identity = next(iter(allowed_identity_hashes))
            if source_identity not in allowed_identity_hashes:
                raise ValueError(f'Unknown runtime identity for capture row: {identity}')
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
            if ('thinking_budget_tokens' in request_binding
                    and payload.get('thinking_budget_tokens') != request_binding['thinking_budget_tokens']):
                raise ValueError(f'Isolated request reasoning budget differs from its generation binding: {identity}')
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
    lineage_hashes = []
    lineage_identities = []
    lineage = manifest.get('identity_lineage', [])
    if not isinstance(lineage, list):
        raise ValueError('Invalid runtime identity lineage')
    for ancestor in lineage:
        name = ancestor.get('snapshot')
        expected_hash = ancestor.get('identity_sha256')
        if (not isinstance(name, str) or Path(name).name != name
                or not name.startswith('identity-') or not name.endswith('.json')):
            raise ValueError('Invalid runtime identity lineage snapshot path')
        source = capture_dir / name
        if not source.is_file() or file_hash(source) != expected_hash:
            raise ValueError('Runtime identity lineage snapshot hash mismatch')
        ancestor_identity = json.loads(source.read_text(encoding='utf-8-sig'))
        if ancestor_identity.get('model') != identity.get('model'):
            raise ValueError('Runtime identity lineage changes the benchmark model')
        lineage_hashes.append(expected_hash)
        lineage_identities.append(ancestor_identity)
    identity_chain = [*lineage_identities, identity]
    for old, new in zip(identity_chain, identity_chain[1:]):
        _validate_runtime_transition(old, new)
    return {'exact_identity_file_hash_verified': bool(sources),
            'identity_hashes': [binding.get('identity_sha256'), *lineage_hashes],
            'scope': ('Exact retained/original identity bytes and embedded model match'
                      if sources else 'Historic embedded model match only; original identity required for real-model grading')}


def load_grading_capture(inputs_path, capture_dir, identity_path=None):
    inputs = load_inputs(inputs_path)
    manifest = json.loads((capture_dir / "capture-manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("status") != "complete":
        raise ValueError("Only a complete capture can be graded")
    if manifest.get("speed_gate_schema", 0) >= 1:
        qualification_path = capture_dir / "speed-qualification.json"
        if (not qualification_path.is_file()
                or file_hash(qualification_path) != manifest.get("speed_qualification_sha256")):
            raise ValueError("Speed qualification hash mismatch")
        qualification = json.loads(qualification_path.read_text(encoding="utf-8-sig"))
        binding = manifest.get("binding", {})
        if (qualification.get("status") != "passed"
                or qualification.get("model") != binding.get("model")
                or qualification.get("runtime_identity_sha256") != binding.get("identity_sha256")
                or qualification.get("minimum_visible_answer_tokens", 0) < SPEED_PROBE_MIN_TOKENS
                or qualification.get("minimum_visible_tokens_per_second", 0) < SPEED_PROBE_MIN_TOKENS_PER_SECOND
                or qualification.get("visible_answer_tokens", 0) < SPEED_PROBE_MIN_TOKENS
                or qualification.get("visible_tokens_per_second", 0) < SPEED_PROBE_MIN_TOKENS_PER_SECOND):
            raise ValueError("Speed qualification does not satisfy the bound model throughput gate")
        manifest["speed_qualification_verified"] = True
        profile = (manifest.get("identity") or {}).get("profile")
        if profile in ECHO_PROFILES:
            route = manifest.get("echo_route_verification")
            if (not isinstance(route, dict) or route.get("status") != "passed"
                    or route.get("profile") != profile
                    or route.get("history_mode") != "persistent_echo"):
                raise ValueError("ECHO route verification is missing or invalid")
            manifest["echo_route_verified"] = True
        else:
            manifest["echo_route_verified"] = False
    else:
        manifest["speed_qualification_verified"] = False
        manifest["echo_route_verified"] = False
    if file_hash(inputs_path) != manifest["binding"]["inputs_sha256"]:
        raise ValueError("Grading input file hash mismatch")
    manifest['identity_validation'] = validate_identity(manifest, capture_dir, identity_path)
    path = capture_dir / "responses.jsonl"
    records = [json.loads(line) for line in path.read_bytes().splitlines()]
    validate_records(inputs, records, complete=True, request_binding=manifest['binding'],
                     allowed_identity_hashes=manifest['identity_validation']['identity_hashes'])
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


def verify_echo_route(base_url, identity):
    profile = identity.get("profile")
    if profile not in ECHO_PROFILES:
        return None
    try:
        stats = request(base_url + "/echo/stats")
    except Exception as error:
        raise RuntimeError(
            f"ECHO profile {profile} must use the live ECHO proxy; "
            f"/echo/stats verification failed: {error}"
        ) from error
    if not isinstance(stats, dict) or stats.get("history_mode") != "persistent_echo":
        raise RuntimeError(
            f"ECHO profile {profile} must use the live ECHO proxy; "
            "the endpoint did not report persistent_echo history"
        )
    return {
        "status": "passed",
        "profile": profile,
        "history_mode": "persistent_echo",
        "archive_capacity": stats.get("archive_capacity"),
    }


def qualify_speed(base_url, model, identity_hash, output, thinking_budget_tokens=None):
    """Require exact-tokenized selected-answer throughput before capture."""
    probe = {
        "model": model,
        "messages": [{"role": "system", "content": "You are a helpful assistant."},
                     {"role": "user", "content": SPEED_PROBE_PROMPT}],
        "temperature": 0,
        "max_tokens": SPEED_PROBE_MAX_TOKENS,
        "stream": False,
        "conversation_id": "speed-qualification-" + uuid.uuid4().hex,
    }
    if thinking_budget_tokens is not None:
        probe['thinking_budget_tokens'] = thinking_budget_tokens
    started = time.perf_counter()
    result = {
        "schema": 1,
        "model": model,
        "runtime_identity_sha256": identity_hash,
        "request_sha256": hashlib.sha256(json_bytes(probe)).hexdigest(),
        "minimum_visible_answer_tokens": SPEED_PROBE_MIN_TOKENS,
        "minimum_visible_tokens_per_second": SPEED_PROBE_MIN_TOKENS_PER_SECOND,
        "started": datetime.now(timezone.utc).isoformat(),
    }
    if thinking_budget_tokens is not None:
        result['thinking_budget_tokens'] = thinking_budget_tokens
    try:
        response = request(base_url + "/v1/chat/completions", probe)
        elapsed = time.perf_counter() - started
        if response.get("model") != model:
            raise ValueError("Speed qualification response model differs from the requested model")
        answer = completion(response)
        token_result = request(base_url + "/tokenize", {"content": answer})
        tokens = token_result.get("tokens")
        if not isinstance(tokens, list):
            raise ValueError("Exact tokenizer endpoint did not return a token list")
        count = len(tokens)
        rate = count / elapsed if elapsed > 0 else 0.0
        result.update(
            status=("passed" if count >= SPEED_PROBE_MIN_TOKENS
                    and rate >= SPEED_PROBE_MIN_TOKENS_PER_SECOND else "rejected"),
            visible_answer_tokens=count,
            wall_seconds=round(elapsed, 6),
            visible_tokens_per_second=round(rate, 3),
            finish_reason=response["choices"][0]["finish_reason"],
            response_sha256=hashlib.sha256(json_bytes(response)).hexdigest(),
        )
    except Exception as error:
        result.update(status="rejected", error=f"{type(error).__name__}: {error}")
    result["finished"] = datetime.now(timezone.utc).isoformat()
    write_manifest(output / "speed-qualification.json", result)
    if result["status"] != "passed":
        speed = result.get("visible_tokens_per_second", 0)
        raise RuntimeError(
            f"Model failed the 20 tokens/s speed preflight "
            f"({speed} visible tokens/s; see speed-qualification.json)"
        )
    return result


def _artifact_fingerprint(identity, key):
    return sorted((Path(item['path']).name, item['bytes'], item['sha256'])
                  for item in identity.get(key, []))


def _validate_runtime_transition(old, new):
    same_fields = ('model', 'evidence_kind', 'profile', 'checkpoint', 'complete_towers',
                   'candidate_budget', 'sampling', 'coupling_trained', 'request_isolation')
    if any(old.get(field) != new.get(field) for field in same_fields):
        raise ValueError('Runtime identity lineage changes the model, checkpoint, or sampling identity')
    if _artifact_fingerprint(old, 'artifacts') != _artifact_fingerprint(new, 'artifacts'):
        raise ValueError('Runtime identity lineage changes model weights')
    old_runtime = {Path(item['path']).name: (item['bytes'], item['sha256'])
                   for item in old.get('runtime_files', [])}
    new_runtime = {Path(item['path']).name: (item['bytes'], item['sha256'])
                   for item in new.get('runtime_files', [])}
    if old_runtime.keys() != new_runtime.keys():
        raise ValueError('Runtime identity lineage changes the runtime file set')
    changed = [name for name in old_runtime if old_runtime[name] != new_runtime[name]]
    if changed != ['selection.py']:
        raise ValueError(f'Runtime identity lineage changes files outside the reviewed parser: {changed}')


def _validate_resume_compatibility(parent_manifest, parent_dir, inputs_hash, model, max_tokens,
                                   identity, thinking_budget_tokens=None):
    if parent_manifest.get('status') not in ('partial', 'in_progress'):
        raise ValueError('Only a partial or interrupted capture can be resumed')
    old_binding = parent_manifest.get('binding', {})
    if (old_binding.get('inputs_sha256') != inputs_hash or old_binding.get('model') != model
            or old_binding.get('max_tokens') != max_tokens or old_binding.get('temperature') != 0
            or old_binding.get('stream') is not False
            or old_binding.get('thinking_budget_tokens') != thinking_budget_tokens
            or old_binding.get('request_isolation') != identity.get('request_isolation')):
        raise ValueError('Resume source differs in benchmark inputs or generation settings')
    validate_identity(parent_manifest, parent_dir)
    old = parent_manifest.get('identity') or {}
    _validate_runtime_transition(old, identity)


def capture(base_url, model, inputs_path, identity_path, output, max_tokens, resume_from=None,
            thinking_budget_tokens=None):
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1") or parts.username:
        raise ValueError("Benchmark capture accepts only a local loopback HTTP endpoint")
    base_url = base_url.rstrip("/")
    if max_tokens < 1:
        raise ValueError("Evaluation token budget must be explicit and positive")
    if (thinking_budget_tokens is not None
            and (isinstance(thinking_budget_tokens, bool)
                 or not isinstance(thinking_budget_tokens, int) or thinking_budget_tokens < 1)):
        raise ValueError("Thinking budget must be a positive integer when specified")
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
    echo_route_verification = verify_echo_route(base_url, identity)
    manifest_path = output / "capture-manifest.json"
    responses_path = output / "responses.jsonl"
    errors_path = output / "errors.jsonl"
    snapshot_path = output / 'identity.json'
    bound = {"inputs_sha256": file_hash(inputs_path), "identity_sha256": hashlib.sha256(identity_bytes).hexdigest(),
             "model": model, "max_tokens": max_tokens, "temperature": 0, "stream": False}
    if thinking_budget_tokens is not None:
        bound['thinking_budget_tokens'] = thinking_budget_tokens
    isolation = identity.get('request_isolation')
    if isolation is not None:
        if isolation != 'fresh_conversation_per_sample':
            raise ValueError('Unknown benchmark request isolation mode')
        bound['request_isolation'] = isolation
    resume_from = Path(resume_from) if resume_from else None
    if resume_from and resume_from.resolve() == output.resolve():
        raise ValueError('Resume source and destination must be separate to preserve the original capture')
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if previous and previous["binding"] != bound:
        raise ValueError("Capture binding differs; refusing to mix checkpoints or inputs")
    if previous:
        validate_identity(previous, output, identity_path)
    if responses_path.exists() and not previous:
        raise ValueError("Existing capture has no identity manifest")
    records = [json.loads(line) for line in responses_path.read_bytes().splitlines()] if responses_path.exists() else []
    lineage = previous.get('identity_lineage', []) if previous else []
    if resume_from:
        if previous or responses_path.exists() or snapshot_path.exists():
            raise ValueError('Resume destination must be empty')
        parent_manifest_path = resume_from / 'capture-manifest.json'
        parent_responses_path = resume_from / 'responses.jsonl'
        parent_manifest = json.loads(parent_manifest_path.read_text(encoding='utf-8-sig'))
        _validate_resume_compatibility(parent_manifest, resume_from, bound['inputs_sha256'], model,
                                       max_tokens, identity, thinking_budget_tokens)
        output.mkdir(parents=True, exist_ok=True)
        parent_binding = parent_manifest['binding']
        parent_records = [json.loads(line) for line in parent_responses_path.read_bytes().splitlines()]
        parent_validation = validate_identity(parent_manifest, resume_from)
        validate_records(inputs, parent_records, complete=False, request_binding=parent_binding,
                         allowed_identity_hashes=parent_validation['identity_hashes'])
        parent_identity_hash = parent_binding['identity_sha256']
        for record in parent_records:
            record.setdefault('runtime_identity_sha256', parent_identity_hash)
        records = parent_records
        lineage = list(parent_manifest.get('identity_lineage', []))
        ancestor_name = f"identity-{parent_identity_hash[:16]}.json"
        (output / ancestor_name).write_bytes((resume_from / parent_manifest['identity_snapshot']).read_bytes())
        lineage.append({'identity_sha256': parent_identity_hash, 'snapshot': ancestor_name})
        for ancestor in parent_manifest.get('identity_lineage', []):
            ancestor_bytes = (resume_from / ancestor['snapshot']).read_bytes()
            copied_name = ancestor['snapshot']
            (output / copied_name).write_bytes(ancestor_bytes)
        allowed = {bound['identity_sha256'], *[item['identity_sha256'] for item in lineage]}
        validate_records(inputs, records, complete=False, request_binding=bound,
                         allowed_identity_hashes=allowed)
        responses_path.write_bytes(b''.join(json_bytes(record) + b'\n' for record in records))
    else:
        identity_hashes = {bound['identity_sha256']}
        if previous:
            identity_hashes.update(validate_identity(previous, output)['identity_hashes'])
        validate_records(inputs, records, complete=bool(previous and previous["status"] == "complete"),
                         request_binding=bound, allowed_identity_hashes=identity_hashes if previous else None)
    if previous and previous["status"] == "complete":
        if file_hash(responses_path) != previous["responses_sha256"]:
            raise ValueError("Completed response file hash mismatch")
        return previous
    output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output).free < 100_000_000_000:
        raise RuntimeError("Storage reserve below 100 GB; benchmark not started")
    speed_qualification = qualify_speed(
        base_url, model, bound["identity_sha256"], output, thinking_budget_tokens)
    responses_path.touch(exist_ok=True)
    if snapshot_path.exists() and snapshot_path.read_bytes() != identity_bytes:
        raise ValueError('Existing identity snapshot differs; refusing to replace evidence')
    if not snapshot_path.exists():
        snapshot_path.write_bytes(identity_bytes)
    start_time = previous['started'] if previous else (
        parent_manifest['started'] if resume_from else datetime.now(timezone.utc).isoformat())
    manifest = {"schema": 2, "speed_gate_schema": 1, 'identity_snapshot': 'identity.json',
                "status": "in_progress", "benchmark": inputs["benchmark"],
                "binding": bound, "identity": identity, "health": health, "properties": props,
                "speed_qualification": speed_qualification,
                "speed_qualification_sha256": file_hash(output / "speed-qualification.json"),
                "echo_route_verification": echo_route_verification,
                "model_quality_measured": False, "samples": len(inputs["rows"]),
                "completed": len(records), "started": start_time}
    if lineage:
        manifest['identity_lineage'] = lineage
    if resume_from:
        manifest['resumed_from'] = str(resume_from.resolve())
    write_manifest(manifest_path, manifest)
    done = {str(row["id"]) for row in records}
    for row in inputs["rows"]:
        if str(row["id"]) in done:
            continue
        if shutil.disk_usage(output).free < 100_000_000_000:
            raise RuntimeError("Storage reserve below 100 GB; capture stopped")
        messages = []
        if inputs.get("system_prompt") is not None:
            messages.append({"role": "system", "content": inputs["system_prompt"]})
        messages.append({"role": "user", "content": row["prompt"]})
        payload = {"model": model, "messages": messages, "temperature": 0,
                   "max_tokens": max_tokens, "stream": False}
        if thinking_budget_tokens is not None:
            payload['thinking_budget_tokens'] = thinking_budget_tokens
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
                      "seconds": time.perf_counter() - started, "recorded": datetime.now(timezone.utc).isoformat(),
                      "runtime_identity_sha256": bound['identity_sha256']}
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
    validate_records(inputs, records, complete=True, request_binding=bound,
                     allowed_identity_hashes={bound['identity_sha256'],
                                              *[item['identity_sha256'] for item in lineage]})
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
    parser.add_argument("--thinking-budget-tokens", type=int,
                        help="Bind and apply an explicit positive reasoning-token budget to preflight and samples")
    parser.add_argument('--resume-from', type=Path,
                        help='Copy verified rows from a partial capture with only a reviewed parser source change')
    args = parser.parse_args()
    try:
        result = capture(args.url, args.model, args.inputs, args.identity, args.output,
                         args.max_tokens, args.resume_from, args.thinking_budget_tokens)
        print(json.dumps({"status": result["status"], "completed": result["completed"]}), flush=True)
        return 0
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
