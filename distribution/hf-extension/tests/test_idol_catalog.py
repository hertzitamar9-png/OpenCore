"""Exact IDOL publisher package, component completeness and truthful capability."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from requested_catalog import append_requested
from hf_download import validate_entry
from model_modes import apply_operating_modes,snapshot_metadata


class IdolCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest=ROOT/'hf-research/requested-models.json'
        cls.snapshots=ROOT/'hf-research/requested-models'
        cls.rows=json.loads(cls.manifest.read_text(encoding='utf-8'))['models']
        cls.entries=append_requested([],cls.manifest,cls.snapshots)

    def test_exact_publisher_is_one_complete_pipeline_not_four_fake_precision_choices(self):
        entries=[entry for entry in self.entries if entry.get('originalRepository')=='speridlabs/idol']
        self.assertEqual(len(entries),1)
        entry=entries[0]
        self.assertEqual((entry['repository'],entry['revision']),('speridlabs/idol','2731c2291fc65a797c5cb627a5e5526fc1490902'))
        self.assertEqual((entry['familyId'],entry['category'],entry['format']),('publisher-idol','3d','3d-pipeline'))
        self.assertEqual(entry['quantization'],'Publisher checkpoint')
        expected={
            'birefnet.safetensors':(444466856,'44bbc97b041f879975d0d6d538b8e66a25eb04d1f49aab40697544cdd97eaa8e'),
            'decoder.safetensors':(203171696,'9da4d7676bfcadb1c3fdfbff796e6257dadf82d26ec05be7717c79b2b13a1c4e'),
            'decoder_gs.safetensors':(949083036,'3087eb8ee5763206a53e4078acf2a1bf382dab5147d407ec13a2cf39c04cc544'),
            'denoiser.safetensors':(1482258308,'ded2cacf671bf3978ebc62729087abdbd318ef4752e6805b4c9fc8ffa51fe716'),
        }
        self.assertEqual({pin['filename'] for pin in entry['files']},set(expected)|{'config.json','README.md'})
        for pin in entry['files']:
            self.assertTrue(pin['pinned'])
            if pin['filename'] in expected:
                self.assertEqual((pin['bytes'],pin['sha256']),expected[pin['filename']])
                self.assertEqual(pin['role'],'model')
        self.assertEqual(entry['bytes'],3078988505)
        self.assertEqual(entry['license'],'apache-2.0')
        validate_entry(entry)

    def test_image_to_gaussian_pipeline_does_not_claim_a_text_or_verified_runtime(self):
        entries=copy.deepcopy([entry for entry in self.entries if entry.get('originalRepository')=='speridlabs/idol'])
        self.assertEqual(len(entries),1)
        apply_operating_modes(entries,snapshot_metadata(ROOT/'hf-research'))
        entry=entries[0]
        self.assertIsNone(entry['runtime']['adapter'])
        self.assertFalse(entry['runtime']['inferenceVerified'])
        self.assertFalse(entry.get('runtimeReady'))
        self.assertFalse(entry['supportsTextGeneration'])
        self.assertEqual(entry['operatingModes'],[])
        self.assertNotIn('memoryMode',entry)
        self.assertIn('IDOL',entry['runtime']['reason'])
        self.assertIn('DINOv3',entry['runtime']['reason'])
        self.assertIn('FLUX.2',entry['runtime']['reason'])
        self.assertFalse(any(pin['filename'].endswith(('.py','.exe','.webp','.mp4')) for pin in entry['files']))

    def test_missing_any_required_idol_component_cannot_be_called_a_complete_package(self):
        row=next((row for row in self.rows if row['repository']=='speridlabs/idol'),None)
        self.assertIsNotNone(row)
        metadata=json.loads((self.snapshots/'speridlabs--idol.api.json').read_text(encoding='utf-8'))
        for missing in ('config.json','birefnet.safetensors','decoder.safetensors','decoder_gs.safetensors','denoiser.safetensors'):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                manifest=root/'manifest.json';manifest.write_text(json.dumps({'models':[row]}),encoding='utf-8')
                modified=copy.deepcopy(metadata)
                modified['siblings']=[item for item in modified['siblings'] if item['rfilename']!=missing]
                (root/'speridlabs--idol.api.json').write_text(json.dumps(modified),encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'Incomplete curated package'):
                    append_requested([],manifest,root)


if __name__=='__main__':unittest.main()
