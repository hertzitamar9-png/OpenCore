"""Offline regressions for the four requested model identities and their packages."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from requested_catalog import append_requested
from bundled_catalog import coverage
from hf_download import validate_entry


class RequestedCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = ROOT / 'hf-research/requested-models.json'
        cls.snapshots = ROOT / 'hf-research/requested-models'
        cls.rows = json.loads(cls.manifest.read_text(encoding='utf-8'))['models']
        cls.entries = append_requested([], cls.manifest, cls.snapshots)

    def family(self, suffix):
        return [entry for entry in self.entries if entry['familyId'] == 'publisher-' + suffix]

    def test_exactly_the_four_requested_models_are_separate_cards(self):
        self.assertEqual({row['repository'] for row in self.rows}, {
            'Hobbylm/hobbylm-1B-computer-use', 'Mininglamp-2718/Mano-CUA-2.0-4B',
            'Teriminal-RSI/Terminal-RSI-27B', 'Gen-Verse/DiscoveryRSI-27B'})
        self.assertEqual(len({entry['familyId'] for entry in self.entries}), 4)
        self.assertEqual(len({entry['id'] for entry in self.entries}), len(self.entries))
        self.assertTrue(all(entry['builtIn'] and entry['installable'] for entry in self.entries))
        self.assertTrue(all(entry['originalRepository'] != 'Qwen/Qwen3.8-27B' for entry in self.entries))

    def test_full_models_have_real_preset_quants_and_pinned_projectors(self):
        for suffix, count in [('mano-cua-2-0-4b', 12), ('terminal-rsi-27b', 26), ('discoveryrsi-27b', 27)]:
            with self.subTest(family=suffix):
                entries = [entry for entry in self.family(suffix) if entry['format'] == 'gguf']
                self.assertEqual(len(entries), count)
                self.assertGreaterEqual(len({entry['quantization'] for entry in entries}), 5)
                self.assertIn('Q4_K_M', {entry['quantization'] for entry in entries})
        self.assertTrue(all(any(pin['role'] == 'companion' for pin in entry['files'])
                            for entry in self.family('mano-cua-2-0-4b') if entry['format'] == 'gguf'))
        for suffix in ('terminal-rsi-27b', 'discoveryrsi-27b'):
            entry = next(entry for entry in self.family(suffix) if entry['format'] == 'gguf' and entry['quantization'] == 'BF16')
            self.assertEqual(sum(pin['role'] == 'model' for pin in entry['files']), 2)
            self.assertEqual(sum(pin['role'] == 'companion' for pin in entry['files']), 1)

    def test_adapter_exception_is_retained_with_its_base_and_platform_requirements(self):
        entries = self.family('hobbylm-1b-computer-use')
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry['packageKind'], 'adapter')
        self.assertEqual(entry['baseRepository'], 'harims95/hobbylm-1B-instruct')
        self.assertIsNone(entry['runtime']['adapter'])
        self.assertIn('macOS', entry['runtime']['reason'])
        self.assertEqual({pin['filename'] for pin in entry['files']}, {'adapter_config.json', 'adapter_model.safetensors'})

    def test_no_package_is_marked_ready_and_specialized_formats_keep_actual_requirements(self):
        for entry in self.entries:
            validate_entry(entry)
            self.assertFalse(entry['runtime'].get('inferenceVerified'))
            self.assertFalse(entry.get('runtimeReady'))
            self.assertFalse(any(pin['filename'].endswith(('.py', '.sh', '.swift', '.exe')) for pin in entry['files']))
        for entry in self.entries:
            if entry['quantization'] in ('MLX 8-bit', 'MLX NVFP4', 'EXL3 4.0 bpw', 'Q2_0'):
                self.assertIsNone(entry['runtime']['adapter'])
        report = coverage({'models': []}, self.entries)
        self.assertEqual(report['families'], 4)
        self.assertEqual(report['withFive'], 3)
        self.assertFalse(report['allModelsMeetMinimum'])

    def test_specialized_packages_keep_their_quantization_and_processor_configurations(self):
        exl = next(entry for entry in self.entries if entry['quantization'] == 'EXL3 4.0 bpw')
        self.assertIn('quantization_config.json', {pin['filename'] for pin in exl['files']})
        for entry in self.entries:
            if entry['quantization'] in ('MLX 8-bit', 'MLX NVFP4', 'EXL3 4.0 bpw'):
                self.assertIn('video_preprocessor_config.json', {pin['filename'] for pin in entry['files']})

    def test_modified_lineage_or_snapshot_identity_cannot_enter_the_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / 'manifest.json'
            rows = copy.deepcopy(self.rows)
            rows[2]['repository'] = 'Qwen/Qwen3.8-27B'
            # The requested original must be present under its own immutable identity.
            manifest.write_text(json.dumps({'models': [rows[2]]}), encoding='utf-8')
            with self.assertRaises((ValueError, FileNotFoundError)):
                append_requested([], manifest, self.snapshots)


if __name__ == '__main__':
    unittest.main()
