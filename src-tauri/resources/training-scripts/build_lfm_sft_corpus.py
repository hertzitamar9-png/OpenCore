import hashlib,heapq,json,sys
from pathlib import Path

sources=[
 ('coding',Path(r'C:\Users\hertz\AppData\Local\OpenCore\training-data\coding-corpus-sanitized-20260930.jsonl'),3000,250),
 ('agentic',Path(r'C:\Users\hertz\Documents\Best ai model in the world\data\agentic-corpus.jsonl'),1500,150),
 ('general',Path(r'C:\Users\hertz\Documents\Best ai model in the world\data\phase3-corpus.jsonl'),1000,150),
 ('gamedev',Path(r'C:\Users\hertz\Documents\Best ai model in the world\data\gamedev-corpus.jsonl'),1000,100),
 ('concise',Path(r'C:\Users\hertz\Documents\Best ai model in the world\data\nohedge-95k.jsonl'),1000,100),
 ('stop',Path(r'C:\Users\hertz\Documents\Best ai model in the world\data\stop-when-done-v1-train.jsonl'),1000,100),
 ]
out=Path(r'C:\Users\hertz\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\OpenCore\training-data'); audit=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
if audit.get('passed') is not True or audit.get('prompt_overlap_count') or audit.get('protected_answer_or_test_overlap_count'):
 raise SystemExit('refusing unclean or failed contamination audit')
source_hashes={Path(x['path']).name:x['sha256'] for x in audit['training_files']}
seen=set(); train=[]; val=[]; counts={}
for source,path,train_n,val_n in sources:
 k=train_n+val_n; heap=[]; local=set(); scanned=valid=0
 with path.open(encoding='utf-8') as f:
  for line_no,raw in enumerate(f,1):
   if not raw.strip(): continue
   row=json.loads(raw); scanned+=1
   if row.get('training_eligible') is False: continue
   messages=row.get('messages')
   if isinstance(messages,str):
    try: messages=json.loads(messages)
    except Exception: continue
   if not isinstance(messages,list): continue
   cleaned=[]
   for m in messages:
    if not isinstance(m,dict): continue
    role=m.get('role'); content=m.get('content')
    if role not in ('system','developer','user','assistant','tool') or not isinstance(content,str) or not content.strip(): continue
    cleaned.append({'role':'system' if role=='developer' else role,'content':content})
   if len(cleaned)<2 or not any(m['role']=='assistant' for m in cleaned): continue
   signature=hashlib.sha256(json.dumps(cleaned,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
   if signature in local: continue
   local.add(signature); valid+=1
   rank=int(hashlib.sha256(f'lfm-sft-20260930:{source}:{signature}'.encode()).hexdigest()[:16],16)
   item=(rank,signature,cleaned)
   if len(heap)<k: heapq.heappush(heap,(-rank,signature,cleaned))
   elif rank < -heap[0][0]: heapq.heapreplace(heap,item if False else (-rank,signature,cleaned))
 chosen=sorted([(-r,s,m) for r,s,m in heap],key=lambda x:x[0])
 selected_val=[]; selected_train=[]
 for _,signature,messages in chosen:
  if signature in seen: continue
  seen.add(signature)
  if len(selected_val)<val_n: selected_val.append({'messages':messages,'category':source})
  elif len(selected_train)<train_n: selected_train.append({'messages':messages,'category':source})
 if len(selected_train)<train_n or len(selected_val)<val_n: raise SystemExit(f'{source}: dedupe reduced below quota {len(selected_train)}/{train_n}, {len(selected_val)}/{val_n}')
 train.extend(selected_train); val.extend(selected_val); counts[source]={'scanned':scanned,'eligible_unique':valid,'train':len(selected_train),'validation':len(selected_val),'sha256':source_hashes.get(path.name)}
for name,rows in [('lfm-balanced-train-20260930.jsonl',train),('lfm-balanced-val-20260930.jsonl',val)]:
 p=out/name
 with p.open('x',encoding='utf-8',newline='\n') as f:
  for row in rows: f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
manifest={'schema_version':1,'seed':'lfm-sft-20260930','train_rows':len(train),'validation_rows':len(val),'categories':counts,'contamination_audit':str(Path(sys.argv[1])),'benchmarks':['HumanEval','MBPP','LiveBench coding'],'excluded_sources':{'skill-gap-v4-train.jsonl':'omitted because rows were duplicates of earlier selected sources'},'status':'ready_for_training_probe'}
(out/'lfm-balanced-corpus-manifest-20260930.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps(manifest,indent=2))
