import ast,gc,json,re,subprocess,sys,time,torch
from pathlib import Path
from datasets import load_dataset
from transformers import AutoTokenizer,AutoModelForCausalLM
from peft import PeftModel
BASE=Path(r'C:\Users\hertz\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\OpenCore')
MODEL=BASE/'training-models'/'LiquidAI-LFM2.5-2.6B-654f9463ce32b05d0429d76fe1f580b27d4c1ac0'
ADAPTER=BASE/'training-adapters'/'lfm-balanced-pilot-20260930'
OUT=Path(r'C:\Users\hertz\Documents\Best ai model in the world\artifacts\evaluations\profile-suite-20260926\lfm-bf16-source-smoke-20260930')
REV='7dce6050a7d6d172f3cc5c32aa97f52fa1a2e544'; MAXTOK=384
SAFE_IMPORTS={'math','re','itertools','collections','heapq','string','functools','operator','datetime','random','bisect','statistics','cmath','typing','numbers','decimal','fractions','array','copy','enum','abc','dataclasses','sys'}

def extract(reply):
 m=re.search(r'```(?:python)?\s*(.*?)```',reply,re.S|re.I)
 return (m.group(1) if m else reply).strip()
def inspect_code(reply):
 code=extract(reply)
 try: tree=ast.parse(code)
 except SyntaxError: return False,'syntax_error'
 for node in ast.walk(tree):
  if isinstance(node,(ast.Import,ast.ImportFrom)):
   names=[a.name.split('.')[0] for a in node.names] if isinstance(node,ast.Import) else [(node.module or '').split('.')[0]]
   if any(n not in SAFE_IMPORTS for n in names): return False,'blocked_import'
  if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in {'open','exec','eval','compile','__import__','breakpoint'}: return False,'blocked_call'
  if isinstance(node,ast.Attribute) and node.attr in {'system','popen','Popen','run','remove','unlink','rmdir','write_text','write_bytes','connect','urlopen'}: return False,'blocked_attribute'
 return True,''
def run_test(program):
 p=OUT/'scratch-humaneval-check.py'; p.write_text(program,encoding='utf-8')
 try:
  r=subprocess.run([sys.executable,'-I',str(p)],cwd=str(OUT),capture_output=True,text=True,timeout=10)
  return r.returncode==0, (r.stderr or r.stdout)[-600:]
 except subprocess.TimeoutExpired:return False,'timeout'
 finally:
  try:p.unlink()
  except OSError:pass

def generate(model,tok,prompt):
 msgs=[{'role':'user','content':'Read the following function signature and docstring, and fully implement the function described. Your response should only contain the code for this function.\n\n'+prompt}]
 x=tok.apply_chat_template(msgs,tokenize=True,return_dict=True,add_generation_prompt=True)
 x={k:torch.tensor(v,device='cuda').unsqueeze(0) for k,v in x.items() if k in ('input_ids','attention_mask')}
 torch.cuda.synchronize(); start=time.perf_counter()
 with torch.inference_mode(): y=model.generate(**x,max_new_tokens=MAXTOK,do_sample=False,use_cache=True,pad_token_id=tok.eos_token_id)
 torch.cuda.synchronize(); secs=time.perf_counter()-start
 n=y.shape[1]-x['input_ids'].shape[1]; reply=tok.decode(y[0,x['input_ids'].shape[1]:],skip_special_tokens=True)
 return reply,secs,n

def eval_one(model_id,adapter=False):
 tok=AutoTokenizer.from_pretrained(MODEL,local_files_only=True)
 m=AutoModelForCausalLM.from_pretrained(MODEL,dtype=torch.bfloat16,device_map='cuda',local_files_only=True,attn_implementation='sdpa')
 if adapter:m=PeftModel.from_pretrained(m,ADAPTER,is_trainable=False)
 m.eval(); p=OUT/f'humaneval-{model_id}.json'; results=[]
 if p.exists():
  old=json.loads(p.read_text(encoding='utf-8')); results=old.get('rows',[])
 byid={r['task_id']:r for r in results}
 ds=load_dataset('openai/openai_humaneval',split='test',revision=REV)
 t0=time.time(); passed=sum(x['passed'] for x in results)
 for i,row in enumerate(ds):
  tid=row['task_id']
  if tid in byid:continue
  reply,secs,ntok=generate(m,tok,row['prompt']); code=extract(reply)
  # Keep only imports produced before a generated definition; the official prompt owns the function header.
  imports=[]
  for line in code.splitlines():
   s=line.strip()
   if s.startswith(('def ','class ')):break
   if re.match(r'^(?:import\s+\S+|from\s+\S+\s+import\s+.+)$',s):imports.append(s)
  body=code
  ix=code.find(':\n    ')
  if ix>=0:body=code[ix+2:]
  program='\n'.join(imports)+'\n'+row['prompt']+body+'\n'+row['test']+'\ncheck('+row['entry_point']+')\n'
  safe,why=inspect_code('\n'.join(imports)+'\n'+body)
  if not safe:ok=False;err=why
  else:ok,err=run_test(program)
  rec={'task_id':tid,'passed':bool(ok),'seconds':secs,'generated_tokens':ntok,'tokens_per_second':ntok/max(secs,1e-6),'safety_screen':why or 'passed','error':err,'completion':reply}
  results.append(rec); byid[tid]=rec; passed+=int(ok)
  if (i+1)%8==0:
   doc={'schema':1,'benchmark':'HumanEval','dataset_revision':REV,'model_id':model_id,'base_revision':'654f9463ce32b05d0429d76fe1f580b27d4c1ac0','adapter':adapter,'max_new_tokens':MAXTOK,'completed':len(results),'total':len(ds),'passed':passed,'pass_at_1':passed/max(1,len(results)),'mean_generation_tokens_per_second':sum(r['tokens_per_second'] for r in results)/len(results),'elapsed_seconds':time.time()-t0,'rows':results}
   tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(doc,indent=2),encoding='utf-8');tmp.replace(p)
   print(f'{model_id}: {len(results)}/{len(ds)} pass={passed} ({passed/max(1,len(results)):.3f}) tok/s={doc["mean_generation_tokens_per_second"]:.1f}',flush=True)
 doc={'schema':1,'benchmark':'HumanEval','dataset_revision':REV,'model_id':model_id,'base_revision':'654f9463ce32b05d0429d76fe1f580b27d4c1ac0','adapter':adapter,'max_new_tokens':MAXTOK,'completed':len(results),'total':len(ds),'passed':passed,'pass_at_1':passed/max(1,len(results)),'mean_generation_tokens_per_second':sum(r['tokens_per_second'] for r in results)/len(results),'elapsed_seconds':time.time()-t0,'rows':results}
 tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(doc,indent=2),encoding='utf-8');tmp.replace(p)
 print(json.dumps({k:v for k,v in doc.items() if k!='rows'},indent=2),flush=True)
 del m,tok;gc.collect();torch.cuda.empty_cache()

eval_one('bf16-base',False)
eval_one('bf16-lora-pilot',True)
