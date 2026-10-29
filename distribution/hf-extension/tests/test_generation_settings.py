import json,tempfile,unittest
from pathlib import Path
from hf_bridge import BridgeState
from hf_catalog import generation_defaults

class GenerationSettingsTests(unittest.TestCase):
    def test_automatic_uses_published_parameters_and_manual_remains_conversation_scoped(self):
        resources=Path(__file__).resolve().parents[3]/'src-tauri/resources'
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);state=BridgeState(root,resources)
            try:
                identity='hf-'+'f'*24
                state.entries[identity]={'id':identity,'profileId':'api:local:test','generationDefaults':{'temperature':.3,'top_k':30}}
                state.generation_settings({'conversationId':'chat-a','modelId':'api:local:test','parameters':None})
                path=root/'echo/generation-settings.json'
                self.assertEqual(json.loads(path.read_text())['conversations']['chat-a'],{'temperature':.3,'top_k':30})
                state.generation_settings({'conversationId':'chat-a','modelId':'api:local:test','parameters':{'temperature':.1}})
                self.assertEqual(json.loads(path.read_text())['conversations']['chat-a'],{'temperature':.1})
                state.generation_settings({'conversationId':'chat-a','modelId':'unknown','parameters':None})
                self.assertNotIn('chat-a',json.loads(path.read_text())['conversations'])
            finally:state.close()

    def test_untrusted_publisher_configuration_supplies_only_bounded_sampler_fields(self):
        self.assertEqual(generation_defaults({'generation_config':{'temperature':.6,'top_k':20,'repetition_penalty':1.1,'messages':['untrusted'],'max_new_tokens':2048}}),{'temperature':.6,'top_k':20,'repeat_penalty':1.1,'max_tokens':2048})
        self.assertEqual(generation_defaults({'generation_config':{'temperature':'execute me','top_k':1.5},'cardData':[]}),{})

if __name__=='__main__':unittest.main()
