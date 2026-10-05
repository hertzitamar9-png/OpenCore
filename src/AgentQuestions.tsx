import { useEffect,useState } from 'react';
import { listen } from '@tauri-apps/api/event';
import { invoke } from '@tauri-apps/api/core';
import { openUrl } from '@tauri-apps/plugin-opener';
type Field={id:string;title:string;type?:string;secret?:boolean;required?:boolean;options?:{label:string;description?:string}[]};
type Request={requestId:string;conversationId:string;method:string;params:{message?:string;url?:string;questions?:{id:string;question:string;header?:string;isSecret?:boolean;options?:{label:string;description?:string}[]}[];requestedSchema?:{properties?:Record<string,{type?:string;title?:string;description?:string;enum?:unknown[];format?:string}>;required?:string[]}}};
function fields(request:Request):Field[]{
 if(request.params.questions)return request.params.questions.slice(0,16).map(q=>({id:q.id,title:q.question,secret:q.isSecret,options:q.options,required:true}));
 const schema=request.params.requestedSchema;
 return Object.entries(schema?.properties||{}).slice(0,32).map(([id,v])=>({id,title:v.title||v.description||id,type:v.type,secret:v.format==='password',required:schema?.required?.includes(id),options:v.enum?.map(e=>({label:String(e)}))}));
}
export function AgentQuestions(){
 const [queue,setQueue]=useState<Request[]>([]),[values,setValues]=useState<Record<string,string>>({}),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const current=queue[0];
 useEffect(()=>{let stopped=false;const unlisteners:(()=>void)[]=[];
  void listen<Request>('opencore-agent-question-request',({payload})=>{if(!stopped)setQueue(q=>q.some(x=>x.requestId===payload.requestId)?q:[...q,payload]);}).then(stop=>stopped?stop():unlisteners.push(stop));
  void listen<{requestId:string}>('opencore-agent-question-resolved',({payload})=>{if(!stopped)setQueue(q=>q.filter(x=>x.requestId!==payload.requestId));}).then(stop=>stopped?stop():unlisteners.push(stop));
  return()=>{stopped=true;unlisteners.forEach(stop=>stop());};
 },[]);
 useEffect(()=>{setValues({});setError('');},[current?.requestId]);
 if(!current)return null;
 const inputs=fields(current),isMcp=current.method==='mcpServer/elicitation/request';
 const answer=async(cancel=false)=>{setBusy(true);setError('');try{
  let response:unknown;
  if(isMcp){const content:Record<string,unknown>={};for(const f of inputs){const value=values[f.id]||'';if(!cancel&&f.required&&!value)throw new Error(`Enter ${f.title}`);if(!value&&!f.required)continue;
   content[f.id]=f.type==='boolean'?value==='true':f.type==='number'||f.type==='integer'?Number(value):value;
   if((f.type==='number'||f.type==='integer')&&(!Number.isFinite(content[f.id])||(f.type==='integer'&&!Number.isInteger(content[f.id]))))throw new Error(`${f.title} needs a valid ${f.type}`);
  }response={action:cancel?'cancel':'accept',content:cancel?null:content};}
  else {const answers:Record<string,{answers:string[]}>={};for(const f of inputs){const value=values[f.id]||'';if(!cancel&&!value)throw new Error(`Answer ${f.title}`);answers[f.id]={answers:cancel?[]:[value]};}response={answers};}
  await invoke('answer_agent_question',{requestId:current.requestId,conversationId:current.conversationId,response});setQueue(q=>q.filter(x=>x.requestId!==current.requestId));
 }catch(e){setError(String(e));}finally{setBusy(false);}};
 return <div className="dialog-backdrop"><section className="opencore-dialog" role="dialog" aria-modal="true" aria-label="OpenCore needs input"><h2>OpenCore needs input</h2><p>{current.params.message||'Answer this question to continue the current task.'}</p>
 {current.params.url&&/^https?:\/\//.test(current.params.url)&&<button onClick={()=>void openUrl(current.params.url!).catch(e=>setError(String(e)))}>Open requested sign-in page</button>}
 {inputs.map(f=><label key={f.id} className="appearance-label">{f.title}{f.options?.length?<><input aria-label={f.title} list={`agent-options-${f.id}`} type={f.secret?'password':'text'} value={values[f.id]||''} onChange={e=>setValues({...values,[f.id]:e.target.value})}/><datalist id={`agent-options-${f.id}`}>{f.options.map(o=><option key={o.label} value={o.label}>{o.description}</option>)}</datalist></>:f.type==='boolean'?<select aria-label={f.title} value={values[f.id]||''} onChange={e=>setValues({...values,[f.id]:e.target.value})}><option value="">Choose…</option><option value="true">Yes</option><option value="false">No</option></select>:<input aria-label={f.title} type={f.secret?'password':f.type==='integer'||f.type==='number'?'number':'text'} value={values[f.id]||''} onChange={e=>setValues({...values,[f.id]:e.target.value})}/>}</label>)}
 {error&&<p role="alert">{error}</p>}<div className="dialog-actions"><button disabled={busy} onClick={()=>void answer(true)}>Cancel</button><button disabled={busy} onClick={()=>void answer()}>Submit</button></div></section></div>;
}
