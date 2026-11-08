import React from 'react';
import {useModelRuntime} from './ComposerAction.js';

export const MEMORY_RELEVANCE_POLICY=`[OpenCore memory relevance v1]
Use saved memories only when they directly help answer the current request. Do not mention unrelated memories or force a connection to old projects. A book-writing request, page count, task instructions and temporary topics are task context, not global facts about the user. Treat personal history, places, dates and sports positions as durable facts when explicitly stated by the user. Preserve the user's meaning; never reinterpret football as esports without evidence. Use relevant facts silently when helpful and cite dates accurately when asked. Never announce a memory was saved unless a persistence receipt confirms it.`;
const memoryPolicySessions=new WeakMap();
function withoutLegacyPolicy(text){
  for(const policy of [MEMORY_RELEVANCE_POLICY,MEMORY_RELEVANCE_POLICY.replaceAll('\n','\r\n')]){
    const start=text.indexOf(policy);
    if(start<0)continue;
    const before=text.slice(0,start),after=text.slice(start+policy.length);
    const leading=/(?:\r?\n[ \t]*){2}$/.exec(before);
    const trailing=/^(?:\r?\n[ \t]*){2}/.exec(after);
    if((!before||leading)&&(!after||trailing)){
      return leading?before.slice(0,leading.index)+after:after.slice(trailing?.[0].length||0);
    }
  }
  return text;
}
export function removeLegacyMemoryRelevancePolicy(invoke=window.__TAURI_INTERNALS__?.invoke){
  if(typeof invoke!=='function')return Promise.resolve();
  if(memoryPolicySessions.has(invoke))return memoryPolicySessions.get(invoke);
  const pending=(async()=>{
    const config=await invoke('agent_platform_configuration',{});
    if(typeof config?.systemPrompt!=='string')return;
    const custom=withoutLegacyPolicy(config.systemPrompt);
    if(custom===config.systemPrompt)return;
    const receipt=await invoke('agent_platform_action',{name:'app_control',args:{action:'set',settings:{systemPrompt:custom},source:'OpenCore built-in memory guidance migration'}});
    if(receipt?.persisted!==true)throw Error('Could not migrate the built-in memory guidance.');
  })().catch(error=>{memoryPolicySessions.delete(invoke);throw error;});
  memoryPolicySessions.set(invoke,pending);return pending;
}

function latestCapture(entries){
  const human=entries.filter(row=>row.role==='user'&&row.kind==='message').at(-1);
  return entries.filter(row=>row.title==='Automatic memory'&&(!human||Date.parse(row.timestamp)>=Date.parse(human.timestamp))).at(-1);
}
export function chatPostprocessing(entries){return latestCapture(entries)?.metadata?.status==='checking';}

export function chatSendFailure(reason){
  const detail=String(reason);
  if(/already has an active writer/i.test(detail))return 'This chat’s previous agent session is still open. Your draft is ready to retry after reopening OpenCore.';
  if(/instructions alone do not leave room|active ECHO (?:prompt|task) leaves no room/i.test(detail))return 'The model’s context is full before generation can start. Your draft is saved. Reduce the additional instructions or enabled tools, or choose a model with a larger window.';
  if(/^OpenCore could not resume saved Codex thread /i.test(detail))
    return 'OpenCore Agent could not reopen the local session. Your chat and draft are saved.';
  return 'Message not sent. Your draft is ready to retry. '+detail;
}

export function ChatMemoryStatus({conversationId,entries,runtime,profile=runtime?.profile,active,onRefresh}){
  const h=React.createElement,capture=latestCapture(entries),id=capture?.metadata?.userEntryId;
  const current=useModelRuntime(profile,runtime);
  const[busy,setBusy]=React.useState(false),[error,setError]=React.useState(''),locked=React.useRef(false);
  const service=globalThis.__opencoreMemoryService;
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
