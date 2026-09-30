"""Tool boundaries and arbitrary stream fragmentation, without model downloads."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src-tauri/resources/lfm'))
from protocol import OutputStream

TOOLS = [{'type': 'function', 'function': {'name': 'sum_numbers', 'parameters': {'type': 'object'}}}]

class ProtocolTests(unittest.TestCase):
    def test_fragmented_thought_answer_and_tool_stay_in_order(self):
        stream = OutputStream(); deltas = []
        for char in '<think>check</think>Ready<tool_call>{"name":"sum_numbers","arguments":{"a":19,"b":23}}</tool_call>':
            deltas += stream.feed(char, tools=TOOLS)
        deltas += stream.feed('', final=True, tools=TOOLS)
        self.assertEqual(''.join(d.get('reasoning_content', '') for d in deltas), 'check')
        self.assertEqual(''.join(d.get('content', '') for d in deltas), 'Ready')
        message = stream.message(TOOLS)
        self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']), {'a': 19, 'b': 23})
        self.assertEqual(message['content'], 'Ready')

    def test_incomplete_or_unknown_calls_never_produce_actions(self):
        incomplete = OutputStream()
        with self.assertRaises(ValueError): incomplete.feed('<tool_call>{"name":"sum_numbers"', final=True, tools=TOOLS)
        unknown = OutputStream(); unknown.feed('<tool_call>{"name":"delete_everything","arguments":{}}</tool_call>', final=True, tools=TOOLS)
        with self.assertRaises(ValueError): unknown.message(TOOLS)

    def test_python_tool_format_is_literal_data_never_executable_code(self):
        for value in ['[sum_numbers(a=__import__("os").system("whoami"), b=1)]', '[sum_numbers(**payload)]', '[sum_numbers(1, 2)]']:
            stream = OutputStream(); stream.feed(value, final=True, tools=TOOLS)
            with self.assertRaises((ValueError, TypeError)): stream.message(TOOLS)
        stream = OutputStream(); stream.feed('[sum_numbers(a=19, b=23)]', final=True, tools=TOOLS)
        self.assertEqual(stream.message(TOOLS)['tool_calls'][0]['function']['name'], 'sum_numbers')

if __name__ == '__main__': unittest.main()
