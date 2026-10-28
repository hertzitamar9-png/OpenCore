import tempfile
import unittest
from pathlib import Path
from PIL import Image
from comfy_worker import build_qwen_workflow, build_moss_workflow
from asset_worker import prepare_reference_images

class ImageReferenceTests(unittest.TestCase):
    def test_qwen_conditions_text_encoder_and_sampler_on_real_reference_latents(self):
        graph=build_qwen_workflow({'prompt':'Make the cup blue','settings':{}},'model.gguf',reference_names=['reference-1.png'])
        self.assertEqual(graph['100']['class_type'],'LoadImage')
        self.assertEqual(graph['4']['inputs']['images.image_1'],['100',0])
        self.assertEqual(graph['4']['inputs']['vae'],['3',0])
        self.assertEqual(graph['16']['inputs']['latent_image'],['4',2])

    def test_moss_image_to_image_uses_encoded_pixels(self):
        graph=build_moss_workflow({'prompt':'A blue cup','settings':{}},'model.safetensors',reference_names=['reference-1.png'])
        self.assertEqual(graph['101']['class_type'],'VAEEncode')
        self.assertEqual(graph['16']['inputs']['latent_image'],['101',0])
        self.assertLess(graph['16']['inputs']['denoise'],1)
        with self.assertRaisesRegex(ValueError,'one reference'):
            build_moss_workflow({'prompt':'test','settings':{}},'model',reference_names=['one.png','two.png'])

    def test_reference_images_are_validated_and_copied_as_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);file=root/'reference.png';Image.new('RGB',(120,80),'blue').save(file)
            names=prepare_reference_images({'referencePaths':[str(file)]},root/'inputs')
            self.assertEqual(names,['reference-1.png'])
            self.assertEqual(Image.open(root/'inputs'/names[0]).getpixel((0,0)),(0,0,255))
            file.write_bytes(b'not a real image')
            with self.assertRaises(ValueError):prepare_reference_images({'referencePaths':[str(file)]},root/'inputs')

if __name__=='__main__':unittest.main()
