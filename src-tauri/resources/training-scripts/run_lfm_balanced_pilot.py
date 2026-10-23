import json, math, os, random, time
from pathlib import Path
import torch
from torch.utils.data import Dataset, DataLoader
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model

ROOT=Path(r'C:\Users\hertz\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\OpenCore')
MODEL=ROOT/'training-models'/'LiquidAI-LFM2.5-2.6B-654f9463ce32b05d0429d76fe1f580b27d4c1ac0'
DATA=ROOT/'training-data'
OUT=ROOT/'training-adapters'/'lfm-balanced-pilot-20260930'
TRAIN=DATA/'lfm-balanced-train-20260930.jsonl'; VAL=DATA/'lfm-balanced-val-20260930.jsonl'
MAXLEN=512; NTRAIN=200; NVAL=128; STEPS=50; ACCUM=4; LR=8e-6
random.seed(20260930); torch.manual_seed(20260930); torch.cuda.manual_seed_all(20260930)
if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable')
props=torch.cuda.get_device_properties(0); print(f'GPU={props.name} total={props.total_memory/2**30:.2f}GiB')

def rows(p):
 with p.open(encoding='utf-8') as f:
  for l in f:
   if l.strip(): yield json.loads(l)
class Encoded(Dataset):
 def __init__(self, path, tok, count, seed):
  pool=list(rows(path)); random.Random(seed).shuffle(pool); self.items=[]
  for r in pool:
   x=tok.apply_chat_template(r['messages'],tokenize=True,return_dict=True,return_assistant_tokens_mask=True)
   ids=x['input_ids']; mask=x.get('assistant_masks')
   if not mask or len(ids)!=len(mask): continue
   if len(ids)>MAXLEN: ids=ids[-MAXLEN:]; mask=mask[-MAXLEN:]
   if sum(mask)<3: continue
   labels=[(v if m else -100) for v,m in zip(ids,mask)]
   self.items.append((ids,labels))
   if len(self.items)>=count: break
  if len(self.items)<count: raise RuntimeError(f'{path.name}: only {len(self.items)} usable examples, need {count}')
  print(f'{path.name}: tokenized={len(self.items)} avg_len={sum(len(x[0]) for x in self.items)/len(self.items):.1f}')
 def __len__(self):return len(self.items)
 def __getitem__(self,i):return self.items[i]
def collate(batch, pad):
 n=max(len(x[0]) for x in batch); ids=[]; labels=[]; att=[]
 for a,b in batch:
  k=n-len(a); ids.append(a+[pad]*k); labels.append(b+[-100]*k); att.append([1]*len(a)+[0]*k)
 return {k:torch.tensor(v,device='cuda',dtype=torch.long) for k,v in {'input_ids':ids,'labels':labels,'attention_mask':att}.items()}
print('loading tokenizer/base BF16...',flush=True)
tok=AutoTokenizer.from_pretrained(MODEL,local_files_only=True)
model=AutoModelForCausalLM.from_pretrained(MODEL,torch_dtype=torch.bfloat16,device_map='cuda',local_files_only=True,attn_implementation='sdpa')
base_count=sum(p.numel() for p in model.parameters())
model.eval()
val=Encoded(VAL,tok,NVAL,77)
def evaluate(model, ds):
 model.eval(); vals=[]
 with torch.inference_mode():
  for ix in range(len(ds)):
   ids,lab=ds[ix]; batch=collate([(ids,lab)],tok.pad_token_id)
   out=model(**batch); vals.append(float(out.loss))
 return sum(vals)/len(vals)
base_loss=evaluate(model,val); print(f'baseline_val_assistant_loss={base_loss:.6f}',flush=True)
train=Encoded(TRAIN,tok,NTRAIN,51)
config=LoraConfig(r=4,lora_alpha=8,lora_dropout=0.03,target_modules='all-linear',bias='none',task_type='CAUSAL_LM')
model=get_peft_model(model,config); model.train(); opt=torch.optim.AdamW((p for p in model.parameters() if p.requires_grad),lr=LR,weight_decay=0.01)
loader=DataLoader(train,batch_size=1,shuffle=True,collate_fn=lambda b:collate(b,tok.pad_token_id),generator=torch.Generator().manual_seed(11))
iterator=iter(loader); losses=[]; t0=time.time(); torch.cuda.reset_peak_memory_stats(); opt.zero_grad(set_to_none=True)
for step in range(1,STEPS+1):
 accum_loss=0.0
 for _ in range(ACCUM):
  try: batch=next(iterator)
  except StopIteration: iterator=iter(loader); batch=next(iterator)
  with torch.autocast('cuda',dtype=torch.bfloat16): out=model(**batch); loss=out.loss/ACCUM
  if not torch.isfinite(loss): raise RuntimeError(f'nonfinite training loss at step {step}')
  loss.backward(); accum_loss+=float(loss.detach())
 torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad),1.0)
 opt.step(); opt.zero_grad(set_to_none=True); losses.append(accum_loss)
 if step in (1,10,25,50):
  print(f'step={step}/{STEPS} train_loss={accum_loss:.5f} elapsed={time.time()-t0:.1f}s peak_vram={torch.cuda.max_memory_allocated()/2**30:.2f}GiB',flush=True)
model.eval(); tuned_loss=evaluate(model,val)
OUT.mkdir(parents=True,exist_ok=False); model.save_pretrained(OUT,safe_serialization=True); tok.save_pretrained(OUT)
report={'model_path':str(MODEL),'revision':'654f9463ce32b05d0429d76fe1f580b27d4c1ac0','precision':'BF16 base + BF16 LoRA adapter','train_rows':len(train),'validation_rows':len(val),'max_sequence_tokens':MAXLEN,'optimizer_steps':STEPS,'examples_per_step':ACCUM,'learning_rate':LR,'seed':20260930,'baseline_validation_assistant_loss':base_loss,'candidate_validation_assistant_loss':tuned_loss,'relative_loss_change':(tuned_loss-base_loss)/base_loss,'train_loss_first_step':losses[0],'train_loss_last_step':losses[-1],'elapsed_seconds':time.time()-t0,'peak_cuda_allocated_gib':torch.cuda.max_memory_allocated()/2**30,'base_parameters':base_count,'trainable_parameters':sum(p.numel() for p in model.parameters() if p.requires_grad),'adapter_dir':str(OUT),'quality_claim':'pilot only; requires held-out functional benchmarks before adoption'}
(OUT/'pilot-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps(report,indent=2))
