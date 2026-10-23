"""Index imported client transcripts in ECHO without starting model inference.

Reads one JSON object per stdin line: {conversation_id, messages}. Writes one
aggregate JSON result on stdout. With --progress, emits committed-batch JSONL
receipts followed by a kind=result aggregate. Exact source text stays in EchoArchive pages;
source_event_id makes repeated imports idempotent.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

# This release also coexists with a research package named ``evoagent``.
# Resolve the archive implementation beside this helper, even when a test
# runner or another client imported the research package first.
_memory_path = Path(__file__).resolve().parent / "evoagent" / "echo_memory.py"
_memory_spec = importlib.util.spec_from_file_location("opencore_release_echo_memory", _memory_path)
if _memory_spec is None or _memory_spec.loader is None:
    raise ImportError(f"Could not load ECHO archive at {_memory_path}")
_memory_module = importlib.util.module_from_spec(_memory_spec)
sys.modules[_memory_spec.name] = _memory_module
_memory_spec.loader.exec_module(_memory_module)
EchoArchive = _memory_module.EchoArchive


def path_for(root: Path, conversation_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", conversation_id)[:32] or "conv"
    digest = hashlib.sha1(conversation_id.encode()).hexdigest()[:12]
    return root / f"{safe}-{digest}.db"


def import_stream(root: Path, lines, on_progress=None) -> dict:
    current_id = None
    archive = None
    imported = skipped = failed = 0
    errors = []
    try:
        for line_number, line in enumerate(lines, start=1):
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("batch must be an object")
                conversation_id = payload.get("conversation_id")
                if not isinstance(conversation_id, str) or not conversation_id:
                    raise ValueError("conversation_id is required")
            except Exception as error:
                failed += 1
                if len(errors) < 20:
                    errors.append(f"batch {line_number}: {type(error).__name__}: {str(error)[:160]}")
                continue
            if conversation_id != current_id:
                if archive is not None:
                    archive.close()
                archive = EchoArchive(path_for(root, conversation_id))
                current_id = conversation_id
            messages = payload.get("messages", [])
            if not isinstance(messages, list):
                failed += 1
                if len(errors) < 20:
                    errors.append(f"batch {line_number}: messages must be a list")
                continue
            batch_failed = failed
            with archive.source_event_batch():
                for message_index, message in enumerate(messages, start=1):
                    archive.db.execute("SAVEPOINT echo_source_event")
                    try:
                        if not isinstance(message, dict):
                            raise ValueError("event must be an object")
                        if archive.record_source_event(message, conversation_id):
                            imported += 1
                        else:
                            skipped += 1
                        archive.db.execute("RELEASE echo_source_event")
                    except Exception as error:
                        # One malformed legacy record must not close stdin and fail
                        # every later conversation with a broken-pipe error.
                        archive.db.execute("ROLLBACK TO echo_source_event")
                        archive.db.execute("RELEASE echo_source_event")
                        failed += 1
                        if len(errors) < 20:
                            errors.append(f"batch {line_number}, event {message_index}: {type(error).__name__}: {str(error)[:160]}")
            # Acknowledge only after RELEASE has committed the batch. Queuing a
            # line to stdin is not evidence that the archive saved its events.
            if on_progress is not None:
                on_progress({"kind": "progress", "conversation_id": conversation_id,
                             "after_id": payload.get("after_id", 0),
                             "conversation_index": payload.get("conversation_index", 0),
                             "conversation_total": payload.get("conversation_total", 0),
                             "imported": imported, "skipped": skipped, "failed": failed,
                             "batch_failed": failed - batch_failed})
    finally:
        if archive is not None:
            archive.close()
    return {"imported": imported, "skipped": skipped, "failed": failed, "errors": errors}


def delete_stream(root: Path, lines) -> dict:
    root = root.resolve()
    conversations = records = failed = 0
    errors = []
    for line_number, line in enumerate(lines, start=1):
        try:
            payload = json.loads(line)
            conversation_id = payload.get("conversation_id") if isinstance(payload, dict) else None
            if not isinstance(conversation_id, str) or not conversation_id:
                raise ValueError("conversation_id is required")
            path = path_for(root, conversation_id)
            # Only touch the exact archive shard computed from a source ID and
            # only when it is an ordinary file beneath the archive directory.
            if path.parent.resolve() != root or path.is_symlink():
                raise ValueError("archive path is not a safe local file")
            if not path.is_file():
                continue
            archive = EchoArchive(path)
            try:
                records += archive.delete_conversation(conversation_id)
            finally:
                archive.close()
            conversations += 1
        except Exception as error:
            failed += 1
            if len(errors) < 20:
                errors.append(f"conversation {line_number}: {type(error).__name__}: {str(error)[:160]}")
    return {"conversations": conversations, "records": records, "failed": failed, "errors": errors}


if __name__ == "__main__":
    # Rust writes UTF-8 JSON. Windows pipe streams must not use the ANSI code page.
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")

    if len(sys.argv) == 2:
        result = import_stream(Path(sys.argv[1]), sys.stdin)
    elif len(sys.argv) == 3 and sys.argv[2] == "--progress":
        result = import_stream(Path(sys.argv[1]), sys.stdin,
                               lambda progress: print(json.dumps(progress), flush=True))
        result["kind"] = "result"
    elif len(sys.argv) == 3 and sys.argv[2] == "--delete":
        result = delete_stream(Path(sys.argv[1]), sys.stdin)
    else:
        raise SystemExit("usage: echo_import.py ARCHIVE_DIRECTORY [--delete|--progress]")
    print(json.dumps(result))
