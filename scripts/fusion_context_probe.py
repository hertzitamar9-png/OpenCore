"""Bounded real-model prompt growth probe; records failures as evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def post(base, path, payload, timeout=180):
    request = Request(base + path, json.dumps(payload).encode(),
                      {"Content-Type": "application/json"})
    return urlopen(request, timeout=timeout)


def tokens(base, text):
    with post(base, "/tokenize", {"content": text}, timeout=30) as response:
        return len(json.load(response)["tokens"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8850")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--targets", type=int, nargs="+", default=[1024, 4096, 7168])
    args = parser.parse_args()
    line = "def combine(left, right): return left + right\n"
    results = []
    try:
        for target in args.targets:
            unit = max(1, tokens(args.base, line))
            count = max(1, target // unit)
            prompt = line * count + "\nFinish: def add(a, b): return"
            content_tokens = tokens(args.base, prompt)
            request = {"messages": [{"role": "user", "content": prompt}],
                       "stream": False, "max_tokens": 1}
            start = time.perf_counter()
            try:
                with post(args.base, "/v1/chat/completions", request) as response:
                    result = json.load(response)
                entry = {"target_content_tokens": target,
                         "measured_content_tokens": content_tokens,
                         "measured_chat_prompt_tokens": result["usage"]["prompt_tokens"],
                         "elapsed_seconds": round(time.perf_counter() - start, 3),
                         "generated": result["choices"][0]["message"]["content"],
                         "status": "ok"}
            except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as error:
                detail = error.read().decode("utf-8", "replace") if isinstance(error, HTTPError) else str(error)
                entry = {"target_content_tokens": target,
                         "measured_content_tokens": content_tokens,
                         "elapsed_seconds": round(time.perf_counter() - start, 3),
                         "status": "failed", "error": detail[:500]}
            results.append(entry)
            print(json.dumps(entry), flush=True)
            if entry["status"] != "ok":
                break
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"results": results}, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if results and all(r["status"] == "ok" for r in results) else 1)


if __name__ == "__main__":
    main()
