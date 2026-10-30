"""Append individually reviewed Hub models to the offline bundled package catalog."""
import copy
import fnmatch
import json
from pathlib import PurePosixPath

from hf_catalog import build_plan, _pin, stable_id, echo_context_tokens
from hf_download import validate_entry
from hf_lineage import same_checkpoint, bases, quantized_relation, checkpoint_name


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
        family = row['familyId']
        prior = [entry for entry in entries if entry.get('familyId') == family]
        if prior and not row.get('reuseFamily'):
            raise ValueError('Requested family already exists without an explicit reuse contract')
        known = {(entry['format'], entry['quantization'].upper().replace('FP16', 'F16').replace('FP32', 'F32')) for entry in prior}
        for source in row['sources']:
            repository = source['repository']
            metadata = read(repository)
            if repository.lower() != row['repository'].lower() and not same_checkpoint(metadata, {row['repository'].lower()}):
                evidence = source.get('publisherEvidence')
                readme = snapshots / (row['repository'].replace('/', '--') + '.README.md')
                alias = source.get('derivationRoot')
                alias_ok = (alias and alias == source.get('publisherLinkedRoot') and source.get('publisherEvidence') == original['sha']
                            and readme.is_file() and 'https://huggingface.co/' + alias in readme.read_text(encoding='utf-8')
                            and (same_checkpoint(metadata, {alias.lower()}) or
                                 quantized_relation(metadata) and bases(metadata) == {alias.lower()}
                                 and checkpoint_name(repository) == checkpoint_name(alias.removesuffix('-BF16'))))
                if not alias_ok and not (evidence == original['sha'] and repository.split('/')[0] == row['repository'].split('/')[0]
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
            if source.get('packages'):
                variants = []
                for package in source['packages']:
                    pins = [_pin(repository, metadata['sha'], item,
                                 'model' if item['rfilename'].endswith(('.safetensors', '.gguf')) else 'support')
                            for item in metadata['siblings'] if any(fnmatch.fnmatchcase(item['rfilename'], pattern) for pattern in package['files'])]
                    if not pins or any(not pin['pinned'] for pin in pins):
                        raise ValueError('Incomplete curated package: ' + repository)
                    key = package['key']
                    variants.append({'id': stable_id(repository, key), 'variantKey': key, 'repository': repository,
                        'revision': metadata['sha'], 'format': package['format'], 'quantization': package['quantization'],
                        'subdirectory': '', 'category': row['category'], 'files': pins, 'bytes': sum(pin['bytes'] for pin in pins),
                        'installable': True, 'status': 'not-installed', 'sourceUrl': 'https://huggingface.co/' + repository,
                        'gated': bool(metadata.get('gated')), 'runtime': {'adapter': None, 'available': False, 'inferenceVerified': False}})
                plan['variants'] = variants
            if source.get('onlyVariantKeys'):
                plan['variants'] = [entry for entry in plan['variants'] if entry['variantKey'] in source['onlyVariantKeys']]
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
                quant = source.get('quantization', source.get('quantizationLabels', {}).get(entry['quantization'], entry['quantization']))
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
                             builtInDescription=row['description'], contextTokens=echo_context_tokens(original) if entry['category']=='text' else 8192, quantization=quant,
                             license=(original.get('cardData') or {}).get('license', 'Publisher terms'))
                entry['name'] = row['label'] + ' · ' + quant
                if entry['category']=='text':
                    entry['memoryMode']='echo'
                if source.get('runtimeReason'):
                    entry['runtime'].update(adapter=None, reason=source['runtimeReason'])
                elif entry['format'] == 'gguf' and entry['category'] == 'text':
                    engine = source.get('managedEngine', 'flint-cpu-v1')
                    if engine not in ('flint-cpu-v1', 'lfm3-cpu-v1'):
                        raise ValueError('Unreviewed requested-model runtime: ' + engine)
                    entry['managedEngine'] = engine
                    entry['runtime']['reason'] = 'The pinned Windows runtime prepares automatically; actual generation is checked after installation. Vision and action-harness execution are unverified.'
                elif entry['runtime'].get('adapter') == 'transformers':
                    entry['runtime']['reason'] = 'The isolated Transformers text runtime prepares automatically; generation must pass before Ready. Vision and action-harness execution are unverified.'
                elif not entry['runtime'].get('reason'):
                    entry['runtime']['reason'] = 'This complete pinned package requires an integrated runtime for its architecture. A download is not a generation test.'
                if row.get('baseRepository'):
                    entry['baseRepository'] = row['baseRepository']
                    entry['packageKind'] = 'adapter'
                if row.get('baseRepositories'):
                    entry['baseRepositories'] = list(row['baseRepositories'])
                entry['catalogOrder'] = 0 if entry['runtime'].get('adapter') else 1
                if source.get('imageRuntime')=='iris-sdcpp-v1':
                    from hf_sdcpp import REPOSITORY,REVISION
                    if repository!=REPOSITORY or metadata['sha']!=REVISION:raise ValueError('Sakura runtime package identity differs')
                    entry.update(category='image',imageRuntime='iris-sdcpp-v1',imageDefaults={'width':1024,'height':1024,'steps':30,'guidance':3},catalogOrder=0 if quant=='Q8_0' else 1)
                    entry['runtime'].update(adapter='sdcpp-iris',reason='The pinned Vulkan image engine and text encoder prepare automatically. Actual image generation is required before Ready.')
                validate_entry(entry)
                entries.append(entry)
                existing.add(entry['id'])
                known.add(identity)
    return entries
