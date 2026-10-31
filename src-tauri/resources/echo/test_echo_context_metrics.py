"""Exercise the slot telemetry HTTP boundary without model weights or user data."""
import hashlib
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import echo_server


class SlotMetricsTests(unittest.TestCase):
    def setUp(self):
        self.slots=[]
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.assertEqual(self.path,'/slots')
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(owner.slots).encode())
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.state=echo_server.EchoState.__new__(echo_server.EchoState)
        self.state.upstream='http://127.0.0.1:'+str(self.server.server_port)
        self.state._backend_conversation='chat';self.state._backend_metrics_lock=threading.Lock()
        self.state._backend_metrics_key=None;self.state._backend_metrics_time=0;self.state._backend_metrics_value={}
        self.state._ctx_size=32768;self.state.active_window_tokens=32768

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def test_stock_owned_slot_exposes_occupancy_without_claiming_persistent_kv(self):
        self.slots=[{'id':0,'n_ctx':32768,'n_past':12500,'is_processing':True}]
        metrics=self.state.backend_session_metrics('chat')
        self.assertEqual(metrics.get('modelActiveTokens'),12500)
        self.assertEqual(metrics.get('modelContextTokens'),32768)
        self.assertTrue(metrics.get('modelSessionActive'))
        self.assertNotIn('modelSessionTokens',metrics)

    def test_standard_prompt_count_is_labeled_as_prompt_usage(self):
        self.slots=[{'id':0,'n_ctx':32768,'n_prompt_tokens':12000,'is_processing':True}]
        metrics=self.state.backend_session_metrics('chat')
        self.assertEqual(metrics.get('modelActiveTokens'),12000)
        self.assertEqual(metrics.get('modelUsageKind'),'prompt')
        self.assertNotIn('modelSessionTokens',metrics)

    def test_missing_occupancy_is_not_fabricated_from_generated_token_count(self):
        self.slots=[{'id':0,'n_ctx':32768,'next_token':{'n_decoded':80},'is_processing':True}]
        metrics=self.state.backend_session_metrics('chat')
        self.assertNotIn('modelActiveTokens',metrics)
        self.assertEqual(metrics.get('modelContextTokens'),32768)

    def test_other_conversation_never_receives_the_owned_slots_usage(self):
        self.slots=[{'id':0,'n_ctx':32768,'n_past':12500}]
        self.assertEqual(self.state.backend_session_metrics('other'),{})

    def test_a_different_echo_session_is_not_misattributed(self):
        self.slots=[{'id':0,'n_ctx':32768,'n_past':12500,'echo_session_id':'another-session'}]
        self.assertEqual(self.state.backend_session_metrics('chat'),{})

    def test_matching_echo_session_retains_its_verified_lifetime_count(self):
        self.slots=[{'id':0,'n_ctx':32768,'n_prompt_tokens':2000,'echo_session_tokens':60000,
                     'echo_session_id':hashlib.sha256(b'chat').hexdigest(),'is_processing':False}]
        metrics=self.state.backend_session_metrics('chat')
        self.assertEqual(metrics['modelActiveTokens'],2000)
        self.assertEqual(metrics['modelSessionTokens'],60000)

if __name__=='__main__':unittest.main()
