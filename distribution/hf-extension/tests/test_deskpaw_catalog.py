"""The custom pointer checkpoint must not silently become a stock Qwen chat model."""
import importlib.util
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[3]
for folder in (REPO / 'distribution', REPO / 'src-tauri/resources/runtime-setup/hf-extension'):
    sys.path.insert(0, str(folder))
from hf_download import validate_entry
from model_modes import produces_text


class DeskpawCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('publisher_builder_deskpaw', REPO / 'distribution/build-publisher-catalog.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        cls.entries = [e for e in builder.build()['entries'] if e.get('familyId') == 'publisher-deskpaw-cua-2b']

    def test_complete_full_checkpoint_and_processor_are_pinned(self):
        self.assertEqual(len(self.entries), 1)
        entry = self.entries[0]
        validate_entry(entry)
        self.assertEqual(entry['repository'], 'llstar/deskpaw-cua-2b')
        self.assertEqual(entry['quantization'], 'BF16')
        files = {pin['filename'] for pin in entry['files']}
        self.assertTrue({f'model-{n:05d}-of-00006.safetensors' for n in range(1, 7)} <= files)
        self.assertTrue({'model.safetensors.index.json', 'config.json', 'processor_config.json',
                         'tokenizer.json', 'chat_template.jinja'} <= files)
        self.assertNotIn('modeling_qwen3vl.py', files)
        self.assertNotIn('packageKind', entry)

    def test_pointer_head_is_not_admitted_as_a_plain_chat_runtime(self):
        self.assertEqual(len(self.entries), 1)
        entry = self.entries[0]
        self.assertEqual(entry['category'], 'computer-use')
        self.assertEqual(entry['libraryCategory'], 'computer-use')
        self.assertEqual(set(entry['inputModalities']), {'image', 'text'})
        self.assertEqual(entry['outputModalities'], ['coordinates'])
        self.assertFalse(entry['supportsTextChat'])
        self.assertFalse(entry['supportsTextGeneration'])
        self.assertFalse(entry['outputsText'])
        self.assertEqual(entry['operatingModes'], [])
        self.assertEqual(entry['runtime']['adapter'], 'computer-use')
        self.assertEqual(entry['computerRuntime'], 'deskpaw-v1')
        self.assertIn('pointer', entry['runtime']['reason'])
        self.assertFalse(entry['runtime']['inferenceVerified'])

    def test_explicit_coordinate_output_takes_precedence_over_the_hub_pipeline_tag(self):
        self.assertFalse(produces_text({'outputModalities': ['coordinates']}, {'pipeline_tag': 'image-text-to-text'}))
        self.assertTrue(produces_text({'outputModalities': ['text']}, {'pipeline_tag': 'feature-extraction'}))
        self.assertTrue(produces_text({}, {'pipeline_tag': 'automatic-speech-recognition'}))


if __name__ == '__main__':
    unittest.main()
