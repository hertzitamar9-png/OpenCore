"""Bounded local online RL pilot; preserve the approved UltraData checkpoint.

This is group-relative, clipped policy-gradient optimization of 90K composition
parameters, not full-model RL or supervised imitation of passing solutions.
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
from transformers import AutoTokenizer
from echo_weight_adapter import MODEL_SHA,load_training_view
from echo_rl import group_advantages,policy_loss,prompt_ids,prompt_text,response_logprobs,sample
from native_echo_backend import NativeBackend
from rl_tasks import TRAIN_TASKS,HELDOUT_TASKS
from rl_verifier import CodingVerifier
from train_echo_pilot import digest,export_candidate,loss_for,parity,save

SOURCE_SHA='4551c5333bb6287f0222e15a4d1e3a969df04cb7a69833125f5b3aa80239b91a'
PROBE_TEXT=['The capital of France is','def add(a, b):\n    return',
    'A box holds 3 rows of 4 apples. The total is','User: Say hello.\nAssistant:',
    'function double(value) {\n  return']


def native_checks(home,folder,source,tokenizer,verifier,label):
    rows=[];probes=[]
    with NativeBackend(home,folder/label,source,port=8892) as backend:
        for text in PROBE_TEXT:
            tokens=tokenizer.encode(text,add_special_tokens=False)
            probes.append({'prompt':text,'tokens':tokens,'native':backend.probabilities(tokens)})
        for task in HELDOUT_TASKS:
            response=backend.chat([{'role':'user','content':prompt_text(task['prompt'])}],max_tokens=128)
            choice=response['choices'][0]
            row={'task_id':task['id'],'response':response,'text':choice['message']['content'] or ''}
            reward=verifier.verify(task,row['text'],label+'-'+task['name'],choice['finish_reason']=='length')
            row['reward']=reward;rows.append(row)
        speed_response=backend.chat([{'role':'user','content':'Write twenty short arithmetic facts.'}],max_tokens=128)
        speed=(speed_response.get('timings') or {}).get('predicted_per_second')
        if speed is None or speed<20: raise RuntimeError(f'{label} native speed gate failed: {speed}')
    result={'examples':len(rows),'passed':sum(x['reward']['all_passed'] for x in rows),
            'mean_case_reward':sum(x['reward']['reward'] for x in rows)/len(rows),
            'native_tokens_per_second':speed,'rows':rows,'speed_response':speed_response}
    save(folder,label+'-checks.json',result)
    save(folder,label+'-probes.json',probes)
    return result,probes


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--research-root',type=Path,required=True)
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--home',type=Path,default=Path.home()/'OpenCore')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args(argv);folder=args.folder;folder.mkdir(parents=True,exist_ok=True)
    # OS lock is released on crash. Never attach a second trainer to a live run.
    import msvcrt
    lock=(folder/'training.lock').open('a+b');lock.seek(0);lock.write(b'1');lock.flush();lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    torch.set_num_threads(2)
    save(folder,'training-process.json',{'pid':os.getpid(),'started':time.time(),'source':str(args.source),'folder':str(folder)})
    try:
        if (folder/'training-result.json').exists(): raise FileExistsError('Completed RL pilot exists; refusing duplicate training')
        if (folder/'rl-protocol.json').exists() and not args.resume: raise FileExistsError('Existing RL attempt requires explicit resume')
        if digest(args.source)!=SOURCE_SHA: raise ValueError('Approved UltraData candidate identity changed')
        if digest(args.home/'OpenCore-Code-Single-File.gguf')!=MODEL_SHA: raise ValueError('Original ECHO identity changed')
        task_bytes=json.dumps({'train':TRAIN_TASKS,'heldout':HELDOUT_TASKS},sort_keys=True,ensure_ascii=False).encode()
        protocol={'source_sha256':SOURCE_SHA,'method':'online group-relative clipped policy gradients',
            'task_sha256':hashlib.sha256(task_bytes).hexdigest(),'train_tasks':8,'heldout_tasks':4,'samples_per_task':4,
            'epochs':1,'max_prompt_tokens':192,'max_response_tokens':128,'temperature':1.,'seed':42,
            'learning_rate':.0005,'clip':.2,'local_reference_kl_beta':.02,'max_gradient_norm':1.,
            'active_training_seconds':2700,'validation_tolerance_nats':.02,'minimum_native_tokens_per_second':20,
            'sample_score_mean_error_max':.15,'sample_score_max_error_max':.6,
            'precision':'Original Q6_K/F32 backbone and BF16 expert factors/export; FP32 composition optimizer masters',
            'trainable_parameters':90000,'trainable':'Six expert seed_scale/shared_coeff arrays; other 471 tensors frozen',
            'reward':'Fraction of fixed isolated unit tests passed; truncated/invalid output earns zero',
            'scope':'Small authored coding pilot, not full-corpus RL or proof of general improvement',
            'promote':False}
        if args.resume and json.loads((folder/'rl-protocol.json').read_text())!=protocol:
            raise ValueError('Resume protocol changed')
        save(folder,'rl-protocol.json',protocol)
        (folder/'tasks.json').write_bytes(task_bytes)
        seed=args.research_root/'release/base-seeds/OpenCore-APEX-Dequantised-HF-corrected-v1'
        tokenizer=AutoTokenizer.from_pretrained(seed,local_files_only=True)
        prompts={task['id']:prompt_ids(tokenizer,task['prompt']) for task in TRAIN_TASKS+HELDOUT_TASKS}
        save(folder,'prompt-lengths.json',{name:len(ids) for name,ids in prompts.items()})
        with CodingVerifier(folder) as verifier:
            if args.resume and (folder/'baseline-checks.json').exists():
                baseline=json.loads((folder/'baseline-checks.json').read_text())
                probes=json.loads((folder/'baseline-probes.json').read_text())
                # Re-score saved baseline solutions with the current immutable
                # verifier after a correctness repair, without re-generating.
                for task,row in zip(HELDOUT_TASKS,baseline['rows']):
                    row['reward']=verifier.verify(task,row['text'],'baseline-'+task['name'],row['reward']['truncated'])
                baseline['passed']=sum(row['reward']['all_passed'] for row in baseline['rows'])
                baseline['mean_case_reward']=sum(row['reward']['reward'] for row in baseline['rows'])/len(baseline['rows'])
                save(folder,'baseline-checks.json',baseline)
            else:
                save(folder,'training-progress.json',{'phase':'baseline-native-checks','optimizer_steps':0})
                baseline,probes=native_checks(args.home,folder,args.source,tokenizer,verifier,'baseline')
            if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable; no CPU training fallback')
            free,_=torch.cuda.mem_get_info()
            if free<10_000_000_000: raise RuntimeError(f'Training requires 10 GB free VRAM; available {free}')
            save(folder,'training-progress.json',{'phase':'loading-autograd-policy','optimizer_steps':0,'free_vram_bytes':free})
            model,reader=load_training_view(args.source,seed,args.research_root/'.opencore-runtime-packages/llama.cpp-opencore-q8-r43/gguf-py',device='cuda')
            checked=parity(model,probes,'cuda');save(folder,'training-view-parity.json',checked)
            if not checked['passed']: raise RuntimeError('Native/autograd parity failed; no optimizer update')
            parameters={name:p for name,p in model.named_parameters() if p.requires_grad}
            if sum(p.numel() for p in parameters.values())!=90000: raise ValueError('Trainable parameter count changed')
            optimizer=torch.optim.AdamW(list(parameters.values()),lr=protocol['learning_rate'],weight_decay=0)
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
            initial={name:p.detach().cpu().clone() for name,p in parameters.items()}
            validation_file=args.research_root/'artifacts/training/mimo-ultradata-20261001/ultra-sft.jsonl'
            if digest(validation_file)!='352c94f418d7f547bc1165b7dc9c6d267faca113fdfff20a4e12f12b970ed202':
                raise ValueError('Held-out UltraData sample identity changed')
            validation=[json.loads(line) for line in validation_file.read_text(encoding='utf-8').splitlines() if json.loads(line)['split']=='validation']
            def validate():
                model.eval();model.opencore_expert_pool.decode_start=None
                with torch.no_grad(): return sum(float(loss_for(model,tokenizer,row,'cuda')) for row in validation)/len(validation)
            before=validate();steps=0;completed=0;elapsed=0.;groups=[]
            state_file=folder/'rl-state.pt'
            if args.resume and state_file.exists():
                state=torch.load(state_file,map_location='cpu',weights_only=True)
                with torch.no_grad():
                    for name,value in state['parameters'].items(): parameters[name].copy_(value.to('cuda'))
                optimizer.load_state_dict(state['optimizer']);steps=state['steps'];completed=state['completed']
                before=state['heldout_before'];elapsed=state['elapsed'];groups=state['groups']
            if args.resume and (folder/'verifier-fix-stop.json').exists():
                receipt=json.loads((folder/'verifier-fix-stop.json').read_text(encoding='utf-8-sig'))
                elapsed=max(elapsed,float(receipt.get('active_seconds') or 0))
            if args.resume and (folder/'interruption-receipt.json').exists():
                receipt=json.loads((folder/'interruption-receipt.json').read_text(encoding='utf-8-sig'))
                elapsed=max(elapsed,float(receipt['progress'].get('active_seconds') or 0))
            save(folder,'heldout-before.json',{'loss':before,'examples':len(validation),'native_coding_passed':baseline['passed']})
            started=time.monotonic();deadline=started+protocol['active_training_seconds']-elapsed
            for task_index,task in enumerate(TRAIN_TASKS[completed:],start=completed):
                rows=[];model.eval()
                for member in range(protocol['samples_per_task']):
                    label=f'group-{task_index:02d}-sample-{member:02d}'
                    path=folder/(label+'.json')
                    if args.resume and path.exists():
                        row=json.loads(path.read_text(encoding='utf-8'))
                        if row['task_id']!=task['id'] or row['policy_step']!=steps or row['prompt_ids']!=prompts[task['id']]:
                            raise ValueError('Saved rollout identity mismatch')
                    else:
                        def progress(tokens):
                            if tokens==1 or tokens%16==0:
                                save(folder,'training-progress.json',{'phase':'rl-rollouts','group':task_index+1,'groups':8,
                                    'sample':member+1,'samples':4,'generated_tokens':tokens,'optimizer_steps':steps,
                                    'active_seconds':elapsed+time.monotonic()-started})
                        row=sample(model,tokenizer,prompts[task['id']],'cuda',max_tokens=128,
                            seed=42+task_index*4+member,deadline=deadline,on_token=progress)
                        row.update(task_id=task['id'],policy_step=steps)
                        save(folder,label+'.json',row)
                    row['reward']=verifier.verify(task,row['text'],label,truncated=row['truncated'])
                    save(folder,label+'.json',row);rows.append(row)
                rewards=[row['reward']['reward'] for row in rows]
                advantages=group_advantages(rewards)
                group={'task_id':task['id'],'rewards':rewards,'advantages':advantages.tolist(),'updated':False}
                if bool(advantages.abs().sum()):
                    # Check cached sampling against teacher forcing BEFORE any update.
                    errors=[];model.eval()
                    for row in rows:
                        with torch.no_grad():
                            current=response_logprobs(model,row['prompt_ids'],row['response_ids'],'cuda').cpu()
                            error=(current-torch.tensor(row['old_logprobs'])).abs()
                        errors.append({'mean':float(error.mean()),'max':float(error.max())})
                    group['sample_score_errors']=errors
                    if any(x['mean']>.15 or x['max']>.6 for x in errors):
                        save(folder,f'group-{task_index:02d}-rejected.json',group)
                        raise RuntimeError('Rollout/scoring route or cache mismatch; no update on this group')
                    save(folder,'training-progress.json',{'phase':'rl-policy-gradient','group':task_index+1,'groups':8,'optimizer_steps':steps,'rewards':rewards})
                    optimizer.zero_grad(set_to_none=True);model.train();losses=[]
                    token_count=sum(row['tokens'] for row in rows)
                    for row,advantage in zip(rows,advantages):
                        if time.monotonic()>deadline: raise TimeoutError('RL active training budget exceeded')
                        probs=response_logprobs(model,row['prompt_ids'],row['response_ids'],'cuda')
                        old=torch.tensor(row['old_logprobs'],device='cuda')
                        loss=policy_loss(probs,old,float(advantage))*row['tokens']/token_count
                        if not torch.isfinite(loss): raise ValueError('Nonfinite RL loss')
                        loss.backward();losses.append(float(loss.detach()))
                    norm=torch.nn.utils.clip_grad_norm_(list(parameters.values()),1.)
                    if not torch.isfinite(norm) or norm==0: raise ValueError('Invalid or zero RL gradient')
                    optimizer.step();steps+=1
                    group.update(updated=True,loss=sum(losses),gradient_norm=float(norm),optimizer_step=steps)
                groups.append(group)
                state={'parameters':{name:p.detach().cpu() for name,p in parameters.items()},'optimizer':optimizer.state_dict(),
                       'steps':steps,'completed':task_index+1,'heldout_before':before,
                       'elapsed':elapsed+time.monotonic()-started,'groups':groups}
                temporary=state_file.with_suffix('.tmp');torch.save(state,temporary);temporary.replace(state_file)
                save(folder,f'group-{task_index:02d}-result.json',group)
                save(folder,'training-progress.json',{'phase':'rl-group-complete','completed_groups':task_index+1,'required_groups':8,
                    'optimizer_steps':steps,'last_rewards':rewards,'peak_vram_bytes':torch.cuda.max_memory_allocated()})
                print(json.dumps(group),flush=True)
            after=validate();save(folder,'heldout-after.json',{'loss':after,'before':before,'maximum_regression':.02})
            if steps==0: raise RuntimeError('No reward variation: no RL updates; cannot claim a trained candidate')
            if after>before+.02: raise RuntimeError('Held-out loss regressed; deltas preserved, candidate not qualified')
            max_delta=max(float((p.detach().cpu()-initial[name]).abs().max()) for name,p in parameters.items())
            if max_delta>.02: raise RuntimeError('Composition parameter trust bound exceeded')
            updates={'opencore.'+name.removeprefix('opencore_expert_pool.'):p for name,p in parameters.items()}
            torch.save({name:p.detach().cpu().to(torch.bfloat16) for name,p in updates.items()},folder/'rl-composition-bf16.pt')
            candidate=folder/'OpenCore-ECHO-RL-Pilot.gguf'
            export=export_candidate(args.source,candidate,reader,updates)
            peak=torch.cuda.max_memory_allocated()
            del model,reader,parameters,optimizer,updates
            # A resumed run can have completed all its policy updates already.
            # Release optional last-batch graphs without assuming one ran here.
            if 'probs' in locals(): del probs
            if 'loss' in locals(): del loss
            gc.collect();torch.cuda.empty_cache()
            save(folder,'training-progress.json',{'phase':'candidate-native-qualification','optimizer_steps':steps})
            candidate_checks,_=native_checks(args.home,folder,candidate,tokenizer,verifier,'candidate')
            if digest(args.source)!=SOURCE_SHA or digest(args.home/'OpenCore-Code-Single-File.gguf')!=MODEL_SHA:
                raise ValueError('Preserved source checkpoint changed')
            status='complete' if candidate_checks['passed']>=baseline['passed'] and candidate_checks['mean_case_reward']>=baseline['mean_case_reward'] else 'rejected-native-heldout-regression'
            result={'status':status,'optimizer_steps':steps,'groups':groups,'heldout_loss_before':before,'heldout_loss_after':after,
                'native_heldout_before':baseline['passed'],'native_heldout_after':candidate_checks['passed'],'native_heldout_tasks':4,
                'native_tokens_per_second':candidate_checks['native_tokens_per_second'],'peak_vram_bytes':peak,
                'candidate':str(candidate),'export':export,'source_sha256_preserved':SOURCE_SHA,'original_sha256_preserved':MODEL_SHA,
                'promoted':False,'limitation':protocol['scope']}
            save(folder,'training-result.json',result);save(folder,'training-progress.json',{'phase':status,'optimizer_steps':steps})
            print(json.dumps(result,indent=2),flush=True)
    except BaseException:
        save(folder,'training-failure.json',{'traceback':traceback.format_exc(),'time':time.time(),'source_preserved':str(args.source)})
        raise
    finally:
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()


if __name__=='__main__': main()
