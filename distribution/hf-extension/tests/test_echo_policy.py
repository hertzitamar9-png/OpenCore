import unittest
import json
from pathlib import Path
from hf_catalog import echo_context_tokens


class EchoContextPolicyTests(unittest.TestCase):
    def test_all_curated_text_packages_have_echo_and_respect_published_limits(self):
        root=Path(__file__).resolve().parents[3]
        entries=json.loads((root/'src-tauri/resources/runtime-setup/hf-extension/publisher-models.json').read_text(encoding='utf-8'))['entries']
        text=[entry for entry in entries if entry.get('category')=='text']
        self.assertGreater(len(text),900)
        for entry in text:
            self.assertEqual(entry['memoryMode'],'echo',entry['id'])
            self.assertGreater(entry['contextTokens'],0)
            self.assertLessEqual(entry['contextTokens'],32768)
        for family in ['publisher-qwen35-4b-3d-coder','publisher-lfm3-1-7b','publisher-apex-35b-v1']:
            family_entries=[entry for entry in text if entry['familyId']==family]
            self.assertTrue(family_entries,family)
            self.assertTrue(all(entry['contextTokens']==32768 for entry in family_entries),family)

    def test_text_and_brain_models_target_32k_with_real_publisher_limits(self):
        self.assertEqual(echo_context_tokens({}),32768)
        self.assertEqual(echo_context_tokens({'config':{'max_position_embeddings':262144}}),32768)
        self.assertEqual(echo_context_tokens({'config':{'text_config':{'max_position_embeddings':131072}}}),32768)
        self.assertEqual(echo_context_tokens({'gguf':{'context_length':40960}}),32768)
        self.assertEqual(echo_context_tokens({'config':{'max_position_embeddings':8192}}),8192)
        self.assertEqual(echo_context_tokens({'config':{'n_positions':1024}}),1024)
        self.assertEqual(echo_context_tokens({'config':[],'gguf':[]},fallback=16384),16384)


if __name__=='__main__':unittest.main()
