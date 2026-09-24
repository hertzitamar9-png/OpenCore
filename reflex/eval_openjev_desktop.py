"""Zero-shot OpenJev on the same held-out Windows control-picking cases as eval_desktop.py.

usage: eval_openjev_desktop.py OPENJEV_DIR SUBFOLDER desktop_uia.json eval_cases.jsonl
"""
import json
import sys
import time

import torch

import desktop_task as task


def main():
    root, subfolder, uia, cases_path = sys.argv[1:5]
    sys.path.insert(0, root + "/code")
    import transformers
    original = transformers.AutoModelForSequenceClassification.from_pretrained
    # The checkpoint's score head has 3 NLI labels; newer transformers infers 2 from its config.
    def three_labels(path, **kw):
        config = transformers.AutoConfig.from_pretrained(path, subfolder=kw.get("subfolder"))
        labels = {0: "contradiction", 1: "entailment", 2: "neutral"}
        for target in (config, config.get_text_config()):
            target.num_labels, target.id2label = 3, labels
            target.label2id = {v: k for k, v in labels.items()}
        return original(path, config=config, **kw)
    transformers.AutoModelForSequenceClassification.from_pretrained = three_labels
    from openjev_decide import RUBRIC_MARK, OpenJev
    jev = OpenJev.from_pretrained(root, subfolder=subfolder, device="cuda", dtype=torch.bfloat16)
    windows = json.load(open(uia, encoding="utf-8"))
    cases = [json.loads(line) for line in open(cases_path, encoding="utf-8")]
    op_ok = target_ok = 0
    latencies = []
    for case in cases:
        window = windows[case["window"]]
        table = task.candidates(window["elements"], keep=int(case["gold_id"]))
        state = task.state(window["title"], window["elements"])
        questions = [
            {"type": "choice", "options": list(task.OPERATIONS),
             "instructions": "Which operation does this request need: %s" % case["goal"] + RUBRIC_MARK + json.dumps(task.OPERATIONS)},
            {"type": "choice", "options": list(table),
             "instructions": "Which control in this window performs the request: %s" % case["goal"] + RUBRIC_MARK
             + json.dumps(table, ensure_ascii=False)},
        ]
        torch.cuda.synchronize()
        started = time.perf_counter()
        op, target = jev.decide(state, questions)
        torch.cuda.synchronize()
        latencies.append((time.perf_counter() - started) * 1000)
        op_ok += max(op["probabilities"], key=op["probabilities"].get) == case["gold_op"]
        target_ok += max(target["probabilities"], key=target["probabilities"].get) == case["gold_id"]
    n = max(1, len(cases))
    print("openjev %s: cases %d | operation %.1f%% | control %.1f%% | %.0f ms per decision (median %.0f)"
          % (subfolder, len(cases), 100 * op_ok / n, 100 * target_ok / n, sum(latencies) / n, sorted(latencies)[len(latencies) // 2]))


if __name__ == "__main__":
    main()
