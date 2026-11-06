"""Pinned GUI editions retain their matching projectors and runtime boundaries."""
import copy
import json
from pathlib import Path, PurePosixPath
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from requested_catalog import append_requested
from model_modes import apply_operating_modes, snapshot_metadata


class GuiRequestedCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = append_requested([], ROOT / 'hf-research/requested-models.json', ROOT / 'hf-research/requested-models')

    def family(self, suffix):
        return [entry for entry in self.entries if entry['familyId'] == 'publisher-' + suffix]

    def test_complete_quantization_menus_keep_each_publisher_identity(self):
        for family, original, total, ggufs in [
            ('herculis-cua-gui-actioner-4b', 'Herculis-CUA-GUI-Actioner-4B', 15, 14),
            ('gliese-cua-tool-call-8b', 'Gliese-CUA-Tool-Call-8B', 19, 16),
        ]:
            with self.subTest(family=family):
                entries = self.family(family)
                self.assertEqual(len(entries), total)
                self.assertEqual(sum(entry['format'] == 'gguf' for entry in entries), ggufs)
                self.assertEqual(len({(entry['format'], entry['quantization']) for entry in entries}), total)
                self.assertTrue({'Q2_K', 'Q3_K_M', 'Q4_K_M', 'Q5_K_M', 'Q6_K', 'Q8_0'}.issubset(
                    {entry['quantization'] for entry in entries}))
                self.assertTrue(all(entry['libraryCategory'] == 'computer-use' for entry in entries))
                self.assertTrue(all(entry['category'] == 'text' for entry in entries))
                self.assertTrue(all(entry['originalRepository'] == 'prithivMLmods/' + original for entry in entries))

    def test_ggufs_include_their_own_pinned_vision_projector(self):
        for family in ('herculis-cua-gui-actioner-4b', 'gliese-cua-tool-call-8b'):
            for entry in self.family(family):
                if entry['format'] != 'gguf':
                    continue
                with self.subTest(id=entry['id']):
                    models = [pin for pin in entry['files'] if pin['role'] == 'model']
                    projectors = [pin for pin in entry['files'] if pin['role'] == 'companion']
                    self.assertEqual(len(models), 1)
                    self.assertEqual(len(projectors), 1)
                    self.assertNotIn('mmproj', models[0]['filename'])
                    self.assertIn('mmproj', projectors[0]['filename'])
                    self.assertEqual('Localization' in projectors[0]['filename'], 'Localization' in entry['quantization'])
                    for pin in entry['files']:
                        self.assertTrue(pin['pinned'])
                        self.assertEqual(pin['repository'], entry['repository'])
                        self.assertEqual(pin['revision'], entry['revision'])
                    self.assertEqual(entry['bytes'], sum(pin['bytes'] for pin in entry['files']))
                    self.assertEqual(entry['runtime']['adapter'], 'gguf')

    def test_gliese_bf16_editions_keep_their_templates_and_own_shards(self):
        entries = self.family('gliese-cua-tool-call-8b')
        for label, directory in [('BF16 Tool calling', ''), ('BF16 Localization', 'Localization-8B/')]:
            entry = next(row for row in entries if row['format'] == 'safetensors' and row['quantization'] == label)
            self.assertEqual(entry['revision'], '6f9d87647b09e8eb126cc925649fc1b52fa67ace')
            self.assertEqual(entry['runtime']['adapter'], 'transformers')
            self.assertIn(directory + 'chat_template.json', {pin['filename'] for pin in entry['files']})
            models = [pin for pin in entry['files'] if pin['role'] == 'model']
            self.assertEqual(len(models), 4)
            self.assertTrue(all(str(PurePosixPath(pin['filename']).parent) == (directory.rstrip('/') or '.') for pin in models))
        fp8 = next(row for row in entries if row['quantization'] == 'FP8 W8A8')
        self.assertIsNone(fp8['runtime']['adapter'])
        self.assertIn('compressed-tensors', fp8['runtime']['reason'])
        self.assertFalse(fp8['runtime']['inferenceVerified'])

    def test_text_modes_share_packages_and_use_published_context_limits(self):
        entries = copy.deepcopy(self.family('herculis-cua-gui-actioner-4b') + self.family('gliese-cua-tool-call-8b'))
        apply_operating_modes(entries, snapshot_metadata(ROOT / 'hf-research'))
        for entry in entries:
            with self.subTest(id=entry['id']):
                self.assertEqual([(mode['id'], mode['contextTokens']) for mode in entry['operatingModes']],
                                 [('native', 128000), ('echo', 32768)])
                self.assertEqual(all(mode['supported'] for mode in entry['operatingModes']),
                                 entry['runtime']['adapter'] is not None)
                self.assertFalse(entry['runtime']['inferenceVerified'])

    def test_twil_is_one_family_with_six_existing_editions(self):
        entries = self.family('twil-8b')
        self.assertEqual(len(entries), 6)
        self.assertEqual({(row['format'], row['quantization']) for row in entries}, {
            ('gguf', 'Q4_K_M Balanced'), ('gguf', 'Q4_K_M Verbose'),
            ('gguf', 'Q8_0 Verbose'), ('gguf', 'F16 Verbose'),
            ('safetensors', 'BF16 Balanced'), ('safetensors', 'BF16 Verbose')})
        self.assertTrue(all(row['repository'] == 'webAI-Official/TwIL-8B' and
                            row['revision'] == '58d49f0d9909e62f9c2fb09e99fc5c76f3f2d630' for row in entries))


if __name__ == '__main__':
    unittest.main()
