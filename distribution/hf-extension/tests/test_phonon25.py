"""Dictation regressions for the separately pinned multilingual Phonon runtime."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src-tauri/resources/runtime-setup/hf-extension'))
from hf_speech import SpeechManager, decode_audio, supports


class Phonon25Tests(unittest.TestCase):
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
