from transformers import AutoTokenizer
p=r'''C:\Users\hertz\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\OpenCore\training-models\LiquidAI-LFM2.5-2.6B-654f9463ce32b05d0429d76fe1f580b27d4c1ac0'''
t=AutoTokenizer.from_pretrained(p,local_files_only=True)
print('chat_template',bool(t.chat_template),'eos',t.eos_token_id,'pad',t.pad_token_id)
for kw in ({'tokenize':True,'return_dict':True,'return_assistant_tokens_mask':True},{'tokenize':True,'return_dict':True}):
 try:
  x=t.apply_chat_template([{'role':'user','content':'say hi'},{'role':'assistant','content':'hello'}],**kw)
  print('keys',getattr(x,'keys',lambda:[])(),'len',len(x['input_ids']),'mask',x.get('assistant_masks'))
 except Exception as e: print(type(e).__name__,str(e)[:300])
