"""Offline regressions for the managed text-to-speech packages (Kitten TTS 2, VoxCPM2, Breeze-TTS-2).

Nothing here downloads a model or starts the speech worker: the catalog is built from the
reviewed Hugging Face snapshots, and job execution is replaced by a fake that writes a WAV.
"""
import base64
import copy
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
import wave

REPO = Path(__file__).resolve().parents[3]
DISTRIBUTION = REPO / 'distribution'
EXTENSION = REPO / 'src-tauri/resources/runtime-setup/hf-extension'
for folder in (DISTRIBUTION, EXTENSION):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import hf_tts  # noqa: E402
from hf_download import validate_entry  # noqa: E402
from tts_catalog import PACKAGES, append_tts  # noqa: E402

SNAPSHOTS = DISTRIBUTION / 'hf-research/requested-models'


def wav(seconds=2.0, rate=24000, channels=1, width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(b'\x10\x00' * int(seconds * rate) * channels if width == 2 else b'\x80' * int(seconds * rate) * channels)
    return base64.b64encode(buffer.getvalue()).decode()


def entries():
    return {entry['ttsRuntime']: entry for entry in append_tts([], SNAPSHOTS)}


def installed(entry, root):
    value = copy.deepcopy(entry)
    value.update(status='installed', localDirectory=str(Path(root) / 'models/hub' / entry['id']))
    return value


class CatalogTests(unittest.TestCase):
    def test_release_package_ships_the_speech_runtime(self):
        mappings = json.loads((REPO / 'distribution/resource-map.json').read_text(encoding='utf-8'))
        for name in ('hf_tts.py', 'tts_worker.py', 'tts-speech-lock.txt', 'hf_bridge.py', 'publisher-models.json'):
            target = 'runtime-setup/hf-extension/' + name
            with self.subTest(file=name):
                self.assertEqual(mappings.get(target), 'src-tauri/resources/' + target)
                self.assertTrue((REPO / mappings[target]).is_file())

    def test_three_pinned_packages_match_the_reviewed_runtime_revisions(self):
        values = entries()
        self.assertEqual(set(values), {'kitten-tts-2-v1', 'voxcpm2-v1', 'breeze-tts-2-v1'})
        for runtime, entry in values.items():
            family = hf_tts.FAMILIES[runtime]
            self.assertEqual(entry['repository'], family['repository'])
            self.assertEqual(entry['revision'], family['revision'])
            self.assertEqual((entry['category'], entry['libraryCategory']), ('tts', 'tts'))
            self.assertEqual(entry['runtime']['adapter'], 'managed-tts')
            self.assertFalse(entry['runtime']['inferenceVerified'])
            self.assertTrue(entry['installable'])
            self.assertTrue(all(pin['pinned'] and pin['revision'] == family['revision'] for pin in entry['files']))
            self.assertTrue(any(pin['role'] == 'model' for pin in entry['files']))
            self.assertTrue(hf_tts.supports(entry))
            validate_entry(entry)

    def test_packages_leave_out_files_the_runtime_never_loads(self):
        values = entries()
        names = {pin['filename'] for pin in values['kitten-tts-2-v1']['files']}
        self.assertNotIn('lm/model.safetensors', names)
        self.assertNotIn('lm/model-tl2-emb4.safetensors', names)
        self.assertTrue(any(name.startswith('voices/') for name in names))
        breeze = {pin['filename'] for pin in values['breeze-tts-2-v1']['files']}
        self.assertFalse(any(name.startswith('assets/') for name in breeze))
        self.assertIn('Non-commercial', next(p for p in PACKAGES if p['runtime'] == 'breeze-tts-2-v1')['description'])
        vox = {pin['filename'] for pin in values['voxcpm2-v1']['files']}
        self.assertNotIn('tokenization_voxcpm2.py', vox)
        self.assertTrue({'model.safetensors', 'audiovae.pth', 'tokenizer.json'} <= vox)
        for entry in values.values():
            self.assertFalse([pin['filename'] for pin in entry['files'] if pin['filename'].endswith(('.py', '.sh', '.exe', '.dll'))])

    def test_kitten_counts_the_vocoder_component_bytes(self):
        values = entries()
        self.assertEqual(values['kitten-tts-2-v1']['ttsComponentBytes'], hf_tts.COMPONENTS['s3genMeanflow']['bytes'])
        self.assertEqual(values['voxcpm2-v1']['ttsComponentBytes'], 0)

    def test_a_snapshot_that_is_not_the_reviewed_checkpoint_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for source in SNAPSHOTS.glob('*.api.json'):
                (folder / source.name).write_bytes(source.read_bytes())
            target = folder / 'openbmb--VoxCPM2.api.json'
            value = json.loads(target.read_text(encoding='utf-8'))
            value['sha'] = '0' * 40
            target.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ValueError):
                append_tts([], folder)

    def test_rebuilding_replaces_earlier_tts_cards_instead_of_duplicating_them(self):
        first = append_tts([], SNAPSHOTS)
        again = append_tts(first, SNAPSHOTS)
        self.assertEqual(len(again), 3)


