"""Every model exposes both mode choices without inventing text interfaces or runtimes."""
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src-tauri/resources/runtime-setup/hf-extension'))
from model_modes import apply_operating_modes
from hf_bridge import BridgeState


class ModeAvailabilityTests(unittest.TestCase):
    def test_every_specialist_keeps_its_own_capability_and_explains_both_modes(self):
        for category in ('3d', 'image', 'video', 'speech', 'tts', 'music', 'embedding', 'policy'):
            model = {'category': category, 'runtime': {'adapter': None, 'reason': 'Install the publisher-specific runtime.'}}
            apply_operating_modes([model])
            self.assertEqual(model['operatingModes'], [])
            self.assertFalse(model['supportsTextGeneration'])
            choices = model['modeAvailability']
            self.assertEqual([choice['id'] for choice in choices], ['native', 'echo'])
            self.assertFalse(any(choice['supported'] for choice in choices))
            self.assertTrue(all(choice['reason'] for choice in choices))
            self.assertTrue(all(choice['contextTokens'] is None for choice in choices))

    def test_a_managed_pipeline_can_run_regular_without_becoming_a_text_agent(self):
        model = {'category': 'tts', 'runtime': {'adapter': 'managed-tts'}}
        apply_operating_modes([model])
        native, echo = model['modeAvailability']
        self.assertTrue(native['supported'])
        self.assertFalse(echo['supported'])
        self.assertFalse(model['supportsTextGeneration'])
        self.assertEqual(model['operatingModes'], [])

    def test_text_modes_retain_their_real_context_and_runtime_limits(self):
        model = {'category': 'text', 'runtime': {'adapter': 'gguf'}, 'contextTokens': 32768}
        apply_operating_modes([model], lambda _: {'config': {'max_position_embeddings': 262144}})
        self.assertEqual([choice['contextTokens'] for choice in model['modeAvailability']], [262144, 32768])
        self.assertTrue(all(choice['supported'] for choice in model['modeAvailability']))
        model['runtime']['adapter'] = None
        apply_operating_modes([model])
        self.assertFalse(any(choice['supported'] for choice in model['modeAvailability']))
        self.assertTrue(all(choice.get('reason') for choice in model['modeAvailability']))

    def test_unavailable_modes_are_rejected_by_the_api_before_touching_a_running_model(self):
        row = {'id': 'budget', 'category': '3d', 'runtime': {'adapter': None, 'reason': 'Budget3D runtime required.'}}
        state = SimpleNamespace(lock=threading.RLock(), entry=lambda _: row, runtime=SimpleNamespace(change_mode=Mock()))
        for mode in ('native', 'echo'):
            with self.assertRaises(ValueError): BridgeState.runtime_mode(state, 'budget', mode)
        state.runtime.change_mode.assert_not_called()

    def test_regular_specialist_selection_preserves_its_runtime_and_files(self):
        row = {'id': 'speech', 'category': 'tts', 'runtime': {'adapter': 'managed-tts'}, 'files': ['same-weights']}
        state = SimpleNamespace(lock=threading.RLock(), entry=lambda _: row, entries={'speech': row},
                                runtime=SimpleNamespace(change_mode=Mock()), persist_catalog=Mock())
        BridgeState.runtime_mode(state, 'speech', 'native')
        self.assertEqual(row['operatingMode'], 'native')
        self.assertEqual(row['files'], ['same-weights'])
        state.runtime.change_mode.assert_not_called()
        with self.assertRaises(ValueError): BridgeState.runtime_mode(state, 'speech', 'echo')

    def test_tool_enabled_specialist_switches_both_modes_without_reloading_weights(self):
        row={'id':'budget','category':'3d','generationRuntime':'budget3d-idol-v1','runtime':{'adapter':'budget3d-idol'},
             'files':['same-weights'],'runtimePreparation':{'dependenciesPrepared':True}}
        state=SimpleNamespace(lock=threading.RLock(),entry=lambda _:row,entries={'budget':row},runtime=SimpleNamespace(change_mode=Mock()),persist_catalog=Mock())
        BridgeState.runtime_mode(state,'budget','echo')
        self.assertEqual(row['operatingMode'],'echo')
        BridgeState.runtime_mode(state,'budget','native')
        self.assertEqual(row['operatingMode'],'native')
        self.assertEqual(row['runtimePreparation'],{'dependenciesPrepared':True})
        self.assertEqual(row['files'],['same-weights'])
        state.runtime.change_mode.assert_not_called()


if __name__ == '__main__': unittest.main()
