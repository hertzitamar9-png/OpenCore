"""Offline multilingual CPU embeddings and syntax evidence. No chat/GPU model."""
import argparse,json,os,time
from pathlib import Path

MODEL_REVISION='2c4055b12046f11709e9df2c122e59ffbdc2f900'

class Embedder:
    def __init__(self,root):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np=np
        root=Path(root)
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        options.log_severity_level=3
        self.session=ort.InferenceSession(str(root/'model_quantized.onnx'),options,providers=['CPUExecutionProvider'])
        self.tokenizer=Tokenizer.from_file(str(root/'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=128);self.tokenizer.enable_padding()
    def encode(self,texts):
        if not isinstance(texts,list) or not 1<=len(texts)<=32 or any(not isinstance(t,str) or len(t)>8000 for t in texts):raise ValueError('Expected 1–32 bounded texts')
        tokens=self.tokenizer.encode_batch(texts)
        np=self.np
        inputs={'input_ids':np.array([t.ids for t in tokens],dtype=np.int64),'attention_mask':np.array([t.attention_mask for t in tokens],dtype=np.int64),'token_type_ids':np.array([t.type_ids for t in tokens],dtype=np.int64)}
        values=self.session.run(None,{item.name:inputs[item.name] for item in self.session.get_inputs()})[0]
        mask=inputs['attention_mask'][...,None]
        pooled=(values*mask).sum(axis=1)/np.maximum(mask.sum(axis=1),1)
        pooled=pooled/np.maximum(np.linalg.norm(pooled,axis=1,keepdims=True),1e-12)
        if pooled.shape!=(len(texts),384) or not np.isfinite(pooled).all():raise ValueError('Embedding model returned invalid vectors')
        return pooled.astype(np.float32).tolist()

def inspect_code(language,content):
    if language not in ('python','rust','javascript','typescript','tsx'):raise ValueError('Unsupported syntax language')
    if not isinstance(content,str) or len(content.encode())>131072:raise ValueError('Source exceeds the syntax batch limit')
    import tree_sitter
    if language in ('typescript','tsx'):
        import tree_sitter_typescript as grammar
        capsule=grammar.language_tsx() if language=='tsx' else grammar.language_typescript()
    else:
        grammar=__import__('tree_sitter_'+language);capsule=grammar.language()
    parser=tree_sitter.Parser(tree_sitter.Language(capsule))
    raw=content.encode();tree=parser.parse(raw);symbols=[];edges=[];visited=0
    definitions={'function_item','function_declaration','function_definition','method_definition','class_definition','class_declaration','interface_declaration','struct_item','enum_item','trait_item','type_alias_declaration','method_signature'}
    imports={'import_statement','import_from_statement','use_declaration','import_declaration'}
    def text(node):return raw[node.start_byte:node.end_byte].decode(errors='replace')
    def visit(node,caller=''):
        nonlocal visited
        visited+=1
        if visited>12000:return
        if node.type in definitions:
            name=node.child_by_field_name('name')
            if name is not None:
                caller=text(name)[:200]
                if len(symbols)<256:symbols.append({'name':caller,'kind':node.type,'line':node.start_point.row+1})
        if node.type in ('call_expression','call') and len(edges)<512:
            target=node.child_by_field_name('function')
            if target is not None:edges.append({'caller':caller,'target':text(target)[:200],'kind':'call','line':node.start_point.row+1})
        elif node.type in imports and len(edges)<512:edges.append({'caller':caller,'target':text(node)[:300],'kind':'import','line':node.start_point.row+1})
        for child in node.named_children:visit(child,caller)
    visit(tree.root_node)
    return {'symbols':symbols,'edges':edges,'parseErrors':tree.root_node.has_error,'truncated':visited>12000,'relationshipMeaning':'Syntax evidence; dynamic dispatch and cross-file target resolution are not guaranteed.'}

def serve(root,port):
    from http.server import BaseHTTPRequestHandler,HTTPServer
    import hmac
    token=os.environ.pop('OPENCORE_MEMORY_WORKER_TOKEN')
    embedder=Embedder(root)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):self.send_error(403);return
            self.connection.settimeout(5)
            try:size=int(self.headers.get('Content-Length','0'))
            except ValueError:self.send_error(400);return
            if not 0<size<=200000:self.send_error(413);return
            try:
                request=json.loads(self.rfile.read(size));start=time.perf_counter()
                if self.path=='/embed':result={'vectors':embedder.encode(request['texts']),'revision':MODEL_REVISION}
                elif self.path=='/inspect':result=inspect_code(request['language'],request['content'])
                elif self.path=='/health':result={'ready':True,'revision':MODEL_REVISION,'provider':'CPUExecutionProvider','threads':2}
                else:raise ValueError('Unknown memory worker operation')
                result['elapsedMs']=round((time.perf_counter()-start)*1000,2)
                body=json.dumps(result,ensure_ascii=True,allow_nan=False).encode();self.send_response(200)
            except Exception as error:
                body=json.dumps({'error':str(error)[:500]}).encode();self.send_response(400)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    HTTPServer(('127.0.0.1',port),Handler).serve_forever()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--port',type=int);parser.add_argument('--verify',action='store_true');args=parser.parse_args()
    if args.verify:
        vectors=Embedder(args.root).encode(['A small red ceramic cube.','A little red pottery cube.'])
        similarity=sum(a*b for a,b in zip(*vectors))
        if similarity<0.5:raise RuntimeError('CPU embedding verification failed')
        samples={'python':'def hello():\n    return world()\n','rust':'fn hello(){world();}','javascript':'function hello(){world();}','typescript':'function hello(): void {world();}','tsx':'function Hello(){return <div/>;}'}
        for language,source in samples.items():
            parsed=inspect_code(language,source)
            if parsed['parseErrors'] or not parsed['symbols']:raise RuntimeError('Syntax verification failed for '+language)
        print(json.dumps({'syntaxVerified':True,'embeddingVerified':True,'dimensions':384,'similarity':similarity,'revision':MODEL_REVISION}))
    else:serve(args.root,args.port)
