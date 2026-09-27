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


class FixedReplyBrain:
    def __init__(self, message, finish_reason):
        self.message, self.finish_reason, self.calls = message, finish_reason, []

    def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        return BackboneReply(self.message['content'], dict(self.message), {
            'choices': [{'message': dict(self.message), 'finish_reason': self.finish_reason}],
            'usage': {'prompt_tokens': 5, 'completion_tokens': 32, 'total_tokens': 37},
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

    def test_reasoning_only_budget_exhaustion_is_unfinished_response_not_server_failure(self):
        self.engine.brains = [FixedReplyBrain({'role': 'assistant', 'content': '',
                                              'reasoning_content': text}, 'length')
                              for text in ('First unfinished reasoning', 'Second unfinished reasoning')]
        try:
            response = self.request(0)
        except urllib.error.HTTPError as error:
            self.fail(f'Budget exhaustion became HTTP {error.code}: {error.read().decode()}')
        choice = response['choices'][0]
        self.assertEqual(choice['finish_reason'], 'length')
        self.assertEqual(choice['message'], {'role': 'assistant', 'content': '',
                                             'reasoning_content': 'First unfinished reasoning'})
        self.assertEqual(response['usage'], {'prompt_tokens': 10, 'completion_tokens': 64, 'total_tokens': 74})
        self.assertIsNone(response['lfm']['selected_brain'])
        self.assertEqual(response['lfm']['status'], 'incomplete')
        self.assertEqual(response['lfm']['candidate_finish_reasons'], ['length', 'length'])
        self.assertEqual([len(brain.calls) for brain in self.engine.brains], [1, 1])

    def test_complete_peer_is_selected_over_reasoning_only_truncation(self):
        self.engine.brains = [FixedReplyBrain({'role': 'assistant', 'content': '',
                                              'reasoning_content': 'Unfinished'}, 'length'), RecordingBrain()]
        response = self.request(0)
        self.assertEqual(response['choices'][0]['message']['content'], 'Identical valid answer')
        self.assertEqual(response['choices'][0]['finish_reason'], 'stop')
        self.assertEqual(response['lfm']['selected_brain'], 2)

    def test_truncated_unregistered_actions_are_never_returned_as_reasoning_only(self):
        self.engine.brains = [FixedReplyBrain({'role': 'assistant', 'content': '',
                                              'reasoning_content': 'Incomplete action',
                                              'tool_calls': [{'function': {'name': 'unregistered', 'arguments': '{}'}}]},
                                             'length') for _ in range(2)]
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(0)
        self.assertEqual(caught.exception.code, 500)

    def test_empty_finished_responses_remain_failures(self):
        self.engine.brains = [FixedReplyBrain({'role': 'assistant', 'content': ''}, 'stop') for _ in range(2)]
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(0)
        self.assertEqual(caught.exception.code, 500)


if __name__ == '__main__':
    unittest.main()
