"""Grade real captured completions with pinned, unchanged official scorers."""
from collections import Counter
from datetime import date
import hashlib
import importlib
import json
from pathlib import Path
import platform
import argparse

from inspect_ai import eval
from inspect_ai.model import ChatMessageAssistant, ModelOutput, ModelUsage, get_model
from inspect_ai.solver import chain, solver, system_message

from benchmark_capture import completion, file_hash, load_grading_capture, load_inputs


def livebench_release(inputs, provenance):
    declared = provenance.get('release_date')
    requested = inputs.get('release_date', declared)
    if requested != declared or requested not in ('2024-07-26', '2024-11-25'):
        raise ValueError('LiveBench release must match qualified public dataset provenance')
    return date.fromisoformat(requested)


def validate_official_inputs(task, inputs):
    expected = {str(sample.id): sample.input for sample in task.dataset}
    rows = inputs["rows"]
    if len(rows) != len(expected) or {str(row["id"]) for row in rows} != set(expected):
        raise ValueError("Captured and official sample identities differ")
    for row in rows:
        if row["prompt"] != expected[str(row["id"])]:
            raise ValueError(f"Captured input differs from the official prompt: {row['id']}")


def validate_pinned_task(task, inputs, provenance_path, task_source):
    if file_hash(provenance_path) != inputs['dataset_manifest_sha256']:
        raise ValueError('Pinned dataset provenance file hash differs from the captured inputs')
    saved = json.loads(provenance_path.read_text(encoding='utf-8'))
    source_scope = 'task module and recorded task hashes; historic source manifest'
    if saved.get('official_source_files') is not None:
        from source_integrity import validate_recorded_sources
        validate_recorded_sources(saved['official_source_files'])
        source_scope = 'all recorded official Python source files verified against installed wheel hashes'
    if saved.get('livebench_revision') is not None:
        from prepare_benchmark import LIVEBENCH_SCORER_REVISION, verify_livebench_revision
        if saved['livebench_revision'] != LIVEBENCH_SCORER_REVISION:
            raise ValueError('LiveBench scorer provenance differs from the qualified revision')
        verify_livebench_revision()
    if file_hash(task_source) != saved['task_source_sha256']:
        raise ValueError('Official scorer source differs from the pinned task')
    expected = {str(row['id']): row for row in saved['rows']}
    samples = list(task.dataset)
    if (len(samples) != saved.get('samples', saved.get('sample_count'))
            or len(samples) != len(expected) or {str(s.id) for s in samples} != set(expected)):
        raise ValueError('Pinned task sample identities differ')
    digest = lambda value: hashlib.sha256(value.encode('utf-8')).hexdigest()
    for sample in samples:
        row = expected[str(sample.id)]
        if digest(sample.input) != row['prompt_sha256']:
            raise ValueError(f'Pinned prompt hash differs: {sample.id}')
        if 'tests_sha256' in row and digest((sample.metadata or {}).get('test', '')) != row['tests_sha256']:
            raise ValueError(f'Pinned test hash differs: {sample.id}')
        if 'target_sha256' in row and digest(sample.target) != row['target_sha256']:
            raise ValueError(f'Pinned target hash differs: {sample.id}')
        if 'metadata_sha256' in row and digest(json.dumps(sample.metadata, sort_keys=True, default=str)) != row['metadata_sha256']:
            raise ValueError(f'Pinned grading metadata hash differs: {sample.id}')
    return {'all_recorded_hashes_match': True, 'samples': len(samples),
            'provenance_sha256': file_hash(provenance_path),
            'official_task_source_sha256': file_hash(task_source),
            'dataset_revision': saved.get('dataset_revision'),
            'dataset_revisions': saved.get('dataset_revisions'),
            'release_date': saved.get('release_date'), 'scorer_source_scope': source_scope}


