"""Regression checks for publisher-matched image adapters and pipeline admission."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from comfy_worker import managed_image_lora
from asset_worker import apply_diffusers_image_lora


class ImageAdapterTests(unittest.TestCase):
    def make_adapter(self, root, base='Tongyi-MAI/Z-Image-Turbo'):
        folder=root/'models/hub/hf-example'
        folder.mkdir(parents=True)
        path=folder/'style.safetensors'
        path.write_bytes(b'adapter test data')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        identity={'id':'hf-example','repository':'artist/z-style','revision':'a'*40}
        pin={'filename':path.name,'role':'model','bytes':path.stat().st_size,'sha256':digest}
        entry={**identity,'baseRepository':base,'packageKind':'adapter','format':'lora','files':[pin]}
        (root/'models/hub-imports.json').write_text(json.dumps({'entries':[entry]}))
        (folder/'install-receipt.json').write_text(json.dumps({**identity,'files':[{**pin,'actualSha256':digest}]}))
        return path

    def test_other_publishers_can_use_matching_base_and_intact_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=self.make_adapter(root)
            self.assertEqual(managed_image_lora(root,{'loraPath':str(path)},'Tongyi-MAI/Z-Image-Turbo'),path.resolve())
            with self.assertRaisesRegex(ValueError,'base'):
                managed_image_lora(root,{'loraPath':str(path)},'Qwen/Qwen-Image-2.1')
            path.write_bytes(b'changed adapter!!!')
            with self.assertRaises(ValueError):
                managed_image_lora(root,{'loraPath':str(path)},'Tongyi-MAI/Z-Image-Turbo')

    def test_receipt_cannot_substitute_a_different_catalog_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=self.make_adapter(root)
            path.write_bytes(b'a replacement adapter')
            receipt=json.loads((path.parent/'install-receipt.json').read_text())
            receipt['files'][0].update(bytes=path.stat().st_size,actualSha256=hashlib.sha256(path.read_bytes()).hexdigest(),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            (path.parent/'install-receipt.json').write_text(json.dumps(receipt))
            with self.assertRaises(ValueError):
                managed_image_lora(root,{'loraPath':str(path)},'Tongyi-MAI/Z-Image-Turbo')

    def test_diffusers_loader_applies_and_activates_adapter_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=self.make_adapter(root)
            class Pipeline:
                def __init__(self):self.calls=[]
                def load_lora_weights(self,*args,**kw):self.calls.append(('load',args,kw))
                def set_adapters(self,*args,**kw):self.calls.append(('set',args,kw))
                def get_active_adapters(self):return ['opencore-image-lora']
            pipeline=Pipeline()
            self.assertTrue(apply_diffusers_image_lora(pipeline,'z-image-turbo',root,{'loraPath':str(path),'loraStrength':0.6}))
            self.assertTrue(pipeline.calls[0][2]['local_files_only'])
            self.assertEqual(pipeline.calls[1][2]['adapter_weights'],[0.6])
            pipeline.get_active_adapters=lambda:[]
            with self.assertRaisesRegex(RuntimeError,'activate'):
                apply_diffusers_image_lora(pipeline,'z-image-turbo',root,{'loraPath':str(path)})


if __name__=='__main__':unittest.main()
