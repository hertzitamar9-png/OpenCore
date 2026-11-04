"""Immutable external-template metadata travels with only its own GGUF files."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('phone_template_catalog', ROOT / 'phone_catalog.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class PhoneTemplateCatalogTests(unittest.TestCase):
    def setUp(self):
        self.repo = 'Guilherme34/LFM3-1.7B-Looped-HiddenCoT-GGUF'
        self.revision = '8530c4a214a115c28b18e845dc0c48604653ef26'
        path = ROOT.parent / 'distribution/hf-research/requested-models' / (self.repo.replace('/', '--') + '.api.json')
        metadata = json.loads(path.read_text(encoding='utf-8'))
        template = next(s for s in metadata['siblings'] if s['rfilename'] == 'chat_template')
        self.template = {'repo': self.repo, 'revision': self.revision, 'filename': 'chat_template',
                         'bytes': 12221, 'sha256': 'f6cd29fd46ccf7817ae8d81b44a7ba979c316647c95d8332d8029af509a571e6',
                         'gitBlobSha1': template['blobId']}
        self.entry = {'familyId': 'looped', 'familyLabel': 'Looped', 'category': 'text',
                      'repository': self.repo, 'revision': self.revision, 'chatTemplate': self.template,
                      'files': [{'repository': self.repo, 'revision': self.revision, 'filename': 'model-Q6_K.gguf',
                                 'bytes': 100, 'sha256': 'b' * 64, 'role': 'model', 'pinned': True},
                                {**self.template, 'repository': self.repo, 'role': 'support', 'pinned': True}]}

    def files(self, entry=None):
        return catalog.build({'models': [], 'artifacts': []}, {'entries': [entry or self.entry]})['families'][0]['files']

    def test_only_model_file_gets_the_verified_template_declaration(self):
        files = self.files()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].get('chatTemplate'), {**self.template, 'modelFilename': 'model-Q6_K.gguf', 'modelSha256': 'b' * 64})

    def test_normal_models_do_not_inherit_any_previous_template(self):
        entry = copy.deepcopy(self.entry)
        entry.pop('chatTemplate')
        self.assertNotIn('chatTemplate', self.files(entry)[0])

    def test_template_must_match_its_weight_and_pinned_support_receipt(self):
        for field, value in [('repo', 'other/model'), ('revision', 'a' * 40), ('bytes', 1048576),
                             ('filename', '../chat_template'), ('gitBlobSha1', '0' * 40), ('sha256', 'bad')]:
            with self.subTest(field=field):
                entry = copy.deepcopy(self.entry)
                entry['chatTemplate'][field] = value
                with self.assertRaisesRegex(ValueError, 'template'):
                    self.files(entry)

    def test_all_four_real_litert_files_keep_their_format_engine_and_small_cache_caps(self):
        sys.path.insert(0, str(ROOT.parent / 'distribution'))
        sys.path.insert(0, str(ROOT.parent / 'src-tauri/resources/runtime-setup/hf-extension'))
        from requested_catalog import append_requested
        research = ROOT.parent / 'distribution/hf-research'
        entries = append_requested([], research / 'requested-models.json', research / 'requested-models')
        result = catalog.build({'models': [], 'artifacts': []}, {'entries': entries})
        family = next(f for f in result['families'] if f['repo'] == 'litert-community/LFM3-1.7B-LiteRT')
        self.assertEqual(len(family['files']), 4)
        for file in family['files']:
            self.assertEqual((file['format'], file['engine'], file['architecture']), ('litertlm', 'litertlm', 'lfm3'))
            self.assertTrue(file['supportsTextGeneration'])
            self.assertEqual(file['contextTokens'], 2048 if 'ekv2048' in file['filename'] else 4096)
            self.assertTrue(file['filename'].endswith('.litertlm'))
            self.assertNotIn('profiles', file)
            self.assertFalse(file.get('runtimeReady'))

    def test_real_looped_options_carry_the_published_template_to_each_exact_model(self):
        sys.path.insert(0, str(ROOT.parent / 'distribution'))
        sys.path.insert(0, str(ROOT.parent / 'src-tauri/resources/runtime-setup/hf-extension'))
        from requested_catalog import append_requested
        research = ROOT.parent / 'distribution/hf-research'
        entries = append_requested([], research / 'requested-models.json', research / 'requested-models')
        result = catalog.build({'models': [], 'artifacts': []}, {'entries': entries})
        files = next(f for f in result['families'] if f['repo'] == self.repo)['files']
        self.assertEqual(len(files), 2)
        for file in files:
            self.assertEqual(file['chatTemplate']['sha256'], self.template['sha256'])
            self.assertEqual(file['chatTemplate']['modelFilename'], file['filename'])
            self.assertEqual(file['chatTemplate']['modelSha256'], file['sha256'])


if __name__ == '__main__':
    unittest.main()
