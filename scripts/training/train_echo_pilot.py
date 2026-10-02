"""Small, gated training run on the shipped main ECHO checkpoint.

This is expert-composition fine tuning, not full-corpus/full-weight training.
GSM8K test examples never enter the optimizer. Original weights are immutable.
"""
import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import shutil
import time
import traceback
import torch
import torch.nn.functional as F
from echo_weight_adapter import MODEL_SHA, load_training_view
from native_echo_backend import NativeBackend
from mimo_actor import rollout


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as file:
        for block in iter(lambda:file.read(8<<20),b''): h.update(block)
    return h.hexdigest()


def save(folder,name,value):
    target=folder/name;temporary=target.with_suffix(target.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8')
    # Windows readers/antivirus can briefly deny replacement even though the
    # directory is writable. Preserve the old complete receipt while retrying;
    # a persistent denial is still an error, never a dropped progress update.
    for attempt in range(6):
        try: temporary.replace(target);return
        except PermissionError:
            if attempt==5: raise
            time.sleep(.05*2**attempt)


def example_tokens(tokenizer,record,device):
    # Preserve a bounded tail of the request and the first 64 answer tokens.
    # The prefix truncation is explicit in the pilot protocol, not a claim
    # to have trained on every token of each long coding solution.
    # Transformers 5 defaults to BatchEncoding. Request IDs explicitly; slicing
    # a BatchEncoding returns tokenizers.Encoding objects rather than token IDs.
    prompt=tokenizer.apply_chat_template(record['messages'][:1],tokenize=True,add_generation_prompt=True,
        enable_thinking=False,return_dict=False)[-192:]
    answer=tokenizer.encode(record['messages'][1]['content'],add_special_tokens=False)[:64]
    if not prompt or not answer: raise ValueError('Empty training example')
    tokens=prompt+answer
    # Predict answer[t] from the preceding position, including the last prompt
    # token. Compute only 64 vocabulary projections rather than a giant tensor.
    positions=torch.arange(len(prompt)-1,len(tokens)-1,device=device)
    return torch.tensor([tokens],device=device),positions,torch.tensor(answer,device=device)


def loss_for(model,tokenizer,record,device):
    tokens,positions,targets=example_tokens(tokenizer,record,device)
    model.opencore_expert_pool.position_start=0
    logits=model(input_ids=tokens,use_cache=False,logits_to_keep=positions).logits[0]
    return F.cross_entropy(logits.float(),targets)


def parity(model,probes,device):
    rows=[]
    model.eval()
    with torch.no_grad():
        for probe in probes:
            model.opencore_expert_pool.position_start=0
            logits=model(input_ids=torch.tensor([probe['tokens']],device=device),use_cache=False,logits_to_keep=1).logits[0,0].float()
            logprobs=F.log_softmax(logits,dim=-1).cpu()
            top=probe['native']['top']
            native_id=max(top,key=lambda item:item['logprob'])['id']
            error=sum(abs(float(logprobs[item['id']])-item['logprob']) for item in top)/len(top)
            rows.append({'prompt':probe['prompt'],'tokens':probe['tokens'],'native_top1':native_id,
                'training_view_top1':int(logits.argmax()),'top64_mean_absolute_logprob_error':error})
    matched=sum(row['native_top1']==row['training_view_top1'] for row in rows)
    error=sum(row['top64_mean_absolute_logprob_error'] for row in rows)/len(rows)
    return {'passed':matched>=4 and error<=.3,'top1_matches':matched,'required_top1_matches':4,
        'probes':rows,'mean_absolute_logprob_error':error,'maximum_mean_error':.3,
        'note':'Short-prefill next-token gate only; not whole-model or long-context equivalence'}


def export_candidate(source,target,reader,updates,train_shared=False):
    if target.exists(): raise FileExistsError('Candidate already exists; preserve it')
    suffixes=('seed_scale','shared_coeff','shared_a','shared_b') if train_shared else ('seed_scale','shared_coeff')
    allowed={f'opencore.{p}.{s}' for p in ('gate','up','down') for s in suffixes}
    if set(updates)!=allowed: raise ValueError('Unexpected trainable tensor inventory')
    tensors={tensor.name:tensor for tensor in reader.tensors}
    encoded={}
    for name,value in updates.items():
        tensor=tensors[name]
        if tensor.tensor_type.name!='BF16' or value.numel()!=tensor.n_elements:
            raise ValueError('Training export changed tensor shape or precision')
        if hasattr(tensor,'shape') and tuple(value.shape)!=tuple(reversed(tensor.shape.tolist())):
            raise ValueError('Training export changed tensor layout')
        converted=value.detach().cpu().to(torch.bfloat16)
        if not torch.isfinite(converted).all(): raise ValueError('Nonfinite exported BF16 weight')
        encoded[name]=converted.contiguous().view(torch.uint16).numpy().tobytes()
        if len(encoded[name])!=tensor.n_bytes: raise ValueError('Training tensor byte-size mismatch')
    shutil.copyfile(source,target)
    with target.open('r+b') as file:
        for name,payload in encoded.items():
            file.seek(tensors[name].data_offset);file.write(payload)
        file.flush()
        import os
        os.fsync(file.fileno())
    changed=[]
    with target.open('rb') as file:
        for tensor in reader.tensors:
            file.seek(tensor.data_offset)
            # Hash slices incrementally, including huge backbone matrices.
            actual=hashlib.sha256();remaining=tensor.n_bytes
            while remaining:
                block=file.read(min(8<<20,remaining))
                if not block: raise ValueError('Incomplete export')
                actual.update(block);remaining-=len(block)
            expected=hashlib.sha256(memoryview(tensor.data).cast('B')).hexdigest()
            if actual.hexdigest()!=expected:
                if tensor.name not in allowed: raise ValueError(f'Frozen tensor changed: {tensor.name}')
                changed.append(tensor.name)
    if not changed: raise ValueError('No BF16 weights changed after training; candidate is not trained')
    return {'candidate_sha256':digest(target),'changed_tensors':changed,'unchanged_tensors':len(reader.tensors)-len(changed),
        'precision':'Original Q6_K/F32 backbone and BF16 expert tensors; '+
            ('twelve BF16 shared-factor/composition arrays eligible for updates' if train_shared else 'six BF16 composition arrays eligible for updates')}


def parse_args(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path,default=Path.home()/'OpenCore')
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--research-root',type=Path,required=True)
    parser.add_argument('--mimo-max-steps',type=int,choices=(16,32),default=16,
        help='Explicit MiMo action budget; use 32 only for the approved retry')
    return parser.parse_args(argv)


def main():
    args=parse_args();folder=args.folder;folder.mkdir(parents=True,exist_ok=True)
    if (folder/'training-result.json').exists(): raise FileExistsError('Completed pilot exists; do not retrain silently')
    source=args.home/'OpenCore-Code-Single-File.gguf'
    if digest(source)!=MODEL_SHA: raise ValueError('Main ECHO identity changed')
    data=json.loads((folder/'data-protocol.json').read_text(encoding='utf-8'))
    if digest(folder/'ultra-sft.jsonl')!=data['sample_sha256']: raise ValueError('Training data changed')
    protocol={'source_sha256':MODEL_SHA,'seed':42,'method':'Reward-filtered SFT + UltraData supervised expert-composition fine tuning',
        'epochs':1,'batch_size':1,'learning_rate':.001,'max_prompt_tokens':192,'max_target_tokens':64,
        'frozen':'All backbone, private/shared factors, router, MTP and stage tensors',
        'trainable':'Only gate/up/down seed_scale and shared_coeff arrays',
        'validation_tolerance_nats':.02,'native_parity_top1_minimum':4,'native_parity_top64_logprob_error_maximum':.3,
        'mimo_max_steps':args.mimo_max_steps,'mimo_action_format':'native_json_schema','mimo_action_output_tokens':512,'minimum_native_decode_tokens_per_second':20,'automatic_promotion':False,
        'scope':'Small local pilot; not full-corpus RL or a general improvement claim','data':data}
    save(folder,'training-protocol.json',protocol)
    torch.set_num_threads(4);torch.manual_seed(42)
    try:
        from transformers import AutoTokenizer
        seed=args.research_root/'release/base-seeds/OpenCore-APEX-Dequantised-HF-corrected-v1'
        tokenizer=AutoTokenizer.from_pretrained(seed,local_files_only=True)
        prompts=['The capital of France is','def add(a, b):\n    return','A box holds 3 rows of 4 apples. The total is',
            'User: Say hello.\nAssistant:','function double(value) {\n  return']
        save(folder,'training-progress.json',{'phase':'native qualification and MiMo rollout'})
        probes=[]
        with NativeBackend(args.home,folder) as backend:
            for prompt in prompts:
                tokens=tokenizer.encode(prompt,add_special_tokens=False)
                probes.append({'prompt':prompt,'tokens':tokens,'native':backend.probabilities(tokens)})
            save(folder,'native-parity-probes.json',probes)
            reward=rollout(backend,folder,max_steps=args.mimo_max_steps)
        if args.mimo_max_steps==32 and reward['reward']!=1:
            save(folder,'training-progress.json',{'phase':'mimo-retry-failed','mimo_verified_examples':0})
            raise RuntimeError('Approved MiMo retry failed immutable verification; existing UltraData candidate preserved, no duplicate training')
        gc.collect()
        if not torch.cuda.is_available(): raise RuntimeError('CUDA is unavailable; never silently use CPU for this training run')
        free,total=torch.cuda.mem_get_info()
        if free<10_000_000_000: raise RuntimeError(f'GPU has {free} free bytes; training requires 10 GB free')
        save(folder,'training-progress.json',{'phase':'loading gated autograd view','free_vram_bytes':free})
        model,reader=load_training_view(source,seed,args.research_root/'.opencore-runtime-packages/llama.cpp-opencore-q8-r43/gguf-py',device='cuda')
        checked=parity(model,probes,'cuda');save(folder,'training-view-parity.json',checked)
        if not checked['passed']: raise RuntimeError('Training view does not reproduce native next-token probabilities; original preserved, optimizer not started')
        rows=[json.loads(line) for line in (folder/'ultra-sft.jsonl').read_text(encoding='utf-8').splitlines()]
        train=[row for row in rows if row['split']=='train'];validation=[row for row in rows if row['split']=='validation']
        if reward['reward']==1:
            train += [json.loads(line) for line in (folder/'mimo-sft.jsonl').read_text(encoding='utf-8').splitlines()]
        def validate():
            model.eval()
            with torch.no_grad(): return sum(float(loss_for(model,tokenizer,row,'cuda')) for row in validation)/len(validation)
        before=validate();save(folder,'heldout-before.json',{'loss':before,'examples':len(validation)})
        initial={name:p.detach().cpu().clone() for name,p in model.named_parameters() if p.requires_grad}
        parameters=[p for p in model.parameters() if p.requires_grad]
        if sum(p.numel() for p in parameters)!=90000: raise ValueError('Unexpected trainable parameter count')
        optimizer=torch.optim.AdamW(parameters,lr=.001,weight_decay=0)
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
        started=time.monotonic();losses=[]
        for index,row in enumerate(train):
            if time.monotonic()-started>2700: raise TimeoutError('Local pilot exceeded 45-minute training budget')
            model.train();optimizer.zero_grad(set_to_none=True)
            loss=loss_for(model,tokenizer,row,'cuda')
            if not torch.isfinite(loss): raise ValueError('Nonfinite training loss')
            loss.backward();norm=torch.nn.utils.clip_grad_norm_(parameters,1)
            if not torch.isfinite(norm): raise ValueError('Nonfinite training gradient')
            optimizer.step();losses.append(float(loss.detach()))
            save(folder,'training-progress.json',{'phase':'training','completed':index+1,'required':len(train),'last_loss':losses[-1],
                'gradient_norm':float(norm),'peak_vram_bytes':torch.cuda.max_memory_allocated()})
            print(f'Training {index+1}/{len(train)} loss={losses[-1]:.4f}',flush=True)
        after=validate();save(folder,'heldout-after.json',{'loss':after,'examples':len(validation),'before':before,'maximum_regression':.02})
        updates={name.removeprefix('opencore_expert_pool.'):p for name,p in model.named_parameters() if p.requires_grad}
        checkpoint={name:value.detach().cpu().to(torch.bfloat16) for name,value in updates.items()}
        torch.save(checkpoint,folder/'expert-composition-bf16.pt')
        if after>before+.02: raise RuntimeError('Heldout loss regressed; trained deltas kept, candidate not released')
        candidate=folder/'OpenCore-ECHO-Pilot.gguf'
        exported=export_candidate(source,candidate,reader,{'opencore.'+name:value for name,value in updates.items()})
        peak=torch.cuda.max_memory_allocated()
        del model,parameters,optimizer,updates,reader;gc.collect();torch.cuda.empty_cache()
        with NativeBackend(args.home,folder,candidate) as backend:
            warm=backend.chat([{'role':'user','content':'Write twenty short arithmetic facts.'}],max_tokens=128)
            speed=(warm.get('timings') or {}).get('predicted_per_second')
            save(folder,'candidate-speed.json',{'tokens_per_second':speed,'minimum':20,'response':warm})
            if speed is None or speed<20: raise RuntimeError(f'Trained candidate failed native speed gate: {speed}')
        result={'status':'complete' if reward['reward']==1 else 'partial-mimo-rollout-failed','ultra_examples':len(train)-(reward['reward']==1),'mimo_verified_examples':int(reward['reward']==1),
            'steps':len(losses),'train_loss_mean':sum(losses)/len(losses),'heldout_before':before,'heldout_after':after,
            'peak_training_vram_bytes':peak,'native_tokens_per_second':speed,'candidate':str(candidate),
            'original_sha256_after':digest(source),'promoted':False,'export':exported,
            'limitation':'One short-prefix pilot. Does not establish coding superiority, full MiMo RL or general benchmark improvement.'}
        if result['original_sha256_after']!=MODEL_SHA: raise ValueError('Original checkpoint changed')
        save(folder,'training-result.json',result);save(folder,'training-progress.json',{'phase':result['status']})
        print(json.dumps(result,indent=2),flush=True)
    except BaseException as error:
        save(folder,'training-failure.json',{'error':str(error),'type':type(error).__name__,'traceback':traceback.format_exc(),'time':time.time()})
        raise


if __name__=='__main__': main()
