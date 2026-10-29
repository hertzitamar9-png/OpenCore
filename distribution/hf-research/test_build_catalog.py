"""Offline checks of immutable model identity, packages, grouping and runtime limits."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent


class CatalogBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('catalog_builder', ROOT / 'build_catalog.py')
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)
        cls.catalog, cls.report = cls.builder.build_catalog()
        cls.models = {m['id']: m for m in cls.catalog['models']}
        cls.artifacts = {a['id']: a for a in cls.catalog['artifacts']}
        cls.original = json.loads((ROOT / 'native-catalog.recovered.json').read_text(encoding='utf-8'))

    def family(self, root):
        return [m for m in self.catalog['models'] if m['id'] == root or m.get('variantOf') == root]

    def weight_files(self, model):
        return [self.artifacts[a] for a in model.get('weightArtifacts', model['artifacts'])]

    def test_native_validation_and_slot_size(self):
        self.builder.validate_catalog(self.catalog)
        raw = json.dumps(self.catalog, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        self.assertLessEqual(len(raw), 5256247)
        self.assertEqual(len(self.models), len(self.catalog['models']))
        self.assertEqual(len(self.artifacts), len(self.catalog['artifacts']))

    def test_every_selectable_text_weight_package_has_an_echo_profile(self):
        groups={}
        for model in self.catalog['models']:
            if model.get('category') not in ('text','brain') or not model.get('selectable'):
                continue
            weights=tuple(sorted(a['sha256'] for a in self.weight_files(model)))
            groups.setdefault(weights,[]).append(model)
        self.assertGreater(len(groups),400)
        for weights,models in groups.items():
            self.assertTrue(any(m.get('memoryMode')=='echo' for m in models),[m['id'] for m in models])
        self.assertEqual(self.report['echoCoverage']['missingEchoWeightPackages'],0)
        original={a['id']:a for a in self.original['artifacts']}
        for identifier,artifact in original.items():
            self.assertEqual(self.artifacts[identifier],artifact)

    def test_added_artifacts_match_primary_repository_metadata(self):
        original = {a['id'] for a in self.original['artifacts']}
        support = json.loads((ROOT / 'support-pins.json').read_text(encoding='utf-8'))
        metadata = {}
        checked = 0
        for artifact in self.catalog['artifacts']:
            if artifact['id'] in original:
                continue
            repo = artifact['repo']
            if repo not in metadata:
                info = json.loads((ROOT / (repo.replace('/', '--') + '.api.json')).read_text(encoding='utf-8'))
                metadata[repo] = info, {f['rfilename']: f for f in info['siblings']}
            info, files = metadata[repo]
            file = files[artifact['filename']]
            pin = (file.get('lfs') or {}).get('sha256') or support[repo][artifact['filename']]['sha256']
            self.assertEqual(artifact['revision'], info['sha'])
            self.assertEqual(artifact['sha256'], pin)
            self.assertEqual(artifact['bytes'], file['size'])
            checked += 1
        self.assertGreater(checked, 300)

    def test_all_eight_swift_models_remain_one_family_with_native_and_echo(self):
        repo = 'ukisai/Swift-1.5-Qwen3.8-27B-GSQ-RCO-GGUF'
        info = json.loads((ROOT / (repo.replace('/', '--') + '.api.json')).read_text(encoding='utf-8'))
        expected = {f['rfilename'] for f in info['siblings'] if f['rfilename'].endswith('.gguf') and not f['rfilename'].startswith('imatrix')}
        rows = self.family('swift-27b')
        actual = {a['filename'] for m in rows for a in self.weight_files(m) if a['repo'] == repo}
        self.assertEqual(len(expected), 8)
        self.assertEqual(actual, expected)
        for filename in expected:
            modes = {m['memoryMode'] for m in rows if any(a['filename'] == filename for a in self.weight_files(m))}
            self.assertEqual(modes, {'echo', 'native'})
        old_ids = {m['id'] for m in self.original['models'] if m['id'].startswith('swift-27b')}
        self.assertTrue(old_ids.issubset(self.models))

    def test_main_safetensors_packages_have_every_shard_and_support_files(self):
        cases = [('flint', 'RecursiveSI/flint', 15), ('ornith-15-122b', 'ornith-ai/Ornith-1.5-122B', 62), ('raon-1-mini-nvfp4', 'KRAFTON/Raon-1-mini-preview-nvfp4', 25)]
        for model_id, repo, count in cases:
            with self.subTest(repo=repo):
                model = self.models[model_id]
                info = json.loads((ROOT / (repo.replace('/', '--') + '.api.json')).read_text(encoding='utf-8'))
                expected = {f['rfilename'] for f in info['siblings'] if f['rfilename'].endswith('.safetensors')}
                actual = {a['filename'] for a in self.weight_files(model)}
                self.assertEqual(len(expected), count)
                self.assertEqual(actual, expected)
                selected = {self.artifacts[a]['filename'] for a in model['artifacts']}
                for name in ['config.json', 'tokenizer.json', 'tokenizer_config.json']:
                    if any(f['rfilename'] == name for f in info['siblings']):
                        self.assertIn(name, selected)
                self.assertTrue(model['installable'])

    def test_raon_main_quants_exclude_draft_and_swift_excludes_imatrix(self):
        repo = 'KRAFTON/Raon-1-mini-preview-gguf'
        rows = self.family('raon-1-mini')
        selected = {a['filename'] for m in rows for a in self.weight_files(m) if a['repo'] == repo}
        self.assertEqual(selected, {'raon-1-mini-preview-' + q + '.gguf' for q in ['IQ4_XS', 'Q4_K_M', 'Q5_K_M', 'Q6_K', 'Q8_0']})
        affected = self.family('swift-27b') + rows
        self.assertFalse(any(re.search(r'imatrix|dflash', a['filename'], re.I) for m in affected for a in self.weight_files(m)))
        roles = {item['role'] for item in self.report['excludedAuxiliaries']}
        self.assertEqual(roles, {'calibration', 'draft'})

    def test_unsupported_architectures_never_advertise_a_ready_runner(self):
        for model in self.family('flint') + self.family('raon-1-mini'):
            with self.subTest(id=model['id']):
                self.assertFalse(model['runtimeReady'])
                self.assertFalse(model['selectable'])
                self.assertTrue(model['installable'])
                self.assertEqual(model['brainRole'], 'controller')
                self.assertIn('ECHO', model['note'])
        self.assertIn('4070', self.models['raon-1-mini-nvfp4']['note'])

    def test_ornith_compatible_gguf_uses_existing_adapter_pattern(self):
        rows = [m for m in self.family('ornith-15-122b') if m.get('backend') == 'gguf']
        self.assertEqual({m['precision'] for m in rows}, {'BF16', 'Q4_K_M', 'Q5_K_M', 'Q6_K', 'Q8_0'})
        self.assertEqual(len(rows), 10)
        for model in rows:
            self.assertTrue(model['runtimeReady'])
            self.assertTrue(model['selectable'])
            self.assertEqual(model['installation']['adapterId'], 'native-text-v1')
            self.assertIn('unverified', model['note'])
            self.assertEqual(model['inputModalities'], ['text'])

    def test_phonon_generic_alias_is_removed_and_quant_variants_remain(self):
        self.assertIn('phonon-2', self.models)
        self.assertNotIn('hf-fermionresearch-phonon-2', self.models)
        self.assertEqual(self.report['aliases']['hf-fermionresearch-phonon-2'], 'phonon-2')
        self.assertEqual(len(self.family('phonon-2')), 4)

    def test_dedup_uses_repository_identity_and_retains_distinct_finetunes(self):
        models = [
            {'id': 'curated', 'label': 'Same', 'artifacts': ['w'], 'weightArtifacts': ['w']},
            {'id': 'hf-owner-base', 'label': 'Different label', 'artifacts': [], 'sourceUrl': 'https://huggingface.co/Owner/Base/tree/' + 'b' * 40, 'backend': 'external', 'runtimeReady': False},
            {'id': 'hf-owner-finetune', 'label': 'Same', 'artifacts': [], 'sourceUrl': 'https://huggingface.co/Owner/Finetune', 'backend': 'external', 'runtimeReady': False},
        ]
        artifacts = [{'id': 'w', 'repo': 'Owner/Base', 'filename': 'model.gguf'}]
        result, aliases = self.builder.deduplicate_generic_models(copy.deepcopy(models), artifacts)
        self.assertEqual({m['id'] for m in result}, {'curated', 'hf-owner-finetune'})
        self.assertEqual(aliases, {'hf-owner-base': 'curated'})

    def test_catalog_generation_is_deterministic(self):
        again, report = self.builder.build_catalog()
        self.assertEqual(again, self.catalog)
        self.assertEqual(report, self.report)

    def test_complete_image_pipelines_have_automatic_runtime_routes(self):
        recipes=json.loads((ROOT.parents[1]/'src-tauri/resources/runtime-setup/recipes.json').read_text())['recipes']
        routes={target:recipe['id'] for recipe in recipes for target in recipe['modelIds']}
        for entry in json.loads((ROOT/'image-pipelines.json').read_text())['pipelines']:
            model=self.models[entry['modelId']]
            self.assertEqual(model['backend'],'diffusers')
            self.assertTrue(model['runtimeReady'])
            self.assertEqual(model['installation']['adapterId'],routes[model['id']])
            index=next(self.artifacts[a] for a in model['artifacts'] if self.artifacts[a]['filename']=='model_index.json')
            self.assertEqual(index['sha256'],entry['modelIndexSha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
