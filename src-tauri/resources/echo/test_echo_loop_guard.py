import unittest
import json
from echo_server import ReasoningLoopGuard,recover_requested_html_file


class LoopGuardTests(unittest.TestCase):
    def test_short_paraphrased_plans_with_single_newlines_do_not_run_forever(self):
        guard=ReasoningLoopGuard()
        plans=["Empty workspace. I'll build a polished, feature-rich snake game.",
               "Empty workspace, so I'll create a complete snake HTML game from scratch.",
               "I'll build a polished single-file snake game with controls and a high score.",
               "Empty workspace. Let me build a complete, polished snake game."]
        detected=False
        for line in plans*4:
            detected=guard.feed(line+'\n') or detected
        self.assertTrue(detected)

    def test_repeated_planning_is_detected_across_stream_chunks(self):
        guard=ReasoningLoopGuard()
        block='The user wants me to find the snake game file and fix movement. I need to search the workspace for the snake game before editing it. I should locate the current snake HTML file and then inspect its actual contents.\n\n'
        detected=False
        for _ in range(12):
            for offset in range(0,len(block),11):detected=guard.feed(block[offset:offset+11]) or detected
        self.assertTrue(detected)

    def test_distinct_steps_and_one_recheck_are_not_a_loop(self):
        guard=ReasoningLoopGuard()
        for block in [
            'First I will inspect the configuration and compare its context allocation with the upstream metadata. '*6,
            'The published context limit is larger, so the catalog can allocate thirty two thousand tokens while preserving the exact archive. '*6,
            'Next the generated HTML file needs a keyboard listener for movement, a fixed animation timestep, and a restart state. '*6,
            'The output receipt now points to the real file and its size, which lets the user open it through the chat. '*6]:
            self.assertFalse(guard.feed(block+'\n\n'))

    def test_requested_html_is_written_as_exact_source_and_not_reported_as_already_created(self):
        tools=[{'type':'function','function':{'name':'dev','parameters':{'properties':{'action':{'enum':['read','write']}}}}}]
        source='<!doctype html>\n<html><body>Snake</body></html>'
        message={'content':'Here is the source:\n```html\n'+source+'\n```'}
        self.assertTrue(recover_requested_html_file(message,tools,'Make a snake HTML game named snake.html',[]))
        args=json.loads(message['tool_calls'][0]['function']['arguments'])
        self.assertEqual(args['path'],'snake.html');self.assertEqual(args['content'],source)
        self.assertEqual(message['content'],'Here is the source:\n```html\n'+source+'\n```')
        second={'content':message['content']}
        self.assertFalse(recover_requested_html_file(second,tools,'Make a snake HTML game named snake.html',[message]))

    def test_prose_partial_html_and_missing_write_tool_do_not_create_a_file(self):
        tools=[{'function':{'name':'dev','parameters':{'properties':{'action':{'enum':['write']}}}}}]
        self.assertFalse(recover_requested_html_file({'content':'```html\n<html>\n```'},tools,'Make an HTML file',[]))
        self.assertFalse(recover_requested_html_file({'content':'```html\n<html></html>\n```'},[],'Make an HTML file',[]))
        self.assertFalse(recover_requested_html_file({'content':'```html\n<html></html>\n```'},tools,'Explain this HTML snippet',[]))

if __name__=='__main__':unittest.main()
