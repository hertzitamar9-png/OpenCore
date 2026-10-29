import tempfile,unittest
from pathlib import Path
from evoagent.generation_settings import save,apply,normalize
from echo_server import apply_generation_settings

class GenerationSettingsTests(unittest.TestCase):
    def test_manual_parameters_apply_to_only_the_requested_chat_and_automatic_clears(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'settings.json'
            payload={'temperature':.7,'top_p':.95,'max_completion_tokens':9999,'messages':[{'role':'user','content':'hello'}]}
            settings={'temperature':.25,'top_p':.9,'top_k':20,'repeat_penalty':1.05,'max_tokens':512,'seed':123}
            save(path,'chat-a',settings)
            result=apply(path,'chat-a',payload)
            self.assertEqual(apply_generation_settings(path,'chat-a',payload),result)
            for key,value in settings.items():self.assertEqual(result[key],value)
            self.assertEqual(result['messages'],payload['messages'])
            self.assertNotIn('max_completion_tokens',result)
            self.assertEqual(result['echo_max_total_tokens'],512)
            self.assertIs(apply(path,'chat-b',payload),payload)
            save(path,'chat-a',None)
            self.assertIs(apply(path,'chat-a',payload),payload)

    def test_bad_values_and_arbitrary_backend_options_are_rejected(self):
        for value in [{'temperature':float('nan')},{'seed':True},{'top_p':5},{'top_k':1.1},{'max_tokens':1000000},{'messages':[]}]:
            with self.subTest(value=value),self.assertRaises(ValueError):normalize(value)

if __name__=='__main__':unittest.main()
