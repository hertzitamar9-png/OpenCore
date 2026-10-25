"""Freeze real quant packages into built-in cards without app-side discovery."""
import copy
import hashlib
import json
from pathlib import Path

from hf_catalog import build_plan, quantization_count, parse_repository
from hf_lineage import same_checkpoint, source_identity, checkpoint_name
from hf_download import validate_entry

PREFERRED = ('Q4_K_M', 'Q5_K_M', 'Q6_K', 'Q8_0', 'Q3_K_M', 'Q2_K', 'IQ4_XS', 'IQ3_M',
             'IQ2_XS', 'Q4_0', 'Q5_K_S', 'Q3_K_S', 'Q3_K_L', 'Q4_K_S', 'Q4_1')


def label(entry):
    value = str(entry.get('quantization') or '').upper()
    return {'FP16': 'F16', 'FP32': 'F32'}.get(value, value)


def append_presets(catalog, entries, manifest, snapshots):
    if not manifest.is_file():
        return entries
    research = json.loads(manifest.read_text(encoding='utf-8'))
    rows = research['models']
    snapshot_paths = {path.name.casefold(): path for path in sorted(snapshots.glob('*.api.json'))}
    def read_snapshot(repository):
        name = repository.replace('/', '--') + '.api.json'
        path = snapshot_paths.get(name.casefold())
        if path is None:
            raise ValueError('Missing frozen preset input: ' + name)
        return json.loads(path.read_text(encoding='utf-8'))
    native = {model['id']: model for model in catalog['models']}
    groups = {}
    known_checkpoints = {}
    for repository in research.get('identityRepositories', []):
        source = read_snapshot(repository)
        identity = source_identity(source)[0]
        known_checkpoints[repository.lower()] = identity
        known_checkpoints[source['id'].lower()] = identity
    for row in rows:
        if row.get('repositories') and row.get('availableQuants'):
            source = read_snapshot(row['repository'])
            known_checkpoints[source['id'].lower()] = source_identity(source)[0]
    def distinct_model(repository, canonical):
        other = known_checkpoints.get(repository.lower())
        same_publisher_name = other is not None and other.split('/')[0] == canonical.split('/')[0] and checkpoint_name(other) == checkpoint_name(canonical)
        return other is not None and other != canonical and not same_publisher_name
    for row in rows:
        if not row.get('repositories') or not row.get('availableQuants'):
            continue
        source = read_snapshot(row['repository'])
        canonical, identities = source_identity(source)
        group = groups.setdefault(canonical, dict(rows=[], identities=set(), sources=[], researched=set()))
        group['rows'].append(row)
        group['identities'].update(identities)
        group['sources'].extend(repository for repository in row.get('presetRepositories', row['repositories']) if not distinct_model(repository, canonical))
        group['researched'].update(repository.lower() for repository in row['repositories'] if not distinct_model(repository, canonical))
    known = {entry['id'] for entry in entries}
    for canonical, group in sorted(groups.items()):
        aliases = set(alias for row in group['rows'] for alias in row['nativeModelIds'])
        matching_roots = set()
        for model in catalog['models']:
            if model.get('variantOf'):
                continue
            try:
                repository = parse_repository(model.get('sourceUrl') or model.get('setupUrl') or '').lower()
            except ValueError:
                continue
            declared = known_checkpoints.get(repository)
            if not distinct_model(repository, canonical) and (repository in group['identities'] or repository in group['researched'] or declared == canonical):
                matching_roots.add(model.get('variantOf') or model['id'])
        aliases.update(model['id'] for model in catalog['models'] if (model.get('variantOf') or model['id']) in matching_roots)
        current = next((entry for entry in entries if aliases.intersection(entry.get('nativeModelIds', []))
                        or str(entry.get('originalRepository', '')).lower() == canonical), None)
        family = current['familyId'] if current else 'builtin-' + hashlib.sha256(canonical.encode()).hexdigest()[:24]
        family_entries = [entry for entry in entries if entry.get('familyId') == family]
        for entry in family_entries:
            aliases.update(entry.get('nativeModelIds', []))
        title = current['familyLabel'] if current else canonical.split('/')[-1]
        row = group['rows'][0]
        if not current:
            source = read_snapshot(row['repository'])
            card = source.get('cardData') or {}
            bases = card.get('base_model', []) if isinstance(card, dict) else []
            bases = [bases] if isinstance(bases, str) else bases if isinstance(bases, list) else []
            spelling = next((value for value in [source['id'], *bases] if isinstance(value, str) and value.lower() == canonical), canonical)
            title = spelling.split('/')[-1]
        # Keep the original product label when it is a single checkpoint card.
        if not current and len(group['rows']) == 1:
            title = row['label']
        original_model = next((native.get(row['id']) for row in group['rows'] if native.get(row['id'])), {})
        wanted = set(label(entry) for entry in family_entries if quantization_count([entry]))
        if len(wanted) >= 5:
            for entry in family_entries:
                entry['nativeModelIds'] = sorted(aliases)
                entry['builtIn'] = True
            continue
        candidates = []
        for repository in dict.fromkeys(group['sources']):
            source = read_snapshot(repository)
            if source['id'].lower() not in group['identities'] and not same_checkpoint(source, group['identities']):
                raise ValueError('Bundled package does not declare the exact checkpoint: ' + repository)
            for value in build_plan(source)['variants']:
                if value['format'] == 'gguf' and value['installable'] and value['runtime']['adapter'] == 'gguf' and quantization_count([value]):
                    candidates.append(value)
        candidates.sort(key=lambda entry: (PREFERRED.index(label(entry)) if label(entry) in PREFERRED else len(PREFERRED),
                                           entry['repository'].lower(), entry['bytes'], entry['id']))
        # Preset families already offering >=5 retain every existing option. Other
        # families get five named published choices without adding duplicate labels.
        for value in candidates:
            if len(wanted) >= 5:
                break
            if value['id'] in known or label(value) in wanted:
                continue
            entry = copy.deepcopy(value)
            entry.update(familyId=family, familyLabel=title, originalRepository=canonical,
                         nativeModelIds=sorted(aliases), builtIn=True, presetSource=True, contextTokens=8192,
                         managedEngine='flint-cpu-v1',
                         builtInDescription=original_model.get('description', ''),
                         libraryCategory=row['category'])
            validate_entry(entry)
            entries.append(entry)
            family_entries.append(entry)
            known.add(entry['id'])
            wanted.add(label(entry))
        for entry in family_entries:
            entry['nativeModelIds'] = sorted(aliases)
            entry['builtIn'] = True
    return entries


