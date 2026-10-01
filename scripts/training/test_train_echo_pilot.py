import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
import torch
import numpy as np
from train_echo_pilot import export_candidate, example_tokens, parse_args


class PilotIntegrity(unittest.TestCase):
    def test_retry_budget_is_explicit_and_rejects_unapproved_limits(self):
        common=['--folder','attempt','--research-root','research']
        self.assertEqual(parse_args(common).mimo_max_steps,16)
        self.assertEqual(parse_args(common+['--mimo-max-steps','32']).mimo_max_steps,32)
        with self.assertRaises(SystemExit):
            parse_args(common+['--mimo-max-steps','100'])

    def test_export_only_changes_allowed_bf16_composition_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source.gguf';target=root/'candidate.gguf'
            original=b'original-header'+b'\x00\x3f'*6+b'frozen-backbone'
            source.write_bytes(original);tensors=[];updates={}
            for index,(prefix,key) in enumerate((p,k) for p in ('gate','up','down') for k in ('seed_scale','shared_coeff')):
                name=f'opencore.{prefix}.{key}';offset=len(b'original-header')+index*2
                tensors.append(SimpleNamespace(name=name,data_offset=offset,n_bytes=2,n_elements=1,
                    tensor_type=SimpleNamespace(name='BF16'),data=np.frombuffer(original[offset:offset+2],dtype=np.uint8)))
                updates[name]=torch.tensor([2.])
            tensors.append(SimpleNamespace(name='backbone',data_offset=len(b'original-header')+12,n_bytes=len(b'frozen-backbone'),
                data=np.frombuffer(b'frozen-backbone',dtype=np.uint8)))
            report=export_candidate(source,target,SimpleNamespace(tensors=tensors),updates)
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(len(report['changed_tensors']),6)
            self.assertEqual(target.read_bytes()[:len(b'original-header')],b'original-header')
            self.assertTrue(target.read_bytes().endswith(b'frozen-backbone'))

    def test_targets_are_predicted_from_preceding_positions_and_budget_is_bounded(self):
        class Tokenizer:
            def apply_chat_template(self,*args,**kwargs): return list(range(300))
            def encode(self,*args,**kwargs): return list(range(500,600))
        record={'messages':[{'role':'user','content':'task'},{'role':'assistant','content':'solution'}]}
        tokens,positions,targets=example_tokens(Tokenizer(),record,'cpu')
        self.assertEqual(tokens.shape,(1,256))
        self.assertEqual(positions[0].item(),191)
        self.assertEqual(targets[0].item(),500)
        torch.testing.assert_close(tokens[0,positions+1],targets)

    def test_fast_tokenizer_chat_template_returns_integer_ids_for_training(self):
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import PreTrainedTokenizerFast
        backend=Tokenizer(models.WordLevel({'[UNK]':0,'fix':1,'done':2},unk_token='[UNK]'))
        backend.pre_tokenizer=pre_tokenizers.Whitespace()
        tokenizer=PreTrainedTokenizerFast(tokenizer_object=backend,unk_token='[UNK]',
            chat_template="{{ messages[0]['content'] }}")
        record={'messages':[{'role':'user','content':'fix'},{'role':'assistant','content':'done'}]}
        tokens,positions,targets=example_tokens(tokenizer,record,'cpu')
        self.assertEqual(tokens.tolist(),[[1,2]])
        self.assertEqual(positions.tolist(),[0])
        self.assertEqual(targets.tolist(),[2])


if __name__=='__main__': unittest.main()
