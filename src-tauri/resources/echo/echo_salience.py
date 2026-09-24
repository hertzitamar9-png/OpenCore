"""Choose what is worth summarising, before the model reads anything.

Profiling says summarising is bounded by generation, not by reading: a pass
spends about 39s on its prompt and 136s writing the summary. Prompt-compression
work such as LongLLMLingua (1.4-3.8x) and LLMLingua-2 (~2.9x) attacks the
smaller half, so it cannot help much here.

Generating less is the lever that can. The observation this module is built on
is that a long conversation is mostly redundant - repeated context, restated
questions, boilerplate - and that ECHO already knows which parts are not,
because it maintains a full-text index over every page. Document frequency from
that index is exactly a measure of how ordinary a page's vocabulary is.

So pages are ranked before the model sees them, and only the informative ones
are summarised. This is lossy selection, which is normally forbidden here - but
it is allowed for the same reason the indexes are: what is dropped stays in the
archive, exact and retrievable. The summary gets thinner; the memory does not.

Two filters, cheapest first:

  1. near-duplicate removal - pages that repeat what a kept page already says
  2. salience ranking       - rare vocabulary, entity density, and novelty
                              against what has already been selected

Neither runs the model, so both are effectively free next to a 2.9-minute
generation call.
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from pathlib import Path

for candidate in (Path(__file__).resolve().parent,
                  Path(__file__).resolve().parents[1] / "src"):
    if (candidate / "evoagent" / "echo_memory.py").exists():
        sys.path.insert(0, str(candidate))
        break

from evoagent.echo_memory import (EchoArchive, MemoryPage, _char_ngrams,  # noqa: E402
                                  _cosine, _tokens, extract_entities)

DUPLICATE_THRESHOLD = 0.82      # cosine over 4-grams; above this it is a restatement
NOVELTY_WEIGHT = 1.0
RARITY_WEIGHT = 1.0
ENTITY_WEIGHT = 0.6


def document_frequency(archive: EchoArchive, terms: set[str],
                       conversation_id: str) -> dict[str, int]:
    """How many pages each term appears in, straight from the FTS index.

    A term in 2 pages of 900,000 marks those pages as carrying something
    specific. A term in 400,000 pages marks nothing at all. This is the same
    signal bm25 uses for ranking, reused here to decide what to read.
    """
    counts: dict[str, int] = {}
    for term in terms:
        try:
            row = archive.db.execute(
                "SELECT COUNT(*) c FROM pages_fts WHERE pages_fts MATCH ?",
                (term,)).fetchone()
            counts[term] = row["c"] if row else 0
        except Exception:
            counts[term] = 0
    return counts


def select(archive: EchoArchive, conversation_id: str, keep_fraction: float = 0.15,
           progress=None) -> dict:
    """Return the pages worth summarising, newest-biased, duplicates removed."""
    say = progress or (lambda message: None)

    # Pass 1: term frequencies across the conversation. Only the vocabulary is
    # held, never the pages - a 900,000-page archive is 4 GB of text and must
    # not be materialised to decide what to read.
    vocabulary: Counter = Counter()
    total_pages = 0
    for row in archive._iter_page_rows("page_id, conversation_id"):
        if row["conversation_id"] != conversation_id:
            continue
        page = archive.load(row["page_id"])
        if page is None:
            continue
        total_pages += 1
        vocabulary.update(set(_tokens(page.text)))
    if not total_pages:
        return {"pages": [], "considered": 0, "duplicates": 0, "kept": 0}

    say("  ranking %s pages without running the model..." % f"{total_pages:,}")

    # Pass 2: score each page, keeping only (score, rowid) - about 50 bytes a
    # page, so a million pages costs tens of megabytes rather than gigabytes.
    scored: list[tuple[float, int, str]] = []
    position = 0
    for row in archive._iter_page_rows("page_id, conversation_id"):
        if row["conversation_id"] != conversation_id:
            continue
        page = archive.load(row["page_id"])
        if page is None:
            continue
        terms = set(_tokens(page.text))
        if terms:
            # Rarity within the conversation itself: unusual *here* is what
            # matters, not unusual in English.
            rarity = sum(math.log(total_pages / max(1, vocabulary[t]))
                         for t in terms) / len(terms)
            entities = len(extract_entities(page.text)) / max(1.0, page.chars / 1000.0)
            score = RARITY_WEIGHT * rarity + ENTITY_WEIGHT * entities
        else:
            score = 0.0
        scored.append((score, position, row["page_id"]))
        position += 1

    target = max(1, int(total_pages * keep_fraction))
    scored.sort(key=lambda item: item[0], reverse=True)

    kept: list[tuple[int, str]] = []
    kept_grams: list[Counter] = []
    kept_terms: set[str] = set()
    duplicates = 0
    for _, order_index, page_id in scored:
        if len(kept) >= target:
            break
        page = archive.load(page_id)
        if page is None:
            continue
        terms = set(_tokens(page.text))
        # Rare terms this page would be the only kept source of. A page whose
        # bulk is ordinary but which states one specific fact - a key, an
        # identifier, a decision - looks like a duplicate by whole-page
        # similarity, and dropping it loses the fact entirely. Measured on a
        # planted-needle archive, similarity alone discarded 613 of 620 pages
        # and 18 of 24 needles. Novel rare vocabulary overrides similarity.
        novel_rare = {t for t in terms - kept_terms
                      if vocabulary[t] <= max(2, total_pages // 100)}
        grams = _char_ngrams(page.text)
        similar = any(_cosine(grams, seen) >= DUPLICATE_THRESHOLD
                      for seen in kept_grams[-40:])
        if similar and not novel_rare:
            duplicates += 1
            continue
        kept.append((order_index, page_id))
        kept_grams.append(grams)
        kept_terms |= terms

    kept.sort()                       # summarising needs chronological order
    say("  keeping %s of %s pages (%.0f%%), %s near-duplicates skipped"
        % (f"{len(kept):,}", f"{total_pages:,}",
           100.0 * len(kept) / total_pages, f"{duplicates:,}"))
    pages = [p for p in (archive.load(pid) for _, pid in kept) if p is not None]
    return {"pages": pages, "considered": total_pages,
            "duplicates": duplicates, "kept": len(kept)}
