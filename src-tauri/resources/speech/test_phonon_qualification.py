"""The long-fixture spelling allowance must never hide a speech error."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('qualify_phonon', Path(__file__).resolve().parents[3] / 'scripts/qualify-phonon-original.py')
qualification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualification)


class TranscriptGate(unittest.TestCase):
    def metrics(self):
        short = 'Open core can recognize the sentence. Both precision options should work correctly.'
        long = ' '.join([short.replace('Open core', 'Opencore')] * 10)
        return {label: {'texts': [short, short, long]} for label in ('publisher', 'minimal', 'minimalRepeat')}

    def test_only_named_product_orthography_is_allowed_on_the_long_fixture(self):
        metrics = self.metrics()
        for label in ('minimal', 'minimalRepeat'):
            metrics[label]['texts'][2] = metrics[label]['texts'][2].replace('Opencore', 'Open core')
        self.assertFalse(qualification.require_transcript_parity(metrics)['rawLongAudioEqual'])
        for wrong in ('Opencore cannot recognize the sentence.', 'Opencorecan recognize the sentence.', ''):
            broken = self.metrics()
            broken['minimal']['texts'][2] = broken['minimalRepeat']['texts'][2] = wrong
            with self.subTest(wrong=wrong), self.assertRaises(AssertionError):
                qualification.require_transcript_parity(broken)

    def test_quiet_audio_and_repeat_must_still_match_raw_text(self):
        for label, index in (('minimal', 1), ('minimalRepeat', 2)):
            metrics = self.metrics()
            metrics[label]['texts'][index] += ' Extra words.'
            with self.subTest(label=label), self.assertRaises(AssertionError):
                qualification.require_transcript_parity(metrics)
