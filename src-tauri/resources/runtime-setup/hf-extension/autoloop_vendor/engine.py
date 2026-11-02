"""Training-free physical block search with one independently confirmed winner."""
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
import math
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import uuid
from .benchmarks import load_questions,split_questions,prompt_for,grade
from .errors import Cancelled,checkpoint
from .gguf import GGUFModel,GGUFError,repeat_variants,publish_no_replace
from .identity import canonical,digest,file_identity,atomic_json
from .runner import LocalServer,RunnerError,find_server


def utc_now():return datetime.now(timezone.utc).isoformat()


def paired_improvement_p(wins,losses):
    """Exact one-sided sign/McNemar test under discordant-pair probability 1/2."""
    if type(wins) is not int or type(losses) is not int or min(wins,losses)<0:raise ValueError('Invalid paired counts')
    n=wins+losses
    return sum(math.comb(n,i) for i in range(wins,n+1))/(2**n) if n else 1.0


@dataclass
class Settings:
    model:str
    output_dir:str
    llama_server:str
    benchmark:str=''
    max_variants:int=3
    context:int=4096
    gpu_layers:int=0
    threads:int=2
    max_tokens:int=2048
    startup_timeout:int=240
    request_timeout:int=240
    seed:int=117
    experimental_damping:bool=False
    runtime_sha256:str=''
    baseline_only:bool=False
    multimodal:bool=False
    vision_compatibility:dict|None=None
    companion:str=''
    minimum_dev_net:int=2
    minimum_holdout:int=16
    max_holdout_regressions:int=0


class OwnedWorkspace:
    def __init__(self,parent):
        self.path=Path(tempfile.mkdtemp(prefix='.autoloop-work-',dir=parent)).resolve()
        self.created={}
    def track(self,path):
        path=Path(path).resolve()
        if path.parent!=self.path:raise ValueError('Candidate is outside this run workspace')
        info=path.stat();self.created[path]=(info.st_dev,info.st_ino)
    def discard(self,path):
        path=Path(path).resolve();identity=self.created.pop(path,None)
        if identity is not None and path.is_file():
            info=path.stat()
            if path.parent==self.path and (info.st_dev,info.st_ino)==identity:path.unlink()
    def close(self):
        for path in list(self.created):self.discard(path)
        try:self.path.rmdir()
        except OSError:pass # Never recursively delete an untracked file.


