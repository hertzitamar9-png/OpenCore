"""Export complete pinned official prompts and grading hashes, without inference."""
import argparse
from collections import Counter
from datetime import date
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import platform

from benchmark_capture import file_hash, write_manifest
from source_integrity import qualified_sources

PACKAGES = {'inspect-ai': '0.3.270', 'inspect-evals': '0.22.0'}
LIVEBENCH_SCORER_REVISION = '1c4c65530fb69f797f7e6101c367b51c05f8cb64'
HUMANEVAL_REVISION = '7dce6050a7d6d172f3cc5c32aa97f52fa1a2e544'
LIVEBENCH_REVISIONS = {
    'math': 'bb66571c8ccf32d3df9e6f48b920d3770ff4aacb',
    'reasoning': '6fc6498a5dfba553f69f4413feabade1f1a2d384',
    'coding': 'a958549fdd8aa57be0a3fafe7b205ffc160ed5f4',
    'language': '3ada32a2e53d5e04e57fa503384cb85ce9116c40',
    'data_analysis': '31b9661ff678df9958e2f7fa228427f4c858c1a1',
    'instruction_following': '0868379c4b5cf62aeacaf8be4f08fced815c81bb',
}
TASK_HASHES = {
    'humaneval': 'ebffdeb39b4f02cbc1366a82afa38d7d11c2d25059e92164924908954f9ee350',
    'livebench': 'e0a33943164af7a33e8740eb5f5bd5999e067ed22ada887fd71f8a7bbb12046f',
}
PUBLIC_RELEASES = (date(2024, 7, 26), date(2024, 11, 25))
LIVEBENCH_CATEGORIES = {'math': 232, 'reasoning': 150, 'coding': 128, 'language': 140,
                        'data_analysis': 150, 'instruction_following': 200}


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def verify_livebench_revision():
    distribution = importlib.metadata.distribution('livebench')
    source = json.loads(distribution.read_text('direct_url.json') or '{}')
    if source.get('vcs_info', {}).get('commit_id') != LIVEBENCH_SCORER_REVISION:
        raise ValueError('LiveBench scorer revision differs from the pinned upstream source')


def verify_environment(benchmark, module):
    for package, version in PACKAGES.items():
        if importlib.metadata.version(package) != version:
            raise ValueError(f'Pinned package version changed: {package}')
    if file_hash(module.__file__) != TASK_HASHES[benchmark]:
        raise ValueError('Official task source differs from the qualified version')
    if benchmark == 'humaneval':
        if module.HUMANEVAL_DATASET_REVISION != HUMANEVAL_REVISION:
            raise ValueError('HumanEval dataset revision changed')
    else:
        if module.LIVEBENCH_DATASETS != LIVEBENCH_REVISIONS:
            raise ValueError('LiveBench dataset revisions changed')
        verify_livebench_revision()
    return qualified_sources(benchmark)


def validate_samples(benchmark, samples):
    expected = 164 if benchmark == 'humaneval' else 1000
    if len(samples) != expected or len({str(s.id) for s in samples}) != expected:
        raise ValueError(f'The complete {benchmark} dataset requires {expected} distinct tasks')
    if benchmark == 'livebench':
        categories = dict(Counter(s.metadata['category'] for s in samples))
        if categories != LIVEBENCH_CATEGORIES:
            raise ValueError('LiveBench requires all six qualified public categories')
        return categories
    return {}


def prepare(benchmark, output, release=None):
    if benchmark not in TASK_HASHES:
        raise ValueError('Unknown qualified benchmark')
    if benchmark == 'livebench' and release not in PUBLIC_RELEASES:
        raise ValueError('Select a qualified historical public LiveBench release')
    if benchmark == 'humaneval' and release is not None:
        raise ValueError('HumanEval is pinned by dataset revision, not release date')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f'Refusing to overwrite evidence: {output}')
    if benchmark == 'livebench' and platform.system() != 'Linux':
        raise RuntimeError('The unchanged LiveBench coding scorer requires Linux SIGALRM')
    module = importlib.import_module(f'inspect_evals.{benchmark}.{benchmark}')
    official_sources = verify_environment(benchmark, module)
    task = (module.humaneval(sandbox='local') if benchmark == 'humaneval'
            else module.livebench(livebench_release_date=release))
    samples = list(task.dataset)
    categories = validate_samples(benchmark, samples)
    rows = []
    for sample in samples:
        row = {'id': str(sample.id), 'prompt_sha256': digest(sample.input),
               'target_sha256': digest(sample.target),
               'metadata_sha256': digest(json.dumps(sample.metadata, sort_keys=True, default=str))}
        if benchmark == 'humaneval':
            row['tests_sha256'] = digest(sample.metadata['test'])
        else:
            row.update(category=sample.metadata['category'], task=sample.metadata['task'])
        rows.append(row)
    provenance = {'status': 'prepared_not_run', 'model_quality_measured': False,
                  'inspect_ai': PACKAGES['inspect-ai'], 'inspect_evals': PACKAGES['inspect-evals'],
                  'task_source_sha256': file_hash(module.__file__), 'official_source_files': official_sources,
                  'samples': len(samples), 'rows': rows}
    if benchmark == 'humaneval':
        provenance['dataset_revision'] = HUMANEVAL_REVISION
    else:
        provenance.update(release_date=release.isoformat(), historical_release=True,
                          livebench_revision=LIVEBENCH_SCORER_REVISION,
                          dataset_revisions=LIVEBENCH_REVISIONS, categories=categories,
                          scope='All 1000 tasks in this dated public release; current private and agentic tasks are excluded')
    output.mkdir(parents=True, exist_ok=True)
    provenance_path = output / f'{benchmark}-provenance.json'
    inputs_path = output / f'{benchmark}-inputs.json'
    write_manifest(provenance_path, provenance)
    inputs = {'schema': 1, 'benchmark': benchmark, 'dataset_manifest_sha256': file_hash(provenance_path),
              'rows': [{'id': str(s.id), 'prompt': s.input, 'prompt_sha256': digest(s.input)} for s in samples]}
    if benchmark == 'livebench':
        inputs.update(release_date=release.isoformat(), system_prompt=module.SYSTEM_PROMPT)
    write_manifest(inputs_path, inputs)
    return {'status': 'prepared_not_run', 'model_quality_measured': False, 'benchmark': benchmark,
            'samples': len(samples), 'inputs_sha256': file_hash(inputs_path),
            'provenance_sha256': file_hash(provenance_path), 'output': str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', choices=TASK_HASHES, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release-date', choices=[d.isoformat() for d in PUBLIC_RELEASES])
    args = parser.parse_args()
    release = date.fromisoformat(args.release_date or '2024-11-25') if args.benchmark == 'livebench' else None
    if args.benchmark == 'humaneval' and args.release_date:
        parser.error('--release-date applies only to LiveBench')
    print(json.dumps(prepare(args.benchmark, args.output, release)), flush=True)


if __name__ == '__main__':
    main()
