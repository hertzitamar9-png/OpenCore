"""Runtime variants: one build per PC by default, every other build on request."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import hardware_profile as hp
import hf_sdcpp
from hf_tts import variant_lock

NVIDIA = hp.build({'gpus': [{'vendor': 'nvidia', 'name': 'RTX 4070', 'vramBytes': 12 * 1024 ** 3}],
                   'cudaDriver': 12080, 'computeCapability': [8, 9], 'vulkan': True,
                   'nvidiaVramBytes': 12 * 1024 ** 3})
AMD = hp.build({'gpus': [{'vendor': 'amd', 'name': 'Radeon RX 7800', 'vramBytes': 16 * 1024 ** 3}], 'vulkan': True})
NONE = hp.build({'gpus': []})
OLD_NVIDIA = hp.build({'gpus': [{'vendor': 'nvidia', 'name': 'GTX 1060'}], 'cudaDriver': 12020,
                       'computeCapability': [6, 1], 'vulkan': True})
EVERYTHING = ('cuda', 'vulkan', 'rocm', 'cpu')


class ProfileTests(unittest.TestCase):
    def test_best_variant_per_pc(self):
        self.assertEqual(NVIDIA['best'], 'cuda')
        self.assertEqual(AMD['best'], 'vulkan')
        self.assertEqual(NONE['best'], 'cpu')
        # Too old for the CUDA builds: Vulkan is the default, never CUDA.
        self.assertEqual(OLD_NVIDIA['best'], 'vulkan')
        self.assertTrue(AMD['amd'])

    def test_default_picks_exactly_one_variant(self):
        self.assertEqual(hp.default_variant(EVERYTHING, NVIDIA), 'cuda')
        self.assertEqual(hp.default_variant(EVERYTHING, AMD), 'vulkan')
        self.assertEqual(hp.default_variant(EVERYTHING, NONE), 'cpu')
        self.assertEqual(hp.default_variant(('cuda', 'cpu'), AMD), 'cpu')
        with self.assertRaises(ValueError):
            hp.default_variant((), NVIDIA)

    def test_alternatives_explain_every_variant(self):
        rows = {row['variant']: row for row in hp.alternatives(EVERYTHING, AMD, installed=['vulkan'])}
        self.assertTrue(rows['vulkan']['recommended'] and rows['vulkan']['installed'])
        self.assertFalse(rows['cuda']['compatible'])
        self.assertIn('NVIDIA', rows['cuda']['reason'])
        self.assertTrue(rows['rocm']['compatible'])
        self.assertTrue(rows['cpu']['compatible'] and not rows['cpu']['installed'])

    def test_fallback_is_only_offered(self):
        self.assertEqual(hp.fallback_offer('cuda', ('cuda', 'vulkan', 'cpu'), NVIDIA)['variant'], 'vulkan')
        self.assertEqual(hp.fallback_offer('vulkan', ('cuda', 'vulkan', 'cpu'), AMD)['variant'], 'cpu')
        self.assertIsNone(hp.fallback_offer('cpu', ('cuda', 'vulkan', 'cpu'), NONE))

    def test_public_profile_has_no_driver_details(self):
        value = hp.public(NVIDIA)
        self.assertEqual(value['best'], 'cuda')
        self.assertNotIn('cudaDriver', value)
        self.assertEqual(value['gpus'][0]['name'], 'RTX 4070')

    def test_choices_round_trip_and_fall_back_to_default(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(hp.chosen_variant(folder, 'image-sdcpp-v1', hf_sdcpp.VARIANTS, NVIDIA), 'cuda')
            hp.save_choice(folder, 'image-sdcpp-v1', 'vulkan')
            self.assertEqual(hp.load_choices(folder), {'image-sdcpp-v1': 'vulkan'})
            self.assertEqual(hp.chosen_variant(folder, 'image-sdcpp-v1', hf_sdcpp.VARIANTS, NVIDIA), 'vulkan')
            # A saved choice the runtime does not offer is ignored.
            self.assertEqual(hp.chosen_variant(folder, 'image-sdcpp-v1', ('cuda', 'cpu'), NVIDIA), 'cuda')
            hp.save_choice(folder, 'image-sdcpp-v1', None)
            self.assertEqual(hp.load_choices(folder), {})
            with self.assertRaises(ValueError):
                hp.save_choice(folder, 'image-sdcpp-v1', 'metal')
            (Path(folder) / hp.CHOICES_FILE).write_text('not json')
            self.assertEqual(hp.load_choices(folder), {})


class ImageEngineVariantTests(unittest.TestCase):
    def test_each_variant_has_its_own_folder_and_pins(self):
        folders = {hf_sdcpp.ENGINE_VARIANTS[v]['folder'] for v in hf_sdcpp.VARIANTS}
        self.assertEqual(len(folders), len(hf_sdcpp.VARIANTS))
        # Existing installs keep working: Vulkan stays in the original folder with the original pin.
        self.assertEqual(hf_sdcpp.ENGINE_VARIANTS['vulkan']['folder'], 'iris-sdcpp-v1')
        self.assertEqual(hf_sdcpp.archives('vulkan'), [(hf_sdcpp.ENGINE_URL, hf_sdcpp.ENGINE_BYTES, hf_sdcpp.ENGINE_SHA)])
        self.assertEqual(len(hf_sdcpp.archives('cuda')), 2)
        for variant in hf_sdcpp.VARIANTS:
            for url, size, sha in hf_sdcpp.archives(variant):
                self.assertTrue(url.startswith('https://github.com/leejet/stable-diffusion.cpp/releases/download/'))
                self.assertGreater(size, 0)
                self.assertEqual(len(sha), 64)

    def test_selected_variant_follows_hardware_and_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(hp, 'profile', return_value=NVIDIA):
                self.assertEqual(hf_sdcpp.selected_variant(folder), 'cuda')
            with patch.object(hp, 'profile', return_value=AMD):
                self.assertEqual(hf_sdcpp.selected_variant(folder), 'vulkan')
            with patch.object(hp, 'profile', return_value=NONE):
                self.assertEqual(hf_sdcpp.selected_variant(folder), 'cpu')
                hp.save_choice(folder, hf_sdcpp.RUNTIME_ID, 'rocm')
                self.assertEqual(hf_sdcpp.selected_variant(folder), 'rocm')

    def test_installed_variants_need_a_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(hf_sdcpp.installed_variants(folder), [])
            engine = hf_sdcpp.engine_folder(folder, 'cpu')
            engine.mkdir(parents=True)
            self.assertEqual(hf_sdcpp.installed_variants(folder), [])
            (engine / 'verified-files.json').write_text('{}')
            self.assertEqual(hf_sdcpp.installed_variants(folder), ['cpu'])

    def test_unknown_variant_is_refused_before_download(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                hf_sdcpp.ensure_engine(folder, threading.Event(), print, 'metal')

    def test_low_disk_space_is_refused_before_download(self):
        with tempfile.TemporaryDirectory() as folder:
            usage = type('Usage', (), {'free': 1024})()
            with patch.object(hf_sdcpp.shutil, 'disk_usage', return_value=usage), \
                    patch.object(hf_sdcpp.urllib.request, 'urlopen') as download:
                with self.assertRaisesRegex(RuntimeError, 'free disk space'):
                    hf_sdcpp.ensure_engine(folder, threading.Event(), print, 'cuda')
            download.assert_not_called()


class SpeechLockTests(unittest.TestCase):
    LOCK = ('# Torch wheels come from https://download.pytorch.org/whl/cu128.\n'
            'numpy==2.1.0 --hash=sha256:aa\ntorch==2.9.1+cu128 --hash=sha256:bb\n'
            'torchaudio==2.9.1+cu128 --hash=sha256:cc\n')

    def test_cuda_lock_is_unchanged(self):
        self.assertEqual(variant_lock(self.LOCK, 'cuda'), self.LOCK)

    def test_cpu_lock_swaps_only_torch(self):
        text = variant_lock(self.LOCK, 'cpu')
        self.assertIn('torch==2.9.1+cpu --hash=sha256:', text)
        self.assertIn('torchaudio==2.9.1+cpu --hash=sha256:', text)
        self.assertIn('numpy==2.1.0 --hash=sha256:aa', text)
        self.assertNotIn('cu128 --hash', text)
        self.assertIn('whl/cpu', text)


if __name__ == '__main__':
    unittest.main()
