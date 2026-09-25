"""ECHO: Exact Context Hierarchical Object-memory.

Implements the memory design in EvoMem-5B.txt. The governing rule is quoted
there and it is the reason this module looks the way it does:

    Anything lossy is allowed to help find information, but nothing lossy is
    ever allowed to become the only surviving copy of information.

So the archive stores compressed *source bytes* and nothing else. Every index in
here - lexical, semantic, entity, temporal - is a derived pointer into those
bytes and may be rebuilt, corrupted, or thrown away without losing anything. A
MemoryPage deliberately has no `summary` field.

What this buys is the thing 100M-token context cannot buy physically. The KV
cache of this model costs 32 KB per token across its 8 attention layers, so a
100M-token window would need roughly 3.2 TB resident (880 GB even at 4-bit).
ECHO instead keeps GPU residency bounded by the working context while the
history on disk grows without limit - "hierarchy of access temperature, not
increasingly aggressive summaries", per the design.

Not yet implemented from the design: the semantic index here is lexical-space
(character n-gram TF-IDF), not a learned embedding with an HNSW graph, and the
learned landmark channel G is present in the scoring function with weight 0.
Both are wired into the score so they can be replaced without touching callers.
Neither omission can lose data, because neither is a copy of anything.
"""

from __future__ import annotations

import bisect
import base64
import hashlib
import json
import math
import re
import sqlite3
import sys
import threading
import time
import zlib
from datetime import datetime
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

CODEC_VERSION = 1

# First-level chunks: "perhaps 512-2,048 model tokens worth of source bytes".
# Held in characters here because the archive must not depend on a tokenizer -
# a tokenizer change must never invalidate stored bytes.
PAGE_TARGET_CHARS = 3000
PAGE_MAX_CHARS = 6000

# Weights for R(q,p) = wl*L + ws*S + we*E + wt*T + wg*G.
WEIGHTS = {"lexical": 1.0, "semantic": 0.7, "entity": 0.5, "temporal": 0.3, "landmark": 0.0}

# "Expand rather than hallucinate around missing memory."
EXPANSION_SCHEDULE = (16, 32, 64, 128)

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_ENTITY = re.compile(
    r"\b(?:[A-Z][a-z]+(?:[A-Z][a-z]+)+"      # CamelCase
    r"|[A-Za-z_][A-Za-z0-9_]*\(\)"            # function()
    r"|[0-9a-f]{8,}"                          # hashes/ids
    r"|[A-Za-z0-9_.-]+\.[A-Za-z]{1,5}"        # filenames
    r"|[A-Z]{2,})\b"                          # ACRONYMS
)


def _now() -> float:
    return time.time()


@dataclass
class MemoryPage:
    """One immutable archive record. Mirrors the design's MemoryPage."""

    page_id: str
    conversation_id: str
    source_offset: tuple[int, int]
    timestamp: float
    codec_version: int
    parent_page: str | None
    next_page: str | None
    content_hash: str
    text: str

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class RetrievalResult:
    """Retrieved pages plus an explicit statement of confidence.

    The design insists on distinguishing "the memory does not contain it" from
    "the retriever did not confidently find it". `uncertain` carries that.
    """

    pages: list[MemoryPage] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    uncertain: bool = False
    reason: str = ""
    examined: int = 0

    def as_context(self, budget_chars: int) -> str:
        """Exact source bytes, newest-cited last, truncated by whole pages."""
        out: list[str] = []
        used = 0
        for page in self.pages:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(page.timestamp))
            block = "[memory %s | %s]\n%s" % (page.page_id[:12], stamp, page.text)
            cost = len(block) + (2 if out else 0)
            if used + cost > budget_chars:
                continue
            out.append(block)
            used += cost
        return "\n\n".join(out)


class BoundedPageCache:
    """Process-wide, byte-accounted RAM cache for exact decoded ECHO pages.

    SQLite remains the authoritative archive. This cache is only a warm tier:
    entries can always be discarded and read again from disk. The accounting
    includes Python object/string sizes plus a conservative per-entry allowance
    for the ordered-dictionary node and references.
    """

    def __init__(self, budget_bytes: int):
        self.budget_bytes = max(0, int(budget_bytes))
        self._lock = threading.RLock()
        self._pages: OrderedDict[str, tuple[MemoryPage, int]] = OrderedDict()
        self._resident_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._oversized = 0

    @staticmethod
    def page_cost(key: str, page: MemoryPage) -> int:
        values = vars(page).values()
        # Include the dataclass and its attribute dictionary, each referenced
        # field, tuple members, and a conservative allowance for the
        # OrderedDict node/references. This is an accounting budget rather
        # than a process-RSS guarantee; shared Python objects may be counted
        # more than once while allocator arenas are outside our control.
        size = 256 + sys.getsizeof(key) + sys.getsizeof(page) + sys.getsizeof(vars(page))
        for value in values:
            if value is None:
                continue
            size += sys.getsizeof(value)
            if isinstance(value, tuple):
                size += sum(sys.getsizeof(item) for item in value)
        return size

    def get(self, key: str) -> MemoryPage | None:
        with self._lock:
            entry = self._pages.get(key)
            if entry is None:
                self._misses += 1
                return None
            self._hits += 1
            self._pages.move_to_end(key)
            return entry[0]

    def put(self, key: str, page: MemoryPage) -> bool:
        if self.budget_bytes == 0:
            return False
        size = self.page_cost(key, page)
        with self._lock:
            if size > self.budget_bytes:
                self._oversized += 1
                return False
            previous = self._pages.pop(key, None)
            if previous is not None:
                self._resident_bytes -= previous[1]
            while self._pages and self._resident_bytes + size > self.budget_bytes:
                _, (_, removed_size) = self._pages.popitem(last=False)
                self._resident_bytes -= removed_size
                self._evictions += 1
            self._pages[key] = (page, size)
            self._resident_bytes += size
            return True

    def snapshot(self) -> dict:
        with self._lock:
            accesses = self._hits + self._misses
            return {
                "budget_bytes": self.budget_bytes,
                "resident_bytes": self._resident_bytes,
                "pages": len(self._pages),
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "oversized": self._oversized,
                "hit_rate": self._hits / accesses if accesses else 0.0,
            }

    def clear(self) -> None:
        with self._lock:
            self._pages.clear()
            self._resident_bytes = 0


