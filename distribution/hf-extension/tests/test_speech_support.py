"""Every built-in speech model must run through a package, run natively, or say why it can't."""
import json
from pathlib import Path
import re
import sys
import unittest

REPO = Path(__file__).resolve().parents[3]
EXTENSION = REPO / 'src-tauri/resources/runtime-setup/hf-extension'
for path in (EXTENSION, REPO / 'distribution'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from speech_support import REASONS, SPEECH_CATEGORIES, speech_support  # noqa: E402

CATALOG = json.loads((REPO / 'distribution/hf-research/expanded-catalog.json').read_text(encoding='utf-8'))
APP_CATALOG = json.loads((REPO / 'src-tauri/resources/model-catalog.json').read_text(encoding='utf-8'))
PUBLISHED = json.loads((EXTENSION / 'publisher-models.json').read_text(encoding='utf-8'))
ENTRIES = {entry['id']: entry for entry in PUBLISHED['entries']}
SUPPORT = PUBLISHED['speechSupport']
MODELS = {model['id']: model for model in APP_CATALOG['models']}

# Text that would only make sense on one person's computer.
MACHINE_SPECIFIC = re.compile(r'RTX 40|4070|C:\\|/Users/|hertz|your PC|this PC', re.IGNORECASE)


class SpeechSupportTests(unittest.TestCase):
    def test_every_speech_model_the_app_shows_has_a_state(self):
        speech = {model['id'] for model in APP_CATALOG['models'] if model.get('category') in SPEECH_CATEGORIES}
        self.assertTrue(speech)
        self.assertEqual(set(SUPPORT), speech)

    def test_the_shipped_states_match_a_fresh_build(self):
        self.assertEqual(SUPPORT, speech_support(CATALOG, PUBLISHED['entries']))

    def test_states_are_known_and_unavailable_models_explain_why(self):
        for identity, value in SUPPORT.items():
            with self.subTest(identity):
                self.assertIn(value['state'], ('managed', 'native', 'unavailable'))
                self.assertIsInstance(value['use'], list)
                if value['state'] == 'unavailable':
                    self.assertGreater(len(value['reason']), 30)
                    self.assertTrue(value['reason'].endswith('.'))
                    self.assertNotRegex(value['reason'], MACHINE_SPECIFIC)
                else:
                    self.assertNotIn('reason', value)
                    self.assertTrue(value['use'])

    def test_alternatives_point_at_models_that_run(self):
        for identity, value in SUPPORT.items():
            for target in value['use']:
                with self.subTest(identity=identity, target=target):
                    if target in ENTRIES:
                        self.assertTrue(ENTRIES[target]['installable'])
                        self.assertTrue(ENTRIES[target].get('ttsRuntime'))
                    else:
                        self.assertTrue(MODELS[target].get('runtimeReady'))

    def test_managed_models_name_their_own_packages(self):
        for identity, value in SUPPORT.items():
            if value['state'] != 'managed':
                continue
            for target in value['use']:
                with self.subTest(identity=identity, target=target):
                    # A package claims its card by id, or by repository as the hub merges imported models.
                    entry = ENTRIES[target]
                    source = MODELS[identity]['sourceUrl'].lower() + '/'
                    self.assertTrue(identity in entry['nativeModelIds']
                                    or source.startswith('https://huggingface.co/' + entry['repository'].lower() + '/'))

    def test_qwen3_tts_runs_through_its_packages(self):
        expected = {
            'tts-qwen3-customvoice-0-6b': 'hf-d3cf04870c3408a4fb6329bd',
            'tts-qwen3-customvoice-1-7b': 'hf-78c1cf6773e25cc97a8388f6',
            'tts-qwen3-voicedesign-1-7b': 'hf-8f72bc9352cac8ce8ad0b61e',
            'voice-cloning-qwen3-base-0-6b': 'hf-e81473db39abcc026034486f',
            'voice-cloning-qwen3-base-1-7b': 'hf-9ac81337b9e3d16f3b88fe9c',
        }
        for identity, entry in expected.items():
            with self.subTest(identity):
                self.assertEqual(SUPPORT[identity], {'state': 'managed', 'use': [entry]})
        for identity, value in SUPPORT.items():
            root = MODELS[identity].get('variantOf')
            if root in expected:
                with self.subTest(identity):
                    self.assertEqual(value['state'], 'unavailable')
                    self.assertEqual(value['use'], [expected[root]])
                    self.assertIn('OpenCore already runs Qwen3 TTS', value['reason'])

    def test_dictation_engines_are_native(self):
        for identity in ('phonon-2', 'whisper-large-v3', 'whisper-large-v3-turbo'):
            self.assertEqual(SUPPORT[identity], {'state': 'native', 'use': [identity]})

    def test_a_new_speech_model_without_a_state_fails_the_build(self):
        catalog = {'models': CATALOG['models'] + [
            {'id': 'tts-brand-new', 'category': 'tts', 'sourceUrl': 'https://huggingface.co/example/brand-new'}]}
        with self.assertRaisesRegex(ValueError, 'tts-brand-new'):
            speech_support(catalog, PUBLISHED['entries'])

    def test_every_reason_names_a_speech_model(self):
        roots = {model['id'] for model in CATALOG['models'] if model.get('category') in SPEECH_CATEGORIES}
        self.assertEqual(set(REASONS) - roots, set())


if __name__ == '__main__':
    unittest.main()
