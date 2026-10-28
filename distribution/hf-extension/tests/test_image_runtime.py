import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from hf_image_runtime import ImageRuntimeManager, supports, validate_request


class ImageRuntimeTests(unittest.TestCase):
    def entry(self,root):
        folder=root/'models/hub/hf-test'
        folder.mkdir(parents=True)
        (folder/'model_index.json').write_text(json.dumps({'_class_name':'StableDiffusionPipeline'}))
        return {'id':'hf-test','repository':'owner/image','revision':'a'*40,'status':'installed','category':'image',
                'format':'diffusers','localDirectory':str(folder),'files':[{'filename':'model_index.json','role':'support'}]}

    def test_only_complete_image_generators_are_admitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry=self.entry(Path(tmp))
            self.assertTrue(supports(entry))
            for fields in ({'format':'gguf'},{'category':'text'},{'packageKind':'adapter'},{'files':[]}):
                self.assertFalse(supports({**entry,**fields}))

    def test_request_bounds_reject_remote_paths_and_excessive_allocations(self):
        request={'modelId':'hf-test','prompt':'A blue cup','conversationId':'opencore:example','settings':{'steps':4}}
        self.assertEqual(validate_request(request)['conversationId'],'opencore:example')
        for settings in ({'width':True},{'height':2049},{'width':129},{'steps':0},{'guidanceScale':float('nan')},{'outputFormat':'exe'},{'modelDirectory':'C:/elsewhere'}):
            with self.assertRaises(ValueError):validate_request({**request,'settings':settings})
        with self.assertRaises(ValueError):validate_request({**request,'conversationId':'../other'})

    def test_local_worker_completion_is_persisted_and_preview_is_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.entry(root);manager=ImageRuntimeManager(root,root/'resources')
            def worker(command,job,**kwargs):
                output=kwargs['output'];path=output/'image-1.png';path.write_bytes(b'fixture image')
                (output/'smoke-result.json').write_text(json.dumps({'modelId':entry['id'],'inferenceVerified':True,'outputs':[{'path':str(path)}]}))
            with patch.object(manager,'dependencies_ready',return_value=True),patch.object(manager,'worker_identity',return_value='proof-worker'),patch('hf_image_runtime.checked_model_files') as verify,patch.object(manager,'_spawn',side_effect=worker):
                job=manager.start(entry,{'modelId':entry['id'],'prompt':'A blue cup','conversationId':'opencore:test'})
                deadline=time.monotonic()+5
                while manager.operation.locked() and time.monotonic()<deadline:time.sleep(0.01)
                result=manager.list()[0]
                self.assertEqual(result['status'],'completed',result)
                verify.assert_called_once()
                request=json.loads((manager.directory/(job['id']+'.json')).read_text())
                self.assertEqual(Path(request['modelDirectory']),Path(entry['localDirectory']))
                self.assertEqual(request['sourceRepository'],'owner/image')
                self.assertEqual(request['conversationId'],'opencore:test')
                self.assertTrue(manager.preview(job['id'],result['outputs'][0])['dataUrl'].startswith('data:image/png;base64,'))
                with self.assertRaises(ValueError):manager.preview(job['id'],str(root/'private.png'))
                self.assertEqual(ImageRuntimeManager(root,root/'resources').list()[0]['status'],'completed')
                self.assertEqual(manager.conversation_summaries()[0]['id'],'opencore:test')
                self.assertEqual(len(manager.conversation_entries('opencore:test')),2)
                manager.update_conversation({'id':'opencore:test','action':'rename','title':'Image test'})
                self.assertEqual(manager.conversation_summaries()[0]['title'],'Image test')
                manager.update_conversation({'id':'opencore:test','action':'delete'})
                self.assertEqual(ImageRuntimeManager(root,root/'resources').conversation_summaries(),[])

    def test_failed_worker_releases_admission_and_keeps_error_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.entry(root);manager=ImageRuntimeManager(root,root/'resources')
            with patch('hf_image_runtime.checked_model_files',side_effect=ValueError('Model checksum changed')):
                manager.start(entry,{'modelId':entry['id'],'prompt':'A cup'})
                deadline=time.monotonic()+5
                while manager.operation.locked() and time.monotonic()<deadline:time.sleep(0.01)
            self.assertEqual(manager.list()[0]['status'],'failed')
            self.assertIn('checksum',manager.list()[0]['error'])
            self.assertFalse(manager.operation.locked())

    def test_reopened_jobs_never_replay_previous_generations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);manager=ImageRuntimeManager(root,root/'resources')
            (manager.directory/'jobs.json').write_text(json.dumps({'jobs':[{'id':'hub-image-test','status':'running'}]}))
            reopened=ImageRuntimeManager(root,root/'resources')
            self.assertEqual(reopened.list()[0]['status'],'interrupted')
            self.assertFalse(reopened.operation.locked())


if __name__=='__main__':unittest.main()
