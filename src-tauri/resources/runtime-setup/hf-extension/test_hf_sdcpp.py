import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
from types import SimpleNamespace
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

    def test_output_receipt_points_to_the_real_png_webp_or_jpeg_export(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            for format,extension in [('png','png'),('webp','webp'),('jpeg','jpg')]:
                output=Path(temporary)/format
                def generate(*args,**kwargs):
                    with Image.new('RGB',(128,128),(30,80,150)) as image:image.save(output/'native-image.png')
                    return SimpleNamespace(stdout=[],returncode=0,wait=lambda:0)
                payload={'entry':{'id':'iris'},'request':{'settings':{'width':128,'height':128,'outputFormat':format}},'engine':'sd-cli.exe'}
                with patch.object(runtime,'command',return_value=['sd-cli.exe']),patch.object(runtime.subprocess,'Popen',side_effect=generate):
                    runtime.run(payload,output)
                proof=json.loads((output/'smoke-result.json').read_text())
                exported=Path(proof['outputs'][0]['path'])
                self.assertEqual(exported,output/('image-1.'+extension))
                self.assertTrue(exported.is_file())
                with Image.open(exported) as image:self.assertEqual(image.size,(128,128))

if __name__=='__main__':unittest.main()
