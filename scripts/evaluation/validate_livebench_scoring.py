"""Validate pinned LiveBench graders with control fixtures, not model answers."""
from datetime import datetime
import argparse
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import platform

from inspect_ai import Task, eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.solver import generate, system_message
from inspect_evals.livebench.scorer import decide_scorer
from inspect_evals.livebench.utils import ensure_nltk_resource

ROOT = Path(__file__).resolve().parent
RELEASE = datetime(2024, 7, 26)


def coding_question(functional):
    kind = "functional" if functional else "stdin"
    def case(a, b):
        return {"input": f"{a}\n{b}" if functional else f"{a} {b}\n",
                "output": str(a + b), "testtype": kind}
    return {"question_title": "scorer control addition",
            "partial_solution": None,
            "public_test_cases": json.dumps([case(1, 2), case(-2, 5)]),
            "private_test_cases": json.dumps([case(0, 0)]),
            "original_json": {"metadata": json.dumps(
                {"func_name": "sum_two"} if functional else {})}}


def fixtures(release=RELEASE):
    specs = [
        ("math", "aime_i_2024", "42", "42", {}),
        ("math", "amps_hard_derivatives", r"\frac{1}{2}",
         r"\boxed{\frac{1}{2}}", {}),
        ("reasoning", "web_of_lies_v2", "yes, no, unknown",
         "<solution>yes, no, unknown</solution>", {}),
        ("data_analysis", "cta", "integer", "integer", {}),
        ("language", "typos", "the quick brown fox",
         "<solution>the quick brown fox</solution>", {}),
        ("instruction_following", "keywords", "", "opencore benchmark",
         {"instruction_following": {"instruction_id_list": ["keywords:existence"],
                                     "kwargs": [{"keywords": ["opencore", "benchmark"]}]}}),
        ("coding", "LCB_generation", "",
         "```python\ndef sum_two(a, b):\n    return a + b\n```",
         {"coding": coding_question(True)}),
        ("coding", "coding_completion", "",
         "```python\nimport sys\na, b = map(int, sys.stdin.read().split())\nprint(a + b)\n```",
         {"coding": coding_question(False)}),
    ]
    samples, answers, expected = [], {}, {}
    for index, (category, task, target, good, extra) in enumerate(specs):
        for correct in (True, False):
            identity = f"control-{index}-{int(correct)}"
            prompt = f"Scorer control {identity}: follow the stated test fixture."
            metadata = {"category": category, "task": task,
                        "livebench_release_date": release, **extra}
            answers[prompt] = good if correct else "scorer negative control, no valid answer"
            expected[identity] = float(correct)
            samples.append(Sample(id=identity, input=prompt, target=target, metadata=metadata))
    return samples, answers, expected


def main(output=ROOT, release=RELEASE):
    if platform.system() != "Linux":
        raise RuntimeError("The unmodified coding grader needs Linux SIGALRM")
    from prepare_benchmark import verify_environment
    verify_environment('livebench', importlib.import_module('inspect_evals.livebench.livebench'))
    if (output / 'livebench-scorer-validation.json').exists() or (output / 'scorer-validation').exists():
        raise FileExistsError(f'Refusing to overwrite scorer evidence: {output}')
    output.mkdir(parents=True, exist_ok=True)
    ensure_nltk_resource()
    samples, answers, expected = fixtures(release)
    def fixture(messages, _tools, _choice, _config):
        return ModelOutput.from_content("livebench-control-fixture", answers[messages[-1].text])
    task = Task(dataset=MemoryDataset(samples),
                solver=[system_message("You are a helpful assistant."), generate()],
                scorer=decide_scorer())
    logs = eval(task, model=get_model("mockllm/livebench-controls", custom_outputs=fixture),
                epochs=1, max_samples=1, max_connections=1, max_subprocesses=1,
                fail_on_error=True, display="none",
                log_dir=str(output / "scorer-validation" / "livebench-controls"))
    log = logs[0]
    rows = [{"id": sample.id, "category": sample.metadata["category"],
             "task": sample.metadata["task"], "expected": expected[sample.id],
             "score": sample.scores["decide_scorer"].value if sample.scores else None,
             "error": str(sample.error) if sample.error else None}
            for sample in log.samples or []]
    report = {"scope": "Scorer infrastructure validation using synthetic positive and negative controls",
              "model_quality_measured": False, "platform": platform.platform(),
              "inspect_ai": importlib.metadata.version("inspect-ai"),
              "inspect_evals": importlib.metadata.version("inspect-evals"),
              "livebench_revision": "1c4c65530fb69f797f7e6101c367b51c05f8cb64",
              "fixture_release_date": release.isoformat(),
              "fixture_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "status": log.status, "samples": len(rows), "fixtures": rows}
    (output / "livebench-scorer-validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    assert log.status == "success" and len(rows) == len(samples)
    assert all(not row["error"] and row["score"] == row["expected"] for row in rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release-date', choices=('2024-07-26', '2024-11-25'), default='2024-11-25')
    args = parser.parse_args()
    main(args.output, datetime.fromisoformat(args.release_date))
