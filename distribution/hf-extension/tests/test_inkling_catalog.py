"""Inkling 2 Mini package and runtime selection regressions without loading weights."""

import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hf_bridge import BridgeState
from hf_download import validate_entry
from hf_runtime import _inkling_cpu_context, runtime_plan, visual_text_probe
from managed_engines import INKLING_GGUF, engine_for


ROOT = Path(__file__).resolve().parents[3]
RESOURCES = ROOT / 'src-tauri/resources'
CATALOG = RESOURCES / 'runtime-setup/hf-extension/publisher-models.json'
FAMILY = 'publisher-inkling-2-mini'
REPOSITORY = 'thinkingmachines/Inkling-2-Mini'


class InklingCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog = json.loads(CATALOG.read_bytes())
        cls.entries = [row for row in catalog['entries'] if row['familyId'] == FAMILY]
        cls.gguf = [row for row in cls.entries if row['format'] == 'gguf']

    def test_full_checkpoint_inventory(self):
        self.assertEqual(len(self.gguf), 18)
        self.assertEqual(
            {row['quantization'] for row in self.gguf if row['repository'].startswith('unsloth/')},
            {'UD-IQ1_S', 'UD-IQ1_M', 'UD-IQ2_XXS', 'UD-Q2_K_XL', 'UD-IQ3_XXS',
             'UD-Q3_K_XL', 'UD-IQ4_XS', 'UD-Q4_K_M', 'UD-Q4_K_XL', 'UD-Q8_K_XL'},
        )
        self.assertEqual(len({row['id'] for row in self.entries}), 26)
        for row in self.entries:
            validate_entry(row)
            self.assertEqual(row['originalRepository'], REPOSITORY)
            self.assertFalse(row['runtime']['inferenceVerified'])
        external = [row for row in self.entries if row['format'] != 'gguf']
        self.assertEqual(sum(row['format'] == 'mlx' for row in external), 7)
        self.assertTrue(all(not row['runtime']['adapter'] for row in external))
        original = next(row for row in external if row['repository'] == REPOSITORY)
        self.assertIn('mtp.safetensors', {file['filename'] for file in original['files']})

    def test_both_modes_launch_each_gguf_with_the_matching_engine_and_projector(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = str(root / 'models/hub/fixture')
            for row in self.gguf:
                with self.subTest(quant=row['quantization']):
                    engine, revision = INKLING_GGUF[row['repository'].lower()]
                    self.assertEqual(row['revision'], revision)
                    self.assertEqual(row['inputModalities'], ['image', 'text'])
                    self.assertEqual(row['outputModalities'], ['text'])
                    self.assertEqual(row['publishedContextTokens'], 1048576)
                    self.assertTrue(all(mode['supported'] for mode in row['modeAvailability']))
                    self.assertEqual(visual_text_probe(row)[0], 'image')
                    projectors = [file['filename'] for file in row['files'] if file['role'] == 'companion']
                    self.assertEqual(len(projectors), 1)
                    weights = [file['filename'] for file in row['files'] if file['role'] == 'model']
                    self.assertTrue(all(not any(x in name.lower() for x in ('dspark', 'imatrix', 'mmproj')) for name in weights))
                    for mode in ('native', 'echo'):
                        plan = runtime_plan({**row, 'operatingMode': mode, 'localDirectory': local}, root, RESOURCES)
                        command = plan['command']
                        self.assertEqual(plan['context'], 32768)
                        self.assertEqual(plan['publisherEngine'], engine)
                        self.assertEqual(plan['runtimeBackend'], 'cpu')
                        self.assertEqual(Path(command[command.index('--model') + 1]).name, Path(weights[0]).name)
                        self.assertEqual(Path(command[command.index('--mmproj') + 1]).name, Path(projectors[0]).name)
            row = self.gguf[0]
            with self.assertRaisesRegex(ValueError, 'reviewed Inkling'):
                runtime_plan({**row, 'revision': '0' * 40, 'localDirectory': local}, root, RESOURCES)

    def test_reinspection_preserves_the_two_layouts_and_drops_calibration_files(self):
        for repo_key in INKLING_GGUF:
            with self.subTest(repository=repo_key):
                rows = [row for row in self.gguf if row['repository'].lower() == repo_key]
                repo = rows[0]['repository']
                file = ROOT / 'distribution/hf-research/requested-models' / (repo.replace('/', '--') + '.api.json')
                metadata = json.loads(file.read_bytes())
                bridge = SimpleNamespace(client=SimpleNamespace(metadata=lambda _: metadata),
                                         lock=threading.Lock(), entries={row['id']: row for row in self.entries},
                                         plans={})
                plan = BridgeState.plan(bridge, repo)
                self.assertEqual(plan['quantizationCount'], len(rows))
                self.assertEqual(plan['variants'], rows)
                self.assertTrue(all(engine_for(row) for row in plan['variants']))

    def test_cpu_memory_limit_keeps_room_for_generation_or_returns_a_ram_error(self):
        gib = 1024 ** 3
        self.assertEqual(_inkling_cpu_context(8 * gib, 32768, 64 * gib), 32768)
        self.assertLess(_inkling_cpu_context(8 * gib, 32768, 14 * gib), 32768)
        with self.assertRaisesRegex(ValueError, 'more free RAM'):
            _inkling_cpu_context(52 * gib, 32768, 32 * gib)

    def test_unsloth_packages_use_the_pinned_optional_gpu_backends(self):
        row = next(row for row in self.gguf if row['repository'].startswith('unsloth/'))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = {**row, 'localDirectory': str(root / 'models/hub/fixture')}
            for backend, fake in [('cuda', 'cuda_driver_version'), ('vulkan', 'vulkan_gpu_available')]:
                with patch('managed_engines.' + fake, return_value=13030 if backend == 'cuda' else True):
                    plan = runtime_plan(entry, root, RESOURCES, backend_override=backend)
                self.assertEqual(plan['runtimeBackend'], backend)
                self.assertTrue(plan['publisherEngine'].startswith('inkling-unsloth-' + backend))


if __name__ == '__main__':
    unittest.main()
