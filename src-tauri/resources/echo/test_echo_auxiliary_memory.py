"""The native post-reply extractor must never become a visible chat turn."""
from contextlib import ExitStack
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from echo_server import ArchiveSet, EchoState, Handler
from evoagent.echo_adapters import adapter_for
from evoagent.echo_context import LiveTranscript


class AuxiliaryMemoryTests(unittest.TestCase):
    def test_native_effort_wrapped_capture_keeps_the_completed_chat_and_preview_untouched(self):
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives = ArchiveSet(root, idle_seconds=60)
            stack.callback(archives.close)
            state = EchoState(archives, 'http://127.0.0.1:1', 0, 4, False)
            stack.callback(state.memory_controller.close)
            state._ctx_size = 32768
            state.count_tokens = lambda text: max(1, len(str(text)) // 4)
            state._memory_adapter = adapter_for({'architecture': 'qwen', 'revision': 'fixture'}, state.count_tokens)
            live = LiveTranscript(archives.get('chat'), 'chat')
            live.start_turn('Open Chrome', state.count_tokens)
            live.append({'role': 'assistant', 'content': 'Chrome is open.'}, state.count_tokens, 'answer')
            live.open = False
            live.save()
            before = copy.deepcopy(live.entries)
            payload = {'model': 'fixture', 'messages': [
                {'role': 'system', 'content': 'Answer directly, without optional deliberation.'},
                {'role': 'system', 'content': 'Select durable facts, preferences, or explicitly stated lessons from the HUMAN_SOURCE data only. Do not emit reasoning, markdown, tools or commentary.'},
                {'role': 'user', 'content': json.dumps({'HUMAN_SOURCE': 'I played football in Canada.', 'deleted_keys': [], 'existing': [], 'has_project': False})},
            ]}
            raw = json.dumps(payload).encode()
            handler = Handler.__new__(Handler)
            handler.state, handler.path = state, '/v1/chat/completions'
            handler.headers = {'Content-Length': str(len(raw)), 'X-Echo-Conversation': 'chat', 'x-opencore-timeline-owner': 'app'}
            handler.rfile = io.BytesIO(raw)
            handler._send_json = lambda status, value: (status, value)
            seen = []
            reply = {'choices': [{'message': {'role': 'assistant', 'content': '{"memories":[]}'}, 'finish_reason': 'stop'}]}
            def upstream(path, body, stream):
                seen.append(copy.deepcopy(body))
                return io.BytesIO(json.dumps(reply).encode())
            handler._upstream = upstream
            status, result = handler.do_POST()
            self.assertEqual(status, 200, result)
            self.assertEqual(result['choices'][0]['message']['content'], '{"memories":[]}')
            self.assertEqual(seen[0]['messages'], payload['messages'][1:])
            self.assertFalse(seen[0]['stream'])
            self.assertEqual(seen[0]['max_tokens'], 1024)
            self.assertNotIn('tools', seen[0])
            after = LiveTranscript(archives.get('chat'), 'chat')
            self.assertEqual(after.question, 'Open Chrome')
            self.assertEqual(after.entries, before)
            self.assertFalse(after.open)
            self.assertIsNone(getattr(handler, '_preview_conversation', None))
            self.assertFalse(state._backend_auxiliary_lock.locked())


if __name__ == '__main__':
    unittest.main()
