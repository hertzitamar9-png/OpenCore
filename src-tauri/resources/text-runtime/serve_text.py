"""Managed offline text serving. No downloaded Python, disk offload, or background downloads."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))


def load(directory,context,precision='auto',device_preference='auto',experimental_fast=False):
    import psutil
    import torch
    from transformers import AutoConfig,AutoTokenizer,AutoModelForCausalLM,AutoModelForImageTextToText
    torch.set_num_threads(2)
    directory=Path(directory)
    raw=json.loads((directory/'config.json').read_text(encoding='utf-8'))
    if raw.get('model_type')=='gptx3':
        from gptx3.configuration_gptx3 import GPTX3Config
        from gptx3.modeling_gptx3 import GPTX3ForCausalLM
        config=GPTX3Config.from_pretrained(directory,local_files_only=True)
        config.use_fla=False
        config.opencore_fast_mode=experimental_fast
        cls=GPTX3ForCausalLM
    elif raw.get('model_type')=='spark2_5':
        if experimental_fast:raise RuntimeError('Experimental layer skipping has not been verified for Spark-X2.5')
        from spark.configuration_spark import Spark2_5Config
        from spark.modeling_spark import Spark2_5ForCausalLM
        config=Spark2_5Config.from_pretrained(directory,local_files_only=True)
        cls=Spark2_5ForCausalLM
    elif raw.get('model_type')=='edgerock':
        if experimental_fast:raise RuntimeError('EdgeRock uses its fixed-memory architecture; experimental layer skipping is not supported')
        from edgerock.configuration_edgerock import EdgeRockConfig
        from edgerock.modeling_edgerock import EdgeRockForCausalLM
        config=EdgeRockConfig.from_pretrained(directory,local_files_only=True)
        config.backend='torch'
        config.prefill_chunk_size=256
        cls=EdgeRockForCausalLM
    else:
        if experimental_fast:raise RuntimeError('Experimental layer skipping is not implemented for this architecture')
        config=AutoConfig.from_pretrained(directory,local_files_only=True,trust_remote_code=False)
        cls=AutoModelForImageTextToText if any('ConditionalGeneration' in name for name in raw.get('architectures',[])) else AutoModelForCausalLM
    declared=getattr(config,'max_position_embeddings',None) or getattr(getattr(config,'text_config',None),'max_position_embeddings',None) or context
    context=min(int(declared),int(context))
    files=list(directory.glob('*.safetensors'))
    if not files:raise RuntimeError('The selected package has no safetensors checkpoint')
    weight_bytes=sum(path.stat().st_size for path in files)
    required=weight_bytes+max(512*1024**2,weight_bytes//10)
    if raw.get('model_type')=='edgerock':
        # Publisher BF16 measurements include several GiB of execution buffers;
        # the 0.1 GiB recurrent history is not the total GPU footprint.
        required=weight_bytes+8*1024**3
    device='cpu'
    if device_preference!='cpu' and torch.cuda.is_available() and torch.cuda.mem_get_info()[0]>required:device='cuda'
    if device_preference=='cuda' and device!='cuda':raise RuntimeError('The selected GPU does not have enough available memory for this checkpoint')
    if raw.get('model_type')=='edgerock' and device=='cpu':
        required=weight_bytes*2+1024**3
        if precision=='auto':precision='fp32'
    if device=='cpu' and psutil.virtual_memory().available<required:
        raise RuntimeError(f'Not enough available RAM for this checkpoint: approximately {required/1e9:.2f} GB including preparation headroom. Disk offload is disabled.')
    dtype={'fp32':torch.float32,'bf16':torch.bfloat16,'fp16':torch.float16}.get(precision,'auto')
    tokenizer=AutoTokenizer.from_pretrained(directory,local_files_only=True,trust_remote_code=False)
    if raw.get('model_type')=='edgerock':
        tokenizer.chat_template=(ROOT/'edgerock/chat_template.jinja').read_text(encoding='utf-8')
        tokenizer._opencore_edgerock=True
    safe_loading={} if raw.get('model_type') in ('gptx3','edgerock','spark2_5') else {'trust_remote_code':False}
    model=cls.from_pretrained(directory,config=config,local_files_only=True,**safe_loading,
        dtype=dtype,low_cpu_mem_usage=True,attn_implementation='eager').eval().to(device)
    if raw.get('model_type')=='edgerock':
        # The model chunks prefill itself. Transformers' similarly named option
        # expects an externally provided cache and otherwise rejects generation.
        model.generation_config.prefill_chunk_size=None
    return model,tokenizer,context,device


def messages_prompt(tokenizer,payload):
    if isinstance(payload.get('prompt'),str):return payload['prompt']
    messages=payload.get('messages')
    if not isinstance(messages,list) or not messages:raise ValueError('Supply messages or a text prompt')
    clean=[]
    for message in messages:
        content=message.get('content','')
        if isinstance(content,list):
            if any(not isinstance(part,dict) or part.get('type')!='text' for part in content):
                raise ValueError('This managed worker supports text only; image and audio inputs require their task runtime')
            content='\n'.join(part.get('text','') for part in content)
        if not isinstance(content,str):raise ValueError('This worker accepts text messages')
        clean.append({'role':message.get('role','user'),'content':content})
    if payload.get('tools'):
        clean.insert(0,{'role':'system','content':'Available tool schemas: '+json.dumps(payload['tools'],ensure_ascii=False)})
    if getattr(tokenizer,'chat_template',None):
        return tokenizer.apply_chat_template(clean,tokenize=False,add_generation_prompt=True,enable_thinking=payload.get('reasoning_budget_tokens')!=0)
    # A base language model has no instruction template. Do not pretend it was chat-trained.
    return '\n'.join(message['content'] if message['role']=='user' else message['role']+': '+message['content'] for message in clean)


def generation_inputs(model,tokenizer,context,payload,stop):
    from transformers import StoppingCriteria,StoppingCriteriaList
    class Cancelled(StoppingCriteria):
        def __call__(self,input_ids,scores,**kwargs):return stop.is_set()
    prompt=messages_prompt(tokenizer,payload)
    encoded=tokenizer(prompt,return_tensors='pt')
    length=encoded['input_ids'].shape[-1]
    if length>=context:raise ValueError(f'The prompt has {length} tokens; the configured window is {context}')
    maximum=min(max(1,int(payload.get('max_tokens',payload.get('n_predict',512)))),context-length,8192)
    temperature=float(payload.get('temperature',0.7))
    if not 0<=temperature<=5:raise ValueError('Temperature must be from zero to five')
    args={**{key:value.to(model.device) for key,value in encoded.items()},'max_new_tokens':maximum,
          'do_sample':temperature>0,'stopping_criteria':StoppingCriteriaList([Cancelled()]),
          'pad_token_id':tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id}
    if temperature>0:args.update(temperature=temperature,top_p=float(payload.get('top_p',0.95)))
    if 'top_k' in payload and temperature>0:args['top_k']=int(payload['top_k'])
    if 'repeat_penalty' in payload:args['repetition_penalty']=float(payload['repeat_penalty'])
    return args,length


def verify(model,tokenizer,context,model_id,pin):
    import torch
    args,count=generation_inputs(model,tokenizer,context,{'prompt':'The sky is','max_tokens':8,'temperature':0},threading.Event())
    with torch.inference_mode():
        forward=model(**{k:v for k,v in args.items() if k in ('input_ids','attention_mask')})
        if not torch.isfinite(forward.logits[:,-1]).all():raise RuntimeError('The installed model produced non-finite logits')
        output=model.generate(**args)
    generated=output.shape[-1]-count
    sample=tokenizer.decode(output[0,count:],skip_special_tokens=True)
    if generated<1 or not sample.strip():raise RuntimeError('The installed model produced no visible test output')
    return {'modelId':model_id,'sourcePin':pin,'executionVerified':True,'generatedTokens':generated,
            'device':str(model.device),'contextTokens':context,'sample':sample}


def serve(model,tokenizer,context,model_id,port,source_pin=''):
    import torch
    from transformers import TextIteratorStreamer
    class MeasuredStreamer(TextIteratorStreamer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.generated_tokens=0
            self.first_token_at=None
        def put(self,value):
            if not (self.skip_prompt and self.next_tokens_are_prompt):
                self.generated_tokens+=value.numel()
                if self.first_token_at is None:self.first_token_at=time.perf_counter()
            super().put(value)
    active=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send_json(self,status,value):
            body=json.dumps(value,ensure_ascii=True,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path=='/health':self.send_json(200,{'status':'ok','model':model_id,'contextTokens':context,'experimentalFast':bool(getattr(model.config,'opencore_fast_mode',False))})
            elif self.path=='/v1/models':self.send_json(200,{'object':'list','data':[{'id':model_id,'object':'model','owned_by':'local'}]})
            elif self.path=='/props':self.send_json(200,{'n_ctx':context,'model':model_id,'model_fingerprint':source_pin,'tokenizer_fingerprint':source_pin,'cache_append_supported':False,'reasoning_min_tokens':False})
            else:self.send_json(404,{'error':'Unknown endpoint'})
        def do_POST(self):
            if self.path=='/tokenize':
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=8*1024**2:raise ValueError('Tokenization request exceeds the limit')
                    content=json.loads(self.rfile.read(size)).get('content')
                    if not isinstance(content,str):raise ValueError('Supply text content to tokenize')
                    self.send_json(200,{'tokens':tokenizer.encode(content,add_special_tokens=False)})
                except Exception as error:self.send_json(400,{'error':str(error)[:1000]})
                return
            if self.path not in ('/v1/chat/completions','/v1/completions','/completion'):
                self.send_json(404,{'error':'Unknown endpoint'});return
            if not active.acquire(blocking=False):self.send_json(409,{'error':'This local model is already generating'});return
            stop=threading.Event();worker=None;stream_started=False;started=time.perf_counter()
            try:
                self.connection.settimeout(30)
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=8*1024**2:raise ValueError('Request exceeds the text worker limit')
                payload=json.loads(self.rfile.read(size));args,prompt_tokens=generation_inputs(model,tokenizer,context,payload,stop)
                if 'seed' in payload:torch.manual_seed(int(payload['seed']))
                chat=self.path=='/v1/chat/completions';request_id='chatcmpl-'+uuid.uuid4().hex;created=int(time.time())
                if not payload.get('stream'):
                    with torch.inference_mode():output=model.generate(**args)
                    tokens=output[0,prompt_tokens:];text=tokenizer.decode(tokens,skip_special_tokens=True)
                    choice={'index':0,'finish_reason':'stop',**({'message':{'role':'assistant','content':text}} if chat else {'text':text})}
                    result={'id':request_id,'object':'chat.completion' if chat else 'text_completion','created':created,'model':model_id,'choices':[choice],
                            'usage':{'prompt_tokens':prompt_tokens,'completion_tokens':len(tokens),'total_tokens':prompt_tokens+len(tokens)}}
                    if self.path=='/completion':result.update(content=text,tokens_predicted=len(tokens),tokens_evaluated=prompt_tokens,stop=True)
                    self.send_json(200,result);return
                streamer=MeasuredStreamer(tokenizer,skip_prompt=True,skip_special_tokens=True,timeout=120)
                failure=[]
                def generate():
                    try:
                        with torch.inference_mode():model.generate(**args,streamer=streamer)
                    except Exception as error:
                        failure.append(str(error));streamer.end()
                worker=threading.Thread(target=generate,daemon=True);worker.start()
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.end_headers();stream_started=True
                for text in streamer:
                    if not text:continue
                    value={'id':request_id,'object':'chat.completion.chunk','created':created,'model':model_id,
                           'choices':[{'index':0,'finish_reason':None,**({'delta':{'content':text}} if chat else {'text':text})}]}
                    if self.path=='/completion':value.update(content=text,stop=False)
                    self.wfile.write(('data: '+json.dumps(value)+'\n\n').encode());self.wfile.flush()
                if failure:raise RuntimeError(failure[0])
                count=streamer.generated_tokens;ended=time.perf_counter()
                first=streamer.first_token_at or ended
                prompt_seconds=max(first-started,0.000001);decode_seconds=max(ended-first,0.000001)
                final={'id':request_id,'model':model_id,'choices':[{'index':0,'finish_reason':'length' if count>=args['max_new_tokens'] else 'stop',**({'delta':{}} if chat else {'text':''})}],
                       'usage':{'prompt_tokens':prompt_tokens,'completion_tokens':count,'total_tokens':prompt_tokens+count,'completion_tokens_details':{'reasoning_tokens':0}},
                       'timings':{'prompt_ms':prompt_seconds*1000,'predicted_ms':decode_seconds*1000,'prompt_per_second':prompt_tokens/prompt_seconds,'predicted_per_second':max(count-1,0)/decode_seconds}}
                self.wfile.write(('data: '+json.dumps(final)+'\n\ndata: [DONE]\n\n').encode());self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError,TimeoutError):stop.set()
            except Exception as error:
                if not stream_started:self.send_json(400,{'error':str(error)[:1000]})
            finally:
                stop.set()
                if worker:worker.join(timeout=10)
                # A non-responsive model must not overlap a second generation.
                if worker and worker.is_alive():
                    import os
                    os._exit(2)
                active.release()
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--model-dir',type=Path,required=True);parser.add_argument('--model-id',required=True)
    parser.add_argument('--context',type=int,default=8192);parser.add_argument('--port',type=int,default=8813)
    parser.add_argument('--device',choices=['auto','cpu','cuda'],default='auto')
    parser.add_argument('--precision',choices=['auto','fp32','bf16','fp16'],default='auto');parser.add_argument('--verify',type=Path);parser.add_argument('--source-pin',default='')
    parser.add_argument('--experimental-fast',action='store_true',help='Experimental GPT-X3 recurrent layer skipping; can reduce quality. Saved weights stay unchanged.')
    args=parser.parse_args();model,tokenizer,context,device=load(args.model_dir,args.context,args.precision,args.device,args.experimental_fast)
    if args.verify:
        proof=verify(model,tokenizer,context,args.model_id,args.source_pin);args.verify.parent.mkdir(parents=True,exist_ok=True);args.verify.write_text(json.dumps(proof,ensure_ascii=True),encoding='utf-8');print(json.dumps(proof))
    else:serve(model,tokenizer,context,args.model_id,args.port,args.source_pin)