def grade(task, inputs_path, capture_dir, output_dir, identity_path=None):
    inputs, manifest, records = load_grading_capture(inputs_path, capture_dir, identity_path)
    if (manifest['identity']['evidence_kind'] == 'real_model'
            and not manifest['identity_validation']['exact_identity_file_hash_verified']):
        raise ValueError('Historic real-model capture requires its original --identity file for verified replay')
    validate_official_inputs(task, inputs)
    pinned = None
    if manifest['identity']['evidence_kind'] == 'real_model':
        module_names = {'humaneval': 'inspect_evals.humaneval.humaneval',
                        'livebench': 'inspect_evals.livebench.livebench'}
        source = Path(importlib.import_module(module_names[inputs['benchmark']]).__file__)
        pinned = validate_pinned_task(task, inputs,
                                     inputs_path.with_name(inputs['benchmark'] + '-provenance.json'), source)
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite grading logs: {output_dir}")
    output_dir.mkdir(parents=True)
    by_id = {str(row["id"]): row for row in records}
    model = manifest["binding"]["model"]

    @solver
    def replay():
        async def solve(state, _generate):
            row = by_id[str(state.sample_id)]  # No missing-answer or canonical-answer fallback.
            text = completion(row["response"])
            state.output = ModelOutput.from_content(model, text)
            usage = row["response"].get("usage") or {}
            if all(isinstance(usage.get(key), int) for key in ("prompt_tokens", "completion_tokens", "total_tokens")):
                state.output.usage = ModelUsage(input_tokens=usage["prompt_tokens"],
                                                output_tokens=usage["completion_tokens"],
                                                total_tokens=usage["total_tokens"])
            state.messages.append(ChatMessageAssistant(content=text))
            return state
        return solve

    solvers = []
    if inputs.get("system_prompt") is not None:
        solvers.append(system_message(inputs["system_prompt"]))
    solvers.append(replay())
    task.solver = chain(*solvers)
    task.metadata = {**(task.metadata or {}), "captured_inference": manifest,
                     "grading_mode": "Replay recorded API answers; MockLLM performs no inference"}
    logs = eval(task, model=get_model("mockllm/recorded-api-answers"), sandbox="local",
                epochs=1, max_samples=1, max_subprocesses=1, max_connections=1,
                fail_on_error=True, display="none", log_dir=str(output_dir / "inspect-logs"))
    log = logs[0]
    samples = log.samples or []
    complete = log.status == "success" and len(samples) == len(task.dataset) and not any(s.error for s in samples)
    report = {"status": "scored" if complete else "invalid_or_partial",
              "scope": ("Actual captured model answers graded by the unchanged official task scorer"
                        if manifest["identity"]["evidence_kind"] == "real_model"
                        else "Non-model recorded controls graded by the unchanged official task scorer"),
              "model_quality_measured": complete and manifest["identity"]["evidence_kind"] == "real_model",
              "inference_model": model, "inference_identity": manifest["identity"],
              'inference_identity_validation': manifest['identity_validation'],
              "official_provenance": pinned,
              "generation_settings": manifest["binding"], "grading_transport_model": "mockllm/recorded-api-answers",
              "grading_platform": platform.platform(), "inputs_sha256": file_hash(inputs_path),
              "responses_sha256": file_hash(capture_dir / "responses.jsonl"),
              "capture_manifest_sha256": file_hash(capture_dir / "capture-manifest.json"),
              "grading_script_sha256": file_hash(Path(__file__)),
              "samples": len(samples), "expected_samples": len(task.dataset),
              "sample_errors": [s.id for s in samples if s.error],
              "finish_reasons": dict(Counter(row["response"]["choices"][0]["finish_reason"] for row in records)),
              "generation_seconds": sum(row["seconds"] for row in records),
              "results": log.results.model_dump(mode="json") if log.results else None,
              "per_sample": [{"id": s.id, "scores": {name: score.value for name, score in (s.scores or {}).items()},
                               "error": str(s.error) if s.error else None} for s in samples]}
    (output_dir / "grading-summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "per_sample"}, indent=2), flush=True)
    if not complete:
        raise RuntimeError("Grading did not complete the full official dataset")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", choices=("humaneval", "livebench"), required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument('--identity', type=Path,
                        help='Original hash-bound identity file, required for historic real-model captures without snapshots')
    args = parser.parse_args()
    inputs = load_inputs(args.inputs)
    if inputs['benchmark'] != args.benchmark:
        raise ValueError('Requested benchmark differs from the captured input benchmark')
    if args.benchmark == "humaneval":
        from inspect_evals.humaneval.humaneval import humaneval
        task = humaneval(sandbox="local")
    else:
        if platform.system() != "Linux":
            raise RuntimeError("LiveBench's unmodified coding scorer requires Linux SIGALRM")
        from inspect_evals.livebench.livebench import livebench
        provenance_path = args.inputs.with_name('livebench-provenance.json')
        if file_hash(provenance_path) != inputs['dataset_manifest_sha256']:
            raise ValueError('LiveBench release provenance differs from the captured inputs')
        provenance = json.loads(provenance_path.read_text(encoding='utf-8'))
        task = livebench(livebench_release_date=livebench_release(inputs, provenance))
    grade(task, args.inputs, args.captures, args.output, args.identity)


if __name__ == "__main__":
    main()
