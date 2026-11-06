"""Alpie's real publisher packages keep their lineage and required components."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from requested_catalog import append_requested
from model_modes import apply_operating_modes, snapshot_metadata
from hf_download import validate_entry


class AlpieSeedCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = [entry for entry in append_requested(
            [], ROOT / 'hf-research/requested-models.json', ROOT / 'hf-research/requested-models'
        ) if entry['familyId'] == 'publisher-alpie-seed-9b']

    def test_only_the_exact_publisher_checkpoint_and_its_real_quants(self):
        self.assertEqual({(entry['format'], entry['quantization']) for entry in self.entries}, {
            ('gguf', 'Q2_K'), ('gguf', 'Q3_K_M'), ('gguf', 'Q4_K_M'), ('safetensors', 'BF16')})
        self.assertEqual(len(self.entries), 4)
        revisions = {
            '169Pi/Alpie_seed': '1cacbe1a206611a433d849e9a92a44d1db5bce1c',
            '169Pi/Alpie_Seed_GGUF': 'cc6b82d39c66ea85de09f6f8c797c10a7af86ff2',
        }
        for entry in self.entries:
            with self.subTest(id=entry['id']):
                self.assertEqual(entry['originalRepository'], '169Pi/Alpie_seed')
                self.assertEqual(entry['revision'], revisions[entry['repository']])
                self.assertEqual(entry['license'], 'apache-2.0')
                self.assertTrue(entry['installable'])
                self.assertFalse(entry['runtime']['inferenceVerified'])
                self.assertEqual(entry['bytes'], sum(pin['bytes'] for pin in entry['files']))
                self.assertTrue(all(pin['pinned'] and pin['repository'] == entry['repository']
                                    and pin['revision'] == entry['revision'] for pin in entry['files']))
                self.assertFalse(any(pin['filename'].endswith(('.mp4', '.pdf', '.py')) for pin in entry['files']))
                validate_entry(entry)

    def test_models_retain_the_projector_or_complete_original_support_files(self):
        models = {'Q2_K': ('alpie_seed-grpo-Q2_K.gguf', 3827273728),
                  'Q3_K_M': ('alpie_seed-text-q3_k_m.gguf', 4623517376),
                  'Q4_K_M': ('alpie_seed-text-q4_k_m.gguf', 5629109184)}
        for entry in self.entries:
            names = {pin['filename'] for pin in entry['files']}
            if entry['format'] == 'gguf':
                self.assertEqual(entry['runtime']['adapter'], 'gguf')
                self.assertEqual(entry['managedEngine'], 'flint-cpu-v1')
                self.assertEqual(names, {models[entry['quantization']][0], 'alpie_seed-vl-mmproj-f16.gguf'})
                weight = next(pin for pin in entry['files'] if pin['role'] == 'model')
                self.assertEqual(weight['bytes'], models[entry['quantization']][1])
                projector = next(pin for pin in entry['files'] if pin['role'] == 'companion')
                self.assertEqual(projector['bytes'], 921704384)
            else:
                self.assertEqual(entry['runtime']['adapter'], 'transformers')
                self.assertTrue({'model.safetensors', 'config.json', 'tokenizer.json', 'tokenizer_config.json',
                                 'chat_template.jinja', 'generation_config.json', 'preprocessor_config.json',
                                 'processor_config.json', 'video_preprocessor_config.json'} <= names)

    def test_native_and_echo_modes_share_the_same_download(self):
        entries = copy.deepcopy(self.entries)
        apply_operating_modes(entries, snapshot_metadata(ROOT / 'hf-research'))
        for entry in entries:
            self.assertTrue(entry['supportsTextGeneration'])
            self.assertEqual([(mode['id'], mode['contextTokens']) for mode in entry['operatingModes']],
                             [('native', 262144), ('echo', 32768)])
            self.assertTrue(all(mode['supported'] for mode in entry['operatingModes']))


if __name__ == '__main__':
    unittest.main()
