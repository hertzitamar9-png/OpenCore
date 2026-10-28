"""Export dimensions must not enlarge model inference or silently truncate files."""
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from asset_worker import save_image_output
from model_smoke import verify_generation
from unittest.mock import patch
from contextlib import contextmanager

@contextmanager
def exporter_contract_upscaler():
    # The real pretrained, tiled inference is exercised in test_ai_upscale.
    # Here we assert export sizing and method metadata without a GPU dependency.
    def restore(image,checkpoint_path,*,target_size,device,progress):
        progress('AI upscaling with Real-ESRGAN')
        return image.resize(target_size)
    import sys,types
    with patch.dict(sys.modules,{'torch':types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda:False))}),patch('ai_upscale.upscale_image',side_effect=restore):yield


class ImageOutputTests(unittest.TestCase):
    def test_exports_2k_4k_8k_with_the_learned_upscaler_and_render_metadata(self):
        with tempfile.TemporaryDirectory() as tmp,exporter_contract_upscaler():
            output=Path(tmp)
            with Image.new('RGB',(256,256),(30,80,150)) as image:
                for size in (2048,4096,8192):
                    with self.subTest(size=size):
                        path=save_image_output(image,output,1,{'outputWidth':size,'outputHeight':size,'outputFormat':'png','upscaleModelPath':'verified-model.pth'})
                        with Image.open(path) as saved:
                            self.assertEqual(saved.size,(size,size))
                            self.assertEqual(saved.info['OpenCore.RenderSize'],'256x256')
                            self.assertEqual(saved.info['OpenCore.ResizeMethod'],'Real-ESRGAN realesr-general-x4v3')
                        self.assertIn('AI upscaling',json.loads((output/'progress.json').read_text())['stage'])

    def test_preserves_native_size_when_no_export_dimensions_are_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Image.new('RGB',(256,128)) as image:
                path=save_image_output(image,Path(tmp),2,{'outputFormat':'webp'})
                self.assertEqual(path.name,'image-2.webp')
                with Image.open(path) as saved:self.assertEqual(saved.size,(256,128))

    def test_rejects_excessive_dimensions_before_resizing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Image.new('RGB',(128,128)) as image:
                for value in (8193,True,'8192',-1):
                    with self.assertRaises(ValueError):
                        save_image_output(image,Path(tmp),1,{'outputWidth':value,'outputHeight':8192})

    def test_real_output_verification_uses_export_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp,exporter_contract_upscaler():
            request={'modelId':'image-test','settings':{'width':256,'height':256,'outputWidth':8192,'outputHeight':8192,'upscaleModelPath':'verified-model.pth'}}
            def worker(task,output):
                with Image.new('RGB',(256,256)) as image:save_image_output(image,output,1,task['settings'])
            proof=verify_generation(worker,request,Path(tmp)/'job')
            self.assertTrue(proof['inferenceVerified'])
            self.assertEqual((proof['outputs'][0]['width'],proof['outputs'][0]['height']),(8192,8192))


if __name__=='__main__':unittest.main()
