import json
from datasets import load_dataset
ids=[0,1,10,24,32,81,129,130]
ds=load_dataset('openai/openai_humaneval',split='test',revision='7dce6050a7d6d172f3cc5c32aa97f52fa1a2e544')
p=r'C:\Users\hertz\Documents\Best ai model in the world\artifacts\evaluations\profile-suite-20260926\dualcore-speed-profile-20260930\selected-hard-humaneval.json'
import os
os.makedirs(os.path.dirname(p),exist_ok=True)
with open(p,'w',encoding='utf8') as f:json.dump([dict(ds[i],old_index=i) for i in ids],f,ensure_ascii=False,indent=2)
print(json.dumps({'rows':len(ids),'ids':[ds[i]['task_id'] for i in ids],'revision':'7dce6050a7d6d172f3cc5c32aa97f52fa1a2e544','output':p},indent=2))
