import base64
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from hf_computer_runtime import ComputerManager, CONFIG, VERSION, profile_for, request_values
from hf_runtime import _hash
from hf_computer_worker import ternary_action


class ComputerRuntimeTests(unittest.TestCase):
    def test_ternary_readiness_rejects_truncated_thoughts_without_an_action(self):
        self.assertEqual(ternary_action('Thought: The button is centered.\nAction: click(500, 500)'), 'click(500, 500)')
        self.assertEqual(ternary_action('done()'), 'done()')
        for text in ('', 'Thought: I need to click the button', 'Action: click(500,', 'This is ready'):
            with self.assertRaises(ValueError): ternary_action(text)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manager = ComputerManager(self.root, self.root/'resources')

    def entry(self, runtime='ternarypilot-v2'):
        profile = CONFIG['profiles'][runtime]
        return {key: profile[key] for key in ('id', 'repository', 'revision')} | {'status': 'installed', 'specialistReady': True}

    def test_only_exact_reviewed_checkpoints_get_a_runtime(self):
        for runtime in CONFIG['profiles']:
            entry = self.entry(runtime)
            self.assertEqual(profile_for(entry)[0], runtime)
            entry['revision'] = '0'*40
            self.assertEqual(profile_for(entry), (None, None))
        self.assertEqual(profile_for({'repository': 'unknown/model'}), (None, None))

    def test_gui_candidates_are_real_bounded_normalized_inputs(self):
        value = {'task': 'Tap Save', 'image': base64.b64encode(b'fixture-image').decode(),
                 'candidates': [{'text': 'Save', 'bounds': [.1, .2, .4, .6], 'clickable': True}]}
        self.assertEqual(len(request_values(value, 'gui-jepa-v1')['candidates']), 1)
        for bounds in ([0, 0, 2, 1], [0, 0, 0, 1], [float('nan'), 0, 1, 1], ['0', 0, 1, 1]):
            invalid = copy.deepcopy(value); invalid['candidates'][0]['bounds'] = bounds
            with self.assertRaises(ValueError): request_values(invalid, 'gui-jepa-v1')
        for candidates in ([], value['candidates']*81, 'not a list'):
            with self.assertRaises(ValueError): request_values({**value, 'candidates': candidates}, 'gui-jepa-v1')

    def test_tasks_and_images_have_limits_and_hobby_does_not_require_an_image(self):
        self.assertEqual(request_values({'task': 'Open Calculator'}, 'hobbylm-v1')['task'], 'Open Calculator')
        for value in ({'task': ''}, {'task': 'x'*3001}, {'task': 'Click', 'image': 'not base64'}):
            with self.assertRaises(ValueError): request_values(value, 'ternarypilot-v2')

    def test_dependency_source_is_the_exact_pinned_base_in_managed_storage(self):
        for profile in CONFIG['profiles'].values():
            dependency = self.manager.dependency(profile)
            self.assertTrue(Path(dependency['localDirectory']).resolve().is_relative_to((self.root/'models/hub').resolve()))
            self.assertEqual(dependency['id'], profile['dependencyId'])
            self.assertTrue(all(pin['pinned'] for pin in dependency['files']))
            self.assertFalse(any(pin['filename'].endswith('.py') for pin in dependency['files']))

    def test_readiness_requires_real_proof_and_unchanged_dependency_files(self):
        entry = self.entry(); profile = CONFIG['profiles']['ternarypilot-v2']; dependency = self.manager.dependency(profile)
        folder = Path(dependency['localDirectory']); folder.mkdir(parents=True)
        weight = folder/'fixture.gguf'; weight.write_bytes(b'real fixture')
        receipt = Path(dependency['receiptPath']); receipt.write_text(json.dumps({'files': [{'filename': weight.name, 'localPath': str(weight), 'bytes': weight.stat().st_size}]}))
        proof = {'revision': entry['revision'], 'workerVersion': VERSION, 'inferenceVerified': True,
                 'dependencyReceiptSha256': _hash(receipt), 'dependencyFiles': [{'filename': weight.name, 'bytes': weight.stat().st_size, 'modifiedNs': weight.stat().st_mtime_ns}]}
        self.assertFalse(self.manager.ready(entry, {}))
        self.assertTrue(self.manager.ready(entry, proof))
        weight.write_bytes(b'edit fixture')
        self.assertFalse(self.manager.ready(entry, proof))

    def test_prediction_is_async_owned_and_rejects_another_active_request(self):
        release = threading.Event()
        def predict(*args, **kwargs): release.wait(2); return {'text': 'Action: click(500, 500)', 'inferenceVerified': True}
        entry = self.entry(); body = {'task': 'Click button', 'image': base64.b64encode(b'image').decode()}
        with patch.object(self.manager, '_worker', side_effect=predict):
            job = self.manager.start(entry, body)
            self.assertEqual(job['status'], 'running')
            with self.assertRaises(RuntimeError): self.manager.start(entry, body)
            release.set()
            for _ in range(100):
                if self.manager.status()['status'] != 'running': break
                time.sleep(.01)
            self.assertEqual(self.manager.status()['status'], 'complete')
            self.assertGreaterEqual(self.manager.status()['elapsedSeconds'], 0)


if __name__ == '__main__': unittest.main()
