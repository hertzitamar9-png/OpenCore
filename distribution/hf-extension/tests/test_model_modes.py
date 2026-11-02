"""Operating modes describe one checkpoint package, never another set of weights."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from model_modes import apply_operating_modes, published_context_tokens, supports_text_generation, native_model_bindings, native_mode_aliases


class ModelModePolicyTests(unittest.TestCase):
    def test_every_generic_quant_gets_two_profiles_with_the_exact_same_artifacts(self):
        models=[{'id':'coder-'+quant,'label':'Coder '+quant,'category':'text','backend':'gguf','memoryMode':'echo',
                 'contextTokens':32768,'precision':quant,'artifacts':['weights-'+quant],'runtimeModelPath':'models/'+quant+'.gguf',
                 **({'variantOf':'coder-Q4'} if quant!='Q4' else {})} for quant in ('Q4','Q6','Q8')]
        catalog={'models':models,'artifacts':[{'id':'weights-'+q,'path':'models/'+q+'.gguf'} for q in ('Q4','Q6','Q8')]}
        original=copy.deepcopy(catalog['artifacts'])
        apply_operating_modes(models,lambda _:{'config':{'max_position_embeddings':1048576}})
        self.assertEqual(native_mode_aliases(catalog),3)
        lookup={m['id']:m for m in models}
        for model in models:
            profiles=model['modeProfiles']
            self.assertEqual(set(profiles),{'native','echo'})
            native,echo=(lookup[profiles[key]] for key in ('native','echo'))
            self.assertEqual(native['artifacts'],echo['artifacts'])
            self.assertEqual(native['runtimeModelPath'],echo['runtimeModelPath'])
            self.assertEqual((native['contextTokens'],echo['contextTokens']),(1048576,32768))
            self.assertEqual(native['precision'],echo['precision'])
        self.assertEqual(catalog['artifacts'],original)

    def test_legacy_native_and_echo_roots_collapse_without_new_profiles_or_files(self):
        models=[{'id':mode,'label':'Coder','category':'text','memoryMode':mode,'backend':'gguf','precision':'Q4',
                 'artifacts':['same'],'contextTokens':32768} for mode in ('native','echo')]
        apply_operating_modes(models,lambda _: {})
        self.assertEqual(native_mode_aliases({'models':models}),0)
        self.assertEqual(models[0]['variantOf'],'echo')
        self.assertEqual(models[0]['modeProfiles'],models[1]['modeProfiles'])
        self.assertEqual(len(models),2)

    def test_modes_for_the_same_quant_share_the_known_supported_cap(self):
        models=[{'id':mode,'label':'Coder','category':'text','memoryMode':mode,'backend':'gguf','precision':'Q4',
                 'artifacts':['same'],'contextTokens':16384 if mode=='native' else 32768} for mode in ('echo','native')]
        apply_operating_modes(models,lambda _: {})
        native_mode_aliases({'models':models})
        self.assertEqual(models[0]['operatingModes'],models[1]['operatingModes'])
        self.assertEqual([m['contextTokens'] for m in models],[32768,32768])

    def test_native_targets_binary_one_million_without_exceeding_published_limits(self):
        model = {'id': 'small', 'category': 'text', 'backend': 'gguf', 'runtimeReady': True, 'selectable': True,
                 'contextTokens': 32768, 'artifacts': ['weights'], 'runtimeModelPath': 'models/small.gguf'}
        original = copy.deepcopy(model)
        apply_operating_modes([model], lambda _: {'config': {'max_position_embeddings': 262144}})
        self.assertTrue(model['supportsTextGeneration'])
        self.assertEqual(model['publishedContextTokens'], 262144)
        native, echo = model['operatingModes']
        self.assertEqual((native['id'], native['targetContextTokens'], native['contextTokens']), ('native', 1048576, 262144))
        self.assertEqual((echo['id'], echo['targetContextTokens'], echo['contextTokens']), ('echo', 32768, 32768))
        self.assertTrue(native['supported'] and echo['supported'])
        self.assertEqual({key: model[key] for key in original}, original, 'Existing model, context and file identities are unchanged')

    def test_short_context_checkpoints_and_unknown_limits_do_not_claim_one_million_tokens(self):
        for published in [1024, 2048, 16384, 2097152]:
            row = {'id': 'publisher', 'repository': 'owner/model', 'category': 'text', 'contextTokens': 8192, 'runtime': {'adapter': 'transformers'}}
            apply_operating_modes([row], lambda _: {'config': {'text_config': {'max_position_embeddings': published}}})
            self.assertEqual([mode['contextTokens'] for mode in row['operatingModes']], [min(published, 1048576), min(published, 32768)])
        unknown = {'id': 'unknown', 'category': 'text', 'contextTokens': 8192, 'runtime': {'adapter': None}}
        apply_operating_modes([unknown], lambda _: {})
        self.assertIsNone(unknown['publishedContextTokens'])
        self.assertEqual(unknown['operatingModes'][0]['contextTokens'], 8192)
        self.assertFalse(any(mode['supported'] for mode in unknown['operatingModes']))
        unknown['contextTokens'] = 0
        apply_operating_modes([unknown], lambda _: {})
        self.assertTrue(all(mode['contextTokens'] is None for mode in unknown['operatingModes']))

    def test_visual_language_and_brain_generation_get_modes_without_converting_specialists_to_chat(self):
        self.assertTrue(supports_text_generation({'category': 'brain'}))
        self.assertTrue(supports_text_generation({'category': 'omni', 'inputModalities': ['text', 'image'], 'outputModalities': ['text']}))
        self.assertTrue(supports_text_generation({'category': 'computer-use'}, {'pipeline_tag': 'image-text-to-text'}))
        self.assertTrue(supports_text_generation({'category': 'text', 'libraryCategory': 'ocr', 'runtime': {'adapter': 'gguf'}}))
        excluded = [
            {'category': 'speech', 'outputModalities': ['text']},
            {'category': 'ocr', 'inputModalities': ['image'], 'outputModalities': ['text']},
            {'category': 'omni', 'inputModalities': ['audio'], 'outputModalities': ['text']},
            {'category': 'text', 'supportsTextChat': False},
            {'category': 'computer-use', 'computerRuntime': 'gui-jepa-v1'},
            {'category': 'embedding'}, {'category': 'image', 'outputModalities': ['image']},
        ]
        for model in excluded:
            self.assertFalse(supports_text_generation(model), model)
            apply_operating_modes([model], lambda _: {})
            self.assertEqual(model['operatingModes'], [])

    def test_generative_ocr_gets_both_modes_without_changing_its_specialist_category(self):
        for row, info in [
            ({'category': 'ocr'}, {'pipeline_tag': 'image-text-to-text'}),
            ({'category': 'ocr', 'inputModalities': ['image', 'text'], 'outputModalities': ['text']}, {}),
            ({'category': 'computer-use'}, {'pipeline_tag': 'text-generation'}),
        ]:
            apply_operating_modes([row], lambda _: info)
            self.assertTrue(row['supportsTextGeneration'])
            self.assertEqual([mode['id'] for mode in row['operatingModes']], ['native', 'echo'])
            self.assertFalse(any(mode['supported'] for mode in row['operatingModes']), 'A generative task does not establish an execution adapter')
        self.assertEqual(row['category'], 'computer-use')

    def test_runtime_and_echo_limits_cannot_exceed_the_checkpoint_or_mode_target(self):
        row = {'category': 'text', 'backend': 'gguf', 'supportedContextTokens': 32768,
               'echoWorkingWindowTokens': 65536, 'contextTokens': 8192}
        apply_operating_modes([row], lambda _: {'config': {'max_position_embeddings': 262144}})
        self.assertEqual(row['publishedContextTokens'], 262144)
        self.assertEqual([mode['contextTokens'] for mode in row['operatingModes']], [32768, 32768])
        row.pop('supportedContextTokens')
        apply_operating_modes([row], lambda _: {'config': {'max_position_embeddings': 4096}})
        self.assertEqual([mode['contextTokens'] for mode in row['operatingModes']], [4096, 4096])

    def test_native_aliases_preserve_the_reviewed_execution_cap(self):
        row = {'id': 'mage', 'label': 'Mage', 'category': 'text', 'backend': 'gguf',
               'memoryMode': 'echo', 'precision': 'Q4', 'artifacts': ['weights'],
               'contextTokens': 32768, 'supportedContextTokens': 32768}
        apply_operating_modes([row], lambda _: {'config': {'max_position_embeddings': 262144}})
        catalog = {'models': [row]}
        native_mode_aliases(catalog)
        self.assertEqual([mode['contextTokens'] for mode in row['operatingModes']], [32768, 32768])
        self.assertEqual([model['contextTokens'] for model in catalog['models']], [32768, 32768])

    def test_published_limit_uses_text_architecture_not_vision_limits_or_boolean_values(self):
        self.assertEqual(published_context_tokens({'config': {'max_position_embeddings': 512, 'text_config': {'max_position_embeddings': 131072}}}), 131072)
        self.assertEqual(published_context_tokens({'config': {'maxSeqLen': 2048}}), 2048)
        self.assertEqual(published_context_tokens({'gguf': {'context_length': 40960}}), 40960)
        self.assertIsNone(published_context_tokens({'config': {'max_position_embeddings': True}, 'tokenizer_config': {'model_max_length': 10**30}}))

    def test_native_bindings_keep_exact_artifacts_and_paths_without_duplicate_weights(self):
        artifact = {'id': 'weights', 'path': 'models/model.gguf', 'repo': 'owner/model', 'revision': 'a'*40,
                    'filename': 'model.gguf', 'sha256': 'b'*64, 'bytes': 42}
        model = {'id': 'model', 'category': 'text', 'contextTokens': 32768, 'artifacts': ['weights'],
                 'weightArtifacts': ['weights'], 'backend': 'gguf', 'runtimeReady': True, 'runtimeModelPath': artifact['path']}
        catalog = {'models': [model], 'artifacts': [artifact]}
        original = copy.deepcopy(catalog)
        apply_operating_modes(catalog['models'], lambda _: {})
        bindings = native_model_bindings(catalog)
        self.assertEqual(len(bindings['models']), 1)
        self.assertEqual(bindings['models'][0]['runtimeModelPath'], artifact['path'])
        self.assertEqual(bindings['artifacts'], original['artifacts'])
        self.assertEqual(catalog['artifacts'], original['artifacts'])
        self.assertEqual(catalog['models'][0]['artifacts'], original['models'][0]['artifacts'])

    def test_text_output_inventory_is_not_a_claim_of_chat_runtime(self):
        from model_modes import produces_text
        transcription={'category':'speech','inputModalities':['audio'],'outputModalities':['text']}
        self.assertTrue(produces_text(transcription))
        apply_operating_modes([transcription])
        self.assertTrue(transcription['outputsText'])
        self.assertFalse(transcription['supportsTextGeneration'])
        self.assertEqual(transcription['operatingModes'], [])
        vl={'category':'omni','inputModalities':['text','image'],'outputModalities':['text'],
            'runtime':{'adapter':'gguf'},'contextTokens':32768}
        apply_operating_modes([vl])
        self.assertTrue(vl['outputsText'])
        self.assertTrue(vl['supportsTextGeneration'])
        self.assertTrue(all(mode['supported'] for mode in vl['operatingModes']))
        pending={'category':'omni','inputModalities':['text','video'],'outputModalities':['text'],
                 'runtime':{'adapter':None},'contextTokens':32768}
        apply_operating_modes([pending])
        self.assertTrue(pending['outputsText'])
        self.assertFalse(any(mode['supported'] for mode in pending['operatingModes']))


if __name__ == '__main__':
    unittest.main()
