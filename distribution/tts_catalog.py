"""Pinned managed text-to-speech packages: Kitten TTS 2, VoxCPM2 and Breeze-TTS-2.

Each package pins exactly the files its managed runtime loads. Revisions and
runtime identities come from hf_tts.FAMILIES so the catalog and the runtime can
never disagree about which checkpoint was reviewed.
"""
import fnmatch
import json

from hf_catalog import _pin, stable_id
from hf_download import validate_entry
from hf_tts import COMPONENTS, FAMILIES

CODE = ('.py', '.pyc', '.sh', '.bat', '.cmd', '.ps1', '.exe', '.dll', '.so', '.dylib', '.swift', '.js')

PACKAGES = (
    {'runtime': 'kitten-tts-2-v1', 'familyId': 'publisher-kitten-tts-2', 'quantization': 'Ternary 1.58-bit',
     'include': ('config.json', 'LICENSE.md', 'lm/*', 'speaker/*', 'voices/*'),
     'exclude': ('lm/model.safetensors', 'lm/model-tl2-emb4.safetensors'),
     'license': 'Stellon Labs Community License',
     'description': ('Expressive speech with 47 built-in voices, emotion tags such as [excited] or [sad], '
                     'vocal events such as <laugh> or <sigh>, (((emphasis))) and voice cloning from a short recording '
                     'with its transcript. Runs on any computer, slowly on the CPU and much faster on an NVIDIA GPU; '
                     'also downloads the pinned 1.06 GB Chatterbox Turbo vocoder.')},
    {'runtime': 'voxcpm2-v1', 'familyId': 'publisher-voxcpm2', 'quantization': 'BF16',
     'include': ('*',), 'exclude': ('.gitattributes', 'README.md'),
     'license': 'apache-2.0',
     'description': ('Tokenizer-free 48 kHz speech in 30 languages. Design a voice from a description, steer the style, '
                     'or clone a voice from a reference recording; adding its transcript gives the closest match. '
                     'Best on an NVIDIA RTX 30-series or newer GPU with 8 GB or more; other computers run it '
                     'much more slowly on the CPU.')},
    {'runtime': 'breeze-tts-2-v1', 'familyId': 'publisher-breeze-tts-2', 'quantization': 'BF16',
     'include': ('*',), 'exclude': ('.gitattributes', 'README.md', 'assets/*'),
     'license': 'BreezeBlue Research and Non-Commercial License',
     'description': ('Taiwanese Mandarin and English speech with voice direction (for example "speak cheerfully"), '
                     'vocal events such as (laugh) or [笑] and voice cloning from a recording with its transcript. '
                     'Non-commercial license. Needs an NVIDIA RTX 30-series or newer GPU with 12 GB or more of memory; '
                     'it cannot run on the CPU.')},
)


def _read(snapshots, repository):
    value = json.loads((snapshots / (repository.replace('/', '--') + '.api.json')).read_text(encoding='utf-8'))
    if value['id'].lower() != repository.lower():
        raise ValueError('TTS snapshot identity differs: ' + repository)
    return value


def append_tts(entries, snapshots):
    vocoder = COMPONENTS['s3genMeanflow']
    metadata = _read(snapshots, vocoder['repository'])
    item = next((item for item in metadata['siblings'] if item['rfilename'] == vocoder['filename']), None)
    if (metadata['sha'] != vocoder['revision'] or not item or item.get('size') != vocoder['bytes']
            or (item.get('lfs') or {}).get('sha256') != vocoder['sha256']):
        raise ValueError('The Chatterbox Turbo vocoder pin differs from its reviewed snapshot')
    result = [entry for entry in entries if not entry.get('ttsRuntime')]
    existing = {entry['id'] for entry in result}
    for package in PACKAGES:
        family = FAMILIES[package['runtime']]
        repository, revision = family['repository'], family['revision']
        metadata = _read(snapshots, repository)
        if metadata['sha'] != revision or metadata.get('pipeline_tag') != 'text-to-speech':
            raise ValueError('The TTS snapshot is not the reviewed checkpoint: ' + repository)
        pins = []
        for item in metadata['siblings']:
            name = item['rfilename']
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in package['include']):
                continue
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in package['exclude']):
                continue
            if name.lower().endswith(CODE):
                # The managed runtimes ship their own model code; never download repository code.
                continue
            role = 'model' if name.endswith(('.safetensors', '.pth', '.pt')) else 'support'
            pins.append(_pin(repository, revision, item, role))
        if not pins or any(not pin['pinned'] for pin in pins) or not any(pin['role'] == 'model' for pin in pins):
            raise ValueError('Incomplete pinned TTS package: ' + repository)
        key = 'tts:' + package['runtime']
        identity = stable_id(repository, key)
        if identity in existing:
            raise ValueError('The TTS package already belongs to another card: ' + repository)
        extra = sum(COMPONENTS[name]['bytes'] for name in family['components'])
        entry = {
            'id': identity, 'repository': repository, 'revision': revision, 'variantKey': key,
            'name': family['label'] + ' · ' + package['quantization'], 'quantization': package['quantization'],
            'format': 'safetensors', 'subdirectory': '', 'category': 'tts', 'libraryCategory': 'tts',
            'inputModalities': ['text', 'audio'], 'outputModalities': ['audio'],
            'files': pins, 'bytes': sum(pin['bytes'] for pin in pins), 'installable': True, 'reason': None,
            'runtime': {'adapter': 'managed-tts', 'available': False, 'inferenceVerified': False,
                        'reason': ('The isolated speech runtime and pinned components install automatically. '
                                   'A real synthesized clip must pass a signal check before Ready.')},
            'private': False, 'gated': bool(metadata.get('gated')), 'sourceUrl': 'https://huggingface.co/' + repository,
            'status': 'not-installed', 'familyId': package['familyId'], 'familyLabel': family['label'],
            'originalRepository': repository, 'builtIn': True, 'nativeModelIds': [],
            'builtInDescription': package['description'], 'contextTokens': 8192, 'license': package['license'],
            'catalogOrder': 0, 'ttsRuntime': package['runtime'], 'ttsComponentBytes': extra,
            'outputsText': False, 'supportsTextGeneration': False, 'operatingModes': [],
        }
        validate_entry(entry)
        result.append(entry)
        existing.add(identity)
    return result
