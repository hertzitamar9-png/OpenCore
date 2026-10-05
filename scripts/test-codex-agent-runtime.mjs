import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { createInterface } from 'node:readline';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { assertAppServerSchemaMatches, codexRuntimeManifest } from './codex-package-version.mjs';

const PINNED_CODEX_VERSION = '0.160.0';
const appRoot = fileURLToPath(new URL('../', import.meta.url));
const resourcesDir = path.join(appRoot, 'src-tauri/resources/codex');
const manifest = codexRuntimeManifest();
assert.equal(manifest.cliVersion, PINNED_CODEX_VERSION);
assertAppServerSchemaMatches(manifest.schemaSha256, manifest.schemaSha256);
assert.throws(() => assertAppServerSchemaMatches('0'.repeat(64), manifest.schemaSha256), /schema hash mismatch/);

const nodeExecutable = path.join(appRoot, 'src-tauri/resources/claude', process.platform === 'win32' ? 'node.exe' : 'node');
assert.ok(existsSync(nodeExecutable), `packaged Node runtime is missing: ${nodeExecutable}; run scripts/prepare-claude-connector.mjs first`);

const root = mkdtempSync(path.join(tmpdir(), 'opencore-codex-app-server-e2e-'));
const workspace = path.join(root, 'workspace');
const codexHome = path.join(root, 'codex-home');
const scratch = path.join(root, 'scratch');
const toolDefinitionsFile = path.join(scratch, 'tools.json');
const bridgeToken = '0123456789abcdef0123456789abcdef';
const conversationId = 'codex-app-server-e2e-conversation';
const mcpStartedFile = path.join(root, 'mcp-started');
const mcpExitedFile = path.join(root, 'mcp-exited');
const hookFile = path.join(root, 'mcp-lifecycle-hook.cjs');
mkdirSync(workspace);
mkdirSync(codexHome);
mkdirSync(scratch);
writeFileSync(path.join(codexHome, 'config.toml'), '[analytics]\nenabled = false\n');

const tool = { type: 'function', function: { name: 'test_action', description: 'Return a deterministic test result.',
  parameters: { type: 'object', properties: { value: { type: 'string' } }, required: ['value'], additionalProperties: false } } };
writeFileSync(toolDefinitionsFile, JSON.stringify([tool]), { mode: 0o600 });
writeFileSync(hookFile, [
  "const fs = require('node:fs');",
  "if (process.env.OPENCORE_TEST_MCP_STARTED) fs.writeFileSync(process.env.OPENCORE_TEST_MCP_STARTED, String(process.pid));",
  "process.once('exit', () => { if (process.env.OPENCORE_TEST_MCP_EXITED) fs.writeFileSync(process.env.OPENCORE_TEST_MCP_EXITED, String(process.pid)); });",
].join('\n'));

let requestCount = 0;
let bridgedCall = null;
let bridgeRequests = 0;
let diagnostics = '';
let child;
let gateway;
let hardTimeout;
let testPassed = false;
let requestCountWaiters = [];
const modelRequests = [];
const notifications = [];
const events = [];
let assistantText = '';
let client;
let processClosed;

function waitUntil(predicate, label, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const poll = () => {
      if (predicate()) return resolve();
      if (Date.now() >= deadline) return reject(new Error(`Timed out waiting for ${label}`));
      setTimeout(poll, 25);
    };
    poll();
  });
}

function pidIsRunning(pid) {
  if (!Number.isSafeInteger(Number(pid)) || Number(pid) <= 0) return false;
  try { process.kill(Number(pid), 0); return true; }
  catch (error) { return error?.code === 'EPERM'; }
}

function signalRequestCount() {
  for (const waiter of requestCountWaiters) waiter();
  requestCountWaiters = [];
}

function waitForRequestCount(count, timeoutMs = 30_000) {
  if (requestCount >= count) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      requestCountWaiters = requestCountWaiters.filter(waiter => waiter !== done);
      reject(new Error(`Timed out waiting for local inference request ${count}; got ${requestCount}`));
    }, timeoutMs);
    const done = () => {
      if (requestCount < count) return;
      clearTimeout(timer);
      resolve();
    };
    requestCountWaiters.push(done);
  });
}

function waitForNotification(predicate, label, timeoutMs = 30_000) {
  const existing = notifications.findIndex(predicate);
  if (existing !== -1) return Promise.resolve(notifications.splice(existing, 1)[0]);
  return new Promise((resolve, reject) => {
    const waiter = { predicate, resolve };
    const timer = setTimeout(() => {
      client.notificationWaiters = client.notificationWaiters.filter(item => item !== waiter);
      reject(new Error(`Timed out waiting for app-server notification ${label}`));
    }, timeoutMs);
    waiter.resolve = value => { clearTimeout(timer); resolve(value); };
    client.notificationWaiters.push(waiter);
  });
}

