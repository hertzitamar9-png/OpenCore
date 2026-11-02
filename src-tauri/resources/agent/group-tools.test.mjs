import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createServer } from 'node:http';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { loadGroupTools } from './group-tools.mjs';

const token = 'group-task-token-0123456789abcdef0123456789';

function fixture(t) {
  const parent = fs.realpathSync(os.tmpdir());
  const appData = fs.mkdtempSync(path.join(parent, 'opencore-group-tools-'));
  const directory = path.join(appData, 'ai.opencore.control-center', 'group-agent-contexts');
  const file = path.join(directory, 'conversation-1.json');
  t.after(() => {
    const resolved = fs.realpathSync(appData);
    assert.ok(resolved.toLowerCase().startsWith((parent + path.sep + 'opencore-group-tools-').toLowerCase()));
    fs.rmSync(resolved, {recursive: true, force: true});
  });
  const events = [], warnings = [];
  const options = {appData, emit: event => events.push(event), warn: text => warnings.push(text)};
  return {appData, directory, file, events, warnings, options, save(extra = {}) {
    fs.mkdirSync(directory, {recursive: true});
    fs.writeFileSync(file, JSON.stringify({schema: 1, conversationId: 'conversation-1',
      url: 'http://127.0.0.1:8812/api/groups/agent/tool', token, expiresAt: Math.floor(Date.now()/1000)+600, ...extra}));
  }};
}

async function server(t, handler) {
  const requests = [];
  const service = createServer(async (request, response) => {
    let content = '';
    for await (const part of request) content += part;
    const record = {url: request.url, authorization: request.headers.authorization, body: JSON.parse(content || '{}')};
    requests.push(record);
    if (handler) return handler(request, response, record);
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify({ok: true, action: record.body.action, taskId: 'approved-task'}));
  });
  await new Promise(resolve => service.listen(0, '127.0.0.1', resolve));
  t.after(async () => { service.closeAllConnections(); await new Promise(resolve => service.close(resolve)); });
  return {requests, url: `http://127.0.0.1:${service.address().port}/api/groups/agent/tool`};
}

test('an ordinary conversation has no group tools and creates no context or profile files', t => {
  const f = fixture(t);
  assert.equal(loadGroupTools('conversation-1', f.options), null);
  assert.equal(fs.existsSync(f.directory), false);
  assert.deepEqual(f.warnings, []);
});

test('only a valid scoped conversation exposes the four group state tools', t => {
  const f = fixture(t); f.save();
  const tools = loadGroupTools('conversation-1', f.options);
  assert.deepEqual(tools.definitions.map(spec => spec.function.name), ['group_plan', 'group_message', 'group_delegate', 'group_context']);
  assert.equal(tools.hasTool('mcp__opencore__group_plan'), true);
  assert.equal(tools.hasTool('mcp__other__group_plan'), false);
  assert.equal(tools.hasTool('desktop_use'), false);
  assert.equal(tools.available(), true);
});

test('expired, mismatched, malformed, or traversal contexts never enable tools', t => {
  const f = fixture(t);
  for (const extra of [{schema: 2}, {conversationId: 'other'}, {token: 'short'}, {token: token+'\r\nOther: injected'},
    {expiresAt: 1}, {expiresAt: '9999999999'}]) {
    f.save(extra);
    assert.equal(loadGroupTools('conversation-1', f.options), null);
  }
  f.save();
  assert.equal(loadGroupTools('../conversation-1', f.options), null);
  assert.equal(loadGroupTools('a'.repeat(101), f.options), null);
  assert.equal(loadGroupTools(123, f.options), null);
  fs.writeFileSync(f.file, '{invalid');
  assert.equal(loadGroupTools('conversation-1', f.options), null);
  assert.ok(f.warnings.length >= 9);
  assert.equal(f.warnings.join(' ').includes(token), false);
});

test('only the exact IPv4 loopback group endpoint can receive the task bearer', t => {
  const f = fixture(t);
  for (const url of ['https://127.0.0.1:8812/api/groups/agent/tool', 'http://localhost:8812/api/groups/agent/tool',
    'http://127.0.0.2:8812/api/groups/agent/tool', 'http://[::1]:8812/api/groups/agent/tool',
    'http://127.0.0.1:8812/api/groups/agent/tool?next=external', 'http://127.0.0.1:8812/api/groups/agent/tool#secret',
    'http://user@127.0.0.1:8812/api/groups/agent/tool', 'http://127.0.0.1:8812/other',
    'http://127.0.0.1:65536/api/groups/agent/tool', 'http://2130706433:8812/api/groups/agent/tool']) {
    f.save({url});
    assert.equal(loadGroupTools('conversation-1', f.options), null, url);
  }
});

