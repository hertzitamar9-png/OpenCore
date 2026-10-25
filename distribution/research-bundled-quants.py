"""Maintainer-only metadata research for preset quantizations; never run by the app."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import json
from pathlib import Path
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src-tauri/resources/runtime-setup/hf-extension'))
from hf_catalog import build_plan, parse_repository, quantization_count
from hf_discovery import discover
from hf_download import HubClient, atomic_json


class SnapshotClient:
    def __init__(self, directory):
        self.directory = directory
        self.lock = threading.Lock()
        self.next_request = 0

    def request(self, method, value):
        for attempt in range(4):
            with self.lock:
                delay = max(0, self.next_request - time.monotonic())
                self.next_request = max(time.monotonic(), self.next_request) + .55
            if delay:
                time.sleep(delay)
            try:
                return getattr(HubClient(timeout=6), method)(value)
            except Exception as error:
                if getattr(error, 'status', None) != 429 or attempt == 3:
                    raise
                time.sleep(30)

    def metadata(self, repository):
        repository = parse_repository(repository)
        path = self.directory / (repository.replace('/', '--') + '.api.json')
        if path.is_file():
            return json.loads(path.read_text(encoding='utf-8'))
        alternatives = [HERE / 'hf-research' / path.name,
                        HERE / '.output/quant-coverage-research' / (repository.replace('/', '--') + '.json')]
        for alternative in alternatives:
            if alternative.is_file():
                value = json.loads(alternative.read_text(encoding='utf-8'))
                if isinstance(value, dict) and value.get('id', '').lower() == repository.lower() and 'siblings' in value:
                    atomic_json(path, value)
                    return value
        value = self.request('metadata', repository)
        with self.lock:
            atomic_json(path, value)
        return value

    def search_quantizations(self, query):
        return self.request('search_quantizations', query)

    def quantization_derivatives(self, repository):
        return self.request('quantization_derivatives', repository)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--retry-incomplete', action='store_true')
    args = parser.parse_args()
    catalog = json.loads((HERE / 'hf-research/expanded-catalog.json').read_text(encoding='utf-8'))
    groups = defaultdict(list)
    for model in catalog['models']:
        groups[model.get('variantOf') or model['id']].append(model)
    targets, report = [], []
    for identity, models in groups.items():
        root = next((model for model in models if model['id'] == identity), models[0])
        native = [dict(quantization=model.get('precision'), installable=bool(model.get('artifacts')) and model.get('installable') is not False)
                  for model in models]
        row = dict(id=identity, label=root['label'], category=root.get('category', 'text'),
                   nativeModelIds=[model['id'] for model in models], nativeQuants=quantization_count(native))
        try:
            row['repository'] = parse_repository(root.get('sourceUrl') or root.get('setupUrl') or '')
        except ValueError:
            row['status'] = 'Native execution profile or non-Hub source; requires its own package integration'
            report.append(row)
            continue
        if row['nativeQuants'] >= 5:
            row['status'] = 'Five or more pinned native package choices already present'
            report.append(row)
        elif row['category'] not in ('text', 'omni', 'computer-use', 'ocr'):
            row['status'] = 'Specialized runtime and complete component-package integration required'
            report.append(row)
        else:
            targets.append(row)
    if args.retry_incomplete:
        previous = json.loads((HERE / 'hf-research/bundled-quantizations.json').read_text(encoding='utf-8'))['models']
        retry = {row['id'] for row in previous if row.get('error') or row.get('searchWarnings')}
        targets = [row for row in targets if row['id'] in retry]
        target_ids = {row['id'] for row in targets}
        report = [row for row in previous if row['id'] not in target_ids]
    if args.limit:
        targets = targets[:args.limit]
    snapshots = HERE / 'hf-research/bundled-quants'
    snapshots.mkdir(parents=True, exist_ok=True)
    client = SnapshotClient(snapshots)

    def inspect(row):
        try:
            source = client.metadata(row['repository'])
            primary = build_plan(source)
            if quantization_count([v for v in primary['variants'] if v['format'] == 'gguf']) >= 5:
                plan = dict(primary, originalRepository=source['id'], relatedRepositories=[])
            elif primary.get('pipelineTag') in (None, '', 'text-generation', 'text2text-generation', 'image-text-to-text', 'conversational'):
                plan = discover(client, row['repository'])
            else:
                return dict(row, status='Specialized model; compatible complete packages require integration')
            variants = [v for v in plan['variants'] if v['format'] == 'gguf' and v['installable'] and v['runtime']['adapter'] == 'gguf']
            found = quantization_count(variants)
            repositories = list(dict.fromkeys(v['repository'] for v in variants))
            return dict(row, originalRepository=plan.get('originalRepository', row['repository']),
                        repositories=repositories, availableQuants=found,
                        status='Preset packages found' if found >= 5 else 'Fewer than five identified published quantizations found',
                        searchWarnings=plan.get('discoveryErrors', []))
        except Exception as error:
            return dict(row, status='Metadata research incomplete', error=str(error)[:400])

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as pool:
        jobs = [pool.submit(inspect, row) for row in targets]
        for number, job in enumerate(as_completed(jobs), 1):
            row = job.result()
            report.append(row)
            print(json.dumps(dict(checked=number, total=len(targets), id=row['id'], quants=row.get('availableQuants'), status=row['status'])), flush=True)
    result = dict(schema=1, scope='Built-in catalog. Maintainer metadata research only; no model weights downloaded. Missing packages are not synthesized or replaced by different fine-tunes.',
                  models=sorted(report, key=lambda row: row['id']))
    atomic_json(HERE / 'hf-research/bundled-quantizations.json', result)
    print(json.dumps(dict(complete=True, researched=len(targets), withFive=sum(row.get('availableQuants', 0) >= 5 for row in report))), flush=True)


if __name__ == '__main__':
    main()
