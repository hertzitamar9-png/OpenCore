"""Offline regressions for the managed text-to-speech packages (Kitten TTS 2, VoxCPM2, Breeze-TTS-2, ZONOS3-Mini,
dots.tts.edit).

Nothing here downloads a model or starts the speech worker: the catalog is built from the
reviewed Hugging Face snapshots, and job execution is replaced by a fake that writes a WAV.
"""
import base64
import copy
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
import wave

REPO = Path(__file__).resolve().parents[3]
DISTRIBUTION = REPO / 'distribution'
EXTENSION = REPO / 'src-tauri/resources/runtime-setup/hf-extension'
for folder in (DISTRIBUTION, EXTENSION):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import hf_tts  # noqa: E402
from hf_download import validate_entry  # noqa: E402
from tts_catalog import PACKAGES, append_tts  # noqa: E402

SNAPSHOTS = DISTRIBUTION / 'hf-research/requested-models'
# Managed Qwen3-TTS runtimes and the built-in cards each one takes over.
QWEN3 = {'qwen3-tts-0.6b-base-v1': 'voice-cloning-qwen3-base-0-6b', 'qwen3-tts-1.7b-base-v1': 'voice-cloning-qwen3-base-1-7b',
         'qwen3-tts-0.6b-customvoice-v1': 'tts-qwen3-customvoice-0-6b', 'qwen3-tts-1.7b-customvoice-v1': 'tts-qwen3-customvoice-1-7b',
         'qwen3-tts-1.7b-voicedesign-v1': 'tts-qwen3-voicedesign-1-7b'}


def wav(seconds=2.0, rate=24000, channels=1, width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(b'\x10\x00' * int(seconds * rate) * channels if width == 2 else b'\x80' * int(seconds * rate) * channels)
    return base64.b64encode(buffer.getvalue()).decode()


def entries():
    return {entry['ttsRuntime']: entry for entry in append_tts([], SNAPSHOTS)}


def installed(entry, root):
    value = copy.deepcopy(entry)
    value.update(status='installed', localDirectory=str(Path(root) / 'models/hub' / entry['id']))
    return value


class CatalogTests(unittest.TestCase):
    def test_release_package_ships_the_speech_runtime(self):
        mappings = json.loads((REPO / 'distribution/resource-map.json').read_text(encoding='utf-8'))
        for name in ('hf_tts.py', 'tts_worker.py', 'tts-speech-lock.txt', 'hf_bridge.py', 'publisher-models.json'):
            target = 'runtime-setup/hf-extension/' + name
            with self.subTest(file=name):
                self.assertEqual(mappings.get(target), 'src-tauri/resources/' + target)
                self.assertTrue((REPO / mappings[target]).is_file())

    def test_pinned_packages_match_the_reviewed_runtime_revisions(self):
        values = entries()
        self.assertEqual(set(values), set(hf_tts.FAMILIES))
        self.assertEqual(set(values), {'kitten-tts-2-v1', 'voxcpm2-v1', 'breeze-tts-2-v1', 'zonos3-mini-v1', 'dots-tts-edit-v1'}
                         | set(QWEN3))
        for runtime, entry in values.items():
            family = hf_tts.FAMILIES[runtime]
            self.assertEqual(entry['repository'], family['repository'])
            self.assertEqual(entry['revision'], family['revision'])
            self.assertEqual((entry['category'], entry['libraryCategory']), ('tts', 'tts'))
            self.assertEqual(entry['runtime']['adapter'], 'managed-tts')
            self.assertFalse(entry['runtime']['inferenceVerified'])
            self.assertTrue(entry['installable'])
            self.assertTrue(all(pin['pinned'] and pin['revision'] == family['revision'] for pin in entry['files']))
            self.assertTrue(any(pin['role'] == 'model' for pin in entry['files']))
            self.assertTrue(hf_tts.supports(entry))
            validate_entry(entry)

    def test_packages_leave_out_files_the_runtime_never_loads(self):
        values = entries()
        names = {pin['filename'] for pin in values['kitten-tts-2-v1']['files']}
        self.assertNotIn('lm/model.safetensors', names)
        self.assertNotIn('lm/model-tl2-emb4.safetensors', names)
        self.assertTrue(any(name.startswith('voices/') for name in names))
        breeze = {pin['filename'] for pin in values['breeze-tts-2-v1']['files']}
        self.assertFalse(any(name.startswith('assets/') for name in breeze))
        self.assertIn('Non-commercial', next(p for p in PACKAGES if p['runtime'] == 'breeze-tts-2-v1')['description'])
        vox = {pin['filename'] for pin in values['voxcpm2-v1']['files']}
        self.assertNotIn('tokenization_voxcpm2.py', vox)
        self.assertTrue({'model.safetensors', 'audiovae.pth', 'tokenizer.json'} <= vox)
        zonos = {pin['filename'] for pin in values['zonos3-mini-v1']['files']}
        self.assertTrue({'lm/zonos3_mini/model.pth', 'lm/zonos3_mini/params.json', 'lm/zonos3_mini/tokenizer/superbpe8192.json',
                         'autoencoder/generator.pt', 'speaker_encoders/ownmect_s2/ckpt_0037.pth', 'web/voices/voices.json'} <= zonos)
        self.assertEqual(len([name for name in zonos if name.endswith('.embedding.npy')]), 27)
        # The GGUF builds, the teacher and the browser studio's audio are never loaded by the torch path.
        self.assertFalse([name for name in zonos if name.endswith(('.gguf', '.wav', '.codes.npy')) or 'teacher' in name])
        self.assertEqual(values['zonos3-mini-v1']['format'], 'pytorch')
        dots = {pin['filename'] for pin in values['dots-tts-edit-v1']['files']}
        self.assertTrue({'model.safetensors', 'vocoder.safetensors', 'speaker_encoder.safetensors', 'tokenizer.json'} <= dots)
        self.assertNotIn('README.md', dots)
        for entry in values.values():
            self.assertFalse([pin['filename'] for pin in entry['files'] if pin['filename'].endswith(('.py', '.sh', '.exe', '.dll'))])

    def test_kitten_counts_the_vocoder_component_bytes(self):
        values = entries()
        self.assertEqual(values['kitten-tts-2-v1']['ttsComponentBytes'], hf_tts.COMPONENTS['s3genMeanflow']['bytes'])
        self.assertEqual(values['voxcpm2-v1']['ttsComponentBytes'], 0)
        self.assertEqual(values['zonos3-mini-v1']['ttsComponentBytes'], hf_tts.COMPONENTS['zonos3Source']['bytes'])
        self.assertEqual(values['dots-tts-edit-v1']['ttsComponentBytes'], hf_tts.COMPONENTS['dotsSource']['bytes'])

    def test_a_snapshot_that_is_not_the_reviewed_checkpoint_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for source in SNAPSHOTS.glob('*.api.json'):
                (folder / source.name).write_bytes(source.read_bytes())
            target = folder / 'openbmb--VoxCPM2.api.json'
            value = json.loads(target.read_text(encoding='utf-8'))
            value['sha'] = '0' * 40
            target.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ValueError):
                append_tts([], folder)

    def test_rebuilding_replaces_earlier_tts_cards_instead_of_duplicating_them(self):
        first = append_tts([], SNAPSHOTS)
        again = append_tts(first, SNAPSHOTS)
        self.assertEqual(len(again), len(hf_tts.FAMILIES))