def split_into_pages(text: str) -> list[str]:
    """Cut the text into pages at paragraph boundaries where possible.

    Every page is a slice of the original string and the slices are contiguous,
    so reassembly is exact by construction rather than by care. An earlier
    version rebuilt pages from split parts and silently dropped the separators;
    slicing removes the possibility.
    """
    if not text:
        return []
    cuts = [m.end() for m in re.finditer(r"\n\n", text)]
    pages: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        hard = min(start + PAGE_MAX_CHARS, length)
        target = start + PAGE_TARGET_CHARS
        index = bisect.bisect_left(cuts, target)
        end = cuts[index] if index < len(cuts) and cuts[index] <= hard else hard
        if end <= start:                      # pathological: force progress
            end = hard
        pages.append(text[start:end])
        start = end
    assert "".join(pages) == text, "page split lost or altered bytes"
    return pages


def _tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD.finditer(text)]


def _char_ngrams(text: str, n: int = 4) -> Counter:
    s = re.sub(r"\s+", " ", text.lower())
    return Counter(s[i:i + n] for i in range(max(0, len(s) - n + 1)))


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(count * large.get(gram, 0) for gram, count in small.items())
    if not dot:
        return 0.0
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def conversation_token(conversation_id: str) -> str:
    """A synthetic word that marks which conversation a page belongs to.

    FTS5 can only filter through MATCH, so scoping by joining to the pages
    table means matching every page in the archive and discarding most of the
    result - which measured 10.4 seconds on a 1.36B-token archive. Indexing
    this token with the page instead lets the conversation filter run inside
    the index, where it costs nothing.
    """
    return "zzc" + hashlib.sha1(conversation_id.encode()).hexdigest()[:16]


def extract_entities(text: str) -> set[str]:
    return {m.group(0) for m in _ENTITY.finditer(text)}


_STOP = {
    "the", "and", "for", "are", "was", "were", "with", "this", "that", "from",
    "what", "which", "when", "where", "who", "whom", "how", "why", "does", "did",
    "has", "have", "had", "you", "your", "our", "its", "his", "her", "their",
    "can", "could", "would", "should", "will", "shall", "may", "might", "must",
    "into", "onto", "about", "there", "here", "then", "than", "some", "any",
    "all", "not", "but", "out", "get", "got", "use", "used", "using", "make",
}


def evidence_coverage(query: str, pages: Sequence[MemoryPage]) -> float:
    """Fraction of the query's distinctive terms actually present in the pages.

    This is `evidence_is_sufficient` from the design, and it must be measured
    against the retrieved *bytes* rather than against retrieval scores. Ranking
    scores are relative - the best candidate always looks good, even when the
    archive holds nothing relevant - so scoring them would make the uncertain
    state unreachable and quietly turn this into ordinary RAG.
    """
    terms = {t for t in _tokens(query) if t not in _STOP and len(t) > 3}
    terms |= {e.lower() for e in extract_entities(query)}
    if not terms:
        return 1.0
    blob = " ".join(p.text for p in pages).lower()
    return sum(1 for t in terms if t in blob) / len(terms)


