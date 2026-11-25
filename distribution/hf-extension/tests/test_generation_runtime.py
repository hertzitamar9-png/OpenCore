"""CPU-only control-plane/worker-contract tests; no torch import, model downloads or inference."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[3]
HERE = REPO / 'src-tauri/resources/runtime-setup/hf-extension'
sys.path.insert(0, str(HERE))
import hf_generation as generation
import generation_worker as worker
from hf_download import Downloader, validate_entry
from hf_catalog import stable_id
from hf_pipeline_mcp import handle
from hf_pipeline_tools import execute, catalog
from model_modes import apply_operating_modes


def ply(path, count=32):
    fields = ['x', 'y', 'z', 'f_dc_0', 'opacity', 'scale_0', 'rot_0']
    header = 'ply\nformat binary_little_endian 1.0\nelement vertex '+str(count)+'\n'
    header += ''.join('property float '+field+'\n' for field in fields)+'end_header\n'
    path.write_bytes(header.encode()+struct.pack('<'+'f'*len(fields)*count, *([.1]*(len(fields)*count))))


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.downloader=Downloader(self.root, Mock())
        self.manager=generation.GenerationManager(self.root, REPO/'src-tauri/resources', self.downloader)
        self.entry={'id':'hf-'+'1'*24,'repository':generation.MODEL[0],'revision':generation.MODEL[1],
                    'status':'installed','generationRuntime':'budget3d-idol-v1','category':'3d',
                    'runtime':{'adapter':'budget3d-idol'},'files':[]}
        self.input=self.root/'photo.png'
        self.request={'modelId':self.entry['id'],'settings':{'inputPath':str(self.input)}}

    def tearDown(self):
        self.manager.stop()
        self.temp.cleanup()

    def test_controls_reject_nonfinite_and_wrong_types_and_fake_mesh_export(self):
        for key,value in [('budget',0),('budget',float('nan')),('budget',True),('steps',2.5),('seed',-1),
                          ('numGaussians',524289),('guidanceScale',float('inf')),('imageMode','arbitrary'),('outputFormat','obj'),('shell','calc.exe')]:
            request=copy.deepcopy(self.request);request['settings'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError): generation.validate_request(request)

    def test_image_input_is_required_and_valid_budget_defaults_are_applied(self):
        for value in ('relative.png','https://example.com/photo.png',str(self.root/'image.svg')):
            with self.assertRaises(ValueError): generation.validate_request({**self.request,'settings':{'inputPath':value}})
        clean=generation.validate_request(self.request)
        self.assertEqual((clean['settings']['budget'],clean['settings']['steps']),(.5,50))
        self.assertEqual(clean['operatingMode'],'native')

    def test_all_dependency_files_have_immutable_pins_and_exclude_the_flux_text_model(self):
        values=self.manager.components()
        for value in values: validate_entry(value)
        self.assertEqual({value['componentId'] for value in values},{'idol','dinov3','flux2'})
        self.assertEqual(sum(value['bytes'] for value in values),6609537059)
        flux=next(value for value in values if value['componentId']=='flux2')
        self.assertTrue(all(pin['filename'].startswith('vae/') for pin in flux['files']))
        dino=next(value for value in values if value['componentId']=='dinov3')
        self.assertTrue(dino['gated'])

    def test_prepare_does_not_launch_inference_or_mark_runtime_ready(self):
        with (patch.object(self.manager,'_prepare',return_value={'dependenciesPrepared':True,'runtimeReady':False,'inferenceVerified':False}) as prepare,
              patch.object(self.manager,'_run') as run):
            proof=self.manager.prepare(self.entry,Mock(),threading.Event())
        run.assert_not_called();prepare.assert_called_once()
        self.assertTrue(proof['dependenciesPrepared']);self.assertFalse(proof['runtimeReady'])

    def test_gated_access_failure_precedes_any_large_download_or_environment_install(self):
        self.downloader.client.open_file.side_effect=ValueError('No authorized token')
        job=self.manager._new(self.entry,self.request,internal=True)
        try:
            with patch('hf_generation.checked_model_files'),patch.object(self.downloader,'install') as install,patch.object(self.manager,'ensure_environment') as environment:
                with self.assertRaisesRegex(ValueError,'DINOv3 access'): self.manager._prepare(self.entry,job)
                install.assert_not_called();environment.assert_not_called()
        finally:self.manager.operation.release()

    def test_only_one_owned_job_runs_and_cancel_is_not_a_model_error(self):
        entered,exit=threading.Event(),threading.Event()
        def run(*_):entered.set();exit.wait(3);raise RuntimeError('worker was terminated')
        with patch.object(self.manager,'_run',side_effect=run):
            job=self.manager.start(self.entry,self.request)
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError,'already running'):self.manager.start(self.entry,self.request)
            self.manager.stop(job['id']);exit.set()
            deadline=time.monotonic()+3
            while self.manager.operation.locked() and time.monotonic()<deadline:time.sleep(.01)
        saved=self.manager.list()[0]
        self.assertEqual(saved['status'],'cancelled');self.assertIsNone(saved['error'])

    def test_reopened_jobs_are_interrupted_and_never_replayed(self):
        self.manager._new(self.entry,self.request)
        self.manager.operation.release()
        reopened=generation.GenerationManager(self.root,REPO/'src-tauri/resources',self.downloader)
        self.assertEqual(reopened.list()[0]['status'],'interrupted')
        self.assertFalse(reopened.operation.locked())

    def test_gaussian_output_must_be_nonempty_binary_and_complete(self):
        file=self.root/'object.ply';ply(file)
        self.assertEqual(generation.verify_ply(file),32)
        file.write_bytes(file.read_bytes()[:-4])
        with self.assertRaisesRegex(ValueError,'truncated'):generation.verify_ply(file)
        file.write_text('pretend generated model')
        with self.assertRaises(ValueError):generation.verify_ply(file)

    def test_artifacts_cannot_read_other_job_or_user_files(self):
        job=self.manager._new(self.entry,self.request);self.manager.operation.release()
        out=self.manager.directory/job['id'];out.mkdir();file=out/'object.ply';ply(file)
        self.manager._update(job,status='completed',outputs=[str(file)])
        self.assertEqual(self.manager.artifact(job['id'],str(file)),file)
        with self.assertRaises(ValueError):self.manager.artifact(job['id'],str(self.root/'personal.txt'))
        with self.assertRaises(ValueError):self.manager.artifact('another-job',str(file))

    def test_worker_contract_encodes_samples_decodes_and_returns_actual_file_hashes(self):
        from PIL import Image
        job=self.manager._new(self.entry,self.request);self.manager.operation.release()
        output=self.manager.directory/job['id'];output.mkdir()
        image=output/'source.png';Image.new('RGB',(32,32),'red').save(image)
        model=self.root/'models/hub'/self.entry['id'];model.mkdir(parents=True)
        components={key:str(self.root/'models/hub'/key) for key in ('idol','dinov3','flux2')}
        events=[]
        class Backend:
            def __init__(inner,request,progress):inner.progress=progress
            def encode(inner,*args):events.append('encode');return Image.new('RGB',(32,32),'red'),'conditioning'
            def sample(inner,*args):events.append('sample');inner.progress('step',step=1,total=1);return 'latent','camera'
            def decode(inner,latent,weights,settings,destination):events.append('decode');ply(destination)
        request={**self.request,'settings':{'inputPath':str(image)},'modelRoot':str(self.root),'modelDirectory':str(model),
                 'repository':generation.MODEL[0],'revision':generation.MODEL[1],'components':components}
        result=worker.generate(request,output,backend_factory=Backend)
        self.assertEqual(events,['encode','sample','decode'])
        self.assertEqual(result['outputSha256'],hashlib.sha256((output/'object.ply').read_bytes()).hexdigest())
        self.assertEqual(result['inputSha256'],hashlib.sha256(image.read_bytes()).hexdigest())

    def test_worker_failure_never_writes_a_success_proof(self):
        from PIL import Image
        job=self.manager._new(self.entry,self.request);self.manager.operation.release()
        output=self.manager.directory/job['id'];output.mkdir()
        image=output/'source.png';Image.new('RGB',(32,32)).save(image)
        request={**self.request,'settings':{'inputPath':str(image)},'modelRoot':str(self.root),'modelDirectory':str(self.root/'models/hub/model'),
                 'repository':generation.MODEL[0],'revision':generation.MODEL[1],
                 'components':{key:str(self.root/'models/hub'/key) for key in ('idol','dinov3','flux2')}}
        with self.assertRaisesRegex(RuntimeError,'no memory'):
            worker.generate(request,output,backend_factory=Mock(side_effect=RuntimeError('no memory')))
        self.assertFalse((output/'generation-result.json').exists())

    def test_specialist_echo_selects_real_tool_dispatch_without_becoming_text_generation(self):
        model=copy.deepcopy(self.entry);apply_operating_modes([model])
        self.assertFalse(model['supportsTextGeneration']);self.assertEqual(model['operatingModes'],[])
        self.assertTrue(all(mode['supported'] for mode in model['modeAvailability']))
        self.assertEqual(model['modeAvailability'][1]['scope'],'tool')
        state=SimpleNamespace(lock=threading.RLock(),progress=None,entry=lambda *_,**__:model,generation=Mock(),images=Mock(),tts=Mock(),image_generated=Mock())
        for service in (state.generation,state.images):service.operation.locked.return_value=False
        state.tts.busy.return_value=False
        state.generation.start.return_value={'id':'real-job','status':'queued'}
        result=execute(state,'run',{'modelId':model['id'],'prompt':'3D object','settings':self.request['settings']})
        self.assertEqual(result['id'],'real-job')
        self.assertEqual(state.generation.start.call_args.args[1]['operatingMode'],'echo')

    def test_mcp_rejects_unknown_tools_and_surfaces_real_generation_errors(self):
        client=Mock();client.call.side_effect=ValueError('DINOv3 access required')
        result=handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'pipeline_run','arguments':{
            'modelId':self.entry['id'],'prompt':'object','settings':{}}}},client)
        self.assertTrue(result['result']['isError']);self.assertIn('DINOv3',result['result']['content'][0]['text'])
        client.reset_mock()
        result=handle({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'run_shell','arguments':{}}},client)
        self.assertTrue(result['result']['isError']);client.call.assert_not_called()


if __name__=='__main__':unittest.main()