class Engine:
    def __init__(self,settings:Settings,progress=None,cancel=None,server_factory=LocalServer):
        self.settings=settings;self.progress=progress or (lambda event:None)
        self.cancel=cancel or threading.Event();self.server_factory=server_factory
        self.sessions=[];self.effective_context=settings.context
        self.answer_log=None;self.answer_count=0;self.answer_digest=hashlib.sha256();self.requires_vision=False
    def emit(self,event,**fields):
        self.progress({'schema':1,'event':event,'timestamp':utc_now(),**fields})
    def check(self):checkpoint(self.cancel)
    @staticmethod
    def comparison(base,candidate):
        if len(base)!=len(candidate):raise ValueError('Evaluation question counts differ')
        if any(a['id']!=b['id'] for a,b in zip(base,candidate)):raise ValueError('Evaluation question identities differ')
        wins=sum(not a['correct'] and b['correct'] for a,b in zip(base,candidate))
        losses=sum(a['correct'] and not b['correct'] for a,b in zip(base,candidate))
        return {'wins':wins,'losses':losses,'net':wins-losses,'baseCorrect':sum(a['correct'] for a in base),
                'candidateCorrect':sum(b['correct'] for b in candidate),'total':len(base),'oneSidedP':paired_improvement_p(wins,losses)}
    def evaluate(self,model,questions,name,kind,output):
        self.check();s=self.settings
        self.emit('runtime-start',stage='loading',candidate=name,split=kind)
        server=self.server_factory(s.llama_server,model,output/'logs',threads=s.threads,gpu_layers=s.gpu_layers,
            context=self.effective_context,startup_timeout=s.startup_timeout,request_timeout=s.request_timeout,cancel=self.cancel,companion=s.companion or None)
        results=[]
        with server:
            self.sessions.append({'candidate':name,'split':kind,'proof':getattr(server,'runtime_proof',None)})
            for index,q in enumerate(questions):
                self.check()
                response=server.ask(prompt_for(q),max_tokens=s.max_tokens)
                if not isinstance(response,str) or not response.strip():raise RunnerError('The model returned no final answer text')
                results.append({'id':digest(asdict(q)),'prompt':q.prompt,'expected':q.answer,'kind':q.kind,
                                'response':response,'correct':bool(grade(q,response))})
                raw=canonical({'candidate':name,'split':kind,**results[-1]})+b'\n'
                try:
                    with self.answer_log.open('ab') as journal:
                        journal.write(raw);journal.flush();os.fsync(journal.fileno())
                except OSError as error:raise RuntimeError('Could not preserve the raw evaluation answer journal') from error
                self.answer_digest.update(raw);self.answer_count+=1
                self.emit('question',stage='evaluating',candidate=name,split=kind,completed=index+1,total=len(questions),correct=sum(x['correct'] for x in results))
        return results
    def _vision_allowed(self,source,runtime):
        if not (self.settings.multimodal or self.requires_vision):return
        proof=self.settings.vision_compatibility or {}
        if (proof.get('validated') is not True or proof.get('sourceSha256')!=source['sha256'] or
            proof.get('runtimeSha256')!=runtime['sha256'] or not isinstance(proof.get('companionSha256'),list) or
            not proof['companionSha256'] or any(not isinstance(value,str) or len(value)!=64 for value in proof['companionSha256'])):
            raise GGUFError('Multimodal surgery needs manager-proven vision package compatibility. Text-only scores do not validate vision or video quality.')
        if not self.settings.companion or file_identity(self.settings.companion,self.cancel)['sha256'] not in proof['companionSha256']:
            raise GGUFError('The selected vision companion differs from the manager compatibility proof')
    def run(self):
        s=self.settings
        if type(s.max_variants) is not int or not 1<=s.max_variants<=8:raise ValueError('max_variants must be 1 to 8')
        if type(s.minimum_dev_net) is not int or s.minimum_dev_net<2 or type(s.minimum_holdout) is not int or s.minimum_holdout<16:
            raise ValueError('Acceptance requires at least two net development wins and sixteen holdout questions')
        if type(s.max_holdout_regressions) is not int or s.max_holdout_regressions<0:raise ValueError('Invalid holdout regression limit')
        if not 1<=s.max_tokens<=32768 or not 128<=s.context<=1048576:raise ValueError('Invalid bounded generation configuration')
        output=Path(s.output_dir).resolve();output.mkdir(parents=True,exist_ok=True)
        report_path=output/'autoloop-report.json'
        with report_path.open('xb'):pass # Reserve this receipt; pre-existing files are untouched.
        report={'schema':1,'status':'running','startedAt':utc_now(),'settings':asdict(s),'source':{},'runtime':{},
                'capabilities':None,'benchmark':None,'baseline':None,'variants':[],'confirmation':None,'acceptedArtifact':None,
                'selected':None,'error':None,'rawResponses':None,'scope':'Objective exact/number text prompts only; not HumanEval, human judgment, or universal intelligence.',
                'limitations':['Physical block copies increase file size and executed depth.','Residual projection damping is experimental and is not the paper Runge–Kutta/cache method.',
                               'Multimodal answer quality and out-of-distribution gains are not established by this text benchmark.'],
                'acceptance':{'minimumDevelopmentNet':s.minimum_dev_net,'minimumHoldoutQuestions':s.minimum_holdout,
                              'maximumHoldoutRegressions':s.max_holdout_regressions,'oneSidedPairedPMaximum':.05,'confirmationCandidates':1}}
        workspace=None;pending=None;exports=[];source=None
        atomic_json(report_path,report)
        try:
            self.check();source=GGUFModel(s.model);report['capabilities']=source.describe()
            self.requires_vision=source.has_vision_metadata
            self.effective_context=min(s.context,source.context_tokens) if source.context_tokens else s.context
            report['evaluation']={'contextTokens':self.effective_context,'requestedContextTokens':s.context,'seed':1234,
                                  'temperature':0,'maxTokens':s.max_tokens}
            self.emit('hashing-source',stage='hashing',bytes=source.file_size)
            report['source']={'path':str(source.path),'before':file_identity(source.path,self.cancel)}
            runtime_path=find_server(s.llama_server)
            report['runtime']={'path':runtime_path,'before':file_identity(runtime_path,self.cancel)}
            if s.runtime_sha256 and report['runtime']['before']['sha256']!=s.runtime_sha256:
                raise RunnerError('The pinned runtime SHA-256 does not match the selected executable')
            if not s.baseline_only:
                source.validate_for_surgery();self._vision_allowed(report['source']['before'],report['runtime']['before'])
            questions=load_questions(s.benchmark or None)
            development,holdout=split_questions(questions,seed=s.seed)
            manifest={'schema':1,'kind':'exact-number-objective-text','seed':s.seed,'promptSuffix':'Reply with ONLY the final answer. No explanation or extra text.',
                      'development':[asdict(q) for q in development],'holdout':[asdict(q) for q in holdout]}
            benchmark_path=output/'benchmark-manifest.json'
            with benchmark_path.open('xb'):pass
            atomic_json(benchmark_path,manifest)
            report['benchmark']={'schema':1,'sha256':file_identity(benchmark_path)['sha256'],'contentSha256':digest(manifest),'manifestPath':str(benchmark_path),'questionCount':len(questions),
                'developmentCount':len(development),'holdoutCount':len(holdout),'source':'owner benchmark' if s.benchmark else 'bundled 48-question demonstration suite',
                'scope':'Exact/number final-answer scoring; no coding execution, human evaluation, image or video questions.'}
            self.answer_log=output/'answers.jsonl'
            with self.answer_log.open('xb'):pass
            baseline_dev=self.evaluate(source.path,development,'baseline','development',output)
            baseline_hold=self.evaluate(source.path,holdout,'baseline','holdout',output)
            report['baseline']={'development':baseline_dev,'holdout':baseline_hold};atomic_json(report_path,report)
            if s.baseline_only:
                report['status']='baseline_complete';report['decision']='Text-only baseline measured; no weights were modified.'
            else:
                workspace=OwnedWorkspace(output);best=None
                for index,(name,mapping,damped) in enumerate(repeat_variants(source.block_count,s.max_variants,s.experimental_damping)):
                    self.check();path=workspace.path/(uuid.uuid4().hex+'.gguf')
                    record={'name':name,'mapping':mapping,'experimentalDamping':damped,'status':'building'}
                    report['variants'].append(record);self.emit('candidate-start',stage='building',candidate=name,completed=index,total=s.max_variants)
                    try:
                        source.build(path,mapping,lambda message:self.emit('copy-progress',stage='building',candidate=name,message=message),damped=damped,cancel=self.cancel)
                        workspace.track(path)
                        identity=file_identity(path,self.cancel)
                        answers=self.evaluate(path,development,name,'development',output)
                        comparison=self.comparison(baseline_dev,answers)
                        record.update(status='evaluated',development=comparison,answers=answers,artifact=identity)
                        self.emit('candidate-evaluated',stage='selection',candidate=name,comparison=comparison)
                        rank=(comparison['net'],-comparison['losses'],comparison['wins'])
                        if comparison['net']>=s.minimum_dev_net and (best is None or rank>best['rank']):
                            best={'name':name,'mapping':mapping,'damped':damped,'identity':identity,'rank':rank}
                    except Cancelled:raise
                    except (GGUFError,RunnerError,OSError,ValueError) as error:
                        record.update(status='rejected',error=str(error)[-4096:])
                    finally:
                        workspace.discard(path);atomic_json(report_path,report)
                if best is None:
                    report.update(status='rejected',decision='No candidate established sufficient development improvement; original retained.')
                else:
                    self.check();path=workspace.path/(uuid.uuid4().hex+'.gguf')
                    source.build(path,best['mapping'],lambda message:self.emit('copy-progress',stage='confirming',candidate=best['name'],message=message),damped=best['damped'],cancel=self.cancel)
                    workspace.track(path)
                    if file_identity(path,self.cancel)['sha256']!=best['identity']['sha256']:
                        raise GGUFError('The confirmation artifact differs from the development-selected artifact')
                    hold=self.evaluate(path,holdout,best['name'],'confirmation',output)
                    compared=self.comparison(baseline_hold,hold)
                    gates={'enoughQuestions':len(hold)>=s.minimum_holdout,'strictImprovement':compared['net']>0,
                           'regressionsAllowed':compared['losses']<=s.max_holdout_regressions,'pairedSignificance':compared['oneSidedP']<=.05}
                    report['confirmation']={'name':best['name'],'evaluatedCandidates':1,'comparison':compared,'gates':gates,'answers':hold}
                    if all(gates.values()):
                        pending=(path,best);report['status']='confirmed'
                    else:report.update(status='rejected',decision='The development winner failed independent holdout gates; original retained.')
            self.check()
        except Cancelled as error:report.update(status='cancelled',error=str(error))
        except Exception as error:report.update(status='failed',error=str(error)[-4096:])
        finally:
            # Source verification runs to completion even after cancellation; it never writes or restores the source.
            for key,path in (('source',source.path if source else None),('runtime',report['runtime'].get('path'))):
                if report[key].get('before'):
                    try:
                        report[key]['after']=file_identity(path)
                        if report[key]['before']['sha256']!=report[key]['after']['sha256']:
                            raise ValueError(key+' changed during the run; no artifact is accepted')
                    except Exception as error:report.update(status='failed',error=str(error)[-4096:])
            if report['benchmark']:
                try:
                    report['benchmark']['after']=file_identity(report['benchmark']['manifestPath'])
                    if report['benchmark']['after']['sha256']!=report['benchmark']['sha256']:
                        raise ValueError('The frozen benchmark manifest changed during evaluation; no artifact is accepted')
                except Exception as error:report.update(status='failed',error=str(error)[-4096:])
            if self.answer_log is not None:
                try:
                    report['rawResponses']={'path':str(self.answer_log),'count':self.answer_count,**file_identity(self.answer_log)}
                    if report['rawResponses']['sha256']!=self.answer_digest.hexdigest():
                        raise ValueError('The raw answer journal changed during evaluation; no artifact is accepted')
                except Exception as error:report.update(status='failed',error=str(error)[-4096:])
            if self.cancel.is_set() and report['status'] not in ('failed','cancelled'):report.update(status='cancelled',error='AutoLoop cancelled before export.')
            try:
                if pending is not None and report['status']=='confirmed':
                    path,best=pending
                    target=output/('accepted-'+uuid.uuid4().hex+'.gguf')
                    seal=file_identity(path,self.cancel)
                    self.check()
                    publish_no_replace(path,target);workspace.created.pop(path,None);exports.append(target)
                    self.check()
                    config={'schema':1,'method':'physical-block-repetition','training':False,'weightSharing':False,
                            'mapping':best['mapping'],'experimentalDamping':best['damped'],'architecture':source.architecture,
                            'sourceSha256':report['source']['before']['sha256'],'runtimeSha256':report['runtime']['before']['sha256'],
                            'benchmarkSha256':report['benchmark']['sha256'],'acceptedSha256':seal['sha256'],
                            'qualityScope':report['scope'],'confirmation':report['confirmation']['comparison']}
                    config_path=target.with_suffix('.config.json')
                    with config_path.open('xb'):pass
                    exports.append(config_path);atomic_json(config_path,config)
                    self.check()
                    verified=bool(self.sessions) and all(item.get('proof') and item['proof'].get('kind')=='local-child-server' and
                        item['proof'].get('authenticated') is True and item['proof'].get('executableSha256')==report['runtime']['before']['sha256'] for item in self.sessions)
                    report.update(status='accepted',selected=str(target),decision='One development-selected candidate passed the independent objective holdout gates.')
                    report['acceptedArtifact']={'path':str(target),**seal,'architecture':source.architecture,
                        'configPath':str(config_path),'configSha256':file_identity(config_path)['sha256'],
                        'sourceSha256':config['sourceSha256'],'runtimeSha256':config['runtimeSha256'],'benchmarkSha256':config['benchmarkSha256'],
                        'inferenceVerified':verified,'qualityScope':report['scope']}
            except Exception as error:
                for path in exports:path.unlink(missing_ok=True)
                report.update(status='cancelled' if isinstance(error,Cancelled) else 'failed',error=str(error)[-4096:],acceptedArtifact=None,selected=None)
            finally:
                if workspace is not None:workspace.close()
            report['runtimeSessions']=self.sessions;report['finishedAt']=utc_now()
            try:atomic_json(report_path,report)
            except Exception:
                for path in exports:path.unlink(missing_ok=True)
                raise
        return report
