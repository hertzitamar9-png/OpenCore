"""Cancellation uses the real bounded loopback transfer and never removes completed weights."""
import json
import hashlib
import os
import time
import unittest
import test_bridge as fixture


class InstallCancelTests(unittest.TestCase):
    setUp = fixture.BridgeTests.setUp
    request = fixture.BridgeTests.request
    entry = fixture.BridgeTests.entry
    seed = fixture.BridgeTests.seed

    def test_cancel_stops_worker_removes_only_matching_partials_and_retains_completed_files(self):
        entry = self.seed()
        self.hub.slow = True
        directory = self.state.downloader.directory(entry)
        directory.mkdir(parents=True)
        completed = directory / 'completed-shared.gguf'
        completed.write_bytes(b'completed shared checkpoint')
        completed_pin = {**entry['files'][0], 'filename': completed.name, 'bytes': completed.stat().st_size,
                         'sha256': hashlib.sha256(completed.read_bytes()).hexdigest()}
        entry['files'].insert(0, completed_pin); entry['bytes'] += completed_pin['bytes']
        unrelated = directory / 'unrelated.gguf.partial'
        unrelated.write_bytes(b'not this job')
        response = self.request('POST', '/api/install', {'id': entry['id']})
        self.assertEqual(response[0], 202)
        job_id = response[2]['jobId']
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self.state.progress.get('bytesDone', 0) <= completed_pin['bytes']:
            time.sleep(.005)
        self.assertGreater(self.state.progress.get('bytesDone', 0), completed_pin['bytes'])
        self.assertEqual(self.request('POST', '/api/cancel', {'jobId': job_id}, secret=None)[0], 401)
        self.assertEqual(self.request('POST', '/api/cancel', {'jobId': 'stale-job'})[0], 409)
        self.assertEqual(self.request('POST', '/api/cancel', {'jobId': job_id})[0], 202)
        self.state.worker.join(3)
        self.assertFalse(self.state.worker.is_alive())
        self.assertEqual(self.state.progress['status'], 'cancelled')
        pin = entry['files'][1]
        self.assertFalse((directory / (pin['filename'] + '.partial')).exists())
        self.assertFalse((directory / (pin['filename'] + '.partial.json')).exists())
        self.assertEqual(completed.read_bytes(), b'completed shared checkpoint')
        self.assertEqual(unrelated.read_bytes(), b'not this job')
        self.assertFalse(self.runtime.stopped, 'Cancelling a download must not stop an unrelated runtime')

    def test_pause_retains_resumable_partial_and_cancel_after_pause_discards_it(self):
        entry = self.seed(); self.hub.slow = True
        job = self.request('POST', '/api/install', {'id': entry['id']})[2]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self.state.progress.get('bytesDone', 0) == 0:
            time.sleep(.005)
        self.request('POST', '/api/pause', {'jobId': job['jobId']})
        self.state.worker.join(3)
        partial = self.state.downloader.directory(entry) / (entry['files'][0]['filename'] + '.partial')
        self.assertTrue(partial.exists())
        self.assertEqual(self.request('POST', '/api/cancel', {'jobId': job['jobId']})[0], 202)
        self.assertFalse(partial.exists())
        self.assertEqual(self.state.progress['status'], 'cancelled')

    def test_partial_cleanup_refuses_foreign_pins_and_shared_hardlinks(self):
        entry = self.seed()
        directory = self.state.downloader.directory(entry)
        pin = entry['files'][0]
        partial = directory / (pin['filename'] + '.partial')
        marker = directory / (pin['filename'] + '.partial.json')
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b'owned bytes')
        marker.write_text(json.dumps({**pin, 'revision': 'f' * 40}))
        self.assertEqual(self.state.downloader.discard_partials(entry)['removedBytes'], 0)
        self.assertEqual(partial.read_bytes(), b'owned bytes')
        marker.write_text(json.dumps(pin))
        self.assertEqual(self.state.downloader.discard_partials(entry)['removedBytes'], len(b'owned bytes'))
        completed = directory / 'shared.gguf'
        completed.write_bytes(b'completed shared file')
        os.link(completed, partial)
        marker.write_text(json.dumps(pin))
        self.assertEqual(self.state.downloader.discard_partials(entry)['removedBytes'], 0)
        self.assertTrue(partial.exists())
        self.assertEqual(completed.read_bytes(), b'completed shared file')

    def test_native_cleanup_is_authenticated_and_removes_only_manifest_partial_paths(self):
        source = self.root / 'runtime-setup/hf-extension/native-model-bindings.json'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps({'models': [{'id': 'native-one', 'artifacts': ['owned']}],
                                     'artifacts': [{'id': 'owned', 'path': 'models/library/one/model.gguf', 'bytes': 100}]}))
        target = self.root / 'models/library/one/model.gguf'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'completed weights')
        partial = target.with_name(target.name + '.partial'); partial.write_bytes(b'partial')
        unrelated = target.parent / 'other.gguf.partial'; unrelated.write_bytes(b'unrelated')
        self.assertEqual(self.request('POST', '/api/native/cancel', {'id': 'native-one'}, secret=None)[0], 401)
        self.assertEqual(self.request('POST', '/api/native/cancel', {'id': 'unknown'})[0], 400)
        result = self.request('POST', '/api/native/cancel', {'id': 'native-one'})
        self.assertEqual(result[0], 200); self.assertEqual(result[2]['removedBytes'], 7)
        self.assertFalse(partial.exists())
        self.assertEqual(target.read_bytes(), b'completed weights')
        self.assertEqual(unrelated.read_bytes(), b'unrelated')
