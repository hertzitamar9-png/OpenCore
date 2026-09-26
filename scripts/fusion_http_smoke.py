"""Record a bounded real-checkpoint Fusion HTTP streaming receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen


def get_json(base: str, path: str):
    with urlopen(base + path, timeout=10) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8850")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    props = get_json(args.base, "/props")
    models = get_json(args.base, "/v1/models")
    payload = {"model": "opencore-twincore-experimental",
               "messages": [{"role": "user", "content": "Reply with exactly: Hello world"}],
               "stream": True, "max_tokens": 3,
               "stream_options": {"include_usage": True}}
    request = Request(args.base + "/v1/chat/completions",
                      json.dumps(payload).encode(), {"Content-Type": "application/json"})
    start = time.perf_counter()
    events = []
    done = False
    with urlopen(request, timeout=120) as response:
        for raw in response:
            if not raw.startswith(b"data: "):
                continue
            line = raw[6:].strip()
            if line == b"[DONE]":
                done = True
                break
            events.append({"at_seconds": round(time.perf_counter() - start, 3),
                           "event": json.loads(line)})
    deltas = [entry["event"]["choices"][0]["delta"].get("content", "")
              for entry in events if entry["event"].get("choices") and
              entry["event"]["choices"][0].get("delta")]
    final = events[-1]["event"] if events else {}
    receipt = {"model": models["data"][0]["id"],
               "server_context_tokens": props["n_ctx"],
               "stream_done": done,
               "delta_count": len(deltas),
               "deltas": deltas,
               "text": "".join(deltas),
               "first_delta_seconds": next((entry["at_seconds"] for entry in events
                                            if entry["event"].get("choices") and
                                            entry["event"]["choices"][0].get("delta", {}).get("content")), None),
               "elapsed_seconds": round(time.perf_counter() - start, 3),
               "finish_reason": final.get("choices", [{}])[0].get("finish_reason"),
               "usage": final.get("usage"),
               "status": "ok" if done and deltas and final.get("usage") else "failed"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    raise SystemExit(0 if receipt["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
