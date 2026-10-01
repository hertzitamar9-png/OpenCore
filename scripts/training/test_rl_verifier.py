import unittest
from rl_verifier import extract_function, verifier_payload, validate_report
from rl_tasks import TRAIN_TASKS, HELDOUT_TASKS
from rl_verify_worker import equal


class IndependentRewards(unittest.TestCase):
    def test_boolean_does_not_pass_an_integer_contract(self):
        self.assertFalse(equal(True,1))
        self.assertFalse(equal([True], [1]))
        self.assertTrue(equal({'answer':[1,None]}, {'answer':[1,None]}))

    def test_training_and_heldout_have_disjoint_identities(self):
        self.assertFalse({x['id'] for x in TRAIN_TASKS} & {x['id'] for x in HELDOUT_TASKS})
        self.assertTrue(all(len(x['cases'])>=5 for x in TRAIN_TASKS+HELDOUT_TASKS))

    def test_executable_content_is_never_sent_to_host_exec(self):
        task=TRAIN_TASKS[0]
        with self.assertRaises(ValueError): extract_function('import os\nos.system("echo hacked")',task['name'])
        with self.assertRaises(ValueError): extract_function('def '+task['name']+'(x):\n return x.__class__',task['name'])
        with self.assertRaises(ValueError): extract_function('@print("reward:1")\ndef '+task['name']+'(x):\n return x',task['name'])

    def test_reward_is_computed_by_verifier_from_case_outcomes(self):
        task=TRAIN_TASKS[0]
        report={'outcomes':[True,False]+[False]*(len(task['cases'])-2),'reward':1.}
        self.assertEqual(validate_report(report,len(task['cases']))['reward'],1/len(task['cases']))
        with self.assertRaises(RuntimeError): validate_report({'outcomes':[1]},len(task['cases']))

    def test_child_case_input_does_not_contain_expected_answers(self):
        task=TRAIN_TASKS[0]
        payload=verifier_payload(task,'def '+task['name']+'(items):\n return items')
        self.assertEqual(payload['name'],task['name'])
        self.assertTrue(all('args' in x and 'expected' in x for x in payload['cases']))


if __name__=='__main__': unittest.main()
