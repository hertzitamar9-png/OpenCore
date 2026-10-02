import unittest
import tempfile
from pathlib import Path
import echo_shared_data
from echo_shared_data import canonical_solution, literal_cases, complete_tokens, split_records


class CompleteCodingData(unittest.TestCase):
    def test_saved_multilingual_data_can_be_loaded_on_windows(self):
        load=getattr(echo_shared_data,'read_json',None)
        self.assertTrue(callable(load),'saved data must use explicit UTF-8')
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'data.json'
            path.write_text('{"text":"文字 שלום 😀"}',encoding='utf-8')
            self.assertEqual(load(path),{'text':'文字 שלום 😀'})

    def test_normalization_preserves_entire_function_and_removes_executable_annotations(self):
        name,source=canonical_solution('def f(x: danger()):\n    "documentation"\n    # comment\n    return x + 2')
        self.assertEqual(name,'f')
        self.assertEqual(source,'def f(x):\n    return x + 2')
        with self.assertRaises(ValueError): canonical_solution('import os\ndef f(x): return x')

    def test_only_literal_direct_contracts_enter_isolated_verifier(self):
        cases=literal_cases('assert f([1, 2]) == [3]\nassert f([]) == []\nassert f([0]) == compute_secret()','f')
        self.assertEqual(cases,[{'args':[[1,2]],'expected':[3]},{'args':[[]],'expected':[]}])
        self.assertEqual(literal_cases('assert g(1) == 2\nassert f(secret) == 3','f'),[])

    def test_complete_targets_include_end_token_and_never_truncate_requirements(self):
        class Tokenizer:
            eos_token_id=9
            def apply_chat_template(self,*a,**k): return [1,2]
            def encode(self,*a,**k): return [3,4,5]
        prompt,response=complete_tokens(Tokenizer(),'task','solution',2,4)
        self.assertEqual(prompt,[1,2]);self.assertEqual(response,[3,4,5,9])
        with self.assertRaises(ValueError): complete_tokens(Tokenizer(),'task','solution',1,4)
        with self.assertRaises(ValueError): complete_tokens(Tokenizer(),'task','solution',2,3)

    def test_duplicates_and_previous_tasks_cannot_leak_between_splits(self):
        rows=[{'id':str(i),'prompt':f' Task {i}  ','solution':f'def f(x): return x + {i}'} for i in range(8)]
        rows += [dict(rows[0],id='duplicate',prompt='task 0')]
        split=split_records(rows,exclude_prompts=['task 1'],train=3,validation=2,heldout=2)
        selected=[x for group in split.values() for x in group]
        self.assertEqual(len(selected),7)
        self.assertEqual(len({x['prompt'].strip().lower() for x in selected}),7)
        self.assertNotIn('1',{x['id'] for x in selected})
        self.assertEqual(split,split_records(list(reversed(rows)),exclude_prompts=['task 1'],train=3,validation=2,heldout=2))
        with self.assertRaises(ValueError): split_records(rows,[],train=8,validation=1,heldout=1)


if __name__=='__main__': unittest.main()