class RequestValidationTests(unittest.TestCase):
    def setUp(self):
        self.values = entries()

    def check(self, runtime, text='Hello there.', **settings):
        entry = self.values[runtime]
        return hf_tts.validate_request(entry, {'modelId': entry['id'], 'text': text, 'settings': settings})

    def test_text_limits_follow_each_family(self):
        for runtime, limit in (('kitten-tts-2-v1', 5000), ('voxcpm2-v1', 2000), ('breeze-tts-2-v1', 800)):
            self.assertEqual(self.check(runtime, 'a' * limit)['text'], 'a' * limit)
            with self.assertRaises(ValueError):
                self.check(runtime, 'a' * (limit + 1))
            with self.assertRaises(ValueError):
                self.check(runtime, '   ')
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', 'bad\x07bell')
        self.assertEqual(self.check('kitten-tts-2-v1', 'Line one\nLine two\tend')['text'], 'Line one\nLine two\tend')

    def test_unknown_controls_are_refused(self):
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', speed=2)

    def test_seed_defaults_and_bounds(self):
        self.assertEqual(self.check('kitten-tts-2-v1')['settings']['seed'], 1234)
        self.assertEqual(self.check('kitten-tts-2-v1', seed=7)['settings']['seed'], 7)
        for bad in (-1, 2 ** 31, 1.5, True, '7'):
            with self.assertRaises(ValueError):
                self.check('kitten-tts-2-v1', seed=bad)

    def test_reference_transcript_rules(self):
        clip = wav()
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', referenceAudio=clip)
        with self.assertRaises(ValueError):
            self.check('breeze-tts-2-v1', referenceAudio=clip, referenceText='  ')
        self.assertEqual(self.check('kitten-tts-2-v1', referenceAudio=clip, referenceText=' Words. ')['settings']['referenceText'], 'Words.')
        self.assertEqual(self.check('voxcpm2-v1', referenceAudio=clip)['settings']['referenceText'], '')
        self.assertIsNotNone(self.check('voxcpm2-v1', referenceAudio=clip)['referenceAudio'])
        with self.assertRaises(ValueError):
            self.check('voxcpm2-v1', referenceText='No recording attached')
        with self.assertRaises(ValueError):
            self.check('voxcpm2-v1', referenceAudio=clip, referenceText='x' * 2001)

    def test_instruction_only_for_families_that_take_one(self):
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', instruction='Speak warmly')
        self.assertEqual(self.check('voxcpm2-v1', instruction=' A calm narrator ')['settings']['instruction'], 'A calm narrator')
        self.assertEqual(self.check('breeze-tts-2-v1', instruction='Speak cheerfully')['settings']['instruction'], 'Speak cheerfully')
        with self.assertRaises(ValueError):
            self.check('voxcpm2-v1', instruction='x' * 501)

    def test_voice_and_preset_are_kitten_controls_and_a_reference_replaces_the_voice(self):
        self.assertEqual(self.check('kitten-tts-2-v1', voice='Bella', preset='expressive')['settings'],
                         {'seed': 1234, 'voice': 'Bella', 'preset': 'expressive'})
        cloned = self.check('kitten-tts-2-v1', voice='Bella', referenceAudio=wav(), referenceText='Words.')
        self.assertNotIn('voice', cloned['settings'])
        for runtime in ('voxcpm2-v1', 'breeze-tts-2-v1'):
            with self.assertRaises(ValueError):
                self.check(runtime, voice='Bella')
            with self.assertRaises(ValueError):
                self.check(runtime, preset='stable')
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', preset='loud')

    def test_cfg_and_steps_ranges(self):
        self.assertEqual(self.check('voxcpm2-v1', cfg=2.5, steps=12)['settings']['steps'], 12)
        self.assertEqual(self.check('breeze-tts-2-v1', cfg=4)['settings']['cfg'], 4)
        for runtime, settings in (('voxcpm2-v1', {'cfg': 0.5}), ('voxcpm2-v1', {'steps': 31}), ('voxcpm2-v1', {'steps': 10.0}),
                                  ('breeze-tts-2-v1', {'cfg': 6.5}), ('breeze-tts-2-v1', {'steps': 10}),
                                  ('kitten-tts-2-v1', {'cfg': 2.0}), ('kitten-tts-2-v1', {'steps': 10})):
            with self.assertRaises(ValueError, msg=(runtime, settings)):
                self.check(runtime, **settings)

    def test_sampling_controls_only_for_kitten(self):
        value = self.check('kitten-tts-2-v1', temperature=0.8, topP=0.9, topK=50, minP=0.05, maxNewTokens=1000)['settings']
        self.assertEqual((value['temperature'], value['topK']), (0.8, 50))
        with self.assertRaises(ValueError):
            self.check('kitten-tts-2-v1', temperature=3)
        for runtime in ('voxcpm2-v1', 'breeze-tts-2-v1'):
            with self.assertRaises(ValueError):
                self.check(runtime, temperature=0.8)

    def test_normalize_is_a_kitten_switch(self):
        self.assertIs(self.check('kitten-tts-2-v1', normalize=False)['settings']['normalize'], False)
        self.assertNotIn('normalize', self.check('voxcpm2-v1', normalize=False)['settings'])
        for runtime, value in (('kitten-tts-2-v1', 1), ('voxcpm2-v1', True)):
            with self.assertRaises(ValueError):
                self.check(runtime, normalize=value)

    def test_reference_audio_must_be_short_mono_pcm(self):
        self.assertTrue(hf_tts.decode_reference(wav(1.0, 16000)).startswith(b'RIFF'))
        for bad in (wav(2.0, channels=2), wav(2.0, width=1), wav(0.5), wav(61.0, 8000), wav(2.0, rate=96000),
                    'not base64!', base64.b64encode(b'RIFF but not a wave').decode(), 12345,
                    'A' * ((hf_tts.MAX_REFERENCE_BYTES + 2) // 3 * 4 + 4)):
            with self.assertRaises(ValueError):
                hf_tts.decode_reference(bad)


def fake_execute(manager, job, entry):
    output = hf_tts.managed_path(manager.directory, job['id'], 'speech.wav')
    output.write_bytes(base64.b64decode(wav(1.2)))
    manager._update(job, status='completed', stage='Speech ready', outputs=[str(output)],
                    result={'sampleRate': 24000, 'durationS': 1.2})


def wait_until(check, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


class JobLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = mock.patch.object(hf_tts.TtsManager, '_execute', fake_execute)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = hf_tts.TtsManager(self.root, REPO / 'src-tauri/resources')
        self.addCleanup(self.manager.close)
        self.values = {key: installed(value, self.root) for key, value in entries().items()}
        voices = self.root / 'models/hub' / self.values['kitten-tts-2-v1']['id'] / 'voices'
        voices.mkdir(parents=True)
        (voices / 'voices.json').write_text(json.dumps({'Bella': {'description': 'Bright female voice'}, 'Jasper': {}}), encoding='utf-8')

    def submit(self, runtime, text='Hello.', internal=False, **settings):
        entry = self.values[runtime]
        return self.manager.submit(entry, {'modelId': entry['id'], 'text': text, 'settings': settings}, internal=internal)

    def finished(self, identity):
        self.assertTrue(wait_until(lambda: (self.manager.job(identity) or {}).get('status') == 'completed'))
        return self.manager.job(identity)

    def test_submit_runs_lists_previews_and_deletes(self):
        job = self.submit('kitten-tts-2-v1', voice='Bella', text='[excited] Hello <laugh> there.')
        self.assertEqual(job['status'], 'queued')
        done = self.finished(job['id'])
        self.assertEqual(done['request']['settings']['voice'], 'Bella')
        self.assertEqual([row['id'] for row in self.manager.list()], [job['id']])
        preview = self.manager.preview(job['id'])
        self.assertEqual(preview['mime'], 'audio/wav')
        self.assertTrue(preview['dataUrl'].startswith('data:audio/wav;base64,UklGR'))
        self.assertEqual(self.manager.delete(job['id']), {'deleted': True})
        self.assertEqual(self.manager.list(), [])
        self.assertFalse((self.manager.directory / job['id']).exists())

    def test_reference_is_stored_with_the_job_and_internal_checks_stay_hidden(self):
        job = self.submit('voxcpm2-v1', referenceAudio=wav(), instruction='Calm narrator')
        done = self.finished(job['id'])
        reference = Path(done['request']['settings']['referencePath'])
        self.assertEqual(reference.parent, self.manager.directory / job['id'])
        self.assertTrue(reference.read_bytes().startswith(b'RIFF'))
        hidden = self.submit('voxcpm2-v1', internal=True)
        self.finished(hidden['id'])
        self.assertNotIn(hidden['id'], [row['id'] for row in self.manager.list()])

    def test_kitten_voice_must_exist_in_the_installed_voice_list(self):
        names = [voice['id'] for voice in self.manager.voices(self.values['kitten-tts-2-v1'])]
        self.assertEqual(names, ['Bella', 'Jasper'])
        with self.assertRaises(ValueError):
            self.submit('kitten-tts-2-v1', voice='Nobody')

    def test_requests_need_an_installed_matching_model(self):
        entry = copy.deepcopy(self.values['voxcpm2-v1'])
        with self.assertRaises(ValueError):
            self.manager.submit(entry, {'modelId': self.values['breeze-tts-2-v1']['id'], 'text': 'Hi'})
        entry['status'] = 'not-installed'
        with self.assertRaises(ValueError):
            self.manager.submit(entry, {'modelId': entry['id'], 'text': 'Hi'})
        entry = copy.deepcopy(self.values['voxcpm2-v1'])
        entry['revision'] = '1' * 40
        with self.assertRaises(ValueError):
            self.manager.submit(entry, {'modelId': entry['id'], 'text': 'Hi'})

    def test_a_queued_job_can_be_cancelled_and_unfinished_jobs_cannot_be_previewed(self):
        gate = threading.Event()

        def blocked(manager, job, entry):
            gate.wait(5)
            fake_execute(manager, job, entry)

        with mock.patch.object(hf_tts.TtsManager, '_execute', blocked):
            first = self.submit('voxcpm2-v1')
            second = self.submit('voxcpm2-v1')
            self.assertEqual(self.manager.cancel(second['id']), {'cancelled': True})
            self.assertEqual(self.manager.job(second['id'])['status'], 'cancelled')
            with self.assertRaises(ValueError):
                self.manager.preview(second['id'])
            gate.set()
            self.finished(first['id'])
        with self.assertRaises(ValueError):
            self.manager.cancel(first['id'])

    def test_status_reports_capabilities_voices_and_components(self):
        status = self.manager.status(list(self.values.values()))
        self.assertEqual(status['environment'], hf_tts.ENVIRONMENT)
        kitten = status['models'][self.values['kitten-tts-2-v1']['id']]
        self.assertEqual(kitten['capabilities']['reference'], 'transcript-required')
        self.assertIn({'group': 'Vocal event', 'insert': '<laugh> ', 'label': 'laugh'}, kitten['capabilities']['tags'])
        self.assertEqual([voice['id'] for voice in kitten['voices']], ['Bella', 'Jasper'])
        self.assertEqual(kitten['components'][0]['repository'], 'ResembleAI/chatterbox-turbo')
        self.assertFalse(kitten['components'][0]['ready'])
        vox = status['models'][self.values['voxcpm2-v1']['id']]
        self.assertEqual(vox['capabilities']['steps']['max'], 30)
        self.assertEqual(vox['components'], [])

    def test_ready_needs_matching_proof(self):
        entry = self.values['voxcpm2-v1']
        proof = {'inferenceVerified': True, 'receiptRevision': entry['revision'], 'ttsRuntime': 'voxcpm2-v1',
                 'workerVersion': hf_tts.WORKER_VERSION, 'workerSha256': self.manager.worker_identity(),
                 'lockSha256': self.manager.lock_identity()}
        with mock.patch.object(hf_tts.TtsManager, 'dependencies_ready', return_value=True):
            self.assertTrue(self.manager.ready(entry, proof))
            for key, value in (('inferenceVerified', False), ('receiptRevision', '0' * 40), ('workerSha256', 'x'),
                               ('lockSha256', 'x'), ('workerVersion', 0), ('ttsRuntime', 'kitten-tts-2-v1')):
                self.assertFalse(self.manager.ready(entry, {**proof, key: value}), key)
        self.assertFalse(self.manager.ready(entry, proof))


try:
    import hf_bridge as bridge
except ModuleNotFoundError:
    bridge = None


@unittest.skipIf(bridge is None, 'The managed bridge is not importable')
class BridgeRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = mock.patch.object(hf_tts.TtsManager, '_execute', fake_execute)
        patcher.start()
        self.addCleanup(patcher.stop)
        client = bridge.HubClient(lambda: None, endpoint='http://127.0.0.1:9', allow_test_http=True)
        self.state = bridge.BridgeState(self.root, REPO / 'src-tauri/resources', client=client, runtime=None)
        self.server = bridge.BridgeServer(('127.0.0.1', 0), self.state, 'test-secret')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.state.close)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.entry = entries()['voxcpm2-v1']
        with self.state.lock:
            self.state.entries[self.entry['id']] = copy.deepcopy(self.entry)
        folder = self.root / 'models/hub' / self.entry['id']
        folder.mkdir(parents=True)
        receipt = {'localDirectory': str(folder), 'receiptPath': str(folder / 'install-receipt.json')}
        self.state._receipt = lambda entry: dict(receipt) if entry['id'] == self.entry['id'] else None
        self.ready = True
        self.state.tts.ready = lambda entry, proof: self.ready

    def request(self, method, path, value=None, secret='test-secret'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if secret:
            headers['X-OpenCore-Hub-Secret'] = secret
        connection.request(method, path, body=json.dumps(value) if value is not None else None, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, json.loads(payload) if payload else None

    def body(self, text='Hello from the bridge.', **settings):
        return {'request': {'modelId': self.entry['id'], 'text': text, 'settings': settings}}

    def test_routes_need_the_secret(self):
        self.assertEqual(self.request('GET', '/api/tts/status', secret=None)[0], 401)
        self.assertEqual(self.request('POST', '/api/tts/jobs', self.body(), secret='wrong')[0], 401)

    def test_catalog_cards_carry_the_hardware_estimate(self):
        self.state.tts.gpu = None
        card = next(value for value in self.state.catalog() if value['id'] == self.entry['id'])
        self.assertEqual(card['hardwareFit']['device'], 'cpu')
        self.assertIn('no NVIDIA GPU was found', card['hardwareFit']['reason'])

    def test_status_lists_installed_tts_models(self):
        status, value = self.request('GET', '/api/tts/status')
        self.assertEqual(status, 200)
        self.assertIn(self.entry['id'], value['status']['models'])

    def test_generate_needs_a_prepared_model_and_a_valid_request(self):
        self.ready = False
        self.assertEqual(self.request('POST', '/api/tts/jobs', self.body())[0], 409)
        self.ready = True
        status, value = self.request('POST', '/api/tts/jobs', self.body(temperature=0.7))
        self.assertEqual(status, 400)
        self.assertIn('temperature', json.dumps(value))
        status, value = self.request('POST', '/api/tts/jobs', self.body(cfg=2.0, steps=10, instruction='Calm narrator'))
        self.assertEqual(status, 202)
        identity = value['job']['id']
        self.assertTrue(wait_until(lambda: (self.state.tts.job(identity) or {}).get('status') == 'completed'))
        status, value = self.request('GET', '/api/tts/jobs')
        self.assertEqual((status, value['jobs'][0]['id']), (200, identity))
        status, value = self.request('POST', '/api/tts/preview', {'id': identity})
        self.assertEqual(status, 200)
        self.assertTrue(value['dataUrl'].startswith('data:audio/wav;base64,'))
        self.assertEqual(self.request('POST', '/api/tts/delete', {'id': identity}), (200, {'deleted': True}))
        self.assertEqual(self.request('POST', '/api/tts/cancel', {'id': identity})[0], 400)


CU128_ARCHES = ('sm_70', 'sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120', 'compute_120')


def fake_torch(available=True, name='NVIDIA GeForce RTX 3060', capability=(8, 6), free_gib=11.0, bf16=True,
               arches=CU128_ARCHES):
    cuda = types.SimpleNamespace(
        is_available=lambda: available, get_device_name=lambda index=0: name,
        get_device_capability=lambda index=0: capability,
        mem_get_info=lambda index=0: (int(free_gib * 1024 ** 3), 24 * 1024 ** 3),
        get_arch_list=lambda: list(arches), is_bf16_supported=lambda including_emulation=True: bf16)
    return types.SimpleNamespace(cuda=cuda)


class DeviceSelectionTests(unittest.TestCase):
    """The worker must pick a device that works on any computer, not only the one it was built on."""
    VOX = {'label': 'VoxCPM2', 'gpuGiB': 7, 'bf16': True, 'cpu': True, 'summary': 'a recent NVIDIA GPU'}
    BREEZE = {'label': 'Breeze-TTS-2', 'gpuGiB': 10, 'bf16': True, 'cpu': False, 'summary': 'an RTX 30-series GPU'}
    KITTEN = {'label': 'Kitten TTS 2', 'gpuGiB': 3, 'bf16': False, 'cpu': True, 'summary': 'any computer'}

    def choose(self, hardware, **gpu):
        import tts_worker
        with mock.patch.dict(sys.modules, {'torch': fake_torch(**gpu)}):
            return tts_worker.choose_device(hardware)

    def test_every_family_declares_its_hardware_to_the_studio(self):
        for runtime, entry in entries().items():
            hardware = hf_tts.FAMILIES[runtime]['hardware']
            self.assertGreater(hardware['gpuGiB'], 0)
            self.assertIsInstance(hardware['bf16'], bool)
            self.assertIsInstance(hardware['cpu'], bool)
            self.assertTrue(hardware['summary'])
            self.assertEqual(hf_tts.capabilities(entry)['hardware'], hardware)

    def test_a_supported_gpu_with_room_is_used(self):
        self.assertEqual(self.choose(self.VOX), ('cuda', None))
        self.assertEqual(self.choose(self.BREEZE, name='NVIDIA GeForce RTX 5090', capability=(12, 0), free_gib=30), ('cuda', None))

    def test_no_gpu_uses_cpu_or_explains_the_requirement(self):
        device, note = self.choose(self.KITTEN, available=False)
        self.assertEqual(device, 'cpu')
        self.assertIn('No NVIDIA GPU', note)
        with self.assertRaisesRegex(RuntimeError, 'Breeze-TTS-2 needs an RTX 30-series GPU'):
            self.choose(self.BREEZE, available=False)

    def test_gpus_missing_from_the_pytorch_build_are_not_used(self):
        device, note = self.choose(self.KITTEN, name='NVIDIA GeForce GTX 1080', capability=(6, 1), bf16=False)
        self.assertEqual(device, 'cpu')
        self.assertIn('GTX 1080 is not supported by the bundled PyTorch build', note)

    def test_bf16_models_skip_gpus_without_native_bf16(self):
        turing = {'name': 'NVIDIA GeForce RTX 2080 Ti', 'capability': (7, 5), 'bf16': False}
        self.assertEqual(self.choose(self.KITTEN, **turing), ('cuda', None))
        device, note = self.choose(self.VOX, **turing)
        self.assertEqual(device, 'cpu')
        self.assertIn('no native BF16 support', note)
        with self.assertRaisesRegex(RuntimeError, 'no native BF16 support'):
            self.choose(self.BREEZE, **turing)

    def test_low_free_memory_says_how_to_free_it(self):
        device, note = self.choose(self.VOX, name='NVIDIA GeForce RTX 4060', capability=(8, 9), free_gib=2.5)
        self.assertEqual(device, 'cpu')
        self.assertIn('2.5 GB of free memory', note)
        self.assertIn('unload other models', note)

    def test_newer_gpus_use_compatible_kernels(self):
        self.assertEqual(self.choose(self.VOX, capability=(12, 1), free_gib=100), ('cuda', None))
        self.assertEqual(self.choose(self.VOX, capability=(13, 0), free_gib=100), ('cuda', None))
        self.assertEqual(self.choose(self.VOX, capability=(13, 0), arches=('sm_80', 'sm_90'))[0], 'cpu')

    def test_missing_hardware_spec_still_runs(self):
        self.assertEqual(self.choose(None, available=False)[0], 'cpu')
        self.assertEqual(self.choose({}), ('cuda', None))

    def test_cards_warn_before_download_when_the_computer_cannot_run_a_model(self):
        fit = hf_tts.hardware_fit
        rtx4090 = {'name': 'NVIDIA GeForce RTX 4090', 'totalGiB': 23.99, 'capability': [8, 9]}
        rtx3080 = {'name': 'NVIDIA GeForce RTX 3080', 'totalGiB': 10.0, 'capability': [8, 6]}
        gtx1660 = {'name': 'NVIDIA GeForce GTX 1660', 'totalGiB': 6.0, 'capability': [7, 5]}
        gtx1080 = {'name': 'NVIDIA GeForce GTX 1080', 'totalGiB': 8.0, 'capability': [6, 1]}
        for runtime in hf_tts.FAMILIES:
            self.assertEqual(fit(runtime, rtx4090), {'device': 'cuda', 'gpu': rtx4090['name'], 'reason': None})
        self.assertEqual(fit('kitten-tts-2-v1', None)['device'], 'cpu')
        self.assertEqual(fit('kitten-tts-2-v1', gtx1660)['device'], 'cuda')
        self.assertIn('too old for the bundled PyTorch build', fit('kitten-tts-2-v1', gtx1080)['reason'])
        self.assertEqual(fit('voxcpm2-v1', gtx1660)['device'], 'cpu')
        self.assertIn('no native BF16 support', fit('voxcpm2-v1', gtx1660)['reason'])
        self.assertIsNone(fit('breeze-tts-2-v1', rtx3080)['device'])
        self.assertIn('needs a card with about 12 GB', fit('breeze-tts-2-v1', rtx3080)['reason'])
        self.assertIsNone(fit('breeze-tts-2-v1', None)['device'])
        self.assertIn('cannot run on the CPU', fit('breeze-tts-2-v1', None)['reason'])

    def test_the_gpu_probe_never_fails_the_catalog(self):
        with mock.patch('ctypes.CDLL', side_effect=OSError('no driver')):
            self.assertIsNone(hf_tts.nvidia_gpu())

    def test_status_reports_why_the_cpu_was_chosen(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        manager = hf_tts.TtsManager(Path(temp.name), REPO / 'src-tauri/resources')
        self.addCleanup(manager.close)
        manager.child_info = {'device': 'cpu', 'deviceNote': 'No NVIDIA GPU was found. Running on the CPU instead.'}
        status = manager.status()
        self.assertEqual((status['device'], status['deviceNote']), ('cpu', manager.child_info['deviceNote']))


if __name__ == '__main__':
    unittest.main()