class EchoArchive:
    """Append-only exact archive plus the derived indexes over it.

    SSD L2 in the design's tier table: complete, immutable, lossless, and free
    to grow for the lifetime of the machine.
    """

    def __init__(self, path: str | Path, idle_seconds: float = 60.0,
                 cache_kib: int = 2048,
                 page_cache: BoundedPageCache | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False plus an explicit reentrant lock: the proxy
        # serves each request on its own thread, and a connection that refuses
        # cross-thread use would make the archive unusable from any server.
        # Every public method takes the lock, so only one statement runs at a
        # time; the lock is reentrant because retrieve() calls load() beneath
        # itself.
        self._lock = threading.RLock()
        self._idle_seconds = idle_seconds
        self._cache_kib = cache_kib
        self.page_cache = page_cache
        self._last_used = time.time()
        self._db: sqlite3.Connection | None = None
        self._open()
        self._create_schema()

    # -- connection lifetime ----------------------------------------------
    #
    # An archive holding a billion tokens is several gigabytes on disk, but it
    # is never read into memory: SQLite pages it in on demand. What does grow
    # is the page cache, so it is capped, and an idle archive drops the
    # connection entirely and releases the cache back to the OS. The next
    # query reopens it - opening a SQLite file is a metadata read, not a load
    # of its contents, so waking up is not proportional to archive size.

    def _open(self) -> None:
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA cache_size=-%d" % self._cache_kib)
        self._db.execute("PRAGMA mmap_size=0")

    @property
    def db(self) -> sqlite3.Connection:
        """The live connection, reopened if it was released while idle."""
        if self._db is None:
            self._open()
        self._last_used = time.time()
        return self._db

    def release_if_idle(self, now: float | None = None) -> bool:
        """Drop the connection and its cache if nothing has used it lately.

        Safe to call from a timer thread: it takes the same lock every query
        takes, so it cannot close a connection mid-statement.
        """
        with self._lock:
            if self._db is None:
                return False
            if (now or time.time()) - self._last_used < self._idle_seconds:
                return False
            try:
                # Writing leaves a write-ahead log beside the archive, and it
                # can reach gigabytes during a long ingest. Checkpointing folds
                # it back into the archive and truncates it to nothing, so an
                # idle archive occupies what its contents actually need rather
                # than what its busiest moment needed.
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._db.execute("PRAGMA optimize")
                self._db.execute("PRAGMA shrink_memory")
            except sqlite3.Error:
                pass
            self._db.close()
            self._db = None
            return True

    # -- hibernation -------------------------------------------------------
    #
    # Measured on a 1.36B-token archive: 4.2 GB of source compresses to 1.11 GB
    # of stored pages, but the file on disk is 7.5 GB. The extra ~6.4 GB is the
    # full-text index and entity postings - roughly six times the size of the
    # data they index.
    #
    # Those indexes are derived. The rule this module is built on says derived
    # representations may be discarded because they are never the only copy, so
    # an idle conversation can drop them and keep only the exact source bytes.
    # Nothing is deleted in any sense that matters: every page, hash and byte
    # survives, and the index is rebuilt from them on the next query.

    def is_partial(self) -> bool:
        """Hibernated with a hot tier: usable now, complete after a full wake."""
        with self._lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS meta "
                            "(key TEXT PRIMARY KEY, value TEXT)")
            row = self.db.execute(
                "SELECT value FROM meta WHERE key='hibernated'").fetchone()
        return bool(row and row["value"] == "partial")

    BATCH = 20000

    def _iter_page_rows(self, columns: str, locked: bool = False):
        """Walk the pages table in rowid batches, holding no more than BATCH.

        Batching by rowid rather than iterating one cursor also means the
        connection stays free between batches, so a long rebuild does not sit
        on the lock that every query needs.
        """
        last = -1
        while True:
            if locked:
                rows = self.db.execute(
                    "SELECT rowid, %s FROM pages WHERE rowid > ? "
                    "ORDER BY rowid LIMIT ?" % columns, (last, self.BATCH)).fetchall()
            else:
                with self._lock:
                    rows = self.db.execute(
                        "SELECT rowid, %s FROM pages WHERE rowid > ? "
                        "ORDER BY rowid LIMIT ?" % columns,
                        (last, self.BATCH)).fetchall()
            if not rows:
                return
            for row in rows:
                yield row
            last = rows[-1]["rowid"]

    def _hibernated(self) -> bool:
        with self._lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS meta "
                            "(key TEXT PRIMARY KEY, value TEXT)")
            row = self.db.execute("SELECT value FROM meta WHERE key='hibernated'").fetchone()
        return bool(row and row["value"] == "1")

    def hibernate(self, keep_recent_pages: int = 0) -> dict:
        """Shed the derived indexes and shrink the file. Reversible.

        With `keep_recent_pages` the newest pages keep their index, so the
        archive stays immediately searchable over recent history and only
        older material needs rebuilding. This matters at scale: a full rebuild
        runs at about 4,500 pages/second, so a trillion-token archive - 671
        million pages - would take roughly 41 hours to wake. Keeping a hot
        tier makes waking proportional to what is actually being used instead
        of to everything ever said, and `retrieve` escalates to a full wake by
        itself when the hot tier turns out not to hold the answer.
        """
        before = self.disk_usage()["total_bytes"]
        started = time.time()
        with self._lock:
            if self._hibernated():
                return {"already": True, "bytes": before}
            self.db.execute("DROP TABLE IF EXISTS pages_fts")
            self.db.execute("DELETE FROM entities")
            # These two are ordinary b-tree indexes over columns that remain in
            # the table, so they are derived exactly like the full-text index
            # and _create_schema puts them back on wake.
            self.db.execute("DROP INDEX IF EXISTS pages_conv")
            self.db.execute("DROP INDEX IF EXISTS pages_time")
            self.db.execute("DROP INDEX IF EXISTS entities_page")
            hot = 0
            if keep_recent_pages > 0:
                self._create_schema()
                rows = self.db.execute(
                    "SELECT page_id, conversation_id FROM pages "
                    "ORDER BY rowid DESC LIMIT ?", (keep_recent_pages,)).fetchall()
                for row in rows:
                    page = self.load(row["page_id"])
                    if page is None:
                        continue
                    self.db.execute(
                        "INSERT INTO pages_fts (text, page_id) VALUES (?,?)",
                        (page.text + " " + conversation_token(row["conversation_id"]),
                         page.page_id))
                    for entity in extract_entities(page.text):
                        self.db.execute("INSERT OR IGNORE INTO entities VALUES (?,?)",
                                        (entity, page.page_id))
                    hot += 1
            state = "partial" if hot else "1"
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('hibernated',?)", (state,))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('hot_pages',?)", (str(hot),))
            self.db.commit()
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.execute("VACUUM")
            self.db.commit()
        after = self.disk_usage()["total_bytes"]
        return {"before_bytes": before, "after_bytes": after,
                "reclaimed_bytes": before - after,
                "seconds": time.time() - started}

    def wake(self) -> dict:
        """Rebuild the indexes from the source pages. Costs time, not data."""
        if not self._hibernated():
            return {"already_awake": True}
        started = time.time()
        rebuilt = 0
        with self._lock:
            self._create_schema()
            for row in self._iter_page_rows("page_id, conversation_id", locked=True):
                page = self.load(row["page_id"])
                if page is None:
                    continue
                self.db.execute(
                    "INSERT INTO pages_fts (text, page_id) VALUES (?,?)",
                    (page.text + " " + conversation_token(row["conversation_id"]),
                     page.page_id))
                for entity in extract_entities(page.text):
                    self.db.execute("INSERT OR IGNORE INTO entities VALUES (?,?)",
                                    (entity, page.page_id))
                rebuilt += 1
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('hibernated','0')")
            self.db.commit()
            # A bulk rebuild leaves the index as many small b-tree segments,
            # which measured larger on disk than the original incrementally
            # built index (1.32 GB against 0.75 GB). Merging them costs seconds
            # and gives the space back.
            try:
                self.db.execute("INSERT INTO pages_fts (pages_fts) VALUES ('optimize')")
                self.db.commit()
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        return {"pages_reindexed": rebuilt, "seconds": time.time() - started,
                "bytes": self.disk_usage()["total_bytes"]}

    def ensure_awake(self) -> None:
        """Wake transparently, so callers never see a hibernated archive."""
        if self._hibernated():
            self.wake()

    def roll_window(self, conversation_id: str, keep_tokens: int,
                    cold_path: str | Path | None = None,
                    chars_per_token: float = 3.10,
                    delete_instead: bool = False) -> dict:
        """Keep the newest `keep_tokens` here; move everything older aside.

        The hot archive stays small and fast while the conversation keeps
        growing. By default the older pages are moved to a cold archive file -
        same schema, same hashes, still exact - rather than deleted, because a
        summary of them is lossy and the rule this module is built on forbids a
        lossy thing from becoming the only surviving copy.

        `delete_instead=True` really does destroy them. It exists because the
        caller may genuinely want the space back, but it is never the default
        and the count of destroyed pages is returned so the loss is visible.
        """
        budget = int(keep_tokens * chars_per_token)
        with self._lock:
            rows = self.db.execute(
                "SELECT page_id, offset_end - offset_start AS size FROM pages "
                "WHERE conversation_id=? ORDER BY rowid DESC", (conversation_id,)).fetchall()
        # Walk newest-first and stop at the first page that does not fit.
        # Everything past that point is old, whatever its size. Testing
        # "does this page fit in what is left" instead let a small page from
        # the distant past slip back into the recent window - rowid 12
        # survived alongside rowids 333-402 - which makes the window a
        # scattering of pages rather than a window.
        kept_bytes = 0
        cold: list[str] = []
        past_edge = False
        for row in rows:
            if not past_edge and kept_bytes + row["size"] <= budget:
                kept_bytes += row["size"]
                continue
            past_edge = True
            cold.append(row["page_id"])
        if not cold:
            return {"moved": 0, "kept_pages": len(rows), "kept_bytes": kept_bytes,
                    "note": "everything already fits inside the window"}

        moved = 0
        if not delete_instead:
            target = Path(cold_path) if cold_path else \
                self.path.with_name(self.path.stem + "-cold" + self.path.suffix)
            cold_archive = EchoArchive(target, page_cache=self.page_cache)
            try:
                for page_id in cold:
                    page = self.load(page_id)
                    if page is None:
                        continue
                    # Re-appending preserves the bytes and re-derives the
                    # indexes in the cold file; hashes are recomputed from the
                    # same text, so corruption cannot pass silently.
                    cold_archive.append(page.text, conversation_id, page.timestamp)
                    moved += 1
            finally:
                cold_archive.close()

        with self._lock:
            for page_id in cold:
                self.db.execute("DELETE FROM pages WHERE page_id=?", (page_id,))
                self.db.execute("DELETE FROM entities WHERE page_id=?", (page_id,))
                try:
                    self.db.execute("DELETE FROM pages_fts WHERE page_id=?", (page_id,))
                except sqlite3.Error:
                    pass
            self.db.commit()
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.execute("VACUUM")
            self.db.commit()

        return {"moved": moved, "destroyed": len(cold) if delete_instead else 0,
                "kept_pages": len(rows) - len(cold), "kept_bytes": kept_bytes,
                "cold_archive": None if delete_instead else str(target),
                "bytes_now": self.disk_usage()["total_bytes"]}

    def compact(self) -> dict:
        """Reclaim free pages on disk. Slow, so it is never automatic.

        VACUUM rewrites the whole file, which costs roughly the archive's size
        in reads and writes - acceptable occasionally, not after every idle
        period. `release_if_idle` does the cheap part (checkpointing the WAL)
        every time; this is here for when the archive has had a lot deleted or
        rewritten, which for an append-only store is rare by design.
        """
        before = self.disk_usage()["total_bytes"]
        with self._lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.execute("VACUUM")
        after = self.disk_usage()["total_bytes"]
        return {"before_bytes": before, "after_bytes": after,
                "reclaimed_bytes": before - after}

    def disk_usage(self) -> dict:
        """Bytes actually on the SSD, archive plus its sidecar files."""
        parts = {}
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            parts[suffix or "db"] = candidate.stat().st_size if candidate.exists() else 0
        parts["total_bytes"] = sum(v for k, v in parts.items() if k != "total_bytes")
        return parts

    def _create_schema(self) -> None:
        cur = self.db
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pages (
                page_id          TEXT PRIMARY KEY,
                conversation_id  TEXT NOT NULL,
                offset_start     INTEGER NOT NULL,
                offset_end       INTEGER NOT NULL,
                timestamp        REAL NOT NULL,
                codec_version    INTEGER NOT NULL,
                parent_page      TEXT,
                next_page        TEXT,
                content_hash     TEXT NOT NULL,
                compressed_bytes BLOB NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS pages_conv ON pages(conversation_id, timestamp)")
        cur.execute("CREATE INDEX IF NOT EXISTS pages_time ON pages(timestamp)")
        # Derived, rebuildable: lexical postings.
        cur.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5("
            "  text, page_id UNINDEXED, tokenize='unicode61')"
        )
        # Derived, rebuildable: entity postings.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS entities ("
            "  entity TEXT NOT NULL, page_id TEXT NOT NULL,"
            "  PRIMARY KEY (entity, page_id)) WITHOUT ROWID"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS entities_page ON entities(page_id)")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS derived_summaries ("
            "summary_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
            "generated_at REAL NOT NULL, source_pages INTEGER NOT NULL, "
            "model_calls INTEGER NOT NULL, truncated INTEGER NOT NULL, "
            "incomplete INTEGER NOT NULL, content TEXT NOT NULL)"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS derived_summaries_conv ON derived_summaries(conversation_id, generated_at)")
        # Lossless event structure lives beside the paged text index. The text
        # index is useful for retrieval, but cannot represent tool calls or files.
        cur.execute("CREATE TABLE IF NOT EXISTS source_events ("
                    "event_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
                    "timestamp REAL NOT NULL, kind TEXT NOT NULL, role TEXT NOT NULL, "
                    "source TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, "
                    "metadata TEXT NOT NULL)")
        cur.execute("CREATE INDEX IF NOT EXISTS source_events_conv ON source_events(conversation_id,timestamp)")
        cur.execute("CREATE TABLE IF NOT EXISTS source_assets ("
                    "asset_id TEXT PRIMARY KEY, mime TEXT NOT NULL, bytes BLOB NOT NULL)")
        cur.execute("CREATE TABLE IF NOT EXISTS source_event_assets ("
                    "event_id TEXT NOT NULL, asset_id TEXT NOT NULL, name TEXT NOT NULL, "
                    "PRIMARY KEY(event_id,asset_id)) WITHOUT ROWID")
        self.db.commit()

    def save_summary(self, conversation_id: str, content: str, source_pages: int,
                     model_calls: int, truncated: bool = False,
                     incomplete: bool = False) -> str:
        """Store a generated view separately from immutable source pages."""
        stamp = _now()
        summary_id = hashlib.sha256((conversation_id + "\0" + str(stamp) + "\0" + content).encode()).hexdigest()
        with self._lock:
            self.db.execute("INSERT INTO derived_summaries VALUES (?,?,?,?,?,?,?,?)",
                            (summary_id, conversation_id, stamp, source_pages,
                             model_calls, int(truncated), int(incomplete), content))
            self.db.commit()
        return summary_id

    def record_source_event(self, message: dict, conversation_id: str) -> bool:
        """Keep a typed event and its available image bytes in the exact archive.

        Returns True only when a new event is recorded. Existing text pages from
        older releases stay in place; reindexing adds structure without copying
        their transcript bytes a second time.
        """
        role = message.get("role")
        if role not in ("user", "assistant", "tool", "system", "developer"):
            return False
        kind = str(message.get("kind") or "message")[:64]
        source = str(message.get("source") or "OpenCore")[:128]
        title = str(message.get("title") or "")[:256]
        metadata = message.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        source_id = message.get("source_event_id")
        content = message.get("content")
        raw_content = content if isinstance(content, str) else "" if content is None else json.dumps(content, ensure_ascii=False, sort_keys=True)
        identity = str(source_id) if source_id is not None else hashlib.sha256(
            (role + "\0" + kind + "\0" + raw_content).encode()).hexdigest()
        event_id = hashlib.sha256((conversation_id + "\0" + identity).encode()).hexdigest()

        def preserve_image(data_url: str) -> str:
            if not isinstance(data_url, str) or not data_url.startswith("data:image/") or ";base64," not in data_url:
                return data_url
            header, encoded = data_url.split(",", 1)
            if len(encoded) > 24 * 1024 * 1024:
                return "[image exceeds archive asset limit]"
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, base64.binascii.Error):
                return "[invalid image data]"
            mime = header[5:].split(";", 1)[0]
            if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                return "[unsupported image format]"
            asset_id = hashlib.sha256(data).hexdigest()
            self.db.execute("INSERT OR IGNORE INTO source_assets VALUES (?,?,?)", (asset_id, mime, data))
            return "echo-asset:" + asset_id

        def link_assets(items) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                reference = item.get("asset", "")
                if isinstance(reference, str) and reference.startswith("echo-asset:"):
                    self.db.execute("INSERT OR IGNORE INTO source_event_assets VALUES (?,?,?)",
                                    (event_id, reference[len("echo-asset:"):], str(item.get("name", "Image"))[:256]))

        with self._lock:
            inline_assets = []
            if "data:image/" in raw_content or "data:image/" in str(metadata):
                for raw in (raw_content, json.dumps(metadata, ensure_ascii=False)):
                    for found in re.finditer(r"data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+", raw):
                        reference = preserve_image(found.group(0))
                        if reference.startswith("echo-asset:") and not any(item["asset"] == reference for item in inline_assets):
                            inline_assets.append({"name": "Image in source event", "asset": reference})
                        if len(inline_assets) >= 8:
                            break
                    if len(inline_assets) >= 8:
                        break
            existing = self.db.execute("SELECT metadata FROM source_events WHERE event_id=?", (event_id,)).fetchone()
            if existing:
                if inline_assets:
                    prior = json.loads(existing["metadata"])
                    prior_assets = prior.get("assets", [])
                    known = {item.get("asset") for item in prior_assets if isinstance(item, dict)}
                    new_assets = [item for item in inline_assets if item["asset"] not in known]
                    if new_assets:
                        prior_assets.extend(new_assets)
                        prior["assets"] = prior_assets
                        self.db.execute("UPDATE source_events SET metadata=? WHERE event_id=?",
                                        (json.dumps(prior, ensure_ascii=False, sort_keys=True), event_id))
                        self.db.commit()
                    link_assets(inline_assets)
                    self.db.commit()
                return False
            if isinstance(content, list):
                clean_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        image = part.get("image_url")
                        url = image.get("url") if isinstance(image, dict) else image
                        clean_parts.append({"type": "image_url", "asset": preserve_image(url)})
                    else:
                        clean_parts.append(part)
                content = clean_parts
            assets = message.get("assets")
            if isinstance(assets, list):
                metadata["assets"] = [{"name": str(item.get("name", "image"))[:256],
                                       "asset": preserve_image(item.get("data_url", ""))}
                                      for item in assets if isinstance(item, dict)]
            if inline_assets:
                known = {item.get("asset") for item in metadata.get("assets", []) if isinstance(item, dict)}
                metadata.setdefault("assets", []).extend(item for item in inline_assets if item["asset"] not in known)
            link_assets(metadata.get("assets", []))
            stamp = message.get("timestamp")
            if isinstance(stamp, str):
                try:
                    stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    stamp = None
            stamp = float(stamp) if isinstance(stamp, (float, int)) else time.time()
            stored_content = content if isinstance(content, str) else "" if content is None else json.dumps(content, ensure_ascii=False, sort_keys=True)
            self.db.execute("INSERT INTO source_events VALUES (?,?,?,?,?,?,?,?,?)",
                            (event_id, conversation_id, stamp, kind, role, source, title,
                             stored_content, json.dumps(metadata, ensure_ascii=False, sort_keys=True)))
            if isinstance(content, list):
                page_content = "\n".join(str(part.get("text") or part.get("asset") or "")
                                         for part in content if isinstance(part, dict))
            else:
                page_content = raw_content
            for attached in metadata.get("assets", []):
                if isinstance(attached, dict):
                    page_content += "\n[Attached image: %s · %s]" % (
                        attached.get("name", "image"), attached.get("asset", "unavailable"))
            if message.get("tool_calls"):
                page_content += "\n" + json.dumps(message["tool_calls"], ensure_ascii=False)
            page_text = role + ": " + page_content
            if page_content.strip():
                legacy_identity = (str(source_id) + "\0" + page_text) if source_id is not None else page_text
                fingerprint = hashlib.sha256((conversation_id + "\0" + legacy_identity).encode()).hexdigest()
                self.db.execute("CREATE TABLE IF NOT EXISTS imported_messages (fingerprint TEXT PRIMARY KEY)")
                if not self.db.execute("SELECT 1 FROM imported_messages WHERE fingerprint=?", (fingerprint,)).fetchone():
                    text_fingerprint = hashlib.sha256((conversation_id + "\0" + page_text).encode()).hexdigest()
                    if not self.db.execute("SELECT 1 FROM imported_messages WHERE fingerprint=?", (text_fingerprint,)).fetchone():
                        self.append(page_text, conversation_id, stamp)
                    self.db.execute("INSERT INTO imported_messages VALUES (?)", (fingerprint,))
            self.db.commit()
        return True

    # ---------------------------------------------------------------- writing

    def append(self, text: str, conversation_id: str = "default",
               timestamp: float | None = None) -> list[MemoryPage]:
        """Write source bytes to the archive. Never overwrites, never edits."""
        if not text.strip():
            return []
        stamp = _now() if timestamp is None else timestamp
        self.ensure_awake()
        with self._lock:
            return self._append_locked(text, conversation_id, stamp)

    def _append_locked(self, text: str, conversation_id: str,
                       stamp: float) -> list[MemoryPage]:
        row = self.db.execute(
            "SELECT page_id, offset_end FROM pages WHERE conversation_id=? "
            "ORDER BY offset_end DESC LIMIT 1", (conversation_id,)).fetchone()
        parent = row["page_id"] if row else None
        offset = row["offset_end"] if row else 0

        written: list[MemoryPage] = []
        for chunk in split_into_pages(text):
            raw = chunk.encode("utf-8")
            content_hash = hashlib.sha256(raw).hexdigest()
            page_id = hashlib.sha256(
                (content_hash + conversation_id + repr(offset)).encode()).hexdigest()
            page = MemoryPage(
                page_id=page_id,
                conversation_id=conversation_id,
                source_offset=(offset, offset + len(raw)),
                timestamp=stamp,
                codec_version=CODEC_VERSION,
                parent_page=parent,
                next_page=None,
                content_hash=content_hash,
                text=chunk,
            )
            self.db.execute(
                "INSERT OR IGNORE INTO pages VALUES (?,?,?,?,?,?,?,?,?,?)",
                (page.page_id, conversation_id, offset, offset + len(raw), stamp,
                 CODEC_VERSION, parent, None, content_hash,
                 zlib.compress(raw, 6)),
            )
            if parent is not None:
                self.db.execute("UPDATE pages SET next_page=? WHERE page_id=? AND next_page IS NULL",
                                (page.page_id, parent))
            # The marker is appended to the indexed copy only; the page text
            # itself is untouched in the pages table, which is the sole source.
            self.db.execute(
                "INSERT INTO pages_fts (text, page_id) VALUES (?,?)",
                (chunk + " " + conversation_token(conversation_id), page.page_id))
            for entity in extract_entities(chunk):
                self.db.execute("INSERT OR IGNORE INTO entities VALUES (?,?)",
                                (entity, page.page_id))
            parent = page.page_id
            offset += len(raw)
            written.append(page)
        self.db.commit()
        return written

    # ---------------------------------------------------------------- reading

    def load(self, page_id: str) -> MemoryPage | None:
        """Load exact source bytes and verify them against the stored hash."""
        cache_key = str(self.path.resolve()) + "\0" + page_id
        if self.page_cache is not None:
            cached = self.page_cache.get(cache_key)
            if cached is not None:
                return cached
        with self._lock:
            row = self.db.execute("SELECT * FROM pages WHERE page_id=?",
                                  (page_id,)).fetchone()
        if row is None:
            return None
        raw = zlib.decompress(row["compressed_bytes"])
        if hashlib.sha256(raw).hexdigest() != row["content_hash"]:
            raise IOError("ECHO archive corruption at page %s" % page_id)
        page = MemoryPage(
            page_id=row["page_id"], conversation_id=row["conversation_id"],
            source_offset=(row["offset_start"], row["offset_end"]),
            timestamp=row["timestamp"], codec_version=row["codec_version"],
            parent_page=row["parent_page"], next_page=row["next_page"],
            content_hash=row["content_hash"], text=raw.decode("utf-8"),
        )
        if self.page_cache is not None:
            self.page_cache.put(cache_key, page)
        return page

    def stats(self) -> dict:
        with self._lock:
            row = self.db.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(offset_end-offset_start),0) b, "
                "COALESCE(SUM(LENGTH(compressed_bytes)),0) c FROM pages").fetchone()
            entities = self.db.execute(
                "SELECT COUNT(*) c FROM entities").fetchone()["c"]
        return {"pages": row["n"], "source_bytes": row["b"],
                "stored_bytes": row["c"], "entities": entities}

    # -------------------------------------------------------------- retrieval

    def _lexical(self, query: str, limit: int,
                 conversation_id: str | None = None) -> dict[str, float]:
        terms = _tokens(query)
        if not terms:
            return {}
        match = " OR ".join(sorted(set(terms)))
        try:
            if conversation_id is None:
                rows = self.db.execute(
                    "SELECT page_id, bm25(pages_fts) AS score FROM pages_fts "
                    "WHERE pages_fts MATCH ? ORDER BY score LIMIT ?",
                    (match, limit)).fetchall()
            else:
                # Scoped to one conversation. Without this, a query in one
                # conversation can return another conversation's pages, which
                # is a correctness problem long before it is a privacy one.
                scoped = "(%s) AND %s" % (match, conversation_token(conversation_id))
                rows = self.db.execute(
                    "SELECT page_id, bm25(pages_fts) AS score FROM pages_fts "
                    "WHERE pages_fts MATCH ? ORDER BY score LIMIT ?",
                    (scoped, limit)).fetchall()
        except sqlite3.OperationalError:
            return {}
        # bm25() is negative-better in SQLite. Normalising by the best score
        # alone compressed the range badly: a page holding the only occurrence
        # of a term in 914,554 pages scored 1.00 while pages matching just the
        # common words scored 0.83, and the 0.30 recency channel then
        # outranked the exact match. Min-max over the candidate set keeps the
        # separation that bm25's IDF term already computed, so rare-term
        # evidence survives contact with the other channels.
        out: dict[str, float] = {}
        if not rows:
            return out
        scores = [r["score"] for r in rows]
        best, worst = min(scores), max(scores)
        spread = worst - best
        for r in rows:
            out[r["page_id"]] = 1.0 if spread <= 0 else (worst - r["score"]) / spread
        return out

    def _entity(self, query: str, limit: int,
                conversation_id: str | None = None) -> dict[str, float]:
        ents = extract_entities(query)
        if not ents:
            return {}
        marks = ",".join("?" * len(ents))
        if conversation_id is None:
            rows = self.db.execute(
                "SELECT page_id, COUNT(*) hits FROM entities WHERE entity IN (%s) "
                "GROUP BY page_id ORDER BY hits DESC LIMIT ?" % marks,
                (*ents, limit)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT entities.page_id AS page_id, COUNT(*) hits FROM entities "
                "JOIN pages ON pages.page_id = entities.page_id "
                "WHERE entities.entity IN (%s) AND pages.conversation_id = ? "
                "GROUP BY entities.page_id ORDER BY hits DESC LIMIT ?" % marks,
                (*ents, conversation_id, limit)).fetchall()
        return {r["page_id"]: r["hits"] / len(ents) for r in rows}

    def _temporal(self, page_ids: Iterable[str]) -> dict[str, float]:
        ids = list(page_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.db.execute(
            "SELECT page_id, timestamp FROM pages WHERE page_id IN (%s)" % marks, ids).fetchall()
        if not rows:
            return {}
        newest = max(r["timestamp"] for r in rows)
        oldest = min(r["timestamp"] for r in rows)
        span = max(1.0, newest - oldest)
        return {r["page_id"]: (r["timestamp"] - oldest) / span for r in rows}

    def _recent(self, conversation_id: str, limit: int) -> dict[str, float]:
        rows = self.db.execute(
            "SELECT page_id FROM pages WHERE conversation_id=? ORDER BY timestamp DESC LIMIT ?",
            (conversation_id, limit)).fetchall()
        return {r["page_id"]: 1.0 for r in rows}

    def hybrid_search(self, query: str, k: int,
                      conversation_id: str | None = None) -> dict[str, float]:
        """R(q,p) over the union of every channel. Missing channels score 0."""
        self.ensure_awake()
        with self._lock:
            return self._hybrid_search_locked(query, k, conversation_id)

    def _hybrid_search_locked(self, query: str, k: int,
                              conversation_id: str | None) -> dict[str, float]:
        pool = max(k * 4, 32)
        lexical = self._lexical(query, pool, conversation_id)
        entity = self._entity(query, pool, conversation_id)
        candidates = set(lexical) | set(entity)
        if conversation_id and len(candidates) < k:
            candidates |= set(self._recent(conversation_id, pool))
        if not candidates:
            return {}

        temporal = self._temporal(candidates)
        q_grams = _char_ngrams(query)
        scored: dict[str, float] = {}
        for page_id in candidates:
            page = self.load(page_id)
            if page is None:
                continue
            semantic = _cosine(q_grams, _char_ngrams(page.text))
            scored[page_id] = (
                WEIGHTS["lexical"] * lexical.get(page_id, 0.0)
                + WEIGHTS["semantic"] * semantic
                + WEIGHTS["entity"] * entity.get(page_id, 0.0)
                + WEIGHTS["temporal"] * temporal.get(page_id, 0.0)
                + WEIGHTS["landmark"] * 0.0
            )
        return dict(sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:k])

    def retrieve(self, query: str, initial_k: int = 8,
                 conversation_id: str | None = None,
                 sufficiency: float = 0.55) -> RetrievalResult:
        """Progressive retrieval, per the design's retrieve_exact_memory().

        Expands instead of pretending, and returns an explicitly uncertain
        result rather than a confident-looking one, when it never got there.
        """
        examined = 0
        scored = self.hybrid_search(query, initial_k, conversation_id)
        examined += len(scored)
        result = self._materialise(scored, examined, uncertain=False, reason="")
        coverage = evidence_coverage(query, result.pages)
        if scored and coverage >= sufficiency:
            result.reason = "evidence coverage %.2f at initial k=%d" % (coverage, initial_k)
            return result

        for k in EXPANSION_SCHEDULE:
            wider = self.hybrid_search(query, k, conversation_id)
            if not wider:
                continue
            examined = max(examined, len(wider))
            scored = wider
            result = self._materialise(scored, examined, uncertain=False, reason="")
            coverage = evidence_coverage(query, result.pages)
            if coverage >= sufficiency:
                result.reason = "evidence coverage %.2f after expansion to k=%d" % (coverage, k)
                return result

        # Before admitting defeat, check whether the archive is only partly
        # indexed. A hot tier that does not contain the answer is not evidence
        # that the archive does not contain it, and reporting uncertainty here
        # would confuse "not indexed yet" with "not remembered" - the exact
        # distinction this class exists to preserve.
        if self.is_partial():
            self.wake()
            return self.retrieve(query, initial_k, conversation_id, sufficiency)

        if not scored:
            return RetrievalResult(uncertain=True, examined=examined,
                                   reason="retriever found no candidate pages")

        result.uncertain = True
        result.reason = ("retriever did not reach sufficiency: evidence coverage "
                         "%.2f < %.2f after expanding to k=%d"
                         % (coverage, sufficiency, EXPANSION_SCHEDULE[-1]))
        return result

    def _materialise(self, scored: dict[str, float], examined: int,
                     uncertain: bool, reason: str) -> RetrievalResult:
        pages = [p for p in (self.load(pid) for pid in scored) if p is not None]
        pages.sort(key=lambda p: scored[p.page_id], reverse=True)
        return RetrievalResult(pages=pages, scores=dict(scored),
                               uncertain=uncertain, reason=reason, examined=examined)

    def find_word(self, term: str, conversation_id: str | None = None,
                  context_chars: int = 320, limit: int = 12) -> list[dict]:
        """Every place a word appears, with the text around it.

        Retrieval returns whole pages ranked by relevance, which answers "what
        is relevant to this question". This answers a different one: "where
        have I used this word, and what did I say around it". A model writing
        at length needs the second to stay consistent with itself - it cannot
        hold a million pages in mind, but it can look up what it already wrote
        about dogs before writing more about dogs.
        """
        term = term.strip()
        if not term:
            return []
        self.ensure_awake()
        with self._lock:
            match = term if " " not in term else '"%s"' % term
            if conversation_id:
                match = "(%s) AND %s" % (match, conversation_token(conversation_id))
            try:
                rows = self.db.execute(
                    "SELECT page_id FROM pages_fts WHERE pages_fts MATCH ? "
                    "ORDER BY bm25(pages_fts) LIMIT ?",
                    (match, limit * 3)).fetchall()
            except sqlite3.OperationalError:
                return []

        needle = re.compile(re.escape(term), re.I)
        out: list[dict] = []
        for row in rows:
            page = self.load(row["page_id"])
            if page is None:
                continue
            for hit in needle.finditer(page.text):
                start = max(0, hit.start() - context_chars // 2)
                end = min(len(page.text), hit.end() + context_chars // 2)
                out.append({
                    "page_id": page.page_id,
                    "when": time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime(page.timestamp)),
                    "snippet": ("..." if start else "") + page.text[start:end]
                               + ("..." if end < len(page.text) else ""),
                })
                if len(out) >= limit:
                    return out
        return out

    def recent_pages(self, conversation_id: str, limit: int = 8) -> list[MemoryPage]:
        """The tail of a conversation, oldest-first, regardless of relevance.

        Retrieval answers "what is relevant"; this answers "where were we".
        They are different questions and a thread resumed on relevance alone
        reads as though the model has forgotten the last thing that was said -
        which it has, because nothing scored it as relevant.
        """
        with self._lock:
            rows = self.db.execute(
                "SELECT page_id FROM pages WHERE conversation_id=? "
                "ORDER BY timestamp DESC, offset_end DESC LIMIT ?",
                (conversation_id, limit)).fetchall()
        pages = [p for p in (self.load(r["page_id"]) for r in rows) if p is not None]
        pages.reverse()
        return pages

    def verify(self) -> dict:
        """Re-hash every page. The archive's only real guarantee, checked."""
        # Streamed in rowid batches. Collecting every page_id first needed
        # memory proportional to the archive - about 40 GB at a trillion
        # tokens - which would have made the ceiling this code's own, not the
        # disk's.
        ok = bad = 0
        for row in self._iter_page_rows("page_id"):
            try:
                self.load(row["page_id"])
                ok += 1
            except IOError:
                bad += 1
        return {"verified": ok, "corrupt": bad}

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
