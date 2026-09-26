#!/usr/bin/env python3
"""Reproducible disk-archive scaling check for the ECHO implementation.

This measures exact SQLite archive writes and lexical retrieval only. It does
not start a model, create attention/KV state, or measure answer quality.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import platform
import sys
import tempfile
from pathlib import Path
from time import perf_counter


REPO = Path(__file__).resolve().parents[1]
ECHO_RESOURCES = REPO / "src-tauri" / "resources" / "echo"
sys.path.insert(0, str(ECHO_RESOURCES))

from evoagent.echo_memory import EchoArchive  # noqa: E402


ANCHOR = "quartz launch key is amberwood 7f3c"


def run_case(token_count: int) -> dict[str, object]:
    words = ANCHOR.split()
    if token_count <= len(words):
        raise ValueError(f"token count must exceed {len(words)}")

    # These are exact whitespace-delimited synthetic tokens, not model tokens.
    text = " ".join(words + ["record"] * (token_count - len(words)))
    with tempfile.TemporaryDirectory(prefix="opencore-echo-scale-") as temporary:
        archive = EchoArchive(Path(temporary) / "archive.db")
        try:
            started = perf_counter()
            pages = archive.append(text, "scale-test", timestamp=1.0)
            append_seconds = perf_counter() - started

            started = perf_counter()
            hits = archive.hybrid_search("quartz amberwood", 8, "scale-test")
            search_seconds = perf_counter() - started

            started = perf_counter()
            verification = archive.verify()
            verify_seconds = perf_counter() - started

            first_page = archive.load(pages[0].page_id) if pages else None
            usage = archive.disk_usage()
            exact = bool(first_page and ANCHOR in first_page.text)
            searchable = bool(pages and pages[0].page_id in hits)
            corrupt_pages = verification.get("corrupt")
            if not exact or not searchable or corrupt_pages != 0:
                raise RuntimeError(
                    "ECHO archive scale check failed: "
                    f"oldest_exact={exact}, oldest_searchable={searchable}, "
                    f"corrupt_pages={corrupt_pages}"
                )
            return {
                "synthetic_whitespace_tokens": len(text.split()),
                "indexed_pages": len(pages),
                "archive_bytes": usage.get("total_bytes"),
                "append_seconds": round(append_seconds, 4),
                "oldest_fact_search_seconds": round(search_seconds, 4),
                "full_integrity_check_seconds": round(verify_seconds, 4),
                "oldest_fact_exact": exact,
                "oldest_fact_searchable": searchable,
                "corrupt_pages": corrupt_pages,
                "scope": "temporary exact ECHO SQLite archive; no model inference",
            }
        finally:
            archive.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=[100_000, 1_000_000, 10_000_000],
        help="synthetic whitespace-token counts to test",
    )
    parser.add_argument("--output", type=Path, help="also save the JSON report here")
    args = parser.parse_args()
    if any(size <= 0 for size in args.sizes):
        parser.error("all sizes must be positive")

    report = {
        "benchmark": "echo-exact-archive-scaling",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "token_counting": "whitespace-delimited synthetic tokens",
        "scope": (
            "Measures SQLite exact-page storage, indexed lookup, and archive integrity. "
            "Does not measure model attention, KV memory, prompt reconstruction, or answer quality."
        ),
        "runs": [run_case(size) for size in args.sizes],
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
