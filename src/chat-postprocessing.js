import React from 'react';

export const MEMORY_RELEVANCE_POLICY=`[OpenCore memory relevance v1]
Use saved memories only when they directly help answer the current request. Do not mention unrelated memories or force a connection to old projects. A book-writing request, page count, task instructions and temporary topics are task context, not global facts about the user. Treat personal history, places, dates and sports positions as durable facts when explicitly stated by the user. Preserve the user's meaning; never reinterpret football as esports without evidence. Use relevant facts silently when helpful and cite dates accurately when asked. Never announce a memory was saved unless a persistence receipt confirms it.`;
const memoryPolicySessions=new WeakMap();
export function ensureMemoryRelevancePolicy(invoke=window.__TAURI_INTERNALS__?.invoke){
  if(typeof invoke!=='function')return Promise.resolve();
  if(memoryPolicySessions.has(invoke))return memoryPolicySessions.get(invoke);
  const pending=(async()=>{
    const config=await invoke('agent_platform_configuration',{});
    if(typeof config?.systemPrompt!=='string'||config.memoryEnabled===false||config.systemPrompt.includes('[OpenCore memory relevance v1]'))return;
    const prompt=[config.systemPrompt.trim(),MEMORY_RELEVANCE_POLICY].filter(Boolean).join('\n\n');
    if(prompt.length>32768)return;
    const receipt=await invoke('agent_platform_action',{name:'app_control',args:{action:'set',settings:{systemPrompt:prompt},source:'OpenCore memory relevance default'}});
    if(receipt?.persisted!==true)throw Error('The memory relevance preference was not saved.');
  })().catch(error=>{memoryPolicySessions.delete(invoke);throw error;});
  memoryPolicySessions.set(invoke,pending);return pending;
}

function latestCapture(entries){
  const human=entries.filter(row=>row.role==='user'&&row.kind==='message').at(-1);
  return entries.filter(row=>row.title==='Automatic memory'&&(!human||Date.parse(row.timestamp)>=Date.parse(human.timestamp))).at(-1);
}
export function chatPostprocessing(entries){return latestCapture(entries)?.metadata?.status==='checking';}

export function ChatMemoryStatus({conversationId,entries,runtime,active,onRefresh}){
  const h=React.createElement,capture=latestCapture(entries),id=capture?.metadata?.userEntryId;
  const[busy,setBusy]=React.useState(false),[error,setError]=React.useState(''),attempted=React.useRef(new Set()),locked=React.useRef(false);
  const service=globalThis.__opencoreMemoryService;
  React.useEffect(()=>{void ensureMemoryRelevancePolicy().catch(()=>{});},[]);
  const reviewable=capture?.metadata?.status==='failed'||capture?.metadata?.status==='deferred'||(capture?.metadata?.status==='unchanged'&&capture?.metadata?.skippedCount>0);
  async function retry(){
    if(locked.current||active||!service||!id)return;
    locked.current=true;setBusy(true);setError('');attempted.current.add(id);
    try{
      let proof=await service('recover',{conversationId,userEntryId:id,temporaryChat:false,runtime});
      for(let count=0;proof.status==='ready'&&count<5;count++){
        proof=await service('revalidate',{proof,runtime});
        if(proof.status!=='ready')break;
        const result=await window.__TAURI_INTERNALS__.invoke('agent_platform_action',{name:'agent_memory',args:proof.candidates[0].recordArgs});
        if(result?.persisted!==true)throw Error('Memory persistence was not confirmed.');
      }
      await onRefresh?.();
      if(!['ready','already_recorded','no_facts'].includes(proof.status))throw Error(proof.message||proof.error?.message||proof.error||'Memory could not be saved. Retry when the text model is idle.');
    }catch(reason){setError(String(reason).replace(/^Error:\s*/,''));}
    finally{locked.current=false;setBusy(false);}
  }
  React.useEffect(()=>{if(reviewable&&runtime?.status==='running'&&!active&&!attempted.current.has(id))void retry();},[id,reviewable,runtime?.status,active]);
  if(busy)return h('div',{className:'chat-postprocessing',role:'status'},'Reply finished · retrying memory capture locally…');
  if(capture?.metadata?.status==='checking')return h('div',{className:'chat-postprocessing',role:'status'},'Reply finished · saving memory locally…');
  if(!reviewable||!service)return null;
  return h('div',{className:'chat-postprocessing'},h('span',null,'Your reply is saved. Automatic memory needs another attempt.'),h('button',{type:'button',disabled:active||runtime?.status!=='running',onClick:()=>void retry()},'Retry saving memory'),error?h('details',null,h('summary',null,'Memory details'),h('small',null,error)):null,runtime?.status!=='running'?h('small',null,'Load a text model to retry.'):null);
}
