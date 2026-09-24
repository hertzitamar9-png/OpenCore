"""ECHO proxy: an OpenAI-compatible endpoint that remembers everything.

Sits in front of the OpenCore llama-server. For every request it retrieves
exact source pages from the ECHO archive, puts them in front of the model, then
appends the turn to the archive. Clients see a normal OpenAI endpoint, so this
works from Unsloth Studio, LM Studio, Continue or curl with no code - which was
the requirement: settings edited in the app, not in a script.

    client  ->  :8812 (this)  ->  :8811 (llama-server)
                    |
                    +-- ECHO archive (SQLite, exact source bytes)

Two behaviours are deliberate and worth knowing before reading the code.

The archive stores supplied turns and structured source events. It never stores
the retrieved block that this proxy injected, because that text is already in
the archive and re-archiving it would compound copies on every turn.

When retrieval is uncertain the model is told so in plain words rather than
being handed weak pages that look authoritative. The design this implements is
explicit that "the memory does not contain it" and "the retriever did not
confidently find it" are different states, and that ordinary RAG blurs them.
Silently injecting low-confidence pages is exactly that blurring.

Streaming is relayed byte-for-byte, but a streamed reply can only be archived
if it can also be reassembled, so the proxy accumulates the SSE deltas as they
pass through and writes the turn once the stream ends.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Works from the repo (evoagent under src/) and from the shipped package, where
# evoagent/ sits next to this file.
for candidate in (Path(__file__).resolve().parent, ROOT / "src"):
    if (candidate / "evoagent" / "echo_memory.py").exists():
        sys.path.insert(0, str(candidate))
        break

from evoagent.echo_memory import EchoArchive, RetrievalResult  # noqa: E402
from evoagent.echo_context import ContextSession, LiveTranscript, COMMAND, INSTRUCTIONS  # noqa: E402
from evoagent.echo_output import OutputLedger, word_target  # noqa: E402
from echo_summarize import summarize, format_result  # noqa: E402


def RetrievalView(pages):
    """Reuse RetrievalResult's budget-aware formatting for a page subset."""
    return RetrievalResult(pages=list(pages))

MEMORY_HEADER = (
    "The following are exact excerpts from earlier history, retrieved from an "
    "archive. They are verbatim source text, not summaries. Use them only if "
    "they are relevant to the question."
)
CONTINUITY_HEADER = (
    "Verbatim tail of this conversation so far, restored from the archive "
    "because it no longer fits in the model's window."
)
NEWLINE = "\n"
PARAGRAPH = "\n\n"

# A reply longer than this is streamed to a file instead of held in memory.
# An answer big enough to need continuations is also big enough that holding
# it as a Python string is what fails first - a billion tokens is 3.1 GB of
# text, and no HTTP client would accept it inline either.
MAX_INLINE_REPLY_CHARS = 2_000_000

_EMBEDDED_TOOL = re.compile(
    r"<tool_call>\s*<function=([A-Za-z_][A-Za-z_0-9]*)>\s*(.*?)\s*</function>\s*</tool_call>",
    re.DOTALL,
)
_EMBEDDED_PARAMETER = re.compile(
    r"<parameter=([A-Za-z_][A-Za-z_0-9]*)>\s*(.*?)\s*</parameter>",
    re.DOTALL,
)


def recover_embedded_tool_call(message, declared_tools):
    """Recover a complete model-native call emitted in text instead of tool_calls.

    Only declared functions are accepted. An incomplete or ambiguous call remains
    visible as model output rather than being silently executed.
    """
    if message.get("tool_calls"):
        return False
    allowed = {tool.get("function", {}).get("name") for tool in declared_tools or []
               if tool.get("type") == "function"}
    for field in ("reasoning_content", "content"):
        source = message.get(field)
        if not isinstance(source, str):
            continue
        matches = list(_EMBEDDED_TOOL.finditer(source))
        if len(matches) != 1:
            continue
        match = matches[0]
        name, body = match.group(1), match.group(2)
        if name not in allowed:
            continue
        parameters = list(_EMBEDDED_PARAMETER.finditer(body))
        if not parameters or _EMBEDDED_PARAMETER.sub("", body).strip():
            continue
        arguments = {}
        for parameter in parameters:
            key, value = parameter.group(1), parameter.group(2).strip()
            if key in arguments:
                break
            if re.fullmatch(r"[A-Za-z]:\\\\[^\r\n]+", value):
                value = value.replace("\\\\", "\\")
            elif re.fullmatch(r"-?\d+", value):
                value = int(value)
            elif value in ("true", "false"):
                value = value == "true"
            arguments[key] = value
        else:
            message["tool_calls"] = [{
                "id": "call_" + uuid.uuid4().hex,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }]
            message[field] = (source[:match.start()] + source[match.end():]).strip()
            return True
    return False

# Reasoning effort. The server takes reasoning_budget_tokens per request, so
# the level is chosen per message rather than fixed when the server starts.
#
# "off" is a real setting, not budget 1: the thinking channel is disabled
# entirely, which is what makes short factual answers fast. At the other end,
# "ultra" is not a bigger budget - past a point more thinking on one pass stops
# helping - it is several passes that check each other.
REASONING_LEVELS = {
    "off":        {"budget": 0,     "passes": 1},
    "low":        {"budget": 512,   "passes": 1},
    "medium":     {"budget": 1500,  "passes": 1},
    "high":       {"budget": 3000,  "passes": 1},
    "extra-high": {"budget": 6000,  "passes": 1},
    "max":        {"budget": 12000, "passes": 1},
    "ultra":      {"budget": 6000,  "passes": 3},
}
REASONING_ALIASES = {
    "none": "off", "minimal": "low", "med": "medium", "normal": "medium",
    "extra_high": "extra-high", "extrahigh": "extra-high", "xhigh": "extra-high",
    "very-high": "extra-high", "maximum": "max", "ultra-max": "ultra",
    "subagents": "ultra", "opencore": "ultra",
}
DEFAULT_REASONING = "high"

CRITIQUE_PROMPT = (
    "You are reviewing a draft answer. List only concrete problems: factual "
    "errors, missing requirements from the question, broken logic, and bugs in "
    "any code. Be specific and brief. If the draft is correct and complete, "
    "reply exactly: NO ISSUES." + PARAGRAPH +
    "QUESTION:" + NEWLINE + "%s" + PARAGRAPH +
    "DRAFT:" + NEWLINE + "%s"
)
REVISE_PROMPT = (
    "Rewrite the draft answer so it fixes every issue listed. Keep what was "
    "already correct. Return only the improved answer, with no commentary "
    "about the revision." + PARAGRAPH +
    "QUESTION:" + NEWLINE + "%s" + PARAGRAPH +
    "DRAFT:" + NEWLINE + "%s" + PARAGRAPH +
    "ISSUES:" + NEWLINE + "%s"
)

UNCERTAIN_NOTE = (
    "A search of the archive did not confidently find material for this "
    "request. Do not assume the archive is empty and do not invent a "
    "remembered answer; say what you do not know if the question depends on it."
)

# The model's own handle on its memory.
#
# Retrieval happens before the model runs and is chosen by the proxy; this is
# the model choosing. It matters most while writing at length: with a 32k
# window it cannot re-read a long piece it is part-way through, but it can look
# up what it already said about a subject before saying more. That is the
# difference between locally sensible and globally consistent.
ECHO_TOOL_INSTRUCTION = (
    "You can search your own memory of this conversation, including parts too "
    "old to still be shown to you. To do it, write on its own line:" + PARAGRAPH
    + "    <echo_search>the word or phrase</echo_search>" + PARAGRAPH +
    "Then stop. The exact passages where you used it will be given to you, and "
    "you continue from there. Use it before writing about something you may "
    "have already covered, so you stay consistent with yourself instead of "
    "repeating or contradicting earlier work. Do not guess what you wrote - "
    "look it up."
)
ECHO_SEARCH_RE = re.compile(r"<echo_search>\s*(.+?)\s*</echo_search>", re.I | re.S)


