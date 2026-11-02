import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {taskProgress,TaskProgress} from '../src/task-progress.js';
import {projectCodexEvent} from '../src-tauri/resources/codex/codex-events.mjs';

// The live native bridge stores raw input in content and correlates toolUseId.
const event=(id,kind,name,input,metadata={})=>({id,conversationId:'chat',kind,role:kind==='tool_result'?'tool':'assistant',title:'Tool '+kind,
  content:typeof input==='string'?input:JSON.stringify(input),metadata:{toolName:name,toolUseId:String(Math.ceil(id/2)),...metadata}});
const call=(input,name='TodoWrite',id=1)=>event(id,'tool_call',name,input);
const result=(input='Todos updated',name='TodoWrite',id=2,metadata={})=>event(id,'tool_result',name,input,metadata);
const todos=[{content:'Inspect the real implementation',status:'completed',activeForm:'Inspecting'},{content:'Verify the result',status:'in_progress',activeForm:'Verifying'}];

test('native TodoWrite input and plain-text success expose only model-declared task states',()=>{
  const state=taskProgress([call({todos}),result()],true,'chat');
  assert.equal(state.tasks.length,2);assert.equal(state.completed,1);
  assert.equal(state.tasks[1].status,'in_progress');
});

test('a denied or failed plan update preserves its last accepted snapshot',()=>{
  const prior=[call({todos}),result()];
  for(const flag of ['isError','denied']){
    const latest=taskProgress([...prior,call({todos:todos.map(task=>({...task,status:'completed'}))},'TodoWrite',3),result('Denied','TodoWrite',4,{[flag]:true})],false,'chat');
    assert.equal(latest.completed,1);assert.equal(latest.tasks[1].status,'in_progress');
  }
});

test('an unfinished tool request, malformed output and narrative checkboxes never fabricate a plan',()=>{
  const entries=[call({todos}),event(3,'message','assistant','- [x] Everything is done'),event(4,'tool_call','Read','null'),event(6,'tool_result','Read','null')];
  assert.deepEqual(taskProgress(entries,true,'chat'),{tasks:[],agents:[],completed:0});
});

test('authoritative TodoWrite output wins over its requested snapshot',()=>{
  const latest=taskProgress([call({todos}),result({newTodos:[{content:'Verified check',status:'completed'}]})],false,'chat');
  assert.equal(latest.tasks.length,1);assert.equal(latest.tasks[0].label,'Verified check');assert.equal(latest.completed,1);
});

test('qualified update_plan, task-list snapshots and native Codex plan metadata retain their actual states',()=>{
  for(const [name,input] of [['mcp__ccd_turn__update_plan',{items:todos}],['functions.update_plan',{plan:[{step:'A',status:'pending'}]}],['task_list',{tasks:[{subject:'A',status:'blocked'}]}]]){
    assert.equal(taskProgress([call(input,name),result('Updated',name)],true,'chat').tasks.length,input.items?.length||1);
  }
  const progress={id:3,conversationId:'chat',kind:'progress',role:'assistant',title:'Plan',content:'Checking the implementation',metadata:{kind:'plan',plan:[{step:'Read implementation',status:'in_progress'}]}};
  assert.equal(taskProgress([progress],true,'chat').tasks[0].status,'in_progress');
});

test('a new user turn or another conversation cannot reuse an older task list',()=>{
  const prior=[call({todos}),result()];
  assert.equal(taskProgress(prior,false,'another-chat').tasks.length,0);
  assert.equal(taskProgress([...prior,{id:3,conversationId:'chat',role:'user',kind:'message',content:'New request'}],true,'chat').tasks.length,0);
  const toolReply={...result(),role:'user'};
  assert.equal(taskProgress([call({todos}),toolReply],true,'chat').tasks.length,2,'SDK tool replies are not a new user task');
});

test('agent activity distinguishes returned, background launch, failure and missing results',()=>{
  const agent=(input,reply,meta={},active=false)=>taskProgress([call(input,'Agent'),...(reply===undefined?[]:[result(reply,'Agent',2,meta)])],active,'chat').agents[0];
  assert.equal(agent({description:'Review'},'Actual review').status,'Returned');
  assert.equal(agent({description:'Explore',run_in_background:true},{status:'async_launched',agentId:'agent-1'}).status,'Launched');
  assert.equal(agent({description:'Review'},'Denied',{isError:true}).status,'Failed');
  assert.equal(agent({description:'Review'},undefined,{},false).status,'No result');
  assert.equal(agent({description:'Review'},undefined,{},true).status,'Running');
  assert.equal(agent({description:'Review'},{status:'failed'}).status,'Failed');
});

test('a real group delegation receipt remains queued until a terminal state is reported',()=>{
  const entries=[call({agentId:'reviewer',task:{id:'task-1'},prompt:'Review the real implementation'},'group_delegate'),result({task:{id:'task-1',agentId:'reviewer',status:'queued'}},'group_delegate')];
  const state=taskProgress(entries,false,'chat');
  assert.equal(state.agents[0].label,'Review the real implementation');assert.equal(state.agents[0].status,'Queued');
  assert.equal(state.completed,0);
});

test('ending a chat does not check off incomplete steps or claim an unresolved agent finished',()=>{
  const entries=[call({todos}),result(),call({description:'Review'},'Task',3)];
  const html=renderToStaticMarkup(React.createElement(TaskProgress,{entries,active:false,conversationId:'chat'}));
  assert.match(html,/1\/2 tasks complete/);assert.match(html,/No result/);assert.match(html,/in progress/);
  assert.doesNotMatch(html,/type="checkbox"/);
});

test('unknown tool status keys remain inert strings and a long real plan keeps its complete task count',()=>{
  const rows=Array.from({length:105},(_,index)=>({content:'Task '+index,status:'pending'}));
  const entries=[call({todos:rows}),result(),call({description:'Review'},'Agent',3),result({status:'__proto__'},'Agent',4)];
  const state=taskProgress(entries,false,'chat');assert.equal(state.tasks.length,105);assert.equal(state.completed,0);
  assert.doesNotThrow(()=>renderToStaticMarkup(React.createElement(TaskProgress,{entries,conversationId:'chat'})));
});

test('SDK todo_list snapshots survive projection without marking incomplete items complete at turn end',()=>{
  const emitted=[],previousText=new Map(),completedItems=new Set();
  const project=event=>projectCodexEvent(event,{emit:value=>emitted.push(value),previousText,completedItems});
  const item={id:'item_7',type:'todo_list',items:[{text:'Inspect',completed:true},{text:'Verify',completed:false}]};
  project({type:'item.started',item});project({type:'item.updated',item});project({type:'item.completed',item});
  assert.equal(emitted.length,2,'Unchanged started/updated/completed snapshots need one paired timeline update');
  assert.equal(emitted[0].kind,'tool_call');assert.equal(emitted[1].kind,'tool_result');
  assert.equal(emitted[0].id,emitted[1].id);
  const rows=emitted.map((value,index)=>event(index+1,value.kind,value.name,value.kind==='tool_call'?value.args:value.value,{toolUseId:value.id}));
  assert.equal(taskProgress(rows,false,'chat').completed,1);
  project({type:'item.updated',item:{...item,items:item.items.map(task=>({...task,completed:true}))}});
  assert.equal(emitted.length,4);assert.notEqual(emitted[2].id,emitted[0].id,'A changed snapshot must not be deduplicated by native event IDs');
  project({type:'item.completed',item:{id:'answer',type:'agent_message',text:'Actual answer'}});
  assert.ok(emitted.some(value=>value.kind==='assistant'&&value.text==='Actual answer'),'Other SDK output remains intact');
});
