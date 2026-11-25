"""Admission checks for complete Edge0 bundles, Raon editions and existing ZONOS3 speech."""
import importlib.util
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[3]
DIST = REPO / 'distribution'
EXTENSION = REPO / 'src-tauri/resources/runtime-setup/hf-extension'
for folder in (DIST, EXTENSION):
    sys.path.insert(0, str(folder))
from hf_download import validate_entry


class November12CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('publisher_builder_nov12', DIST / 'build-publisher-catalog.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        cls.entries = builder.build()['entries']

    def family(self, name):
        return [entry for entry in self.entries if entry['familyId'] == 'publisher-' + name]

    def test_edge0_downloads_the_expert_pack_instead_of_only_the_skeleton(self):
        required = {'model.json', 'experts.bin', 'labels.bin', 'dense.bin', 'index.txt',
                    'native_experts.txt', 'latent_dense_q8.bin', 'model-skeleton.gguf',
                    'ple-fp8.gguf', 'profile.bin', 'mtp/experts.bin', 'mtp/dense.bin',
                    'mtp/dense.txt', 'mtp/draft_vocab.bin', 'tokenizer/tokenizer.json',
                    'tokenizer/vocab.json', 'tokenizer/merges.txt', 'tokenizer/chat_template.jinja'}
        for name in ('edge0-145b', 'edge0-120b'):
            with self.subTest(family=name):
                entries = self.family(name)
                self.assertEqual(len(entries), 2)
                self.assertEqual({entry['quantization'] for entry in entries}, {'Q3_K', 'NVFP4'})
                for entry in entries:
                    validate_entry(entry)
                    files = {pin['filename'] for pin in entry['files']}
                    self.assertTrue(required <= files)
                    self.assertEqual(sum(name.startswith('scales/') for name in files),
                                     48 if entry['quantization'] == 'Q3_K' else 0)
                    self.assertGreater(entry['bytes'], 80_000_000_000)
                    self.assertEqual(entry['runtime']['adapter'], 'edge0')
                    self.assertEqual(entry['managedEngine'], 'edge0-cuda13-v1')
                    self.assertIn('Strata', entry['runtime']['reason'])
                    self.assertFalse(entry['runtime']['inferenceVerified'])

    def test_raon_keeps_full_mtp_models_separate_from_plain_quantizations(self):
        entries = self.family('raon-1-nano')
        gguf = [entry for entry in entries if entry['format'] == 'gguf']
        self.assertEqual(len(gguf), 8)
        self.assertEqual(len({entry['quantization'] for entry in gguf}), 8)
        for entry in entries:
            validate_entry(entry)
            self.assertEqual(entry['supportedContextTokens'], 4096 if entry['format'] == 'litertlm' else 262144)
            self.assertEqual(entry['runtime']['adapter'], 'gguf' if entry['format']=='gguf' else None)
        for entry in gguf:
            models = [pin['filename'] for pin in entry['files'] if pin['role'] == 'model']
            self.assertEqual(len(models), 1)
            self.assertEqual('_MTP.gguf' in models[0], 'MTP' in entry['quantization'])
            self.assertEqual(entry['managedEngine'], 'raon-nano-cpu-v1')
            self.assertIn('CPU', entry['runtime']['reason'])

    def test_raon_platform_packages_are_complete_and_not_false_windows_adapters(self):
        entries = self.family('raon-1-nano')
        self.assertEqual(len(entries), 11)
        mlx = next(entry for entry in entries if entry['format'] == 'mlx')
        self.assertTrue({'model.safetensors', 'mtp/model.safetensors', 'config.json',
                         'mtp/config.json'} <= {pin['filename'] for pin in mlx['files']})
        self.assertTrue(all(not pin['filename'].endswith(('.py', '.sh', '.exe'))
                            for entry in entries for pin in entry['files']))
        litert = next(entry for entry in entries if entry['format'] == 'litertlm')
        self.assertIn('model.litertlm', {pin['filename'] for pin in litert['files']})
        self.assertEqual(litert['supportedContextTokens'], 4096,
                         'The LiteRT bundle has a fixed 4K cache, unlike the full checkpoint.')
        self.assertTrue(any(entry['format'] == 'safetensors' for entry in entries))

    def test_zonos3_keeps_its_real_managed_speech_package_without_a_duplicate_card(self):
        entries = self.family('zonos3-mini')
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        validate_entry(entry)
        self.assertEqual(entry['repository'], 'Zyphra/ZONOS3-Mini')
        self.assertEqual(entry['ttsRuntime'], 'zonos3-mini-v1')
        files = {pin['filename'] for pin in entry['files']}
        self.assertTrue({'lm/zonos3_mini/model.pth', 'autoencoder/generator.pt',
                         'speaker_encoders/ownmect_s2/ckpt_0037.pth'} <= files)


if __name__ == '__main__':
    unittest.main()
