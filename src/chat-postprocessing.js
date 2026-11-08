import React from 'react';
import {useModelRuntime} from './ComposerAction.js';

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

export function ChatMemoryStatus({conversationId,entries,runtime,profile=runtime?.profile,active,onRefresh}){
  const h=React.createElement,capture=latestCapture(entries),id=capture?.metadata?.userEntryId;
  const current=useModelRuntime(profile,runtime);
  const[busy,setBusy]=React.useState(false),[error,setError]=React.useState(''),locked=React.useRef(false);
  const service=globalThis.__opencoreMemoryService;
  React.useEffect(()=>{void ensureMemoryRelevancePolicy().catch(()=>{});},[]);
  const reviewable=capture?.metadata?.status==='failed'||capture?.metadata?.status==='deferred'||(capture?.metadata?.status==='unchanged'&&capture?.metadata?.skippedCount>0);
  async function retry(){
    if(locked.current||active||!service||!id||current?.status!=='running')return;
    locked.current=true;setBusy(true);setError('');
    try{
      let proof=await service('recover',{conversationId,userEntryId:id,temporaryChat:false,runtime:current});
      for(let count=0;proof.status==='ready'&&count<5;count++){
        proof=await service('revalidate',{proof,runtime:current});
        if(proof.status!=='ready')break;
        const result=await window.__TAURI_INTERNALS__.invoke('agent_platform_action',{name:'agent_memory',args:proof.candidates[0].recordArgs});
        if(result?.persisted!==true)throw Error('Memory persistence was not confirmed.');
      }
      await onRefresh?.();
      if(!['ready','already_recorded','no_facts'].includes(proof.status))throw Error(proof.message||proof.error?.message||proof.error||'Memory could not be saved. Retry when the text model is idle.');
    }catch(reason){setError(String(reason).replace(/^Error:\s*/,''));}
    finally{locked.current=false;setBusy(false);}
  }
  if(busy)return h('div',{className:'chat-postprocessing',role:'status'},'Reply finished · retrying memory capture locally…');
  if(capture?.metadata?.status==='checking'&&current?.status!=='stopped')return h('div',{className:'chat-postprocessing',role:'status'},'Reply finished · saving memory locally…');
  if(!reviewable||!service||current?.status!=='running')return null;
  return h('details',{className:'chat-postprocessing'},h('summary',null,'Memory details'),
    h('button',{type:'button',disabled:active,onClick:()=>void retry()},'Retry saving memory'),
    h('small',null,error||capture.metadata?.error||capture.content));
}
