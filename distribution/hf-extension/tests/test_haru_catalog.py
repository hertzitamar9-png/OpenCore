"""Haru's integrated audio checkpoint needs its duplex engine rather than a chat or TTS adapter."""
import importlib.util
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[3]
for folder in (REPO / 'distribution', REPO / 'src-tauri/resources/runtime-setup/hf-extension'):
    sys.path.insert(0, str(folder))
from hf_download import validate_entry


class HaruCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('publisher_builder_haru', REPO / 'distribution/build-publisher-catalog.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        cls.entries = [e for e in builder.build()['entries'] if e.get('familyId') == 'publisher-haru-4b']

    def test_one_pinned_bf16_package_contains_the_integrated_codec_weights(self):
        self.assertEqual(len(self.entries), 1)
        entry = self.entries[0]
        validate_entry(entry)
        self.assertEqual(entry['repository'], 'higurashi-ai/Haru-4B')
        self.assertEqual(entry['originalRepository'], 'higurashi-ai/Haru-4B')
        self.assertEqual(entry['revision'], '8d6e83bcd3701a76fc27a764965665da1df7da78')
        self.assertEqual((entry['format'], entry['quantization']), ('safetensors', 'BF16'))
        self.assertEqual({p['filename'] for p in entry['files']},
                         {'model.safetensors', 'config.json', 'tokenizer.json', 'tokenizer_config.json'})
        self.assertEqual(sum(p['role'] == 'model' for p in entry['files']), 1)
        self.assertEqual(entry['bytes'], 10_560_106_066)

    def test_audio_capabilities_do_not_advertise_an_ordinary_chat_or_tts_runtime(self):
        self.assertEqual(len(self.entries), 1)
        entry = self.entries[0]
        self.assertEqual(entry['category'], 'omni')
        self.assertEqual(set(entry['inputModalities']), {'audio', 'text'})
        self.assertEqual(set(entry['outputModalities']), {'audio', 'text'})
        self.assertTrue(entry['outputsText'])
        self.assertFalse(entry['supportsTextChat'])
        self.assertFalse(entry['supportsTextGeneration'])
        self.assertEqual(entry['operatingModes'], [])
        self.assertEqual(entry['runtime']['adapter'], 'haru-duplex')
        self.assertNotIn('ttsRuntime', entry)
        self.assertNotIn('speechRuntime', entry)
        self.assertIn('duplex', entry['runtime']['reason'])
        self.assertFalse(entry['runtime']['inferenceVerified'])
        self.assertFalse(entry.get('runtimeReady'))


if __name__ == '__main__':
    unittest.main()
