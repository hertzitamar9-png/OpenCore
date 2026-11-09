"""Pinned multilingual Phonon-2.5 packages for the managed dictation runtime."""
import json

from hf_catalog import _pin, stable_id
from hf_download import validate_entry

REPOSITORY = 'mudler/parakeet-cpp-gguf'
REVISION = '07a08a3a405d20c5434b9a1442f6e6cf571f57ad'
FAMILY = 'publisher-phonon-2-5'
PRESETS = (('Packed', 'packed'), ('Q4_K', 'q4_k'), ('Q5_K', 'q5_k'),
           ('Q6_K', 'q6_k'), ('Q8_0', 'q8_0'))


def apply_presets(entries, snapshots):
    original = next((entry for entry in entries if entry.get('familyId') == FAMILY), None)
    if original is None:
        return entries
    metadata = json.loads((snapshots / 'mudler--parakeet-cpp-gguf.api.json').read_text(encoding='utf-8'))
    if metadata['sha'] != REVISION or 'FermionResearch/Phonon-2.5' not in metadata['cardData']['base_model']:
        raise ValueError('The Phonon-2.5 conversion source does not match its reviewed checkpoint')
    files = {item['rfilename']: item for item in metadata['siblings']}
    result = [entry for entry in entries if entry.get('familyId') != FAMILY]
    for order, (label, kind) in enumerate(PRESETS):
        derived = kind in ('q5_k', 'q6_k')
        filename = 'phonon-2.5-' + ('f16' if derived else kind) + '.gguf'
        pins = [_pin(REPOSITORY, REVISION, files[filename], 'model'),
                _pin(REPOSITORY, REVISION, files['NOTICE-phonon-2.5.txt'], 'support')]
        entry = dict(original, id=stable_id(REPOSITORY, 'phonon25-' + kind),
                     repository=REPOSITORY, revision=REVISION, familyId=FAMILY,
                     format='gguf', quantization=label, files=pins, bytes=sum(pin['bytes'] for pin in pins),
                     variantKey='phonon25-' + kind, subdirectory='', installable=True,
                     speechRuntime='parakeet-phonon25-v1', speechQuantization=kind,
                     speechConversion=kind if derived else None, catalogOrder=order,
                     sourceUrl='https://huggingface.co/' + REPOSITORY,
                     runtime={'adapter': 'phonon25', 'available': False, 'inferenceVerified': False,
                              'reason': 'A lightweight local speech engine prepares automatically. '
                                        'Use for dictation enables the regular chat microphone.'},
                     license='cc-by-4.0')
        entry['builtInDescription'] = ('Multilingual speech recognition for 25 languages. '
                                      'Fermion Research Phonon-2.5, converted by mudler for parakeet.cpp.'
                                      + (' This option downloads the verified F16 source and prepares ' + label
                                         + ' locally during installation.' if derived else ''))
        validate_entry(entry)
        result.append(entry)
    return result
