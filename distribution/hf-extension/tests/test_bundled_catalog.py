import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bundled_catalog import append_presets, coverage


def metadata(repository, quantizations, base=None):
    return dict(id=repository, sha='a' * 40, pipeline_tag='text-generation',
                cardData=dict(base_model=base, base_model_relation='quantized') if base else {},
                config=dict(model_type='llama'), siblings=[dict(rfilename='model-' + q + '.gguf',
                size=10, lfs=dict(sha256='b' * 64, size=10)) for q in quantizations])


class BundledCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.catalog = dict(models=[dict(id='native-model', label='Original checkpoint', category='text',
                                        description='Original description', precision='Original', artifacts=[])])
        self.manifest = self.root / 'manifest.json'

    def write(self, repository, quants, base=None):
        (self.root / (repository.replace('/', '--') + '.api.json')).write_text(json.dumps(metadata(repository, quants, base)))

    def plan(self, sources):
        self.manifest.write_text(json.dumps(dict(models=[dict(id='native-model', label='Original checkpoint',
            category='text', repository='owner/Model', nativeModelIds=['native-model'], repositories=sources, availableQuants=5)])))
        return append_presets(self.catalog, [], self.manifest, self.root)

    def test_five_real_choices_are_bundled_offline_and_duplicate_publisher_copies_do_not_pad_them(self):
        self.write('owner/Model', ['Q4_K_M'])
        self.write('publisher/Model-GGUF', ['Q4_K_M', 'Q5_K_M', 'Q6_K', 'Q8_0', 'Q3_K_M', 'F16'], 'owner/Model')
        entries = self.plan(['owner/Model', 'publisher/Model-GGUF'])
        self.assertEqual(len(entries), 5)
        self.assertEqual(len({entry['quantization'] for entry in entries}), 5)
        self.assertTrue(all(entry['builtIn'] and entry['installable'] for entry in entries))
        self.assertTrue(all(entry['nativeModelIds'] == ['native-model'] for entry in entries))
        self.assertTrue(all(pin['pinned'] for entry in entries for pin in entry['files']))
        self.assertTrue(coverage(self.catalog, entries)['allModelsMeetMinimum'])

    def test_missing_quantizations_remain_a_reported_gap(self):
        self.write('owner/Model', ['Q4_K_M', 'Q8_0'])
        entries = self.plan(['owner/Model'])
        self.assertEqual(len(entries), 2)
        report = coverage(self.catalog, entries)
        self.assertFalse(report['allModelsMeetMinimum'])
        self.assertEqual(report['models'][0]['quantizations'], 2)

    def test_other_checkpoints_cannot_be_bundled_to_meet_the_minimum(self):
        self.write('owner/Model', ['Q4_K_M'])
        self.write('other/Different-GGUF', ['Q2_K', 'Q3_K_M', 'Q4_K_M', 'Q5_K_M', 'Q6_K'], 'other/Different')
        with self.assertRaisesRegex(ValueError, 'exact checkpoint'):
            self.plan(['owner/Model', 'other/Different-GGUF'])

    def test_existing_custom_checkpoint_is_not_merged_into_its_training_base(self):
        self.catalog['models'][0]['sourceUrl'] = 'https://huggingface.co/owner/Model'
        self.catalog['models'].append(dict(id='native-custom', label='Model Turbo', category='text',
            sourceUrl='https://huggingface.co/custom/Model-Turbo-GGUF', precision='Q4_K_M', artifacts=[]))
        self.write('owner/Model', ['Q4_K_M'])
        self.write('publisher/Model-GGUF', ['Q3_K_M', 'Q4_K_M', 'Q5_K_M', 'Q6_K', 'Q8_0'], 'owner/Model')
        self.write('custom/Model-Turbo-GGUF', ['Q4_K_M', 'Q8_0'], 'owner/Model')
        rows=[dict(id='native-model',label='Model',category='text',repository='owner/Model',availableQuants=5,
                   nativeModelIds=['native-model'],repositories=['owner/Model','publisher/Model-GGUF','custom/Model-Turbo-GGUF']),
              dict(id='native-custom',label='Model Turbo',category='text',repository='custom/Model-Turbo-GGUF',availableQuants=2,
                   nativeModelIds=['native-custom'],repositories=['custom/Model-Turbo-GGUF'])]
        self.manifest.write_text(json.dumps(dict(models=rows)))
        entries=append_presets(self.catalog,[],self.manifest,self.root)
        self.assertEqual(len({entry['familyId'] for entry in entries}),2)
        self.assertTrue(all(entry['repository']!='custom/Model-Turbo-GGUF' for entry in entries if 'native-model' in entry['nativeModelIds']))
        self.assertTrue(all('native-model' not in entry['nativeModelIds'] for entry in entries if entry['repository']=='custom/Model-Turbo-GGUF'))
        self.manifest.write_text(json.dumps(dict(identityRepositories=['custom/Model-Turbo-GGUF'],models=[rows[0]])))
        entries=append_presets(self.catalog,[],self.manifest,self.root)
        self.assertTrue(all(entry['repository']!='custom/Model-Turbo-GGUF' for entry in entries))
        self.assertTrue(all('native-custom' not in entry['nativeModelIds'] for entry in entries))


if __name__ == '__main__':
    unittest.main()
