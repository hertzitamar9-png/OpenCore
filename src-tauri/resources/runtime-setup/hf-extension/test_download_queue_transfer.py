"""Bounded loopback transfer and native receipt integration, no remote weights."""
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from hf_bridge import BridgeServer, BridgeState, RuntimeUnavailable
from hf_download import HubClient


DATA = b'OpenCore queue fixture\n' * 16384
COMMIT = 'a' * 40
HUB_ID = 'hf-' + 'a' * 24


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        self.server.requests.append(self.path)
        with self.server.guard:
            self.server.live += 1
            self.server.max_live = max(self.server.live, self.server.max_live)
        data = self.server.payloads.get(self.path.rsplit('/', 1)[-1], DATA)
        first = int((self.headers.get('Range') or 'bytes=0-').split('=')[1].split('-')[0])
        self.send_response(206 if first else 200)
        self.send_header('Content-Length', str(len(data) - first))
        if first: self.send_header('Content-Range', f'bytes {first}-{len(data)-1}/{len(data)}')
        self.end_headers()
        try:
            for index in range(first, len(data), 8192):
                self.wfile.write(data[index:index+8192]); self.wfile.flush()
                if self.server.slow: time.sleep(.005)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
        finally:
            with self.server.guard: self.server.live -= 1


class QueueTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.resources = self.root / 'resources'
        self.extension = self.resources / 'runtime-setup/hf-extension'; self.extension.mkdir(parents=True)
        digest = hashlib.sha256(DATA).hexdigest()
        self.artifact = {'id': 'tiny', 'repo': 'fixture/model', 'revision': COMMIT, 'filename': 'model.gguf',
                         'path': 'models/native/model.gguf', 'bytes': len(DATA), 'sha256': digest}
        models = [{'id': name, 'artifacts': ['tiny'], 'installable': True} for name in ('native-tiny', 'native-tiny-echo')]
        (self.extension / 'native-model-bindings.json').write_text(json.dumps({'schema': 1, 'models': models, 'artifacts': [self.artifact]}), encoding='utf-8')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        self.server.requests, self.server.slow = [], False
        self.server.payloads, self.server.guard = {}, threading.Lock()
        self.server.live = self.server.max_live = 0
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        client = HubClient(endpoint=f'http://127.0.0.1:{self.server.server_port}', allow_test_http=True)
        self.state = BridgeState(self.root, self.resources, client=client, runtime=RuntimeUnavailable())
        self.addCleanup(self.state.close)
        pin = {'repository': 'fixture/model', 'revision': COMMIT, 'filename': 'model.gguf', 'bytes': len(DATA), 'sha256': digest, 'role': 'model', 'pinned': True}
        self.entry = {'id': HUB_ID, 'repository': 'fixture/model', 'revision': COMMIT, 'bytes': len(DATA), 'files': [pin], 'installable': True}
        self.state.entries[HUB_ID] = self.entry
        self.state._register_queue_shared_files()

    def wait(self, condition):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if condition(): return
            time.sleep(.01)
        self.fail('Queue transfer did not finish: ' + str(self.state.download_queue.snapshot()))

    def enqueue(self, *ids):
        return self.state.download_queue.action({'action': 'enqueue', 'items': [
            {'modelId': name, 'label': name, 'command': 'install_model', 'args': {'id': name}} for name in ids]})

    def test_native_alias_uses_exact_manifest_path_and_compiled_receipt_without_duplicate_start(self):
        self.server.slow = True
        self.enqueue('native-tiny', 'native-tiny-echo')
        self.wait(lambda: self.state.download_queue.snapshot()['items'][0]['phase'] == 'ready')
        state = self.state.download_queue.snapshot()
        self.assertEqual(len(state['items']), 1)
        self.assertEqual((self.root / self.artifact['path']).read_bytes(), DATA)
        receipt = self.root / 'models/receipts/model-native-tiny.json'
        self.assertEqual(json.loads(receipt.read_text(encoding='utf-8')), ['tiny'])
        verified = json.loads((self.root / 'models/receipts/file-tiny.json').read_text(encoding='utf-8'))
        self.assertEqual(verified['sha256'], self.artifact['sha256'])
        self.assertEqual(verified['modifiedNanos'], (self.root / self.artifact['path']).stat().st_mtime_ns)
        self.assertEqual(len(self.server.requests), 1)
        self.assertFalse((self.root / 'models/hub/native-tiny').exists())

    def test_hub_and_native_share_verified_bytes_with_no_second_network_transfer(self):
        self.enqueue('native-tiny')
        self.wait(lambda: self.state.download_queue.snapshot()['items'][0]['phase'] == 'ready')
        self.enqueue(HUB_ID)
        self.wait(lambda: all(x['phase'] == 'ready' for x in self.state.download_queue.snapshot()['items']))
        native = self.root / self.artifact['path']
        hub = self.root / 'models/hub' / HUB_ID / 'model.gguf'
        self.assertEqual(native.stat().st_ino, hub.stat().st_ino)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.state.entry(HUB_ID)['status'], 'installed')

    def test_reverse_hub_to_native_reuses_existing_verified_bytes(self):
        self.enqueue(HUB_ID)
        self.wait(lambda: self.state.download_queue.snapshot()['items'][0]['phase'] == 'ready')
        self.enqueue('native-tiny')
        self.wait(lambda: all(x['phase'] == 'ready' for x in self.state.download_queue.snapshot()['items']))
        native = self.root / self.artifact['path']
        hub = self.root / 'models/hub' / HUB_ID / 'model.gguf'
        self.assertEqual(native.stat().st_ino, hub.stat().st_ino)
        self.assertEqual(len(self.server.requests), 1)

    def test_six_models_transfer_bytes_simultaneously_in_parallel_mode(self):
        self.server.slow = True
        names = []
        for index in range(6):
            identity, filename = 'hf-' + f'{index+1:024x}', f'model-{index}.gguf'
            data = bytes([index]) + DATA[1:]
            self.server.payloads[filename] = data
            pin = {**self.entry['files'][0], 'filename': filename, 'sha256': hashlib.sha256(data).hexdigest()}
            self.state.entries[identity] = {**self.entry, 'id': identity, 'files': [pin]}
            names.append(identity)
        self.state.download_queue.action({'action': 'mode', 'mode': 'parallel'})
        self.enqueue(*names)
        self.wait(lambda: len(self.state.download_queue.snapshot()['activeIds']) == 6)
        self.wait(lambda: all(x['downloadedBytes'] > 0 for x in self.state.download_queue.snapshot()['items']))
        self.assertEqual(self.server.max_live, 6)
        self.wait(lambda: all(x['phase'] == 'ready' for x in self.state.download_queue.snapshot()['items']))
        self.assertEqual(len(self.server.requests), 6)

    def test_queue_http_routes_require_owner_authentication(self):
        server = BridgeServer(('127.0.0.1', 0), self.state, 'fixture-secret')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def request(method, path, secret=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            headers = {'Content-Type': 'application/json'}
            if secret: headers['X-OpenCore-Hub-Secret'] = secret
            connection.request(method, path, json.dumps({'action': 'snapshot'}) if method == 'POST' else None, headers)
            response = connection.getresponse(); result = response.status, json.loads(response.read()); connection.close()
            return result
        for method, path in [('GET', '/api/download-queue'), ('POST', '/api/download-queue/action')]:
            self.assertEqual(request(method, path)[0], 401)
            status, body = request(method, path, 'fixture-secret')
            self.assertEqual(status, 200)
            self.assertEqual(body['schema'], 1)
            self.assertEqual(body['items'], [])


if __name__ == '__main__': unittest.main()
