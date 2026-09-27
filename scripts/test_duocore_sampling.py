"""Exercise request sampling and truncation through the actual DuoCore HTTP route."""
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
import urllib.error
import urllib.request

RESOURCE = Path(__file__).resolve().parents[1] / 'src-tauri/resources/doucode'
sys.path.insert(0, str(RESOURCE))
from duocore.runtime import BackboneReply, DuoCoreEngine
from duocore.spec import default_duocore_config
from serve_duocore import DuoCoreHandler


class SamplingApiTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.finish = {'K2': 'stop', 'Nanbeige': 'stop'}
        self.engine = DuoCoreEngine(default_duocore_config(), Path('.'))

        def chat(name, messages, **kwargs):
            self.calls.append((name, kwargs))
            if kwargs.get('json_mode'):
                pair = json.loads(messages[-1]['content'].split('\n', 1)[1])
                content = json.dumps({
                    'score_a': 90 if pair['candidate_a']['content'] == 'preferred Nanbeige answer' else 10,
                    'score_b': 90 if pair['candidate_b']['content'] == 'preferred Nanbeige answer' else 10,
                    'confidence': 0.8, 'reason': 'Prefer the complete answer.',
                })
                reason = 'stop'
            else:
                content = 'preferred Nanbeige answer' if name == 'Nanbeige' else 'K2 answer'
                reason = self.finish[name]
            message = {'role': 'assistant', 'content': content}
            return BackboneReply(content, message, {
                'choices': [{'message': message, 'finish_reason': reason}],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 3},
            })

        self.engine.k2.chat = lambda messages, **kwargs: chat('K2', messages, **kwargs)
        self.engine.nanbeige.chat = lambda messages, **kwargs: chat('Nanbeige', messages, **kwargs)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), DuoCoreHandler)
        self.server.engine = self.engine
        self.server.config = self.engine.config
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.engine.pool.shutdown(wait=True)

    def request(self, **options):
        payload = {'messages': [{'role': 'user', 'content': 'Answer the question.'}],
                   'max_tokens': 4096, **options}
        req = urllib.request.Request(
            f'http://127.0.0.1:{self.server.server_port}/v1/chat/completions',
            data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.load(response)

    def assert_temperatures(self, expected):
        drafts = [kwargs['temperature'] for _, kwargs in self.calls if not kwargs.get('json_mode')]
        reviews = [kwargs['temperature'] for _, kwargs in self.calls if kwargs.get('json_mode')]
        self.assertEqual(drafts, [expected, expected])
        self.assertEqual(reviews, [0, 0])

    def test_greedy_benchmark_request_reaches_both_drafts(self):
        result = self.request(temperature=0)
        self.assert_temperatures(0)
        self.assertEqual(result['duocore']['draft_temperature'], 0)

    def test_requested_nonzero_temperature_reaches_both_drafts(self):
        self.request(temperature=0.7)
        self.assert_temperatures(0.7)

    def test_omitted_temperature_keeps_application_default(self):
        self.request()
        self.assert_temperatures(0.35)

    def test_invalid_temperature_is_rejected_before_any_model_work(self):
        for temperature in ('invalid', -1, True, float('nan'), float('inf'), 2.1):
            with self.subTest(temperature=temperature):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request(temperature=temperature)
                self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.calls, [])

    def test_selected_draft_truncation_is_not_reported_as_stop(self):
        self.finish['Nanbeige'] = 'length'
        result = self.request(temperature=0)
        self.assertEqual(result['duocore']['selected_model'], 'Nanbeige')
        self.assertEqual(result['choices'][0]['finish_reason'], 'length')
        self.assertEqual(result['duocore']['candidate_finish_reasons'], self.finish)

    def test_unselected_draft_truncation_does_not_change_selected_reason(self):
        self.finish['K2'] = 'length'
        result = self.request(temperature=0)
        self.assertEqual(result['choices'][0]['finish_reason'], 'stop')
        self.assertEqual(result['duocore']['candidate_finish_reasons'], self.finish)


if __name__ == '__main__':
    unittest.main()
