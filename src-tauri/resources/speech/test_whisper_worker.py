import unittest

from whisper_worker import transcribe_code_switched


class FakeRecognizer:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return self.result


class CodeSwitchedTranscriptionTests(unittest.TestCase):
    def test_keeps_transcribe_autodetection_and_short_overlapping_chunks(self):
        expected = {"text": "Una español, I am Itamar, אני אוהב שניצל.", "language": "auto"}
        recognizer = FakeRecognizer(expected)
        audio = [0.0] * (16000 * 12)

        result = transcribe_code_switched(recognizer, audio)

        self.assertEqual(result, expected)
        self.assertEqual(len(recognizer.calls), 1)
        payload, options = recognizer.calls[0]
        self.assertEqual(payload["sampling_rate"], 16000)
        self.assertIs(payload["array"], audio)
        self.assertEqual(options["chunk_length_s"], 3)
        self.assertEqual(options["stride_length_s"], (0.75, 0.75))
        self.assertEqual(options["generate_kwargs"], {
            "task": "transcribe",
            "language": None,
            "forced_decoder_ids": None,
        })
        self.assertTrue(options["return_language"])


if __name__ == "__main__":
    unittest.main()
