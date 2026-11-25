"""Keep the requested Baphomet checkpoint, all weight shards and vision components together."""
import importlib.util
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[3]
for folder in (REPO / 'distribution', REPO / 'src-tauri/resources/runtime-setup/hf-extension'):
    sys.path.insert(0, str(folder))
from hf_download import validate_entry


class BaphometCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('publisher_builder_baphomet', REPO / 'distribution/build-publisher-catalog.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        cls.entries = [e for e in builder.build()['entries'] if e.get('familyId') == 'publisher-baphomet-1-1-4b']

    def test_publisher_package_keeps_both_shards_and_multimodal_configuration(self):
        originals = [e for e in self.entries if e['repository'] == 'CloudGoat/Baphomet-1.1-4B']
        self.assertEqual(len(originals), 1)
        entry = originals[0]
        self.assertEqual(entry['quantization'], 'BF16')
        self.assertEqual(entry['runtime']['adapter'], 'transformers')
        self.assertTrue({'model.safetensors-00001-of-00002.safetensors',
                         'model.safetensors-00002-of-00002.safetensors',
                         'model.safetensors.index.json', 'config.json', 'chat_template.jinja',
                         'tokenizer.json', 'preprocessor_config.json', 'video_preprocessor_config.json'}
                        <= {pin['filename'] for pin in entry['files']})

    def test_each_gguf_has_its_projector_and_the_current_managed_engine(self):
        gguf = [e for e in self.entries if e['format'] == 'gguf']
        self.assertEqual({e['quantization'] for e in gguf},
                         {'IQ4_XS', 'Q2_K', 'Q3_K_S', 'Q3_K_M', 'Q3_K_L', 'Q4_K_S',
                          'Q4_K_M', 'Q5_K_S', 'Q5_K_M', 'Q6_K', 'Q8_0', 'F16'})
        for entry in gguf:
            self.assertEqual(entry['repository'], 'mradermacher/Baphomet-1.1-4B-GGUF')
            self.assertEqual(entry['managedEngine'], 'lfm3-cpu-v1')
            self.assertEqual(entry['runtime']['adapter'], 'gguf')
            self.assertEqual(sum(pin['role'] == 'model' for pin in entry['files']), 1)
            self.assertEqual(sum(pin['role'] == 'companion' for pin in entry['files']), 1)

    def test_all_editions_preserve_the_exact_identity_and_wait_for_real_generation(self):
        self.assertEqual(len(self.entries), 13)
        for entry in self.entries:
            validate_entry(entry)
            self.assertEqual(entry['originalRepository'], 'CloudGoat/Baphomet-1.1-4B')
            self.assertEqual(entry['supportedContextTokens'], 262144)
            self.assertEqual(set(entry['inputModalities']), {'image', 'text'})
            self.assertEqual(entry['license'], 'apache-2.0')
            self.assertFalse(entry['runtime']['inferenceVerified'])
            self.assertFalse(entry.get('runtimeReady'))
            self.assertFalse(any('imatrix' in pin['filename'] for pin in entry['files']))


if __name__ == '__main__':
    unittest.main()
