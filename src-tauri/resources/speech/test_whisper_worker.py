import unittest

from whisper_worker import transcribe_code_switched, transcribe_ct2, CTranslateWhisper
from types import SimpleNamespace


class FakeRecognizer:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return self.result


class CodeSwitchedTranscriptionTests(unittest.TestCase):
    def ct2_fixture(self):
        engine=CTranslateWhisper.__new__(CTranslateWhisper)
        engine.directory='fixture'
        engine.minimum_gpu=4*1024**3
        engine.torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:True,mem_get_info=lambda device:(8*1024**3,12*1024**3)))
        engine.psutil=SimpleNamespace(virtual_memory=lambda:SimpleNamespace(available=32*1024**3))
        engine.origin='cpu';engine.device='cpu'
        engine.model=SimpleNamespace(model=SimpleNamespace(unload_model=lambda **kwargs:None))
        return engine

    def test_ct2_gpu_load_oom_restores_cpu_instead_of_losing_standby(self):
        engine=self.ct2_fixture();attempts=[]
        def factory(directory,**kwargs):
            attempts.append(kwargs['device'])
            if kwargs['device']=='cuda':
                raise RuntimeError('CUDA failed with error out of memory')
            return SimpleNamespace(model=SimpleNamespace(unload_model=lambda **kwargs:None))
        engine.factory=factory
        engine.activate()
        self.assertEqual(attempts,['cuda','cpu'])
        self.assertEqual(engine.device,'cpu')
        self.assertIsNotNone(engine.model)

    def test_ct2_lazy_decode_oom_retries_complete_recording_on_cpu(self):
        engine=self.ct2_fixture();engine.origin='cuda';engine.device='cuda:0'
        def failed_segments():
            yield SimpleNamespace(text='Incomplete')
            raise RuntimeError('CUDA failed with error out of memory')
        engine.model.transcribe=lambda audio,**kwargs:(failed_segments(),SimpleNamespace(language='en'))
        cpu_model=SimpleNamespace(transcribe=lambda audio,**kwargs:(iter([SimpleNamespace(text='Complete recording')]),SimpleNamespace(language='en')))
        engine.factory=lambda directory,**kwargs:cpu_model
        self.assertEqual(engine.transcribe([0.0]),{'text':'Complete recording','language':'en'})
        self.assertEqual(engine.device,'cpu')

    def test_full_v3_detects_languages_and_uses_transcription(self):
        class Model:
            def transcribe(self, audio, **kwargs):
                self.options = kwargs
                return iter([SimpleNamespace(text='Hello'), SimpleNamespace(text='שלום')]), SimpleNamespace(language='he')
        model = Model()
        self.assertEqual(transcribe_ct2(model, [0.0]), {'text':'Hello שלום', 'language':'he'})
        self.assertTrue(model.options['multilingual'])
        self.assertIsNone(model.options['language'])
        self.assertEqual(model.options['task'], 'transcribe')

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
