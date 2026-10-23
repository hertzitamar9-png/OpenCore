import json,re,ast
p=r'C:\Users\hertz\Documents\Best ai model in the world\artifacts\evaluations\profile-suite-20260926\lfm-bf16-source-smoke-20260930\humaneval-bf16-base-nothink.json'
x=json.load(open(p,encoding='utf8'))
for r in x['rows'][:2]:
 t=r['completion']; m=re.search(r'```(?:python)?\s*(.*?)```',t,re.S|re.I); code=(m.group(1) if m else t).strip()
 try: ast.parse(code); print(r['task_id'],'parse ok',repr(code[:80]))
 except Exception as e:print(r['task_id'],e,repr(code[:100]))
