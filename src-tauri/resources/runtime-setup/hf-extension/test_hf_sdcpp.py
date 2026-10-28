import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
import hf_sdcpp as runtime
from hf_catalog import build_plan

class IrisRuntimeTests(unittest.TestCase):
    def metadata(self):
        return json.loads((Path(__file__).resolve().parents[4]/'distribution/hf-research/requested-models/webmp3--Sakura-Iris-3B-Uncensored-GGUF.api.json').read_text(encoding='utf-8'))

    def test_four_real_image_quants_include_the_complete_reviewed_encoder(self):
        variants=build_plan(self.metadata())['variants']
        self.assertEqual({row['quantization'] for row in variants},set(runtime.QUANTS))
        for row in variants:
            self.assertTrue(runtime.supports(row))
            self.assertEqual(len(row['files']),2)
            self.assertTrue(any(file['filename']==runtime.ENCODER for file in row['files']))
            self.assertEqual(row['bytes'],sum(file['bytes'] for file in row['files']))

    def test_native_cli_uses_publisher_sampler_encoder_and_no_vae(self):
        entry=build_plan(self.metadata())['variants'][0]
        request={'prompt':'A blue cup','settings':{'width':1024,'height':1024,'steps':30,'guidanceScale':3}}
        with patch.object(runtime,'model_paths',return_value=[Path('image.gguf'),Path('encoder.gguf')]):
            args=runtime.command(entry,request,Path('output'),Path('sd-cli.exe'))
            self.assertIn('dpm++2m',args);self.assertIn('--llm',args);self.assertIn('--offload-to-cpu',args)
            self.assertNotIn('--vae',args)
            for extra in ({'referencePaths':['reference.png']},{'loraPath':'adapter.safetensors'}):
                with self.assertRaises(ValueError):runtime.command(entry,{**request,'settings':{**request['settings'],**extra}},Path('out'),Path('sd-cli.exe'))

    def test_unsafe_runtime_archive_cannot_escape_its_managed_folder(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);folder=root/'runtime-setup/runtimes/iris-sdcpp-v1';folder.mkdir(parents=True)
            archive=folder/'runtime.zip'
            with zipfile.ZipFile(archive,'w') as package:package.writestr('../../outside.exe',b'fixture')
            with patch.object(runtime,'ENGINE_BYTES',archive.stat().st_size),patch.object(runtime,'ENGINE_SHA',runtime.digest(archive)):
                with self.assertRaises(ValueError):runtime.ensure_engine(root,threading.Event(),lambda message:None)
            self.assertFalse((root/'runtime-setup/outside.exe').exists())

if __name__=='__main__':unittest.main()
