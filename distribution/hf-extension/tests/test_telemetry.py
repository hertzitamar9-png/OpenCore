import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hf_telemetry import LiveTelemetry


class TelemetryTests(unittest.TestCase):
    def test_samples_change_during_decode_without_waiting_for_snapshot_cache(self):
        now=[10.0];gpu=[40];count=[50]
        reader=LiveTelemetry(lambda:{'gpuUtilization':gpu[0],'vramUsedMib':2000+gpu[0]},lambda:now[0])
        runtime={'status':'running','profile':'test','echoPort':8813}
        context=lambda:{'modelOutputTokens':count[0],'modelActiveTokens':12000+count[0]-1,'modelSessionActive':True}
        first=reader.read(runtime,'chat',context)
        now[0]+=1;gpu[0]=90;count[0]=85
        second=reader.read(runtime,'chat',context)
        self.assertEqual(first['telemetry']['gpuUtilization'],40)
        self.assertEqual(second['telemetry']['gpuUtilization'],90)
        self.assertEqual(second['telemetry']['tokensPerSecond'],35)
        self.assertEqual(second['telemetry']['completionTokens'],85)
        self.assertEqual(second['context']['modelActiveTokens'],12084)

    def test_idle_samples_do_not_read_context_or_claim_active_generation(self):
        reader=LiveTelemetry(lambda:{'gpuUtilization':5})
        def fail(): raise AssertionError('Idle context read')
        self.assertEqual(reader.read({'status':'stopped'},None,fail)['telemetry']['tokensPerSecond'],0)

    def test_restarted_decode_and_other_chats_never_inherit_a_rate(self):
        now=[10.0];reader=LiveTelemetry(lambda:{},lambda:now[0]);runtime={'status':'running'}
        for conversation,count in [('a',100),('b',200),('b',10)]:
            now[0]+=1
            self.assertEqual(reader.read(runtime,conversation,lambda:{'modelOutputTokens':count,'modelSessionActive':True})['telemetry']['tokensPerSecond'],0)
