"""Held-out Windows control picking accuracy for a Reflex checkpoint.

usage: eval_desktop.py MODEL_DIR desktop_uia.json eval_cases.jsonl
"""
import json
import sys
import time

import desktop_task as task
from reflex_model import Reflex


def main():
    model = Reflex(sys.argv[1])
    windows = json.load(open(sys.argv[2], encoding="utf-8"))
    cases = [json.loads(line) for line in open(sys.argv[3], encoding="utf-8")]
    op_ok = target_ok = both = 0
    latencies = []
    misses = []
    for case in cases:
        window = windows[case["window"]]
        table = task.candidates(window["elements"], keep=int(case["gold_id"]))
        started = time.perf_counter()
        answers = model.decide(task.state(window["title"], window["elements"]), task.questions(case["goal"], table))["answers"]
        latencies.append((time.perf_counter() - started) * 1000)
        op_hit = answers["operation"]["choice"] == case["gold_op"]
        target_hit = answers["target"]["choice"] == case["gold_id"]
        op_ok += op_hit
        target_ok += target_hit
        both += op_hit and target_hit
        if not target_hit and len(misses) < 8:
            misses.append((case["goal"], case["name"], table.get(answers["target"]["choice"], "?")))
    n = max(1, len(cases))
    print("cases %d | operation %.1f%% | control %.1f%% | both %.1f%% | %.0f ms per decision"
          % (len(cases), 100 * op_ok / n, 100 * target_ok / n, 100 * both / n, sum(latencies) / max(1, len(latencies))))
    for goal, gold, picked in misses:
        print("   miss: %r wanted %r picked %r" % (goal, gold, picked))


if __name__ == "__main__":
    main()
