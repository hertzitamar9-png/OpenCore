import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from comfy_worker import build_qwen_workflow, managed_qwen_lora
from asset_worker import apply_diffusers_image_lora

class QwenLoRATest(unittest.TestCase):
    def test_graph_base_and_gguf_lora(self):
        request={'prompt':'a mountain','settings':{'width':512,'height':512,'loraStrength':0.7}}
        base=build_qwen_workflow(request,'qwen-image-2.1-UC-Q4_K_M.gguf')
        self.assertNotIn('19',base)
        self.assertEqual(base['16']['inputs']['model'],['1',0])
        graph=build_qwen_workflow(request,'qwen-image-2.1-UC-Q4_K_M.gguf','opencore-qwen-lora.safetensors')
        self.assertEqual(graph['16']['inputs']['model'],['19',0])
        self.assertEqual(graph['19']['class_type'],'LoraLoaderModelOnly')
        self.assertEqual(graph['19']['inputs']['strength_model'],0.7)
        for bad in (-1,3,float('nan'),True):
            with self.assertRaises(ValueError):
                build_qwen_workflow({'prompt':'test','settings':{'loraStrength':bad}},'base.gguf','lora.safetensors')

    def test_installed_hub_receipt_and_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            folder=root/'models'/'hub'/'hf-qwen'
            folder.mkdir(parents=True)
            file=folder/'NSFW Qwen Lora.safetensors'
            file.write_bytes(b'verified sample for unit test only')
            digest=hashlib.sha256(file.read_bytes()).hexdigest()
            receipt={'repository':'f23gg/NSFW-LORA-Qwen-Image-2.1','files':[{
                'role':'model','filename':file.name,'bytes':file.stat().st_size,'sha256':digest,'actualSha256':digest
            }]}
            (folder/'install-receipt.json').write_text(json.dumps(receipt))
            self.assertEqual(managed_qwen_lora(root,{'loraPath':str(file)}),file)
            class FakeQwenPipeline:
                def __init__(self):self.actions=[]
                def load_lora_weights(self,*args,**kwargs):self.actions.append(('load',args,kwargs))
                def set_adapters(self,*args,**kwargs):self.actions.append(('set',args,kwargs))
            pipeline=FakeQwenPipeline()
            self.assertTrue(apply_diffusers_image_lora(pipeline,'qwen-image-21',root,{'loraPath':str(file),'loraStrength':0.9}))
            self.assertEqual(pipeline.actions[0][2]['weight_name'],file.name)
            self.assertEqual(pipeline.actions[1][2]['adapter_weights'],[0.9])
            with self.assertRaises(ValueError):apply_diffusers_image_lora(pipeline,'sana-16',root,{'loraPath':str(file)})
            file.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                managed_qwen_lora(root,{'loraPath':str(file)})
            with self.assertRaises((ValueError,FileNotFoundError)):
                managed_qwen_lora(root,{'loraPath':'untrusted.bin'})
if __name__=='__main__':unittest.main()