class ArchiveSet:
    """One archive file per conversation, opened on demand.

    A single shared database caps every conversation together: SQLite's limit
    is 4,294,967,294 pages, so at the 4 KB page size one file tops out at
    17.59 TB - about 3.17 trillion tokens. Raising the page size raises that
    ceiling but measured +156% disk at 16 KB and +745% at 64 KB with no query
    benefit, which would defeat the point of shrinking idle archives.

    Sharding by conversation gets the ceiling per-conversation instead, with no
    global limit beyond the disk, and it makes the maintenance operations local:
    hibernating or vacuuming one finished conversation no longer rewrites
    everyone else's history, which on a single shared file would mean touching
    terabytes to tidy up megabytes.
    """

    def __init__(self, directory: Path, idle_seconds: float, max_open: int = 8):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.idle_seconds = idle_seconds
        self.max_open = max_open
        self._open: dict[str, EchoArchive] = {}
        self._lock = threading.RLock()

    def path_for(self, conversation_id: str) -> Path:
        # Readable prefix for a human browsing the folder, hash for uniqueness
        # and for filesystem safety - conversation ids come from clients.
        safe = re.sub(r"[^A-Za-z0-9_-]", "", conversation_id)[:32] or "conv"
        digest = hashlib.sha1(conversation_id.encode()).hexdigest()[:12]
        return self.directory / ("%s-%s.db" % (safe, digest))

    def get(self, conversation_id: str) -> EchoArchive:
        with self._lock:
            archive = self._open.get(conversation_id)
            if archive is None:
                if len(self._open) >= self.max_open:
                    self._close_least_recent()
                archive = EchoArchive(self.path_for(conversation_id),
                                      idle_seconds=self.idle_seconds)
                self._open[conversation_id] = archive
            return archive

    def _close_least_recent(self) -> None:
        oldest = min(self._open.items(), key=lambda kv: kv[1]._last_used)
        oldest[1].close()
        del self._open[oldest[0]]

    def known_conversations(self) -> list[str]:
        return sorted(p.stem for p in self.directory.glob("*.db"))

    def maintain(self, hibernate_seconds: float, log) -> None:
        """Release idle connections and hibernate finished conversations."""
        with self._lock:
            items = list(self._open.items())
        now = time.time()
        for conversation_id, archive in items:
            try:
                idle_for = now - archive._last_used
                if hibernate_seconds > 0 and idle_for >= hibernate_seconds \
                        and not archive.is_partial() and not archive._hibernated():
                    result = archive.hibernate(keep_recent_pages=20000)
                    if not result.get("already"):
                        log("  echo: '%s' idle - hibernated %.1f MB -> %.1f MB"
                            % (conversation_id, result["before_bytes"] / 1e6,
                               result["after_bytes"] / 1e6))
                archive.release_if_idle()
            except Exception:
                continue

    def total_disk(self) -> dict:
        files = list(self.directory.glob("*.db*"))
        return {"conversations": len(list(self.directory.glob("*.db"))),
                "total_bytes": sum(f.stat().st_size for f in files if f.exists())}

    def close(self) -> None:
        with self._lock:
            for archive in self._open.values():
                archive.close()
            self._open.clear()


