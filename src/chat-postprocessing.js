import React from 'react';

function latestCapture(entries){
  const human=entries.filter(row=>row.role==='user'&&row.kind==='message').at(-1);
  return entries.filter(row=>row.title==='Automatic memory'&&(!human||Date.parse(row.timestamp)>=Date.parse(human.timestamp))).at(-1);
}
export function chatPostprocessing(entries){return latestCapture(entries)?.metadata?.status==='checking';}

export function ChatMemoryStatus({conversationId,entries,runtime,active,onRefresh}){
  const h=React.createElement,capture=latestCapture(entries),id=capture?.metadata?.userEntryId;
  const[busy,setBusy]=React.useState(false),[error,setError]=React.useState(''),attempted=React.useRef(new Set()),locked=React.useRef(false);
  const service=globalThis.__opencoreMemoryService;
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
  React.useEffect(()=>{if(capture?.metadata?.status==='failed'&&runtime?.status==='running'&&!active&&!attempted.current.has(id))void retry();},[id,capture?.metadata?.status,runtime?.status,active]);
  if(busy)return h('div',{className:'chat-postprocessing',role:'status'},'Reply finished · retrying memory capture locally…');
  if(capture?.metadata?.status==='checking')return h('div',{className:'chat-postprocessing',role:'status'},'Reply finished · saving memory locally…');
  if(capture?.metadata?.status!=='failed'||!service)return null;
  return h('div',{className:'chat-postprocessing'},h('span',null,error||'Memory capture failed. Your message is still saved.'),h('button',{type:'button',disabled:active||runtime?.status!=='running',onClick:()=>void retry()},'Retry saving memory'),runtime?.status!=='running'?h('small',null,'Load a text model to retry.'):null);
}
