import importlib.util,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Managed image runtime supplies Torch')
class ConditioningTests(unittest.TestCase):
    def test_checkpoint_loader_owns_tensor_memory_without_mapping_the_checkpoint(self):
        import json,struct,torch
        import qwen_phase_nodes as module
        self.assertTrue(hasattr(module,'load_owned_safetensors'), 'The no-mmap loader must not retain a second mapped copy of the encoder')
        header={'__metadata__':{'format':'pt'},'a':{'dtype':'I8','shape':[2,2],'data_offsets':[0,4]},'b':{'dtype':'F32','shape':[2],'data_offsets':[4,12]}}
        encoded=json.dumps(header).encode();body=bytes([1,2,3,4])+struct.pack('<ff',1.5,2.5)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model.safetensors';path.write_bytes(struct.pack('<Q',len(encoded))+encoded+body)
            values,metadata=module.load_owned_safetensors(path)
            path.unlink()  # A retained Windows mapping would prevent deletion.
            self.assertTrue(torch.equal(values['a'],torch.tensor([[1,2],[3,4]],dtype=torch.int8)))
            self.assertTrue(torch.equal(values['b'],torch.tensor([1.5,2.5])))
            self.assertEqual(metadata,{'format':'pt'})

    def test_checkpoint_loader_rejects_overlapping_and_out_of_bounds_tensor_ranges(self):
        import json,struct
        import qwen_phase_nodes as module
        self.assertTrue(hasattr(module,'load_owned_safetensors'), 'Validate tensor ranges before allocating')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model.safetensors'
            for bounds in ([0,100],[0,3]):
                header=json.dumps({'a':{'dtype':'F32','shape':[1],'data_offsets':bounds}}).encode()
                path.write_bytes(struct.pack('<Q',len(header))+header+b'1234')
                with self.assertRaises(ValueError):module.load_owned_safetensors(path)

    def test_prompt_roundtrip_preserves_values_and_uses_the_qwen_21_latent_shape(self):
        import torch
        from qwen_phase_nodes import SaveConditioning,LoadConditioning
        positive=[[torch.arange(6,dtype=torch.float32).reshape(1,2,3),{'reference_latents':[torch.ones(1,64,4,6)]}]]
        negative=[[torch.zeros(1,2,3),{}]];reference={'samples':torch.zeros(1,64,4,6)}
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'OPENCORE_IMAGE_PHASE_DIR':directory}):
            SaveConditioning().save(positive,negative,reference)
            path=Path(directory)/'prompt-conditioning.pt'
            self.assertLess(path.stat().st_size,30_000)
            pos,neg,latent=LoadConditioning().load(512,384,False)
            self.assertTrue(torch.equal(pos[0][0],positive[0][0]))
            self.assertTrue(torch.equal(neg[0][0],negative[0][0]))
            self.assertEqual(tuple(latent['samples'].shape),(1,64,24,32))
            _,_,latent=LoadConditioning().load(512,384,True)
            self.assertEqual(tuple(latent['samples'].shape),(1,64,4,6))

    def test_no_model_or_executable_object_can_be_saved_as_conditioning(self):
        from qwen_phase_nodes import cpu_tensors
        with self.assertRaisesRegex(ValueError,'Unsupported prompt conditioning'):
            cpu_tensors({'model':object()})


if __name__=='__main__':unittest.main()
