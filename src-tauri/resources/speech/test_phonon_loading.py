"""Small real Parakeet fixture; no downloaded weights or GPU execution."""
import contextlib
import copy
import importlib.util
import io
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace


class WorkerProgressTests(unittest.TestCase):
    def test_startup_announces_runtime_import_before_import_failure(self):
        import phonon_worker
        output = io.StringIO()
        with patch.object(sys, 'argv', ['phonon_worker', '--model', '.', '--idle-mode', 'cold']), \
                patch.dict(sys.modules, {'av': None}), contextlib.redirect_stdout(output):
            self.assertEqual(phonon_worker.main(), 1)
        import json
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(messages[0], {'progress': 'starting-runtime'})
        self.assertIn('error', messages[-1])

    def test_features_use_selected_model_precision_and_masks_keep_integer_dtype(self):
        import phonon_worker
        calls = []
        class Tensor:
            def __init__(self, name):
                self.name = name
            def to(self, *args, **kwargs):
                calls.append((self.name, args, kwargs))
                return self
        processor = SimpleNamespace(
            batch_decode=lambda *args, **kwargs: ['hello'],
        )
        class Processor:
            def __call__(self, *args, **kwargs):
                return {'input_features': Tensor('features'), 'attention_mask': Tensor('mask')}
            batch_decode = processor.batch_decode
        model = SimpleNamespace(dtype='bfloat16', generate=lambda **kwargs: [])
        torch = SimpleNamespace(inference_mode=contextlib.nullcontext, float32='float32')
        self.assertEqual(phonon_worker.transcribe(model, Processor(), [0.0], None, torch, 'cpu'), 'hello')
        self.assertEqual(calls[0], ('features', (), {'device': 'cpu', 'dtype': 'bfloat16'}))
        self.assertEqual(calls[1], ('mask', ('cpu',), {}))


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('transformers'),
                     'Run with the pinned Phonon runtime for real CPU kernel qualification')
class ParakeetLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        from transformers import ParakeetForTDT, ParakeetTDTConfig
        torch.set_num_threads(2)
        cls.torch, cls.model_class = torch, ParakeetForTDT
        cls.config = ParakeetTDTConfig(
            encoder_config=dict(hidden_size=8, num_hidden_layers=1, num_attention_heads=2,
                intermediate_size=16, subsampling_conv_channels=4, num_mel_bins=8,
                subsampling_factor=2, conv_kernel_size=3, dropout=0, layerdrop=0,
                attention_dropout=0, activation_dropout=0),
            vocab_size=8, decoder_hidden_size=8, num_decoder_layers=1,
            blank_token_id=7, pad_token_id=0, max_symbols_per_step=2,
        )
        torch.manual_seed(1)
        cls.original = ParakeetForTDT(cls.config).eval()
        cls.state = {k: v.clone() for k, v in cls.original.state_dict().items()}

    def test_assigned_weights_and_nonpersistent_buffers_match_reference_both_precisions(self):
        from phonon_loading import materialize_model
        torch = self.torch
        for dtype in (torch.float32, torch.bfloat16):
            with self.subTest(dtype=dtype):
                state = {k: v.clone() for k, v in self.state.items()}
                assigned = materialize_model(self.config, state, dtype)
                reference = self.model_class(self.config).eval()
                reference.load_state_dict(self.state, strict=True)
                reference.to(dtype)
                assigned.generation_config.decoder_start_token_id = 7
                assigned.generation_config.eos_token_id = 3
                assigned.generation_config.pad_token_id = 0
                assigned.generation_config.suppress_tokens = list(range(self.config.vocab_size,
                    self.config.vocab_size + len(self.config.durations)))
                assigned.generation_config.max_new_tokens = 8
                reference.generation_config = copy.deepcopy(assigned.generation_config)
                for name, value in assigned.state_dict().items():
                    self.assertTrue(torch.equal(value, reference.state_dict()[name]), name)
                    self.assertEqual(value.dtype, reference.state_dict()[name].dtype, name)
                for name, value in assigned.named_buffers():
                    self.assertFalse(value.is_meta, name)
                    self.assertTrue(torch.equal(value, dict(reference.named_buffers())[name]), name)
                features = torch.randn(1, 24, 8).to(dtype)
                mask = torch.ones(1, 24, dtype=torch.long)
                with torch.inference_mode():
                    output = assigned(input_features=features, attention_mask=mask,
                                      decoder_input_ids=torch.tensor([[1]]))
                    expected = reference(input_features=features, attention_mask=mask,
                                         decoder_input_ids=torch.tensor([[1]]))
                    self.assertEqual(output.logits.dtype, dtype)
                    self.assertTrue(torch.equal(output.logits, expected.logits))
                    decoded = assigned.generate(input_features=features, attention_mask=mask)
                    expected_decoded = reference.generate(input_features=features, attention_mask=mask)
                    sequences = getattr(decoded, 'sequences', decoded)
                    self.assertFalse(sequences.is_cuda)
                    self.assertTrue(torch.equal(sequences, getattr(expected_decoded, 'sequences', expected_decoded)))

    def test_missing_weight_is_rejected_instead_of_uninitialized_tensor(self):
        from phonon_loading import materialize_model
        state = {k: v.clone() for k, v in self.state.items()}
        state.pop(next(iter(state)))
        with self.assertRaisesRegex(RuntimeError, 'Missing key'):
            materialize_model(self.config, state, self.torch.float32)


if __name__ == '__main__':
    unittest.main()
