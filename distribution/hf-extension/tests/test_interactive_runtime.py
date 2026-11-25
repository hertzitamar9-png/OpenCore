"""Protocol and lifecycle boundaries for the owned live-audio and pointer workers."""
import base64
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import wave
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src-tauri/resources/runtime-setup/hf-extension'))
from hf_interactive import validate_frame, validate_reference, screen_point, InteractiveManager


class InteractiveProtocolTests(unittest.TestCase):
    def test_duplex_frames_require_exactly_one_133ms_pcm_frame(self):
        encoded = base64.b64encode(bytes(6400)).decode()
        self.assertEqual(len(validate_frame(encoded)), 6400)
        for value in ('invalid!', base64.b64encode(bytes(6398)).decode(), '', None):
            with self.assertRaises(ValueError): validate_frame(value)

    def test_reference_recordings_are_bounded_and_validated_before_worker_start(self):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as clip:
            clip.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            clip.writeframes(bytes(24000 * 2 * 3))
        encoded = base64.b64encode(buffer.getvalue()).decode()
        self.assertEqual(validate_reference(encoded), encoded)
        for value in ('bad', base64.b64encode(b'not audio').decode(), 'A' * 8_000_001):
            with self.assertRaises(ValueError): validate_reference(value)

    def test_pointer_grid_maps_to_original_screen_pixels_and_rejects_nonfinite_scores(self):
        self.assertEqual(screen_point([0, 0, 0, 1], 800, 600, 2, 2),
                         {'x': 600, 'y': 450, 'normalizedX': .75, 'normalizedY': .75})
        with self.assertRaises(ValueError): screen_point([float('nan')], 800, 600, 1, 1)
        with self.assertRaises(ValueError): screen_point([1, 2], 800, 600, 1, 1)

    def test_stale_hangup_cannot_stop_a_new_call(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = InteractiveManager(folder, folder)
            manager.session_id = 'new-call'
            manager.child = Mock()
            self.assertEqual(manager.stop('old-call'), {'stopped': False})
            manager.child.terminate.assert_not_called()

    def test_double_hangup_releases_the_call_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = InteractiveManager(folder, folder)
            manager.session_id = 'call'
            manager.operation.acquire()
            entered,release=threading.Event(),threading.Event()
            def dispose(*args):entered.set();release.wait(2)
            with patch.object(manager,'_dispose_child',side_effect=dispose):
                first=threading.Thread(target=manager.stop,args=('call',));first.start()
                self.assertTrue(entered.wait(1))
                self.assertEqual(manager.stop('call'),{'stopped':False})
                self.assertTrue(manager.operation.locked())
                release.set();first.join(2)
            self.assertFalse(manager.operation.locked())

    def test_stale_loader_never_publishes_worker_or_message_queue_into_a_new_call(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=InteractiveManager(folder,folder);manager.session_id='new'
            sentinel=object();manager.messages=sentinel
            with patch.object(manager,'ready',return_value=True),patch('hf_interactive.subprocess.Popen') as launch:
                with self.assertRaisesRegex(RuntimeError,'cancelled'):
                    manager._launch({'id':'old'},'haru',session_id='old')
            launch.assert_not_called();self.assertIs(manager.messages,sentinel)


if __name__ == '__main__': unittest.main()
