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


class ManagedEngineTests(unittest.TestCase):
    def test_spark_gguf_selects_the_pinned_engine_that_supports_its_architecture(self):
        for fields in ({'modelType': 'spark2_5'}, {'modelType': 'sparkx2_5'},
                       {'repository': 'XHToken/Spark-X2.6-4B-GGUF'}):
            self.assertEqual(engines.engine_for({'format': 'gguf', **fields}), 'flint-cpu-v1')
        self.assertIsNone(engines.engine_for({'format': 'safetensors', 'modelType': 'spark2_5'}))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def install(self, files, cancel=lambda: None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, value in files:
                info = zipfile.ZipInfo('fixture')
                info.filename = name
                archive.writestr(info, value)
        data = stream.getvalue()
        pin = dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), label='fixture',
                   url='https://example.invalid/pinned.zip', source='fixture', revision='pinned')
        with patch.object(engines, 'definition', return_value=pin), patch.object(engines.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
            path = engines.ensure(self.root, 'raon-cpu-v1', cancel=cancel)
            self.assertTrue(engines.cached(self.root, 'raon-cpu-v1'))
            path.write_bytes(b'modified')
            self.assertFalse(engines.cached(self.root, 'raon-cpu-v1'))
        return path

    def test_pinned_install_checks_cached_executable_contents(self):
        self.install([('llama-server.exe', b'fixture-server'), ('backend.dll', b'fixture-backend')])

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


class PublisherCatalogTests(unittest.TestCase):
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
            self.assertEqual(len({x['familyId'] for x in state.entries.values()}), 7)
            self.assertEqual(len({x['id'] for x in state.entries.values()}), count)

    def test_ready_requires_current_engine_and_successful_echo_proof(self):
        source = Path(__file__).resolve().parents[3] / 'src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'
        entry = next(x for x in json.loads(source.read_text())['entries'] if x['format'] == 'gguf')
        with tempfile.TemporaryDirectory() as temporary:
            state = BridgeState(temporary, temporary)
            self.addCleanup(state.close)
            selected = engines.engine_for(entry)
            entry['runtimePreparation'] = {'runtimeReady': True, 'receiptRevision': entry['revision'],
                'engineArchiveSha256': engines.definition(selected)['sha256'], 'echoMode': 'archive-retrieval'}
            with patch.object(state, '_receipt', return_value={'localDirectory': 'fixture', 'receiptPath': 'fixture'}):
                self.assertTrue(state._hydrate(entry)['runtimeReady'])
                entry['runtimePreparation']['echoMode'] = 'old-proof'
                self.assertFalse(state._hydrate(entry)['runtimeReady'])
                entry['runtimePreparation']['echoMode'] = 'archive-retrieval'
                entry['runtimePreparation']['engineArchiveSha256'] = '0' * 64
                self.assertFalse(state._hydrate(entry)['runtimeReady'])


if __name__ == '__main__':
    unittest.main()