class RequestValidationTests(unittest.TestCase):
    def setUp(self):
        self.values = entries()

    def check(self, runtime, text='Hello there.', **settings):
        entry = self.values[runtime]
        return hf_tts.validate_request(entry, {'modelId': entry['id'], 'text': text, 'settings': settings})

    def test_text_limits_follow_each_family(self):
        for runtime, limit in (('kitten-tts-2-v1', 5000), ('voxcpm2-v1', 2000), ('breeze-tts-2-v1', 800),
                               ('zonos3-mini-v1', 1000), ('dots-tts-edit-v1', 500)):
            self.assertEqual(self.check(runtime, 'a' * limit)['text'], 'a' * limit)
            with self.assertRaises(ValueError):
                self.check(runtime, 'a' * (limit + 1))
            with self.assertRaises(ValueError):
                self.check(runtime, '   ')
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', 'bad\x07bell')
        self.assertEqual(self.check('kitten-tts-2-v1', 'Line one\nLine two\tend')['text'], 'Line one\nLine two\tend')

    def test_unknown_controls_are_refused(self):
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', speed=2)

    def test_seed_defaults_and_bounds(self):
        self.assertEqual(self.check('kitten-tts-2-v1')['settings']['seed'], 1234)
        self.assertEqual(self.check('kitten-tts-2-v1', seed=7)['settings']['seed'], 7)
        for bad in (-1, 2 ** 31, 1.5, True, '7'):
            with self.assertRaises(ValueError):
                self.check('kitten-tts-2-v1', seed=bad)

    def test_reference_transcript_rules(self):
        clip = wav()
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', referenceAudio=clip)
        with self.assertRaises(ValueError):
            self.check('breeze-tts-2-v1', referenceAudio=clip, referenceText='  ')
        self.assertEqual(self.check('kitten-tts-2-v1', referenceAudio=clip, referenceText=' Words. ')['settings']['referenceText'], 'Words.')
        self.assertEqual(self.check('voxcpm2-v1', referenceAudio=clip)['settings']['referenceText'], '')
        self.assertIsNotNone(self.check('voxcpm2-v1', referenceAudio=clip)['referenceAudio'])
        with self.assertRaises(ValueError):
            self.check('voxcpm2-v1', referenceText='No recording attached')
        with self.assertRaises(ValueError):
            self.check('voxcpm2-v1', referenceAudio=clip, referenceText='x' * 2001)
        # ZONOS3 clones from the sound alone; dots.tts.edit takes an optional transcript.
        self.assertEqual(self.check('zonos3-mini-v1', referenceAudio=clip)['settings']['referenceText'], '')
        with self.assertRaisesRegex(ValueError, 'clones from the recording alone'):
            self.check('zonos3-mini-v1', referenceAudio=clip, referenceText='Words.')
        self.assertEqual(self.check('dots-tts-edit-v1', referenceAudio=clip)['settings']['referenceText'], '')
        self.assertEqual(self.check('dots-tts-edit-v1', referenceAudio=clip, referenceText='Words.')['settings']['referenceText'], 'Words.')

    def test_instruction_only_for_families_that_take_one(self):
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', instruction='Speak warmly')
        self.assertEqual(self.check('voxcpm2-v1', instruction=' A calm narrator ')['settings']['instruction'], 'A calm narrator')
        self.assertEqual(self.check('breeze-tts-2-v1', instruction='Speak cheerfully')['settings']['instruction'], 'Speak cheerfully')
        with self.assertRaises(ValueError):
            self.check('voxcpm2-v1', instruction='x' * 501)

    def test_voice_and_preset_are_kitten_controls_and_a_reference_replaces_the_voice(self):
        self.assertEqual(self.check('kitten-tts-2-v1', voice='Bella', preset='expressive')['settings'],
                         {'seed': 1234, 'voice': 'Bella', 'preset': 'expressive'})
        cloned = self.check('kitten-tts-2-v1', voice='Bella', referenceAudio=wav(), referenceText='Words.')
        self.assertNotIn('voice', cloned['settings'])
        for runtime in ('voxcpm2-v1', 'breeze-tts-2-v1', 'dots-tts-edit-v1'):
            with self.assertRaises(ValueError):
                self.check(runtime, voice='Bella')
            with self.assertRaises(ValueError):
                self.check(runtime, preset='stable')
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', preset='loud')
        for preset in hf_tts.ZONOS3_STYLES:
            self.assertEqual(self.check('zonos3-mini-v1', voice='Cillian', preset=preset)['settings']['preset'], preset)
        with self.assertRaises(ValueError):
            self.check('zonos3-mini-v1', preset='expressive')
        self.assertNotIn('voice', self.check('zonos3-mini-v1', voice='Cillian', referenceAudio=wav())['settings'])

    def test_cfg_and_steps_ranges(self):
        self.assertEqual(self.check('voxcpm2-v1', cfg=2.5, steps=12)['settings']['steps'], 12)
        self.assertEqual(self.check('breeze-tts-2-v1', cfg=4)['settings']['cfg'], 4)
        for runtime, settings in (('voxcpm2-v1', {'cfg': 0.5}), ('voxcpm2-v1', {'steps': 31}), ('voxcpm2-v1', {'steps': 10.0}),
                                  ('breeze-tts-2-v1', {'cfg': 6.5}), ('breeze-tts-2-v1', {'steps': 10}),
                                  ('kitten-tts-2-v1', {'cfg': 2.0}), ('kitten-tts-2-v1', {'steps': 10})):
            with self.assertRaises(ValueError, msg=(runtime, settings)):
                self.check(runtime, **settings)

    def test_sampling_controls_only_for_language_model_families(self):
        value = self.check('kitten-tts-2-v1', temperature=0.8, topP=0.9, topK=50, minP=0.05, maxNewTokens=1000)['settings']
        self.assertEqual((value['temperature'], value['topK']), (0.8, 50))
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', temperature=3)
        self.assertEqual(self.check('zonos3-mini-v1', temperature=0.7, topP=0.9)['settings']['topP'], 0.9)
        for runtime in ('voxcpm2-v1', 'breeze-tts-2-v1', 'dots-tts-edit-v1'):
            with self.assertRaises(ValueError):
                self.check(runtime, temperature=0.8)

    def test_normalize_is_a_switch_where_the_family_has_one(self):
        self.assertIs(self.check('kitten-tts-2-v1', normalize=False)['settings']['normalize'], False)
        self.assertIs(self.check('zonos3-mini-v1', normalize=True)['settings']['normalize'], True)
        self.assertNotIn('normalize', self.check('voxcpm2-v1', normalize=False)['settings'])
        for runtime, value in (('kitten-tts-2-v1', 1), ('voxcpm2-v1', True), ('dots-tts-edit-v1', True)):
            with self.assertRaises(ValueError):
                self.check(runtime, normalize=value)

    def test_language_is_one_the_family_speaks(self):
        self.assertEqual(self.check('zonos3-mini-v1', language='ja')['settings']['language'], 'ja')
        self.assertEqual(self.check('dots-tts-edit-v1', language='zh')['settings']['language'], 'zh')
        self.assertNotIn('language', self.check('zonos3-mini-v1', language='')['settings'])
        for runtime, language in (('zonos3-mini-v1', 'zh'), ('zonos3-mini-v1', 'hi'), ('dots-tts-edit-v1', 'fr'),
                                  ('kitten-tts-2-v1', 'en'), ('voxcpm2-v1', 'en')):
            with self.assertRaises(ValueError, msg=(runtime, language)):
                self.check(runtime, language=language)

    def test_dots_edit_tags_change_a_reference_recording(self):
        edit = 'The quick brown <sub targ="red cat">fox</sub> jumps.'
        with self.assertRaisesRegex(ValueError, 'Add the recording to edit'):
            self.check('dots-tts-edit-v1', edit)
        for text in (edit, 'Say <emo type="happy" level="2">this</emo>.', 'A <pause act="ins" level="2"/> gap.',
                     'Make it <PITCH, semitones=5>higher</pitch>.', '< del>gone</del>'):
            self.assertEqual(self.check('dots-tts-edit-v1', text, referenceAudio=wav())['text'], text)
            with self.assertRaises(ValueError, msg=text):
                self.check('dots-tts-edit-v1', text)
        # Plain text, including angle brackets that are not edit tags, needs no recording.
        self.assertEqual(self.check('dots-tts-edit-v1', 'Two < three and <b>bold</b>.')['settings'], {'seed': 1234})
        self.assertEqual(self.check('dots-tts-edit-v1', cfg=2.0, steps=16)['settings']['steps'], 16)
        for settings in ({'cfg': 3.5}, {'steps': 3}, {'steps': 33}, {'instruction': 'Warm'}):
            with self.assertRaises(ValueError, msg=settings):
                self.check('dots-tts-edit-v1', **settings)

    def test_zonos3_takes_no_cfg_steps_tags_or_instruction(self):
        for settings in ({'cfg': 2.0}, {'steps': 10}, {'instruction': 'Warm'}):
            with self.assertRaises(ValueError, msg=settings):
                self.check('zonos3-mini-v1', **settings)
        self.assertEqual(hf_tts.capabilities(self.values['zonos3-mini-v1'])['tags'], [])

    def test_reference_audio_must_be_short_mono_pcm(self):
        self.assertTrue(hf_tts.decode_reference(wav(1.0, 16000)).startswith(b'RIFF'))
        for bad in (wav(2.0, channels=2), wav(2.0, width=1), wav(0.5), wav(61.0, 8000), wav(2.0, rate=96000),
                    'not base64!', base64.b64encode(b'RIFF but not a wave').decode(), 12345,
                    'A' * ((hf_tts.MAX_REFERENCE_BYTES + 2) // 3 * 4 + 4)):
            with self.assertRaises(ValueError):
                hf_tts.decode_reference(bad)


class Qwen3TtsTests(unittest.TestCase):
    """The built-in Qwen3 TTS cards install and run through the managed speech runtime."""

    def setUp(self):
        self.values = entries()

    def check(self, runtime, text='Hello there.', **settings):
        entry = self.values[runtime]
        return hf_tts.validate_request(entry, {'modelId': entry['id'], 'text': text, 'settings': settings})

    def test_packages_take_over_the_built_in_cards_at_the_same_precision(self):
        catalog = json.loads((REPO / 'src-tauri/resources/model-catalog.json').read_text(encoding='utf-8'))
        native = {model['id']: model for model in catalog['models']}
        for runtime, identity in QWEN3.items():
            entry = self.values[runtime]
            with self.subTest(runtime=runtime):
                self.assertEqual(entry['nativeModelIds'], [identity])
                self.assertEqual(entry['quantization'], native[identity]['precision'])
                self.assertEqual(entry['repository'], hf_tts.FAMILIES[runtime]['repository'])
                # The studio lists managed speech cards by category, so the Base cloning cards stay text-to-speech cards.
                self.assertEqual(entry['category'], 'tts')
                names = {pin['filename'] for pin in entry['files']}
                self.assertTrue({'model.safetensors', 'config.json', 'vocab.json', 'merges.txt',
                                 'speech_tokenizer/model.safetensors', 'speech_tokenizer/config.json'} <= names)
                self.assertNotIn('README.md', names)
                self.assertEqual(entry['ttsComponentBytes'], 0)
        others = [entry for runtime, entry in self.values.items() if runtime not in QWEN3]
        self.assertTrue(all(entry['nativeModelIds'] == [] for entry in others))

    def test_base_needs_a_reference_and_takes_an_optional_transcript(self):
        for runtime in ('qwen3-tts-0.6b-base-v1', 'qwen3-tts-1.7b-base-v1'):
            with self.assertRaisesRegex(ValueError, 'clones a voice; add a reference recording'):
                self.check(runtime)
            self.assertEqual(self.check(runtime, referenceAudio=wav())['settings']['referenceText'], '')
            self.assertEqual(self.check(runtime, referenceAudio=wav(), referenceText='Words.')['settings']['referenceText'], 'Words.')
            for settings in ({'voice': 'Ryan'}, {'instruction': 'Warm'}):
                with self.assertRaises(ValueError, msg=settings):
                    self.check(runtime, referenceAudio=wav(), **settings)

    def test_custom_voice_and_voice_design_do_not_clone(self):
        for runtime in ('qwen3-tts-0.6b-customvoice-v1', 'qwen3-tts-1.7b-customvoice-v1', 'qwen3-tts-1.7b-voicedesign-v1'):
            with self.assertRaisesRegex(ValueError, 'does not clone voices'):
                self.check(runtime, referenceAudio=wav())
        self.assertEqual(self.check('qwen3-tts-0.6b-customvoice-v1', voice='Sohee', language='korean')['settings'],
                         {'seed': 1234, 'voice': 'Sohee', 'language': 'korean'})
        # Only the 1.7B CustomVoice model follows a speaking-style instruction.
        with self.assertRaisesRegex(ValueError, 'does not take a voice instruction; choose one of its voices instead'):
            self.check('qwen3-tts-0.6b-customvoice-v1', instruction='Very happy')
        self.assertEqual(self.check('qwen3-tts-1.7b-customvoice-v1', voice='Ryan', instruction='Very happy')['settings']['instruction'],
                         'Very happy')
        self.assertEqual(self.check('qwen3-tts-1.7b-voicedesign-v1', instruction='A calm deep voice')['settings']['instruction'],
                         'A calm deep voice')
        with self.assertRaises(ValueError):
            self.check('qwen3-tts-1.7b-voicedesign-v1', voice='Ryan')

    def test_sampling_languages_and_text_limits(self):
        for runtime in QWEN3:
            extra = {'referenceAudio': wav()} if 'base' in runtime else {}
            value = self.check(runtime, temperature=0.8, topP=0.9, topK=40, maxNewTokens=1200, language='german', **extra)['settings']
            self.assertEqual((value['temperature'], value['topP'], value['topK'], value['maxNewTokens'], value['language']),
                             (0.8, 0.9, 40, 1200, 'german'))
            for settings in ({'minP': 0.05}, {'cfg': 2.0}, {'steps': 10}, {'preset': 'Calm'}, {'normalize': True},
                             {'language': 'en'}, {'language': 'hebrew'}):
                with self.assertRaises(ValueError, msg=(runtime, settings)):
                    self.check(runtime, **settings, **extra)
            self.assertEqual(len(self.check(runtime, 'a' * 1500, **extra)['text']), 1500)
            with self.assertRaises(ValueError):
                self.check(runtime, 'a' * 1501, **extra)

    def test_every_family_verification_request_is_valid(self):
        for runtime, entry in self.values.items():
            family = hf_tts.FAMILIES[runtime]
            settings = dict(family['verification']['settings'])
            if family.get('referenceRequired'):
                settings['referenceAudio'] = hf_tts.verification_reference()
            with self.subTest(runtime=runtime):
                hf_tts.validate_request(entry, {'modelId': entry['id'], 'text': family['verification']['text'],
                                                'settings': settings})

    def test_the_synthetic_verification_reference_is_a_voiced_wav(self):
        data = hf_tts.decode_reference(hf_tts.verification_reference())
        with wave.open(io.BytesIO(data)) as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 16000))
            self.assertEqual(audio.getnframes(), 48000)
            frames = audio.readframes(audio.getnframes())
        samples = [int.from_bytes(frames[index:index + 2], 'little', signed=True) for index in range(0, len(frames), 2)]
        self.assertGreater(max(abs(value) for value in samples), 15000)
        # Syllables rise and fall, so it is not one steady tone.
        quiet = sum(1 for value in samples[::160] if abs(value) < 300)
        self.assertGreater(quiet, 10)

    def test_status_reports_the_qwen_voices_and_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = hf_tts.TtsManager(Path(directory), REPO / 'src-tauri/resources')
            try:
                values = {key: installed(value, directory) for key, value in self.values.items() if key in QWEN3}
                status = manager.status(list(values.values()))
                custom = status['models'][values['qwen3-tts-1.7b-customvoice-v1']['id']]
                self.assertEqual([voice['id'] for voice in custom['voices']],
                                 ['Ryan', 'Aiden', 'Vivian', 'Serena', 'Uncle_Fu', 'Dylan', 'Eric', 'Ono_Anna', 'Sohee'])
                self.assertEqual(custom['capabilities']['instruction']['label'], 'Speaking style')
                self.assertEqual(list(custom['capabilities']['samplingControls']), ['temperature', 'topP', 'topK', 'maxNewTokens'])
                self.assertEqual(len(custom['capabilities']['languages']), 10)
                self.assertIsNone(status['models'][values['qwen3-tts-0.6b-customvoice-v1']['id']]['capabilities']['instruction'])
                base = status['models'][values['qwen3-tts-1.7b-base-v1']['id']]
                self.assertEqual((base['capabilities']['reference'], base['capabilities']['referenceRequired']),
                                 ('transcript-optional', True))
                self.assertEqual(base['voices'], [])
                design = status['models'][values['qwen3-tts-1.7b-voicedesign-v1']['id']]
                self.assertEqual(design['capabilities']['instruction']['label'], 'Voice description')
                self.assertIsNone(design['capabilities']['reference'])
                with self.assertRaises(ValueError):
                    entry = values['qwen3-tts-1.7b-customvoice-v1']
                    manager.submit(entry, {'modelId': entry['id'], 'text': 'Hi', 'settings': {'voice': 'Nobody'}})
            finally:
                manager.close()


