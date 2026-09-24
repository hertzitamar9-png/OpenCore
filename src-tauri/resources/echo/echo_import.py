"""Index imported client transcripts in ECHO without starting model inference.

Reads one JSON object per stdin line: {conversation_id, messages}. Writes one
aggregate JSON result on stdout. Exact source text stays in EchoArchive pages;
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


def import_stream(root: Path, lines) -> dict:
    current_id = None
    archive = None
    imported = skipped = 0
    try:
        for line in lines:
            payload = json.loads(line)
            conversation_id = payload["conversation_id"]
            if not isinstance(conversation_id, str) or not conversation_id:
                raise ValueError("conversation_id is required")
            if conversation_id != current_id:
                if archive is not None:
                    archive.close()
                archive = EchoArchive(path_for(root, conversation_id))
                current_id = conversation_id
            for message in payload.get("messages", []):
                if archive.record_source_event(message, conversation_id):
                    imported += 1
                else:
                    skipped += 1
    finally:
        if archive is not None:
            archive.close()
    return {"imported": imported, "skipped": skipped}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: echo_import.py ARCHIVE_DIRECTORY")
    print(json.dumps(import_stream(Path(sys.argv[1]), sys.stdin)))
