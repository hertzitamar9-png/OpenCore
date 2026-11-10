"""Pinned additions preserve complete checkpoints and fixed-slot adapter boundaries."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from requested_catalog import append_requested
from model_modes import apply_operating_modes, snapshot_metadata
from hf_download import validate_entry


class November10CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = append_requested([], ROOT / 'hf-research/requested-models.json', ROOT / 'hf-research/requested-models')
        apply_operating_modes(cls.entries, snapshot_metadata(ROOT / 'hf-research'))

    def family(self, name):
        return [row for row in self.entries if row['familyId'] == 'publisher-' + name]

    def test_lfm_distill_has_all_real_quants_with_shared_native_and_echo_packages(self):
        entries = self.family('lfm3-coder-claude-distill')
        self.assertEqual(len(entries), 13)
        self.assertEqual(sum(row['format'] == 'gguf' for row in entries), 12)
        self.assertEqual(len({(row['format'], row['quantization']) for row in entries}), 13)
        for row in entries:
            validate_entry(row)
            self.assertEqual([(mode['id'], mode['contextTokens']) for mode in row['operatingModes']],
                             [('native', 262144), ('echo', 32768)])
            self.assertTrue(all(pin['pinned'] for pin in row['files']))

    def test_gui_swift_is_never_sent_to_an_ordinary_text_lora_loader(self):
        for name, base, config in [('gui-swift-4b', 'mPLUG/GUI-Owl-1.5-4B-Instruct', 'language_adapter_config.json'),
                                  ('gui-swift-mobile-2b', 'mPLUG/GUI-Owl-1.5-2B-Instruct', 'adapter_config.json')]:
            entries = self.family(name)
            self.assertEqual(len(entries), 1)
            row = entries[0]
            validate_entry(row)
            self.assertEqual(row['baseRepository'], base)
            self.assertEqual(row['packageKind'], 'adapter')
            self.assertIsNone(row['runtime']['adapter'])
            self.assertFalse(row['supportsTextGeneration'])
            self.assertEqual(row['operatingModes'], [])
            self.assertIn(config, {pin['filename'] for pin in row['files']})

    def test_complete_gui_checkpoints_keep_processor_and_publisher_tool_files(self):
        mobile = self.family('mobilegym-4b')[0]
        self.assertEqual(sum(pin['role'] == 'model' for pin in mobile['files']), 2)
        self.assertIn('video_preprocessor_config.json', {pin['filename'] for pin in mobile['files']})
        gated = self.family('kormo-gui-4b')[0]
        self.assertTrue(gated['gated'])
        self.assertTrue({'system_prompt.md', 'tools.json', 'chat_template.jinja'} <= {pin['filename'] for pin in gated['files']})
        self.assertEqual(gated['supportedContextTokens'], 131072)
        self.assertEqual(gated['operatingModes'][0]['contextTokens'], 32768,
                         'A serving recommendation must not invent an inaccessible model configuration')
        for row in (mobile, gated):
            validate_entry(row)
            self.assertFalse(row['runtime']['inferenceVerified'])


if __name__ == '__main__':
    unittest.main()