def fake_execute(manager, job, entry):
    output = hf_tts.managed_path(manager.directory, job['id'], 'speech.wav')
    output.write_bytes(base64.b64decode(wav(1.2)))
    manager._update(job, status='completed', stage='Speech ready', outputs=[str(output)],
                    result={'sampleRate': 24000, 'durationS': 1.2})


def wait_until(check, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


class JobLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = mock.patch.object(hf_tts.TtsManager, '_execute', fake_execute)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = hf_tts.TtsManager(self.root, REPO / 'src-tauri/resources')
        self.addCleanup(self.manager.close)
        self.values = {key: installed(value, self.root) for key, value in entries().items()}
        voices = self.root / 'models/hub' / self.values['kitten-tts-2-v1']['id'] / 'voices'
        voices.mkdir(parents=True)
        (voices / 'voices.json').write_text(json.dumps({'Bella': {'description': 'Bright female voice'}, 'Jasper': {}}), encoding='utf-8')
        zonos = self.root / 'models/hub' / self.values['zonos3-mini-v1']['id'] / 'web/voices'
        zonos.mkdir(parents=True)
        (zonos / 'voices.json').write_text(json.dumps([
            {'id': 'default_1', 'label': 'Cillian en Irish M 1', 'embedding': 'English/Cillian_en-Irish_M_1.embedding.npy'},
            {'id': 'default_2', 'label': 'Imogen en British F 1'}, {'id': 'default_3', 'label': 'Gaspard fr M 1'},
            {'id': 'default_4', 'label': 'Haoran zh energetic M 1'}, {'id': 'default_5', 'label': 'Ananya hi F 1'},
            {'id': 'default_6', 'label': 'Chaitanya te M 1'}, {'id': 'default_7', 'label': 'Thaddeus en announcervoice M 1'},
            {'id': 'default_8', 'label': 'Willow en asrm F 1'},
            {'id': 'bad', 'label': 'Nameless'}, {'id': 'dup', 'label': 'Cillian en Irish M 2'}, 'not a voice']), encoding='utf-8')

    def submit(self, runtime, text='Hello.', internal=False, **settings):
        entry = self.values[runtime]
        return self.manager.submit(entry, {'modelId': entry['id'], 'text': text, 'settings': settings}, internal=internal)

    def finished(self, identity):
        self.assertTrue(wait_until(lambda: (self.manager.job(identity) or {}).get('status') == 'completed'))
        return self.manager.job(identity)

    def test_submit_runs_lists_previews_and_deletes(self):
        job = self.submit('kitten-tts-2-v1', voice='Bella', text='[excited] Hello <laugh> there.')
        self.assertEqual(job['status'], 'queued')
        done = self.finished(job['id'])
        self.assertEqual(done['request']['settings']['voice'], 'Bella')
        self.assertEqual([row['id'] for row in self.manager.list()], [job['id']])
        preview = self.manager.preview(job['id'])
        self.assertEqual(preview['mime'], 'audio/wav')
        self.assertTrue(preview['dataUrl'].startswith('data:audio/wav;base64,UklGR'))
        self.assertEqual(self.manager.delete(job['id']), {'deleted': True})
        self.assertEqual(self.manager.list(), [])
        self.assertFalse((self.manager.directory / job['id']).exists())

    def test_reference_is_stored_with_the_job_and_internal_checks_stay_hidden(self):
        job = self.submit('voxcpm2-v1', referenceAudio=wav(), instruction='Calm narrator')
        done = self.finished(job['id'])
        reference = Path(done['request']['settings']['referencePath'])
        self.assertEqual(reference.parent, self.manager.directory / job['id'])
        self.assertTrue(reference.read_bytes().startswith(b'RIFF'))
        hidden = self.submit('voxcpm2-v1', internal=True)
        self.finished(hidden['id'])
        self.assertNotIn(hidden['id'], [row['id'] for row in self.manager.list()])

    def test_kitten_voice_must_exist_in_the_installed_voice_list(self):
        names = [voice['id'] for voice in self.manager.voices(self.values['kitten-tts-2-v1'])]
        self.assertEqual(names, ['Bella', 'Jasper'])
        with self.assertRaises(ValueError):
            self.submit('kitten-tts-2-v1', voice='Nobody')

    def test_zonos3_voices_come_from_the_release_voice_index(self):
        voices = self.manager.voices(self.values['zonos3-mini-v1'])
        self.assertEqual(voices, [
            {'id': 'Cillian', 'language': 'en_us', 'gender': 'male', 'style': 'Irish'},
            {'id': 'Imogen', 'language': 'en_gb', 'gender': 'female', 'style': 'British'},
            {'id': 'Gaspard', 'language': 'fr_fr', 'gender': 'male', 'style': ''},
            {'id': 'Haoran', 'language': 'cmn', 'gender': 'male', 'style': 'energetic'},
            # Hindi and Telugu speakers are kept as voices and say so; the model speaks one of its own languages with them.
            {'id': 'Ananya', 'language': 'en_us', 'gender': 'female', 'style': 'Hindi speaker'},
            {'id': 'Chaitanya', 'language': 'en_us', 'gender': 'male', 'style': 'Telugu speaker'},
            # The release index's misspelled style words are shown readably.
            {'id': 'Thaddeus', 'language': 'en_us', 'gender': 'male', 'style': 'announcer'},
            {'id': 'Willow', 'language': 'en_us', 'gender': 'female', 'style': 'ASMR'}])
        languages = {item['id'] for item in hf_tts.FAMILIES['zonos3-mini-v1']['languages']}
        self.assertTrue(all(voice['language'] in languages for voice in voices))
        job = self.submit('zonos3-mini-v1', voice='Imogen', preset='Calm', language='en_gb', temperature=0.7, normalize=True)
        self.assertEqual(self.finished(job['id'])['request']['settings']['voice'], 'Imogen')
        with self.assertRaises(ValueError):
            self.submit('zonos3-mini-v1', voice='Nobody')
        self.assertEqual(self.manager.voices(self.values['dots-tts-edit-v1']), [])

    def test_dots_edit_job_keeps_the_recording_it_edits(self):
        job = self.submit('dots-tts-edit-v1', text='The quick <sub targ="red">brown</sub> fox.', referenceAudio=wav())
        done = self.finished(job['id'])
        self.assertTrue(Path(done['request']['settings']['referencePath']).read_bytes().startswith(b'RIFF'))
        with self.assertRaises(ValueError):
            self.submit('dots-tts-edit-v1', text='The quick <sub targ="red">brown</sub> fox.')

    def test_requests_need_an_installed_matching_model(self):
        entry = copy.deepcopy(self.values['voxcpm2-v1'])
        with self.assertRaises(ValueError):
            self.manager.submit(entry, {'modelId': self.values['breeze-tts-2-v1']['id'], 'text': 'Hi'})
        entry['status'] = 'not-installed'
        with self.assertRaises(ValueError):
            self.manager.submit(entry, {'modelId': entry['id'], 'text': 'Hi'})
        entry = copy.deepcopy(self.values['voxcpm2-v1'])
        entry['revision'] = '1' * 40
        with self.assertRaises(ValueError):
            self.manager.submit(entry, {'modelId': entry['id'], 'text': 'Hi'})

    def test_a_queued_job_can_be_cancelled_and_unfinished_jobs_cannot_be_previewed(self):
        gate = threading.Event()

        def blocked(manager, job, entry):
            gate.wait(5)
            fake_execute(manager, job, entry)

        with mock.patch.object(hf_tts.TtsManager, '_execute', blocked):
            first = self.submit('voxcpm2-v1')
            second = self.submit('voxcpm2-v1')
            self.assertEqual(self.manager.cancel(second['id']), {'cancelled': True})
            self.assertEqual(self.manager.job(second['id'])['status'], 'cancelled')
            with self.assertRaises(ValueError):
                self.manager.preview(second['id'])
            gate.set()
            self.finished(first['id'])
        with self.assertRaises(ValueError):
            self.manager.cancel(first['id'])

    def test_status_reports_capabilities_voices_and_components(self):
        status = self.manager.status(list(self.values.values()))
        self.assertEqual(status['environment'], hf_tts.ENVIRONMENT)
        kitten = status['models'][self.values['kitten-tts-2-v1']['id']]
        self.assertEqual(kitten['capabilities']['reference'], 'transcript-required')
        self.assertIn({'group': 'Vocal event', 'insert': '<laugh> ', 'label': 'laugh'}, kitten['capabilities']['tags'])
        self.assertEqual([voice['id'] for voice in kitten['voices']], ['Bella', 'Jasper'])
        self.assertEqual(kitten['components'][0]['repository'], 'ResembleAI/chatterbox-turbo')
        self.assertFalse(kitten['components'][0]['ready'])
        vox = status['models'][self.values['voxcpm2-v1']['id']]
        self.assertEqual(vox['capabilities']['steps']['max'], 30)
        self.assertEqual(vox['components'], [])
        zonos = status['models'][self.values['zonos3-mini-v1']['id']]
        self.assertEqual(zonos['capabilities']['reference'], 'audio-only')
        self.assertEqual(zonos['capabilities']['presets'], hf_tts.ZONOS3_STYLES)
        self.assertEqual(len(zonos['capabilities']['languages']), 10)
        self.assertEqual(zonos['components'][0]['repository'], 'Zyphra/ZONOS3-Mini')
        dots = status['models'][self.values['dots-tts-edit-v1']['id']]
        self.assertIs(dots['capabilities']['editTags'], True)
        self.assertEqual([item['id'] for item in dots['capabilities']['languages']], ['en', 'zh'])
        self.assertTrue(all(tag['insert'] and (not tag.get('wrap') or len(tag['wrap']) == 2) for tag in dots['capabilities']['tags']))
        self.assertTrue(all(hf_tts.DOTS_EDIT_TAG.search(tag['insert']) for tag in dots['capabilities']['tags']))
        self.assertEqual(dots['components'][0]['repository'], 'studio-dots-ai/dots.tts')
        self.assertIsNone(status['models'][self.values['kitten-tts-2-v1']['id']]['capabilities']['editTags'])

    def test_the_worker_starts_isolated_in_utf8_mode_at_low_priority(self):
        seen = {}

        def popen(command, **options):
            seen.update(command=command, **options)
            raise OSError('stop here')
        with mock.patch.object(hf_tts.subprocess, 'Popen', popen), self.assertRaises(OSError):
            self.manager._start_worker()
        self.assertEqual(seen['command'][1:4], ['-I', '-X', 'utf8'])
        self.assertTrue(seen['command'][4].endswith('tts_worker.py'))
        self.assertEqual(seen['env']['HF_HUB_OFFLINE'], '1')
        if sys.platform == 'win32':
            self.assertTrue(seen['creationflags'] & hf_tts.subprocess.BELOW_NORMAL_PRIORITY_CLASS)

    def test_ready_needs_matching_proof(self):
        entry = self.values['voxcpm2-v1']
        proof = {'inferenceVerified': True, 'receiptRevision': entry['revision'], 'ttsRuntime': 'voxcpm2-v1',
                 'workerVersion': hf_tts.WORKER_VERSION, 'workerSha256': self.manager.worker_identity(),
                 'lockSha256': self.manager.lock_identity()}
        with mock.patch.object(hf_tts.TtsManager, 'dependencies_ready', return_value=True):
            self.assertTrue(self.manager.ready(entry, proof))
            for key, value in (('inferenceVerified', False), ('receiptRevision', '0' * 40), ('workerSha256', 'x'),
                               ('lockSha256', 'x'), ('workerVersion', 0), ('ttsRuntime', 'kitten-tts-2-v1')):
                self.assertFalse(self.manager.ready(entry, {**proof, key: value}), key)
        self.assertFalse(self.manager.ready(entry, proof))


try:
    import hf_bridge as bridge
except ModuleNotFoundError:
    bridge = None


@unittest.skipIf(bridge is None, 'The managed bridge is not importable')
class BridgeRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = mock.patch.object(hf_tts.TtsManager, '_execute', fake_execute)
        patcher.start()
        self.addCleanup(patcher.stop)
        client = bridge.HubClient(lambda: None, endpoint='http://127.0.0.1:9', allow_test_http=True)
        self.state = bridge.BridgeState(self.root, REPO / 'src-tauri/resources', client=client, runtime=None)
        self.server = bridge.BridgeServer(('127.0.0.1', 0), self.state, 'test-secret')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.state.close)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.entry = entries()['voxcpm2-v1']
        with self.state.lock:
            self.state.entries[self.entry['id']] = copy.deepcopy(self.entry)
        folder = self.root / 'models/hub' / self.entry['id']
        folder.mkdir(parents=True)
        receipt = {'localDirectory': str(folder), 'receiptPath': str(folder / 'install-receipt.json')}
        self.state._receipt = lambda entry: dict(receipt) if entry['id'] == self.entry['id'] else None
        self.ready = True
        self.state.tts.ready = lambda entry, proof: self.ready

    def request(self, method, path, value=None, secret='test-secret'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if secret:
            headers['X-OpenCore-Hub-Secret'] = secret
        connection.request(method, path, body=json.dumps(value) if value is not None else None, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, json.loads(payload) if payload else None

    def body(self, text='Hello from the bridge.', **settings):
        return {'request': {'modelId': self.entry['id'], 'text': text, 'settings': settings}}

    def test_routes_need_the_secret(self):
        self.assertEqual(self.request('GET', '/api/tts/status', secret=None)[0], 401)
        self.assertEqual(self.request('POST', '/api/tts/jobs', self.body(), secret='wrong')[0], 401)
        self.assertEqual(self.request('GET', '/api/speech/support', secret=None)[0], 401)

    def test_speech_support_says_which_models_run_and_why_others_cannot(self):
        status, value = self.request('GET', '/api/speech/support')
        self.assertEqual(status, 200)
        models = value['models']
        self.assertEqual(models['tts-qwen3-customvoice-1-7b'], {'state': 'managed', 'use': ['hf-78c1cf6773e25cc97a8388f6']})
        self.assertEqual(models['phonon-2'], {'state': 'native', 'use': ['phonon-2']})
        self.assertEqual(models['tts-chatterbox']['state'], 'unavailable')
        self.assertIn('chatterbox engine', models['tts-chatterbox']['reason'])

    def test_catalog_cards_carry_the_hardware_estimate(self):
        self.state.tts.gpu = None
        card = next(value for value in self.state.catalog() if value['id'] == self.entry['id'])
        self.assertEqual(card['hardwareFit']['device'], 'cpu')
        self.assertIn('no NVIDIA GPU was found', card['hardwareFit']['reason'])

    def test_status_lists_installed_tts_models(self):
        status, value = self.request('GET', '/api/tts/status')
        self.assertEqual(status, 200)
        self.assertIn(self.entry['id'], value['status']['models'])

    def test_generate_needs_a_prepared_model_and_a_valid_request(self):
        self.ready = False
        self.assertEqual(self.request('POST', '/api/tts/jobs', self.body())[0], 409)
        self.ready = True
        status, value = self.request('POST', '/api/tts/jobs', self.body(temperature=0.7))
        self.assertEqual(status, 400)
        self.assertIn('temperature', json.dumps(value))
        status, value = self.request('POST', '/api/tts/jobs', self.body(cfg=2.0, steps=10, instruction='Calm narrator'))
        self.assertEqual(status, 202)
        identity = value['job']['id']
        self.assertTrue(wait_until(lambda: (self.state.tts.job(identity) or {}).get('status') == 'completed'))
        status, value = self.request('GET', '/api/tts/jobs')
        self.assertEqual((status, value['jobs'][0]['id']), (200, identity))
        status, value = self.request('POST', '/api/tts/preview', {'id': identity})
        self.assertEqual(status, 200)
        self.assertTrue(value['dataUrl'].startswith('data:audio/wav;base64,'))
        self.assertEqual(self.request('POST', '/api/tts/delete', {'id': identity}), (200, {'deleted': True}))
        self.assertEqual(self.request('POST', '/api/tts/cancel', {'id': identity})[0], 400)


CU128_ARCHES = ('sm_70', 'sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120', 'compute_120')


def fake_torch(available=True, name='NVIDIA GeForce RTX 3060', capability=(8, 6), free_gib=11.0, bf16=True,
               arches=CU128_ARCHES):
    cuda = types.SimpleNamespace(
        is_available=lambda: available, get_device_name=lambda index=0: name,
        get_device_capability=lambda index=0: capability,
        mem_get_info=lambda index=0: (int(free_gib * 1024 ** 3), 24 * 1024 ** 3),
        get_arch_list=lambda: list(arches), is_bf16_supported=lambda including_emulation=True: bf16)
    return types.SimpleNamespace(cuda=cuda)


class DeviceSelectionTests(unittest.TestCase):
    """The worker must pick a device that works on any computer, not only the one it was built on."""
    VOX = {'label': 'VoxCPM2', 'gpuGiB': 7, 'bf16': True, 'cpu': True, 'summary': 'a recent NVIDIA GPU'}
    BREEZE = {'label': 'Breeze-TTS-2', 'gpuGiB': 10, 'bf16': True, 'cpu': False, 'summary': 'an RTX 30-series GPU'}
    KITTEN = {'label': 'Kitten TTS 2', 'gpuGiB': 3, 'bf16': False, 'cpu': True, 'summary': 'any computer'}

    def choose(self, hardware, **gpu):
        import tts_worker
        with mock.patch.dict(sys.modules, {'torch': fake_torch(**gpu)}):
            return tts_worker.choose_device(hardware)

    def test_every_family_declares_its_hardware_to_the_studio(self):
        for runtime, entry in entries().items():
            hardware = hf_tts.FAMILIES[runtime]['hardware']
            self.assertGreater(hardware['gpuGiB'], 0)
            self.assertIsInstance(hardware['bf16'], bool)
            self.assertIsInstance(hardware['cpu'], bool)
            self.assertTrue(hardware['summary'])
            self.assertEqual(hf_tts.capabilities(entry)['hardware'], hardware)

    def test_a_supported_gpu_with_room_is_used(self):
        self.assertEqual(self.choose(self.VOX), ('cuda', None))
        self.assertEqual(self.choose(self.BREEZE, name='NVIDIA GeForce RTX 5090', capability=(12, 0), free_gib=30), ('cuda', None))

    def test_no_gpu_uses_cpu_or_explains_the_requirement(self):
        device, note = self.choose(self.KITTEN, available=False)
        self.assertEqual(device, 'cpu')
        self.assertIn('No NVIDIA GPU', note)
        with self.assertRaisesRegex(RuntimeError, 'Breeze-TTS-2 needs an RTX 30-series GPU'):
            self.choose(self.BREEZE, available=False)

    def test_gpus_missing_from_the_pytorch_build_are_not_used(self):
        device, note = self.choose(self.KITTEN, name='NVIDIA GeForce GTX 1080', capability=(6, 1), bf16=False)
        self.assertEqual(device, 'cpu')
        self.assertIn('GTX 1080 is not supported by the bundled PyTorch build', note)

    def test_bf16_models_skip_gpus_without_native_bf16(self):
        turing = {'name': 'NVIDIA GeForce RTX 2080 Ti', 'capability': (7, 5), 'bf16': False}
        self.assertEqual(self.choose(self.KITTEN, **turing), ('cuda', None))
        device, note = self.choose(self.VOX, **turing)
        self.assertEqual(device, 'cpu')
        self.assertIn('no native BF16 support', note)
        with self.assertRaisesRegex(RuntimeError, 'no native BF16 support'):
            self.choose(self.BREEZE, **turing)

    def test_low_free_memory_says_how_to_free_it(self):
        device, note = self.choose(self.VOX, name='NVIDIA GeForce RTX 4060', capability=(8, 9), free_gib=2.5)
        self.assertEqual(device, 'cpu')
        self.assertIn('2.5 GB of free memory', note)
        self.assertIn('unload other models', note)

    def test_newer_gpus_use_compatible_kernels(self):
        self.assertEqual(self.choose(self.VOX, capability=(12, 1), free_gib=100), ('cuda', None))
        self.assertEqual(self.choose(self.VOX, capability=(13, 0), free_gib=100), ('cuda', None))
        self.assertEqual(self.choose(self.VOX, capability=(13, 0), arches=('sm_80', 'sm_90'))[0], 'cpu')

    def test_missing_hardware_spec_still_runs(self):
        self.assertEqual(self.choose(None, available=False)[0], 'cpu')
        self.assertEqual(self.choose({}), ('cuda', None))

    def test_cards_warn_before_download_when_the_computer_cannot_run_a_model(self):
        fit = hf_tts.hardware_fit
        rtx4090 = {'name': 'NVIDIA GeForce RTX 4090', 'totalGiB': 23.99, 'capability': [8, 9]}
        rtx3080 = {'name': 'NVIDIA GeForce RTX 3080', 'totalGiB': 10.0, 'capability': [8, 6]}
        gtx1660 = {'name': 'NVIDIA GeForce GTX 1660', 'totalGiB': 6.0, 'capability': [7, 5]}
        gtx1080 = {'name': 'NVIDIA GeForce GTX 1080', 'totalGiB': 8.0, 'capability': [6, 1]}
        for runtime in hf_tts.FAMILIES:
            self.assertEqual(fit(runtime, rtx4090), {'device': 'cuda', 'gpu': rtx4090['name'], 'reason': None})
        self.assertEqual(fit('kitten-tts-2-v1', None)['device'], 'cpu')
        self.assertEqual(fit('kitten-tts-2-v1', gtx1660)['device'], 'cuda')
        self.assertIn('too old for the bundled PyTorch build', fit('kitten-tts-2-v1', gtx1080)['reason'])
        self.assertEqual(fit('voxcpm2-v1', gtx1660)['device'], 'cpu')
        self.assertIn('no native BF16 support', fit('voxcpm2-v1', gtx1660)['reason'])
        self.assertIsNone(fit('breeze-tts-2-v1', rtx3080)['device'])
        self.assertIn('needs a card with about 12 GB', fit('breeze-tts-2-v1', rtx3080)['reason'])
        self.assertIsNone(fit('breeze-tts-2-v1', None)['device'])
        self.assertIn('cannot run on the CPU', fit('breeze-tts-2-v1', None)['reason'])

    def test_new_families_fall_back_to_the_cpu_on_any_computer(self):
        fit = hf_tts.hardware_fit
        rtx3060 = {'name': 'NVIDIA GeForce RTX 3060', 'totalGiB': 12.0, 'capability': [8, 6]}
        rtx3050 = {'name': 'NVIDIA GeForce RTX 3050', 'totalGiB': 6.0, 'capability': [8, 6]}
        rtx3070 = {'name': 'NVIDIA GeForce RTX 3070', 'totalGiB': 8.0, 'capability': [8, 6]}
        gtx1660 = {'name': 'NVIDIA GeForce GTX 1660', 'totalGiB': 6.0, 'capability': [7, 5]}
        for runtime in ('zonos3-mini-v1', 'dots-tts-edit-v1'):
            self.assertEqual(fit(runtime, rtx3060)['device'], 'cuda')
            # No NVIDIA driver: CPU-only computers and AMD or Intel graphics.
            self.assertEqual(fit(runtime, None)['device'], 'cpu')
            self.assertIn('no NVIDIA GPU was found', fit(runtime, None)['reason'])
            self.assertEqual(fit(runtime, gtx1660)['device'], 'cpu')
            self.assertIn('no native BF16 support', fit(runtime, gtx1660)['reason'])
            self.assertEqual(fit(runtime, rtx3050)['device'], 'cpu')
        self.assertEqual(fit('zonos3-mini-v1', rtx3070)['device'], 'cuda')
        self.assertEqual(fit('dots-tts-edit-v1', rtx3070)['device'], 'cpu')
        self.assertIn('needs a card with about 10 GB', fit('dots-tts-edit-v1', rtx3070)['reason'])
        zonos = dict(hf_tts.FAMILIES['zonos3-mini-v1']['hardware'], label='ZONOS3-Mini')
        device, note = self.choose(zonos, name='NVIDIA GeForce RTX 4070', capability=(8, 9), free_gib=4.8)
        self.assertEqual(device, 'cpu')
        self.assertIn('4.8 GB of free memory and ZONOS3-Mini needs about 6 GB', note)
        self.assertEqual(self.choose(zonos, available=False)[0], 'cpu')

    def test_the_gpu_probe_never_fails_the_catalog(self):
        with mock.patch('ctypes.CDLL', side_effect=OSError('no driver')):
            self.assertIsNone(hf_tts.nvidia_gpu())

    def test_status_reports_why_the_cpu_was_chosen(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        manager = hf_tts.TtsManager(Path(temp.name), REPO / 'src-tauri/resources')
        self.addCleanup(manager.close)
        manager.child_info = {'device': 'cpu', 'deviceNote': 'No NVIDIA GPU was found. Running on the CPU instead.'}
        status = manager.status()
        self.assertEqual((status['device'], status['deviceNote']), ('cpu', manager.child_info['deviceNote']))


if __name__ == '__main__':
    unittest.main()