function emitSse(response, type, sequence, fields = {}) {
  response.write(`event: ${type}\ndata: ${JSON.stringify({ type, sequence_number: sequence, ...fields })}\n\n`);
}

function responseBody(id, output, inputTokens = 73, outputTokens = 9) {
  return { id, object: 'response', created_at: 1, status: 'completed', error: null, incomplete_details: null,
    model: 'opencore', output, parallel_tool_calls: true,
    usage: { input_tokens: inputTokens, output_tokens: outputTokens, total_tokens: inputTokens + outputTokens } };
}

function sendModelTurn(response, id, output) {
  const body = responseBody(id, output);
  response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
  emitSse(response, 'response.created', 0, { response: { ...body, status: 'in_progress', output: [] } });
  let sequence = 1;
  output.forEach((item, outputIndex) => {
    emitSse(response, 'response.output_item.added', sequence++, { output_index: outputIndex, item });
    if (item.type === 'function_call') {
      emitSse(response, 'response.function_call_arguments.delta', sequence++, { item_id: item.id, output_index: outputIndex, delta: item.arguments });
      emitSse(response, 'response.function_call_arguments.done', sequence++, { item_id: item.id, output_index: outputIndex, arguments: item.arguments });
    } else if (item.type === 'message') {
      const text = item.content?.[0]?.text ?? '';
      emitSse(response, 'response.content_part.added', sequence++, { item_id: item.id, output_index: outputIndex, content_index: 0,
        part: { type: 'output_text', text: '', annotations: [] } });
      emitSse(response, 'response.output_text.delta', sequence++, { item_id: item.id, output_index: outputIndex, content_index: 0, delta: text });
      emitSse(response, 'response.output_text.done', sequence++, { item_id: item.id, output_index: outputIndex, content_index: 0, text });
    }
    emitSse(response, 'response.output_item.done', sequence++, { output_index: outputIndex, item });
  });
  emitSse(response, 'response.completed', sequence, { response: body });
  response.end();
}

function modelFunctionCall() {
  return { id: 'fc_open_core_test', type: 'function_call', status: 'completed', call_id: 'call_open_core_test',
    namespace: 'mcp__opencore', name: 'test_action', arguments: JSON.stringify({ value: 'bridge me' }) };
}

function responseForText(id, text) {
  return [{ id: `msg_${id}`, type: 'message', status: 'completed', role: 'assistant',
    content: [{ type: 'output_text', text, annotations: [] }] }];
}

function safeRuntimeEnvironment() {
  const safeKeys = ['PATH', 'Path', 'PATHEXT', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'ComSpec', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'];
  const env = {};
  for (const key of safeKeys) if (process.env[key] !== undefined) env[key] = process.env[key];
  env.CODEX_HOME = codexHome;
  return env;
}

class JsonRpcClient {
  constructor(process) {
    this.process = process;
    this.nextId = 1;
    this.pending = new Map();
    this.notificationWaiters = [];
    this.lines = createInterface({ input: process.stdout, crlfDelay: Infinity });
    this.lines.on('line', line => this.receive(line));
    process.stderr.on('data', chunk => { diagnostics += chunk.toString(); });
    process.on('error', error => { diagnostics += `app-server spawn error: ${error.stack ?? error}\n`; });
  }

  receive(line) {
    if (!line.trim()) return;
    let message;
    try { message = JSON.parse(line); } catch { diagnostics += `Malformed app-server JSON: ${line}\n`; return; }
    if (message.id !== undefined && this.pending.has(String(message.id))) {
      const pending = this.pending.get(String(message.id));
      this.pending.delete(String(message.id));
      clearTimeout(pending.timer);
      if (message.error) pending.reject(new Error(`app-server ${pending.method} failed: ${JSON.stringify(message.error)}`));
      else pending.resolve(message.result);
      return;
    }
    if (message.method && message.id !== undefined) {
      const result = message.method.toLowerCase().includes('approval') ? { decision: 'decline' } : {};
      this.process.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id: message.id, result })}\n`);
      diagnostics += `replied to app-server request ${message.method}\n`;
      return;
    }
    if (message.method) {
      notifications.push(message);
      if (message.method === 'item/agentMessage/delta') {
        assistantText += message.params?.delta ?? '';
        events.push({ kind: 'assistant', text: assistantText });
      }
      if (message.method === 'turn/completed') {
        const status = message.params?.turn?.status;
        events.push({ kind: ['interrupted', 'cancelled'].includes(status) ? 'turn_interrupted' : 'turn_completed',
          turnId: message.params?.turn?.id, status });
      }
      if (message.method === 'turn/interrupted' || message.method === 'turn/cancelled') events.push({ kind: 'turn_interrupted', turnId: message.params?.turn?.id });
      for (const waiter of [...this.notificationWaiters]) {
        if (waiter.predicate(message)) {
          this.notificationWaiters = this.notificationWaiters.filter(item => item !== waiter);
          waiter.resolve(message);
          break;
        }
      }
    }
  }

  send(method, params) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(String(id));
        reject(new Error(`app-server request ${method} timed out`));
      }, 25_000);
      this.pending.set(String(id), { method, resolve, reject, timer });
      this.process.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
    });
  }

  notify(method, params) {
    this.process.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method, ...(params === undefined ? {} : { params }) })}\n`);
  }
}

