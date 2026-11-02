"""Owned AutoLoop runs and promotion; fixtures do not establish model quality."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src-tauri/resources/runtime-setup/hf-extension'))
from autoloop_manager import AutoLoopManager


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.entries = {}
        self.progress = None
        self.persisted = 0
        self.runtime = SimpleNamespace(_generation=threading.Lock(), _preparation=threading.Lock())
    def persist_catalog(self): self.persisted += 1
    def entry(self, identity, installed=False): return self.entries[identity].copy()


class Runtime:
    def __init__(self, root, resources): self.stopped = False
    def prepare(self, entry):
        return {'runtimeReady': True, 'inferenceVerified': True, 'revision': entry['revision'],
                'operatingMode': 'echo', 'echoMode': 'main-context-v1', 'contextTokens': 32768}
    def stop(self): self.stopped = True


class AutoLoopManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.root = self.base/'root'; self.resources = self.base/'resources'
        self.root.mkdir(); self.resources.mkdir()
        (self.root/'runtime').mkdir(); (self.root/'runtime/llama-server.exe').write_bytes(b'fixture runtime')
        self.model = self.base/'original.gguf'; self.model.write_bytes(b'GGUF fixture source')
        self.state = FakeState(); self.mode = 'accepted'; self.gate = threading.Event(); self.started = threading.Event()
        parent = self
        class Engine:
            def __init__(self, settings, progress, cancel): self.settings, self.progress, self.cancel = settings, progress, cancel
            def run(self):
                parent.started.set()
                self.progress({'stage':'development','event':'question','completed':1,'total':31})
                if parent.mode == 'wait':
                    self.cancel.wait(3)
                    return {'status':'cancelled','acceptedArtifact':None}
                if parent.mode == 'rejected': return {'status':'rejected','acceptedArtifact':None,'reason':'No confirmed gain'}
                target = Path(self.settings.output_dir)/'accepted.gguf'; target.write_bytes(b'GGUF fixture winner')
                benchmark=target.parent/'benchmark.json';benchmark.write_text('["fixture"]')
                config=target.with_suffix('.config.json')
                config.write_text(json.dumps({'acceptedSha256':digest(target),'sourceSha256':digest(self.settings.model),'runtimeSha256':digest(self.settings.llama_server),'benchmarkSha256':digest(benchmark)}))
                artifact = {'path':str(target),'bytes':target.stat().st_size,'sha256':digest(target),'sourceSha256':digest(self.settings.model),
                            'runtimeSha256':digest(self.settings.llama_server),'benchmarkSha256':digest(benchmark),'configPath':str(config),'configSha256':digest(config),'inferenceVerified':True,'qualityScope':'fixture only'}
                if parent.mode == 'unverified': artifact['inferenceVerified'] = False
                if parent.mode == 'tampered': artifact['sha256'] = 'f'*64
                if parent.mode == 'source-changed': Path(self.settings.model).write_bytes(b'changed source')
                if parent.mode == 'outside': artifact['path'] = str(parent.model)
                if parent.mode == 'config-tampered':config.write_text('{}')
                if parent.mode == 'benchmark-tampered':benchmark.write_text('[]')
                return {'schema':1,'status':'accepted','acceptedArtifact':artifact,'capabilities':{'architecture':'llama','blockCount':8},
                        'benchmark':{'manifestPath':str(benchmark),'sha256':artifact['benchmarkSha256']},
                        'source':{'before':{'sha256':digest(parent.model)}},'confirmation':{'gates':{'accepted':True}}}
        self.core = SimpleNamespace(Engine=Engine, Settings=lambda **kw:SimpleNamespace(**kw),
            inspect_model=lambda path:{'supported':True,'architecture':'llama','blockCount':8,'bytes':Path(path).stat().st_size,'contextTokens':32768})
        self.manager = AutoLoopManager(self.root,self.resources,self.state,core=self.core,runtime_factory=Runtime)
        self.addCleanup(self.manager.close)

    def start(self, **extra): return self.manager.start({'modelPath':str(self.model),**extra})
    def done(self, run):
        for _ in range(300):
            value = self.manager.status()
            match = next(x for x in value['runs'] if x['id']==run['id'])
            if match['status'] in ('accepted','rejected','failed','cancelled'): return match
            time.sleep(.01)
        self.fail('Fixture run did not finish')

    def test_accepted_winner_has_separate_pinned_installed_identity_and_source_stays(self):
        before=self.model.read_bytes(); value=self.done(self.start())
        self.assertEqual(value['status'],'accepted'); self.assertTrue(value['runtimeReady'])
        entry=self.state.entries[value['modelId']]
        self.assertEqual(entry['origin'],'autoloop'); self.assertTrue(entry['localOnly'])
        self.assertEqual(entry['files'][0]['sha256'],digest(entry['localDirectory']+'/model.gguf'))
        self.assertEqual(entry['runtimePreparation']['echoMode'],'main-context-v1')
        self.assertEqual(self.model.read_bytes(),before)
        self.assertEqual(self.state.persisted,1)

    def test_no_gain_retains_original_and_never_registers(self):
        self.mode='rejected'; before=self.model.read_bytes(); value=self.done(self.start())
        self.assertEqual(value['status'],'rejected'); self.assertEqual(self.state.entries,{})
        self.assertEqual(self.model.read_bytes(),before)

    def test_forged_or_unverified_candidates_never_promote(self):
        for mode in ('unverified','tampered','outside','source-changed','config-tampered','benchmark-tampered'):
            with self.subTest(mode=mode):
                self.mode=mode; self.model.write_bytes(b'GGUF fixture source')
                value=self.done(self.start()); self.assertEqual(value['status'],'failed')
                self.assertEqual(self.state.entries,{})

    def test_runtime_verification_is_required_and_owned_runtime_is_stopped(self):
        instances=[]
        class BadRuntime(Runtime):
            def __init__(self,*args): super().__init__(*args); instances.append(self)
            def prepare(self,entry): return {'runtimeReady':True,'inferenceVerified':False}
        self.manager.runtime_factory=BadRuntime
        value=self.done(self.start()); self.assertEqual(value['status'],'failed')
        self.assertEqual(self.state.entries,{}); self.assertTrue(instances[0].stopped)
        self.assertFalse(list((self.root/'models/hub').glob('hf-*/*.gguf')))
        report=self.manager.report(value['id'])
        self.assertEqual(report['optimizationStatus'],'accepted')
        self.assertEqual(report['status'],'failed')
        self.assertIn('confirmation',report)
        self.assertIsNone(report['acceptedArtifact'])

    def test_cancel_during_catalog_commit_rolls_back_the_new_entry(self):
        original=self.state.persist_catalog
        def persist():
            original()
            if self.state.persisted==1:self.manager.cancel(self.manager.active)
        self.state.persist_catalog=persist
        value=self.done(self.start())
        self.assertEqual(value['status'],'cancelled');self.assertEqual(self.state.entries,{})
        self.assertFalse(list((self.root/'models/hub').glob('hf-*/*.gguf')))

    def test_cancel_after_completed_commit_cannot_turn_accepted_into_cancelling(self):
        results=[];manager=self.manager
        class FinishingRuntime(Runtime):
            def stop(self):
                super().stop()
                if manager.active:results.append(manager.cancel(manager.active))
        self.manager.runtime_factory=FinishingRuntime
        value=self.done(self.start())
        for _ in range(100):
            if results:break
            time.sleep(.01)
        self.assertEqual(value['status'],'accepted');self.assertTrue(results)
        self.assertFalse(results[0]['cancelling'])
        self.assertEqual(self.manager.status()['runs'][0]['status'],'accepted')

    def test_duplicate_start_rejected_and_cancel_stops_only_own_run(self):
        self.mode='wait'; run=self.start(); self.assertTrue(self.started.wait(1))
        with self.assertRaisesRegex(ValueError,'already'): self.start()
        self.manager.cancel(run['id']); self.assertEqual(self.done(run)['status'],'cancelled')
        self.assertTrue(self.model.exists()); self.assertEqual(self.state.entries,{})

    def test_bounded_validated_controls_and_no_external_runtime_or_output_override(self):
        for extra in ({'maxVariants':0},{'maxVariants':9},{'threads':100},{'maxMinutes':0},
                      {'llamaServer':'evil.exe'},{'outputDirectory':str(self.base)},{'experimentalDamping':'yes'}):
            with self.subTest(extra=extra),self.assertRaises(ValueError): self.start(**extra)

    def test_active_download_or_generation_prevents_start(self):
        self.state.progress={'status':'running'}
        with self.assertRaisesRegex(ValueError,'finish|pause'): self.start()
        self.state.progress=None; self.state.runtime._generation.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'response'): self.start()
        finally: self.state.runtime._generation.release()

    def test_list_only_known_installed_paths_deduplicated_without_model_hashing(self):
        ext=self.resources/'runtime-setup/hf-extension';ext.mkdir(parents=True)
        model=self.root/'models/library/test/original.gguf';model.parent.mkdir(parents=True);model.write_bytes(b'GGUF')
        (ext/'native-model-bindings.json').write_text(json.dumps({'models':[{'id':'test','label':'Test','runtimeModelPath':'models/library/test/original.gguf','supportsTextGeneration':True},{'id':'test-native','label':'Test native','runtimeModelPath':'models/library/test/original.gguf','supportsTextGeneration':True}], 'artifacts':[]}))
        result=self.manager.models();self.assertEqual(len(result['models']),1)
        self.assertEqual(result['models'][0]['modelPath'],str(model.resolve()))
        self.assertFalse(any('sha256' in x for x in result['models']))

    def test_real_catalog_reloads_ready_local_proof_and_uninstall_preserves_source(self):
        from hf_bridge import BridgeState, RuntimeUnavailable
        state=BridgeState(self.root,self.resources,runtime=RuntimeUnavailable())
        self.addCleanup(state.close);self.manager.state=state
        original=self.model.read_bytes();value=self.done(self.start())
        self.assertEqual(value['status'],'accepted')
        self.assertTrue(state.entry(value['modelId'])['runtimeReady'])
        restored=BridgeState(self.root,self.resources,runtime=RuntimeUnavailable())
        self.addCleanup(restored.close)
        self.assertTrue(restored.entry(value['modelId'])['runtimeReady'])
        with self.assertRaisesRegex(ValueError,'generated locally'):restored.start(value['modelId'],'download')
        plan=restored.removal_plan(value['modelId'])
        restored.uninstall(value['modelId'],plan['confirmationToken'])
        self.assertNotIn(value['modelId'],restored.entries)
        self.assertEqual(self.model.read_bytes(),original)

    def test_report_read_is_scoped_to_owned_valid_run_id(self):
        run=self.start(); self.done(run)
        self.assertEqual(self.manager.report(run['id'])['status'],'accepted')
        for value in ('../original',str(self.model),'f'*32):
            with self.assertRaises(ValueError): self.manager.report(value)

    def test_restarted_active_run_becomes_interrupted_without_launching_a_child(self):
        folder=self.root/'learning/autoloop/runs'/('b'*32);folder.mkdir(parents=True)
        (folder/'state.json').write_text(json.dumps({'id':'b'*32,'status':'running','createdAt':1,'updatedAt':1}))
        other=AutoLoopManager(self.root,self.resources,self.state,core=self.core,runtime_factory=Runtime)
        self.addCleanup(other.close)
        value=next(x for x in other.status()['runs'] if x['id']=='b'*32)
        self.assertEqual(value['status'],'failed'); self.assertIn('interrupted',value['error'].lower())


if __name__=='__main__': unittest.main()
