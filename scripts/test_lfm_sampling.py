"""Check requested draft sampling through the public API without loading weights."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src-tauri/resources/lfm'))
from dual import BackboneReply, DualCoreEngine
from serve_lfm import Handler


class RecordingBrain:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        message = {'role': 'assistant', 'content': 'Identical valid answer'}
        return BackboneReply(message['content'], message, {
            'choices': [{'message': message, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8},
        })


class SamplingTests(unittest.TestCase):
    def setUp(self):
        self.engine = DualCoreEngine(Path('unused-fixture.gguf'), 18000)
        self.engine.brains = [RecordingBrain(), RecordingBrain()]
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.lock = threading.Lock()
        self.server.state = {'name': 'DualCore KV', 'kind': 'dual', 'context': 512,
                             'engine': self.engine, 'evidence': {}}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.engine.close()

    def request(self, temperature=None):
        payload = {'model': 'DualCore KV', 'messages': [{'role': 'user', 'content': 'Answer directly'}],
                   'max_tokens': 32}
        if temperature is not None:
            payload['temperature'] = temperature
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.server.server_port}/v1/chat/completions',
            json.dumps(payload).encode(), {'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_api_zero_temperature_reaches_both_drafts(self):
        response = self.request(0)
        self.assertEqual([brain.calls[0].get('temperature') for brain in self.engine.brains], [0, 0])
        self.assertEqual(response['lfm']['draft_temperature'], 0)
        self.assertEqual(response['lfm']['review_temperature'], 0)

    def test_api_fractional_temperature_is_preserved(self):
        self.request(0.7)
        self.assertEqual([brain.calls[0].get('temperature') for brain in self.engine.brains], [0.7, 0.7])

    def test_omitted_temperature_keeps_existing_default(self):
        self.request()
        self.assertEqual([brain.calls[0].get('temperature') for brain in self.engine.brains], [0.35, 0.35])

    def test_invalid_temperature_is_rejected_before_drafting(self):
        for value in ('zero', -1, float('nan'), True):
            with self.subTest(value=value):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request(value)
                self.assertEqual(caught.exception.code, 400)
        self.assertEqual([brain.calls for brain in self.engine.brains], [[], []])


if __name__ == '__main__':
    unittest.main()