test('linked context files cannot grant group access', t => {
  const f = fixture(t); f.save();
  const other = path.join(f.appData, 'other.json');
  fs.renameSync(f.file, other);
  try { fs.symlinkSync(other, f.file); }
  catch (error) { if (['EPERM','EACCES'].includes(error.code)) return t.skip('Creating file links is unavailable'); throw error; }
  assert.equal(loadGroupTools('conversation-1', f.options), null);
});

test('group actions post only their allowed fields and retain paired timeline receipts', async t => {
  const f = fixture(t), s = await server(t); f.save({url: s.url});
  const tools = loadGroupTools('conversation-1', f.options);
  const inputs = [
    ['group_plan', {steps: [{id: 'research', text: 'Inspect the failing test', status: 'in_progress'}]}],
    ['group_message', {text: 'The local check passed'}],
    ['group_delegate', {agentId: 'other-pc', text: 'Run the approved verification task'}],
    ['group_context', {}],
  ];
  for (const [name, args] of inputs) {
    const result = await tools.callTool(name, args);
    assert.equal(result.isError, false);
    assert.equal(result.structuredContent.taskId, 'approved-task');
  }
  assert.deepEqual(s.requests.map(request => request.body), [
    {action: 'plan', steps: [{id: 'research', text: 'Inspect the failing test', status: 'in_progress'}]},
    {action: 'message', text: 'The local check passed'},
    {action: 'delegate', agentId: 'other-pc', text: 'Run the approved verification task'},
    {action: 'context'},
  ]);
  assert.ok(s.requests.every(request => request.authorization === 'Bearer '+token && request.url === '/api/groups/agent/tool'));
  assert.deepEqual(f.events.map(event => event.kind), ['tool_call','tool_result','tool_call','tool_result','tool_call','tool_result','tool_call','tool_result']);
  for (let index = 0; index < f.events.length; index += 2) {
    assert.equal(typeof f.events[index].id, 'string');
    assert.equal(f.events[index].id, f.events[index+1].id);
    assert.equal(f.events[index].name, f.events[index+1].name);
    assert.deepEqual(f.events[index].args, f.events[index+1].args);
  }
  assert.equal(JSON.stringify(f.events).includes(token), false);
});

test('expired, removed, or replaced context is revoked before another request', async t => {
  const f = fixture(t), s = await server(t); f.save({url: s.url});
  const tools = loadGroupTools('conversation-1', f.options);
  f.save({url: s.url, token: token+'rotated'});
  assert.equal((await tools.callTool('group_message', {text: 'must not post'})).isError, true);
  fs.unlinkSync(f.file);
  assert.equal(tools.available(), false);
  assert.equal((await tools.callTool('group_context', {})).isError, true);
  f.save({url: s.url, expiresAt: 1});
  assert.equal((await tools.callTool('group_context', {})).isError, true);
  assert.equal(s.requests.length, 0);
});

test('tool arguments cannot replace the action, endpoint, or approved task authority', async t => {
  const f = fixture(t), s = await server(t); f.save({url: s.url});
  const tools = loadGroupTools('conversation-1', f.options);
  for (const [name, args] of [
    ['group_message', {text: 'hello', action: 'delegate'}],
    ['group_delegate', {agentId: 'other-pc', text: 'hello', url: 'http://example.com'}],
    ['group_plan', {steps: [{text: 'Do work', status: 'approved'}]}],
    ['group_message', {text: '  '}],
    ['group_context', {token}],
  ]) assert.equal((await tools.callTool(name, args)).isError, true);
  assert.equal(s.requests.length, 0);
  assert.equal(JSON.stringify(f.events).includes(token), false, 'Rejected capability fields must not enter timeline events');
});

test('an HTTP redirect never forwards group authorization to another endpoint', async t => {
  const f = fixture(t), target = await server(t);
  const source = await server(t, (_request, response) => response.writeHead(307, {location: target.url}).end());
  f.save({url: source.url});
  const result = await loadGroupTools('conversation-1', f.options).callTool('group_message', {text: 'hello'});
  assert.equal(result.isError, true);
  assert.match(result.structuredContent.error, /307/);
  assert.equal(source.requests.length, 1);
  assert.equal(target.requests.length, 0);
});

test('service errors and malformed responses return an error receipt instead of success', async t => {
  const f = fixture(t), service = await server(t, (_request, response) => response.end('not JSON'));
  f.save({url: service.url});
  const result = await loadGroupTools('conversation-1', f.options).callTool('group_context', {});
  assert.equal(result.isError, true);
  assert.equal(f.events.at(-1).kind, 'tool_result');
  assert.equal(typeof f.events.at(-1).value.error, 'string');
});

