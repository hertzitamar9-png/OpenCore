import ast,json,re,subprocess,sys,time,threading,urllib.request
from pathlib import Path
ROOT=Path(r'C:\Users\hertz\Documents\Best ai model in the world'); RES=ROOT/'apps/opencore-control-center/src-tauri/resources'; LFM=RES/'lfm'; RUNTIME=RES/'doucode/runtime'
MODEL=ROOT/'artifacts/evaluations/profile-suite-20260926/temp-weights/lfm/LFM2.5-2.6B-Q3.8-TBrilliance-NEO-MAX-Q8_0.gguf'
TASKS=ROOT/'artifacts/evaluations/profile-suite-20260926/dualcore-speed-profile-20260930/selected-hard-humaneval.json'
OUT=ROOT/'artifacts/evaluations/profile-suite-20260926/dualcore-speed-profile-20260930/reasoning-budget-512.json'
BUDGET=512; CTX=32768; MAXTOK=4096
sys.path.insert(0,str(LFM)); sys.path.insert(0,str(RES/'doucode'))
from serve_lfm import create_engine,launch_dual_backbone
from duocore.runtime import wait_healthy,stop_llama_server
engine=None;children=[];lock=threading.Lock();calls=[]
SAFE={'math','re','itertools','collections','heapq','string','functools','operator','datetime','random','bisect','statistics','cmath','typing','numbers','decimal','fractions','array','copy','enum','abc','dataclasses','sys'}
def code_from(text):
 m=re.search(r'```(?:python)?\s*(.*?)```',text,re.S|re.I);return (m.group(1) if m else text).strip()
def safe_code(text):
 try:tree=ast.parse(text)
 except SyntaxError:return False,'syntax_error'
 for n in ast.walk(tree):
  if isinstance(n,(ast.Import,ast.ImportFrom)):
   mods=[a.name.split('.')[0] for a in n.names] if isinstance(n,ast.Import) else [(n.module or '').split('.')[0]]
   if any(x not in SAFE for x in mods):return False,'blocked_import'
  if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in {'open','exec','eval','compile','__import__','breakpoint'}:return False,'blocked_call'
  if isinstance(n,ast.Attribute) and n.attr in {'system','popen','Popen','run','remove','unlink','rmdir','write_text','write_bytes','connect','urlopen'}:return False,'blocked_attribute'
 return True,''
def test_solution(row,text):
 code=code_from(text);imports=[]
 for line in code.splitlines():
  s=line.strip()
  if s.startswith(('def ','class ')):break
  if re.match(r'^(?:import\s+\S+|from\s+\S+\s+import\s+.+)$',s):imports.append(s)
 ix=code.find(':\n    ');body=code[ix+2:] if ix>=0 else code
 ok,why=safe_code(code)
 if not ok:return False,why,code
 program='\n'.join(imports)+'\n'+row['prompt']+body+'\n'+row['test']+'\ncheck('+row['entry_point']+')\n'
 p=OUT.parent/'scratch-human-eval.py';p.write_text(program,encoding='utf-8')
 try:
  q=subprocess.run([sys.executable,'-I',str(p)],cwd=str(OUT.parent),capture_output=True,text=True,timeout=12)
  return q.returncode==0,((q.stderr or q.stdout)[-700:]),code
 except subprocess.TimeoutExpired:return False,'timeout',code
 finally:p.unlink(missing_ok=True)
def count_tokens(text):
 data=json.dumps({'content':text,'add_special':False}).encode();req=urllib.request.Request(engine.brains[0].base_url+'/tokenize',data=data,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=30) as r:return len(json.load(r).get('tokens') or [])
try:
 engine=create_engine('dualcore-echo',MODEL,RUNTIME,CTX,8850)
 for brain in engine.brains:
  child=launch_dual_backbone(RUNTIME,brain,CTX,thinking_budget_tokens=BUDGET);children.append(child);wait_healthy(brain,child,timeout=240)
 for i,b in enumerate(engine.brains,1):
  orig=b.chat
  def traced(*args,_orig=orig,_i=i,**kw):
   start=time.perf_counter();stage='review' if kw.get('json_schema') else 'draft';reply=_orig(*args,**kw);end=time.perf_counter();u=reply.raw.get('usage') or {}
   with lock:calls.append({'brain':_i,'stage':stage,'max_tokens':kw.get('max_tokens'),'seconds':end-start,'prompt_tokens':u.get('prompt_tokens',0),'completion_tokens':u.get('completion_tokens',0),'finish_reason':reply.raw.get('choices',[{}])[0].get('finish_reason','unknown')})
   return reply
  b.chat=traced
 tasks=json.loads(TASKS.read_text(encoding='utf-8')); old_path=ROOT/'artifacts/evaluations/profile-suite-20260926/dualcore-echo-incremental-kv-20260929-human164/graded/grading-summary.json'; old=json.loads(old_path.read_text(encoding='utf-8')); old_scores={x['id']:x['scores'].get('verify')=='C' for x in old['per_sample']}
 results=[]
 for row in tasks:
  calls.clear();messages=[{'role':'user','content':'Read the following function signature and docstring, and fully implement the function described. Your response should only contain the code for this function.\n\n'+row['prompt']}]
  start=time.perf_counter();msg,usage,evidence=engine.complete(messages,None,MAXTOK,temperature=0.0,thinking_budget_tokens=BUDGET);wall=time.perf_counter()-start
  answer=msg.get('content') or '';passed,error,code=test_solution(row,answer);ntok=count_tokens(answer)
  stages={}
  for stage in ('draft','review'):
   xs=[x for x in calls if x['stage']==stage]
   if xs:stages[stage]={'calls':len(xs),'parallel_wall_span_seconds':max(x['seconds'] for x in xs),'completion_tokens_sum':sum(x['completion_tokens'] for x in xs),'items':xs}
  item={'task_id':row['task_id'],'old_passed':old_scores.get(row['task_id']),'budget_512_passed':passed,'test_error':error,'reasoning_chars':len(msg.get('reasoning_content') or ''),'answer_chars':len(answer),'answer_tokens':ntok,'wall_seconds':wall,'answer_tokens_per_second':ntok/max(wall,1e-6),'internal_completion_tokens':usage.get('completion_tokens'),'finish_reason':evidence.get('finish_reason'),'stages':stages,'answer':answer}
  results.append(item);print(json.dumps({k:v for k,v in item.items() if k not in ('answer','stages')},ensure_ascii=False),flush=True)
  OUT.write_text(json.dumps({'schema':1,'status':'measured_sample','model':'DualCore ECHO','checkpoint_sha256':'524dbaa84e6f238f3cc5c8b79c54480f','benchmark':'8 selected hard HumanEval tasks','dataset_revision':'7dce6050a7d6d172f3cc5c32aa97f52fa1a2e544','reasoning_budget_tokens':BUDGET,'max_tokens_per_brain':MAXTOK,'temperature':0.0,'context_tokens':CTX,'results':results},indent=2),encoding='utf-8')
 print(json.dumps({'completed':len(results),'old_passes':sum(x['old_passed'] is True for x in results),'budget512_passes':sum(x['budget_512_passed'] for x in results),'mean_old_seconds':sum(x['wall_seconds'] for x in results)/len(results),'mean_answer_tps':sum(x['answer_tokens_per_second'] for x in results)/len(results),'output':str(OUT)},indent=2),flush=True)
finally:
 if engine is not None:engine.close()
 for child in children:stop_llama_server(child)
