import io
import threading
import time
import unittest
from echo_server import Handler


class HeartbeatTests(unittest.TestCase):
    def test_waiting_stream_sends_heartbeats_and_stops_after_done(self):
        handler=Handler.__new__(Handler)
        handler.wfile=io.BytesIO()
        handler.send_response=lambda code:None
        handler.send_header=lambda *args:None
        handler.end_headers=lambda:None
        handler._heartbeat_interval=.02
        handler._generation_log_status=lambda:{}
        handler._live_event({'echo_context':{'active':True}})
        try:
            time.sleep(.075)
            self.assertIn(b': OpenCore working\n\n',handler.wfile.getvalue())
            self.assertIn(b'"delta": {}',handler.wfile.getvalue())
            handler._finish_live({'choices':[{'message':{'content':'Ready'},'finish_reason':'stop'}]})
            before=handler.wfile.getvalue()
            time.sleep(.06)
            self.assertEqual(handler.wfile.getvalue(),before)
            self.assertTrue(before.endswith(b'data: [DONE]\n\n'))
        finally:
            if hasattr(handler,'_heartbeat_stop'): handler._heartbeat_stop.set()
