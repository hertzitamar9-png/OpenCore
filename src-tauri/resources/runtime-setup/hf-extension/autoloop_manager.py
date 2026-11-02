"""Owner-requested, training-free experiments and verified local model promotion.

No inference starts at app startup. Only this manager's children and newly
created artifacts are disposable; source checkpoints and external runtimes are
never modified or stopped. A benchmark gain is scoped to that benchmark.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

from hf_download import atomic_json, managed_path, validate_entry

TERMINAL = {'accepted', 'rejected', 'failed', 'cancelled', 'baseline_complete'}
RUN_ID = re.compile(r'[0-9a-f]{32}\Z')


def _read(path, limit=16 * 1024 * 1024):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError('AutoLoop record is too large')
    return json.loads(path.read_text(encoding='utf-8'))


def _plain_file(path, suffix=None):
    if not isinstance(path, (str, Path)) or not str(path) or '\x00' in str(path):
        raise ValueError('Choose a local model file')
    path = Path(path).absolute()
    if not path.is_absolute() or not path.is_file() or suffix and path.suffix.lower() != suffix:
        raise ValueError('Choose an existing single-file GGUF checkpoint')
    for part in (path, *path.parents):
        stat = part.lstat()
        if part.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 0x400:
            raise ValueError('AutoLoop does not follow linked or redirected files')
    return path.resolve(strict=True)


def _hash(path, cancel=None):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            if cancel and cancel.is_set():
                raise ValueError('AutoLoop cancelled')
            digest.update(block)
    return digest.hexdigest()


def _integer(body, key, default, lower, upper):
    value = body.get(key, default)
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f'{key} must be between {lower} and {upper}')
    return value


class AutoLoopManager:
    def __init__(self, root, resources, state, *, core=None, runtime_factory=None):
        self.root, self.resources, self.state = Path(root).resolve(), Path(resources).resolve(), state
        self.folder = managed_path(self.root, 'learning', 'autoloop', 'runs')
        self.folder.mkdir(parents=True, exist_ok=True)
        if core is None:
            import autoloop_vendor as core
        if runtime_factory is None:
            from hf_runtime import RuntimeManager
            runtime_factory = RuntimeManager
        self.core, self.runtime_factory = core, runtime_factory
        self.lock = threading.RLock()
        self.runs, self.active, self.worker = {}, None, None
        self.cancellation = threading.Event()
        self.validation_runtime = None
        for folder in sorted(self.folder.iterdir(), key=lambda p:p.name)[-250:]:
            if not RUN_ID.fullmatch(folder.name) or folder.is_symlink():
                continue
            try:
                saved = _read(folder/'state.json', 128 * 1024)
                if not isinstance(saved, dict) or saved.get('id') != folder.name:
                    continue
                if saved.get('status') not in TERMINAL:
                    saved.update(status='failed',stage='interrupted',error='AutoLoop was interrupted when OpenCore stopped; the source was retained.')
                    atomic_json(folder/'state.json',saved)
                self.runs[folder.name] = saved
            except (OSError, ValueError, TypeError):
                continue

    def _runtime_path(self):
        for file in (self.root/'runtime/llama-server.exe', self.resources/'opencore/runtime/llama-server.exe'):
            if file.is_file():
                return _plain_file(file)
        raise ValueError('The bundled GGUF runtime is missing. Repair the OpenCore installation before using AutoLoop.')

    def models(self):
        """Known installed paths only: never scan drives or hash model contents."""
        items, seen = [], set()
        def add(path, label, identity, license='', source=''):
            try:
                file = _plain_file(path, '.gguf')
                key = os.path.normcase(str(file))
                if key in seen or file.name.lower().startswith('mmproj'):
                    return
                seen.add(key)
                items.append({'id':identity,'label':label,'modelPath':str(file),'bytes':file.stat().st_size,
                              'license':license,'sourceUrl':source})
            except (OSError, ValueError):
                pass
        source = self.resources/'runtime-setup/hf-extension/native-model-bindings.json'
        if source.is_file():
            data = _read(source,64*1024*1024)
            artifacts = {a['id']:a for a in data.get('artifacts',[]) if isinstance(a,dict) and 'id' in a}
            for model in data.get('models',[]):
                if not model.get('supportsTextGeneration') or model.get('modeAliasOf'):
                    continue
                paths = [model['runtimeModelPath']] if model.get('runtimeModelPath') else []
                paths += [artifacts[key]['path'] for key in model.get('weightArtifacts',model.get('artifacts',[])) if key in artifacts and artifacts[key].get('path','').lower().endswith('.gguf')]
                for path in paths:
                    target = (self.root/path).resolve()
                    if target.is_relative_to(self.root):
                        add(target, model.get('label',model['id'])+' · '+str(model.get('precision','GGUF')),model['id'],model.get('license',''),model.get('sourceUrl',''))
        with self.state.lock:
            entries = list(self.state.entries.values())
        for entry in entries:
            if entry.get('format') != 'gguf' or entry.get('category','text') not in ('text','brain'):
                continue
            folder = self.root/'models/hub'/entry['id']
            if not (folder/'install-receipt.json').is_file():
                continue
            for pin in entry.get('files',[]):
                if pin.get('role')=='model' and str(pin.get('filename','')).lower().endswith('.gguf'):
                    target = (folder/pin['filename']).resolve()
                    if target.is_relative_to(folder):
                        add(target,entry.get('familyLabel',entry['repository'])+' · '+entry.get('quantization','GGUF'),entry['id'],entry.get('license',''),entry.get('sourceUrl',''))
        return {'models':items,'runtimeAvailable':any(p.is_file() for p in (self.root/'runtime/llama-server.exe',self.resources/'opencore/runtime/llama-server.exe'))}

    def inspect(self, body):
        file = _plain_file(body.get('modelPath'),'.gguf')
        result = dict(self.core.inspect_model(file))
        result['modelPath'] = str(file)
        if result.get('requiresVisionCompatibility'):
            result.update(supported=False,reason='This vision checkpoint needs a separately verified companion after layer modification. AutoLoop leaves it unchanged.')
        if result.get('supported') and (type(result.get('contextTokens')) is not int or result['contextTokens'] < 8192):
            result.update(supported=False,reason='This GGUF does not declare the required 8,192-token context length. AutoLoop cannot promote it as an OpenCore chat model.')
        return result

    def status(self):
        with self.lock:
            return {'activeRunId':self.active,'runs':copy.deepcopy(sorted(self.runs.values(),key=lambda x:x.get('createdAt',0),reverse=True))}

    def _update(self, identity, **values):
        with self.lock:
            self.runs[identity].update(values,updatedAt=time.time())
            atomic_json(self.folder/identity/'state.json',self.runs[identity])

    def _save_report(self, identity, report):
        # Windows readers must finish before atomic replacement of this file.
        with self.lock:
            atomic_json(self.folder/identity/'report.json',report)

    def _admit(self):
        progress = self.state.progress or {}
        if progress.get('status') in ('running','pausing'):
            raise ValueError('Let model installation finish or pause it before starting AutoLoop')
        for attr in ('_generation','_preparation'):
            lock = getattr(self.state.runtime,attr,None)
            if lock and lock.locked():
                raise ValueError('Wait for the active model response or preparation to finish')

    def start(self, body):
        if not isinstance(body,dict) or set(body)-{'modelPath','benchmarkPath','maxVariants','threads','maxMinutes','maxDiskGiB','experimentalDamping','label'}:
            raise ValueError('AutoLoop accepts model and benchmark files, not executable or output-folder overrides')
        variants = _integer(body,'maxVariants',3,1,8)
        threads = _integer(body,'threads',2,1,8)
        minutes = _integer(body,'maxMinutes',60,1,1440)
        disk = _integer(body,'maxDiskGiB',128,1,2048)*1024**3
        damping = body.get('experimentalDamping',False)
        if type(damping) is not bool:
            raise ValueError('experimentalDamping must be a checkbox value')
        model = _plain_file(body.get('modelPath'),'.gguf')
        capabilities = dict(self.core.inspect_model(model))
        if not capabilities.get('supported'):
            raise ValueError(capabilities.get('reason') or 'This architecture is not supported for training-free layer surgery')
        if capabilities.get('requiresVisionCompatibility'):
            raise ValueError('This vision checkpoint needs verified companion compatibility before layer modification')
        context = capabilities.get('contextTokens')
        if context is not None and (type(context) is not int or context < 8192):
            raise ValueError('This model has less than 8,192 context tokens required by OpenCore chat; it cannot be promoted as a chat model.')
        if context is None:
            raise ValueError('The checkpoint does not declare a verified context length. AutoLoop cannot safely register its chat runtime.')
        benchmark = str(_plain_file(body['benchmarkPath'],'.json')) if body.get('benchmarkPath') else ''
        if benchmark and len(self.core.load_questions(benchmark))<48:
            raise ValueError('Use at least 48 objective questions so the independent confirmation set is large enough')
        runtime = self._runtime_path()
        label = str(body.get('label') or model.stem)[:120].strip() or model.stem
        estimated = model.stat().st_size*3 + 512*1024**2
        if estimated > disk or estimated+2*1024**3 > shutil.disk_usage(self.root).free:
            raise ValueError('There is not enough reserved disk space for candidate checkpoints. Increase the disk budget or choose a smaller model.')
        with self.lock:
            if self.active:
                raise ValueError('An AutoLoop run is already active')
            self._admit()
            identity = uuid.uuid4().hex
            folder = self.folder/identity; folder.mkdir(exist_ok=False)
            self.cancellation = threading.Event()
            now=time.time()
            self.runs[identity]={'id':identity,'name':label,'status':'running','stage':'queued','createdAt':now,'updatedAt':now,
                                 'modelPath':str(model),'capabilities':capabilities,'runtimeReady':False,
                                 'settings':{'maxVariants':variants,'threads':threads,'maxMinutes':minutes,'maxDiskGiB':disk//1024**3,'experimentalDamping':damping},
                                 'qualityScope':'Only the selected benchmark; not a promise of general model improvement.'}
            atomic_json(folder/'state.json',self.runs[identity])
            self.active=identity
            options={'model':str(model),'output_dir':str(folder),'llama_server':str(runtime),'benchmark':benchmark,
                     'max_variants':variants,'context':min(4096,context),'threads':threads,'gpu_layers':0,
                     'startup_timeout':min(240,minutes*60),'request_timeout':min(240,minutes*60),'experimental_damping':damping}
            self.worker=threading.Thread(target=self._run,args=(identity,options),daemon=True,name='OpenCore-AutoLoop')
            self.worker.start()
            return copy.deepcopy(self.runs[identity])

    def _run(self, identity, options):
        cancel = self.cancellation
        report = None
        timer = threading.Timer(self.runs[identity]['settings']['maxMinutes']*60,cancel.set)
        timer.daemon=True;timer.start()
        last_write=[0.0]
        def progress(value):
            if not isinstance(value,dict):return
            public={key:value[key] for key in ('stage','event','candidate','split','completed','total','correct','bytesDone','bytesTotal','message') if key in value}
            public['status']='cancelling' if cancel.is_set() else 'running'
            if time.monotonic()-last_write[0]>.4 or value.get('event')!='copy-progress':
                self._update(identity,**public);last_write[0]=time.monotonic()
        try:
            self._update(identity,stage='hashing-source')
            source_hash=_hash(options['model'],cancel)
            runtime_hash=_hash(options['llama_server'],cancel)
            options['runtime_sha256']=runtime_hash
            report=self.core.Engine(self.core.Settings(**options),progress=progress,cancel=cancel).run()
            if not isinstance(report,dict):raise ValueError('AutoLoop returned no verifiable report')
            report['optimizationStatus']=report.get('status')
            if cancel.is_set():
                report.update(status='cancelled',acceptedArtifact=None,selected=None)
            self._save_report(identity,report)
            if report.get('status')=='accepted':
                self._promote(identity,report,options,source_hash,runtime_hash,cancel)
            elif report.get('status') in ('rejected','cancelled','baseline_complete','failed'):
                self._update(identity,status=report['status'],stage=report['status'],reason=report.get('reason',''),error=report.get('error',''),runtimeReady=False)
            else:
                raise ValueError('AutoLoop returned an unknown completion state')
        except Exception as error:
            status='cancelled' if cancel.is_set() else 'failed'
            if isinstance(report,dict):
                if report.get('acceptedArtifact'):report['confirmedCandidate']=report['acceptedArtifact']
                report.update(status=status,acceptedArtifact=None,selected=None,promotion={'status':status,'error':str(error)[:4000]})
                self._save_report(identity,report)
            self._update(identity,status=status,stage=status,error=str(error)[:4000],runtimeReady=False)
        finally:
            timer.cancel()
            # Failed/cancelled/rejected exports are disposable only inside this
            # unique run directory; source and pre-existing files are excluded.
            if self.runs[identity]['status']!='accepted':
                for file in (self.folder/identity).glob('*.gguf'):
                    if file.is_file() and not file.is_symlink():file.unlink()
            with self.lock:
                if self.active==identity:self.active=None

    def _promote(self, identity, report, options, source_hash, runtime_hash, cancel):
        artifact=report.get('acceptedArtifact') or {}
        winner=_plain_file(artifact.get('path'),'.gguf')
        folder=(self.folder/identity).resolve()
        if not winner.is_relative_to(folder) or winner==Path(options['model']).resolve():
            raise ValueError('The accepted artifact is not an owned candidate')
        if artifact.get('inferenceVerified') is not True:
            raise ValueError('An injected or unverified test result cannot register a model')
        if artifact.get('sourceSha256')!=source_hash or artifact.get('runtimeSha256')!=runtime_hash:
            raise ValueError('The accepted report does not match this source and runtime')
        actual=_hash(winner,cancel)
        if actual!=artifact.get('sha256') or winner.stat().st_size!=artifact.get('bytes'):
            raise ValueError('The accepted candidate changed after confirmation')
        config_file=_plain_file(artifact.get('configPath'),'.json')
        benchmark_file=_plain_file((report.get('benchmark') or {}).get('manifestPath'),'.json')
        if not config_file.is_relative_to(folder) or not benchmark_file.is_relative_to(folder):
            raise ValueError('The quality report references an unowned configuration or benchmark')
        if _hash(config_file,cancel)!=artifact.get('configSha256') or _hash(benchmark_file,cancel)!=artifact.get('benchmarkSha256'):
            raise ValueError('The accepted configuration or frozen benchmark changed after confirmation')
        config=_read(config_file,1024*1024)
        if any(config.get(key)!=expected for key,expected in (('acceptedSha256',actual),('sourceSha256',source_hash),('runtimeSha256',runtime_hash),('benchmarkSha256',artifact.get('benchmarkSha256')))):
            raise ValueError('The accepted configuration does not match its verified identities')
        if _hash(options['model'],cancel)!=source_hash or _hash(options['llama_server'],cancel)!=runtime_hash:
            raise ValueError('The original checkpoint or runtime changed during optimization')
        self._admit()
        model_id='hf-'+hashlib.sha256(('autoloop:'+identity+':'+actual).encode()).hexdigest()[:24]
        destination=managed_path(self.root,'models','hub',model_id)
        destination.mkdir(parents=True,exist_ok=False)
        target=destination/'model.gguf'
        worker=None;published=False
        try:
            os.replace(winner,target)
            repository='OpenCore-Local/AutoLoop-'+identity[:12]
            pin={'filename':'model.gguf','bytes':target.stat().st_size,'role':'model','repository':repository,'revision':actual[:40],'sha256':actual}
            capabilities=self.runs[identity]['capabilities'];context=capabilities['contextTokens']
            entry={'id':model_id,'repository':repository,'revision':actual[:40],'variantKey':'local-autoloop:'+actual,
                   'name':self.runs[identity]['name']+' AutoLoop','familyLabel':self.runs[identity]['name']+' · AutoLoop','familyId':'autoloop-'+identity,
                   'format':'gguf','quantization':'Original GGUF precision',
                   'bytes':pin['bytes'],'files':[pin],'installable':True,'localOnly':True,'origin':'autoloop','category':'text',
                   'runtime':{'adapter':'gguf'},'contextTokens':min(32768,context),'publishedContextTokens':context,'operatingMode':'echo',
                   'supportsTextGeneration':True,'sourceUrl':'','license':'Original model terms apply',
                   'description':'Local training-free variant; improvement confirmed only on the selected benchmark.',
                   'builtInDescription':'Locally generated training-free variant. Its recorded gain applies to the selected benchmark; original checkpoint retained.',
                   'autoLoop':{'runId':identity,'sourcePath':options['model'],'sourceSha256':source_hash,'contentRevision':actual,
                               'benchmarkSha256':artifact.get('benchmarkSha256'),'qualityScope':artifact.get('qualityScope','Selected benchmark only')},
                   'localDirectory':str(destination),'receiptPath':str(destination/'install-receipt.json')}
            from model_modes import apply_operating_modes
            apply_operating_modes([entry])
            validate_entry(entry)
            receipt={'schema':1,'id':model_id,'repository':repository,'revision':entry['revision'],'bytes':pin['bytes'],
                     'localDirectory':str(destination),'receiptPath':entry['receiptPath'],'files':[{**pin,'localPath':str(target),'actualSha256':actual}],
                     'origin':'autoloop','provenance':entry['autoLoop']}
            atomic_json(destination/'install-receipt.json',receipt)
            self._update(identity,stage='verifying-app-runtime')
            worker=self.runtime_factory(self.root,self.resources)
            with self.lock:self.validation_runtime=worker
            worker.on_progress=lambda **values:self._update(identity,stage='verifying-app-runtime',detail=values.get('detail',values.get('stage','')))
            if cancel.is_set():raise ValueError('AutoLoop cancelled before app runtime verification')
            proof=worker.prepare(entry)
            if cancel.is_set():raise ValueError('AutoLoop cancelled')
            if not isinstance(proof,dict) or proof.get('runtimeReady') is not True or proof.get('inferenceVerified') is not True or proof.get('revision')!=entry['revision'] or proof.get('operatingMode')!='echo' or proof.get('echoMode')!='main-context-v1':
                raise ValueError('The normal OpenCore runtime did not verify this candidate')
            proof['receiptRevision']=entry['revision']
            entry.update(runtimePreparation=proof,runtimePreparations={'echo':proof})
            # Publication and acknowledged cancellation share one completion
            # boundary. A later Cancel returns the terminal result unchanged.
            with self.lock, self.state.lock:
                if cancel.is_set():raise ValueError('AutoLoop cancelled before publication')
                self.state.entries[model_id]=entry
                try:
                    self.state.persist_catalog()
                    if cancel.is_set():raise ValueError('AutoLoop cancelled during publication')
                    artifact['path']=str(target);report['selected']=str(target);report['managedModelId']=model_id
                    self._save_report(identity,report)
                    self._update(identity,status='accepted',stage='accepted',modelId=model_id,runtimeReady=True,
                                 artifactSha256=actual,qualityScope=artifact.get('qualityScope','Selected benchmark only'),confirmation=report.get('confirmation',{}).get('comparison',{}))
                    published=True
                except Exception:
                    self.state.entries.pop(model_id,None)
                    self.state.persist_catalog()
                    raise
        finally:
            if worker is not None:worker.stop()
            with self.lock:self.validation_runtime=None
            if not published:
                for file in (target,destination/'install-receipt.json'):
                    if file.exists() and not file.is_symlink():file.unlink()
                try:destination.rmdir()
                except OSError:pass

    def cancel(self, identity):
        with self.lock:
            if identity!=self.active:raise ValueError('That AutoLoop run is not active')
            if self.runs[identity].get('status') in TERMINAL:
                return {'cancelling':False,'id':identity,'status':self.runs[identity]['status']}
            self.cancellation.set()
            self._update(identity,status='cancelling',stage='cancelling')
            worker=self.validation_runtime
        if worker is not None:worker.stop()
        return {'cancelling':True,'id':identity}

    def report(self, identity):
        with self.lock:
            if not isinstance(identity,str) or not RUN_ID.fullmatch(identity) or identity not in self.runs:
                raise ValueError('Select an existing AutoLoop run')
            file=self.folder/identity/'report.json'
            if not file.is_file():return {'status':self.runs[identity]['status'],'message':'The full report is written when the run finishes.'}
            return _read(file,32*1024*1024)

    def close(self):
        self.cancellation.set()
        worker=self.validation_runtime
        if worker is not None:worker.stop()
        if self.worker and self.worker is not threading.current_thread():self.worker.join(timeout=2)