function terminalTurn(turnId) {
  return waitForNotification(message => ['turn/completed', 'turn/interrupted', 'turn/cancelled', 'turn/failed'].includes(message.method)
    && message.params?.turn?.id === turnId, `terminal event for turn ${turnId}`, 60_000);
}

async function closeAppServer() {
  if (!child || child.exitCode !== null || child.signalCode !== null) return { code: child?.exitCode ?? null, signal: child?.signalCode ?? null };
  child.stdin.end();
  const result = await Promise.race([processClosed, new Promise(resolve => setTimeout(() => resolve(null), 12_000))]);
  if (result) return result;
  if (process.platform === 'win32' && child.pid) {
    spawnSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  } else child.kill('SIGKILL');
  return await Promise.race([processClosed, new Promise(resolve => setTimeout(() => resolve(null), 5_000))]);
}

try {
  gateway = createServer(async (request, response) => {
    try {
      let raw = '';
      for await (const chunk of request) raw += chunk;
      if (request.url?.startsWith('/opencore/codex-tool/')) {
        const requestedToken = request.url.split('/').at(-1);
        assert.equal(requestedToken, bridgeToken);
        assert.equal(request.headers.authorization, `Bearer ${bridgeToken}`);
        assert.equal(request.method, 'POST');
        const toolRequest = JSON.parse(raw);
        bridgeRequests++;
        bridgedCall = toolRequest;
        response.writeHead(200, { 'content-type': 'application/json' });
        response.end(JSON.stringify({ content: [{ type: 'text', text: toolRequest.arguments?.value ?? '' }],
          structuredContent: { ok: true, value: toolRequest.arguments?.value ?? '' } }));
        return;
      }
      if (request.url !== '/v1/responses') { response.writeHead(404).end('{}'); return; }
      assert.equal(request.headers['x-opencore-harness'], 'codex-app-server');
      assert.equal(request.headers['x-echo-conversation'], conversationId);
      assert.equal(request.headers['x-opencore-timeline-owner'], 'app');
      const payload = JSON.parse(raw || '{}');
      assert.equal(payload.model, 'opencore');
      modelRequests.push({ model: payload.model, input: payload.input, tools: payload.tools });
      requestCount++;
      signalRequestCount();
      if (requestCount === 1) sendModelTurn(response, 'resp_local', responseForText('local', 'Local OpenCore response passed.'));
      else if (requestCount === 2) sendModelTurn(response, 'resp_tool', [modelFunctionCall()]);
      else if (requestCount === 3) sendModelTurn(response, 'resp_answer', responseForText('answer', 'OpenCore MCP tool loop passed.'));
      else if (requestCount === 4) {
        response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
        emitSse(response, 'response.created', 0, { response: { ...responseBody('resp_wait', []), status: 'in_progress', output: [] } });
        response.on('close', () => { if (!response.writableEnded) events.push({ kind: 'model_request_aborted' }); });
      } else sendModelTurn(response, `resp_resumed_${requestCount}`, responseForText('resumed', 'Resumed on the same local Codex thread.'));
    } catch (error) {
      diagnostics += `gateway error: ${error.stack ?? error}\n`;
      if (!response.headersSent) response.writeHead(500, { 'content-type': 'application/json' });
      response.end(JSON.stringify({ error: String(error?.message ?? error) }));
    }
  });
  await new Promise(resolve => gateway.listen(0, '127.0.0.1', resolve));

  child = spawn(manifest.appServerExecutable, ['app-server', '--listen', 'stdio://'], {
    cwd: workspace, env: safeRuntimeEnvironment(), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
  });
  processClosed = new Promise(resolve => child.once('close', (code, signal) => resolve({ code, signal })));
  client = new JsonRpcClient(child);
  hardTimeout = setTimeout(() => {
    if (process.platform === 'win32' && child.pid) spawnSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    else child.kill('SIGKILL');
  }, 150_000);

  const initialized = await client.send('initialize', { clientInfo: { name: 'opencore-packaged-runtime-test', version: '1.0.0' }, capabilities: {} });
  assert.equal(path.resolve(initialized.codexHome), path.resolve(codexHome), 'app-server must honor the isolated CODEX_HOME');
  client.notify('initialized');
  const gatewayUrl = `http://127.0.0.1:${gateway.address().port}`;
  const configuration = {
    model_provider: 'opencore', model: 'opencore', model_context_window: 16_384, model_auto_compact_token_limit: 12_000,
    model_providers: { opencore: { name: 'OpenCore local model', base_url: `${gatewayUrl}/v1`, wire_api: 'responses',
      requires_openai_auth: false, supports_websockets: false,
      http_headers: { 'x-opencore-harness': 'codex-app-server', 'x-echo-conversation': conversationId,
        'x-opencore-effort': 'off', 'x-opencore-timeline-owner': 'app' } } },
    sandbox_mode: 'read-only', sandbox_workspace_write: { writable_roots: [workspace], network_access: false },
    approval_policy: 'on-request', project_doc_max_bytes: 0, features: { multi_agent: false }, agents: { max_threads: 1 },
    mcp_servers: { opencore: { command: nodeExecutable, args: [path.join(resourcesDir, 'mcp-server.mjs')], default_tools_approval_mode: 'approve',
      env: { OPENCORE_MCP_BRIDGE: `${gatewayUrl}/opencore/codex-tool/${bridgeToken}`, OPENCORE_MCP_TOKEN: bridgeToken,
        OPENCORE_MCP_TOOLS: toolDefinitionsFile, OPENCORE_TEST_MCP_STARTED: mcpStartedFile,
        OPENCORE_TEST_MCP_EXITED: mcpExitedFile, NODE_OPTIONS: `--require=${hookFile}` },
      startup_timeout_sec: 30, tool_timeout_sec: 60 } },
  };
  const threadParams = threadId => ({ cwd: workspace, model: 'opencore', modelProvider: 'opencore',
    developerInstructions: 'Use the registered test action once, then answer briefly.', approvalPolicy: 'on-request', sandbox: 'read-only',
    config: configuration, ...(threadId ? { threadId } : {}) });
  const started = await client.send('thread/start', threadParams());
  const threadId = started?.thread?.id;
  assert.equal(typeof threadId, 'string', `thread/start must return a durable thread id: ${JSON.stringify(started)}`);
  events.push({ kind: 'app_server_started', pid: child.pid, threadId });

  await client.send('config/mcpServer/reload', null);
  await waitUntil(() => existsSync(mcpStartedFile), 'the real packaged OpenCore MCP server to start');
  const localTurn = await client.send('turn/start', { threadId, cwd: workspace, model: 'opencore', input: [{ type: 'text', text: 'Reply with the local response check phrase.' }] });
  const localTerminal = await terminalTurn(localTurn.turn.id);
  assert.equal(localTerminal.method, 'turn/completed', `local tool-free turn failed: ${JSON.stringify(localTerminal)}`);
  assert.ok(assistantText.includes('Local OpenCore response passed.'), `expected a local tool-free answer, got ${assistantText}`);

  const firstTurn = await client.send('turn/start', { threadId, cwd: workspace, model: 'opencore', input: [{ type: 'text', text: 'Call the test action with value bridge me, then report the result.' }] });
  assert.equal(typeof firstTurn?.turn?.id, 'string', `turn/start must return a turn id: ${JSON.stringify(firstTurn)}`);
  const firstTerminal = await terminalTurn(firstTurn.turn.id);
  assert.equal(firstTerminal.method, 'turn/completed', `tool turn did not complete: ${JSON.stringify(firstTerminal)}`);
  assert.equal(requestCount, 3, `expected tool selection and follow-up inference after the tool-free response, got ${requestCount}`);
  assert.equal(bridgeRequests, 1, 'the app-server must call the real MCP server and authenticated bridge exactly once');
  assert.deepEqual(bridgedCall, { name: 'test_action', arguments: { value: 'bridge me' } });
  assert.ok(JSON.stringify(modelRequests[1]?.tools).includes('test_action'), 'the local model request must include the live MCP tool definition');
  assert.ok(JSON.stringify(modelRequests[2]?.input).includes('bridge me'), 'the completed MCP result must return to local inference');
  assert.ok(assistantText.includes('OpenCore MCP tool loop passed.'), `expected final assistant text, got ${assistantText}`);

  const cancelledTurn = await client.send('turn/start', { threadId, cwd: workspace, model: 'opencore', input: [{ type: 'text', text: 'Wait for cancellation.' }] });
  assert.equal(typeof cancelledTurn?.turn?.id, 'string');
  await waitForRequestCount(4);
  await client.send('turn/interrupt', { threadId, turnId: cancelledTurn.turn.id });
  const interrupted = await terminalTurn(cancelledTurn.turn.id);
  assert.ok(['interrupted', 'cancelled'].includes(interrupted.params?.turn?.status), `expected interrupted turn status: ${JSON.stringify(interrupted)}`);
  await waitUntil(() => events.some(event => event.kind === 'model_request_aborted'), 'local inference request cancellation');

  const resumed = await client.send('thread/resume', threadParams(threadId));
  assert.equal(resumed?.thread?.id, threadId, 'thread/resume must return the same durable local thread');
  await client.send('config/mcpServer/reload', null);
  const resumedTurn = await client.send('turn/start', { threadId, cwd: workspace, model: 'opencore', input: [{ type: 'text', text: 'Continue after cancellation.' }] });
  const resumedTerminal = await terminalTurn(resumedTurn.turn.id);
  assert.equal(resumedTerminal.method, 'turn/completed', `resumed turn failed: ${JSON.stringify(resumedTerminal)}`);
  assert.equal(child.pid, events.find(event => event.kind === 'app_server_started')?.pid,
    'resume must reuse the same scoped app-server process rather than starting another one');
  assert.ok(assistantText.includes('Resumed on the same local Codex thread.'), assistantText);

  const closed = await closeAppServer();
  assert.equal(closed?.code, 0, `app-server did not exit cleanly: ${JSON.stringify(closed)}\n${diagnostics}`);
  const mcpPid = Number(readFileSync(mcpStartedFile, 'utf8'));
  await waitUntil(() => !pidIsRunning(mcpPid), `packaged MCP child process ${mcpPid} to exit`);
  if (existsSync(mcpExitedFile)) assert.equal(readFileSync(mcpExitedFile, 'utf8'), String(mcpPid));

  const toolOutputs = modelRequests[2]?.input?.filter(item => ['function_call_output', 'custom_tool_call_output'].includes(item.type)) ?? [];
  const evidence = JSON.stringify({ threadId, appServerPid: child.pid, requestCount, bridgeRequests, bridgedCall,
    toolOutputs, events, notifications: notifications.map(item => item.method), diagnostics });
  assert.ok(toolOutputs.some(item => JSON.stringify(item).includes('bridge me')), `MCP output was not included in the next local inference: ${evidence}`);
  assert.ok(events.some(event => event.kind === 'turn_completed'), evidence);
  assert.ok(events.some(event => event.kind === 'turn_interrupted'), evidence);
  assert.ok(events.some(event => event.kind === 'app_server_started'), evidence);
  assert.equal(pidIsRunning(child.pid), false, `app-server child ${child.pid} remained alive after close`);
  testPassed = true;
  console.log(JSON.stringify({ codexAppServer: manifest.cliVersion, schemaSha256: manifest.schemaSha256,
    sameThreadResumed: true, sameAppServerProcess: true, mcpToolCalls: bridgeRequests,
    cancelledInferenceRequests: events.filter(event => event.kind === 'model_request_aborted').length,
    mcpChildExited: true, hostedInference: false, passed: true }));
} finally {
  if (hardTimeout) clearTimeout(hardTimeout);
  if (child && (child.exitCode === null && child.signalCode === null || pidIsRunning(child.pid))) {
    await closeAppServer();
    if (pidIsRunning(child.pid)) spawnSync(process.platform === 'win32' ? 'taskkill.exe' : 'kill',
      process.platform === 'win32' ? ['/PID', String(child.pid), '/T', '/F'] : ['-KILL', String(child.pid)],
      { windowsHide: true, stdio: 'ignore' });
  }
  if (!testPassed && existsSync(mcpStartedFile)) {
    const mcpPid = Number(readFileSync(mcpStartedFile, 'utf8'));
    if (pidIsRunning(mcpPid)) spawnSync(process.platform === 'win32' ? 'taskkill.exe' : 'kill',
      process.platform === 'win32' ? ['/PID', String(mcpPid), '/T', '/F'] : ['-KILL', String(mcpPid)],
      { windowsHide: true, stdio: 'ignore' });
  }
  gateway?.closeAllConnections?.();
  if (gateway?.listening) await new Promise(resolve => gateway.close(resolve));
  if (testPassed) rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  else console.error(`Preserved failed integration evidence at ${root}`);
}
