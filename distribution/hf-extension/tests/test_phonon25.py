"""Dictation regressions for the separately pinned multilingual Phonon runtime."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src-tauri/resources/runtime-setup/hf-extension'))
from hf_speech import SpeechManager, decode_audio, supports


class Phonon25Tests(unittest.TestCase):
    def test_cancelled_load_cannot_report_recording_after_health_returns(self):
        child = Mock()
        child.poll.return_value = None

        def health(*args, **kwargs):
            self.manager.cancel('cancel-at-health')
            return io.BytesIO(b'{"status":"ok"}')

        with patch.object(self.manager, 'ready', return_value=True):
            self.manager.select(self.entry['id'])
            with patch.object(self.manager, '_engine', return_value=self.root), \
                    patch.object(self.manager, '_model_path', return_value=self.root / 'model.gguf'), \
                    patch('hf_speech.subprocess.Popen', return_value=child), \
                    patch('hf_speech.urlopen', side_effect=health):
                with self.assertRaisesRegex(ValueError, 'cancelled'):
                    self.manager.start('cancel-at-health')
        self.assertIsNone(self.manager.session_id)
        self.assertEqual(self.manager.phase, 'idle')

    def test_failed_worker_launch_releases_the_microphone_for_retry(self):
        with patch.object(self.manager, 'ready', return_value=True):
            self.manager.select(self.entry['id'])
            with patch.object(self.manager, '_engine', return_value=self.root), \
                    patch.object(self.manager, '_model_path', return_value=self.root / 'model.gguf'), \
                    patch('hf_speech.subprocess.Popen', side_effect=OSError('fixture launch failure')):
                with self.assertRaises((OSError, ValueError)):
                    self.manager.start('failed-start')
        self.assertIsNone(self.manager.session_id)
        self.assertEqual(self.manager.phase, 'idle')

    def test_warm_worker_reports_recording_on_the_next_session(self):
        child = Mock()
        child.poll.return_value = None
        with patch.object(self.manager, 'ready', return_value=True):
            self.manager.select(self.entry['id'])
            self.manager.child = child
            self.manager.phase = 'idle'
            self.assertEqual(self.manager.start('warm-next-session'), 'warm-next-session')
        self.assertEqual(self.manager.phase, 'recording')

    def test_saved_speech_settings_reject_invalid_field_types(self):
        self.manager.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.manager.settings_path.write_text(json.dumps({
            'modelId': ['invalid'], 'enabled': 'false', 'idleMode': 'unknown',
        }), encoding='utf-8')
        restored = SpeechManager(self.root, self.root / 'resources', lambda identity: self.entry)
        self.addCleanup(restored.close)
        self.assertIsNone(restored.status())
        self.assertEqual(restored.settings, {'modelId': None, 'enabled': False, 'idleMode': 'ram'})

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manager = SpeechManager(self.root, self.root / 'resources', lambda identity: self.entry)
        self.addCleanup(self.manager.close)
        self.entry = {'id': 'hf-' + 'a' * 24, 'speechRuntime': 'parakeet-phonon25-v1',
                      'repository': 'mudler/parakeet-cpp-gguf',
                      'revision': '07a08a3a405d20c5434b9a1442f6e6cf571f57ad',
                      'speechQuantization': 'packed', 'status': 'installed'}

    def test_only_reviewed_multilingual_packages_use_this_decoder(self):
        self.assertTrue(supports(self.entry))
        self.assertFalse(supports(dict(self.entry, revision='0' * 40)))
        self.assertFalse(supports(dict(self.entry, repository='FermionResearch/Phonon-2.5-English')))

    def test_selecting_for_dictation_requires_a_verified_runtime(self):
        with self.assertRaisesRegex(ValueError, 'Finish'):
            self.manager.select(self.entry['id'])
        self.assertIsNone(self.manager.status())

    def test_selection_enable_and_idle_mode_survive_restart(self):
        with patch.object(self.manager, 'ready', return_value=True):
            self.manager.select(self.entry['id'])
            self.manager.configure('enabled', False)
            self.manager.configure('idleMode', 'ram')
        other = SpeechManager(self.root, self.root / 'resources', lambda identity: self.entry)
        self.addCleanup(other.close)
        self.assertFalse(other.status()['enabled'])
        self.assertEqual(other.status()['idleMode'], 'ram')
        self.assertEqual(other.status()['modelId'], self.entry['id'])

    def test_late_session_cancellation_cannot_stop_a_new_recording(self):
        self.manager.session_id = 'new-session'
        with patch.object(self.manager, '_stop') as stop:
            self.manager.cancel('old-session')
            stop.assert_not_called()
        self.assertEqual(self.manager.session_id, 'new-session')

    def test_native_selection_releases_managed_dictation(self):
        with patch.object(self.manager, 'ready', return_value=True):
            self.manager.select(self.entry['id'])
        self.manager.select(None)
        self.assertIsNone(self.manager.status())

    def test_audio_requires_a_bounded_wave_recording(self):
        with self.assertRaisesRegex(ValueError, 'WAV'):
            decode_audio(base64.b64encode(b'not audio').decode())
        with self.assertRaises(ValueError):
            decode_audio('%%%')


if __name__ == '__main__':
    unittest.main()
