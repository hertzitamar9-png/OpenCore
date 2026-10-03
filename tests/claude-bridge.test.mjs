import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createBridgeClient, installBridgeHooks, supportedVersion } from '../src-tauri/resources/claude-bridge/hooks/bridge.mjs';

test('Mods version gate accepts supported releases and refuses unknown versions', () => {
  assert.equal(supportedVersion('2.1.287'), true);
  assert.equal(supportedVersion('2.1.286'), false);
  assert.equal(supportedVersion('unknown'), false);
});

test('bridge credentials can only reach the fixed loopback endpoint', async () => {
  let calls = 0;
  assert.throws(() => createBridgeClient({baseUrl:'https://other.invalid',token:'secret',fetch:()=>{calls++;}}), /loopback/);
  assert.equal(calls, 0);
  const client = createBridgeClient({baseUrl:'http://127.0.0.1:8812',token:'secret',fetch:async (url, options) => {
    assert.equal(url, 'http://127.0.0.1:8812/opencore/claude-bridge');
    assert.equal(options.headers['x-opencore-bridge-token'], 'secret');
    assert.equal(JSON.parse(options.body).sessionId, 'session');
    return {ok:true,status:200,text:JSON.stringify({context:'old evidence'})};
  }});
  assert.equal((await client.request({sessionId:'session',workspace:'/project',action:'recall',args:{query:'Symbol'}})).context, 'old evidence');
});

function hostFixture(handler, saved = new Map()) {
  const hooks = new Map(), tools = [], logs = [], calls = [];
  const $ = {
    session:{id:async()=> 'session',root:async()=>'/project',version:async()=>({version:'2.1.287'})},
    http:{fetch:async(_,init)=> {const body=JSON.parse(init.body);calls.push(body);return handler(body);}},
    tool:{register:async spec=>{tools.push(spec);return {tool:`mcp__opencore-bridge__${spec.name}`};}},
    store:{keys:async()=>[...saved.keys()],get:async key=>saved.get(key),set:async(key,value)=>{saved.set(key,value);},delete:async key=>{saved.delete(key);}},
    ui:{log:text=>logs.push(text)},clock:{every:()=>({cancel(){}}),after:()=>({cancel(){}})},prompt:{submit:async()=>({})},
  };
  installBridgeHooks((name, hook)=>hooks.set(name, hook), {baseUrl:'http://127.0.0.1:8812',token:'secret'});
  return {$,hooks,tools,logs,calls};
}
const response = data => ({ok:true,status:200,text:JSON.stringify(data)});

test('automatic recall reaches model context without rewriting user text', async () => {
  const f=hostFixture(body=>response(body.action==='recall'?{context:'ECHO evidence: CharacterController uses SQLite'}:{}));
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>({cwd:e.cwd}));
  let received;
  await f.hooks.get('prompt.submit')(f.$,{text:'continue CharacterController',context:['existing']},async e=>{received=e;return e;});
  assert.equal(received.text, 'continue CharacterController');
  assert.deepEqual(received.context,['existing','ECHO evidence: CharacterController uses SQLite']);
  assert.equal(f.tools.length,3);
});

test('an unavailable bridge preserves tool results and reports the failed capture', async () => {
  const f=hostFixture(body=>{if(body.action==='event') throw Error('offline');return response({});});
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>({cwd:e.cwd}));
  const output={result:{exitCode:0},text:'passed'};
  let executed=0;
  const actual=await f.hooks.get('tool.call')(f.$,{tool:'Bash',tool_use_id:'call',command:'npm test'},async()=>{executed++;return output;});
  assert.equal(executed,1);
  assert.equal(actual,output);
  assert.ok(f.logs.some(line=>line.includes('offline')));
});

test('a throwing tool stays failed and its failure is captured', async () => {
  const f=hostFixture(()=>response({}));
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>e);
  const failure=Error('command rejected');
  await assert.rejects(f.hooks.get('tool.call')(f.$,{tool:'Bash',tool_use_id:'failed',command:'test'},async()=>{throw failure;}),error=>error===failure);
  const result=f.calls.find(c=>c.action==='event'&&c.args.kind==='tool_result');
  assert.equal(result.args.metadata.isError,true);
  assert.equal(result.args.content,'command rejected');
});

test('registered bridge tools are served once through the authenticated app route', async () => {
  const f=hostFixture(body=>response(body.action==='tool'?{value:{hits:['exact Symbol']}}:{}));
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>({cwd:e.cwd}));
  const actual=await f.hooks.get('tool.call')(f.$,{tool:'mcp__opencore-bridge__echo_search',tool_use_id:'call',query:'Symbol'},async()=>{throw Error('must not call engine core');});
  assert.deepEqual(actual.result,{hits:['exact Symbol']});
  assert.equal(f.calls.filter(body=>body.action==='tool').length,1);
});

test('old Claude hosts keep the normal session and announce the required upgrade', async () => {
  const f=hostFixture(()=>{throw Error('no network for unsupported host');});
  f.$.session.version=async()=>({version:'2.1.286'});
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>({cwd:e.cwd}));
  assert.equal(f.tools.length,0);
  assert.ok(f.logs.some(line=>line.includes('2.1.287')));
});

