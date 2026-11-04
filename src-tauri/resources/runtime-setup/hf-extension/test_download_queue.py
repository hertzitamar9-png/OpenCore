"""Small in-memory transfers exercise the durable scheduler, never user weights."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from hf_download import Paused
from hf_download_queue import DownloadQueue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'queue.json'
        self.started, self.release = [], {}
        self.guard = threading.Lock()

    def resolve(self, value):
        if value.get('command') not in ('install_model', 'runtime_setup_start'):
            raise ValueError('Unsupported queue command')
        return {**value, 'key': value['modelId'], 'totalBytes': 100}

    def execute(self, item, pause, cancel, report):
        with self.guard:
            self.started.append(item['modelId'])
            ready = self.release.setdefault(item['modelId'], threading.Event())
        report({'stage': 'downloading', 'bytesDone': 20, 'bytesTotal': 100})
        while not ready.wait(.01):
            if pause.is_set():
                raise Paused('Paused')
        report({'stage': 'installed', 'bytesDone': 100, 'bytesTotal': 100})

    def queue(self):
        value = DownloadQueue(self.path, self.resolve, self.execute)
        self.addCleanup(value.close)
        return value

    def item(self, name):
        return {'modelId': name, 'label': name, 'command': 'install_model', 'args': {'id': name}}

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if condition(): return
            time.sleep(.01)
        self.fail('Scheduler did not reach the expected state')

    def test_fifo_starts_next_without_any_ui_poll_and_deduplicates_repeated_click(self):
        queue = self.queue()
        queue.action({'action': 'enqueue', 'items': [self.item('one'), self.item('two')]})
        queue.action({'action': 'enqueue', 'items': [self.item('one')]})
        self.wait_for(lambda: self.started == ['one'])
        self.assertEqual(len(queue.snapshot()['items']), 2)
        queue.action({'action': 'enqueue', 'items': [self.item('three')]})
        self.release['one'].set()
        self.wait_for(lambda: self.started == ['one', 'two'])
        self.release['two'].set()
        self.wait_for(lambda: self.started == ['one', 'two', 'three'])
        self.release['three'].set()
        self.wait_for(lambda: all(x['phase'] == 'ready' for x in queue.snapshot()['items']))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['schema'], 1)

    def test_parallel_remove_cancels_only_its_owned_worker(self):
        queue = self.queue()
        queue.action({'action': 'mode', 'mode': 'parallel'})
        queue.action({'action': 'enqueue', 'items': [self.item('one'), self.item('two')]})
        self.wait_for(lambda: len(self.started) == 2)
        state = queue.snapshot()
        self.assertEqual(len(state['activeIds']), 2)
        first = state['items'][0]['id']
        queue.action({'action': 'remove', 'id': first})
        self.wait_for(lambda: queue.snapshot()['items'][0]['phase'] == 'cancelled')
        self.assertEqual(queue.snapshot()['items'][1]['phase'], 'downloading')
        self.release['two'].set()
        self.wait_for(lambda: queue.snapshot()['items'][1]['phase'] == 'ready')

    def test_restart_restores_pending_fifo_and_mode_without_ui_timer(self):
        first = self.queue()
        first.action({'action': 'enqueue', 'items': [self.item('one'), self.item('two')]})
        self.wait_for(lambda: self.started == ['one'])
        first.close()
        self.started = []
        second = self.queue()
        self.wait_for(lambda: self.started == ['one'])
        self.assertEqual([i['modelId'] for i in second.snapshot()['items']], ['one', 'two'])
        self.release['one'].set()
        self.wait_for(lambda: self.started == ['one', 'two'])

    def test_global_pause_is_durable_and_resume_releases_pending(self):
        queue = self.queue()
        queue.action({'action': 'pause'})
        state = queue.action({'action': 'enqueue', 'items': [self.item('one')]})
        self.assertTrue(state['paused'])
        self.assertEqual(state['activeIds'], [])
        queue.close()
        queue = self.queue()
        self.assertTrue(queue.snapshot()['paused'])
        queue.action({'action': 'resume'})
        self.wait_for(lambda: self.started == ['one'])

    def test_invalid_batch_is_atomic_and_does_not_start_arbitrary_commands(self):
        queue = self.queue()
        with self.assertRaises(ValueError):
            queue.action({'action': 'enqueue', 'items': [self.item('one'), {**self.item('bad'), 'command': 'shell'}]})
        self.assertEqual(queue.snapshot()['items'], [])


if __name__ == '__main__':
    unittest.main()
