"""Local image jobs for complete Hub pipelines, using only bundled worker code."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
import importlib.util
import urllib.request

from hf_download import atomic_json
from hf_runtime import checked_model_files, _inside, _python
import hf_sdcpp

RECIPE='diffusers-images-v1'
TARGET='hub-image-runtime'
ACTIVE={'queued','starting','running','preparing'}


def supports(entry):
    return hf_sdcpp.supports(entry) or (entry.get('category')=='image' and entry.get('format')=='diffusers'
            and entry.get('packageKind')!='adapter'
            and any(Path(file['filename']).name=='model_index.json' for file in entry.get('files',[])))


def validate_request(request):
    if not isinstance(request,dict):raise ValueError('Invalid image request')
    prompt=request.get('prompt')
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>16000:raise ValueError('Enter an image prompt up to 16000 characters')
    settings=request.get('settings') or {}
    if not isinstance(settings,dict):raise ValueError('Invalid image controls')
    for name,default,low,high in [('width',512,128,2048),('height',512,128,2048),('steps',30,1,100),('numImages',1,1,8),('seed',831001,0,2**32-1)]:
        value=settings.get(name,default)
        if type(value) is not int or not low<=value<=high:raise ValueError(f'{name} must be an integer from {low} to {high}')
    if any(settings.get(name,512)%8 for name in ('width','height')):raise ValueError('Image dimensions must be multiples of 8')
    for name in ('outputWidth','outputHeight'):
        if name in settings and (type(settings[name]) is not int or not 128<=settings[name]<=8192 or settings[name]%8):
            raise ValueError(f'{name} must be a multiple of 8 from 128 to 8192')
    if set(settings)-{'width','height','outputWidth','outputHeight','steps','numImages','seed','guidanceScale','negativePrompt','outputFormat','loraPath','loraStrength','referencePaths','upscaleModelPath','promptRecap','originalPrompt'}:
        raise ValueError('This image request contains unsupported controls')
    for name,limit in [('promptRecap',300),('originalPrompt',16000)]:
        if name in settings and (not isinstance(settings[name],str) or len(settings[name])>limit):raise ValueError('Invalid prompt assistant summary')
    references=settings.get('referencePaths',[])
    if not isinstance(references,list) or len(references)>4 or any(not isinstance(path,str) or not Path(path).is_absolute() or Path(path).suffix.lower() not in ('.png','.jpg','.jpeg','.webp') for path in references):
        raise ValueError('Attach up to four local PNG, JPEG or WebP reference images')
    for name,default,high in [('guidanceScale',4,30),('loraStrength',0.8,2)]:
        value=settings.get(name,default)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<=value<=high:raise ValueError(f'{name} must be from 0 to {high}')
    if settings.get('outputFormat','png') not in ('png','jpeg','webp'):raise ValueError('Choose PNG, JPEG or WebP')
    conversation=request.get('conversationId')
    if conversation is not None and (not isinstance(conversation,str) or len(conversation)>512 or any(value in conversation for value in ('..','/','\\','\x00'))):
        raise ValueError('Invalid conversation identity')
    return {'modelId':request.get('modelId'),'prompt':prompt.strip(),'settings':copy.deepcopy(settings),'conversationId':conversation}


class ImageRuntimeManager:
    def __init__(self,root,resources):
        self.root,self.resources=Path(root).resolve(),Path(resources).resolve()
        self.directory=self.root/'studio/hub-image-jobs'
        self.directory.mkdir(parents=True,exist_ok=True)
        self.lock,self.operation=threading.RLock(),threading.Lock()
        self.child,self.cancelled,self.jobs=None,threading.Event(),[]
        self.conversations={}
        self.upscale_lock=threading.Lock()
        try:
            saved=json.loads((self.directory/'jobs.json').read_text(encoding='utf-8'))
            self.jobs=saved['jobs']
            self.conversations=saved.get('conversations',{})
            for job in self.jobs:
                if job['status'] in ACTIVE:job.update(status='interrupted',stage='Interrupted when OpenCore closed')
        except (OSError,ValueError,KeyError,TypeError):self.jobs=[]

    def python(self):
        return self.root/'runtime-setup/environments'/RECIPE/('Scripts/python.exe' if os.name=='nt' else 'bin/python')

    def prepare_upscaler(self):
        spec=importlib.util.spec_from_file_location('opencore_ai_upscale',self.resources/'studio/ai_upscale.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        folder=self.root/'models/components/image-upscale';folder.mkdir(parents=True,exist_ok=True)
        file=folder/module.MODEL_FILENAME
        with self.upscale_lock:
            if not file.is_file() or file.stat().st_size!=module.MODEL_BYTES or hashlib.sha256(file.read_bytes()).hexdigest()!=module.MODEL_SHA256:
                temporary=folder/(module.MODEL_FILENAME+'.download')
                try:
                    digest=hashlib.sha256();size=0
                    with urllib.request.urlopen(urllib.request.Request(module.MODEL_URL,headers={'User-Agent':'OpenCore AI image setup'}),timeout=45) as response,temporary.open('wb') as stream:
                        while chunk:=response.read(65536):
                            size+=len(chunk)
                            if size>module.MODEL_BYTES:raise ValueError('AI upscaler download exceeded its verified size')
                            digest.update(chunk);stream.write(chunk)
                    if size!=module.MODEL_BYTES or digest.hexdigest()!=module.MODEL_SHA256:raise ValueError('AI upscaler download failed its publisher checksum')
                    temporary.replace(file)
                finally:temporary.unlink(missing_ok=True)
        return {'path':str(file),'modelId':module.MODEL_ID,'sha256':module.MODEL_SHA256}

    def dependencies_ready(self):
        try:
            manifest=json.loads((self.resources/'runtime-setup/recipes.json').read_text(encoding='utf-8'))
            recipe=next(item for item in manifest['recipes'] if item['id']==RECIPE)
            fingerprint=hashlib.sha256(json.dumps(recipe,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            receipt=json.loads((self.root/'runtime-setup/receipts'/f'{TARGET}.json').read_text(encoding='utf-8'))
            return bool(receipt.get('dependenciesVerified') is True and receipt.get('recipeFingerprint')==fingerprint and self.python().is_file()
                        and Path(receipt.get('python','')).resolve()==self.python().resolve()
                        and receipt.get('hardware',{}).get('kernelVerified') is True)
        except (OSError,ValueError,TypeError,StopIteration):return False

    def ready(self,entry,proof):
        try:
            return bool(supports(entry) and entry.get('status')=='installed' and isinstance(proof,dict)
                        and proof.get('inferenceVerified') is True and proof.get('receiptRevision')==entry['revision']
                        and proof.get('workerVersion')==1 and proof.get('workerSha256')==self.worker_identity() and self.dependencies_ready())
        except (OSError,ValueError,TypeError):return False

    def worker_identity(self):
        digest=hashlib.sha256()
        for name in ('asset_worker.py','comfy_worker.py','model_smoke.py'):
            digest.update((self.resources/'studio'/name).read_text(encoding='utf-8').replace('\r\n','\n').encode())
        source=self.resources/'runtime-setup/hf-extension/hf_sdcpp.py'
        if source.is_file():digest.update(source.read_text(encoding='utf-8').replace('\r\n','\n').encode())
        return digest.hexdigest()

    def _update(self,job,**value):
        with self.lock:
            job.update(value,updatedAt=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
            atomic_json(self.directory/'jobs.json',{'schema':1,'jobs':self.jobs,'conversations':self.conversations})

    def list(self):
        with self.lock:return copy.deepcopy(list(reversed([job for job in self.jobs if not job.get('internalVerification') and not job.get('hidden')][-100:])))

    def conversation_summaries(self):
        with self.lock:
            groups={}
            for job in self.jobs:
                if job.get('internalVerification') or job.get('hidden'):continue
                identity=job['request'].get('conversationId')
                if identity and not self.conversations.get(identity,{}).get('deleted'):groups.setdefault(identity,[]).append(job)
            summaries=[]
            for identity,jobs in groups.items():
                saved=self.conversations.get(identity,{})
                summaries.append({'id':identity,'client':'OpenCore','title':saved.get('title') or jobs[0]['request']['prompt'][:100],
                                  'createdAt':jobs[0]['createdAt'],'updatedAt':jobs[-1].get('updatedAt',jobs[-1]['createdAt']),
                                  'messageCount':sum(1+int(job['status'] not in ACTIVE) for job in jobs),'profile':'image',
                                  'status':'active' if any(job['status'] in ACTIVE for job in jobs) else 'completed',
                                  'project':'','projectId':saved.get('projectId'),'pinned':bool(saved.get('pinned'))})
            return copy.deepcopy(summaries)

    def conversation_entries(self,identity):
        rows=[]
        with self.lock:
            if self.conversations.get(identity,{}).get('deleted'):return []
            for job in self.jobs:
                if job.get('internalVerification') or job.get('hidden'):continue
                if job['request'].get('conversationId')!=identity:continue
                number=-int(hashlib.sha256(job['id'].encode()).hexdigest()[:12],16)
                common={'conversationId':identity,'source':'OpenCore','kind':'message','metadata':{'hubImageJobId':job['id'],'studioReceipt':True}}
                rows.append({**common,'id':number,'timestamp':job['createdAt'],'role':'user','title':'You','content':job['request']['prompt']})
                if job['status'] not in ACTIVE:
                    content='Image generated.' if job['status']=='completed' else 'Image generation '+job['status']+'. '+str(job.get('error') or '')
                    rows.append({**common,'id':number+1,'timestamp':job.get('updatedAt',job['createdAt']),'role':'assistant','title':'OpenCore','content':content})
        return rows

    def update_conversation(self,body):
        identity=body.get('id')
        if not isinstance(identity,str) or identity not in {row['id'] for row in self.conversation_summaries()}:raise ValueError('Image conversation not found')
        action=body.get('action')
        with self.lock:
            saved=self.conversations.setdefault(identity,{})
            if action=='rename':
                title=body.get('title')
                if not isinstance(title,str) or not title.strip() or len(title)>500:raise ValueError('Enter a conversation title')
                saved['title']=title.strip()
            elif action=='pin':saved['pinned']=bool(body.get('pinned'))
            elif action=='project':
                project=body.get('projectId')
                if project is not None and (not isinstance(project,str) or len(project)>200):raise ValueError('Invalid project')
                saved['projectId']=project
            elif action=='delete':
                if any(job['request'].get('conversationId')==identity and job['status'] in ACTIVE for job in self.jobs):raise ValueError('Cancel image generation before deleting this conversation')
                saved['deleted']=True
            else:raise ValueError('Unknown conversation action')
            atomic_json(self.directory/'jobs.json',{'schema':1,'jobs':self.jobs,'conversations':self.conversations})
        return {'updated':True}

    def stop(self,identity=None):
        with self.lock:
            if identity is not None and not any(job['id']==identity and job['status'] in ACTIVE for job in self.jobs):
                raise ValueError('This image job is not active')
            self.cancelled.set()
            if self.directory.is_dir():(self.directory/'cancel').touch()
            child=self.child
        if child and child.poll() is None:
            from hf_specialist_ocr import OCRManager
            OCRManager._terminate(child)
        return {'cancelled':True}

    def _spawn(self,command,job,*,output=None,timeout=3600):
        if self.cancelled.is_set():raise RuntimeError('Image generation cancelled')
        log=self.directory/(job['id']+'.log')
        environment=dict(os.environ,PYTHONIOENCODING='utf-8',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
        with log.open('wb') as stream:
            child=subprocess.Popen([str(arg) for arg in command],stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,
                                   env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0))
            with self.lock:self.child=child
            started=time.monotonic()
            try:
                while child.poll() is None:
                    if self.cancelled.is_set() or time.monotonic()-started>timeout:
                        timed_out=not self.cancelled.is_set()
                        self.stop()
                        raise RuntimeError('Image generation timed out' if timed_out else 'Image generation cancelled')
                    if output:
                        try:
                            progress=json.loads((output/'progress.json').read_text(encoding='utf-8'))
                            if progress.get('stage') and progress['stage']!=job['stage']:self._update(job,stage=str(progress['stage'])[:300])
                        except (OSError,ValueError):pass
                    time.sleep(0.5)
                if self.cancelled.is_set():raise RuntimeError('Image generation cancelled')
                if child.returncode:
                    with log.open('rb') as error_log:
                        error_log.seek(max(0,log.stat().st_size-3000))
                        detail=error_log.read().decode('utf-8',errors='replace').strip()
                    raise RuntimeError(detail[-1800:] or 'The local image worker failed')
            finally:
                if child.poll() is None:
                    self.stop()
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.kill()
                with self.lock:
                    if self.child is child:self.child=None

    def _run(self,entry,request,job):
        self._update(job,status='preparing',stage='Verifying installed model files')
        checked_model_files(entry,self.root,self.cancelled)
        folder=_inside(entry['localDirectory'],self.root/'models/hub')
        directory=_inside(folder/entry['subdirectory'],folder) if entry.get('subdirectory') else folder
        if hf_sdcpp.supports(entry):
            return self._run_iris(entry,request,job)
        index=json.loads((directory/'model_index.json').read_text(encoding='utf-8'))
        name=index.get('_class_name')
        if not isinstance(name,str) or not name.endswith('Pipeline') or not name.replace('_','').isalnum():
            raise ValueError('This repository does not declare a standard Diffusers pipeline')
        if not self.dependencies_ready():
            self._update(job,stage='Installing the isolated image runtime')
            self._spawn([_python(self.root),'-I',self.resources/'runtime-setup/setup_manager.py','--root',self.root,
                         '--resources',self.resources,'--target',TARGET,'--recipe-id',RECIPE,'--cancel-file',self.directory/'cancel'],job)
            if not self.dependencies_ready():raise RuntimeError('The managed image dependencies did not pass verification')
        output=self.directory/job['id']
        output.mkdir(exist_ok=False)
        native_request={**request,'modelRoot':str(self.root),'modelDirectory':str(directory),'sourceRepository':entry['repository'],'pipelineVariant':entry.get('diffusersVariant')}
        request_path=self.directory/(job['id']+'.json')
        atomic_json(request_path,native_request)
        self._update(job,status='running',stage='Loading the image model')
        self._spawn([self.python(),'-I',self.resources/'studio/model_smoke.py','--request',request_path,'--output',output],job,output=output)
        proof=json.loads((output/'smoke-result.json').read_text(encoding='utf-8'))
        if proof.get('inferenceVerified') is not True or proof.get('modelId')!=entry['id']:raise RuntimeError('The image worker did not verify a generated image')
        paths=[]
        for image in proof.get('outputs',[]):
            path=_inside(image['path'],output)
            if not path.is_file() or path.suffix.lower() not in ('.png','.jpeg','.jpg','.webp'):raise ValueError('Invalid image output')
            paths.append(str(path))
        if not paths:raise RuntimeError('The image worker returned no images')
        self._update(job,status='completed',stage='Generation complete',outputs=paths)
        return {'runtimeReady':True,'inferenceVerified':True,'receiptRevision':entry['revision'],'workerVersion':1,'workerSha256':self.worker_identity(),'verifiedAt':time.time()}

    def _run_iris(self,entry,request,job):
        hf_sdcpp.model_paths(entry)
        if not self.dependencies_ready():
            self._update(job,stage='Preparing the isolated image tools')
            self._spawn([_python(self.root),'-I',self.resources/'runtime-setup/setup_manager.py','--root',self.root,
                         '--resources',self.resources,'--target',TARGET,'--recipe-id',RECIPE,'--cancel-file',self.directory/'cancel'],job)
            if not self.dependencies_ready():raise RuntimeError('The managed image tools did not pass verification')
        engine=hf_sdcpp.ensure_engine(self.root,self.cancelled,lambda message:self._update(job,stage=message))
        output=self.directory/job['id'];output.mkdir(exist_ok=False)
        payload={'entry':entry,'request':request,'engine':str(engine)}
        request_file=self.directory/(job['id']+'.json');atomic_json(request_file,payload)
        self._update(job,status='running',stage='Generating with Sakura-Iris · 30-step quality preset')
        self._spawn([self.python(),'-I',self.resources/'runtime-setup/hf-extension/hf_sdcpp.py','--request',request_file,'--output',output],job,output=output)
        proof=json.loads((output/'smoke-result.json').read_text(encoding='utf-8'))
        if proof.get('modelId')!=entry['id'] or proof.get('inferenceVerified') is not True:raise RuntimeError('The Iris worker did not verify the generation')
        paths=[str(_inside(item['path'],output)) for item in proof['outputs']]
        if not paths or any(not Path(path).is_file() for path in paths):raise RuntimeError('Iris returned no image files')
        self._update(job,status='completed',stage='Generation complete',outputs=paths)
        return {'runtimeReady':True,'inferenceVerified':True,'receiptRevision':entry['revision'],'workerVersion':1,'workerSha256':self.worker_identity(),'verifiedAt':time.time()}

    def _new(self,entry,request,internal=False):
        if not supports(entry) or entry.get('status')!='installed':raise ValueError('Download a complete supported Diffusers image package first')
        if not self.operation.acquire(blocking=False):raise ValueError('An image operation is already running')
        self.cancelled.clear()
        try:
            (self.directory/'cancel').unlink(missing_ok=True)
            job={'id':'hub-image-'+uuid.uuid4().hex,'source':'hub-image','category':'image','request':request,'status':'queued',
                 'stage':'Queued','createdAt':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'outputs':[],'error':None,'internalVerification':internal}
            with self.lock:self.jobs.append(job)
            self._update(job)
        except Exception:
            self.operation.release()
            raise
        return job

    def start(self,entry,request,on_ready=None):
        request=validate_request(request)
        if request['modelId']!=entry['id']:raise ValueError('The image request selects a different model')
        job=self._new(entry,request)
        def work():
            try:
                proof=self._run(entry,request,job)
                if on_ready:on_ready(proof)
            except Exception as error:self._update(job,status='cancelled' if self.cancelled.is_set() else 'failed',stage='Cancelled' if self.cancelled.is_set() else 'Failed',error=str(error)[:2000])
            finally:self.operation.release()
        threading.Thread(target=work,daemon=True).start()
        return copy.deepcopy(job)

    def prepare(self,entry,report,pause):
        request=validate_request({'modelId':entry['id'],'prompt':'A blue ceramic cup on a plain table','settings':{'width':512,'height':512,'steps':20 if hf_sdcpp.supports(entry) else 4,'guidanceScale':3,'outputFormat':'png'}})
        job=self._new(entry,request,internal=True)
        try:
            report({'stage':'verifying-inference','detail':'Preparing the image runtime and generating a verification image'})
            if pause.is_set():self.stop()
            return self._run(entry,request,job)
        except Exception as error:
            self._update(job,status='cancelled' if self.cancelled.is_set() else 'failed',stage='Failed',error=str(error)[:2000])
            raise
        finally:self.operation.release()

    def preview(self,identity,path):
        with self.lock:job=next((job for job in self.jobs if job['id']==identity),None)
        if not job or path not in job['outputs']:raise ValueError('Choose an image output from this job')
        file=_inside(path,self.directory/identity)
        if file.suffix.lower() not in ('.png','.jpg','.jpeg','.webp') or not 0<file.stat().st_size<=64*1024**2:raise ValueError('Invalid image preview')
        mime={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}[file.suffix.lower()]
        return {'mime':mime,'dataUrl':'data:'+mime+';base64,'+base64.b64encode(file.read_bytes()).decode()}
