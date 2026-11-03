from pathlib import Path
import importlib.util
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class PhoneCatalogTests(unittest.TestCase):
    def test_shared_phone_and_pc_packages_preserve_real_quantizations(self):
        path=ROOT/'phone_catalog.py'
        self.assertTrue(path.exists(),'The phone needs the existing OpenCore model catalog, not only Hub search')
        spec=importlib.util.spec_from_file_location('phone_catalog',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        artifact={'id':'weight','repo':'publisher/model','revision':'a'*40,'filename':'model-Q4_K_M.gguf','sha256':'b'*64,'bytes':100,'path':'models/a.gguf'}
        base={'artifacts':[artifact],'models':[{'id':'one-native','label':'Model native','category':'text','sourceUrl':'https://huggingface.co/publisher/model','artifacts':['weight']},{'id':'one-echo','label':'Model ECHO','category':'text','sourceUrl':'https://huggingface.co/publisher/model','artifacts':['weight']}]}
        publisher={'entries':[{'id':'preset','familyId':'family-model','familyLabel':'Model','repository':'publisher/model','originalRepository':'publisher/model','category':'text','files':[{'repository':artifact['repo'],**artifact}]}]}
        result=module.build(base,publisher)
        self.assertEqual(len(result['families']),1)
        self.assertEqual(len(result['families'][0]['files']),1)
        file=result['families'][0]['files'][0]
        self.assertEqual(file['sha256'],artifact['sha256']);self.assertEqual(file['revision'],artifact['revision'])
        self.assertTrue(result['families'][0]['textCapable'])
        self.assertNotIn('Phone ready',str(result))


if __name__=='__main__':unittest.main()
