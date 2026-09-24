"""Summarise an ECHO conversation, however long it is.

Used two ways: the proxy calls it when a message begins with /summarize, and
Summarize-Conversation.cmd calls it interactively.

A summary is lossy, so it is treated the way this project treats every lossy
thing - as a derived view that points at the archive and never replaces it.
Nothing is deleted, nothing is rewritten, and the summary is handed back (or
written to a separate .md file) rather than stored as though it were memory.

Long conversations do not fit in the model's window, so summarising is done in
two passes: each batch of pages is summarised on its own, then those summaries
are summarised together, recursing while the combined text is still too large.
That is lossy twice over, which is the honest cost of the operation and is
reported rather than hidden - the result states how many pages were read, how
many model calls it took, and how many rounds were needed.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

for candidate in (Path(__file__).resolve().parent,
                  Path(__file__).resolve().parents[1] / "src"):
    if (candidate / "evoagent" / "echo_memory.py").exists():
        sys.path.insert(0, str(candidate))
        break

from evoagent.echo_memory import EchoArchive  # noqa: E402
from echo_salience import select as select_salient  # noqa: E402

# How much of the model's window one summarisation pass may occupy.
#
# This used to be a flat 18,000 characters - about 5,800 tokens, or 18% of a
# 32k window - which meant roughly five times more model calls than necessary.
# Comparable tools fill far more: Claude Code auto-compacts at roughly 83-95%
# of the window, Codex CLI applies an effective_context_window_percent of 95%,
# and OpenCode uses the clearest rule of the three: compact when tokens exceed
# (context_limit - output_limit). That last formula is what is implemented
# here, with the fill fraction left explicit rather than buried.
CONTEXT_FILL = 0.90

# How hard each pass compresses. The output used to be a flat 1,500 tokens
# regardless of how much was read, which is why a huge conversation collapsed
# into a blurb: 18.7x compression every round, applied repeatedly.
#
# Input and output share one window, so the summary has to be smaller than what
# it read or the process never converges - a "compression" of 1.0 would loop
# forever. Within that constraint the summary is now sized from the input
# rather than fixed: at 4x the model writes about 5,900 tokens per pass instead
# of 1,500, so far more detail survives each round.
COMPRESSION = 4.0
CHARS_PER_TOKEN = 3.10           # measured for this tokenizer; only an estimate
FALLBACK_CTX = 32768

# Merge rounds needed are log(batches) / log(COMPRESSION): eight for a billion
# tokens at 4x, thirteen for a trillion. This was 4, which was enough at the
# old 18.7x and is not enough now - and when it was too low the merge loop
# simply stopped with summaries outstanding and returned the first, discarding
# the rest without saying so. The cap is well above anything reachable, and
# hitting it is reported rather than hidden.
MAX_ROUNDS = 24

SEPARATOR = "\n\n---\n\n"
PARAGRAPH = "\n\n"

BATCH_PROMPT = (
    "Below is a verbatim extract from a longer conversation. Summarise it "
    "faithfully: decisions made, facts established, names, identifiers, "
    "numbers, and anything left unresolved. Do not speculate and do not add "
    "anything that is not present. Be compact.\n\n"
)
MERGE_PROMPT = (
    "Below are summaries of consecutive parts of one conversation, in order. "
    "Combine them into a single coherent summary without losing specific "
    "facts, names, identifiers or numbers. Keep it organised and compact.\n\n"
)


def context_window(upstream: str) -> int:
    """The model's real window, from the server rather than assumed."""
    try:
        with urllib.request.urlopen(upstream.rstrip("/") + "/props", timeout=15) as r:
            props = json.loads(r.read())
        for key in ("n_ctx", "default_generation_settings"):
            value = props.get(key)
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, dict) and isinstance(value.get("n_ctx"), int):
                return value["n_ctx"]
    except Exception:
        pass
    return FALLBACK_CTX


