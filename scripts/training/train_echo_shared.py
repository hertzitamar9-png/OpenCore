"""Complete-answer SFT of existing shared ECHO experts, followed by native tests.

The selected source is immutable. A candidate is separate, keeps shipped
precision, and is never promoted automatically. Failed gates retain evidence.
"""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import time
import traceback
import torch
from echo_weight_adapter import MODEL_SHA,load_training_view
from echo_shared_data import training_prompt,read_json
from echo_shared_training import complete_loss,candidate_status
from echo_rl import response_logprobs
from native_echo_backend import NativeBackend,request
from rl_verifier import CodingVerifier
from train_echo_pilot import digest,export_candidate,parity,save

SOURCE_SHA='4551c5333bb6287f0222e15a4d1e3a969df04cb7a69833125f5b3aa80239b91a'
TRAINABLE=4258704


def stripped_function(text):
    text=text.strip()
    if text.startswith('```python\n') and text.endswith('\n```'): return text[10:-4].strip()
    if text.startswith('```\n') and text.endswith('\n```'): return text[4:-4].strip()
    return text


def native_checks(args,rows,source,label,profile):
    folder=args.folder;path=folder/(label+'-native.json');saved=[];source_hash=digest(source)
    if path.exists():
        prior=read_json(path)
        if prior['source_sha256']!=source_hash: raise ValueError('Saved native capture checkpoint changed')
        saved=prior['rows']
    with CodingVerifier(folder) as verifier, NativeBackend(args.home,folder/label,source,port=8892,
            kv_offload=profile['kv_offload'],threads=profile['threads']) as backend:
        for index,row in enumerate(rows):
            if index<len(saved):
                capture=saved[index]
                if capture['id']!=row['id']: raise ValueError('Saved native question order changed')
            else:
                output=backend.chat([{'role':'user','content':training_prompt(row['prompt'])}],max_tokens=256)
                choice=output['choices'][0]
                capture={'id':row['id'],'response':output,'code':stripped_function(choice['message']['content'] or ''),
                    'truncated':choice['finish_reason']=='length'}
                saved.append(capture)
            capture['reward']=verifier.verify(row,capture['code'],f'{label}-heldout-{index:03d}',capture['truncated'])
            save(folder,path.name,{'source_sha256':source_hash,'rows':saved,'completed':index+1,'total':len(rows)})
            save(folder,'training-progress.json',{'phase':label+'-native-checks','completed':index+1,'total':len(rows)})
        # The same sustained coding speed workload qualifies both checkpoints.
        output=backend.chat([{'role':'user','content':'Return one Python function that merges adjacent equal items into (item, count) runs. Include comments explaining the loop invariant.'}],max_tokens=192)
        speed=(output.get('timings') or {}).get('predicted_per_second')
        sequence=None
        if label=='baseline':
            prompt=rows[0]['prompt_ids']
            raw=request(backend.base,'/completion',{'prompt':prompt,'n_predict':32,'n_probs':64,'temperature':-1,
                'post_sampling_probs':False,'cache_prompt':False,'seed':42})
            probs=raw.get('probs') or raw.get('completion_probabilities')
            if not probs or any('id' not in p or 'logprob' not in p for p in probs):
                raise ValueError('Native continuation probabilities unavailable')
            sequence={'prompt':prompt,'response':[p['id'] for p in probs],
                'logprobs':[p['logprob'] for p in probs],'raw':raw}
            save(folder,'native-sequence-probe.json',sequence)
    result={'source_sha256':source_hash,'rows':saved,'passed':sum(r['reward']['all_passed'] for r in saved),
        'mean_case_reward':sum(r['reward']['reward'] for r in saved)/len(saved),'examples':len(saved),
        'speed':speed,'speed_response':output,'runtime':{'kv_offload':profile['kv_offload'],'threads':profile['threads']}}
    save(folder,path.name,result)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--research-root',type=Path,required=True)
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--home',type=Path,default=Path.home()/'OpenCore')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args();folder=args.folder;folder.mkdir(parents=True,exist_ok=True)
    import msvcrt
    lock=(folder/'shared-training.lock').open('a+b');lock.seek(0);lock.write(b'1');lock.flush();lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    torch.set_num_threads(2);torch.manual_seed(42)
    save(folder,'shared-process.json',{'pid':os.getpid(),'started':time.time(),'source':str(args.source)})
    try:
        if (folder/'shared-result.json').exists(): raise FileExistsError('Completed attempt exists')
        if (folder/'shared-protocol.json').exists() and not args.resume: raise FileExistsError('Existing attempt requires --resume')
        if digest(args.source)!=SOURCE_SHA or digest(args.home/'OpenCore-Code-Single-File.gguf')!=MODEL_SHA:
            raise ValueError('Source/original checkpoint identity changed')
        profile=read_json(folder/'runtime-qualification.json')
        if not profile['passed'] or profile['source_sha256']!=SOURCE_SHA: raise RuntimeError('Native speed/parity unqualified; no training')
        data_protocol=read_json(folder/'data-protocol.json')
        path=folder/'complete-coding.json'
        if digest(path)!=data_protocol['data_sha256']: raise ValueError('Complete data changed')
        data=read_json(path)
        protocol={'source_sha256':SOURCE_SHA,'data_sha256':digest(path),'method':'Complete-answer supervised shared-expert fine tuning',
            'trainable_parameters':TRAINABLE,'trainable':'12 existing BF16 seed_scale/shared_coeff/shared_a/shared_b arrays',
            'frozen':'All private experts, backbone, router, MTP, stage and auxiliary heads',
            'runtime':{'kv_offload':profile['kv_offload'],'threads':profile['threads']},
            'seed':42,'epochs':1,'learning_rate':.0003,'batch_size':1,'max_gradient_norm':1.,
            'max_prompt_tokens':256,'max_response_tokens':256,'active_training_seconds':3600,
            'minimum_native_tokens_per_second':20,'heldout_loss_max_regression':.02,'automatic_promotion':False,
            'scope':'One bounded coding specialization run; not proof of global superiority'}
        if args.resume and read_json(folder/'shared-protocol.json')!=protocol: raise ValueError('Resume protocol changed')
        save(folder,'shared-protocol.json',protocol)
        if (folder/'baseline-native.json').exists() and read_json(folder/'baseline-native.json').get('speed') is not None:
            baseline=read_json(folder/'baseline-native.json')
            if baseline['source_sha256']!=SOURCE_SHA or [r['id'] for r in baseline['rows']]!=[r['id'] for r in data['heldout']]:
                raise ValueError('Baseline identity changed')
        else: baseline=native_checks(args,data['heldout'],args.source,'baseline',profile)
        if baseline['speed'] is None or baseline['speed']<20: raise RuntimeError('Native coding speed below 20; no optimizer update')
        if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable; no CPU fallback')
        free,_=torch.cuda.mem_get_info()
        if free<10_000_000_000: raise RuntimeError(f'Requires 10 GB free VRAM; available {free}')
        seed=args.research_root/'release/base-seeds/OpenCore-APEX-Dequantised-HF-corrected-v1'
        save(folder,'training-progress.json',{'phase':'loading-shared-training-view','optimizer_steps':0})
        model,reader=load_training_view(args.source,seed,args.research_root/'.opencore-runtime-packages/llama.cpp-opencore-q8-r43/gguf-py',
                                        device='cuda',train_shared=True)
        probes=read_json(folder/'native-parity-probes.json')
        checked=parity(model,probes,'cuda');save(folder,'shared-view-parity.json',checked)
        if not checked['passed']: raise RuntimeError('Shared training view failed native parity; no update')
        sequence=read_json(folder/'native-sequence-probe.json')
        with torch.no_grad(): scores=response_logprobs(model,sequence['prompt'],sequence['response'],'cuda')
        errors=(scores.cpu()-torch.tensor(sequence['logprobs'])).abs()
        checked={'tokens':len(errors),'mean_error':float(errors.mean()),'maximum_error':float(errors.max()),
            'passed':float(errors.mean())<=.15 and float(errors.max())<=.6,'note':'Native decode versus teacher-forced selected-token log probabilities'}
        save(folder,'complete-sequence-parity.json',checked)
        if not checked['passed']: raise RuntimeError('Complete response parity failed; no update')
        parameters={name:p for name,p in model.named_parameters() if p.requires_grad}
        if sum(p.numel() for p in parameters.values())!=TRAINABLE: raise ValueError('Shared parameter inventory changed')
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
        optimizer=torch.optim.AdamW(list(parameters.values()),lr=.0003,weight_decay=0)
        def validate():
            model.eval()
            with torch.no_grad(): return sum(float(complete_loss(model,row,'cuda')) for row in data['validation'])/len(data['validation'])
        state_file=folder/'shared-state.pt';completed=0;elapsed=0.;losses=[];before=validate()
        if args.resume and state_file.exists():
            state=torch.load(state_file,map_location='cpu',weights_only=True)
            if state['protocol']!=protocol: raise ValueError('Saved optimizer protocol changed')
            with torch.no_grad():
                for name,value in state['parameters'].items(): parameters[name].copy_(value.to('cuda'))
            optimizer.load_state_dict(state['optimizer']);completed=state['completed'];elapsed=state['elapsed'];losses=state['losses'];before=state['before']
        save(folder,'shared-heldout-before.json',{'loss':before,'examples':len(data['validation']),'native_passed':baseline['passed']})
        started=time.monotonic();peak=0
        for index,row in enumerate(data['train'][completed:],start=completed):
            if elapsed+time.monotonic()-started>3600: raise TimeoutError('Recorded one-hour active optimizer budget exhausted')
            model.train();optimizer.zero_grad(set_to_none=True)
            loss=complete_loss(model,row,'cuda')
            if not torch.isfinite(loss): raise ValueError('Nonfinite complete-answer loss')
            loss.backward();norm=torch.nn.utils.clip_grad_norm_(list(parameters.values()),1)
            if not torch.isfinite(norm): raise ValueError('Nonfinite shared gradient')
            optimizer.step();losses.append(float(loss.detach()));peak=max(peak,torch.cuda.max_memory_allocated())
            checkpoint={'protocol':protocol,'parameters':{n:p.detach().cpu().clone() for n,p in parameters.items()},
                'optimizer':optimizer.state_dict(),'completed':index+1,'elapsed':elapsed+time.monotonic()-started,'losses':losses,'before':before}
            temporary=state_file.with_suffix('.tmp');torch.save(checkpoint,temporary);temporary.replace(state_file)
            save(folder,'training-progress.json',{'phase':'complete-answer-training','completed':index+1,'total':len(data['train']),
                'last_loss':losses[-1],'gradient_norm':float(norm),'peak_vram_bytes':peak,'active_seconds':checkpoint['elapsed']})
            print(f'Shared SFT {index+1}/{len(data["train"])} loss={losses[-1]:.4f}',flush=True)
        after=validate();save(folder,'shared-heldout-after.json',{'before':before,'after':after,'examples':len(data['validation'])})
        candidate=folder/'OpenCore-ECHO-Shared-Candidate.gguf'
        updates={'opencore.'+name.removeprefix('opencore_expert_pool.'):value for name,value in parameters.items()}
        if candidate.exists():
            if not args.resume: raise FileExistsError('Candidate exists')
            exported=read_json(folder/'shared-export.json')
            if digest(candidate)!=exported['candidate_sha256']: raise ValueError('Saved candidate changed')
        else:
            exported=export_candidate(args.source,candidate,reader,updates,train_shared=True)
            save(folder,'shared-export.json',exported)
        # No dense master copy persists. Free every autograd/optimizer object
        # before the native model is loaded, including the last loss graph.
        del model,reader,optimizer,parameters,updates,checkpoint,loss,norm,scores
        gc.collect();torch.cuda.empty_cache()
        candidate_metrics=native_checks(args,data['heldout'],candidate,'candidate',profile)
        status=candidate_status(baseline,candidate_metrics,before,after)
        paired=[{'id':a['id'],'before':a['reward']['all_passed'],'after':b['reward']['all_passed']}
            for a,b in zip(baseline['rows'],candidate_metrics['rows'])]
        wins=sum(not r['before'] and r['after'] for r in paired);losses_count=sum(r['before'] and not r['after'] for r in paired)
        result={'status':status,'training_complete':True,'promoted':False,'source_sha256':digest(args.source),
            'original_sha256_after':digest(args.home/'OpenCore-Code-Single-File.gguf'),'candidate':str(candidate),'export':exported,
            'trainable_parameters':TRAINABLE,'training_examples':len(data['train']),'validation_examples':len(data['validation']),
            'heldout_coding_examples':len(data['heldout']),'heldout_before':before,'heldout_after':after,
            'native_coding_before':baseline['passed'],'native_coding_after':candidate_metrics['passed'],
            'case_reward_before':baseline['mean_case_reward'],'case_reward_after':candidate_metrics['mean_case_reward'],
            'native_speed':candidate_metrics['speed'],'peak_training_vram_bytes':peak,'paired_wins':wins,'paired_losses':losses_count,
            'paired':paired,'limitation':'Small finite same-source held-out test. Neither global superiority nor a public benchmark result.'}
        if result['source_sha256']!=SOURCE_SHA or result['original_sha256_after']!=MODEL_SHA: raise ValueError('Immutable checkpoint changed')
        save(folder,'shared-result.json',result);save(folder,'training-progress.json',{'phase':status});print(json.dumps(result,indent=2),flush=True)
    except BaseException as error:
        save(folder,'shared-failure.json',{'type':type(error).__name__,'error':str(error),'traceback':traceback.format_exc(),'time':time.time()})
        raise


if __name__=='__main__': main()