class EchoState:
    """Shared archive plus settings. One instance per process."""

    def __init__(self, archive: EchoArchive, upstream: str, budget_chars: int,
                 min_query_chars: int, verbose: bool, max_continuations: int = 8,
                 recent_turns: int = 6, window_tokens: int = 100_000_000,
                 salience: float = 0.0, offload_every: int = 1000,
                 reasoning: str = DEFAULT_REASONING,
                 allow_model_search: bool = True):
        self.archives = archive
        self.upstream = upstream.rstrip("/")
        self.budget_chars = budget_chars
        self.min_query_chars = min_query_chars
        self.verbose = verbose
        self.max_continuations = max_continuations
        self.recent_turns = recent_turns
        self.window_tokens = window_tokens
        self.salience = salience
        self.offload_every = offload_every
        self.reasoning = reasoning
        self.allow_model_search = allow_model_search
        self.lock = threading.Lock()
        self._cold: dict = {}
        self._written: dict = {}
        self.turns = 0
        self._ctx_size: int | None = None
        self.autonomous_context = True
        self.context_steps = 0
        self._context_active = set()
        # Live transcript compaction: past live_high of the window, the oldest
        # whole turns move to the archive until live_low remains.
        self.live_high = 0.85
        self.live_low = 0.45

    def log(self, message: str) -> None:
        if self.verbose:
            try:
                print(message, flush=True)
            except OSError:
                # The desktop app can be restarted while this server is still
                # listening. A closed log pipe must not break HTTP responses.
                pass

    # -- window management -------------------------------------------------

    def context_size(self) -> int:
        """The model's real window, asked once and remembered."""
        if self._ctx_size is None:
            self._ctx_size = 32768
            try:
                with urllib.request.urlopen(urllib.request.Request(self.upstream + "/props",
                                            headers=self.upstream_headers()), timeout=15) as r:
                    props = json.loads(r.read())
                for key in ("n_ctx", "default_generation_settings"):
                    value = props.get(key)
                    if isinstance(value, int) and value > 0:
                        self._ctx_size = value
                        break
                    if isinstance(value, dict) and isinstance(value.get("n_ctx"), int):
                        self._ctx_size = value["n_ctx"]
                        break
            except Exception as error:
                self.log("  echo: could not read /props (%s); assuming %d"
                         % (error, self._ctx_size))
            self.log("  echo: model window is %d tokens" % self._ctx_size)
        return self._ctx_size

    def upstream_headers(self):
        headers = {"Content-Type": "application/json"}
        key = os.environ.get("ECHO_UPSTREAM_API_KEY")
        if key:
            headers["Authorization"] = "Bearer " + key
        return headers

    def count_tokens(self, text: str) -> int:
        """Exact count from the server when possible.

        The fallback is 3.10 characters per token, measured on this tokenizer
        earlier in the project. It is only a fallback: guessing the budget is
        how a window overflows, and overflowing is the thing being prevented.
        """
        if not text:
            return 0
        try:
            request = urllib.request.Request(
                self.upstream + "/tokenize",
                data=json.dumps({"content": text}).encode(),
                headers=self.upstream_headers(), method="POST")
            with urllib.request.urlopen(request, timeout=30) as response:
                return len(json.loads(response.read()).get("tokens", []))
        except Exception:
            # A UTF-8 byte bound is conservative for this byte-level tokenizer.
            return len(text.encode("utf-8"))

    def fit_window(self, messages: list, reserve: int) -> tuple[list, int]:
        """Drop the oldest turns until the prompt fits, keeping what matters.

        System messages and the final user turn are never dropped - the first
        because it is the client's own configuration, the second because it is
        the question. Everything discarded here is already in the archive, so
        this evicts rather than forgets: the material stays retrievable, it
        just stops occupying the window.
        """
        limit = self.context_size() - reserve - 512
        if limit <= 0:
            raise ValueError("Reply reservation leaves no room for the prompt")
        keep_always = [i for i, m in enumerate(messages) if m.get("role") == "system"]
        last_user = next((i for i in range(len(messages) - 1, -1, -1)
                          if messages[i].get("role") == "user"), None)
        if last_user is not None:
            keep_always.append(last_user)
        pinned = set(keep_always)

        costs = [self.count_tokens(str(m.get("content") or "")) + 4 for m in messages]
        total = sum(costs)
        if total <= limit:
            return messages, total

        droppable = [i for i in range(len(messages)) if i not in pinned]
        dropped = 0
        for index in droppable:                    # oldest first
            if total <= limit:
                break
            total -= costs[index]
            pinned.discard(index)
            dropped += 1
            costs[index] = -1                      # mark as removed
        kept = [m for i, m in enumerate(messages) if costs[i] != -1]
        if total > limit:
            raise ValueError("System instructions and latest message exceed the working window")
        self.log("  echo: window %d/%d tokens, evicted %d older turn(s) to the archive"
                 % (total, limit, dropped))
        return kept, total

    def prepare_messages(self, messages: list, conversation: str, reserve: int,
                         archive_input: bool = True) -> list:
        """Persist supplied history before eviction, then reserve retrieval space.

        Imported message fingerprints live on disk so repeated full-history
        requests do not grow a Python set or duplicate the archive on each call.
        """
        if archive_input:
            self.archive_messages(messages, conversation)
        return self._prepare_archived_messages(messages, conversation, reserve)

    def archive_messages(self, messages, conversation):
        archive = self.archives.get(conversation)
        imported = skipped = 0
        for message in messages:
            if archive.record_source_event(message, conversation):
                imported += 1
            else:
                skipped += 1
        return {"imported": imported, "skipped": skipped}

    def _prepare_archived_messages(self, messages, conversation, reserve):
        # Give archival retrieval a quarter of the existing window. If pinned
        # instructions need that space, use only the room actually available.
        allowance = max(0, min(8192, (self.context_size() - reserve - 512) // 4))
        try:
            visible, _ = self.fit_window(messages, reserve + allowance)
        except ValueError:
            visible, _ = self.fit_window(messages, reserve)
        query = _last_user_message(messages)
        already = " ".join(str(m.get("content") or "") for m in visible)
        context = self.build_context(query, conversation, already,
                                     self.available_budget(visible, reserve))
        if context:
            # Tokenize the actual block; character estimates alone cannot
            # bound code, Unicode, or punctuation-heavy memories.
            free = self.context_size() - reserve - 512 - sum(
                self.count_tokens(str(m.get("content") or "")) + 4 for m in visible)
            while context and self.count_tokens(context) + 4 > free:
                context = context[:len(context) // 2]
            if context:
                visible = _inject(visible, context)
        return self.fit_window(visible, reserve)[0]

    # -- retrieval ---------------------------------------------------------

    def available_budget(self, messages: list, reserve: int) -> int:
        """Characters of memory that actually fit in the window right now.

        There was a flat 24,000-character cap here, which is an arbitrary
        number and usually far less than the window can hold. The real limit is
        physical - the model reads a fixed number of tokens and the retrieved
        pages have to share that with the conversation and the reply - so the
        budget is computed from what is genuinely free rather than guessed.
        """
        window = self.context_size()
        used = sum(self.count_tokens(str(m.get("content") or "")) + 4
                   for m in messages)
        # The retrieved block is injected as a system message, and fit_window
        # pins system messages - so an overshoot here cannot be trimmed away
        # later and the server would silently truncate the prompt instead.
        # The margin covers the chat template and the fact that 3.10 chars per
        # token is an average, not a guarantee.
        free = window - used - reserve - 512
        chars = int(free * 3.10 * 0.92)
        if self.budget_chars > 0:                     # explicit override
            return min(self.budget_chars, max(0, chars))
        return max(0, chars)

    def build_context(self, query: str, conversation_id: str,
                      already_present: str = "", budget_chars: int | None = None) -> str | None:
        """Assemble two different kinds of memory for this turn.

        Continuity ("where were we") comes from the tail of the conversation by
        time. Relevance ("what matters here") comes from retrieval across all
        of history. A thread resumed on relevance alone appears to have
        forgotten the previous exchange, so both are supplied, and the
        continuity half is capped so it cannot crowd out retrieval.

        Pages already visible in the client's own message list are skipped:
        when a client sends full history, injecting the same bytes again would
        waste the window on a duplicate.
        """
        budget = self.budget_chars if budget_chars is None else budget_chars
        if budget <= 0:
            return None
        blocks: list[str] = []
        seen_ids: set[str] = set()
        half = budget // 2

        if self.recent_turns > 0:
            recent = self.archives.get(conversation_id).recent_pages(
                conversation_id, self.recent_turns)
            fresh, used = [], 0
            for page in recent:
                if page.text[:400] in already_present:      # client already sent it
                    continue
                if used + page.chars > half:
                    continue
                fresh.append(page)
                seen_ids.add(page.page_id)
                used += page.chars
            if fresh:
                blocks.append("%s\n\n%s" % (
                    CONTINUITY_HEADER,
                    "\n\n".join(p.text for p in fresh)))
                self.log("  echo: %d recent page(s) for continuity" % len(fresh))

        if len(query.strip()) >= self.min_query_chars:
            result = self.archives.get(conversation_id).retrieve(
                query, conversation_id=conversation_id)
            # Offloaded history is still history. Without this, everything
            # moved to the cold file would silently stop being findable, which
            # would turn continuous offload into continuous forgetting.
            cold = self.cold_archive_for(conversation_id)
            if cold is not None:
                colder = cold.retrieve(query, conversation_id=conversation_id)
                # A recent match must never hide a conflicting older fact.
                # Interleave ranks because independent BM25 scores are not
                # comparable across databases. Deduplicate by source hash.
                merged, hashes = [], set()
                for rank in range(max(len(result.pages), len(colder.pages))):
                    for pages in (result.pages, colder.pages):
                        if rank < len(pages) and pages[rank].content_hash not in hashes:
                            merged.append(pages[rank])
                            hashes.add(pages[rank].content_hash)
                result = RetrievalResult(pages=merged, uncertain=True,
                    reason="searched both hot and cold archives; relevance requires verification")
            keep = [p for p in result.pages if p.page_id not in seen_ids]
            body = ""
            if keep:
                trimmed = RetrievalView(keep)
                body = trimmed.as_context(budget - sum(len(b) for b in blocks))
            if body:
                self.log("  echo: %d retrieved page(s), %s" % (len(keep), result.reason))
                head = MEMORY_HEADER if not result.uncertain \
                    else "%s\n\n%s" % (UNCERTAIN_NOTE, MEMORY_HEADER)
                blocks.append("%s\n\n%s" % (head, body))
            elif result.uncertain:
                blocks.append(UNCERTAIN_NOTE)

        if self.allow_model_search:
            blocks.append(ECHO_TOOL_INSTRUCTION)
        return PARAGRAPH.join(blocks) if blocks else None

    def spill_path(self, conversation_id: str) -> Path:
        """Where an over-long answer is written instead of into the reply."""
        safe = re.sub(r"[^A-Za-z0-9_-]", "", conversation_id)[:32] or "conv"
        directory = self.archives.directory / "long-answers"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return directory / ("%s-%s-%s.md" % (safe, stamp, uuid.uuid4().hex[:12]))

    def cold_archive_for(self, conversation_id: str):
        """The cold file beside a conversation's live archive, if it exists."""
        live = self.archives.path_for(conversation_id)
        cold = live.with_name(live.stem + "-cold" + live.suffix)
        if not cold.exists():
            return None
        with self.lock:
            handle = self._cold.get(str(cold))
            if handle is None:
                handle = EchoArchive(cold, idle_seconds=self.archives.idle_seconds)
                self._cold[str(cold)] = handle
            return handle

    def archive_turn(self, user_text: str, reply_text: str, conversation_id: str) -> None:
        stamp = time.time()
        archive = self.archives.get(conversation_id)
        with self.lock:
            if user_text.strip():
                archive.append("user: " + user_text, conversation_id, stamp)
            if reply_text.strip():
                archive.append("assistant: " + reply_text, conversation_id, stamp)
            self.turns += 1
            self._written[conversation_id] = self._written.get(conversation_id, 0)                 + len(user_text) + len(reply_text)

        # Offload continuously rather than waiting to be asked. Checking after
        # every turn would mean a VACUUM per message, so the check runs once
        # per offload_every tokens of new text - the work is proportional to
        # what was written, not to how often someone talks.
        if self.offload_every > 0 and self.window_tokens > 0:
            threshold = self.offload_every * 3.10
            if self._written.get(conversation_id, 0) >= threshold:
                self._written[conversation_id] = 0
                try:
                    rolled = archive.roll_window(conversation_id, self.window_tokens)
                    if rolled.get("moved"):
                        self.log("  echo: offloaded %s page(s) to the cold "
                                 "archive automatically" % f"{rolled['moved']:,}")
                except Exception as error:
                    self.log("  echo: offload skipped (%s)" % error)


def resolve_reasoning(payload: dict, headers, default: str) -> tuple[str, dict]:
    """Pick the effort level for this request.

    Accepted, in order: the OpenAI-style `reasoning_effort` field, an
    X-Echo-Reasoning header, and a suffix on the model name ("opencore:max").
    The model-name route exists because several chat clients let you type a
    model string but expose no way to send an extra field.
    """
    raw = (payload.get("reasoning_effort")
           or headers.get("X-Echo-Reasoning")
           or "")
    model = str(payload.get("model") or "")
    if not raw and ":" in model:
        raw = model.rsplit(":", 1)[1]
        payload["model"] = model.rsplit(":", 1)[0]
    key = str(raw).strip().lower().replace(" ", "-")
    key = REASONING_ALIASES.get(key, key)
    if key not in REASONING_LEVELS:
        key = default
    return key, REASONING_LEVELS[key]


def apply_reasoning(payload: dict, headers, default: str) -> tuple[str, dict]:
    """Apply per-request thinking settings before either ECHO context path runs."""
    level, settings = resolve_reasoning(payload, headers, default)
    payload.pop("reasoning_effort", None)
    payload["reasoning_budget_tokens"] = settings["budget"]
    kwargs = dict(payload.get("chat_template_kwargs") or {})
    kwargs["enable_thinking"] = settings["budget"] > 0
    payload["chat_template_kwargs"] = kwargs
    payload["reasoning_format"] = "deepseek" if settings["budget"] > 0 else "none"
    return level, settings


def review_draft(question: str, draft: str, ask) -> tuple[str, int]:
    """Critique and, only when needed, revise a model-controlled ECHO answer."""
    issues = ask(CRITIQUE_PROMPT % (question, draft), 1500)
    if "NO ISSUES" in issues.upper()[:400]:
        return draft, 2
    revised = ask(REVISE_PROMPT % (question, draft, issues), None)
    return (revised.strip() or draft), 3


def _conversation_id(payload: dict, headers) -> str:
    for candidate in (headers.get("X-Echo-Conversation"), payload.get("conversation_id"), payload.get("user")):
        if candidate:
            return str(candidate)
    return "default"


def _last_user_message(messages: list) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):        # OpenAI content-parts form
                return " ".join(part.get("text", "") for part in content
                                if isinstance(part, dict))
    return ""


def _inject(messages: list, context: str) -> list:
    """Put retrieved memory before the final user turn, after any system prompt.

    It goes late on purpose: models weight nearby context more heavily, and
    putting it ahead of the user's own words would bury the actual question.
    The client's system prompt is never touched, so app-level settings still
    behave exactly as the user configured them.
    """
    out = list(messages)
    for index in range(len(out) - 1, -1, -1):
        if out[index].get("role") == "user":
            out.insert(index, {"role": "system", "content": context})
            return out
    out.insert(0, {"role": "system", "content": context})
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: EchoState = None                       # set in main()

    def log_message(self, fmt, *args):            # quieter default logging
        if self.state and self.state.verbose:
            try:
                super().log_message(fmt, *args)
            except OSError:
                pass

    # -- plumbing ----------------------------------------------------------

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _upstream(self, path: str, payload: dict, stream: bool):
        request = urllib.request.Request(
            self.state.upstream + path,
            data=json.dumps(payload).encode(),
            headers=self.state.upstream_headers(),
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=None if stream else 3600)

    def do_GET(self):
        if self.path.startswith("/echo/stats"):
            stats = self.state.archives.total_disk()
            stats["turns_this_session"] = self.state.turns
            stats["open_archives"] = len(self.state.archives._open)
            stats["context_mode"] = "model_controlled" if self.state.autonomous_context else "legacy"
            stats["archive_directory"] = str(self.state.archives.directory.resolve())
            stats["outputs_directory"] = str((self.state.archives.directory / "long-answers").resolve())
            stats["archive_capacity"] = "limited by disk and SQLite; no guaranteed token count"
            return self._send_json(200, stats)
        if self.path.startswith("/v1/models") or self.path.startswith("/models"):
            try:
                with urllib.request.urlopen(urllib.request.Request(self.state.upstream + "/v1/models",
                                            headers=self.state.upstream_headers()), timeout=30) as response:
                    body = response.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except urllib.error.URLError as error:
                return self._send_json(502, {"error": {"message": str(error)}})
        # Any other GET (e.g. /health, /props) is proxied straight to the model,
        # so a client's health and capability checks succeed through ECHO.
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    self.state.upstream + self.path,
                    headers=self.state.upstream_headers()), timeout=30) as response:
                body = response.read()
                ctype = response.headers.get("Content-Type", "application/json")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        except urllib.error.URLError as error:
            return self._send_json(502, {"error": {"message": str(error)}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if self.path == "/echo/import" and (length <= 0 or length > 16 * 1024 * 1024):
            return self._send_json(413, {"error": {"message": "ECHO import batch must be 1-16 MiB"}})
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError as error:
            return self._send_json(400, {"error": {"message": "bad JSON: %s" % error}})

        if self.path == "/echo/import":
            conversation = payload.get("conversation_id")
            messages = payload.get("messages")
            if (not isinstance(conversation, str) or not conversation.strip()
                    or len(conversation) > 256 or not isinstance(messages, list)
                    or len(messages) > 512
                    or any(not isinstance(message, dict)
                           or message.get("role") not in ("user", "assistant", "tool", "system", "developer")
                           or not isinstance(message.get("content"), (str, list))
                           for message in messages)):
                return self._send_json(400, {"error": {"message": "Invalid ECHO import batch"}})
            result = self.state.archive_messages(messages, conversation)
            return self._send_json(200, {"conversation_id": conversation, **result})

        if not self.path.startswith("/v1/chat/completions"):
            # Anything else is proxied untouched; ECHO only understands chat.
            return self._passthrough(payload)

        conversation = _conversation_id(payload, self.headers)
        messages = payload.get("messages") or []
        question = _last_user_message(messages)
        app_owns_timeline = self.headers.get("X-OpenCore-Timeline-Owner", "").lower() == "app"

        command = question.strip().lower()
        if command.startswith("/trim"):
            return self._compact(question, conversation, payload)
        if command.startswith("/compact") or command.startswith("/summarize"):
            return self._summarize(question, conversation, payload)

        level, settings = apply_reasoning(payload, self.headers, self.state.reasoning)
        if level != self.state.reasoning:
            self.state.log("  echo: reasoning=%s (budget %d, %d pass(es))"
                           % (level, settings["budget"], settings["passes"]))

        if self.state.autonomous_context:
            with self.state.lock:
                if conversation in self.state._context_active:
                    return self._send_json(409, {"error": {"message": "Conversation already active"}})
                self.state._context_active.add(conversation)
            try:
                return self._controlled_context(payload, conversation, level,
                                                archive_input=not app_owns_timeline)
            except (ValueError, urllib.error.URLError) as error:
                return self._send_json(502 if isinstance(error, urllib.error.URLError) else 400,
                                       {"error": {"message": str(error)}})
            finally:
                with self.state.lock:
                    self.state._context_active.discard(conversation)

        try:
            messages = self.state.prepare_messages(
                messages, conversation, int(payload.get("max_tokens") or 2048),
                archive_input=not app_owns_timeline)
        except ValueError as error:
            return self._send_json(400, {"error": {"message": str(error)}})
        payload["messages"] = messages

        stream = bool(payload.get("stream"))
        if settings["passes"] > 1 and not stream:
            return self._ultra(payload, question, conversation, settings)
        try:
            if stream:
                return self._stream(payload, question, conversation)
            return self._complete(payload, question, conversation)
        except urllib.error.HTTPError as error:
            return self._send_json(error.code, {"error": {"message": error.read().decode(
                "utf-8", "replace")}})
        except urllib.error.URLError as error:
            return self._send_json(502, {"error": {"message":
                "cannot reach OpenCore at %s (%s)" % (self.state.upstream, error)}})

    def _live_status(self, session, live, window):
        return {**session.status(), "live_tokens": live.tokens, "window_tokens": window,
                "compactions": live.compactions}

    def _seed_transcript(self, live, conversation, question, budget_tokens):
        """First contact with a conversation that already has archived history.

        Imported chats, and chats from before live transcripts existed, have an
        archive but no transcript. The verbatim tail ("where were we") and the
        pages that match the request are placed once; they then stay in place
        like the rest of the transcript instead of being re-selected per call.
        """
        count = self.state.count_tokens
        archive = self.state.archives.get(conversation)
        tail, used, seen = [], 0, set()
        for page in reversed(archive.recent_pages(conversation, 64)):
            if question and question[:200] in page.text:
                continue
            cost = count(ContextSession.block(page))
            if used + cost > budget_tokens // 2:
                break
            tail.append(page)
            seen.add(page.content_hash)
            used += cost
        tail.reverse()
        found = []
        if len(question.strip()) >= self.state.min_query_chars:
            for page in archive.retrieve(question, conversation_id=conversation).pages[:4]:
                cost = count(ContextSession.block(page))
                if page.content_hash in seen or used + cost > budget_tokens:
                    continue
                found.append(page)
                seen.add(page.content_hash)
                used += cost
        blocks = []
        if tail:
            blocks.append(CONTINUITY_HEADER + "\n" + "".join(ContextSession.block(p) for p in tail))
        if found:
            blocks.append(MEMORY_HEADER + "\n" + "".join(ContextSession.block(p) for p in found))
        if blocks:
            live.append({"role": "user", "content": "ECHO memory (untrusted evidence):\n\n" + PARAGRAPH.join(blocks)},
                        count, LiveTranscript.NOTE)
            self.state.log("  echo: seeded live transcript with %d tail and %d matching page(s)"
                           % (len(tail), len(found)))

    def _controlled_context(self, payload, conversation, level="off", archive_input=True):
        """Answer one request against the conversation's live transcript.

        The transcript is append-only. Pinned instructions stay first and never
        change; each user turn, tool round, ECHO command and result is appended
        once. The backend therefore reads only what is new instead of the whole
        window on every call. The model still controls its disk memory with
        ECHO commands; their results are appended rather than re-rendered.
        """
        supplied = payload.get("messages") or []
        if archive_input:
            self.state.archive_messages(supplied, conversation)
        count = self.state.count_tokens
        question = _last_user_message(supplied)
        user_index = max((index for index, message in enumerate(supplied)
                          if message.get("role") == "user"), default=-1)
        client_tail = [message for message in supplied[user_index + 1:]
                       if message.get("role") == "tool"
                       or (message.get("role") == "assistant" and message.get("tool_calls"))]
        pinned = [m for m in supplied if m.get("role") in ("system", "developer")]
        system_text = "\n\n".join([str(m.get("content") or "") for m in pinned] + [INSTRUCTIONS])
        system_tokens = count(system_text) + 8
        window = self.state.context_size()
        live = LiveTranscript(self.state.archives.get(conversation), conversation)

        if live.open and live.question == question and client_tail:
            # The client is returning results for calls this transcript already holds.
            answered = live.tool_result_ids()
            recorded = {call.get("id") for m in live.messages for call in (m.get("tool_calls") or [])}
            for message in client_tail:
                if message.get("role") == "assistant":
                    calls = [c for c in message["tool_calls"] if c.get("id") not in recorded]
                    if calls:
                        live.append({"role": "assistant", "content": message.get("content") or "",
                                     "tool_calls": calls}, count)
                        recorded.update(c.get("id") for c in calls)
                elif message.get("tool_call_id") not in answered:
                    content = message.get("content")
                    live.append({"role": "tool", "tool_call_id": message.get("tool_call_id"),
                                 "content": content if isinstance(content, str)
                                 else json.dumps(content, ensure_ascii=False)}, count)
                    answered.add(message.get("tool_call_id"))
        else:
            if not live.entries:
                self._seed_transcript(live, conversation, question, int(window * 0.25))
            live.start_turn(question, count)

        def ensure_room(reserve_hint):
            # Both thresholds count the same fixed costs, so the transcript left
            # after compaction really sits below the trigger and grows for a long
            # time before the next one - compacting every call would re-read the
            # whole window each time, which is the cost this exists to remove.
            fixed = system_tokens + reserve_hint + 2560
            if fixed + live.tokens <= int(window * self.state.live_high):
                return
            before = live.tokens
            moved = live.compact(max(0, int(window * self.state.live_low) - fixed), count)
            if moved:
                self.state.log("  echo: live window reached %d tokens; moved %d older message(s) to the "
                               "archive (%d -> %d live tokens)" % (system_tokens + before, moved,
                                                                   before, live.tokens))

        # A client may ask for more output than the window holds - that is the
        # normal way to request a very long answer. Clamp the per-call reserve to
        # what fits and let continuation supply the rest across passes.
        asked = int(payload.get("max_completion_tokens") or payload.get("max_tokens") or 2048)
        ensure_room(min(asked, 16384))
        room = window - system_tokens - live.tokens - 2560
        if room < 1024:
            live.save()
            raise ValueError(
                "the question and its instructions alone do not leave room to "
                "answer in a %d-token window" % window)
        reserve = min(asked, max(1024, int(room * 0.5)))
        if reserve < asked:
            self.state.log("  echo: %s tokens requested, %s fit per pass - "
                           "continuing across passes" % (f"{asked:,}", f"{reserve:,}"))
            payload = dict(payload)
            payload["max_tokens"] = reserve
            payload.pop("max_completion_tokens", None)
        session = ContextSession(self.state, conversation, max(0, room - reserve))
        session.budget = session.capacity
        session._fit()
        live.save()
        head = ""
        spill_path = None
        previous_command = None
        completed_operations = []
        calls = 0
        call_limit = int(payload.get("echo_max_calls", self.state.context_steps))
        if call_limit < 0:
            raise ValueError("echo_max_calls must be nonnegative")
        ledger = None
        target_words = int(payload.get("echo_target_words", word_target(question)))
        if target_words < 0:
            raise ValueError("echo_target_words must be nonnegative")
        empty_calls = 0
        repeated_controls = 0
        malformed_controls = 0
        last_output = None
        repeated_outputs = 0
        paragraph_break = False
        while call_limit == 0 or calls < call_limit:
            body = {k: v for k, v in payload.items() if not k.startswith("echo_")}
            body.pop("conversation_id", None)
            body["messages"] = [{"role": "system", "content": system_text}] + live.messages
            body["stream"] = False
            body["cache_prompt"] = True
            try:
                with self._upstream("/v1/chat/completions", body, stream=False) as response:
                    parsed = json.load(response)
            except Exception:
                if ledger:
                    ledger.update("interrupted")
                live.save()
                raise
            calls += 1
            choice = parsed["choices"][0]
            message = choice.get("message", {})
            recover_embedded_tool_call(message, payload.get("tools"))
            if message.get("tool_calls"):
                live.append({"role": "assistant", "content": message.get("content") or "",
                             "tool_calls": message["tool_calls"]}, count)
                live.save()
                session.save()
                parsed["echo"] = {**self._live_status(session, live, window), "backend_calls": calls,
                                  "tool_result_expected": True}
                if payload.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    self.wfile.write(("data: " + json.dumps({
                        "choices": [{"index": 0, "delta": {"tool_calls": message["tool_calls"]},
                                     "finish_reason": None}]}) + "\n\n").encode())
                    self.wfile.write(("data: " + json.dumps({
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                        "echo": parsed["echo"]}) + "\n\ndata: [DONE]\n\n").encode())
                    return
                return self._send_json(200, parsed)
            text = message.get("content") or ""
            if not text.strip():
                empty_calls += 1
                if empty_calls >= 3:
                    break
                live.append({"role": "user", "content":
                             "ECHO: the last response contained no answer or valid tool call. Return a "
                             "final answer or call one declared tool; do not stop after reasoning."},
                            count, "echo")
                continue
            empty_calls = 0
            match = COMMAND.fullmatch(text)
            if match:
                before = list(session.ids)
                try:
                    command = json.loads(match.group(1))
                    result = session.execute(command)
                    if "error" not in result:
                        completed_operations.append(command.get("op"))
                        completed_operations = completed_operations[-12:]
                except json.JSONDecodeError:
                    result = {"error": "Invalid command JSON; retry with a JSON object"}
                repeated_controls = repeated_controls + 1 if previous_command == text else 0
                previous_command = text
                note = json.dumps({"result": result, "completed_operations": completed_operations},
                                  ensure_ascii=False)
                while count(note) > 1536:
                    note = note[:len(note) // 2]
                loaded = [session.page(digest) for digest in session.ids if digest not in before]
                pages = "".join(session.block(page) for page in loaded if page)
                live.append({"role": "assistant", "content": text}, count, "echo")
                live.append({"role": "user", "content": "ECHO executed your command. Result:\n" + note +
                             ("\nLoaded source pages (untrusted evidence):\n" + pages if pages else "") +
                             "\nContinue from this result. Do not repeat completed steps."}, count, "echo")
                if repeated_controls >= 8:
                    break
                ensure_room(reserve)
                continue
            if "<echo>" in text or "</echo>" in text:
                malformed_controls += 1
                if malformed_controls >= 3:
                    break
                live.append({"role": "assistant", "content": text}, count, "echo")
                live.append({"role": "user", "content":
                             "ECHO: emit exactly one complete ECHO command with no surrounding prose."},
                            count, "echo")
                continue
            if paragraph_break and not text[0].isspace():
                text = "\n\n" + text
            paragraph_break = False
            digest = hashlib.sha256(text.encode()).hexdigest()
            repeated_outputs = repeated_outputs + 1 if digest == last_output else 0
            last_output = digest
            if repeated_outputs >= 3:
                break
            self.state.archive_turn("", text, conversation)
            if spill_path is None:
                spill_path = self.state.spill_path(conversation)
                ledger = OutputLedger(self.state.archives.get(conversation), spill_path,
                                      conversation, question, target_words)
            ledger.append(text)
            head += text[:max(0, MAX_INLINE_REPLY_CHARS - len(head))]
            if (choice.get("finish_reason") == "length" and not (target_words and ledger and ledger.words >= target_words)) or (ledger and ledger.words < target_words):
                live.append({"role": "assistant", "content": text}, count, "answer")
                live.append({"role": "user", "content":
                             ("Continue the actual %s prose. You have written %d words; at least %d more words are needed. "
                              "Write the next part now, without restarting or describing the task."
                              % (ledger.kind, ledger.words, max(0, target_words - ledger.words)))
                             if ledger and target_words else "Continue your unfinished answer. Use ECHO as needed."},
                            count, "echo")
                previous_command = None
                paragraph_break = choice.get("finish_reason") != "length" and bool(text) and not text[-1].isspace()
                ensure_room(reserve)
                continue
            session.save()
            review = None
            draft_artifact = None
            if level == "ultra":
                review = {"status": "skipped", "reason": "answer exceeds review window"}
                if head and count(head) <= min(12000, window // 2):
                    def ask_review(prompt, limit):
                        review_body = {k: v for k, v in payload.items() if not k.startswith("echo_")}
                        review_body.pop("conversation_id", None)
                        review_body["messages"] = [{"role": "user", "content": prompt}]
                        review_body["stream"] = False
                        review_body["max_tokens"] = limit or min(16384, max(2048, count(head) * 2))
                        with self._upstream("/v1/chat/completions", review_body, stream=False) as response:
                            answer = json.load(response)
                        return answer["choices"][0]["message"].get("content") or ""

                    try:
                        reviewed, passes = review_draft(question, head, ask_review)
                        review = {"status": "completed", "passes": passes, "revised": reviewed != head}
                        if reviewed != head:
                            draft_artifact = ledger.update("superseded") if ledger else None
                            spill_path = self.state.spill_path(conversation)
                            ledger = OutputLedger(self.state.archives.get(conversation), spill_path,
                                                  conversation, question, target_words)
                            ledger.append(reviewed)
                            self.state.archive_turn("", reviewed, conversation)
                            head = reviewed
                            text = reviewed
                    except Exception as error:
                        review = {"status": "failed", "error": str(error)}
            live.append({"role": "assistant", "content": text}, count, "answer")
            live.open = False
            live.save()
            artifact = ledger.update("complete") if ledger else None
            if artifact:
                self.state.archives.get(conversation).append(
                    "ECHO output record: " + json.dumps(artifact), conversation)
            parsed["choices"][0]["message"]["content"] = head
            parsed["echo"] = {**self._live_status(session, live, window), "backend_calls": calls,
                              "answer_file": str(spill_path) if spill_path else None,
                              "stream_buffered": bool(payload.get("stream"))}
            parsed["echo"]["artifact"] = artifact
            if review is not None:
                parsed["echo"]["review"] = review
            if draft_artifact is not None:
                parsed["echo"]["draft_artifact"] = draft_artifact
            if spill_path and spill_path.stat().st_size > len(head.encode("utf-8")):
                head += "\n[Full answer saved to %s]" % spill_path
                parsed["choices"][0]["message"]["content"] = head
                parsed["echo"]["inline_truncated"] = True
            if payload.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                for start in range(0, len(head), 4096):
                    chunk = {"id": parsed.get("id", "echo"), "object": "chat.completion.chunk",
                             "choices": [{"index": 0, "delta": {"content": head[start:start+4096]},
                                          "finish_reason": None}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": {},
                    "finish_reason": choice.get("finish_reason", "stop")}], "echo": parsed["echo"]}) +
                    "\n\ndata: [DONE]\n\n").encode())
                return
            return self._send_json(200, parsed)
        session.save()
        live.save()
        artifact = ledger.update("incomplete") if ledger else None
        return self._send_json(422, {"error": {"message": "ECHO call limit or no-progress guard reached; partial output saved"},
                                    "echo": {**self._live_status(session, live, window), "backend_calls": calls,
                                             "artifact": artifact}})

    def _ultra(self, payload: dict, question: str, conversation: str,
               settings: dict):
        """Draft, critique, revise - the model checking its own work.

        Past a certain point a larger thinking budget on a single pass stops
        buying accuracy, because the model cannot see its own blind spot from
        inside the same pass. Ultra spends the extra compute on separate passes
        instead: one writes, one looks for concrete faults, one rewrites. If
        the critic finds nothing the draft is returned unchanged, so the third
        pass is only paid for when it has something to fix.
        """
        def ask(messages, budget, max_tokens=None):
            body = dict(payload)
            body["messages"] = messages
            body["reasoning_budget_tokens"] = budget
            body.pop("stream", None)
            if max_tokens:
                body["max_tokens"] = max_tokens
            with self._upstream("/v1/chat/completions", body, stream=False) as r:
                parsed = json.loads(r.read())
            recover_embedded_tool_call(parsed["choices"][0]["message"], payload.get("tools"))
            return parsed, (parsed["choices"][0]["message"].get("content") or "")

        try:
            self.state.log("  echo: ultra pass 1/3 - drafting")
            parsed, draft = ask(payload["messages"], settings["budget"])
            usage = dict(parsed.get("usage") or {})
            tool_calls = parsed["choices"][0]["message"].get("tool_calls") or []
            final = draft
            if tool_calls:
                # A tool call is an action request, not a draft answer. Reviewing
                # empty prose here can rewrite or suppress the action entirely.
                self.state.log("  echo: ultra - executing the model's tool call before review")
            else:
                self.state.log("  echo: ultra pass 2/3 - critiquing")
                review, issues = ask([{"role": "user",
                                       "content": CRITIQUE_PROMPT % (question, draft)}],
                                     settings["budget"], max_tokens=1500)
                for key, value in (review.get("usage") or {}).items():
                    if isinstance(value, (int, float)):
                        usage[key] = usage.get(key, 0) + value

                if "NO ISSUES" in issues.upper()[:400]:
                    self.state.log("  echo: ultra - critic found nothing, keeping the draft")
                else:
                    self.state.log("  echo: ultra pass 3/3 - revising")
                    revision, revised = ask([{"role": "user",
                                              "content": REVISE_PROMPT % (question, draft, issues)}],
                                            settings["budget"])
                    for key, value in (revision.get("usage") or {}).items():
                        if isinstance(value, (int, float)):
                            usage[key] = usage.get(key, 0) + value
                    if revised.strip():
                        final = revised

                parsed["choices"][0]["message"]["content"] = final
                parsed["choices"][0]["finish_reason"] = "stop"
            parsed["usage"] = usage
            body = json.dumps(parsed).encode()
        except Exception as error:
            return self._send_json(502, {"error": {"message":
                "ultra reasoning failed: %s" % error}})

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if not tool_calls:
            self.state.archive_turn(question, final, conversation)

    def _compact(self, question: str, conversation: str, payload: dict):
        """Trim to the window and reclaim disk. No model calls, so seconds.

        This is /trim. /compact does the same thing and then writes a summary
        of what remains, which costs model time; trimming and compacting on
        their own is disk work and finishes in a fraction of a second.
        """
        parts = question.split()
        target = parts[1] if len(parts) > 1 and not parts[1].isdigit() else conversation
        window = next((int(p) for p in parts[1:] if p.isdigit()),
                      self.state.window_tokens)
        archive = self.state.archives.get(target)
        try:
            before = archive.disk_usage()["total_bytes"]
            pages_before = archive.stats()["pages"]
            lines = []
            if window > 0:
                rolled = archive.roll_window(target, window)
                if rolled.get("moved"):
                    lines.append(
                        "Trimmed to the most recent %s tokens: %s older pages "
                        "moved to a cold archive (%s). They stay exact and "
                        "searchable there - nothing was deleted."
                        % (f"{window:,}", f"{rolled['moved']:,}",
                           Path(rolled["cold_archive"]).name))
                else:
                    lines.append("Already inside the %s-token window; nothing "
                                 "needed moving." % f"{window:,}")
            compacted = archive.compact()
            after = compacted["after_bytes"]
            lines.append("Compacted %.1f MB -> %.1f MB (%.0f%% smaller), %s "
                         "pages live."
                         % (before / 1e6, after / 1e6,
                            100.0 * (before - after) / max(1, before),
                            f"{archive.stats()['pages']:,}"))
            lines.append("Started from %s pages. No model was run, so this took "
                         "under a second - use /compact if you also want a "
                         "summary written."
                         % f"{pages_before:,}")
            text = "\n\n".join(lines)
            self.state.log("  echo: /compact '%s' %.1f MB -> %.1f MB"
                           % (target, before / 1e6, after / 1e6))
        except Exception as error:
            text = "Could not compact '%s': %s" % (target, error)
        body = json.dumps({
            "id": "echo-compact", "object": "chat.completion",
            "model": payload.get("model", "opencore"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _summarize(self, question: str, conversation: str, payload: dict):
        """Answer /summarize in place, without involving the archive's memory.

        Deliberately not routed through the normal path: retrieval would inject
        a slice of the conversation into a request whose whole purpose is to
        read all of it.
        """
        parts = question.split()
        target = parts[1] if len(parts) > 1 and not parts[1].isdigit() else conversation
        limit = next((int(p) for p in parts[1:] if p.isdigit()), 0)
        archive = self.state.archives.get(target)
        self.state.log("  echo: /summarize '%s'%s"
                       % (target, " (max %d pages)" % limit if limit else ""))
        try:
            notes = []
            size_before = archive.disk_usage()["total_bytes"]

            # 1. Trim first. The window is what gets summarised, so it has to
            #    be established before the model reads anything - otherwise the
            #    expensive pass covers history that is about to be set aside.
            #    Older pages move to a cold archive rather than being erased:
            #    the summary is lossy and must not become their only copy.
            rolled = None
            if self.state.window_tokens > 0:
                rolled = archive.roll_window(target, self.state.window_tokens)
                if rolled.get("moved"):
                    self.state.log("  echo: trimmed to the newest %s tokens, "
                                   "%s pages moved to cold storage"
                                   % (f"{self.state.window_tokens:,}",
                                      f"{rolled['moved']:,}"))
                    notes.append(
                        "Trimmed to the most recent %s tokens first; %s older "
                        "pages moved to a cold archive (%s), still exact and "
                        "still searchable there."
                        % (f"{self.state.window_tokens:,}",
                           f"{rolled['moved']:,}",
                           Path(rolled["cold_archive"]).name))

            # 2. Summarise what remains inside the window.
            result = summarize(archive, target, self.state.upstream,
                               max_pages=limit, progress=self.state.log,
                               salience=0.0, older_archive=self.state.cold_archive_for(target))
            text = format_result(target, result)
            archive.save_summary(target, text, result["pages_read"], result.get("model_calls", 0),
                                 result.get("truncated", False), result.get("incomplete", False))

            # 3. Compress what is left.
            compacted = archive.compact()
            size_after = compacted["after_bytes"]
            notes.append("Archive compacted: %.1f MB -> %.1f MB (%.0f%% smaller)."
                         % (size_before / 1e6, size_after / 1e6,
                            100.0 * (size_before - size_after) / max(1, size_before)))
            text += PARAGRAPH + "*" + " ".join(notes) + "*"
        except Exception as error:
            text = "Could not summarise '%s': %s" % (target, error)
        body = json.dumps({
            "id": "echo-summary", "object": "chat.completion",
            "model": payload.get("model", "opencore"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _passthrough(self, payload: dict):
        try:
            with self._upstream(self.path, payload, stream=False) as response:
                body = response.read()
        except urllib.error.URLError as error:
            return self._send_json(502, {"error": {"message": str(error)}})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _complete(self, payload: dict, question: str, conversation: str):
        """Answer, continuing across window boundaries until the model stops.

        `finish_reason: "length"` means the model ran out of room, not that it
        finished. Returning that to the client is where a long piece of work
        dies. Instead the partial answer is fed back as an assistant turn and
        generation resumes, so the only thing that ends a response is the model
        deciding it is done.
        """
        # Output is streamed to disk, not accumulated in memory. A response
        # long enough to need continuations is also long enough that holding it
        # as a Python string is the thing that fails first: a billion tokens is
        # 3.1 GB of text. Only a bounded head is kept for the reply body.
        parts: list[str] = []
        head_chars = 0
        finish = "stop"
        body = b"{}"
        parsed: dict = {}
        spill = None
        spill_path = None
        produced = 0

        unlimited = self.state.max_continuations <= 0
        attempt = -1
        while True:
            attempt += 1
            if not unlimited and attempt > self.state.max_continuations:
                self.state.log("  echo: continuation cap reached")
                break
            with self._upstream("/v1/chat/completions", payload, stream=False) as response:
                body = response.read()
            try:
                parsed = json.loads(body)
                choice = parsed["choices"][0]
                piece = choice["message"].get("content") or ""
                finish = choice.get("finish_reason") or "stop"
            except Exception:
                break
            # Commit each fragment before a continuation can evict it or a
            # memory search asks about it. This also preserves partial answers
            # when a later upstream call fails.
            if piece:
                self.state.archive_turn("", piece, conversation)
            produced += len(piece)
            if head_chars < MAX_INLINE_REPLY_CHARS:
                parts.append(piece)
                head_chars += len(piece)
            if spill is None and produced > MAX_INLINE_REPLY_CHARS:
                spill_path = self.state.spill_path(conversation)
                spill = open(spill_path, "w", encoding="utf-8")
                spill.write("".join(parts))
                self.state.log("  echo: long answer - streaming to %s"
                               % spill_path.name)
            elif spill is not None:
                spill.write(piece)
                spill.flush()
            request = ECHO_SEARCH_RE.search(piece)
            if request and self.state.allow_model_search:
                term = request.group(1)[:200]
                hits = self.state.archives.get(conversation).find_word(
                    term, conversation_id=conversation)
                cold = self.state.cold_archive_for(conversation)
                if cold is not None:
                    hits += cold.find_word(term, conversation_id=conversation)
                self.state.log("  echo: model searched %r - %d passage(s)"
                               % (term, len(hits)))
                if hits:
                    found = PARAGRAPH.join("[%s] %s" % (h["when"], h["snippet"])
                                           for h in hits[:8])
                    note = ("Passages where you used %r before:" % term
                            + PARAGRAPH + found + PARAGRAPH +
                            "Continue your answer, consistent with these.")
                else:
                    note = ("ECHO did not find %r. This does not prove it is absent "
                            "from the conversation. Continue with that uncertainty." % term)
                messages = list(payload.get("messages") or [])
                messages.append({"role": "assistant", "content": piece})
                messages.append({"role": "user", "content": note})
                payload = dict(payload)
                payload["messages"], _ = self.state.fit_window(
                    messages, int(payload.get("max_tokens") or 2048))
                continue

            if finish != "length":
                break

            # No content at all, but the model was cut off: the reasoning
            # budget consumed the whole reply allowance before the answer
            # started. Continuing would append an empty assistant turn, which
            # the server rejects with a 400 - so raise the allowance and retry
            # rather than build an invalid request out of nothing.
            if not piece.strip():
                current = int(payload.get("max_tokens") or 2048)
                budget = int(payload.get("reasoning_budget_tokens") or 0)
                room = max(current * 2, budget + 1024)
                if room > 32768 or attempt > 6:
                    self.state.log("  echo: model produced only reasoning and no "
                                   "answer; giving up after %d attempt(s)" % (attempt + 1))
                    break
                self.state.log("  echo: reasoning used the whole reply budget "
                               "(%d); retrying with max_tokens=%d" % (budget, room))
                payload = dict(payload)
                payload["max_tokens"] = room
                continue

            self.state.log("  echo: hit the window, continuing (%d, %s tokens so far)"
                           % (attempt + 1, f"{int(produced / 3.10):,}"))
            messages = list(payload.get("messages") or [])
            messages.append({"role": "assistant", "content": piece})
            reserve = int(payload.get("max_tokens") or 2048)
            payload = dict(payload)
            payload["messages"], _ = self.state.fit_window(messages, reserve)

        if spill is not None:
            spill.close()
            reply = ("".join(parts)
                     + PARAGRAPH
                     + "[...continues - the full answer is %s tokens and was "
                       "written to %s, because a reply this long cannot be "
                       "delivered inline.]"
                       % (f"{int(produced / 3.10):,}", spill_path))
        else:
            reply = "".join(parts)
        if len(parts) > 1 and parsed:
            # Hand back one coherent answer, not the last fragment of it.
            try:
                parsed["choices"][0]["message"]["content"] = reply
                parsed["choices"][0]["finish_reason"] = finish
                body = json.dumps(parsed).encode()
            except Exception:
                pass
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, payload: dict, question: str, conversation: str):
        # An SSE body has no Content-Length and this handler does not chunk, so
        # the end of the stream has to be signalled by closing the socket.
        # Advertising keep-alive here leaves the client waiting for an end that
        # never arrives.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        collected: list[str] = []
        collected_chars = 0
        pending = b""
        with self._upstream("/v1/chat/completions", payload, stream=True) as response:
            while True:
                chunk = response.read1(65536) if hasattr(response, "read1") \
                    else response.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                pending += chunk
                # Accumulate deltas so the turn can still be archived.
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data in (b"[DONE]", b""):
                        continue
                    try:
                        delta = json.loads(data)["choices"][0].get("delta", {})
                    except Exception:
                        continue
                    piece = delta.get("content")
                    if piece:
                        collected.append(piece)
                        collected_chars += len(piece)
                        if collected_chars >= 3000:
                            self.state.archive_turn("", "".join(collected), conversation)
                            collected.clear()
                            collected_chars = 0
        if collected:
            self.state.archive_turn("", "".join(collected), conversation)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8812)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--upstream", default="http://127.0.0.1:8811",
                        help="the OpenCore llama-server")
    parser.add_argument("--archive", type=Path,
                        default=ROOT / "state" / "echo",
                        help="directory holding one archive file per conversation")
    parser.add_argument("--budget-chars", type=int, default=0,
                        help="cap on retrieved memory per request; 0 (default) "
                             "uses every character the window has free")
    parser.add_argument("--min-query-chars", type=int, default=12,
                        help="skip retrieval for trivially short messages")
    parser.add_argument("--recent-turns", type=int, default=6,
                        help="archive pages of conversation tail always restored "
                             "(0 disables continuity, leaving relevance only)")
    parser.add_argument("--max-continuations", type=int, default=0,
                        help="how many times a reply may resume after filling "
                             "the window; 0 (default) means never stop until "
                             "the model itself is done")
    parser.add_argument("--idle-seconds", type=float, default=60.0,
                        help="release the archive after this long with no "
                             "requests; it reopens on the next one")
    parser.add_argument("--hibernate-seconds", type=float, default=900.0,
                        help="after this long idle, drop the rebuildable "
                             "indexes and shrink the archive on disk; 0 to "
                             "never hibernate")
    parser.add_argument("--window-tokens", type=int, default=100_000_000,
                        help="after /summarize, keep this many tokens live; "
                             "older pages move to a cold archive beside it "
                             "(0 leaves everything in the live archive)")
    parser.add_argument("--salience", type=float, default=0.0,
                        help="0-1: rank pages and summarise only this fraction, "
                             "far faster on long histories (0 reads everything)")
    parser.add_argument("--console", dest="console", action="store_true",
                        default=None,
                        help="accept typed commands (default: on when this is "
                             "a terminal)")
    parser.add_argument("--no-console", dest="console", action="store_false")
    parser.add_argument("--offload-every", type=int, default=1000,
                        help="check for offload after this many new tokens; "
                             "0 offloads only on /compact")
    parser.add_argument("--reasoning", default=DEFAULT_REASONING,
                        choices=sorted(REASONING_LEVELS),
                        help="default effort when a request does not ask for one")
    parser.add_argument("--no-model-search", dest="model_search",
                        action="store_false", default=True,
                        help="stop the model searching its own memory mid-answer")
    parser.add_argument("--context-size", type=int, default=0,
                        help="Backend context ceiling for providers without /props; match backend configuration")
    parser.add_argument("--context-steps", type=int, default=0,
                        help="Maximum backend calls per answer; 0 permits unlimited continuation with no-progress guards")
    parser.add_argument("--live-high", type=float, default=0.85,
                        help="compact the live transcript once it fills this fraction of the window")
    parser.add_argument("--live-low", type=float, default=0.45,
                        help="after compaction, keep about this fraction of the window live")
    parser.add_argument("--legacy-context", action="store_true",
                        help="Use previous automatic retrieval instead of model-controlled context")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if args.context_size < 0 or args.context_steps < 0:
        parser.error("context-size and context-steps must be nonnegative")

    archives = ArchiveSet(args.archive, idle_seconds=args.idle_seconds)
    Handler.state = EchoState(archives, args.upstream, args.budget_chars,
                              args.min_query_chars, not args.quiet,
                              args.max_continuations, args.recent_turns,
                              args.window_tokens, args.salience,
                              args.offload_every, args.reasoning,
                              args.model_search)
    Handler.state.autonomous_context = not args.legacy_context and args.model_search
    Handler.state.context_steps = args.context_steps
    if not 0 < args.live_low < args.live_high <= 1:
        parser.error("need 0 < live-low < live-high <= 1")
    Handler.state.live_high, Handler.state.live_low = args.live_high, args.live_low
    if args.context_size:
        Handler.state._ctx_size = args.context_size

    disk = archives.total_disk()
    print("ECHO proxy")
    print("  listening on http://%s:%d/v1" % (args.host, args.port))
    print("  upstream    %s" % args.upstream)
    print("  archives    %s" % args.archive)
    print("  holding     %d conversation(s), %.1f MB on disk"
          % (disk["conversations"], disk["total_bytes"] / 1e6))
    print("  ceiling     3.17 trillion tokens per conversation "
          "(17.59 TB), no limit on conversations")
    print("  memory      %s" % ("all free space in the model's window"
                                 if args.budget_chars <= 0
                                 else "%s chars per request" % f"{args.budget_chars:,}"))
    print()

    # Release the archive's connection and page cache when nothing is talking
    # to it, so an idle ECHO costs almost nothing and a billion-token archive
    # is not held open for a conversation that ended hours ago.
    def reaper():
        while True:
            time.sleep(max(5.0, args.idle_seconds / 4))
            try:
                archives.maintain(args.hibernate_seconds, Handler.state.log)
            except Exception:
                pass

    threading.Thread(target=reaper, daemon=True).start()

    def console():
        """Accept commands typed into this window.

        The model's own window is occupied by llama-server and cannot take
        input, so the memory window is where commands are typed. Everything
        here is also available as a /command inside any chat client; this is
        the same code path, just reachable without a client open.
        """
        helptext = NEWLINE.join([
            "  compact [conversation]   trim to the window, summarise what "
            "remains, shrink",
            "  trim    [conversation]   trim and shrink only - no model, "
            "under a second",
            "  list                     conversations on disk",
            "  stats                    disk usage",
            "  quit                     stop the memory layer",
        ])
        print(helptext + PARAGRAPH)
        while True:
            try:
                line = input("echo> ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not line:
                continue
            word = line.split()
            verb = word[0].lstrip("/").lower()
            target = word[1] if len(word) > 1 else "default"
            try:
                if verb in ("quit", "exit", "stop"):
                    print("stopping the memory layer; the model keeps running.")
                    server.shutdown()
                    return
                if verb in ("help", "?"):
                    print(helptext)
                elif verb == "list":
                    names = archives.known_conversations()
                    print("  " + ((NEWLINE + "  ").join(names)
                                  if names else "(none yet)"))
                elif verb == "stats":
                    d = archives.total_disk()
                    print("  %d conversation(s), %.1f MB on disk"
                          % (d["conversations"], d["total_bytes"] / 1e6))
                elif verb in ("trim", "compact", "summarize", "summarise"):
                    archive = archives.get(target)
                    before = archive.disk_usage()["total_bytes"]
                    rolled = archive.roll_window(target, args.window_tokens)                         if args.window_tokens > 0 else {}
                    if rolled.get("moved"):
                        print("  trimmed: %s pages moved to %s"
                              % (f"{rolled['moved']:,}",
                                 Path(rolled["cold_archive"]).name))
                    if verb != "trim":
                        print("  summarising what remains...")
                        result = summarize(archive, target, args.upstream,
                                           progress=print, salience=args.salience)
                        print()
                        print(format_result(target, result))
                    after = archive.compact()["after_bytes"]
                    print("  %.1f MB -> %.1f MB (%.0f%% smaller)"
                          % (before / 1e6, after / 1e6,
                             100.0 * (before - after) / max(1, before)))
                else:
                    print("  unknown command. type help")
            except Exception as error:
                print("  failed: %s" % error)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    want_console = args.console
    if want_console is None:
        want_console = bool(sys.stdin and sys.stdin.isatty())
    if want_console:
        threading.Thread(target=console, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        archives.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
