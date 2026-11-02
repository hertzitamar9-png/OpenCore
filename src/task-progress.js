import React from 'react';

const object=value=>{try{if(typeof value==='string')value=value.length<262144?JSON.parse(value):null;return value&&typeof value==='object'?value:{}}catch{return {}}};
const callId=entry=>entry.metadata?.toolUseId||entry.metadata?.toolCallId||entry.metadata?.id||object(entry.content).tool_call_id||object(entry.content).id;
const failed=entry=>entry?.metadata?.isError===true||entry?.metadata?.denied===true||object(entry?.content)?.isError===true;

export function taskProgress(entries=[],active=false,conversationId){
  const current=entries.filter(entry=>!conversationId||entry.conversationId===conversationId);
  let start=0;
  current.forEach((entry,index)=>{if(entry.kind==='message'&&entry.role==='user')start=index+1});
  const turn=current.slice(start),results=new Map(turn.filter(entry=>entry.kind==='tool_result'&&callId(entry)).map(entry=>[callId(entry),entry]));
  let tasks=[];const agents=[];
  for(const entry of turn){
    const raw=object(entry.content),meta=entry.metadata||{},name=String(meta.toolName||raw.name||(meta.kind==='plan'?'update_plan':entry.title)||'');
    const result=results.get(callId(entry)),output=object(result?.content),value=object(output.structuredContent||output.value||output);
    const args=object(raw.args||raw.input||raw.arguments||(Object.keys(raw).length?raw:meta));
    if(/(?:^|[._])(todowrite|update_plan|task_list|todo_list)$/i.test(name)&&
       (entry.kind==='progress'||entry.kind==='tool_call'&&result&&!failed(result))){
      const rows=value.newTodos||value.tasks||value.plan||value.items||args.todos||args.plan||args.tasks||args.items;
      if(Array.isArray(rows))tasks=rows.flatMap((item,index)=>{
        const label=item?.content||item?.step||item?.text||item?.subject;
        if(typeof label!=='string'||!label.trim())return [];
        const status=typeof item.completed==='boolean'?(item.completed?'completed':'pending'):item.status;
        return [{id:item.id||index,label:label.slice(0,500),status:['pending','in_progress','completed','blocked','cancelled','failed'].includes(status)?status:'unknown'}];
      });
    }
    if(entry.kind==='tool_call'&&/(?:^|[._])(agent|task|spawn_agent|group_delegate)$/i.test(name))agents.push({
      id:callId(entry)||entry.id,label:([args.description,args.subagent_type,args.task,args.prompt].find(value=>typeof value==='string')||'Agent').slice(0,180),
      status:failed(result)?'Failed':result?({__proto__:null,completed:'Returned',async_launched:'Launched',running:'Running',queued:'Queued',pending:'Queued',failed:'Failed',killed:'Stopped',paused:'Paused'}[value.task?.status||value.status]||(args.run_in_background?'Launched':/group_delegate$/i.test(name)?'Dispatched':'Returned')):active?'Running':'No result',
    });
  }
  return {tasks,agents,completed:tasks.filter(task=>task.status==='completed').length};
}

export function TaskProgress({entries,active=false,conversationId}){
  const state=React.useMemo(()=>taskProgress(entries,active,conversationId),[entries,active,conversationId]),h=React.createElement;
  if(!state.tasks.length&&!state.agents.length)return null;
  return h('details',{className:'task-progress','aria-label':'Task progress'},
    h('summary',null,h('strong',null,state.tasks.length?state.completed+'/'+state.tasks.length+' tasks complete':'Agent activity'),
      h('span',null,[active?'Working':'Saved activity',state.agents.length?state.agents.length+' agent'+(state.agents.length===1?'':'s'):''].filter(Boolean).join(' · '))),
    state.tasks.length?h('ol',null,...state.tasks.map(task=>h('li',{key:task.id,'data-status':task.status},
      h('span',{'aria-hidden':true},task.status==='completed'?'✓':task.status==='in_progress'?'◉':'○'),
      h('span',null,task.label),h('small',null,task.status.replaceAll('_',' '))))):null,
    state.agents.length?h('ul',{'aria-label':'Agent activity'},...state.agents.map(agent=>h('li',{key:agent.id},h('span',null,agent.label),h('small',null,agent.status)))):null);
}
