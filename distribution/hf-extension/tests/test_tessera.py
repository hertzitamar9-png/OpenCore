"""Tessera's curated package, sprite controls and CPU pipeline routing."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest

import hf_tessera
from hf_bridge import BridgeState
from hf_download import validate_entry
from hf_image_runtime import supports, validate_request
from hf_pipeline_tools import _kind


class TesseraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[3]
        catalog = root / 'src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'
        cls.entry, = [x for x in json.loads(catalog.read_bytes())['entries'] if x['repository'] == hf_tessera.REPOSITORY]
        cls.metadata = json.loads((root / 'distribution/hf-research/requested-models/thejagstudio--tessera-small.api.json').read_bytes())

    def test_catalog_and_reimport(self):
        self.assertIs(validate_entry(self.entry), self.entry)
        self.assertTrue(supports(self.entry))
        self.assertFalse(supports({**self.entry, 'revision': '0' * 40}))
        self.assertFalse(hf_tessera.supports({**self.entry, 'files': []}))
        self.assertFalse(self.entry['supportsTextGeneration'])
        validate_entry(hf_tessera.ENCODER)
        self.assertEqual(self.entry['componentBytes'], hf_tessera.ENCODER['bytes'])
        bridge = SimpleNamespace(client=SimpleNamespace(metadata=lambda _: self.metadata), lock=threading.RLock(),
                                 entries={self.entry['id']: self.entry}, plans={})
        plan = BridgeState.plan(bridge, hf_tessera.REPOSITORY)
        self.assertEqual(plan['variants'], [self.entry])
        self.assertEqual(plan['quantizationCount'], 1)

    def test_native_dimensions_are_specific_to_this_model(self):
        request = {'modelId': self.entry['id'], 'prompt': 'a blue gem'}
        settings = validate_request(request)['settings']
        self.assertEqual((settings['width'], settings['height'], settings['steps'], settings['guidanceScale']), (16, 16, 16, 3))
        for incompatible in ({'width': 256}, {'outputWidth': 128}, {'outputFormat': 'jpeg'}, {'negativePrompt': 'red'}):
            with self.assertRaises(ValueError):
                validate_request({**request, 'settings': incompatible})
        with self.assertRaises(ValueError):
            validate_request({**request, 'modelId': 'other', 'settings': {'width': 16, 'height': 16}})

    def test_cpu_pipeline_remains_available_on_older_builds(self):
        state = SimpleNamespace(pipeline_cpu_speech_only=True)
        self.assertEqual(_kind(state, self.entry), 'image')
        self.assertIsNone(_kind(state, {'imageRuntime': 'iris-sdcpp-v1'}))
        self.assertEqual([m['scope'] for m in self.entry['modeAvailability']], ['pipeline', 'tool'])
