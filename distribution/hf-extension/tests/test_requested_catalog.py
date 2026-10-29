"""Offline regressions for the explicitly requested model identities and packages."""
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
    def test_apex_keeps_the_exact_finetune_and_complete_published_packages(self):
        entries=self.family('apex-35b-v1')
        self.assertEqual(len(entries),26)
        self.assertTrue(all(e['originalRepository']=='Apexintelligence-AI/Apex-35B-V1.0' for e in entries))
        self.assertEqual(len({(e['format'],e['quantization']) for e in entries}),26)
        gguf=[e for e in entries if e['format']=='gguf']
        self.assertEqual(len(gguf),25)
        self.assertTrue({'Q4_K_M','Q5_K_M','Q6_K','Q8_0','IQ4_XS'}.issubset({e['quantization'] for e in gguf}))
        self.assertTrue(all(e['managedEngine']=='flint-cpu-v1' for e in gguf))
        official=[e for e in gguf if e['repository']=='Apexintelligence-AI/Apex-35B-V1.0-GGUF']
        self.assertEqual(len(official),5)
        split=next(e for e in official if e['quantization']=='BF16')
        self.assertEqual(sum(pin['role']=='model' for pin in split['files']),2)
        original=next(e for e in entries if e['format']=='safetensors')
        self.assertEqual(sum(pin['role']=='model' for pin in original['files']),26)
        for entry in entries:
            for pin in entry['files']:
                self.assertTrue(pin['pinned'])
                self.assertNotIn('imatrix',pin['filename'].lower())
                if pin['filename'].startswith('mmproj-'):
                    self.assertEqual(pin['role'],'companion')

    def test_lfm3_and_carbon_packages_keep_real_precisions_and_compatible_runtimes(self):
        expected = {'lfm3-vl-2b': 8, 'lfm3-1-7b': 8, 'carbon-x': 13}
        for suffix, count in expected.items():
            entries = self.family(suffix)
            self.assertEqual(len(entries), count)
            self.assertTrue(all(pin['pinned'] for entry in entries for pin in entry['files']))
            self.assertEqual(len({(e['format'], e['quantization']) for e in entries}), count)
        for suffix in ('lfm3-vl-2b', 'lfm3-1-7b'):
            entries = self.family(suffix)
            gguf = [e for e in entries if e['format'] == 'gguf']
            self.assertEqual({e['quantization'] for e in gguf},
                             {'BF16', 'F16', 'QAD Q4_0', 'Q4_K_M', 'Q5_K_M', 'Q6_K', 'Q8_0'})
            self.assertTrue(all(e['managedEngine'] == 'lfm3-cpu-v1' for e in gguf))
            original = next(e for e in entries if e['format'] == 'safetensors')
            self.assertIsNone(original['runtime']['adapter'])
            self.assertIn('Choose an official GGUF', original['runtime']['reason'])
            for entry in gguf:
                companions = [pin for pin in entry['files'] if pin['role'] == 'companion']
                self.assertEqual(len(companions), 1 if suffix == 'lfm3-vl-2b' else 0)
                if companions:
                    self.assertIn('mmproj-', companions[0]['filename'])
        carbon = self.family('carbon-x')
        original = next(e for e in carbon if e['format'] == 'safetensors')
        self.assertEqual(sum(pin['role'] == 'model' for pin in original['files']), 2)
        self.assertTrue(all(e['originalRepository'] == 'HuggingFaceBio/Carbon-X' for e in carbon))
        self.assertTrue({'Q2_K', 'Q3_K_M', 'Q4_K_M', 'Q5_K_M', 'Q6_K', 'Q8_0'}.issubset(
            {e['quantization'] for e in carbon}))

    def test_new_requested_publishers_have_pinned_real_packages(self):
        hi3d=self.family('hi3d-v1')
        ocr=self.family('unlimited-ocr-2')
        coder=self.family('qwen35-4b-3d-coder')
        self.assertEqual(len(hi3d),1)
        self.assertEqual(len(ocr),1)
        self.assertEqual({e['quantization'] for e in coder},{'Q4_K_M','Q8_0','FP16'})
        self.assertEqual({e['originalRepository'] for e in hi3d+ocr+coder},
                         {'hi3d-official/Hi3Dv1.0','baidu/Unlimited-OCR-2','ccore/Qwen3.5-4B-3D-Coder'})
        self.assertTrue(all(p['pinned'] for entry in hi3d+ocr+coder for p in entry['files']))
        self.assertTrue(all(e['runtime']['adapter'] is None for e in hi3d+ocr))
        self.assertEqual(sum(p['role']=='model' for p in hi3d[0]['files']),3)
        self.assertEqual(sum(p['role']=='model' for p in ocr[0]['files']),2)
        self.assertTrue(all(e['runtime']['adapter']=='gguf' for e in coder if e['format']=='gguf'))
        original=next(e for e in coder if e['format']=='safetensors')
        self.assertEqual(sum(p['role']=='model' for p in original['files']),4)
        self.assertTrue(all(e['revision']=='1bf0f958cb4bac8e04b98280dd9fb3f0e942c015' for e in coder))

    def test_latest_requested_models_have_all_verified_preset_options(self):
        expected = {'qwen38-jumbo-fusion-neo-mtp':7, 'phonon-2-5':1, 'kat-coder-apex-miniplus-v2-1':1,
                    'simplesd-4b-thinking':27, 'cortiq-qwopus-dev':5, 'nemotron-3-5-bolt-e4b':10,
                    'fibo-2-0-dev':1, 'flux-3-image-klein':13, 'moss-image-nsfw-lora':2, 'qwen-image-2-1-nsfw-lora':1}
        for family, count in expected.items():
            with self.subTest(family=family): self.assertEqual(len(self.family(family)), count)
        cyber = [entry for entry in self.entries if entry['familyId']=='builtin-b3765810015c2fb7f52d5378']
        self.assertEqual(len(cyber),12)
        self.assertTrue(all(entry['repository'].endswith('GGUF-MTP') for entry in cyber))

    def test_new_image_pipelines_and_loras_do_not_masquerade_as_text_models(self):
        flux=[entry for entry in self.family('flux-3-image-klein') if entry['format']=='image-pipeline']
        self.assertEqual({entry['quantization'] for entry in flux},{'BF16','FP8','NVFP4'})
        for entry in flux:
            names={pin['filename'] for pin in entry['files']}
            self.assertIn('ae.safetensors',names)
            self.assertEqual(sum(name.startswith('text_encoder/') and name.endswith('.safetensors') for name in names),2)
            self.assertIsNone(entry['runtime']['adapter'])
        for family in ('fibo-2-0-dev','flux-3-image-klein','moss-image-nsfw-lora','qwen-image-2-1-nsfw-lora'):
            self.assertTrue(all(entry['category']=='image' and not entry['runtime']['adapter'] for entry in self.family(family)))
        fibo=self.family('fibo-2-0-dev')[0]
        self.assertEqual(sum(pin['role']=='model' for pin in fibo['files']),4)
        self.assertTrue(fibo['gated'])
    @classmethod
    def setUpClass(cls):
        cls.manifest = ROOT / 'hf-research/requested-models.json'
        cls.snapshots = ROOT / 'hf-research/requested-models'
        cls.rows = json.loads(cls.manifest.read_text(encoding='utf-8'))['models']
        cls.entries = append_requested([], cls.manifest, cls.snapshots)

    def family(self, suffix):
        return [entry for entry in self.entries if entry['familyId'] == 'publisher-' + suffix]

    def test_all_requested_models_are_retained_as_separate_cards(self):
        self.assertTrue({
            'Hobbylm/hobbylm-1B-computer-use', 'Mininglamp-2718/Mano-CUA-2.0-4B',
            'Teriminal-RSI/Terminal-RSI-27B', 'Gen-Verse/DiscoveryRSI-27B',
            'IsValorum/Tiel-Coder-35B-A3B-APEX-I-MiniPlus-V2.1-GGUF',
            'talxcc/Tals-coder-flash-02', 'Jackrong/Kestrel-4B-Coder'}.issubset({row['repository'] for row in self.rows}))
        self.assertEqual(len({entry['familyId'] for entry in self.entries}), 26)
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
        self.assertEqual(report['families'], 26)
        self.assertEqual(report['withFive'], 13)
        self.assertFalse(report['allModelsMeetMinimum'])

    def test_custom_editions_keep_their_real_labels_projectors_and_runtime_requirements(self):
        tiel = self.family('tiel-coder-apex-miniplus-v2-1')
        tals = self.family('tals-coder-flash-02')
        kestrel = self.family('kestrel-4b-coder')
        self.assertEqual([entry['quantization'] for entry in tiel], ['APEX-I-MiniPlus V2.1'])
        self.assertEqual([entry['quantization'] for entry in tals], ['Custom ternary'])
        self.assertEqual({entry['quantization'] for entry in kestrel}, {'BF16', 'Q8_0'})
        self.assertIsNone(tals[0]['runtime']['adapter'])
        self.assertIn('Hadamard', tals[0]['runtime']['reason'])
        self.assertNotIn('managedEngine', tals[0])
        for entry in [*tiel, *tals, *(entry for entry in kestrel if entry['format'] == 'gguf')]:
            self.assertEqual(sum(pin['role'] == 'model' for pin in entry['files']), 1)
            self.assertEqual(sum(pin['role'] == 'companion' for pin in entry['files']), 1)
        self.assertTrue(all(entry['originalRepository'].startswith('IsValorum/Tiel-Coder-') for entry in tiel))
        self.assertEqual(tals[0]['originalRepository'], 'talxcc/Tals-coder-flash-02')
        self.assertTrue(all(entry['originalRepository'] == 'Jackrong/Kestrel-4B-Coder' for entry in kestrel))

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
