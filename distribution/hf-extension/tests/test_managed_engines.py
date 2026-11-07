import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import managed_engines as engines
from hf_bridge import BridgeState
from hf_runtime import checked_model_files, runtime_plan


class ManagedEngineTests(unittest.TestCase):
    def test_lfm3_uses_its_newer_architecture_runtime_without_changing_other_engines(self):
        for fields in ({'modelType': 'lfm3'}, {'modelType': 'lfm3_vl'},
                       {'repository': 'LiquidAI/LFM3-1.7B-GGUF'},
                       {'repository': 'LiquidAI/LFM3-VL-2B-GGUF'},
                       {'managedEngine': 'lfm3-cpu-v1'}):
            self.assertEqual(engines.engine_for({'format': 'gguf', **fields}), 'lfm3-cpu-v1')
        self.assertIsNone(engines.engine_for({'format': 'safetensors', 'modelType': 'lfm3'}))
        self.assertIsNone(engines.engine_for({'format': 'gguf', 'repository': 'unrelated/lfm3-lookalike'}))
        self.assertEqual(engines.engine_for({'format': 'gguf', 'modelType': 'qwen3', 'managedEngine': 'flint-cpu-v1'}), 'flint-cpu-v1')
        self.assertEqual(engines.definition('lfm3-cpu-v1')['version'], 'b11960')

    def test_spark_gguf_selects_the_pinned_engine_that_supports_its_architecture(self):
        for fields in ({'modelType': 'spark2_5'}, {'modelType': 'sparkx2_5'},
                       {'repository': 'XHToken/Spark-X2.6-4B-GGUF'}):
            self.assertEqual(engines.engine_for({'format': 'gguf', **fields}), 'flint-cpu-v1')
        self.assertIsNone(engines.engine_for({'format': 'safetensors', 'modelType': 'spark2_5'}))

    def test_standard_publisher_engine_uses_the_matching_vulkan_build_only_with_a_gpu(self):
        folder=self.root/'models/hub/fixture';folder.mkdir(parents=True)
        entry={'format':'gguf','modelType':'lfm3','contextTokens':8192,'localDirectory':str(folder),
               'files':[{'filename':'model.gguf','role':'model'}]}
        with patch.object(engines,'cuda_driver_version',return_value=0), patch.object(engines,'vulkan_gpu_available',return_value=True):
            plan=runtime_plan(entry,self.root,self.root/'resources')
            self.assertEqual(plan['publisherEngine'],'lfm3-vulkan-v1')
            self.assertEqual(plan['runtimeBackend'],'vulkan')
            self.assertEqual(plan['command'][plan['command'].index('--gpu-layers')+1],'auto')
            self.assertEqual(plan['command'][plan['command'].index('--fit')+1],'on')
            fallback=runtime_plan(entry,self.root,self.root/'resources',prefer_gpu=False)
            self.assertEqual(fallback['publisherEngine'],'lfm3-cpu-v1')
            self.assertEqual(fallback['command'][fallback['command'].index('--gpu-layers')+1],'0')
        with patch.object(engines,'cuda_driver_version',return_value=0), patch.object(engines,'vulkan_gpu_available',return_value=False):
            with self.assertRaisesRegex(ValueError,'requires a compatible CUDA or Vulkan GPU'):
                runtime_plan(entry,self.root,self.root/'resources')

    def test_automatic_and_manual_runtime_choices_respect_hardware_and_publisher(self):
        with patch.object(engines,'cuda_driver_version',return_value=13040), patch.object(engines,'vulkan_gpu_available',return_value=True):
            self.assertEqual(engines.select_engine('lfm3-cpu-v1','auto'),'lfm3-cuda13-v1')
            self.assertEqual(engines.select_engine('lfm3-cpu-v1','vulkan'),'lfm3-vulkan-v1')
            self.assertEqual(engines.select_engine('lfm3-cpu-v1','cpu'),'lfm3-cpu-v1')
            with self.assertRaisesRegex(ValueError,'publisher-specific'):
                engines.select_engine('raon-cpu-v1','cuda')
        with patch.object(engines,'cuda_driver_version',return_value=12040), patch.object(engines,'vulkan_gpu_available',return_value=True):
            self.assertEqual(engines.select_engine('flint-cpu-v1','auto'),'flint-cuda12-v1')
        with patch.object(engines,'cuda_driver_version',return_value=0), patch.object(engines,'vulkan_gpu_available',return_value=False):
            with self.assertRaisesRegex(ValueError,'requires a compatible CUDA or Vulkan GPU'):
                engines.select_engine('lfm3-cpu-v1','auto')
            with self.assertRaisesRegex(ValueError,'NVIDIA'):
                engines.select_engine('lfm3-cpu-v1','cuda')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def install(self, files, cancel=lambda: None, prefix=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, value in files:
                info = zipfile.ZipInfo('fixture')
                info.filename = name
                archive.writestr(info, value)
        data = stream.getvalue()
        pin = dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), label='fixture',
                   url='https://example.invalid/pinned.zip', source='fixture', revision='pinned')
        if prefix: pin['stripPrefix'] = prefix
        with patch.object(engines, 'definition', return_value=pin), patch.object(engines.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
            path = engines.ensure(self.root, 'raon-cpu-v1', cancel=cancel)
            self.assertTrue(engines.cached(self.root, 'raon-cpu-v1'))
            path.write_bytes(b'modified')
            self.assertFalse(engines.cached(self.root, 'raon-cpu-v1'))
        return path

    def test_pinned_install_checks_cached_executable_contents(self):
        self.install([('llama-server.exe', b'fixture-server'), ('backend.dll', b'fixture-backend')])

    def test_reviewed_layout_prefix_preserves_server_and_licenses(self):
        server = self.install([('bundle/llama-server.exe', b'fixture'), ('bundle/licenses/LICENSE', b'notice')], prefix='bundle/')
        self.assertEqual((server.parent/'licenses/LICENSE').read_bytes(), b'notice')

    def test_reviewed_layout_prefix_rejects_outside_members(self):
        with self.assertRaisesRegex(ValueError, 'layout prefix'):
            self.install([('bundle/llama-server.exe', b'fixture'), ('outside.dll', b'bad')], prefix='bundle/')

    def test_ternary_and_q8a64_select_their_actual_publisher_engines(self):
        for engine in ('prism-cpu-v1', 'velociq8-cpu-v1'):
            self.assertEqual(engines.engine_for({'format': 'gguf', 'managedEngine': engine}), engine)
            self.assertIsNone(engines.engine_for({'format': 'safetensors', 'managedEngine': engine}))

    def test_archive_cannot_escape_or_collide_on_windows(self):
        for unsafe in ('../outside.dll', '/absolute.dll', 'C:/outside.dll', 'folder\\outside.dll', 'LLAMA-SERVER.EXE'):
            with self.subTest(unsafe=unsafe), self.assertRaisesRegex(ValueError, 'unsafe|collid'):
                self.install([('llama-server.exe', b'fixture'), (unsafe, b'invalid')])
        self.assertFalse((self.root / 'outside.dll').exists())

    def test_cancellation_releases_lock_and_keeps_previous_engine(self):
        def cancel():
            raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.install([('llama-server.exe', b'fixture')], cancel)
        self.install([('llama-server.exe', b'retry')])

    def test_same_runtime_cannot_be_prepared_twice_and_lock_can_be_reused(self):
        path = self.root / 'engine.lock'
        with engines.runtime_lock(path):
            with self.assertRaisesRegex(RuntimeError, 'Another model preparation'):
                with engines.runtime_lock(path):
                    self.fail('Concurrent runtime acquired the same lock')
        with engines.runtime_lock(path):
            pass

    def test_engine_selection_requires_gguf_and_recognizes_hub_metadata(self):
        self.assertEqual(engines.engine_for({'format': 'gguf', 'modelType': 'flint'}), 'flint-cpu-v1')
        self.assertEqual(engines.engine_for({'format': 'gguf', 'modelType': 'raon1_mini'}), 'raon-cpu-v1')
        self.assertIsNone(engines.engine_for({'format': 'safetensors', 'modelType': 'flint'}))
        self.assertEqual(engines.engine_for({'format': 'gguf', 'managedEngine': 'flint-cpu-v1'}), 'flint-cpu-v1')
        self.assertIsNone(engines.engine_for({'format': 'gguf', 'managedEngine': 'arbitrary-path'}))
        self.assertEqual(engines.engine_for({'format': 'gguf', 'modelType': 'edgerock', 'managedEngine': 'flint-cpu-v1'}), 'edgerock-cpu-v1')


class PublisherCatalogTests(unittest.TestCase):
    def test_dory_cuda_uses_the_existing_native_receipt_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root/'resources'
            state = BridgeState(root, resources)
            self.addCleanup(state.close)
            data = b'verified Dory fixture'
            target = root/'models/library/dory-4b/Dory-Q8.gguf'
            target.parent.mkdir(parents=True)
            target.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            pin = {'repository':'test/Dory', 'revision':'a'*40, 'filename':target.name, 'bytes':len(data), 'sha256':digest, 'role':'model'}
            artifact = {**pin, 'id':'dory-pin', 'repo':pin['repository'], 'path':target.relative_to(root).as_posix()}
            native = {'id':'dory-q8', 'artifacts':[artifact['id']], 'modeProfiles':{'native':'dory-q8-native'},
                      'installation':{'adapterId':'dory-native-text-v1'}}
            state._native_bindings = ({native['id']:native}, {artifact['id']:artifact})
            receipt = root/'models/receipts/model-dory-q8-native.json'
            receipt.parent.mkdir()
            receipt.write_text(json.dumps(native['artifacts']))
            entry = {'id':'hf-'+'a'*24, 'repository':pin['repository'], 'revision':pin['revision'],
                     'nativeWeightProfile':native['id'], 'format':'gguf', 'category':'text', 'contextTokens':32768,
                     'managedEngine':'dory-cuda13-v1', 'bytes':len(data), 'files':[pin], 'installable':True}
            hydrated = state._hydrate(entry)
            self.assertEqual(hydrated['status'], 'installed')
            self.assertEqual(hydrated['installReceipt']['nativeProfileId'], 'dory-q8-native')
            self.assertEqual(hydrated['runtimeChoices'], ['auto', 'cuda'])
            self.assertEqual(checked_model_files(hydrated, root), [target.resolve(strict=True)])
            with patch.object(engines,'dory_cuda_devices',return_value=['CUDA1']):
                plan = runtime_plan(hydrated, root, resources)
            self.assertEqual(plan['runtimeBackend'], 'cuda')
            self.assertEqual(plan['command'][plan['command'].index('--device')+1], 'CUDA1')
            self.assertFalse((root/'models/hub'/entry['id']).exists())
            receipt.write_text('["different-artifact"]')
            with self.assertRaisesRegex(ValueError,'receipt'):
                checked_model_files(hydrated,root)

    def test_runtime_preference_is_persisted_and_does_not_remove_downloaded_files(self):
        source = Path(__file__).resolve().parents[3] / 'src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'
        entries = json.loads(source.read_text())['entries']
        entry = next(x for x in entries if x['format']=='gguf' and engines.engine_for(x)=='lfm3-cpu-v1')
        with tempfile.TemporaryDirectory() as temporary:
            state=BridgeState(temporary,temporary);self.addCleanup(state.close)
            state.entries[entry['id']]=entry
            receipt={'localDirectory':'kept','receiptPath':'kept'}
            with patch.object(state,'_receipt',return_value=receipt):
                state.entries[entry['id']]['runtimePreparation']={'runtimeReady':True,'receiptRevision':entry['revision']}
                result=state.runtime_backend(entry['id'],'cuda')
                self.assertEqual(result['runtimePreference'],'cuda')
                self.assertEqual(result['status'],'installed')
                self.assertFalse(result['runtimeReady'])
                self.assertEqual(state.entries[entry['id']]['runtimePreference'],'cuda')
                with self.assertRaises(ValueError):state.runtime_backend(entry['id'],'invalid')

    def test_seed_is_idempotent_and_preserves_verified_user_profile(self):
        source = Path(__file__).resolve().parents[3] / 'src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'
        seed = json.loads(source.read_text())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / 'resources/runtime-setup/hf-extension'
            resources.mkdir(parents=True)
            (resources / source.name).write_text(json.dumps(seed))
            state = BridgeState(root, root / 'resources')
            self.addCleanup(state.close)
            count = len(state.entries)
            self.assertEqual(count, len(seed['entries']))
            entry = next(iter(state.entries.values()))
            entry.update(profileId='kept-profile', providerId='kept-provider', runtimePreparation={'runtimeReady': True})
            state._seed_publisher_catalog()
            self.assertEqual(len(state.entries), count)
            kept = state.entries[entry['id']]
            self.assertEqual(kept['profileId'], 'kept-profile')
            self.assertEqual(kept['runtimePreparation'], {'runtimeReady': True})
            self.assertEqual({x['familyId'] for x in state.entries.values()}, {x['familyId'] for x in seed['entries']})
            self.assertEqual(len({x['id'] for x in state.entries.values()}), count)

    def test_ready_requires_current_engine_and_successful_echo_proof(self):
        source = Path(__file__).resolve().parents[3] / 'src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'
        entry = next(x for x in json.loads(source.read_text())['entries'] if x['format'] == 'gguf')
        with tempfile.TemporaryDirectory() as temporary:
            state = BridgeState(temporary, temporary)
            self.addCleanup(state.close)
            selected = engines.engine_for(entry)
            entry['runtimePreparation'] = {'runtimeReady': True, 'receiptRevision': entry['revision'],
                'engineArchiveSha256': engines.definition(selected)['sha256'], 'echoMode': 'main-context-v1'}
            with patch.object(state, '_receipt', return_value={'localDirectory': 'fixture', 'receiptPath': 'fixture'}):
                self.assertTrue(state._hydrate(entry)['runtimeReady'])
                entry['runtimePreparation']['echoMode'] = 'archive-retrieval'
                self.assertFalse(state._hydrate(entry)['runtimeReady'])
                entry['runtimePreparation']['echoMode'] = 'main-context-v1'
                entry['runtimePreparation']['engineArchiveSha256'] = '0' * 64
                self.assertFalse(state._hydrate(entry)['runtimeReady'])


if __name__ == '__main__':
    unittest.main()
