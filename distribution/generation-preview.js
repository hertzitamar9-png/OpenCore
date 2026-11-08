// Keep model-emitted items until their persisted copies are visible. Checkpoints
// separate passes; a new pass must not overwrite the previous pass's preview.
function $generationRevision(payload){
  const receipt=$activityReceipt(payload.conversationId);
  receipt.runId=payload.runId;
  return receipt.revision=(receipt.revision||0)+1;
}
function $settleGenerationPreview(current,payload,saved,revision){
  const receipt=$activityReceipt(payload.conversationId);
  if(receipt.revision!==revision||receipt.runId!==payload.runId)return current;
  const settled=$mergeGenerationPreview(current,payload,saved);
  return current?.conversationId===payload.conversationId&&current?.runId===payload.runId?settled:current;
}
function $mergeGenerationPreview(current,payload,saved){
  const receipt=$activityReceipt(payload.conversationId);
  if(!current||current.conversationId!==payload.conversationId)current=receipt.generation;
  if(!current||current.runId!==payload.runId)current={conversationId:payload.conversationId,runId:payload.runId,segments:[]};
  const segments=value=>value.segments?.length?value.segments:[...(value.reasoning?[{kind:'thinking',content:value.reasoning}]:[]),...(value.content?[{kind:'text',content:value.content}]:[])];
  const same=(a,b)=>a.kind===b.kind&&(!a.itemId||!b.itemId||a.itemId===b.itemId)
    &&(a.content.startsWith(b.content)||b.content.startsWith(a.content));
  const combine=(before,after)=>{const result=[...before];for(const item of after){const index=result.findIndex(old=>same(old,item));if(index<0)result.push(item);else if(!result[index].content.startsWith(item.content))result[index]=item}return result};
  let retained=current.retainedSegments||[],active=combine(current.activeSegments||segments(current),segments(payload));
  if(payload.checkpoint||payload.done){
    const timeline=saved||[];
    const turn=timeline.slice(timeline.findLastIndex(entry=>entry.role==='user'&&entry.kind==='message')+1);
    retained=combine(retained,active).filter(item=>!turn.some(entry=>entry.role==='assistant'&&
      (item.kind==='thinking'?['thinking','generation'].includes(entry.kind):['message','progress'].includes(entry.kind))&&
      ($modelText(entry.content).trim()===$modelText(item.content).trim()||
        (!item.itemId||!entry.metadata?.itemId||item.itemId===entry.metadata.itemId)&&$modelText(entry.content).trim().startsWith($modelText(item.content).trim()))));
    active=[];
  }
  const value={...current,...payload,checkpoint:undefined,retainedSegments:retained,activeSegments:active,segments:[...retained,...active]};
  return receipt.generation=payload.done&&!value.segments.length?undefined:value;
}
