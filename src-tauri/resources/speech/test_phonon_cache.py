"""Small real tensors cover cache validity and atomic replacement without weights."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('torch'), 'PyTorch is required for dense cache tests')
class DenseCacheTests(unittest.TestCase):
    def setUp(self):
        import torch
        from phonon_cache import DenseCache
        self.torch = torch
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.identity = {'checkpointSha256': 'fixture', 'configSha256': 'config', 'precision': 'bf16'}
        self.spec = {'weight': {'shape': [2, 3], 'dtype': 'bfloat16'}, 'count': {'shape': [], 'dtype': 'int64'}}
        self.state = {'weight': torch.arange(6).reshape(2, 3).to(torch.bfloat16), 'count': torch.tensor(1)}
        self.cache = DenseCache(self.root, 'bf16', self.identity, self.spec)

    def tearDown(self): self.temp.cleanup()

    def test_cache_hit_uses_mmap_weights_only_and_preserves_every_tensor(self):
        self.assertIsNone(self.cache.read())
        self.cache.write(self.state, {'container_records': 2})
        with patch.object(self.torch, 'load', wraps=self.torch.load) as load:
            state, receipt = self.cache.read()
        self.assertTrue(load.call_args.kwargs['mmap'])
        self.assertTrue(load.call_args.kwargs['weights_only'])
        self.assertEqual(load.call_args.kwargs['map_location'], 'cpu')
        for name, value in state.items():
            self.assertEqual(value.dtype, self.state[name].dtype)
            self.assertTrue(self.torch.equal(value, self.state[name]), name)
        self.assertEqual(receipt['container_records'], 2)
        del state

    def test_source_or_precision_change_does_not_reuse_cache(self):
        from phonon_cache import DenseCache
        self.cache.write(self.state, {})
        changed = DenseCache(self.root, 'bf16', {**self.identity, 'configSha256': 'changed'}, self.spec)
        self.assertIsNone(changed.read())
        other_precision = DenseCache(self.root, 'fp32', {**self.identity, 'precision': 'fp32'}, self.spec)
        self.assertIsNone(other_precision.read())

    def test_corrupted_bytes_fall_back_without_loading_tensors(self):
        self.cache.write(self.state, {})
        self.cache.weights.write_bytes(b'corrupted cache')
        with patch.object(self.torch, 'load', wraps=self.torch.load) as load:
            self.assertIsNone(self.cache.read())
            load.assert_not_called()

    def test_tensor_shapes_keys_and_dtypes_are_verified_even_with_matching_file_hash(self):
        from phonon_cache import digest
        for bad in ({**self.state, 'weight': self.state['weight'].float()},
                    {**self.state, 'weight': self.state['weight'].reshape(3, 2)},
                    {'weight': self.state['weight']}):
            with self.subTest(keys=list(bad)):
                self.cache.write(self.state, {})
                self.torch.save(bad, self.cache.weights)
                manifest = json.loads(self.cache.manifest.read_text())
                manifest['weightsSha256'] = digest(self.cache.weights)
                manifest['weightsBytes'] = self.cache.weights.stat().st_size
                self.cache.manifest.write_text(json.dumps(manifest))
                self.assertIsNone(self.cache.read())

    def test_failed_write_keeps_previous_cache_and_removes_own_temporary_file(self):
        self.cache.write(self.state, {})
        original = self.cache.weights.read_bytes()
        def partial(state, destination):
            destination.write(b'partial')
            raise OSError('disk full')
        with patch.object(self.torch, 'save', side_effect=partial):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.cache.write(self.state, {})
        self.assertEqual(self.cache.weights.read_bytes(), original)
        self.assertFalse(list(self.root.glob('*.tmp')))
        result = self.cache.read()
        self.assertIsNotNone(result)
        del result


if __name__ == '__main__': unittest.main()