test('a recovered connection registers tools before the next user prompt', async () => {
  let offline=true;
  const f=hostFixture(()=>{if(offline)throw Error('offline');return response({});});
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>({cwd:e.cwd}));
  offline=false;
  await f.hooks.get('prompt.submit')(f.$,{text:'hello'},async e=>e);
  assert.equal(f.tools.length,3);
});

test('session shutdown cancels the host timer without disturbing its result', async () => {
  const f=hostFixture(()=>response({}));let cancelled=0;
  f.$.clock.every=()=>({cancel(){cancelled++;}});
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>({cwd:e.cwd}));
  const output={ended:true};
  assert.equal(await f.hooks.get('session.end')(f.$,{},async()=>output),output);
  assert.equal(cancelled,1);
});

test('buffered capture keeps its original workspace after a root change', async () => {
  let offline=false,root='/first';
  const f=hostFixture(body=>{if(offline&&body.action==='event')throw Error('offline');return response({});});
  f.$.session.root=async()=>root;
  await f.hooks.get('session.start')(f.$,{cwd:root},async e=>e);
  offline=true;
  await f.hooks.get('tool.call')(f.$,{tool:'Bash',tool_use_id:'old',command:'test'},async()=>({text:'old output'}));
  root='/second';offline=false;
  await f.hooks.get('prompt.submit')(f.$,{text:'hello',origin:{kind:'composer'}},async e=>e);
  assert.ok(f.calls.some(c=>c.action==='start'&&c.workspace==='/second'));
  assert.ok(f.calls.filter(c=>c.action==='event'&&c.args.content==='old output').every(c=>c.workspace==='/first'));
});

test('only engine-attested user submissions authorize studio generation', async () => {
  const f=hostFixture(()=>response({}));
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>e);
  await f.hooks.get('prompt.submit')(f.$,{text:'/music a song',origin:{kind:'plugin'}},async e=>e);
  assert.equal(f.calls.findLast(c=>c.action==='prompt').args.userAuthorized,false);
  await f.hooks.get('prompt.submit')(f.$,{text:'/music a song',origin:{kind:'composer'}},async e=>e);
  assert.equal(f.calls.findLast(c=>c.action==='prompt').args.userAuthorized,true);
});

test('a completed studio job triggers one continuation while idle', async () => {
  let tick,notifications=0,terminal=false;
  const f=hostFixture(body=>response(body.action==='tool'?{value:body.args.input.action==='generate'?{status:'queued',id:'job',category:'music'}:{status:terminal?'completed':'running',category:'music'}}:{}));
  f.$.clock.every=(_,fn)=>{tick=fn;return {cancel(){}};};
  f.$.prompt.submit=async()=>{notifications++;};
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>e);
  await f.hooks.get('tool.call')(f.$,{tool:'mcp__opencore-bridge__studio_use',tool_use_id:'job-call',action:'generate'},async()=>{throw Error('core must not execute');});
  await f.hooks.get('turn.complete')(f.$,{answer:'queued',turnId:'turn'},async()=>({}));
  tick();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(notifications,0);
  terminal=true;tick();await new Promise(resolve=>setImmediate(resolve));
  tick();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(notifications,1);
});

test('resuming the same session restores watched jobs without regenerating', async () => {
  const saved=new Map([['studio-job:job',{sessionId:'session',workspace:'/project'}]]);
  let tick,notices=0;
  const f=hostFixture(body=>response(body.action==='tool'?{value:{status:'completed',category:'music'}}:{}),saved);
  f.$.clock.every=(_,fn)=>{tick=fn;return {cancel(){}};};
  f.$.prompt.submit=async()=>{notices++;};
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>e);
  tick();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(notices,1);
  assert.equal(saved.size,0);
  assert.equal(f.calls.filter(c=>c.action==='tool'&&c.args.input.action==='generate').length,0);
});

test('another session cannot restore someone else\'s watched jobs', async () => {
  const saved=new Map([['studio-job:private',{sessionId:'other-session',workspace:'/other'}]]);
  let tick;
  const f=hostFixture(()=>response({}),saved);
  f.$.clock.every=(_,fn)=>{tick=fn;return {cancel(){}};};
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>e);
  tick();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.calls.filter(c=>c.action==='tool').length,0);
  assert.equal(saved.size,1);
});

test('one permanently rejected event cannot block later tool activity', async () => {
  let rejected=false;
  const f=hostFixture(body=>{
    if(body.action==='event'&&!rejected){rejected=true;return {ok:false,status:400,text:JSON.stringify({error:'event too large'})};}
    return response({});
  });
  await f.hooks.get('session.start')(f.$,{cwd:'/project'},async e=>e);
  await f.hooks.get('tool.call')(f.$,{tool:'Bash',tool_use_id:'call',command:'test'},async()=>({text:'new result'}));
  assert.ok(f.calls.some(c=>c.action==='event'&&c.args.content==='new result'));
  assert.equal(f.calls.filter(c=>c.action==='event'&&c.args.kind==='tool_call').length,1);
  assert.ok(f.logs.some(line=>line.includes('not archived')));
});
