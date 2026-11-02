import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';

const source = path.dirname(fileURLToPath(import.meta.url));
const nativeTool = {type: 'function', function: {name: 'fixture_native', description: 'The existing native dispatch path', parameters: {type: 'object', properties: {value: {type: 'string'}}, required: ['value']}}};

function writePackage(root, name, content) {
  const directory = path.join(root, 'node_modules', ...name.split('/'));
  fs.mkdirSync(directory, {recursive: true});
  fs.writeFileSync(path.join(directory, 'package.json'), JSON.stringify({type: 'module', exports: './index.mjs'}));
  fs.writeFileSync(path.join(directory, 'index.mjs'), content);
}

async function fixture(t, backend, consent) {
  const parent = fs.realpathSync(os.tmpdir());
  const root = fs.mkdtempSync(path.join(parent, 'opencore-group-runners-'));
  t.after(() => {
    const resolved = fs.realpathSync(root);
    assert.ok(resolved.toLowerCase().startsWith((parent+path.sep+'opencore-group-runners-').toLowerCase()));
    fs.rmSync(resolved, {recursive: true, force: true});
  });
  for (const name of ['agent', backend, 'work', 'config', 'appdata']) fs.mkdirSync(path.join(root, name));
  for (const name of ['group-tools.mjs', 'coding-verification.mjs']) fs.copyFileSync(path.join(source, name), path.join(root, 'agent', name));
  const runnerSource = path.join(source, '..', backend);
  const files = backend === 'claude' ? ['runner.mjs', 'agent-loop-guard.mjs', 'context-budget.mjs', 'coding-verification.mjs']
    : ['runner.mjs', 'codex-config.mjs', 'codex-events.mjs', 'tool-relay.mjs'];
  for (const name of files) fs.copyFileSync(path.join(runnerSource, name), path.join(root, backend, name));
  const requests = [];
  const server = createServer(async (request, response) => {
    let text = ''; for await (const part of request) text += part;
    requests.push({authorization: request.headers.authorization, body: JSON.parse(text)});
    response.setHeader('content-type', 'application/json');
    response.end('{"ok":true,"taskId":"group-task","status":"queued"}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  if (consent) {
    const contexts = path.join(root, 'appdata', 'ai.opencore.control-center', 'group-agent-contexts');
    fs.mkdirSync(contexts, {recursive: true});
    fs.writeFileSync(path.join(contexts, 'fixture-chat.json'), JSON.stringify({schema: 1, conversationId: 'fixture-chat',
      url: `http://127.0.0.1:${server.address().port}/api/groups/agent/tool`, token: 'scoped-task-0123456789abcdef0123456789abcdef', expiresAt: Math.floor(Date.now()/1000)+600}));
  }
  return {root, requests};
}

async function run(f, backend) {
  const child = spawn(process.execPath, [path.join(f.root, backend, 'runner.mjs')], {cwd: f.root, windowsHide: true,
    env: {...process.env, APPDATA: path.join(f.root, 'appdata')}, stdio: ['pipe', 'pipe', 'pipe']});
  const events = []; let pending = '', stderr = '';
  child.stderr.on('data', part => stderr += part);
  child.stdout.on('data', part => {
    pending += part;
    while (pending.includes('\n')) {
      const index = pending.indexOf('\n'), line = pending.slice(0, index); pending = pending.slice(index+1);
      if (!line) continue;
      const event = JSON.parse(line); events.push(event);
      if (event.kind === 'permission') child.stdin.write(JSON.stringify({kind: 'reply', id: event.id, value: true})+'\n');
      if (event.kind === 'tool') child.stdin.write(JSON.stringify({kind: 'reply', id: event.id, value: {ok: true, nativeReceipt: 'native-dispatch-preserved'}})+'\n');
    }
  });
  const config = {kind: 'start', conversationId: 'fixture-chat', cwd: path.join(f.root, 'work'), workspace: path.join(f.root, 'work'),
    configDir: path.join(f.root, 'config'), codexHome: path.join(f.root, 'config'), resourcesDir: path.join(f.root, 'codex'),
    gatewayUrl: 'http://127.0.0.1:8812/v1', contextWindowTokens: 32768, compactAtTokens: 20000,
    nodeExecutable: process.execPath, content: 'Use the approved group task if available', instructions: '', tools: [nativeTool], effort: 'off'};
  child.stdin.write(JSON.stringify(config)+'\n');
  const code = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(new Error('Runner fixture timed out: '+stderr)); }, 10000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', value => { clearTimeout(timer); resolve(value); });
  });
  assert.equal(code, 0, stderr);
  assert.equal(events.some(event => event.kind === 'fatal'), false, JSON.stringify(events));
  return events;
}

