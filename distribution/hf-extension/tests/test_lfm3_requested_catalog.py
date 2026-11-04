"""Pinned Nov 4 LFM3 editions, complete packages and honest runtime boundaries."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from requested_catalog import append_requested
from hf_download import validate_entry
from model_modes import apply_operating_modes, snapshot_metadata


EXPECTED = {
    'Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF': ('d41a069845366e4770c12c104c1dff19ce7fe853', 25),
    'mondk/LFM3-1.7B-Claude-Code-GGUF': ('1589719430defe5364fb9e65cc3fa995f55babcf', 1),
    'litert-community/LFM3-1.7B-LiteRT': ('29f09e1eac2ccf753af14806dca96eb048e8baad', 4),
    'Guilherme34/LFM3-1.7B-Looped-HiddenCoT-GGUF': ('8530c4a214a115c28b18e845dc0c48604653ef26', 2),
    'mohommed/LFM3-1.7B-Frontier-SmartAgentv4-GGUF': ('15337568f924a8218d4dc6c6a81a86670c915a52', 3),
    'kaitchup/LFM3-1.7B-SLQ-GGUF': ('60e8d51e442e75b017c432cb9f3798cc6b7a07f3', 1),
    'orcarouter/LFM3-1.7B-abliterated': ('c1ca5b62e529a095966acbc126909375280aff8b', 1),
    'AtomicChat/LFM3-1.7B-Uncensored-GGUF': ('3ad712542eb9de349dbd28f2739b69c925cdec7d', 2),
}
LITERT = 'litert-community/LFM3-1.7B-LiteRT'
LOOPED = 'Guilherme34/LFM3-1.7B-Looped-HiddenCoT-GGUF'
GATED = 'orcarouter/LFM3-1.7B-abliterated'


class Lfm3RequestedCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = ROOT / 'hf-research/requested-models.json'
        cls.snapshots = ROOT / 'hf-research/requested-models'
        cls.rows = json.loads(cls.manifest.read_text(encoding='utf-8'))['models']
        cls.entries = append_requested([], cls.manifest, cls.snapshots)

    def entries_for(self, repository):
        entries = [e for e in self.entries if e.get('originalRepository') == repository]
        self.assertEqual(len(entries), EXPECTED[repository][1], repository)
        return entries

    def row_for(self, repository):
        rows = [r for r in self.rows if r['repository'] == repository]
        self.assertEqual(len(rows), 1, repository)
        return copy.deepcopy(rows[0])

    def build_row(self, row, snapshots=None):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text(json.dumps({'models': [row]}), encoding='utf-8')
            return append_requested([], manifest, snapshots or self.snapshots)

    def test_eight_exact_cards_keep_every_published_weight_once_with_real_hashes(self):
        families = set()
        for repository, (revision, _) in EXPECTED.items():
            with self.subTest(repository=repository):
                entries = self.entries_for(repository)
                metadata = json.loads((self.snapshots / (repository.replace('/', '--') + '.api.json')).read_text(encoding='utf-8'))
                self.assertEqual((metadata['id'], metadata['sha']), (repository, revision))
                published = {s['rfilename']: s for s in metadata['siblings']
                             if s['rfilename'].endswith(('.gguf', '.litertlm', '.safetensors'))}
                chosen = []
                for entry in entries:
                    self.assertEqual((entry['repository'], entry['revision']), (repository, revision))
                    self.assertEqual(entry['category'], 'text')
                    families.add(entry['familyId'])
                    validate_entry(entry)
                    self.assertEqual(entry['bytes'], sum(pin['bytes'] for pin in entry['files']))
                    for pin in entry['files']:
                        self.assertTrue(pin['pinned'])
                        self.assertEqual((pin['repository'], pin['revision']), (repository, revision))
                        self.assertFalse(pin['filename'].endswith(('.py', '.sh', '.exe')))
                        if pin['role'] == 'model':
                            self.assertIn(pin['filename'], published)
                            item = published[pin['filename']]
                            self.assertEqual((pin['bytes'], pin['sha256']), (item['lfs']['size'], item['lfs']['sha256']))
                            chosen.append(pin['filename'])
                self.assertCountEqual(chosen, published)
        self.assertEqual(len(families), 8)

    def test_standard_ggufs_select_the_lfm3_engine_without_claiming_inference(self):
        count = 0
        for repository in EXPECTED.keys() - {LITERT, LOOPED, GATED}:
            for entry in self.entries_for(repository):
                count += 1
                self.assertEqual((entry['format'], entry['modelType']), ('gguf', 'lfm3'))
                self.assertEqual(entry['managedEngine'], 'lfm3-cpu-v1')
                self.assertEqual(entry['runtime']['adapter'], 'gguf')
                self.assertFalse(entry['runtime']['available'])
                self.assertFalse(entry['runtime']['inferenceVerified'])
                self.assertEqual(entry['memoryMode'], 'echo')
                self.assertEqual(entry['contextTokens'], 32768)
        self.assertEqual(count, 32)

    def test_vortecks_includes_low_bits_and_all_three_full_precision_editions(self):
        entries = self.entries_for('Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF')
        self.assertEqual({e['quantization'] for e in entries}, {
            'BF16', 'F16', 'F32', 'IQ3_M', 'IQ3_S', 'IQ4_NL', 'IQ4_XS',
            'Q1_0', 'Q2_0', 'Q2_K', 'Q3_K_L', 'Q3_K_M', 'Q3_K_S',
            'Q4_0', 'Q4_1', 'Q4_K_M', 'Q4_K_S', 'Q5_0', 'Q5_1',
            'Q5_K_M', 'Q5_K_S', 'Q6_K', 'Q8_0', 'TQ1_0', 'TQ2_0'})
        review = json.loads((self.snapshots / 'LFM3-derivatives-runtime.review.json').read_text(encoding='utf-8'))
        self.assertTrue(all(review['typeChecks'].values()))
        self.assertTrue(review['allocationChecks']['IQ3_M'])
        self.assertEqual(review['engine'], 'lfm3-cpu-v1')
        self.assertFalse(review['inferenceVerified'])

    def test_litert_keeps_two_precisions_and_both_real_cache_sizes_without_a_gguf_adapter(self):
        entries = copy.deepcopy(self.entries_for(LITERT))
        expected = {
            'LFM3-1.7B_int8_ekv2048.litertlm': (1801549264, 'fb8af4d6cfb71d0f381a2af23f14733c3adbd93171b251560ee899276926001a', 2048),
            'LFM3-1.7B_int8_ekv4096.litertlm': (1801557456, '8b83651037fcec23f232e8f4e670683a44cc7097a4d5f74a1abddeb7bc0dae27', 4096),
            'LFM3-1.7B_mixed_int4_ekv2048.litertlm': (1441620928, '0400d8ffdb00e4bbe1209120e2c7baf05322a8c900839b0a94cc28f599011196', 2048),
            'LFM3-1.7B_mixed_int4_ekv4096.litertlm': (1441629120, '327486bf01d0b1eefd727b7aadec8627a2b8db143349262857ac98403109d72b', 4096),
        }
        self.assertEqual(len({e['quantization'] for e in entries}), 4)
        published = json.loads((self.snapshots / (LITERT.replace('/', '--') + '.litertlm_manifest.json')).read_text(encoding='utf-8'))
        self.assertEqual({v['file']: (v['size_bytes'], v['sha256']) for v in published['variants']},
                         {name: (value[0], value[1]) for name, value in expected.items()})
        apply_operating_modes(entries, snapshot_metadata(ROOT / 'hf-research'))
        for entry in entries:
            self.assertEqual(entry['format'], 'litertlm')
            model = [p for p in entry['files'] if p['role'] == 'model']
            self.assertEqual(len(model), 1)
            size, sha256, cap = expected[model[0]['filename']]
            self.assertEqual((model[0]['bytes'], model[0]['sha256']), (size, sha256))
            self.assertEqual(entry['supportedContextTokens'], cap)
            self.assertEqual(entry['contextTokens'], cap)
            self.assertEqual([m['contextTokens'] for m in entry['operatingModes']], [cap, cap])
            self.assertTrue(entry['supportsTextGeneration'])
            self.assertFalse(any(m['supported'] for m in entry['operatingModes']))
            self.assertIsNone(entry['runtime']['adapter'])
            self.assertNotIn('managedEngine', entry)
            self.assertIn('LiteRT-LM', entry['runtime']['reason'])
            self.assertIn('Android', entry['runtime']['reason'])
            self.assertEqual({p['filename'] for p in entry['files'] if p['role'] == 'support'}, {'litertlm_manifest.json', 'LICENSE', 'README.md'})

    def test_gated_bf16_is_the_real_checkpoint_with_no_invented_quant_or_custom_code_execution(self):
        entry = self.entries_for(GATED)[0]
        self.assertEqual((entry['format'], entry['quantization'], entry['modelType']), ('safetensors', 'BF16', 'lfm3'))
        self.assertTrue(entry['gated'])
        self.assertIsNone(entry['runtime']['adapter'])
        self.assertIn('gated', entry['runtime']['reason'])
        self.assertIn('custom', entry['runtime']['reason'])
        self.assertIn('config.json', {p['filename'] for p in entry['files']})
        self.assertEqual([p['sha256'] for p in entry['files'] if p['role'] == 'model'], ['a11f80c25e8d70ead520fa8e686442d3807cbee12a6286182f40174d5ef34bf5'])
        self.assertFalse(entry['runtime']['inferenceVerified'])

    def test_litert_cache_caps_survive_the_full_publisher_build(self):
        spec = importlib.util.spec_from_file_location('lfm3_publisher_build', ROOT / 'build-publisher-catalog.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        entries = [e for e in module.build()['entries'] if e.get('originalRepository') == LITERT]
        self.assertEqual(len(entries), 4)
        for entry in entries:
            cap = entry['supportedContextTokens']
            self.assertIn(cap, (2048, 4096))
            self.assertEqual(entry['contextTokens'], cap)
            self.assertEqual(entry['echoWorkingWindowTokens'], cap)
            self.assertEqual([m['contextTokens'] for m in entry['operatingModes']], [cap, cap])

    def test_looped_pins_the_required_external_template_and_explains_missing_integration(self):
        for entry in self.entries_for(LOOPED):
            self.assertEqual(entry['format'], 'gguf')
            self.assertIn('chat_template', {p['filename'] for p in entry['files']})
            self.assertIsNone(entry['runtime']['adapter'])
            self.assertNotIn('managedEngine', entry)
            self.assertIn('template', entry['runtime']['reason'])
            self.assertIn('latent', entry['runtime']['reason'])
        metadata = json.loads((self.snapshots / (LOOPED.replace('/', '--') + '.api.json')).read_text(encoding='utf-8'))
        template = (self.snapshots / (LOOPED.replace('/', '--') + '.chat_template')).read_text(encoding='utf-8')
        self.assertNotEqual(metadata['gguf']['chat_template'], template)

    def test_custom_allocations_keep_publisher_labels_without_turning_calibration_into_quality_claims(self):
        self.assertEqual({e['quantization'] for e in self.entries_for('kaitchup/LFM3-1.7B-SLQ-GGUF')}, {'SLQ EAR97.7 (mixed Q6_K)'})
        self.assertEqual({e['quantization'] for e in self.entries_for('AtomicChat/LFM3-1.7B-Uncensored-GGUF')}, {'AD-Q4_K_M', 'AD-Q6_K'})

    def test_modes_preserve_the_published_256k_and_512k_metadata_without_claiming_one_million(self):
        selected = copy.deepcopy([e for e in self.entries if e.get('originalRepository') in EXPECTED])
        self.assertEqual(len(selected), 39)
        apply_operating_modes(selected, snapshot_metadata(ROOT / 'hf-research'))
        for entry in selected:
            self.assertEqual([m['id'] for m in entry['operatingModes']], ['native', 'echo'])
            self.assertFalse(entry['runtime']['inferenceVerified'])
            if entry['originalRepository'] in {LITERT, GATED}:
                continue
            native = 524288 if entry['originalRepository'].startswith('mohommed/') else 262144
            self.assertEqual(entry['publishedContextTokens'], native)
            self.assertEqual([m['contextTokens'] for m in entry['operatingModes']], [native, 32768])

    def test_missing_litert_weight_or_manifest_cannot_be_a_complete_download(self):
        row = self.row_for(LITERT)
        metadata = json.loads((self.snapshots / (LITERT.replace('/', '--') + '.api.json')).read_text(encoding='utf-8'))
        for missing in ('LFM3-1.7B_int8_ekv2048.litertlm', 'litertlm_manifest.json'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                broken = copy.deepcopy(metadata)
                broken['siblings'] = [s for s in broken['siblings'] if s['rfilename'] != missing]
                (target / (LITERT.replace('/', '--') + '.api.json')).write_text(json.dumps(broken), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'Incomplete curated package'):
                    self.build_row(row, target)

    def test_package_context_limits_are_validated_and_cannot_raise_a_family_cap(self):
        row = self.row_for(LITERT)
        for invalid in (0, -1, True, None, '4096', 17 * 1048576):
            with self.subTest(invalid=invalid):
                broken = copy.deepcopy(row)
                broken['sources'][0]['packages'][0]['supportedContextTokens'] = invalid
                with self.assertRaisesRegex(ValueError, 'execution context limit'):
                    self.build_row(broken)
        row['supportedContextTokens'] = 1024
        self.assertTrue(all(e['contextTokens'] == 1024 and e['supportedContextTokens'] == 1024 for e in self.build_row(row)))


if __name__ == '__main__':
    unittest.main()
