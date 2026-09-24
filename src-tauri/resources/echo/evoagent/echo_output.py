"""Disk output ledger: UTC receipt timestamps, exact bytes, bounded counters."""
import hashlib
import json
import re
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def word_target(question):
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(billion|million|thousand|[bmk])?\s+words?\b",
                      question, re.I)
    if not match:
        return 0
    factor = {"billion": 10**9, "b": 10**9, "million": 10**6, "m": 10**6,
              "thousand": 1000, "k": 1000}.get((match[2] or "").lower(), 1)
    return int(float(match[1]) * factor)


class OutputLedger:
    def __init__(self, archive, path, conversation, request, target=0):
        self.archive = archive
        self.path = path
        self.conversation = conversation
        self.target = target
        self.words = self.chars = self.bytes = self.chunks = 0
        self.in_word = False
        self.created = utc_now()
        self.kind = "essay" if re.search(r"\bessay\b", request, re.I) else "response"
        self.request = request[:1000]
        with archive._lock:
            archive.db.execute("CREATE TABLE IF NOT EXISTS echo_outputs "
                               "(path TEXT PRIMARY KEY, conversation TEXT, updated TEXT, metadata TEXT)")
            archive.db.execute("CREATE INDEX IF NOT EXISTS echo_outputs_conversation "
                               "ON echo_outputs(conversation, updated)")
            archive.db.commit()
        self.update("running")

    @staticmethod
    def recent(archive, conversation, limit=3):
        with archive._lock:
            exists = archive.db.execute("SELECT 1 FROM sqlite_master WHERE name='echo_outputs'").fetchone()
            if not exists:
                return []
            rows = archive.db.execute("SELECT metadata FROM echo_outputs WHERE conversation=? "
                                      "ORDER BY updated DESC LIMIT ?", (conversation, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update(self, status):
        info = {"file": str(self.path), "kind": self.kind, "request": self.request,
                "created_utc": self.created, "updated_utc": utc_now(), "status": status,
                "words": self.words, "target_words": self.target, "bytes": self.bytes,
                "chunks": self.chunks, "timestamp_basis": "UTC time each chunk was received by ECHO"}
        with self.archive._lock:
            self.archive.db.execute("INSERT OR REPLACE INTO echo_outputs VALUES (?,?,?,?)",
                (str(self.path), self.conversation, info["updated_utc"], json.dumps(info)))
            self.archive.db.commit()
        return info

    def append(self, text):
        raw = text.encode("utf-8")
        stamp = utc_now()
        words = len(re.findall(r"\S+", text))
        if self.in_word and text and not text[0].isspace():
            words -= 1
        if text:
            self.in_word = not text[-1].isspace()
        record = {"received_utc": stamp, "byte_start": self.bytes,
                  "byte_end": self.bytes + len(raw), "word_count_before": self.words,
                  "word_count_after": self.words + words,
                  "sha256": hashlib.sha256(raw).hexdigest()}
        with self.path.open("ab") as output:
            output.write(raw)
            output.flush()
        with self.path.with_suffix(".timestamps.jsonl").open("a", encoding="utf-8") as journal:
            journal.write(json.dumps(record) + "\n")
            journal.flush()
        self.words += words
        self.chars += len(text)
        self.bytes += len(raw)
        self.chunks += 1
        self.update("running")
