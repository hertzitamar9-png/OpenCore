import unittest

from hf_discovery import discover, same_checkpoint, source_identity


def metadata(name, quants=('Q4_K_M',), base=None, relation=None):
    card = {}
    if base is not None:
        card['base_model'] = base
    if relation is not None:
        card['base_model_relation'] = relation
    elif base is not None:
        card['base_model_relation'] = 'quantized'
    return {'id': name, 'sha': 'a' * 40, 'config': {'model_type': 'llama'}, 'cardData': card,
            'siblings': [{'rfilename': 'model-' + q + '.gguf', 'size': 7,
                          'lfs': {'sha256': 'b' * 64, 'size': 7}} for q in quants]}


class DiscoveryTests(unittest.TestCase):
    def test_quantized_publisher_repo_can_resolve_its_exact_original(self):
        self.assertEqual(source_identity(metadata('owner/Model-GGUF', base='owner/Model')),
                         ('owner/model', {'owner/model', 'owner/model-gguf'}))

    def test_finetune_does_not_resolve_to_training_base(self):
        self.assertEqual(source_identity(metadata('owner/Custom-Model', base='Qwen/Qwen3.8-27B'))[0],
                         'owner/custom-model')
        self.assertEqual(source_identity(metadata('other/Model-GGUF', base='owner/Model', relation='finetune'))[0],
                         'other/model-gguf')

    def test_wrong_checkpoint_finetunes_merges_and_missing_lineage_are_rejected(self):
        for value in [metadata('community/copy'), metadata('community/copy', base='owner/Other'),
                      metadata('community/copy', base='owner/Model', relation=''),
                      metadata('community/copy', base='owner/Model', relation='finetune'),
                      metadata('community/copy', base=['owner/Model', 'owner/Other'], relation='merge')]:
            self.assertFalse(same_checkpoint(value, {'owner/model'}))

    def test_community_options_group_by_checkpoint_and_have_immutable_file_pins(self):
        class Client:
            def metadata(self, name):
                return {'owner/Model': metadata('owner/Model', ('Q4_K_M',)),
                    'community/Model-GGUF': metadata('community/Model-GGUF', ('Q4_K_M', 'Q5_K_M', 'Q6_K', 'Q8_0', 'F16'), 'owner/Model'),
                    'community/Other': metadata('community/Other', base='owner/Other')}[name]
            def search_quantizations(self, query):
                return [{'id': 'community/Model-GGUF'}, {'id': 'community/Other'}]
        plan = discover(Client(), 'owner/Model')
        self.assertEqual(plan['quantizationCount'], 5)
        self.assertEqual(len(plan['variants']), 6)
        self.assertEqual(plan['relatedRepositories'], ['community/Model-GGUF'])
        self.assertEqual(len(plan['rejectedRepositories']), 1)
        self.assertEqual(len({v['familyId'] for v in plan['variants']}), 1)
        self.assertTrue(all(pin['pinned'] for v in plan['variants'] for pin in v['files']))
        self.assertEqual(plan, discover(Client(), 'owner/Model'))


if __name__ == '__main__':
    unittest.main()
