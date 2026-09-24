"""Backfill typed ECHO events from the OpenCore app's saved timeline.

The source database is read-only. ECHO pages remain immutable and existing
fingerprints make this safe to resume after interruption.
"""
from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
import time
from pathlib import Path

RELEASE_ECHO = Path(__file__).resolve().parents[3] / "release" / "OpenCore-Code-Portable" / "echo"
sys.path.insert(0, str(RELEASE_ECHO))
from echo_import import EchoArchive, path_for  # noqa: E402

IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp"}


def image_assets(metadata: dict, source: str, role: str) -> list[dict]:
    if source != "OpenCore" or role != "user":
        return []
    result = []
    for item in metadata.get("files", []):
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path", "")))
        mime = IMAGE_MIME.get(path.suffix.lower())
        if not mime or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            continue
        result.append({"name": str(item.get("name") or path.name),
                       "data_url": "data:%s;base64,%s" %
                       (mime, base64.b64encode(path.read_bytes()).decode("ascii"))})
    return result


def backfill(database: Path, archive_root: Path, limit: int = 0,
             conversation_id: str | None = None) -> dict:
    source = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    archive_root.mkdir(parents=True, exist_ok=True)
    totals = {"processed": 0, "imported": 0, "already_present": 0,
              "conversations": 0, "errors": 0}
    query = ("SELECT id,conversation_id,timestamp,kind,role,source,title,content,metadata "
             "FROM timeline WHERE kind<>'echo_import'")
    args: list[str] = []
    if conversation_id:
        query += " AND conversation_id=?"
        args.append(conversation_id)
    query += " ORDER BY conversation_id,id"
    current_id = None
    archive = None
    started = time.monotonic()
    try:
        for row in source.execute(query, args):
            if limit and totals["processed"] >= limit:
                break
            try:
                if row["conversation_id"] != current_id:
                    if archive is not None:
                        archive.close()
                    current_id = row["conversation_id"]
                    archive = EchoArchive(path_for(archive_root, current_id))
                    totals["conversations"] += 1
                metadata = json.loads(row["metadata"] or "{}")
                if not isinstance(metadata, dict):
                    metadata = {}
                message = {"source_event_id": metadata.get("opencore_source_event_id") or f"legacy:{row['id']}",
                           "timestamp": row["timestamp"], "kind": row["kind"],
                           "role": row["role"], "source": row["source"],
                           "title": row["title"], "content": row["content"],
                           "metadata": metadata,
                           "assets": image_assets(metadata, row["source"], row["role"])}
                if archive.record_source_event(message, current_id):
                    totals["imported"] += 1
                else:
                    totals["already_present"] += 1
            except Exception as error:
                totals["errors"] += 1
                print(f"Event {row['id']} could not be indexed: {error}", file=sys.stderr)
            totals["processed"] += 1
            if totals["processed"] % 1000 == 0:
                print(json.dumps({**totals, "elapsed_seconds": round(time.monotonic() - started, 1)}),
                      flush=True)
    finally:
        if archive is not None:
            archive.close()
        source.close()
    totals["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--conversation-id")
    options = parser.parse_args()
    print(json.dumps(backfill(options.database, options.archive_root,
                              options.limit, options.conversation_id)), flush=True)
