"""Append individually reviewed Hub models to the offline bundled package catalog."""
import copy
import json
from pathlib import PurePosixPath

from hf_catalog import build_plan, _pin
from hf_download import validate_entry
from hf_lineage import same_checkpoint


def append_requested(entries, manifest, snapshots):
    research = json.loads(manifest.read_text(encoding='utf-8'))
    existing = {entry['id'] for entry in entries}

    def read(repository):
        value = json.loads((snapshots / (repository.replace('/', '--') + '.api.json')).read_text(encoding='utf-8'))
        if value['id'].lower() != repository.lower():
            raise ValueError('Requested-model snapshot identity differs: ' + repository)
        return value

    for row in research['models']:
        original = read(row['repository'])
        known = set()
        for source in row['sources']:
            repository = source['repository']
            metadata = read(repository)
            if repository.lower() != row['repository'].lower() and not same_checkpoint(metadata, {row['repository'].lower()}):
                evidence = source.get('publisherEvidence')
                readme = snapshots / (row['repository'].replace('/', '--') + '.README.md')
                if not (evidence == original['sha'] and repository.split('/')[0] == row['repository'].split('/')[0]
                        and repository.startswith(row['repository'] + '-') and readme.is_file()
                        and 'https://huggingface.co/' + repository in readme.read_text(encoding='utf-8')):
                    raise ValueError('Requested package does not declare the exact checkpoint: ' + repository)
            metadata = copy.deepcopy(metadata)
            config = metadata.get('config') or {}
            text_config = config.get('text_config') or {}
            if not config.get('torch_dtype') and not config.get('dtype'):
                config['dtype'] = text_config.get('dtype', text_config.get('torch_dtype', ''))
            metadata['config'] = config
            plan = build_plan(metadata)
            # Split GGUF checkpoints often share one projector at the repository
            # root. Attach that pinned companion instead of omitting vision files.
            projectors = [pin for variant in plan['variants'] for pin in variant['files'] if pin['role'] == 'companion']
            if not projectors:
                candidates = [item for item in metadata['siblings'] if 'mmproj' in PurePosixPath(item['rfilename']).name.lower()
                              and item['rfilename'].lower().endswith('.gguf') and '/' not in item['rfilename']]
                candidates.sort(key=lambda item: ('f16' not in item['rfilename'].lower(), item['rfilename']))
                if candidates:
                    projectors = [_pin(repository, metadata['sha'], candidates[0], 'companion')]
            for value in plan['variants']:
                entry = copy.deepcopy(value)
                quant = source.get('quantization', entry['quantization'])
                identity = (entry['format'], quant.upper().replace('FP16', 'F16').replace('FP32', 'F32'))
                if identity in known:
                    continue
                if entry['id'] in existing:
                    raise ValueError('Requested package already belongs to a bundled card: ' + entry['id'])
                if entry['format'] == 'gguf' and not any(pin['role'] == 'companion' for pin in entry['files']) and projectors:
                    entry['files'].append(copy.deepcopy(projectors[0]))
                if entry['format'] != 'gguf':
                    selected = {pin['filename'] for pin in entry['files']}
                    for item in metadata['siblings']:
                        path = PurePosixPath(item['rfilename'])
                        if (path.name in ('quantization_config.json', 'video_preprocessor_config.json')
                                and str(path.parent) in ('.', entry['subdirectory']) and str(path) not in selected):
                            entry['files'].append(_pin(repository, metadata['sha'], item, 'support'))
                entry['bytes'] = sum(pin['bytes'] for pin in entry['files'])
                entry.update(familyId=row['familyId'], familyLabel=row['label'], originalRepository=row['repository'],
                             builtIn=True, nativeModelIds=[], libraryCategory=row['category'],
                             builtInDescription=row['description'], contextTokens=8192, quantization=quant,
                             license=(original.get('cardData') or {}).get('license', 'Publisher terms'))
                entry['name'] = row['label'] + ' · ' + quant
                if source.get('runtimeReason'):
                    entry['runtime'].update(adapter=None, reason=source['runtimeReason'])
                elif entry['format'] == 'gguf':
                    entry['managedEngine'] = 'flint-cpu-v1'
                    entry['runtime']['reason'] = 'The pinned Windows runtime prepares automatically; actual generation is checked after installation. Vision and action-harness execution are unverified.'
                else:
                    entry['runtime']['reason'] = 'The isolated Transformers text runtime prepares automatically; generation must pass before Ready. Vision and action-harness execution are unverified.'
                if row.get('baseRepository'):
                    entry['baseRepository'] = row['baseRepository']
                    entry['packageKind'] = 'adapter'
                entry['catalogOrder'] = 0 if entry['runtime'].get('adapter') else 1
                validate_entry(entry)
                entries.append(entry)
                existing.add(entry['id'])
                known.add(identity)
    return entries
