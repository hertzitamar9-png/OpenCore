"""Desktop control-picking items for Reflex from UIA dumps + OpenCore-written goals.

usage: build_desktop_items.py BASE_DIR desktop_uia.json desktop_goals.jsonl OUT_TRAIN.pt OUT_EVAL.jsonl [holdout_window=6]
"""
import json
import os
import random
import sys

import torch
from transformers import AutoTokenizer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "laya_core"))
import desktop_task as task  # noqa: E402
from rl_common import QTYPES, build_sequence  # noqa: E402


def encode(tok, st, question, gold, rng):
    keys = list(question["criteria"])
    order = list(range(len(keys)))
    rng.shuffle(order)
    internal = {"t": "choice", "ins": json.dumps(question["instructions"], ensure_ascii=False), "crit": question["criteria"]}
    ids, markers = build_sequence(tok, st, internal, 1024, 768, option_order=order)
    if len(markers) != len(keys):
        return None
    target = [1.0 if keys[i] == gold else 0.0 for i in order]
    return {"ids": ids, "markers": markers, "qtype": QTYPES["choice"], "target": target, "label": target.index(1.0),
            "task": "desktop"}


def main():
    base, uia_path, goals_path, out_train, out_eval = sys.argv[1:6]
    holdout = int(sys.argv[6]) if len(sys.argv) > 6 else 6
    tok = AutoTokenizer.from_pretrained(os.path.join(base, "tokenizer"))
    windows = json.load(open(uia_path, encoding="utf-8"))
    rows = [json.loads(line) for line in open(goals_path, encoding="utf-8")]
    rng = random.Random(5)
    items, evals, blocked = [], [], 0
    for row in rows:
        window = windows[row["window"]]
        element = next(e for e in window["elements"] if e["elementId"] == row["elementId"])
        table = task.candidates(window["elements"], keep=row["elementId"])
        if str(row["elementId"]) not in table:
            continue
        st = task.state(window["title"], window["elements"])
        op = task.operation_for(row["controlType"])
        for goal in row["goals"]:
            if row["window"] == holdout:
                evals.append({"window": row["window"], "goal": goal, "gold_op": op, "gold_id": str(row["elementId"]),
                              "name": row["name"]})
                continue
            qs = task.questions(goal, table)
            for qid, gold in (("operation", op), ("target", str(row["elementId"]))):
                item = encode(tok, st, qs[qid], gold, rng)
                if item:
                    items.extend([item] * (3 if qid == "operation" and op != "CLICK" else 1))
            # the same request asked in a window that cannot do it -> BLOCKED
            if rng.random() < 0.35:
                other_index = rng.choice([i for i in range(len(windows)) if i not in (row["window"], holdout)])
                other = windows[other_index]
                if not any(task.clean(e.get("name")) == row["name"] for e in other["elements"]):
                    other_table = task.candidates(other["elements"])
                    item = encode(tok, task.state(other["title"], other["elements"]),
                                  task.questions(goal, other_table)["operation"], "BLOCKED", rng)
                    if item:
                        items.append(item)
                        blocked += 1
    torch.save(items, out_train)
    with open(out_eval, "w", encoding="utf-8") as handle:
        for case in evals:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    lengths = [len(i["ids"]) for i in items]
    print("desktop items %d (blocked %d) | eval cases %d | tokens mean %.0f max %d"
          % (len(items), blocked, len(evals), sum(lengths) / max(1, len(lengths)), max(lengths or [0])))


if __name__ == "__main__":
    main()
