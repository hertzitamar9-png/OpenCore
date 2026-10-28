import tempfile
import unittest
from hf_prompt_assistant import PromptAssistant

class FakeAssistant(PromptAssistant):
    def __init__(self,root):super().__init__(root,allowed_ports={8811,8813});self.calls=[];self.restored=True;self.reply='{"prompt":"A detailed blue ceramic cup on a wooden table","recap":"A blue cup on a table"}'
    def _slot(self,*args):return {'n_prompt_tokens':0,'echo_session_tokens':0,'is_processing':False}
    def _check_standalone_finished(self,*args):return True
    def _http(self,url,payload=None,authorization='',timeout=3):
        self.calls.append((url,payload))
        if url.endswith('/lease'):
            if payload['action']=='acquire':return {'acquired':True,'nonce':'owned-lease'}
            return {'released':True,'invalidation':{'invalidated':self.restored,'nextTurn':'full-transcript-reset'}}
        return {'choices':[{'finish_reason':'stop','message':{'content':self.reply}}]}

class PromptAssistantTests(unittest.TestCase):
    def test_opt_in_json_rewrite_and_cache_reset(self):
        with tempfile.TemporaryDirectory() as root:
            helper=FakeAssistant(root);request={'enabled':True,'prompt':'blue cup','runtime':{'status':'running','backendPort':8811,'echoPort':8813}}
            result=helper.improve(request);self.assertEqual(result['status'],'ready',result)
            body=next(body for url,body in helper.calls if url.endswith('/completions'))
            self.assertEqual(body['response_format']['type'],'json_object');self.assertFalse(body['echo_append']);self.assertEqual(body['tools'],[])
            self.assertTrue(result['diagnostics']['leaseReleased'])
            helper.restored=False;self.assertEqual(helper.improve(request)['status'],'error')
    def test_disabled_or_malformed_request_cannot_generate_or_replace_a_prompt(self):
        with tempfile.TemporaryDirectory() as root:
            helper=FakeAssistant(root);self.assertEqual(helper.improve({'enabled':False})['status'],'error');self.assertFalse(helper.calls)
            helper.reply='not JSON';self.assertEqual(helper.improve({'enabled':True,'prompt':'cup','runtime':{'status':'running','backendPort':8811,'echoPort':8813}})['status'],'error')

if __name__=='__main__':unittest.main()