test('an echoed task bearer stays out of model output and timeline receipts even when JSON escapes it', async t => {
  const f = fixture(t), unusualToken = 'task-"\\-'+token;
  const service = await server(t, (_request, response) => response.end(JSON.stringify({ok: true, echoed: unusualToken})));
  f.save({url: service.url, token: unusualToken});
  const tools = loadGroupTools('conversation-1', f.options);
  const result = await tools.callTool('group_context', {});
  assert.equal(result.structuredContent.echoed, '[scoped credential]');
  assert.equal(f.events.at(-1).value.echoed, '[scoped credential]');
  const refused = await tools.callTool('group_message', {text: unusualToken});
  assert.equal(refused.isError, true);
  assert.equal(service.requests.length, 1, 'A task bearer cannot become a shared group message');
});

test('a context directory junction cannot grant group access', {skip: process.platform !== 'win32'}, t => {
  const f = fixture(t); f.save();
  const raw = fs.readFileSync(f.file), other = path.join(f.appData, 'other-contexts');
  fs.unlinkSync(f.file); fs.rmdirSync(f.directory);
  fs.mkdirSync(other); fs.writeFileSync(path.join(other, 'conversation-1.json'), raw);
  fs.symlinkSync(other, f.directory, 'junction');
  assert.equal(loadGroupTools('conversation-1', f.options), null);
});

test('plan limits match the host: thirty steps and three hundred characters per step', async t => {
  const f = fixture(t), service = await server(t); f.save({url: service.url});
  const tools = loadGroupTools('conversation-1', f.options);
  assert.equal((await tools.callTool('group_plan', {steps: Array.from({length: 31}, () => ({text: 'Work', status: 'pending'}))})).isError, true);
  assert.equal((await tools.callTool('group_plan', {steps: [{text: 'x'.repeat(301), status: 'pending'}]})).isError, true);
  assert.equal(service.requests.length, 0);
  assert.equal((await tools.callTool('group_plan', {steps: Array.from({length: 30}, () => ({text: 'x'.repeat(300), status: 'pending'}))})).isError, false);
  assert.equal(service.requests.length, 1);
});

test('a real Python-created task context enables tools and preserves the queued delegation result', async t => {
  const f = fixture(t), service = await server(t, (_request, response) => response.end(JSON.stringify({
    taskId: 'delegated-task', task: {id: 'delegated-task', agentId: 'other-pc', modelId: 'shared-model', status: 'queued'},
  })));
  const resourceDirectory = fileURLToPath(new URL('../runtime-setup/hf-extension', import.meta.url));
  const program = `
import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from group_chats import GroupManager
class Remote:
    pc_name='Fixture PC'
manager=GroupManager(Path(sys.argv[2])/'managed',Remote())
group=manager.local('create',{'name':'Test group','displayName':'Owner'})['id']
shared=manager.local('sharing',{'id':group,'enabled':True,'models':[{'id':'fixture-model','label':'Fixture model'}]})
manager.local('message',{'id':group,'text':'Approved task','requestId':'fixture-request','agentIds':[shared['agents'][0]['id']]})
item=manager.local('worker/poll',{})['jobs'][0]
task=item['task']
manager.local('worker/register',{'connectionId':item['connectionId'],'taskId':task['id'],'leaseToken':task['leaseToken'],'conversationId':task['conversationId']},base_url=sys.argv[3])
print(json.dumps({'conversationId':task['conversationId']}))
manager.store.close()
`;
  const result = spawnSync(process.env.OPENCORE_TEST_PYTHON || 'python', ['-c', program, resourceDirectory, f.appData, new URL(service.url).origin],
    {encoding: 'utf8', windowsHide: true, env: {...process.env, APPDATA: f.appData}, timeout: 10000});
  assert.equal(result.status, 0, result.stderr || String(result.error || ''));
  const {conversationId} = JSON.parse(result.stdout);
  const tools = loadGroupTools(conversationId, f.options);
  assert.ok(tools, f.warnings.join('\n'));
  const delegated = await tools.callTool('group_delegate', {agentId: 'other-pc', text: 'Run the approved review'});
  assert.equal(delegated.isError, false);
  assert.deepEqual(delegated.structuredContent, {taskId: 'delegated-task', task: {id: 'delegated-task', agentId: 'other-pc', modelId: 'shared-model', status: 'queued'}});
  assert.equal(service.requests.length, 1);
  assert.equal(service.requests[0].body.action, 'delegate');
});
