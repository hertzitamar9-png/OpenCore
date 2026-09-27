"""Offline identity contracts; these tiny files are not model evidence."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class TwinCoreIdentityTests(unittest.TestCase):
    def setUp(self):
        import record_twincore_identity as recorder
        self.recorder = recorder
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fusion = self.root / 'fusion'
        self.fusion.mkdir()
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.adapter = self.root / 'adapter'
        self.adapter.mkdir()
        self.native = self.fusion / 'native'
        self.native.mkdir()
        self.weights = {}
        for key in ('nanbeige', 'k2'):
            path = self.root / (key + '.gguf')
            path.write_bytes(('fixture-' + key).encode())
            self.weights[key] = path
        self.pins = {key: self.descriptor(path) for key, path in self.weights.items()}
        self.pins = {key: {name: row[name] for name in ('bytes', 'sha256')} for key, row in self.pins.items()}
        self.patcher = patch.object(recorder, 'CHECKPOINTS', self.pins)
        self.patcher.start()
        self.sources = {}
        for name in recorder.COUPLING_SOURCES:
            path = self.fusion / name
            path.write_bytes(('fixture source: ' + name).encode())
            self.sources[name] = self.descriptor(path)['sha256']
        (self.native / 'twincore.cpp').write_bytes(b'#include "head_projection.h"\n')
        (self.native / 'head_projection.h').write_bytes(b'#pragma once\n')
        (self.native / 'CMakeLists.txt').write_bytes(b'project(twincore)\n')
        libraries, self.loaded = [], {}
        for name in recorder.REQUIRED_LIBRARIES:
            base = self.native if name == 'twincore.dll' else self.runtime
            path = base / name
            path.write_bytes(('fixture DLL: ' + name).encode())
            row = self.descriptor(path)
            libraries.append({'path': name, 'scope': 'native' if name == 'twincore.dll' else 'runtime',
                              'bytes': row['bytes'], 'sha256': row['sha256']})
            self.loaded[name.lower()] = row
        native_sources = [{'path': name, 'scope': 'source', 'bytes': row['bytes'], 'sha256': row['sha256']}
                          for name in ('twincore.cpp', 'head_projection.h', 'CMakeLists.txt')
                          for row in (self.descriptor(self.native / name),)]
        self.build = {'schema': 1, 'abi': 1, 'source_commit': recorder.SOURCE_COMMIT,
                      'libraries': libraries, 'sources': native_sources}
        (self.native / 'build-info.json').write_text(json.dumps(self.build), encoding='utf-8')
        self.configuration = recorder.execution_configuration(context=8192, rank=256, seed=7, recompute=False)
        self.binding = {'schema': 1, 'checkpoints': self.pins, 'native': self.build,
                        'coupling_sources': self.sources, 'geometry': {'rank': 256}}
        self.placement = [{'head_on_gpu': True, 'physical_matrix_layers': n, 'gpu_matrix_layers': n}
                          for n in (22, 36)]
        self.qualification = {'schema': 2, 'status': 'full_q6_resource_probe_passed', 'gpu_qualified': True,
            'models_loaded': True, 'models_released': True, 'configuration': self.configuration,
            'gpu_before': {'uuid': 'GPU-fixture'}, 'binding': self.binding, 'placement': self.placement,
            'probe': {'finite_bridge_gradients': True, 'complete_target': True, 'tokens': 1}}
        self.qpath = self.root / 'qualification.json'
        self.qpath.write_text(json.dumps(self.qualification), encoding='utf-8')
        tensor = self.adapter / 'bridge.safetensors'
        tensor.write_bytes(b'fixture trained tensors')
        self.receipt = {'schema': 1, 'checkpoint': False, 'binding': self.binding,
            'tensor_fingerprint': 'b' * 64, 'files': {'bridge.safetensors': self.descriptor(tensor)['sha256']},
            'training': {'steps': 8, 'tokens': 24, 'initial_bridge_sha256': 'a' * 64,
                'validation_is_current': True, 'validation_step': 8,
                'validation': {'tokens': 12, 'loss': 1.2, 'baseline_loss': 1.3}}}
        self.rewrite_receipt()
        self.model = recorder.NAMES['twincore-kv']
        self.health = {'ready': True, 'status': 'ok', 'model': self.model}
        self.props = {'model': self.model, 'n_ctx': 8192, 'configuration': self.configuration,
            'runtime_identity': {'precision': 'Q6_K', 'binding': self.binding,
                'adapter_receipt_sha256': self.receipt['receipt_sha256'],
                'qualification_sha256': recorder.file_hash(self.qpath), 'gpu_uuid': 'GPU-fixture',
                'placement': self.placement, 'loaded_libraries': self.loaded}}
        self.args = SimpleNamespace(profile='twincore-kv', context=8192, rank=256, seed=7,
            nanbeige=self.weights['nanbeige'], k2=self.weights['k2'], adapter=self.adapter,
            qualification=self.qpath, dll=self.native / 'twincore.dll', runtime=self.runtime,
            url='http://127.0.0.1:1')

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def descriptor(self, path):
        data = path.read_bytes()
        return {'path': str(path.resolve()), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def rewrite_receipt(self):
        self.receipt.pop('receipt_sha256', None)
        self.receipt['receipt_sha256'] = hashlib.sha256(self.recorder.canonical(self.receipt)).hexdigest()
        (self.adapter / 'receipt.json').write_text(json.dumps(self.receipt), encoding='utf-8')

    def record(self):
        fetch = lambda url: copy.deepcopy(self.health if url.endswith('/health') else self.props)
        return self.recorder.build_identity(self.args, fetch=fetch, fusion_root=self.fusion)

    def test_binds_both_weights_trained_adapter_qualification_and_native_sources(self):
        result = self.record()
        self.assertEqual(result['model'], self.model)
        self.assertEqual(result['complete_towers'], 2)
        self.assertEqual(result['candidate_budget'], 1)
        self.assertTrue(result['coupling_trained'])
        artifacts = {Path(row['path']).name for row in result['artifacts']}
        self.assertTrue({'nanbeige.gguf', 'k2.gguf', 'bridge.safetensors', 'receipt.json', 'qualification.json'} <= artifacts)
        sources = {Path(row['path']).name for row in result['runtime_files']}
        self.assertTrue({'head_projection.h', 'twincore.cpp', 'CMakeLists.txt', 'twincore.dll'} <= sources)

    def test_refuses_an_adapter_different_from_the_running_endpoint(self):
        self.props['runtime_identity']['adapter_receipt_sha256'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'adapter'):
            self.record()

    def test_echo_records_fresh_conversation_isolation_for_every_benchmark_sample(self):
        self.args.profile = 'twincore-echo'
        configuration = self.recorder.execution_configuration(context=8192, rank=256, seed=7, recompute=True)
        self.qualification['configuration'] = configuration
        self.qpath.write_text(json.dumps(self.qualification), encoding='utf-8')
        self.health['model'] = self.props['model'] = self.recorder.NAMES['twincore-echo']
        self.props['configuration'] = configuration
        self.props['runtime_identity']['qualification_sha256'] = self.recorder.file_hash(self.qpath)
        self.assertEqual(self.record()['request_isolation'], 'fresh_conversation_per_sample')

    def test_refuses_untrained_tensors_even_with_a_recomputed_receipt(self):
        self.receipt['training']['initial_bridge_sha256'] = self.receipt['tensor_fingerprint']
        self.rewrite_receipt()
        self.props['runtime_identity']['adapter_receipt_sha256'] = self.receipt['receipt_sha256']
        with self.assertRaisesRegex(ValueError, 'trained'):
            self.record()

    def test_refuses_a_resume_only_checkpoint(self):
        self.receipt['checkpoint'] = True
        self.rewrite_receipt()
        with self.assertRaisesRegex(ValueError, 'checkpoint|completed'):
            self.record()

    def test_refuses_stale_validation(self):
        self.receipt['training']['validation_step'] = 7
        self.rewrite_receipt()
        with self.assertRaisesRegex(ValueError, 'validation'):
            self.record()

    def test_refuses_substituted_weight_content(self):
        self.weights['k2'].write_bytes(b'changed model')
        with self.assertRaisesRegex(ValueError, 'checkpoint'):
            self.record()

    def test_refuses_changed_coupling_source(self):
        (self.fusion / 'bridge.py').write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError, 'source'):
            self.record()

    def test_refuses_native_sources_different_from_the_compiled_build(self):
        (self.native / 'head_projection.h').write_bytes(b'changed compiled source')
        with self.assertRaisesRegex(ValueError, 'source'):
            self.record()

    def test_refuses_partial_current_gpu_placement(self):
        self.props['runtime_identity']['placement'][0]['gpu_matrix_layers'] = 21
        with self.assertRaisesRegex(ValueError, 'GPU'):
            self.record()

    def test_refuses_a_substituted_loaded_numerical_library(self):
        self.props['runtime_identity']['loaded_libraries']['ggml-cuda.dll']['sha256'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'library'):
            self.record()

    def test_refuses_a_different_context_configuration(self):
        self.props['n_ctx'] = 1024
        with self.assertRaisesRegex(ValueError, 'configuration'):
            self.record()

    def test_refuses_unready_or_non_loopback_endpoints(self):
        self.health['ready'] = False
        with self.assertRaisesRegex(ValueError, 'ready'):
            self.record()
        self.args.url = 'https://example.com'
        with self.assertRaisesRegex(ValueError, 'loopback'):
            self.record()


if __name__ == '__main__':
    unittest.main()