def coverage(catalog, entries):
    groups = {}
    for model in catalog['models']:
        groups.setdefault(model.get('variantOf') or model['id'], []).append(model)
    result = []
    for identity, models in groups.items():
        root = next((model for model in models if model['id'] == identity), models[0])
        ids = {model['id'] for model in models}
        replacements = [entry for entry in entries if ids.intersection(entry.get('nativeModelIds', []))]
        choices = replacements + [dict(quantization=model.get('precision'), installable=bool(model.get('artifacts')) and model.get('installable') is not False and model.get('runtimeReady') is not False) for model in models]
        count = quantization_count(choices)
        result.append(dict(id=identity, label=root['label'], category=root.get('category', 'text'),
                           quantizations=count, readyToInstallChoices=count >= 5,
                           source='bundled packages' if replacements else 'native catalog',
                           repositories=sorted({entry['repository'] for entry in replacements})))
    native_ids = {model['id'] for model in catalog['models']}
    represented = {entry['familyId'] for entry in entries if native_ids.intersection(entry.get('nativeModelIds', []))}
    extra = {}
    for entry in entries:
        if entry['familyId'] not in represented:
            extra.setdefault(entry['familyId'], []).append(entry)
    for identity, variants in sorted(extra.items()):
        count = quantization_count(variants)
        result.append(dict(id=identity, label=variants[0]['familyLabel'], category=variants[0].get('libraryCategory', 'text'),
                           quantizations=count, readyToInstallChoices=count >= 5, source='bundled packages',
                           repositories=sorted({entry['repository'] for entry in variants})))
    return dict(schema=1, requirement='At least five distinct named published packages per built-in checkpoint',
                allModelsMeetMinimum=all(row['readyToInstallChoices'] for row in result),
                families=len(result), withFive=sum(row['readyToInstallChoices'] for row in result), models=result)
