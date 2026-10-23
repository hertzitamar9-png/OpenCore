import json,sys,time,threading,urllib.request
from pathlib import Path
ROOT=Path(r'C:\Users\hertz\Documents\Best ai model in the world')
RES=ROOT/'apps/opencore-control-center/src-tauri/resources'
LFM=RES/'lfm'; RUNTIME=RES/'doucode/runtime'
MODEL=ROOT/'artifacts/evaluations/profile-suite-20260926/temp-weights/lfm/LFM2.5-2.6B-Q3.8-TBrilliance-NEO-MAX-Q8_0.gguf'
OUT=ROOT/'artifacts/evaluations/profile-suite-20260926/dualcore-speed-profile-20260930.json'
sys.path.insert(0,str(LFM)); sys.path.insert(0,str(RES/'doucode'))
from serve_lfm import create_engine,launch_dual_backbone
from duocore.runtime import wait_healthy,stop_llama_server
engine=None; children=[]; calls=[]; lock=threading.Lock()
try:
 engine=create_engine('dualcore-echo',MODEL,RUNTIME,32768,8850)
 for brain in engine.brains:
  child=launch_dual_backbone(RUNTIME,brain,32768,thinking_budget_tokens=None); children.append(child); wait_healthy(brain,child,timeout=240)
 t0=time.perf_counter()
 for index,brain in enumerate(engine.brains,1):
  orig=brain.chat
  def traced(*args,_orig=orig,_index=index,**kwargs):
   start=time.perf_counter(); schema=kwargs.get('json_schema'); role='review' if schema else 'draft'
   reply=_orig(*args,**kwargs); end=time.perf_counter(); usage=reply.raw.get('usage') or {}
   with lock:calls.append({'brain':_index,'stage':role,'max_tokens':kwargs.get('max_tokens'),'duration_seconds':end-start,'prompt_tokens':usage.get('prompt_tokens',0),'completion_tokens':usage.get('completion_tokens',0),'finish_reason':reply.raw.get('choices',[{}])[0].get('finish_reason','unknown')})
   return reply
  brain.chat=traced
 request=[{'role':'user','content':'Complete this Python function and return only the function code.\n\ndef has_close_elements(numbers, threshold):\n    """Return True if any two numbers in numbers differ by less than threshold."""'}]
 wall_start=time.perf_counter(); message,usage,evidence=engine.complete(request,None,512,temperature=0.0); wall=time.perf_counter()-wall_start
 answer=message.get('content') or ''
 payload=json.dumps({'content':answer,'add_special':False}).encode()
 req=urllib.request.Request(engine.brains[0].base_url+'/tokenize',data=payload,headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=30) as response: tok=json.load(response); answer_tokens=len(tok.get('tokens') or [])
 except Exception as exc: answer_tokens=None; tokenize_error=f'{type(exc).__name__}: {exc}'
 groups={}
 for stage in ('draft','review'):
  items=[x for x in calls if x['stage']==stage]
  if items:
   groups[stage]={'calls':len(items),'serial_sum_seconds':sum(x['duration_seconds'] for x in items),'parallel_wall_span_seconds':max(x['duration_seconds'] for x in items),'completion_tokens_sum':sum(x['completion_tokens'] for x in items),'items':items}
 report={'schema':1,'status':'measured_smoke','profile':'DualCore ECHO (inference backend; ECHO archive not part of this one-turn profile)','checkpoint_sha256':'524dbaa84e79279977f62978a12f4d97c76e30a2c3976f7f21ed78e73e4b837e','runtime':'llama-server pinned app runtime','context_tokens':32768,'kv_cache':'F16 on GPU per launch_dual_backbone','user_answer_max_tokens':512,'temperature':0.0,'overall_wall_seconds':wall,'selected_answer_tokens':answer_tokens,'selected_answer_tokens_per_second':(answer_tokens/wall if answer_tokens is not None else None),'internal_usage':usage,'execution':evidence,'stages':groups,'answer':answer}
 if 'tokenize_error' in locals():report['tokenize_error']=tokenize_error
 OUT.write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps({k:v for k,v in report.items() if k!='answer'},indent=2),flush=True)
finally:
 if engine is not None:engine.close()
 for child in children:stop_llama_server(child)
