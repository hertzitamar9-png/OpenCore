"""Expose curated publisher families through the owned managed-inference bridge."""
import copy
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
EXTENSION = HERE.parent / 'src-tauri/resources/runtime-setup/hf-extension'
sys.path.insert(0, str(EXTENSION))
from hf_catalog import build_plan, stable_id
from hf_download import validate_entry


def build():
    catalog = json.loads((HERE / 'hf-research/expanded-catalog.json').read_text(encoding='utf-8'))
    artifacts = {a['id']: a for a in catalog['artifacts']}
    entries, seen = [], set()
    for family, label, model_type in [('flint', 'Flint 40B A2.5B', 'flint'), ('raon-1-mini', 'Raon 1 mini', 'raon1_mini')]:
        models = [m for m in catalog['models'] if m['id'] == family or m.get('variantOf') == family]
        native_ids = [m['id'] for m in models]
        for model in models:
            weights = model.get('weightArtifacts', model['artifacts'])
            pins = [artifacts[a] for a in model['artifacts']]
            primary = artifacts[weights[0]]
            repository, revision = primary['repo'], primary['revision']
            if any(p['repo'] != repository or p['revision'] != revision for p in pins):
                raise ValueError('Publisher family has mixed repository pins')
            fmt = 'gguf' if primary['filename'].endswith('.gguf') else 'safetensors'
            key = 'gguf:' + primary['filename'] if fmt == 'gguf' else 'safetensors:.'
            identifier = stable_id(repository, key)
            if identifier in seen:
                continue
            seen.add(identifier)
            entry = {
                'id': identifier, 'repository': repository, 'revision': revision, 'variantKey': key,
                'name': label + ' · ' + model['precision'], 'quantization': model['precision'],
                'format': fmt, 'subdirectory': '', 'modelType': model_type, 'architectures': [],
                'contextTokens': 8192, 'familyId': 'publisher-' + family, 'familyLabel': label,
                'nativeModelIds': native_ids, 'license': model['license'], 'sourceUrl': 'https://huggingface.co/' + repository,
                'files': [{'repository': p['repo'], 'revision': p['revision'], 'filename': p['filename'],
                           'bytes': p['bytes'], 'sha256': p['sha256'], 'role': 'model' if p['id'] in weights else 'support',
                           'pinned': True} for p in pins],
                'installable': model.get('installable') is not False, 'status': 'not-installed',
                'runtime': {'adapter': 'gguf' if fmt == 'gguf' else None, 'available': False, 'inferenceVerified': False,
                            'reason': 'Managed Windows CPU runtime; generation is checked after installation' if fmt == 'gguf' else
                                      'Choose a GGUF variant for the managed Windows runtime. This publisher format needs a different runtime.'},
            }
            entry['bytes'] = sum(p['bytes'] for p in entry['files'])
            if entry['installable']:
                validate_entry(entry)
            entries.append(entry)
    extra = HERE / 'hf-research/bartowski--RecursiveSI_flint-GGUF.api.json'
    if extra.is_file():
        for value in build_plan(json.loads(extra.read_text()))['variants']:
            if value['id'] in seen:
                continue
            entry = copy.deepcopy(value)
            entry.update(familyId='publisher-flint', familyLabel='Flint 40B A2.5B', modelType='flint',
                         contextTokens=8192, nativeModelIds=[m['id'] for m in catalog['models']
                            if m['id'] == 'flint' or m.get('variantOf') == 'flint'])
            entries.append(entry)
            seen.add(entry['id'])
    spark_communities = {
        'spark-x26-4b': 'bartowski/Spark-X2.6-4B-GGUF',
        'spark-x25-4b': 'mradermacher/Spark-X2.5-4B-GGUF',
        'spark-x25-1-7b': 'ngquocvinh/Spark-X2.5-1.7B-GGUF',
    }
    for repository, family, label in [
            ('hkust-aaron/EdgeRock-4B-GGUF', 'edgerock-4b', 'EdgeRock-4B'),
            ('bartowski/migtissera_Tess-4.5-27B-GGUF', 'tess-45-27b', 'Tess 4.5 27B'),
            ('XHToken/Spark-X2.6-4B-GGUF', 'spark-x26-4b', 'Spark X2.6 4B'),
            ('XHToken/Spark-X2.5-4B-GGUF', 'spark-x25-4b', 'Spark-X2.5-4B'),
            ('XHToken/Spark-X2.5-1.7B-GGUF', 'spark-x25-1-7b', 'Spark-X2.5-1.7B')]:
        native_root = 'hf-xhtoken-spark-x2-5-4b' if family == 'spark-x25-4b' else family
        native_ids = [model['id'] for model in catalog['models'] if model['id'] == native_root or model.get('variantOf') == native_root]
        original = {'edgerock-4b': 'rockmatrix-ai/EdgeRock', 'tess-45-27b': 'migtissera/Tess-4.5-27B'}.get(family, repository.removesuffix('-GGUF'))
        quantizations = set()
        for source_repository in [repository] + ([spark_communities[family]] if family in spark_communities else []):
            metadata = json.loads((HERE / 'hf-research' / (source_repository.replace('/', '--') + '.api.json')).read_text(encoding='utf-8'))
            if source_repository != repository:
                from hf_discovery import same_checkpoint
                if not same_checkpoint(metadata, {original.lower()}):
                    raise ValueError('Community checkpoint lineage differs: ' + source_repository)
            for value in build_plan(metadata)['variants']:
                if value['format'] != 'gguf' or value['id'] in seen or value['quantization'] in quantizations:
                    continue
                entry = copy.deepcopy(value)
                entry.update(familyId='publisher-' + family, familyLabel=label, contextTokens=8192,
                             nativeModelIds=native_ids, originalRepository=original)
                if family in spark_communities:
                    entry['modelType'] = 'spark2_5'
                entries.append(entry)
                seen.add(entry['id'])
                quantizations.add(entry['quantization'])
    from bundled_catalog import append_presets
    entries = append_presets(catalog, entries, HERE / 'hf-research/bundled-quantizations.json', HERE / 'hf-research/bundled-quants')
    for entry in entries:
        entry['builtIn'] = True
    entries.sort(key=lambda e: (e['familyId'], e['format'] != 'gguf', e['bytes'], e['id']))
    return {'schema': 1, 'entries': entries}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    result = build()
    from bundled_catalog import coverage
    catalog = json.loads((HERE / 'hf-research/expanded-catalog.json').read_text(encoding='utf-8'))
    outputs = {EXTENSION / 'publisher-models.json': json.dumps(result, ensure_ascii=False, indent=2) + '\n',
               HERE / 'builtin-quant-coverage.json': json.dumps(coverage(catalog, result['entries']), indent=2) + '\n'}
    for target, source in outputs.items():
        if args.check:
            if not target.is_file() or target.read_text(encoding='utf-8') != source:
                raise ValueError('Regenerate the bundled quant catalog: ' + str(target))
        else:
            target.write_text(source, encoding='utf-8')
    print(json.dumps({'families': len({e['familyId'] for e in result['entries']}), 'variants': len(result['entries']),
                      'windowsGgufVariants': sum(e['format'] == 'gguf' for e in result['entries'])}))
