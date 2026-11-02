// Tool receipts contain only events already delivered by the native bridge.
// Keep streamed prefixes and terminal status when an older snapshot arrives.
function $toolText(before='',after=''){
  before=typeof before==='string'?before:'';after=typeof after==='string'?after:'';
  if(!after||before.includes(after))return before;
  if(after.includes(before))return after;
  let overlap=Math.min(before.length,after.length);
  while(overlap&&!before.endsWith(after.slice(0,overlap)))overlap--;
  return before+after.slice(overlap);
}
function $toolValue(before={},after={}){
  const terminal=status=>['completed','failed','cancelled','interrupted'].includes(status);
  const value={...before,...after};
  value.status=terminal(before.status)&&!terminal(after.status)?before.status:after.status??before.status;
  if(typeof before.output==='string'||typeof after.output==='string')value.output=$toolText(before.output,after.output);
  return value;
}
function $toolProgress(state,payload){
  const old=state[payload.toolCallId]||{},value=$toolValue(old,payload);
  value.call=payload.call??old.call;
  value.output=$toolText(old.output,payload.output)+(payload.delta||'');
  if(old.result||payload.result){
    value.result=typeof (payload.result??old.result)==='object'?$toolValue(old.result,payload.result):payload.result??old.result;
    value.output=$toolText(value.output,value.result.output);
    if(typeof value.result.output==='string')value.result={...value.result,output:value.output};
  }
  return {...state,[payload.toolCallId]:value};
}
function $toolTimeline(entries,progress){
  const result=[...entries];
  for(const [index,item] of Object.values(progress).entries()){
    let call=result.findIndex(entry=>entry.kind==='tool_call'&&Ll(entry)===item.toolCallId);
    if(call<0&&item.call){
      const output=result.findIndex(entry=>entry.kind==='tool_result'&&Ll(entry)===item.toolCallId);
      call=output<0?result.length:output;result.splice(call,0,item.call);
    }
    const at=result.findIndex(entry=>entry.kind==='tool_result'&&Ll(entry)===item.toolCallId);
    let saved={};if(at>=0)try{saved=JSON.parse(result[at].content)}catch{}
    const live={...(item.result&&typeof item.result==='object'?item.result:item.result!==undefined?{result:item.result}:{}),status:item.status??item.result?.status};
    if(item.output||typeof item.result?.output==='string')live.output=$toolText(item.output,item.result?.output);
    const value=$toolValue(saved,live),entry={...(at>=0?result[at]:{id:-1000000-index,conversationId:item.conversationId,timestamp:item.call?.timestamp||'',kind:'tool_result',role:'tool',source:'OpenCore',title:item.title}),content:JSON.stringify(value),metadata:{...(at>=0?result[at].metadata:{}),live:at<0,toolCallId:item.toolCallId,status:value.status}};
    if(at<0)result.splice(call<0?result.length:call+1,0,entry);else result[at]=entry;
  }
  return result;
}

// A small renderer-session cache survives composer unmounts and chat switches.
// It never writes conversation data or raw output to browser storage.
const $activityReceipts=new Map,$toolProgressListeners=new Set;
function $activityReceipt(id){
  if(!id)return {tools:{}};
  let receipt=$activityReceipts.get(id);
  if(!receipt)receipt={tools:{}};
  $activityReceipts.delete(id);$activityReceipts.set(id,receipt);if($activityReceipts.size>8)$activityReceipts.delete($activityReceipts.keys().next().value);
  return receipt;
}
function $toolPreviewState(id){return $activityReceipt(id).tools}
function $generationPreviewFor(id){return $activityReceipt(id).generation}
function $listenToolProgress(listener){$toolProgressListeners.add(listener);return Promise.resolve(()=>$toolProgressListeners.delete(listener))}
if(typeof It==='function')It('opencore-tool-progress',event=>{
  const receipt=$activityReceipt(event.payload.conversationId);
  receipt.tools=$toolProgress(receipt.tools,event.payload);
  for(const listener of $toolProgressListeners)listener(event);
}).catch(()=>{});