const claudeSdk = `
export const tool=(name,description,shape,run)=>({name,run});
export const createSdkMcpServer=({tools})=>({tools});
export function query({options}){return {
  async *[Symbol.asyncIterator](){
    const tools=options.mcpServers.opencore.tools;
    const group=tools.find(t=>t.name==='group_message');
    if(group){
      const hook=await options.hooks.PreToolUse[0].hooks[0]({tool_name:'mcp__opencore__group_message',tool_input:{text:'Group update'}});
      if(hook.hookSpecificOutput.permissionDecision!=='allow')throw Error('Scoped group hook denied');
      const use=await options.canUseTool('mcp__opencore__group_message',{text:'Group update'});
      if(use.behavior!=='allow')throw Error('Scoped group tool denied');
      await group.run({text:'Group update'});
    }
    await options.hooks.PreToolUse[0].hooks[0]({tool_name:'mcp__opencore__fixture_native',tool_input:{value:'native'}});
    await tools.find(t=>t.name==='fixture_native').run({value:'native'});
    yield {type:'assistant',message:{id:'done',content:[{type:'text',text:JSON.stringify(tools.map(t=>t.name))}]}};
  }, close(){}, async getContextUsage(){return {totalTokens:1,maxTokens:32768};}
}};
`;

const codexSdk = `
import {readFileSync} from 'node:fs';
export class Codex {
 constructor({config}){this.config=config;}
 startThread(){const cfg=this.config;return {async runStreamed(){return {events:(async function*(){
   const env=cfg.mcp_servers.opencore.env,defs=JSON.parse(readFileSync(env.OPENCORE_MCP_TOOLS,'utf8'));
   async function call(name,arguments_){
     const response=await fetch(env.OPENCORE_MCP_BRIDGE,{method:'POST',headers:{authorization:'Bearer '+env.OPENCORE_MCP_TOKEN,'content-type':'application/json'},body:JSON.stringify({name,arguments:arguments_})});
     const result=await response.json();if(result.isError||result.error)throw Error('Runner tool failed: '+JSON.stringify(result));
   }
   if(defs.some(t=>t.function.name==='group_message'))await call('group_message',{text:'Group update'});
   await call('fixture_native',{value:'native'});
   yield {type:'item.completed',item:{id:'manifest',type:'agent_message',text:JSON.stringify(defs.map(t=>t.function.name))}};
   yield {type:'turn.completed',usage:{input_tokens:1,output_tokens:1}};
 })()};}};}
 close(){}
}
`;

for (const consent of [false, true]) {
  test(`Claude runner ${consent ? 'routes scoped group tools' : 'omits unapproved group tools'} and preserves native permissions`, {skip: process.platform !== 'win32'}, async t => {
    const f = await fixture(t, 'claude', consent);
    const root = path.join(f.root, 'claude');
    writePackage(root, '@anthropic-ai/claude-agent-sdk', claudeSdk);
    writePackage(root, 'zod', 'export const z={fromJSONSchema:()=>({shape:{}})};');
    const binary = path.join(root, 'node_modules', '@anthropic-ai', 'claude-agent-sdk-win32-x64');
    fs.mkdirSync(binary, {recursive: true}); fs.writeFileSync(path.join(binary, 'opencore-agent.exe'), 'never executed fixture');
    const events = await run(f, 'claude');
    assert.equal(f.requests.length, consent ? 1 : 0);
    assert.ok(events.some(event => event.kind === 'permission' && event.name === 'mcp__opencore__fixture_native'));
    assert.ok(events.some(event => event.kind === 'tool' && event.name === 'fixture_native'));
    assert.equal(events.some(event => ['permission','tool'].includes(event.kind) && event.name?.includes('group_')), false);
    assert.equal(events.some(event => event.kind === 'tool_result' && event.name === 'group_message' && event.value?.taskId === 'group-task'), consent);
  });

  test(`Codex runner ${consent ? 'routes scoped group tools' : 'omits unapproved group tools'} and preserves native permissions`, async t => {
    const f = await fixture(t, 'codex', consent);
    writePackage(path.join(f.root, 'codex'), '@openai/codex-sdk', codexSdk);
    const events = await run(f, 'codex');
    assert.equal(f.requests.length, consent ? 1 : 0);
    assert.ok(events.some(event => event.kind === 'permission' && event.name === 'fixture_native'));
    assert.ok(events.some(event => event.kind === 'tool_result' && event.name === 'fixture_native' && event.value?.nativeReceipt === 'native-dispatch-preserved'));
    assert.equal(events.some(event => ['permission','tool'].includes(event.kind) && event.name?.includes('group_')), false);
    assert.equal(events.some(event => event.kind === 'tool_result' && event.name === 'group_message' && event.value?.taskId === 'group-task'), consent);
  });
}
