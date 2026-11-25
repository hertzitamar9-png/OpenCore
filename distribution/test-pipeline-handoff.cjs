const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const load=()=>import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(__dirname,'../src/generation-panel.js'),'utf8')).toString('base64'));
test('ECHO handoff waits for active chats and never unloads another in-flight request',async()=>{
  const {createPipelineHandoff}=await load(),calls=[];
  const handoff=createPipelineHandoff({request:async(route)=>{calls.push(route);return {jobs:[{jobId:'job'}]}},invoke:async(command)=>{calls.push(command);return {activeConversationIds:['active'],runtime:{status:'running'}}}});
  await handoff();assert.deepEqual(calls,['/pipelines/pending','get_snapshot']);
});
test('finished chat releases its model before admitting the real pipeline job',async()=>{
  const {createPipelineHandoff}=await load(),calls=[];
  const handoff=createPipelineHandoff({request:async(route,body)=>{calls.push([route,body]);return route.endsWith('pending')?{jobs:[{jobId:'job'}]}:{}},invoke:async(command)=>{calls.push([command]);return {activeConversationIds:[],runtime:{status:'running'}}}});
  await handoff();assert.deepEqual(calls.map(call=>call[0]),['/pipelines/pending','get_snapshot','stop_runtime','/pipelines/admit']);
  assert.deepEqual(calls.at(-1)[1],{jobId:'job'});
});
test('failed unload cannot admit generation or hide its failure',async()=>{
  const {createPipelineHandoff}=await load(),calls=[];
  const handoff=createPipelineHandoff({request:async(route)=>{calls.push(route);return {jobs:[{jobId:'job'}]}},invoke:async(command)=>{if(command==='stop_runtime')throw Error('unload failed');return {activeConversationIds:[],runtime:{status:'running'}}}});
  await assert.rejects(handoff,/unload failed/);assert.deepEqual(calls,['/pipelines/pending']);
});