def count_tokens(upstream: str, text: str) -> int:
    """Exact count from the server, falling back to the measured ratio."""
    try:
        request = urllib.request.Request(
            upstream.rstrip("/") + "/tokenize",
            data=json.dumps({"content": text}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=60) as response:
            return len(json.loads(response.read()).get("tokens", []))
    except Exception:
        return int(len(text) / CHARS_PER_TOKEN) + 1


def input_budget(upstream: str) -> tuple[int, int, int]:
    """(input tokens, characters that roughly holds, output tokens).

    OpenCode's rule - what fits is the window minus what the answer needs -
    with the answer sized from the input instead of pinned to a constant. The
    fill fraction keeps a margin for the chat template and any system prompt
    the server adds, neither of which is visible from here.
    """
    ctx = context_window(upstream)
    usable = max(2048, int(ctx * CONTEXT_FILL))
    output = max(512, int(usable / (COMPRESSION + 1.0)))
    tokens = max(1024, usable - output)
    return tokens, int(tokens * CHARS_PER_TOKEN), output


def chat(upstream: str, prompt: str, max_tokens: int = 4096,
         timeout: float = 1800.0) -> str:
    body = json.dumps({
        "model": "opencore",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }).encode()
    request = urllib.request.Request(
        upstream.rstrip("/") + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    return (payload["choices"][0]["message"].get("content") or "").strip()


def output_budget_for_text(upstream: str, prompt: str, ceiling: int) -> int:
    """Size each response from the text it actually reads, not an empty window.

    A 2 KB conversation must not reserve nearly 6k output tokens just because
    the model has a 32k window. That can turn a short summary into minutes of
    generation and let the model elaborate beyond its source.
    """
    return min(ceiling, max(384, int(count_tokens(upstream, prompt) / COMPRESSION) + 64))


def _fit(upstream: str, prompt: str, body: str, token_budget: int) -> str:
    """Trim `body` until prompt+body fits the budget, measured not estimated.

    Characters per token is only an average, so a batch packed to 90% by
    estimate can still overflow on text that tokenises badly - code, hashes,
    non-English. One measurement per batch costs a single request and turns a
    hopeful guess into a guarantee.
    """
    used = count_tokens(upstream, prompt + body)
    while used > token_budget and len(body) > 1000:
        overshoot = (used - token_budget) / max(1, used)
        body = body[:int(len(body) * (1.0 - overshoot - 0.02))]
        used = count_tokens(upstream, prompt + body)
    return body


def iter_batches(archive: EchoArchive, conversation_id: str, max_pages: int,
                 batch_chars: int):
    """Yield page text in window-sized batches, oldest first."""
    yield from iter_batches_archives([archive], conversation_id, max_pages, batch_chars)


def iter_batches_archives(archives: list[EchoArchive], conversation_id: str,
                          max_pages: int, batch_chars: int):
    """Read exact cold and hot pages in chronological archive order."""
    batch: list[str] = []
    size = 0
    seen = 0
    for archive in archives:
        for row in archive._iter_page_rows("page_id, conversation_id"):
            if row["conversation_id"] != conversation_id:
                continue
            page = archive.load(row["page_id"])
            if page is None:
                continue
            seen += 1
            if size + page.chars > batch_chars and batch:
                yield "\n\n".join(batch), seen - 1
                batch, size = [], 0
            batch.append(page.text)
            size += page.chars
            if max_pages and seen >= max_pages:
                break
        if max_pages and seen >= max_pages:
            break
    if batch:
        yield "\n\n".join(batch), seen


def _batches_from_pages(pages, batch_chars: int):
    """Same batching as iter_batches, over an already-chosen page list."""
    batch, size, seen = [], 0, 0
    for page in pages:
        seen += 1
        if size + page.chars > batch_chars and batch:
            yield PARAGRAPH.join(batch), seen
            batch, size = [], 0
        batch.append(page.text)
        size += page.chars
    if batch:
        yield PARAGRAPH.join(batch), seen


def summarize(archive: EchoArchive, conversation_id: str, upstream: str,
              max_pages: int = 0, progress=None, salience: float = 0.0,
              older_archive: EchoArchive | None = None) -> dict:
    """Map-reduce summary. Returns the text plus what it actually covered.

    With `salience` above 0, pages are ranked and filtered before the model
    reads anything, and only that fraction is summarised. Everything skipped
    stays in the archive, exact and retrievable - the summary gets thinner,
    the memory does not.
    """
    started = time.time()
    say = progress or (lambda message: None)

    ctx = context_window(upstream)
    token_budget, batch_chars, out_budget = input_budget(upstream)
    say("  window %s tokens, using %.0f%%: reads %s tokens and writes up to %s "
        "per pass (%.0fx compression)"
        % (f"{ctx:,}", CONTEXT_FILL * 100, f"{token_budget:,}",
           f"{out_budget:,}", COMPRESSION))

    total_pages = archive.db.execute(
        "SELECT COUNT(*) c FROM pages WHERE conversation_id=?",
        (conversation_id,)).fetchone()["c"]
    if older_archive is not None:
        total_pages += older_archive.db.execute(
            "SELECT COUNT(*) c FROM pages WHERE conversation_id=?",
            (conversation_id,)).fetchone()["c"]
    if max_pages:
        total_pages = min(total_pages, max_pages)
    est_chars = total_pages * 4600.0
    est_calls = max(1, int(est_chars / max(1, batch_chars)))
    if est_calls > 200:
        say("  note: about %s model calls for %s pages. Summarising a very "
            "long conversation is bounded by that, not by memory - pass "
            "--max-pages, or /summarize <n>, to read only the oldest n pages."
            % (f"{est_calls:,}", f"{total_pages:,}"))

    selection = None
    if salience > 0.0 and older_archive is None:
        selection = select_salient(archive, conversation_id,
                                   keep_fraction=salience, progress=say)
        batches = _batches_from_pages(selection["pages"], batch_chars)
    else:
        batches = iter_batches_archives(
            [older_archive, archive] if older_archive is not None else [archive],
            conversation_id, max_pages, batch_chars)

    calls = 0
    summaries: list[str] = []
    pages_read = 0
    for text, seen in batches:
        pages_read = seen
        text = _fit(upstream, BATCH_PROMPT, text, token_budget)
        say("  summarising pages up to %s..." % f"{seen:,}")
        prompt = BATCH_PROMPT + text
        summaries.append(chat(upstream, prompt,
                              max_tokens=output_budget_for_text(upstream, prompt, out_budget)))
        calls += 1

    if not summaries:
        return {"summary": "", "pages_read": 0, "rounds": 0, "model_calls": 0,
                "tokens_per_pass": token_budget,
                "output_per_pass": out_budget,
                "seconds": time.time() - started,
                "note": "this conversation has no archived pages yet"}

    def merge(block: list[str]) -> str:
        body = _fit(upstream, MERGE_PROMPT, SEPARATOR.join(block), token_budget)
        prompt = MERGE_PROMPT + body
        return chat(upstream, prompt,
                    max_tokens=output_budget_for_text(upstream, prompt, out_budget))

    # out_budget is rebound below when summaries stop shrinking, so merge()
    # reads it from the enclosing scope on every call rather than capturing it.

    rounds = 1
    while len(summaries) > 1 and rounds <= MAX_ROUNDS:
        say("  merging %d summaries (round %d)..." % (len(summaries), rounds))
        merged: list[str] = []
        block: list[str] = []
        size = 0
        for piece in summaries:
            if size + len(piece) > batch_chars and block:
                merged.append(merge(block) if len(block) > 1 else block[0])
                calls += 1 if len(block) > 1 else 0
                block, size = [], 0
            block.append(piece)
            size += len(piece)
        if block:
            merged.append(merge(block) if len(block) > 1 else block[0])
            calls += 1 if len(block) > 1 else 0

        # A round that merges nothing is a round that will never finish. It
        # happens when the summaries have grown so large that only one fits in
        # a batch: merging a lone summary just rewrites it at the same size.
        # Asking for a shorter summary is what actually makes progress, and it
        # is better than looping to the cap and reporting a partial result.
        if len(merged) >= len(summaries):
            if out_budget > 512:
                out_budget = max(512, out_budget // 2)
                say("  summaries are not shrinking; compressing harder "
                    "(%s tokens per summary)" % f"{out_budget:,}")
            else:
                say("  summaries will not shrink further; stopping")
                summaries = merged
                break
        summaries = merged
        rounds += 1

    incomplete = len(summaries) > 1
    if incomplete:
        # Should be unreachable now, but returning summaries[0] here would
        # look like a finished summary while dropping everything else, so the
        # remainder is kept and the shortfall stated.
        say("  merge cap reached with %d summaries outstanding" % len(summaries))
        final = SEPARATOR.join(summaries)
    else:
        final = summaries[0]

    truncated = bool(max_pages and pages_read >= max_pages)
    notes = []
    if truncated:
        notes.append("stopped at the --max-pages limit; this summarises the "
                     "oldest %s pages only" % f"{max_pages:,}")
    if incomplete:
        notes.append("could not be merged into a single summary within %d "
                     "rounds; %d partial summaries are concatenated below "
                     "rather than discarded" % (MAX_ROUNDS, len(summaries)))
    return {
        "summary": final,
        "pages_read": pages_read,
        "rounds": rounds,
        "model_calls": calls,
        "tokens_per_pass": token_budget,
        "output_per_pass": out_budget,
        "seconds": time.time() - started,
        "selected_from": selection["considered"] if selection else 0,
        "truncated": truncated,
        "incomplete": incomplete,
        "note": "; ".join(notes),
    }


def format_result(conversation_id: str, result: dict) -> str:
    lines = [result["summary"]]
    footer = ("\n\n---\n*Summary of `%s` - %s pages read in %s model call(s) at "
              "%s tokens read and up to %s written per pass, %d merge "
              "round(s), %.0fs. The summary is lossy; the pages it was made "
              "from are unchanged and remain the exact record.*"
              % (conversation_id, f"{result['pages_read']:,}",
                 f"{result.get('model_calls', 0):,}",
                 f"{result.get('tokens_per_pass', 0):,}",
                 f"{result.get('output_per_pass', 0):,}",
                 result["rounds"], result["seconds"]))
    if result.get("note"):
        footer += "\n*Note: %s*" % result["note"]
    lines.append(footer)
    return "".join(lines)
