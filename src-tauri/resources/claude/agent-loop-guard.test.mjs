import test from 'node:test';
import assert from 'node:assert/strict';
import { AgentLoopGuard, forwardGuardedAgent, guardedToolResult } from './agent-loop-guard.mjs';

const assistant = (id, text) => ({type:'assistant',message:{id,content:[{type:'text',text}]}});

test('repeated plans in separate assistant turns trigger a no-progress stop', () => {
  const guard=new AgentLoopGuard();
  const plans=[
    "Empty workspace. I'll build a polished, feature-rich snake game.",
    "Empty workspace, so I'll create a complete snake HTML game from scratch.",
    "I'll build a polished single-file snake game with controls and a high score.",
    "Empty workspace. Let me build a complete, polished snake game.",
    "I'll build a complete snake game from scratch in the empty workspace.",
    "Empty workspace. I will create a polished snake HTML game.",
  ];
  const outcomes=plans.map((text,index)=>guard.observeAssistant(assistant(String(index),text)));
  assert.ok(outcomes.some(value=>value?.kind==='repeated-plan'));
});

test('multiple streamed blocks of the same assistant response do not count as separate turns', () => {
  const guard=new AgentLoopGuard();
  for(let i=0;i<8;i++)assert.equal(guard.observeAssistant(assistant('same-id',"I'll build a snake game from scratch.")),null);
});

test('real tool progress and different plans are not mistaken for a loop', () => {
  const guard=new AgentLoopGuard();
  const steps=["I'll inspect the snake game workspace for an existing file.","I will create the snake game HTML file.","I'll add keyboard movement to the snake game.","I will run the snake game tests."];
  for(let i=0;i<steps.length;i++){
    assert.equal(guard.observeAssistant(assistant(String(i),steps[i])),null);
    assert.equal(guard.observeToolResult('Read',{file_path:`file-${i}`},{content:`result-${i}`}),null);
  }
});

test('three identical read-only tool results stop repeated searches without progress', () => {
  const guard=new AgentLoopGuard();
  for(let i=0;i<2;i++)assert.equal(guard.observeToolResult('Grep',{pattern:'snake',path:'.'},{content:'no matches'}),null);
  assert.equal(guard.observeToolResult('Grep',{path:'.',pattern:'snake'},{content:'no matches'})?.kind,'repeated-tool');
});

test('alternating unchanged searches cannot form an endless two-tool cycle',()=>{
  const guard=new AgentLoopGuard();let issue;
  for(let i=0;i<6;i++)issue=guard.observeToolResult('Grep',{pattern:i%2?'index.html':'snake'},{content:'no matches'});
  assert.equal(issue?.kind,'repeated-tool');
});

test('changed results and mutating actions are not stopped as repeated reads', () => {
  const guard=new AgentLoopGuard();
  for(let i=0;i<5;i++)assert.equal(guard.observeToolResult('Read',{file_path:'a.txt'},{content:`version ${i}`}),null);
  for(let i=0;i<5;i++)assert.equal(guard.observeToolResult('Write',{file_path:'a.txt',content:'same'},{ok:true}),null);
  for(let i=0;i<5;i++)assert.equal(guard.observeToolResult('mcp__opencore__dev',{action:'status'},{status:'running'}),null);
});

test('the streamed agent forwards its activity and interrupts before another looping turn', async () => {
  const plans=["I'll build a polished snake game from scratch.","I will create a complete snake game.",
    "Let me build the snake game with a high score.","I'll create a polished snake game.","I will build the snake game."];
  let interrupted=0;
  const agent={async *[Symbol.asyncIterator](){for(let i=0;i<plans.length;i++)yield assistant(String(i),plans[i]);},
    async interrupt(){interrupted++;}};
  const visible=[];
  await forwardGuardedAgent(agent,new AgentLoopGuard(),value=>visible.push(value),async()=>{});
  assert.equal(interrupted,1);
  assert.ok(visible.some(value=>value.kind==='fatal'&&/repeated planning/i.test(value.error)));
  assert.ok(visible.filter(value=>value.kind==='sdk').length>=4);
});

test('the real tool hook requests a stop after unchanged repeated searches',()=>{
  const guard=new AgentLoopGuard(),visible=[];
  const event={tool_name:'Grep',tool_input:{pattern:'snake'},tool_response:{content:'no matches'}};
  assert.equal(guardedToolResult(guard,event,value=>visible.push(value)),null);
  assert.equal(guardedToolResult(guard,event,value=>visible.push(value)),null);
  const result=guardedToolResult(guard,event,value=>visible.push(value));
  assert.equal(result.continue,false);
  assert.ok(visible.some(value=>value.kind==='fatal'&&/repeating the same tool/i.test(value.error)));
});
