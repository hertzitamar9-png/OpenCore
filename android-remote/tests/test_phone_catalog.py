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
        base={'artifacts':[artifact],'models':[{'id':'one-native','label':'Model native','category':'text','memoryMode':'native','contextTokens':32768,'precision':'Q4_K_M','sourceUrl':'https://huggingface.co/publisher/model','artifacts':['weight']},{'id':'one-echo','label':'Model ECHO','category':'text','memoryMode':'echo','sourceUrl':'https://huggingface.co/publisher/model','artifacts':['weight']}]}
        publisher={'entries':[{'id':'preset','familyId':'family-model','familyLabel':'Model','repository':'publisher/model','originalRepository':'publisher/model','category':'text','files':[{'repository':artifact['repo'],**artifact}]}]}
        result=module.build(base,publisher)
        self.assertEqual(len(result['families']),1)
        self.assertEqual(len(result['families'][0]['files']),1)
        file=result['families'][0]['files'][0]
        self.assertEqual(file['sha256'],artifact['sha256']);self.assertEqual(file['revision'],artifact['revision'])
        self.assertTrue(result['families'][0]['textCapable'])
        self.assertEqual({item['id']:item['memoryMode'] for item in file['profiles']},{'one-native':'native','one-echo':'echo'})
        self.assertEqual(file['profiles'][0]['contextTokens'],32768)
        self.assertEqual(file['profiles'][0]['precision'],'Q4_K_M')
        self.assertNotIn('Phone ready',str(result))

    def test_projector_never_aliases_the_text_model_profile(self):
        path=ROOT/'phone_catalog.py'
        spec=importlib.util.spec_from_file_location('phone_catalog_projector',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        weight={'id':'weight','repo':'publisher/model','revision':'a'*40,'filename':'model-Q4_K_M.gguf','sha256':'b'*64,'bytes':100,'path':'models/a.gguf'}
        projector={**weight,'id':'projector','filename':'mmproj-model-f16.gguf','sha256':'c'*64,'bytes':40}
        native={'artifacts':[weight,projector],'models':[{'id':'vision-native','label':'Vision Native','category':'omni','supportsTextGeneration':True,'memoryMode':'native','artifacts':['weight','projector']}]}
        files=module.build(native,{'entries':[]})['families'][0]['files']
        self.assertEqual(len(files),2)
        self.assertEqual(next(file for file in files if file['filename']==weight['filename'])['profiles'][0]['id'],'vision-native')
        self.assertNotIn('profiles',next(file for file in files if file['filename']==projector['filename']))

    def test_split_package_is_not_aliased_to_one_arbitrary_shard(self):
        spec=importlib.util.spec_from_file_location('phone_catalog_split',ROOT/'phone_catalog.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        parts=[{'id':str(i),'repo':'publisher/model','revision':'a'*40,'filename':f'model-0000{i}-of-00002.gguf','sha256':'b'*64,'bytes':100} for i in (1,2)]
        native={'artifacts':parts,'models':[{'id':'split-native','label':'Split','category':'text','memoryMode':'native','artifacts':['1','2']}]}
        files=module.build(native,{'entries':[]})['families'][0]['files']
        self.assertEqual(len(files),2)
        self.assertTrue(all('profiles' not in file for file in files))


if __name__=='__main__':unittest.main()
