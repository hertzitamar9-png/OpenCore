"""Budget3D keeps its exact publisher identity and does not invent precision editions."""
import importlib.util
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[3]
for folder in (REPO / 'distribution', REPO / 'src-tauri/resources/runtime-setup/hf-extension'):
    sys.path.insert(0, str(folder))
from hf_download import validate_entry


class Budget3DCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('publisher_builder_budget3d', REPO / 'distribution/build-publisher-catalog.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        cls.all_entries = builder.build()['entries']
        cls.entries = [e for e in cls.all_entries if e.get('familyId') == 'publisher-budget3d-idol']

    def test_the_requested_checkpoint_is_separate_from_the_original_idol(self):
        self.assertEqual(len(self.entries), 1)
        entry = self.entries[0]
        validate_entry(entry)
        self.assertEqual(entry['repository'], 'BudgetAny/Budget3D-Idol')
        self.assertEqual(entry['originalRepository'], 'BudgetAny/Budget3D-Idol')
        self.assertEqual(entry['revision'], '2dd6d83c57e7853234dbbb24c54b78edc842d074')
        self.assertEqual(entry['familyLabel'], 'Budget3D-Idol')
        self.assertEqual(entry['category'], '3d')
        self.assertTrue(any(e.get('familyId') == 'publisher-idol' for e in self.all_entries))

    def test_one_weight_package_does_not_duplicate_budgets_or_download_demo_videos(self):
        self.assertEqual(len(self.entries), 1)
        entry = self.entries[0]
        self.assertEqual(entry['quantization'], 'Publisher checkpoint')
        self.assertEqual(entry['format'], '3d-pipeline')
        self.assertEqual({p['filename'] for p in entry['files']},
                         {'budget3d_idol.safetensors', 'config.json', 'README.md'})
        self.assertEqual(entry['bytes'], 1_512_430_259)
        self.assertEqual(entry['license'], 'apache-2.0')

    def test_shared_pipeline_requirements_do_not_claim_verified_generation(self):
        self.assertEqual(len(self.entries), 1)
        entry = self.entries[0]
        self.assertEqual(entry['runtime']['adapter'],'budget3d-idol')
        self.assertEqual(entry['generationRuntime'],'budget3d-idol-v1')
        self.assertTrue(all(mode['supported'] for mode in entry['modeAvailability']))
        self.assertEqual(entry['modeAvailability'][1]['scope'],'tool')
        self.assertFalse(entry['runtime']['inferenceVerified'])
        self.assertFalse(entry.get('runtimeReady'))
        self.assertFalse(entry['supportsTextGeneration'])
        self.assertEqual(entry['operatingModes'], [])
        self.assertNotIn('memoryMode', entry)
        self.assertNotIn('packageKind', entry)
        for name in ('Budget3D', 'IDOL', 'DINOv3', 'FLUX.2'):
            self.assertIn(name, entry['runtime']['reason'])


if __name__ == '__main__': unittest.main()
