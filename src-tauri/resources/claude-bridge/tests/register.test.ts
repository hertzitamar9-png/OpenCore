import { expect, mock, test, tier } from 'claude-code/testing';

tier('user');

// Run against an isolated paired copy (see README); all I/O is mocked here.
test('real Mods dispatch recalls evidence and preserves the original prompt', async ($, on) => {
  mock.clock(on);
  mock.store(on);
  const requests: any[] = [], tools: string[] = [];
  on('session.start', (_$, e) => ({cwd:e.cwd}));
  on('session.id', () => ({value:'test-session'}));
  on('session.root', () => ({value:'/test-project'}));
  on('session.version', () => ({value:{version:'2.1.288'}}));
  on('ui.log', () => ({value:undefined}));
  on('tool.register', (_$, e) => {
    tools.push(e.name);
    return {value:{tool:`mcp__opencore-bridge__${e.name}`}};
  });
  on('http.fetch', (_$, e) => {
    const body=JSON.parse(e.init?.body as string);
    requests.push(body);
    return {value:{ok:true,status:200,headers:{},text:JSON.stringify(
      body.action==='recall'?{context:'ECHO: CharacterController uses SQLite'}:{}
    )}};
  });
  let received: any;
  on('prompt.submit', (_$, e) => {received=e;return {text:e.text};});
  await $.session.start({cwd:'/test-project',surface:null,isInteractive:false});
  await $.prompt.submit({text:'continue CharacterController'});
  expect(tools).toEqual(['echo_search','echo_read','studio_use']);
  expect(received.text).toBe('continue CharacterController');
  expect(received.context).toContain('ECHO: CharacterController uses SQLite');
  expect(requests.some(r=>r.action==='event'&&r.args.kind==='prompt')).toBe(true);
});

test('real Mods dispatch serves a custom tool without invoking the engine tool', async ($, on) => {
  mock.clock(on);
  mock.store(on);
  on('session.start', (_$, e) => ({cwd:e.cwd}));
  on('session.id', () => ({value:'test-session'}));
  on('session.root', () => ({value:'/test-project'}));
  on('session.version', () => ({value:{version:'2.1.288'}}));
  on('ui.log', () => ({value:undefined}));
  on('tool.register', (_$, e) => ({value:{tool:`mcp__opencore-bridge__${e.name}`}}));
  let executed=0;
  on('http.fetch', (_$, e) => {
    const body=JSON.parse(e.init?.body as string);
    if(body.action==='tool')executed++;
    return {value:{ok:true,status:200,headers:{},text:JSON.stringify(body.action==='tool'?{value:{hits:['exact symbol']}}:{})}};
  });
  await $.session.start({cwd:'/test-project',surface:null,isInteractive:false});
  const result=await $.tool.call({tool:'mcp__opencore-bridge__echo_search',tool_use_id:'call-1',query:'CharacterController'});
  expect(result).toEqual({result:{hits:['exact symbol']}});
  expect(executed).toBe(1);
});

test('real Mods host restores an owned job and notifies once after completion', async ($, on) => {
  const clock=mock.clock(on);
  mock.store(on, {'studio-job:owned':{sessionId:'test-session',workspace:'/test-project'}});
  on('session.start', (_$, e) => ({cwd:e.cwd}));
  on('session.id', () => ({value:'test-session'}));
  on('session.root', () => ({value:'/test-project'}));
  on('session.version', () => ({value:{version:'2.1.288'}}));
  on('ui.log', () => ({value:undefined}));
  on('tool.register', (_$, e) => ({value:{tool:`mcp__opencore-bridge__${e.name}`}}));
  let notices=0;
  on('prompt.submit', (_$,e) => {notices++;return {text:e.text};});
  on('http.fetch', (_$, e) => {
    const body=JSON.parse(e.init?.body as string);
    return {value:{ok:true,status:200,headers:{},text:JSON.stringify(body.action==='tool'?{value:{status:'completed',category:'music'}}:{})}};
  });
  await $.session.start({cwd:'/test-project',surface:null,isInteractive:false});
  await clock.advance(15000);
  await clock.advance(15000);
  expect(notices).toBe(1);
});
