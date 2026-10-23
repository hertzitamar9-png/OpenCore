"""Build a reviewable catalog from anonymous HF metadata snapshots. No network or deployment.

The original recovered catalog is an immutable input. All output stays beside this script.
runtimeReady indicates an existing adapter pattern, never a successful local model test.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
SLOT_BYTES = 5256247
REQUESTED = [
    'RecursiveSI/flint', 'ornith-ai/Ornith-1.5-122B',
    'ukisai/Swift-1.5-Qwen3.8-27B-GSQ-RCO-GGUF',
    'KRAFTON/Raon-1-mini-preview-gguf', 'KRAFTON/Raon-1-mini-preview-nvfp4',
]
COMPANIONS = [
    'RecursiveSI/flint-int4-gguf', 'RecursiveSI/flint-int4', 'RecursiveSI/flint-nvfp4-gguf',
    'ornith-ai/Ornith-1.5-122B-GGUF', 'ornith-ai/Ornith-1.5-122B-FP8',
    'ornith-ai/Ornith-1.5-122B-NVFP4', 'ornith-ai/Ornith-1.5-122B-MLX',
    'ornith-ai/Ornith-1.5-122B-MLX-4bit', 'ornith-ai/Ornith-1.5-122B-MLX-8bit',
    'KRAFTON/Raon-1-mini-preview-mlx',
]


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def repository_from_url(url):
    parsed = urlparse(url or '')
    parts = parsed.path.strip('/').split('/')
    if parsed.hostname not in ('huggingface.co', 'hf.co') or len(parts) < 2:
        return None
    return '/'.join(parts[:2])


def deduplicate_generic_models(models, artifacts):
    """Collapse empty publisher placeholders only when the weight repository matches.

    Distinct checkpoints/finetunes from a different repository remain distinct even when
    their labels match. Existing concrete packages, execution modes and quants survive.
    """
    files = {a['id']: a for a in artifacts}
    curated = {}
    for model in models:
        if model.get('variantOf') or not model.get('artifacts'):
            continue
        selected = [files[a] for a in model.get('weightArtifacts', model['artifacts']) if a in files]
        weights = [f for f in selected if f.get('filename', '').lower().endswith(
            ('.gguf', '.safetensors', '.bin', '.pt', '.pth', '.ckpt', '.onnx', '.zst', '.fermion'))]
        repos = {a['repo'].lower() for a in weights or selected}
        # A projector or tokenizer from another publisher is not a duplicate model.
        primary = repository_from_url(model.get('sourceUrl'))
        if primary and primary.lower() in repos:
            repos = {primary.lower()}
        elif len(repos) > 1:
            totals = {repo: sum(a.get('bytes', 0) for a in weights if a['repo'].lower() == repo) for repo in repos}
            repos = {max(totals, key=totals.get)}
        for repo in repos:
            curated.setdefault(repo, model['id'])
    aliases = {}
    for model in models:
        if (model['id'].startswith('hf-') and not model.get('artifacts')
                and not model.get('variantOf') and model.get('backend') == 'external'
                and not model.get('runtimeReady')):
            repo = repository_from_url(model.get('sourceUrl'))
            target = curated.get((repo or '').lower())
            if target and target != model['id']:
                aliases[model['id']] = target
    result = [m for m in models if m['id'] not in aliases]
    for model in result:
        if model.get('variantOf') in aliases:
            model['variantOf'] = aliases[model['variantOf']]
    return result, aliases


def validate_catalog(catalog):
    artifacts = {a['id']: a for a in catalog['artifacts']}
    models = {m['id']: m for m in catalog['models']}
    if len(artifacts) != len(catalog['artifacts']) or len(models) != len(catalog['models']):
        raise ValueError('Duplicate catalog identities')
    for artifact in artifacts.values():
        if not re.fullmatch('[0-9a-fA-F]{40}', artifact['revision']):
            raise ValueError('Unpinned revision: ' + artifact['id'])
        if not re.fullmatch('[0-9a-fA-F]{64}', artifact['sha256']) or artifact['bytes'] <= 0:
            raise ValueError('Unpinned file identity: ' + artifact['id'])
        if len(artifact['repo'].split('/')) != 2:
            raise ValueError('Invalid repository: ' + artifact['id'])
        for key in ['filename', 'path']:
            name = artifact[key]
            if ('\\' in name or ':' in name or name.startswith('/') or
                    any(x in ('', '.', '..') for x in name.split('/'))):
                raise ValueError('Unsafe artifact path: ' + name)
    for model in models.values():
        selected = set(model['artifacts'])
        if not selected.issubset(artifacts):
            raise ValueError('Unknown artifact on ' + model['id'])
        if not set(model.get('weightArtifacts', [])).issubset(selected):
            raise ValueError('Weight artifact outside package: ' + model['id'])
        if model.get('variantOf'):
            seen = {model['id']}
            current = model
            while current.get('variantOf'):
                parent = current['variantOf']
                if parent not in models or parent in seen:
                    raise ValueError('Invalid variant tree: ' + model['id'])
                seen.add(parent)
                current = models[parent]
        if model.get('memoryMode', 'native') not in ('echo', 'native'):
            raise ValueError('Invalid memory mode: ' + model['id'])
        path = model.get('runtimeModelPath')
        if path and model.get('backend') != 'managed-text':
            prepared = model.get('preparedRuntime') or {}
            if path != prepared.get('path') and not any(artifacts[a]['path'] == path for a in selected):
                raise ValueError('Unpinned runtime path: ' + model['id'])


def build_catalog(include_official_companions=True):
    catalog = copy.deepcopy(read_json(ROOT / 'native-catalog.recovered.json'))
    original_ids = {m['id'] for m in catalog['models']}
    pins = read_json(ROOT / 'support-pins.json')
    info_by_repo = {repo: read_json(ROOT / (repo.replace('/', '--') + '.api.json'))
                    for repo in REQUESTED + (COMPANIONS if include_official_companions else [])}
    artifacts = {a['id']: a for a in catalog['artifacts']}
    existing_files = {(a['repo'], a['revision'], a['filename'], a['sha256']): a for a in artifacts.values()}
    updated = []
    coverage = {}
    excluded = []
    optional = []

    def file_artifact(repo, filename, folder):
        info = info_by_repo[repo]
        source = next(f for f in info['siblings'] if f['rfilename'] == filename)
        lfs = source.get('lfs') or {}
        pin = pins.get(repo, {}).get(filename) or {'sha256': lfs.get('sha256'), 'bytes': source.get('size')}
        if not pin.get('sha256') or not pin.get('bytes'):
            raise ValueError('Missing immutable pin: ' + repo + '/' + filename)
        identity = (repo, info['sha'], filename, pin['sha256'])
        if identity in existing_files:
            return existing_files[identity]['id']
        suffix = hashlib.sha256((repo + '@' + info['sha'] + '/' + filename).encode()).hexdigest()[:20]
        artifact = {'id': 'hf20261023-' + suffix, 'repo': repo, 'revision': info['sha'],
                    'filename': filename, 'bytes': pin['bytes'], 'sha256': pin['sha256'],
                    'path': folder + '/' + filename}
        catalog['artifacts'].append(artifact)
        artifacts[artifact['id']] = artifact
        existing_files[identity] = artifact
        return artifact['id']

    def add_model(model_id, family, label, repo, filenames, precision, mode='external', note='', parameter_count=0):
        info = info_by_repo[repo]
        folder = 'models/library/' + model_id
        weights = [file_artifact(repo, name, folder) for name in filenames]
        support = [file_artifact(repo, name, folder) for name in sorted(pins.get(repo, {}))]
        package = list(dict.fromkeys(weights + support))
        ready = mode in ('gguf', 'managed-text')
        context = 16384 if ready else 262144
        memory = 'echo' if ready else 'native'
        license_name = info.get('cardData', {}).get('license') or 'See publisher license'
        if isinstance(license_name, list):
            license_name = '; '.join(license_name)
        model = {'id': model_id, 'label': label, 'description': label + ' · text and reasoning',
                 'precision': precision, 'contextTokens': context, 'artifacts': package,
                 'weightArtifacts': weights, 'license': license_name, 'experimental': True,
                 'note': note, 'selectable': ready, 'category': 'text', 'backend': mode,
                 'memoryMode': memory, 'runtimeReady': ready, 'installable': not bool(info.get('gated')),
                 'inputModalities': ['text'], 'outputModalities': ['text'], 'brainRole': 'controller',
                 'parameterCount': parameter_count, 'vramWeightMultiplier': 1,
                 'sourceUrl': 'https://huggingface.co/' + repo + '/tree/' + info['sha'],
                 'setupUrl': 'https://huggingface.co/' + repo,
                 'runtimeModelPath': folder if mode == 'managed-text' else artifacts[weights[0]]['path']}
        if model_id != family:
            model['variantOf'] = family
        if ready:
            model['managedInstallation'] = True
            model['installation'] = {'adapterId': 'native-text-v1' if mode == 'gguf' else 'managed-transformers-text-v1',
                                     'sourceIdentity': repo + '@' + info['sha'], 'smokeTestId': 'text-generated-tokens'}
            if mode == 'managed-text':
                model['managedModelType'] = 'qwen3_5_moe'
        catalog['models'].append(model)
        coverage.setdefault(repo, set()).update(filenames)
        return model

    echo_note = ('ECHO can archive and retrieve conversation history within the allocated working window after a compatible runtime is available. '
                 'ECHO does not make all previous tokens part of native attention. Text/brain capability is declared; tool quality, local fit and speed are unverified. ')
    unsupported_note = ('Downloadable immutable publisher package. The installed OpenCore runtime has no verified adapter for this architecture; downloading does not enable execution. ' + echo_note)
    flint_repo = 'RecursiveSI/flint'
    flint_files = [f['rfilename'] for f in info_by_repo[flint_repo]['siblings'] if f['rfilename'].endswith('.safetensors')]
    add_model('flint', 'flint', 'Flint 40B A2.5B', flint_repo, flint_files, 'BF16',
              note=unsupported_note + 'The publisher says Transformers modeling code is not yet included and supplies a separate SGLang kit. 79.58 GB of stored weights.', parameter_count=39785287456)

    ornith_repo = 'ornith-ai/Ornith-1.5-122B'
    ornith_files = [f['rfilename'] for f in info_by_repo[ornith_repo]['siblings'] if f['rfilename'].endswith('.safetensors')]
    add_model('ornith-15-122b', 'ornith-15-122b', 'Ornith 1.5 122B', ornith_repo, ornith_files, 'BF16', 'managed-text',
              note=('Uses the existing managed Transformers text adapter for standard qwen3_5_moe with ECHO. All 62 shards and tokenizer/config files are pinned. '
                    'The 250.17 GB stored checkpoint must fit available host or GPU memory; this PC has not executed it. Local fit and speed are unverified. '
                    'Image input and vision tools are not exposed by this text adapter. Install must pass real token generation before Ready.'), parameter_count=125086497008)

    swift_repo = REQUESTED[2]
    swift_names = [f['rfilename'] for f in info_by_repo[swift_repo]['siblings'] if f['rfilename'].endswith('.gguf') and not f['rfilename'].startswith('imatrix')]
    coverage[swift_repo] = set(swift_names)
    for model in catalog['models']:
        if model['id'] == 'swift-27b' or model.get('variantOf') == 'swift-27b':
            model['label'] = 'Swift 1.5 27B' + ('' if model['id'] == 'swift-27b' else ' · ' + model['precision'] + (' · Native' if model.get('memoryMode') == 'native' else ''))
            model['brainRole'] = 'controller'
            model['note'] = ('Pinned publisher GGUF with the existing local text adapter. ' +
                             ('ECHO archives and retrieves history within the allocated working context. ' if model.get('memoryMode') == 'echo' else 'Uses native context with ECHO retrieval disabled. ') +
                             ('The MTP head is included in this file; speculative decoding acceleration is unverified. ' if 'MTP' in model['precision'] else '') +
                             'Local fit, quality and speed depend on hardware and are unverified for this selected file.')
            updated.append(model['id'])
    excluded.append({'repo': swift_repo, 'filename': 'imatrix-swift15-v1mix.gguf', 'role': 'calibration'})

    raon_repo = REQUESTED[3]
    raon_info = info_by_repo[raon_repo]
    raon_names = [f['rfilename'] for f in raon_info['siblings'] if f['rfilename'].endswith('.gguf') and not f['rfilename'].startswith('dflash')]
    raon_names.sort(key=lambda name: (not name.endswith('Q4_K_M.gguf'), name))
    for name in raon_names:
        precision = name.removeprefix('raon-1-mini-preview-').removesuffix('.gguf')
        model_id = 'raon-1-mini' if precision == 'Q4_K_M' else 'raon-1-mini-' + precision.lower().replace('_', '-')
        add_model(model_id, 'raon-1-mini', 'Raon 1 mini' + ('' if precision == 'Q4_K_M' else ' · ' + precision), raon_repo, [name], precision,
                  note=unsupported_note + 'Raon uses custom Raon Delta Attention and mHC layers; the installed GGUF engine has no Raon architecture. Publisher access rules must be rechecked before downloading.', parameter_count=30000000000)
    excluded.append({'repo': raon_repo, 'filename': 'dflash-raon-1-mini-preview-Q8_0.gguf', 'role': 'draft'})
    nv_repo = REQUESTED[4]
    nv_files = [f['rfilename'] for f in info_by_repo[nv_repo]['siblings'] if f['rfilename'].endswith('.safetensors')]
    add_model('raon-1-mini-nvfp4', 'raon-1-mini', 'Raon 1 mini · NVFP4', nv_repo, nv_files, 'NVFP4',
              note=unsupported_note + 'Requires the publisher-compatible vLLM/Raon implementation. Publisher NVFP4 recipe targets RTX 5090 with 32 GB VRAM. '
                   'The RTX 4070 has no native Blackwell FP4 hardware, and this 17.54 GB package exceeds its 12 GB VRAM before cache/buffers. '
                   'Other engines may emulate FP4; that is not a verified Raon/OpenCore adapter. Publisher access rules must be rechecked before downloading.', parameter_count=30000000000)

    if include_official_companions:
        for repo, suffix, precision in [
            ('RecursiveSI/flint-int4-gguf', 'q4-1', 'Q4_1'),
            ('RecursiveSI/flint-int4', 'mlx-int4', 'MLX INT4'),
            ('RecursiveSI/flint-nvfp4-gguf', 'nvfp4-gguf', 'NVFP4 GGUF')]:
            names = [f['rfilename'] for f in info_by_repo[repo]['siblings'] if f['rfilename'].endswith(('.gguf', '.safetensors'))]
            extra = ('The official NVFP4 GGUF is intended for a Flint-capable CUDA llama.cpp build; publisher tests use Blackwell/Spark hardware and recommend at least 48 GB GPU or unified memory.' if 'nvfp4' in suffix else
                     'MLX INT4 is an Apple Silicon runtime package.' if 'mlx' in suffix else 'Publisher primarily targets Apple Silicon Metal and recommends at least 48 GB unified memory.')
            add_model('flint-' + suffix, 'flint', 'Flint 40B A2.5B · ' + precision, repo, names, precision,
                      note=unsupported_note + extra, parameter_count=39785287456)
        repo = 'ornith-ai/Ornith-1.5-122B-GGUF'
        for file in info_by_repo[repo]['siblings']:
            name = file['rfilename']
            if not name.endswith('.gguf'):
                continue
            if name.startswith('mmproj'):
                artifact_id = file_artifact(repo, name, 'models/library/ornith-15-122b/optional')
                optional.append({'repo': repo, 'filename': name, 'role': 'vision-projector', 'artifactId': artifact_id,
                                 'note': 'Not selected by the text-only adapter; vision execution is unverified.'})
                continue
            precision = name.removeprefix('Ornith-1.5-122B-').removesuffix('.gguf')
            model_id = 'ornith-15-122b-gguf-' + precision.lower().replace('_', '-')
            model = add_model(model_id, 'ornith-15-122b', 'Ornith 1.5 122B · ' + precision, repo, [name], precision, 'gguf',
                              note=('Uses the existing qwen35moe GGUF text adapter and ECHO archive/retrieval. Model execution, memory fit and speed on this PC are unverified; '
                                    'even Q4_K_M is 75.84 GB of weights. Installation requires a real token-generation check. '
                                    'Text/brain integration is enabled; image input and the optional vision projector are not enabled.'), parameter_count=125086497008)
            native = copy.deepcopy(model)
            native['id'] += '-native'
            native['label'] += ' · Native'
            native['memoryMode'] = 'native'
            native['note'] = native['note'].replace('and ECHO archive/retrieval', 'with native context; ECHO retrieval is disabled')
            catalog['models'].append(native)
        for repo, suffix, precision in [
            ('ornith-ai/Ornith-1.5-122B-FP8', 'fp8', 'FP8'),
            ('ornith-ai/Ornith-1.5-122B-NVFP4', 'nvfp4', 'NVFP4'),
            ('ornith-ai/Ornith-1.5-122B-MLX', 'mlx-bf16', 'MLX BF16'),
            ('ornith-ai/Ornith-1.5-122B-MLX-4bit', 'mlx-4bit', 'MLX 4-bit'),
            ('ornith-ai/Ornith-1.5-122B-MLX-8bit', 'mlx-8bit', 'MLX 8-bit')]:
            names = [f['rfilename'] for f in info_by_repo[repo]['siblings'] if f['rfilename'].endswith('.safetensors')]
            add_model('ornith-15-122b-' + suffix, 'ornith-15-122b', 'Ornith 1.5 122B · ' + precision, repo, names, precision,
                      note=('Immutable official publisher package. This precision has no verified OpenCore Windows execution adapter. ' + echo_note +
                            ('MLX targets Apple Silicon. ' if 'mlx' in suffix else '') +
                            ('Native NVFP4 targets Blackwell; RTX 4070 emulation and this full package are unverified. ' if suffix == 'nvfp4' else '')),
                      parameter_count=125086497008)
        repo = 'KRAFTON/Raon-1-mini-preview-mlx'
        names = [f['rfilename'] for f in info_by_repo[repo]['siblings'] if f['rfilename'].endswith('.safetensors')]
        add_model('raon-1-mini-mlx', 'raon-1-mini', 'Raon 1 mini · MLX 4-bit', repo, names, 'MLX 4-bit',
                  note=unsupported_note + 'Official MLX weights target Apple Silicon; no Windows MLX/Raon adapter is declared.', parameter_count=30000000000)

    catalog['models'], aliases = deduplicate_generic_models(catalog['models'], catalog['artifacts'])
    validate_catalog(catalog)
    raw = json.dumps(catalog, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > SLOT_BYTES:
        raise ValueError('Expanded catalog exceeds the existing embedded slot')
    report = {'requestedRepositories': REQUESTED,
              'officialCompanionRepositories': COMPANIONS if include_official_companions else [],
              'companionEvidence': 'Official publisher repositories and matching model/base-model identity; main README files do not enumerate every companion.',
              'coverage': {repo: sorted(names) for repo, names in sorted(coverage.items())},
              'excludedAuxiliaries': excluded, 'optionalComponents': optional, 'aliases': aliases,
              'updatedModelIds': sorted(updated),
              'addedModelIds': sorted(m['id'] for m in catalog['models'] if m['id'] not in original_ids),
              'artifactCount': len(catalog['artifacts']), 'modelCount': len(catalog['models']),
              'compactBytes': len(raw), 'embeddedSlotBytes': SLOT_BYTES, 'remainingSlotBytes': SLOT_BYTES - len(raw),
              'catalogSha256': hashlib.sha256(raw).hexdigest(),
              'runtimeReadiness': 'Existing native-text/managed-transformers adapter patterns only; no requested weights downloaded or executed.',
              'accessObservation': {repo: {'private': info.get('private'), 'gated': info.get('gated'), 'revision': info['sha']}
                                    for repo, info in sorted(info_by_repo.items())}}
    return catalog, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--requested-only', action='store_true')
    args = parser.parse_args()
    catalog, report = build_catalog(include_official_companions=not args.requested_only)
    (ROOT / 'expanded-catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    (ROOT / 'catalog-build-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ['artifactCount', 'modelCount', 'compactBytes', 'remainingSlotBytes', 'catalogSha256']}))


if __name__ == '__main__':
    main()
