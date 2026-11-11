"""Pinned managed text-to-speech packages: Kitten TTS 2, VoxCPM2, Breeze-TTS-2, ZONOS3-Mini, dots.tts.edit and Qwen3-TTS.

Each package pins exactly the files its managed runtime loads. Revisions and
runtime identities come from hf_tts.FAMILIES so the catalog and the runtime can
never disagree about which checkpoint was reviewed.

The Qwen3-TTS packages take over the built-in Qwen3 TTS cards (nativeModelIds) with
the same "Original published checkpoints" precision, so installing one of those
cards installs the managed runtime instead of a native engine.
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
    # Only the files the torch reference path loads: the LM, the codec generator with its pruned KEEP-2 iSTFT head,
    # the speaker encoder used for cloning and the preset voice embeddings. The GGUF, web and teacher files are not used.
    {'runtime': 'zonos3-mini-v1', 'familyId': 'publisher-zonos3-mini', 'quantization': 'BF16', 'format': 'pytorch',
     'include': ('lm/zonos3_mini/model.pth', 'lm/zonos3_mini/params.json', 'lm/zonos3_mini/tokenizer/superbpe8192.json',
                 'autoencoder/generator.pt', 'autoencoder/heads/keep2_plain/*',
                 'speaker_encoders/ownmect_s2/ckpt_0037.pth', 'web/voices/voices.json', 'web/voices/*.embedding.npy'),
     'exclude': (),
     'license': 'apache-2.0',
     'description': ('Multilingual speech with 27 preset voices, moods and speaking styles such as a newsreader or a '
                     'late-night radio host, and voice cloning from a short recording alone. Speaks English, French, '
                     'German, Spanish, Italian, Portuguese, Japanese, Mandarin and Korean. Best on an NVIDIA RTX '
                     '30-series or newer GPU with 6 GB or more; other computers run it much more slowly on the CPU.')},
    {'runtime': 'dots-tts-edit-v1', 'familyId': 'publisher-dots-tts-edit', 'quantization': 'BF16',
     'include': ('*',), 'exclude': ('.gitattributes', 'README.md'),
     'license': 'other (the dots.tts project releases its checkpoints under Apache-2.0)',
     'description': ('English and Mandarin speech that can also edit a recording: replace, insert or delete words, '
                     'change the emotion, pitch, speed or pauses of a part of it with edit tags. Without tags it '
                     'speaks the text, cloning the voice of a recording when you add one. Best on an NVIDIA RTX '
                     '30-series or newer GPU with 8 GB or more; other computers run it much more slowly on the CPU.')},
)

QWEN3_TTS_LANGUAGES = 'English, Chinese, Japanese, Korean, German, French, Russian, Portuguese, Spanish and Italian'
QWEN3_TTS_SPEED = {
    '0.6B': ('Runs on an NVIDIA GPU with 4 GB or more of memory; other computers run it much more slowly on the CPU.'),
    '1.7B': ('Best on an NVIDIA GPU with 6 GB or more of memory; other computers run it much more slowly on the CPU.'),
}
QWEN3_TTS_KINDS = {
    'base': ('Base', 'voice-cloning-qwen3-base-{}',
             'Clones a voice from a short reference recording in ' + QWEN3_TTS_LANGUAGES + '. Adding the transcript of '
             'the recording gives the closest match; without it only the voice timbre is copied.'),
    'customvoice': ('CustomVoice', 'tts-qwen3-customvoice-{}',
                    'Speaks with 9 built-in voices in ' + QWEN3_TTS_LANGUAGES + '. Each voice is native to one language '
                    'and can read the others.'),
    'voicedesign': ('VoiceDesign', 'tts-qwen3-voicedesign-{}',
                    'Designs a new voice from a written description, such as "a calm middle-aged man with a deep, warm '
                    'voice", and speaks ' + QWEN3_TTS_LANGUAGES + '.'),
}


def _qwen3_package(size, kind):
    label, native, description = QWEN3_TTS_KINDS[kind]
    if kind == 'customvoice' and size == '1.7B':
        description += ' A speaking-style instruction such as "very happy, speaking quickly" changes how it sounds.'
    slug = size.lower().replace('.', '-')
    return {'runtime': 'qwen3-tts-{}-{}-v1'.format(size.lower(), kind), 'familyId': 'publisher-qwen3-tts-{}-{}'.format(slug, kind),
            'quantization': 'Original published checkpoints', 'include': ('*',), 'exclude': ('.gitattributes', 'README.md'),
            'license': 'apache-2.0', 'nativeModelIds': [native.format(slug)],
            # The reviewed 1.7B-Base revision carries no pipeline tag on the Hub; its files match the other Base checkpoint.
            'pipelineTags': ('text-to-speech', None) if (size, kind) == ('1.7B', 'base') else ('text-to-speech',),
            'description': description + ' ' + QWEN3_TTS_SPEED[size]}


PACKAGES += tuple(_qwen3_package(size, kind) for size, kind in
                  (('0.6B', 'base'), ('1.7B', 'base'), ('0.6B', 'customvoice'), ('1.7B', 'customvoice'), ('1.7B', 'voicedesign')))


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
        if metadata['sha'] != revision or metadata.get('pipeline_tag') not in package.get('pipelineTags', ('text-to-speech',)):
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
            'format': package.get('format', 'safetensors'), 'subdirectory': '', 'category': 'tts', 'libraryCategory': 'tts',
            'inputModalities': ['text', 'audio'], 'outputModalities': ['audio'],
            'files': pins, 'bytes': sum(pin['bytes'] for pin in pins), 'installable': True, 'reason': None,
            'runtime': {'adapter': 'managed-tts', 'available': False, 'inferenceVerified': False,
                        'reason': ('The isolated speech runtime and pinned components install automatically. '
                                   'A real synthesized clip must pass a signal check before Ready.')},
            'private': False, 'gated': bool(metadata.get('gated')), 'sourceUrl': 'https://huggingface.co/' + repository,
            'status': 'not-installed', 'familyId': package['familyId'], 'familyLabel': family['label'],
            'originalRepository': repository, 'builtIn': True, 'nativeModelIds': list(package.get('nativeModelIds', ())),
            'builtInDescription': package['description'], 'contextTokens': 8192, 'license': package['license'],
            'catalogOrder': 0, 'ttsRuntime': package['runtime'], 'ttsComponentBytes': extra,
            'outputsText': False, 'supportsTextGeneration': False, 'operatingModes': [],
        }
        validate_entry(entry)
        result.append(entry)
        existing.add(identity)
    return result
