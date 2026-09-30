"""Regression checks for live ECHO output and exact eviction/retrieval."""
import concurrent.futures
import hashlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src-tauri/resources/echo"))
from echo_server import (
    ArchiveSet,
    BoundedTokenCountCache,
    EchoState,
    Handler,
    _conversation_id,
    tool_call_error,
)
from evoagent.echo_context import LiveTranscript
from evoagent.echo_memory import BoundedPageCache, EchoArchive, MemoryPage
import echo_import
from echo_import import import_stream


class EchoLiveTests(unittest.TestCase):
    def test_sdk_budget_update_does_not_replace_user_request_or_tool_results(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), idle_seconds=0)
            state = EchoState(archives, 'http://127.0.0.1:1', 0, 4, False)
            state._ctx_size = 8192
            state.count_tokens = lambda text: len(text) // 4
            handler = object.__new__(Handler)
            handler.state = state
            handler._send_json = lambda code, value: value
            seen = []
            call = {'id': 'inspect-one', 'type': 'function',
                    'function': {'name': 'inspect', 'arguments': '{}'}}
            def generate(body, phase):
                seen.append(body)
                message = {'role': 'assistant', 'content': '' if len(seen) == 1 else 'Done.'}
                if len(seen) == 1:
                    message['tool_calls'] = [call]
                return {'choices': [{'finish_reason': 'tool_calls' if len(seen) == 1 else 'stop',
                                     'message': message}], 'usage': {'completion_tokens': 4}}
            handler._generate_live = generate
            question = 'Inspect the current PhysicsController.'
            note = {'role': 'user', 'opencore_harness_context': True,
                    'content': '<harness_context>\n<total_tokens>14978886 tokens left</total_tokens>\n</harness_context>'}
            payload = {'messages': [{'role': 'user', 'content': question}, note],
                       'max_tokens': 256,
                       'tools': [{'type': 'function', 'function': {'name': 'inspect',
                                  'parameters': {'type': 'object', 'properties': {}}}}]}
            try:
                handler._controlled_context(payload, 'sdk-budget')
                live = LiveTranscript(archives.get('sdk-budget'), 'sdk-budget')
                self.assertEqual(live.question, question)
                self.assertIn(question, json.dumps(seen[0]['messages']))
                payload['messages'] = [{'role': 'user', 'content': question},
                    {'role': 'assistant', 'content': '', 'tool_calls': [call]},
                    {'role': 'tool', 'tool_call_id': 'inspect-one', 'content': 'PhysicsController exists.'}, note]
                handler._controlled_context(payload, 'sdk-budget')
                self.assertIn('PhysicsController exists.', json.dumps(seen[1]['messages']))
                live = LiveTranscript(archives.get('sdk-budget'), 'sdk-budget')
                self.assertEqual(live.question, question)
                # Literal user text remains a user request, even if it resembles a wrapper.
                literal = {'messages': [{'role': 'user', 'content': note['content']}], 'max_tokens': 256}
                handler._controlled_context(literal, 'literal-wrapper')
                self.assertEqual(LiveTranscript(archives.get('literal-wrapper'), 'literal-wrapper').question,
                                 note['content'])
            finally:
                state.memory_controller.close()
                archives.close()

    def test_latest_idle_window_setting_supersedes_an_older_deferred_setting(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), idle_seconds=0)
            state = EchoState(archives, 'http://127.0.0.1:1', 0, 4, False)
            state._ctx_size = 262144
            try:
                for latest in (16384, 32768):
                    state.active_window_tokens = 32768
                    config = state.memory_configuration()
                    state.begin_context('active')
                    config['activeWindowTokens'] = 8192
                    state.configure_memory(config)
                    state.end_context('active')
                    config['activeWindowTokens'] = latest
                    self.assertEqual(state.configure_memory(config)['activeWindowTokens'], latest)
                    state.apply_pending_window()
                    self.assertEqual(state.context_size(), latest)
                    self.assertIsNone(state.pending_active_window_tokens)
            finally:
                state.memory_controller.close()
                archives.close()

    def test_configured_working_window_is_capped_by_backend_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), idle_seconds=0)
            state = EchoState(archives, 'http://127.0.0.1:1', 0, 4, False)
            state._ctx_size = 262144
            self.assertEqual(state.context_size(), 32768)
            config = state.memory_configuration()
            config['activeWindowTokens'] = 8192
            state.configure_memory(config)
            self.assertEqual(state.context_size(), 8192)
            state._ctx_size = 4096
            self.assertEqual(state.context_size(), 4096)
            self.assertTrue(state._backend_needs_reset)
            state.begin_context('active')
            config['activeWindowTokens'] = 16384
            state.configure_memory(config)
            self.assertEqual(state.active_window_tokens, 8192)
            state.apply_pending_window()
            self.assertEqual(state.active_window_tokens, 16384)
            state.end_context('active')
            state.memory_controller.close()
            archives.close()

    def test_page_fault_evidence_survives_a_following_refresh_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), idle_seconds=0)
            state = EchoState(archives, 'http://127.0.0.1:1', 0, 4, False)
            state._ctx_size = 8192
            state.count_tokens = lambda text: len(text) // 4
            archive = archives.get('fault')
            archive.append('PhysicsController launch key is blue-garnet-742.', 'fault')
            live = LiveTranscript(archive, 'fault')
            live.start_turn('We were fixing menus.', state.count_tokens)
            live.open = False
            live.save()
            handler = object.__new__(Handler)
            handler.state = state
            handler._send_json = lambda code, value: value
            seen = []
            def generate(body, phase):
                seen.append(body)
                content = '<echo>{"op":"fault","query":"PhysicsController"}</echo>' if len(seen) == 1 else 'The key is blue-garnet-742.'
                return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':content}}], 'usage':{'completion_tokens':128}}
            handler._generate_live = generate
            try:
                handler._controlled_context({'messages':[{'role':'user','content':'Explain the render failure.'}], 'max_tokens':1024, 'echo_max_calls':2}, 'fault')
                self.assertIn('blue-garnet-742', json.dumps(seen[1]['messages']))
            finally:
                state.memory_controller.close()
                archives.close()

    def test_recall_leaves_the_same_framing_and_output_reserve_as_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), idle_seconds=0)
            state = EchoState(archives, 'http://127.0.0.1:1', 0, 4, False)
            state._ctx_size = 8192
            state.count_tokens = lambda text: len(text) // 4
            for i in range(9):
                archives.get('small').append(f'PhysicsController detail{i} ' + 'x' * 900, 'small')
            live = LiveTranscript(archives.get('small'), 'small')
            live.start_turn('Unrelated recent menu work.', state.count_tokens)
            live.open = False
            live.save()
            handler = object.__new__(Handler)
            handler.state = state
            handler._send_json = lambda code, value: value
            seen = []
            def generate(body, phase):
                seen.append(body)
                return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'The fix is available.'}}]}
            handler._generate_live = generate
            try:
                handler._controlled_context({'messages':[{'role':'system','content':'x'*10500}, {'role':'user','content':'Recall PhysicsController'}], 'max_tokens':1024}, 'small')
                self.assertTrue(seen)
                self.assertGreaterEqual(seen[0]['max_tokens'], 1024)
            finally:
                state.memory_controller.close()
                archives.close()

    def test_tokenizer_result_cache_evicts_old_entries_at_its_capacity(self):
        cache = BoundedTokenCountCache(max_entries=2)
        first = cache.key_for('first old turn')
        cache.put(first, 3)
        cache.put(cache.key_for('second old turn'), 3)
        cache.put(cache.key_for('new turn'), 2)
        self.assertIsNone(cache.get(first))
        self.assertEqual(cache.get(cache.key_for('new turn')), 2)
        self.assertEqual(cache.snapshot()['entries'], 2)

    def test_repeated_history_token_counts_reuse_the_model_tokenizer_result(self):
        calls = []

        class Tokenizer(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(payload['content'])
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                tokens = payload['content'].split()
                self.wfile.write(json.dumps({'tokens': tokens}).encode())

            def log_message(self, *_args):
                pass

        with tempfile.TemporaryDirectory() as folder:
            upstream = ThreadingHTTPServer(('127.0.0.1', 0), Tokenizer)
            threading.Thread(target=upstream.serve_forever, daemon=True).start()
            state = EchoState(ArchiveSet(Path(folder), idle_seconds=0),
                              'http://127.0.0.1:%d' % upstream.server_port,
                              10000, 4, False)
            try:
                self.assertEqual(state.count_tokens('preserve the running system'), 4)
                self.assertEqual(state.count_tokens('preserve the running system'), 4)
                self.assertEqual(calls, ['preserve the running system'])
            finally:
                state.archives.close()
                upstream.shutdown()
                upstream.server_close()

    def test_concurrent_requests_for_one_conversation_queue_instead_of_conflicting(self):
        with tempfile.TemporaryDirectory() as folder:
            state = EchoState(ArchiveSet(Path(folder), idle_seconds=0), 'http://127.0.0.1:1', 10000, 4, False)
            metrics = {'active': 0, 'maximum': 0}
            metrics_lock = threading.Lock()

            class SerialHandler(Handler):
                def _controlled_context_serial(self, payload, conversation, level, archive_input=True):
                    with metrics_lock:
                        metrics['active'] += 1
                        metrics['maximum'] = max(metrics['maximum'], metrics['active'])
                    time.sleep(0.08)
                    with metrics_lock:
                        metrics['active'] -= 1
                    return self._send_json(200, {'conversation': conversation})

            SerialHandler.state = state
            server = ThreadingHTTPServer(('127.0.0.1', 0), SerialHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = 'http://127.0.0.1:%d/v1/chat/completions' % server.server_port

            def send():
                request = urllib.request.Request(url, data=json.dumps({
                    'conversation_id': 'same-conversation',
                    'messages': [{'role': 'user', 'content': 'hi'}],
                    'stream': False,
                }).encode(), headers={'Content-Type': 'application/json'})
                try:
                    with urllib.request.urlopen(request, timeout=3) as response:
                        return response.status
                except urllib.error.HTTPError as error:
                    return error.code

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    statuses = list(pool.map(lambda _: send(), range(2)))
                self.assertEqual(statuses, [200, 200])
                self.assertEqual(metrics['maximum'], 1)
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    with state.lock:
                        active = 'same-conversation' in state._context_active
                    if not active:
                        break
                    time.sleep(0.005)
                self.assertFalse(active, 'the conversation stayed active after both requests completed')
            finally:
                server.shutdown()
                server.server_close()
                state.archives.close()

    def test_forwarded_app_conversation_id_selects_the_matching_echo_archive(self):
        from email.message import Message
        headers = Message()
        headers["x-echo-conversation"] = "app-conversation-42"
        self.assertEqual(_conversation_id({}, headers), "app-conversation-42")

    def test_app_timeline_owner_leaves_exact_event_archiving_to_the_app_sync(self):
        with tempfile.TemporaryDirectory() as folder:
            state = EchoState(ArchiveSet(Path(folder), idle_seconds=0), 'http://127.0.0.1:1', 10000, 4, False)
            archive_input_values = []

            class AppOwnedHandler(Handler):
                def _controlled_context_serial(self, payload, conversation, level, archive_input=True):
                    archive_input_values.append(archive_input)
                    return self._send_json(200, {'conversation': conversation})

            AppOwnedHandler.state = state
            server = ThreadingHTTPServer(('127.0.0.1', 0), AppOwnedHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                request = urllib.request.Request(
                    'http://127.0.0.1:%d/v1/chat/completions' % server.server_port,
                    data=json.dumps({'messages': [{'role': 'user', 'content': 'hi'}]}).encode(),
                    headers={'Content-Type': 'application/json',
                             'X-Echo-Conversation': 'app-conversation-42',
                             'X-OpenCore-Timeline-Owner': 'app'})
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                self.assertEqual(archive_input_values, [False])
            finally:
                server.shutdown()
                server.server_close()
                state.archives.close()

    def test_echo_state_has_no_fixed_reply_continuation_cap_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            state = EchoState(ArchiveSet(Path(folder), idle_seconds=0), 'http://127.0.0.1:1', 0, 12, False)
            self.assertEqual(0, state.max_continuations)

    def test_context_endpoint_reports_cache_budget_without_a_conversation_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), idle_seconds=0, warm_cache_budget_mib=2)
            state = EchoState(archives, 'http://127.0.0.1:1', 10000, 4, False)
            state._ctx_size = 32768
            class EchoHandler(Handler):
                pass
            EchoHandler.state = state
            server = ThreadingHTTPServer(('127.0.0.1', 0), EchoHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with urllib.request.urlopen('http://127.0.0.1:%d/echo/context?conversation=not-created' % server.server_port) as response:
                    status = json.load(response)
                self.assertFalse(status['available'])
                self.assertEqual(status['contextMode'], 'persistent_echo')
                self.assertEqual(status['warmCache']['budgetBytes'], 2 * 1024 * 1024)
                self.assertEqual(status['warmCache']['residentBytes'], 0)
                with urllib.request.urlopen('http://127.0.0.1:%d/echo/stats' % server.server_port) as response:
                    stats = json.load(response)
                self.assertEqual(stats['warm_cache']['budget_bytes'], 2 * 1024 * 1024)
                self.assertEqual(stats['open_cold_archives'], 0)
                self.assertEqual(stats['history_mode'], 'persistent_echo')

                live = LiveTranscript(archives.get('finished-turn'), 'finished-turn')
                count = lambda text: max(1, len(text) // 4)
                live.start_turn('Remember this exact decision', count)
                live.append({'role':'assistant','content':'Keep decision ARCHIVE-ONLY-93.'}, count)
                live.open = False
                live.prompt_tokens = 1234
                live.offload_completed()
                live.save()
                with urllib.request.urlopen('http://127.0.0.1:%d/echo/context?conversation=finished-turn' % server.server_port) as response:
                    finished = json.load(response)
                self.assertTrue(finished['available'])
                self.assertEqual(finished['liveTokens'], 0)
                self.assertEqual(finished['promptTokens'], 1234)
                self.assertEqual(finished['offloadedMessages'], 2)
                self.assertEqual(finished['contextMode'], 'persistent_echo')

                state._backend_conversation = 'finished-turn'
                state._backend_metrics_key = None
                session_id = hashlib.sha256(b'finished-turn').hexdigest()
                slots = [{'echo_session_id': session_id, 'echo_session_tokens': 9100,
                          'n_prompt_tokens': 3210, 'n_ctx': 32768, 'is_processing': False}]
                with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(slots).encode())):
                    metrics = state.backend_session_metrics('finished-turn')
                self.assertEqual(metrics, {'modelSessionTokens': 9100, 'modelActiveTokens': 3210,
                                           'modelContextTokens': 32768, 'modelSessionActive': False})
            finally:
                server.shutdown()
                server.server_close()
                archives.close()

    def test_completed_turn_is_archived_and_remains_live_for_follow_up(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = EchoArchive(Path(folder) / 'persistent.sqlite3')
            count = lambda text: max(1, len(text) // 4)
            live = LiveTranscript(archive, 'persistent-conversation')
            live.start_turn('Build the movement system', count)
            live.append({'role': 'assistant', 'content': 'The movement code lives in movement.py.'}, count)
            live.open = False
            self.assertEqual(live.archive_completed(), 2)
            self.assertEqual(len(live.messages), 2)
            first_ids = {entry['archive_event_id'] for entry in live.entries}

            live.start_turn('Now add walking without replacing running', count)
            self.assertEqual(len(live.messages), 3)
            self.assertIn('movement.py', live.messages[1]['content'])
            live.open = False
            self.assertEqual(live.archive_completed(), 1)
            self.assertEqual(live.archive_completed(), 0)
            self.assertEqual(len(live.messages), 3)
            self.assertTrue(first_ids.issubset({entry['archive_event_id'] for entry in live.entries}))
            restored = LiveTranscript(archive, 'persistent-conversation')
            self.assertEqual(restored.messages, live.messages)
            archive.close()

    def test_cold_archive_connections_share_the_open_database_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), idle_seconds=0, max_open=2)
            state = EchoState(archives, 'http://127.0.0.1:1', 10000, 4, False)
            try:
                cold_paths = []
                first_handle = None
                for conversation in ('one', 'two', 'three'):
                    live = archives.path_for(conversation)
                    cold = live.with_name(live.stem + '-cold' + live.suffix)
                    seed = EchoArchive(cold)
                    seed.close()
                    cold_paths.append(cold)
                    opened = state.cold_archive_for(conversation)
                    if conversation == 'one':
                        first_handle = opened
                self.assertEqual(len(state._cold), 2)
                self.assertNotIn(str(cold_paths[0]), state._cold)
                self.assertIsNotNone(first_handle)
                self.assertIsNone(first_handle._db)
                self.assertLessEqual(sum(1 for item in state._cold.values() if item._db is not None), 2)
            finally:
                for archive in state._cold.values():
                    archive.close()
                archives.close()

    def test_exact_page_cache_uses_a_global_byte_budget_and_lru_eviction(self):
        make_page = lambda name: MemoryPage(name, 'cache-test', (0, 240), 1.0, 1, None, None,
                                            name * 64, name * 240)
        first, second, third = make_page('a'), make_page('b'), make_page('c')
        item_bytes = BoundedPageCache.page_cost('a', first)
        cache = BoundedPageCache(item_bytes * 2)
        self.assertTrue(cache.put('a', first))
        self.assertTrue(cache.put('b', second))
        self.assertIs(cache.get('a'), first)  # a becomes most recently used
        self.assertTrue(cache.put('c', third))
        self.assertIsNone(cache.get('b'))
        status = cache.snapshot()
        self.assertLessEqual(status['resident_bytes'], status['budget_bytes'])
        self.assertEqual(status['pages'], 2)
        self.assertEqual(status['evictions'], 1)
        self.assertEqual(status['hits'], 1)

    def test_synthetic_history_growth_keeps_the_warm_cache_under_one_fixed_budget(self):
        # One exact page models roughly 1,000 source tokens. This exercises
        # 32K through 10M-token histories without loading model weights.
        budget = 16 * 1024 * 1024
        for history_tokens in (32_000, 100_000, 1_000_000, 10_000_000):
            cache = BoundedPageCache(budget)
            page_count = history_tokens // 1000
            for index in range(page_count):
                page_id = '%064x' % index
                text = ('%08d ' % index) + ('x' * 3980)
                page = MemoryPage(page_id, 'synthetic', (index * 4000, (index + 1) * 4000),
                                  1.0, 1, None, None, page_id, text)
                cache.put('synthetic\0' + page_id, page)
            status = cache.snapshot()
            self.assertLessEqual(status['resident_bytes'], budget, history_tokens)
            if history_tokens == 10_000_000:
                self.assertGreater(status['evictions'], 0)
                self.assertLess(status['pages'], page_count)

    def test_exact_page_cache_skips_oversized_pages_and_serves_verified_archive_pages(self):
        oversized = MemoryPage('large', 'cache-test', (0, 6000), 1.0, 1, None, None,
                               '0' * 64, 'x' * 6000)
        tiny = MemoryPage('tiny', 'cache-test', (0, 10), 1.0, 1, None, None,
                          '1' * 64, 'exact text')
        cache = BoundedPageCache(BoundedPageCache.page_cost('tiny', tiny) + 4096)
        self.assertFalse(cache.put('large', oversized))
        self.assertEqual(cache.snapshot()['resident_bytes'], 0)
        self.assertEqual(cache.snapshot()['oversized'], 1)

        with tempfile.TemporaryDirectory() as folder:
            archive = EchoArchive(Path(folder) / 'memory.db', page_cache=cache)
            try:
                page = archive.append('the exact archived page', 'cache-test')[0]
                self.assertEqual(archive.load(page.page_id).text, 'the exact archived page')
                self.assertEqual(archive.load(page.page_id).content_hash, page.content_hash)
                status = cache.snapshot()
                self.assertEqual(status['hits'], 1)
                self.assertGreater(status['misses'], 0)
                self.assertLessEqual(status['resident_bytes'], status['budget_bytes'])
            finally:
                archive.close()

    def test_completed_turn_is_exactly_offloaded_and_not_kept_in_the_live_transcript(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = EchoArchive(Path(folder) / 'memory.db')
            try:
                live = LiveTranscript(archive, 'turn-memory')
                count = lambda text: max(1, len(text) // 4)
                live.start_turn('Keep the running function unchanged', count)
                live.append({'role':'assistant','tool_calls':[{'id':'run-check','function':{
                    'name':'dev','arguments':'{"action":"run","file":"movement.py"}'}}]}, count)
                live.append({'role':'tool','tool_call_id':'run-check','content':'Distinctive assertion: running returns speed * 2.'}, count)
                live.append({'role':'assistant','content':'The existing running function is preserved.'}, count)
                live.open = False
                live.save()

                original_record = archive.record_source_event
                calls = 0
                def fail_after_one(message, conversation):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise OSError('simulated interruption during archive write')
                    return original_record(message, conversation)
                with patch.object(archive, 'record_source_event', side_effect=fail_after_one):
                    with self.assertRaisesRegex(OSError, 'simulated interruption'):
                        live.offload_completed()
                retry = LiveTranscript(archive, 'turn-memory')
                self.assertEqual(retry.turn_id, live.turn_id)
                moved = retry.offload_completed()
                retry.save()

                self.assertEqual(moved, 4)
                self.assertEqual(retry.messages, [])
                self.assertIsNone(retry.question)
                self.assertIsNone(retry.turn_id)
                restored = LiveTranscript(archive, 'turn-memory')
                self.assertEqual(restored.messages, [])
                self.assertEqual(restored.offloaded_messages, 4)
                events = archive.db.execute(
                    'SELECT role,content FROM source_events WHERE conversation_id=? ORDER BY timestamp,event_id',
                    ('turn-memory',)).fetchall()
                self.assertEqual({row['role'] for row in events}, {'user', 'assistant', 'tool'})
                self.assertEqual(len(events), 4)
                recalled = archive.retrieve('Distinctive assertion running speed', conversation_id='turn-memory')
                self.assertTrue(any('Distinctive assertion' in page.text for page in recalled.pages))
                self.assertEqual(retry.offload_completed(), 0)
            finally:
                archive.close()

    def test_history_import_skips_one_bad_event_and_keeps_reading(self):
        original = echo_import.EchoArchive.record_source_event

        def reject_one(archive, message, conversation_id):
            if message.get("content") == "broken event":
                raise ValueError("unsupported legacy field")
            return original(archive, message, conversation_id)

        payload = {"conversation_id": "history-test", "messages": [
            {"role": "assistant", "kind": "message", "content": "broken event"},
            {"role": "assistant", "kind": "message", "content": "valid reply"},
        ]}
        with tempfile.TemporaryDirectory() as folder, patch.object(echo_import.EchoArchive, "record_source_event", reject_one):
            result = import_stream(Path(folder), [json.dumps(payload)])
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertIn("unsupported legacy field", result["errors"][0])

    def test_image_parts_survive_echo_and_are_not_tokenized_as_base64(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), 0)
            try:
                state = EchoState(archives, 'http://127.0.0.1:1', 10000, 4, False)
                state._ctx_size = 32768
                counted = []
                def count(text):
                    counted.append(text)
                    return max(1, len(text)//4)
                state.count_tokens = count
                handler = object.__new__(Handler)
                handler.state = state
                seen = []
                def generate(body, phase):
                    seen.append(json.loads(json.dumps(body)))
                    return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'A blue spiral.'}}]}
                handler._generate_live = generate
                handler._send_json = lambda code, result: result
                image = {'type':'image_url','image_url':{'url':'data:image/png;base64,'+'AAAB'*4096}}
                parts = [{'type':'text','text':'Describe this image.'},image]
                handler._controlled_context({'messages':[{'role':'user','content':parts}], 'max_tokens':1024}, 'image')
                self.assertTrue(any(m.get('content') == parts for m in seen[0]['messages']))
                self.assertFalse(any('AAAB'*100 in text for text in counted))
                restored = LiveTranscript(archives.get('image'), 'image')
                self.assertEqual([message['role'] for message in restored.messages], ['user', 'assistant'])
                self.assertEqual(restored.messages[1]['content'], 'A blue spiral.')
                row = archives.get('image').db.execute(
                    "SELECT content FROM source_events WHERE conversation_id='image' AND role='user'").fetchone()
                saved_parts = json.loads(row['content'])
                self.assertTrue(saved_parts[1]['asset'].startswith('echo-asset:'))
                self.assertEqual(archives.get('image').db.execute(
                    'SELECT COUNT(DISTINCT asset_id) FROM source_event_assets').fetchone()[0], 1)
                self.assertGreaterEqual(LiveTranscript.cost({'content':parts},count),2048)
            finally:
                archives.close()

    def test_edit_requires_actual_patch_not_checkpoint_fields(self):
        tools = [{'function': {'name': 'dev', 'parameters': {}}}]
        def check(args):
            return tool_call_error({'tool_calls': [{'function': {'name': 'dev', 'arguments': json.dumps(args)}}]}, tools)
        invalid = {'action':'edit','paths':['movement.py'],'versionSha256':'old'}
        self.assertIn('expectedSha256', check(invalid))
        valid = {'action':'edit','path':'movement.py','expectedSha256':'current',
                 'edits':[{'oldText':'return speed','newText':'return speed + 1'}]}
        self.assertIsNone(check(valid))
        valid['edits'][0]['newText'] = ''
        self.assertIsNone(check(valid))
        valid['edits'] = []
        self.assertIsNotNone(check(valid))

    def test_review_sees_execution_receipts_and_keeps_room_for_its_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), 0)
            try:
                state = EchoState(archives, 'http://127.0.0.1:1', 10000, 4, False)
                state._ctx_size = 32768
                state.count_tokens = lambda text: max(1, len(text)//4)
                live = LiveTranscript(archives.get('review'), 'review')
                live.start_turn('Verify movement.py', state.count_tokens)
                call = {'id':'run1','function':{'name':'dev','arguments':'{"action":"run"}'}}
                receipt = '{"exitCode":0,"checked":{"movement.py":"source-hash"},"stdout":"assertions passed"}'
                live.append({'role':'assistant','tool_calls':[call]}, state.count_tokens)
                live.append({'role':'tool','tool_call_id':'run1','content':receipt}, state.count_tokens)
                live.save()
                handler = object.__new__(Handler)
                handler.state = state
                seen = []
                def generate(body, phase):
                    seen.append((body, phase))
                    return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'NO ISSUES' if phase == 'reviewing' else 'movement.py assertions passed.'}}]}
                handler._generate_live = generate
                handler._send_json = lambda code, result: result
                result = handler._controlled_context({'messages':[{'role':'user','content':'Verify movement.py'},
                    {'role':'assistant','tool_calls':[call]}, {'role':'tool','tool_call_id':'run1','content':receipt}],
                    'max_tokens':4096,'reasoning_budget_tokens':6000}, 'review', level='ultra')
                review_body = next(body for body, phase in seen if phase == 'reviewing')
                self.assertIn('source-hash', review_body['messages'][0]['content'])
                self.assertIn('"exitCode":0', review_body['messages'][0]['content'].replace('\\"','"'))
                self.assertLessEqual(review_body['reasoning_budget_tokens'],500)
                self.assertEqual(result['choices'][0]['message']['content'], 'movement.py assertions passed.')
            finally:
                archives.close()

    def test_new_turn_keeps_the_completed_transcript_live_for_dialogue_continuity(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), 0)
            try:
                state = EchoState(archives, 'http://127.0.0.1:1', 10000, 4, False)
                state._ctx_size = 8192
                state.count_tokens = lambda text: max(1, len(text) // 4)
                state.backend_session_metrics = lambda conversation: {'modelSessionTokens': 100}
                handler = object.__new__(Handler)
                handler.state = state
                seen = []
                answers = iter((
                    'Project decision: retain the stable feature key SPIRAL-ANCHOR-47.',
                    'The archived decision keeps SPIRAL-ANCHOR-47 unchanged.',
                    'This is a separate chat session.',
                ))

                def generate(body, phase):
                    seen.append(json.loads(json.dumps(body)))
                    return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':next(answers)}}]}

                handler._generate_live = generate
                handler._send_json = lambda code, result: result
                handler._controlled_context({'messages':[{'role':'user','content':'Keep the feature key.'}],
                                             'max_tokens':1024}, 'turn-scoped')
                after_first = LiveTranscript(archives.get('turn-scoped'), 'turn-scoped')
                self.assertEqual([message['role'] for message in after_first.messages], ['user', 'assistant'])
                self.assertEqual(after_first.offloaded_messages, 0)

                handler._controlled_context({'messages':[{'role':'user','content':'What key did we decide to keep?'}],
                                             'max_tokens':1024}, 'turn-scoped')
                second_messages = seen[1]['messages']
                self.assertTrue(seen[1]['echo_append'])
                self.assertEqual(seen[1]['id_slot'], 0)
                self.assertEqual(len(seen[1]['echo_session_id']), 64)
                self.assertEqual([message['role'] for message in second_messages], ['user'])
                self.assertIn('SPIRAL-ANCHOR-47', json.dumps(after_first.messages))
                self.assertIn('What key did we decide to keep?', json.dumps(second_messages))
                self.assertTrue(seen[0]['messages'][0]['role'] == 'system')
                self.assertFalse(seen[0]['echo_append'])
                self.assertTrue(after_first.entries[1]['backend_sent'])
                after_second = LiveTranscript(archives.get('turn-scoped'), 'turn-scoped')
                self.assertEqual([message['role'] for message in after_second.messages],
                                 ['user', 'assistant', 'user', 'assistant'])
                self.assertEqual(after_second.offloaded_messages, 0)

                handler._controlled_context({'messages':[{'role':'user','content':'Start a separate chat.'}],
                                             'max_tokens':1024}, 'another-chat')
                self.assertFalse(seen[2]['echo_append'])
                self.assertNotEqual(seen[1]['echo_session_id'], seen[2]['echo_session_id'])
                self.assertEqual([message['role'] for message in seen[2]['messages']], ['system', 'user'])
            finally:
                archives.close()

    def test_controlled_loop_repairs_json_without_replaying_it_and_preserves_output_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), 0)
            try:
                state = EchoState(archives, 'http://127.0.0.1:1', 10000, 4, False)
                state._ctx_size = 32768
                state.count_tokens = lambda text: max(1, len(text)//4)
                state.backend_session_metrics = lambda conversation: {'modelSessionTokens': 100}
                handler = object.__new__(Handler)
                handler.state = state
                seen = []
                tools = [{'type':'function','function':{'name':'dev','parameters':{'required':['action']}}}]
                def generate(body, phase):
                    seen.append(json.loads(json.dumps(body)))
                    arguments = '{"action":"edit","edits":"cut' if len(seen) == 1 else '{"action":"read"}'
                    return {'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':'',
                        'tool_calls':[{'id':str(len(seen)),'function':{'name':'dev','arguments':arguments}}]}}]}
                handler._generate_live = generate
                handler._send_json = lambda code, result: result
                result = handler._controlled_context({'messages':[{'role':'user','content':'Fix a small bug without replacing the game'}],
                    'tools':tools,'max_tokens':1024,'reasoning_budget_tokens':6000}, 'repair')
                self.assertEqual(len(seen),2)
                self.assertFalse(seen[0]['echo_append'])
                self.assertTrue(seen[1]['echo_append'])
                self.assertEqual([message['role'] for message in seen[1]['messages']], ['user'])
                self.assertLessEqual(seen[0]['reasoning_budget_tokens'],341)
                self.assertFalse(any(m.get('tool_calls') for m in seen[1]['messages']))
                self.assertEqual(json.loads(result['choices'][0]['message']['tool_calls'][0]['function']['arguments']), {'action':'read'})
                self.assertEqual(len(LiveTranscript(archives.get('repair'),'repair').pending_tool_calls()),1)
            finally:
                archives.close()

    def test_unshiftable_backend_rehydrates_a_bounded_prompt_without_losing_session_count(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), 0)
            try:
                state = EchoState(archives, 'http://127.0.0.1:1', 10000, 4, False)
                state._ctx_size = 8192
                state.count_tokens = lambda text: max(1, len(text) // 4)
                state.backend_session_metrics = lambda conversation: {'modelSessionTokens': 18000}
                handler = object.__new__(Handler)
                handler.state = state
                seen = []

                def generate(body, phase):
                    seen.append(json.loads(json.dumps(body)))
                    if len(seen) == 2:
                        raise urllib.error.HTTPError('http://backend', 400, 'context limit', {},
                            io.BytesIO(b'the active model memory cannot roll forward'))
                    answer = 'Original answer: keep the exact feature key FROST-LANTERN-28.' if len(seen) == 1 \
                        else 'The session continued from its compacted active transcript.'
                    return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':answer}}]}

                handler._generate_live = generate
                handler._send_json = lambda code, result: result
                handler._controlled_context({'messages':[{'role':'user','content':'Keep this feature key.'}],
                                             'max_tokens':1024}, 'rollover')
                result = handler._controlled_context({'messages':[{'role':'user','content':'Continue the feature.'}],
                                                      'max_tokens':1024}, 'rollover')

                self.assertTrue(seen[1]['echo_append'])
                self.assertTrue(seen[2]['echo_reset'])
                self.assertFalse(seen[2]['echo_append'])
                self.assertEqual(seen[2]['id_slot'], 0)
                self.assertIn('FROST-LANTERN-28', json.dumps(seen[2]['messages']))
                self.assertEqual(result['choices'][0]['message']['content'],
                                 'The session continued from its compacted active transcript.')
                self.assertNotIn('rollover', state._backend_append_disabled)
            finally:
                archives.close()

    def test_incomplete_tool_json_is_rejected_before_execution_or_replay(self):
        tools = [{'type':'function','function':{'name':'dev','parameters':{'required':['action']}}}]
        call = {'id':'bad','function':{'name':'dev','arguments':'{"action":"write","content":"cut'}}
        self.assertIn('JSON', tool_call_error({'tool_calls':[call]}, tools))
        call['function']['arguments'] = '{}'
        self.assertIn('action', tool_call_error({'tool_calls':[call]}, tools))
        call['function']['arguments'] = '{"action":"read"}'
        self.assertIsNone(tool_call_error({'tool_calls':[call]}, tools))

    def test_old_invalid_calls_are_archived_and_removed_from_backend_history(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = EchoArchive(Path(folder) / 'memory.db')
            try:
                live = LiveTranscript(archive, 'broken')
                count = lambda text: max(1, len(text)//4)
                live.start_turn('Fix the existing game', count)
                live.append({'role':'assistant','tool_calls':[{'id':'bad','function':{'name':'dev','arguments':'{"content":"truncated'}}]}, count)
                live.append({'role':'tool','tool_call_id':'bad','content':'action is required'}, count)
                self.assertEqual(live.repair_invalid_calls(count), 1)
                self.assertFalse(any(m.get('tool_calls') or m.get('role') == 'tool' for m in live.messages))
                self.assertTrue(archive.retrieve('truncated', conversation_id='broken').pages)
            finally:
                archive.close()

    def test_tokens_arrive_before_upstream_finishes(self):
        release = threading.Event()
        started = threading.Event()
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                started.set()
                self.send_response(200)
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                if body.get('stream'):
                    self.wfile.write(b'data: {"choices":[{"delta":{"reasoning_content":"Checking existing code"}}]}\n\n')
                    self.wfile.flush()
                    release.wait(5)
                    self.wfile.write(b'data: {"choices":[{"delta":{"content":"Preserved running."},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
                else:
                    release.wait(5)
                    self.wfile.write(json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'Preserved running.'}, 'finish_reason': 'stop'}]}).encode())

        with tempfile.TemporaryDirectory() as folder:
            upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
            archives = ArchiveSet(Path(folder), idle_seconds=0)
            state = EchoState(archives, 'http://127.0.0.1:%d' % upstream.server_port, 10000, 4, False)
            state._ctx_size = 8192
            state.count_tokens = lambda text: max(1, len(text) // 4)
            class EchoHandler(Handler):
                pass
            EchoHandler.state = state
            proxy = ThreadingHTTPServer(('127.0.0.1', 0), EchoHandler)
            for server in (upstream, proxy):
                threading.Thread(target=server.serve_forever, daemon=True).start()
            pool = concurrent.futures.ThreadPoolExecutor()
            first_line = concurrent.futures.Future()
            def observe():
                request = urllib.request.Request('http://127.0.0.1:%d/v1/chat/completions' % proxy.server_port,
                    data=json.dumps({'messages': [{'role': 'user', 'content': 'Add walking, keep running'}],
                                     'stream': True, 'max_tokens': 1024, 'reasoning_effort': 'off',
                                     'conversation_id': 'live-test'}).encode(), headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=8) as response:
                    while True:
                        line = response.readline()
                        if b'Checking existing code' in line:
                            first_line.set_result(line)
                            break
                        if not line:
                            raise AssertionError('No live reasoning delta')
                    return response.read()
            task = pool.submit(observe)
            try:
                self.assertTrue(started.wait(2))
                self.assertIn(b'Checking existing code', first_line.result(timeout=1))
                self.assertFalse(release.is_set())
                release.set()
                tail = task.result(timeout=3)
                self.assertIn(b'Preserved running.', tail)
                self.assertIn(b'[DONE]', tail)
            finally:
                release.set()
                pool.shutdown(wait=True)
                for server in (proxy, upstream):
                    server.shutdown()
                    server.server_close()
                for archive in archives._open.values():
                    archive.close()

    def test_single_long_task_evicts_completed_tools_but_preserves_exact_code(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = EchoArchive(Path(folder) / 'memory.db')
            try:
                live = LiveTranscript(archive, 'game')
                count = lambda text: max(1, len(text) // 4)
                live.start_turn('Build walking while preserving the running system', count)
                code = 'function running() { return speed * 2; }\n' * 100
                for index in range(4):
                    live.append({'role': 'assistant', 'content': '', 'tool_calls': [
                        {'id': str(index), 'type': 'function', 'function': {'name': 'dev', 'arguments': '{}'}}]}, count)
                    live.append({'role': 'tool', 'tool_call_id': str(index), 'content': code}, count)
                live.append({'role': 'assistant', 'content': '', 'tool_calls': [
                    {'id': 'pending', 'type': 'function', 'function': {'name': 'dev', 'arguments': '{}'}}]}, count)
                before = live.tokens
                self.assertGreater(live.compact(1800, count), 0)
                self.assertLess(live.tokens, before)
                self.assertEqual(live.pending_tool_calls(), ['pending'])
                self.assertIn('Build walking', json.dumps(live.messages))
                pages = archive.retrieve('function running speed', conversation_id='game').pages
                self.assertTrue(any('function running() { return speed * 2; }' in page.text for page in pages))
                live.save()
                restored = LiveTranscript(archive, 'game')
                self.assertEqual(restored.messages, live.messages)
            finally:
                archive.close()

    def test_tool_arguments_stream_without_executing_partial_json(self):
        handler = object.__new__(Handler)
        handler.wfile = io.BytesIO()
        handler.send_response = lambda *args: None
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        events = [
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call1', 'function': {'name': 'dev', 'arguments': '{"action":'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"checkpoint"}'}}]}, 'finish_reason': 'tool_calls'}]},
        ]
        wire = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'
        handler._upstream = lambda *args, **kwargs: io.BytesIO(wire.encode())
        result = handler._generate_live({'stream': True}, 'working')
        self.assertEqual(result['choices'][0]['message']['tool_calls'][0]['function']['arguments'], '{"action":"checkpoint"}')
        self.assertEqual(handler.wfile.getvalue().count(b'echo_preview'), 2)

    def test_forwards_twincore_previews_without_appending_them_to_final_answer(self):
        handler = object.__new__(Handler)
        handler.wfile = io.BytesIO()
        handler.send_response = lambda *args: None
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        events = [
            {'echo_preview': {'generation': 'twin-1', 'phase': 'drafting', 'delta': {'content': 'draft words'}}},
            {'choices': [{'delta': {'role': 'assistant', 'content': 'final answer'}, 'finish_reason': 'stop'}]},
        ]
        wire = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'
        handler._upstream = lambda *args, **kwargs: io.BytesIO(wire.encode())

        result = handler._generate_live({'stream': True}, 'working')

        self.assertEqual(result['choices'][0]['message']['content'], 'final answer')
        streamed = handler.wfile.getvalue().decode()
        self.assertIn('"generation": "twin-1"', streamed)
        self.assertIn('"phase": "drafting"', streamed)
        self.assertIn('draft words', streamed)
        self.assertNotIn('draft wordsfinal answer', streamed)

    def test_checkpoint_result_releases_completed_work_before_next_model_call(self):
        with tempfile.TemporaryDirectory() as folder:
            archives = ArchiveSet(Path(folder), 0)
            state = EchoState(archives, 'http://127.0.0.1:1', 10000, 4, False, automatic_recall_tokens=0)
            state._ctx_size = 32768
            state.count_tokens = lambda text: max(1, len(text) // 4)
            handler = object.__new__(Handler)
            handler.state = state
            handler.wfile = io.BytesIO()
            handler.send_response = lambda *args: None
            handler.send_header = lambda *args: None
            handler.end_headers = lambda: None
            archive = archives.get('game')
            live = LiveTranscript(archive, 'game')
            live.start_turn('Add running and walking', state.count_tokens)
            for index in range(4):
                live.append({'role': 'assistant', 'content': 'Completed running step ' + str(index) + ' x' * 4000}, state.count_tokens)
            call = {'id': 'checkpoint', 'type': 'function', 'function': {'name': 'dev', 'arguments': '{"action":"checkpoint"}'}}
            live.append({'role': 'assistant', 'content': '', 'tool_calls': [call]}, state.count_tokens)
            live.model_fingerprint = state.memory_adapter().identity
            live.mark_backend_sent()
            state._backend_conversation = 'game'
            state.backend_session_metrics = lambda conversation: {'modelSessionTokens': 9000}
            live.save()
            seen = []
            bodies = []
            def generate(body, phase):
                bodies.append(body)
                seen.append(json.dumps(body['messages']))
                return {'choices': [{'message': {'role': 'assistant', 'content': 'Walking is next.'}, 'finish_reason': 'stop'}]}
            handler._generate_live = generate
            try:
                handler._controlled_context({'messages': [
                    {'role': 'user', 'content': 'Add running and walking'},
                    {'role': 'assistant', 'content': '', 'tool_calls': [call]},
                    {'role': 'tool', 'tool_call_id': 'checkpoint', 'content': '{"checkpointSaved":true,"title":"Running","nextSteps":"Walking"}'},
                ], 'max_tokens': 1024}, 'game')
                self.assertIn('Add running and walking', seen[0])
                self.assertIn('checkpointSaved', seen[0])
                self.assertTrue(bodies[0].get('echo_reset'))
                self.assertFalse(bodies[0].get('echo_append'))
                self.assertNotIn('Completed running step 0', seen[0])
                self.assertTrue(archive.retrieve('Completed running step', conversation_id='game').pages)
            finally:
                archives.close()


if __name__ == '__main__':
    unittest.main()
