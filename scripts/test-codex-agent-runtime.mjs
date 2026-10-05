import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { createInterface } from 'node:readline';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildCodexConfiguration } from '../src-tauri/resources/codex/codex-config.mjs';

const appRoot = fileURLToPath(new URL('../', import.meta.url));
const resourcesDir = path.join(appRoot, 'src-tauri/resources/codex');
const workspace = mkdtempSync(path.join(tmpdir(), 'opencore-codex-agent-e2e-'));
const codexHome = path.join(workspace, 'codex-home');
const scratch = path.join(workspace, 'scratch');
mkdirSync(scratch);
const configProbe = buildCodexConfiguration({ gatewayUrl: 'http://127.0.0.1:8812/v1', bridgeUrl: 'http://127.0.0.1:8813/tool',
  bridgeToken: '0123456789abcdef0123456789abcdef', conversationId: 'codex-config-test', model: 'opencore',
  contextWindowTokens: 16384, compactAtTokens: 12000, developerInstructions: '', workspace, nodeExecutable: process.execPath,
  mcpServerScript: path.join(resourcesDir, 'mcp-server.mjs'), toolDefinitionsFile: path.join(scratch, 'tools.json'),
  effort: 'off', sandboxMode: 'read-only', approvalPolicy: 'never', networkAccess: false, subagentsEnabled: false,
  maxSubagents: 1, projectSkillsEnabled: false });
assert.equal(configProbe.mcp_servers.opencore.default_tools_approval_mode, 'approve',
  'Codex must trust its private MCP server so the OpenCore app approval RPC can run');
const tool = { type: 'function', function: { name: 'test_action', description: 'Return a deterministic test result.',
  parameters: { type: 'object', properties: { value: { type: 'string' } }, required: ['value'], additionalProperties: false } } };
const toolDefinitionsFile = path.join(scratch, 'tools.json');
writeFileSync(toolDefinitionsFile, JSON.stringify([tool]), { mode: 0o600 });

let requestCount = 0;
let bridgedCall = null;
let permissionCalls = 0;
let toolCalls = 0;
let diagnostics = '';
let child;
let gateway;
let timeout;
const events = [];
const modelRequests = [];

function emitSse(response, type, sequence, fields = {}) {
  const value = { type, sequence_number: sequence, ...fields };
  response.write(`event: ${type}\ndata: ${JSON.stringify(value)}\n\n`);
}

function responseBody(id, output, inputTokens = 73, outputTokens = 9) {
  return { id, object: 'response', created_at: 1, status: 'completed', error: null, incomplete_details: null,
    model: 'opencore', output, parallel_tool_calls: true,
    usage: { input_tokens: inputTokens, output_tokens: outputTokens, total_tokens: inputTokens + outputTokens } };
}

function sendModelTurn(response, id, output) {
  const body = responseBody(id, output);
  response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
  const starting = { ...body, status: 'in_progress', output: [] };
  emitSse(response, 'response.created', 0, { response: starting });
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

try {
  gateway = createServer(async (request, response) => {
    try {
      let raw = '';
      for await (const chunk of request) raw += chunk;
      const payload = JSON.parse(raw || '{}');
      if (!request.url?.endsWith('/responses')) { response.writeHead(404).end('{}'); return; }
      modelRequests.push({ model: payload.model, input: payload.input, tools: payload.tools?.map(value => ({ type: value.type,
        name: value.name, description: value.description, parameters: value.parameters, function: value.function, tools: value.tools })),
        toolChoice: payload.tool_choice, previousResponseId: payload.previous_response_id });
      assert.equal(request.headers['x-opencore-harness'], 'codex-sdk');
      assert.equal(request.headers['x-echo-conversation'], 'codex-e2e-conversation');
      assert.equal(request.headers['x-opencore-timeline-owner'], 'app');
      requestCount++;
      if (requestCount === 1) {
        sendModelTurn(response, 'resp_tool', [modelFunctionCall()]);
      } else {
        sendModelTurn(response, 'resp_answer', [{ id: 'msg_final', type: 'message', status: 'completed', role: 'assistant',
          content: [{ type: 'output_text', text: 'OpenCore MCP tool loop passed.', annotations: [] }] }]);
      }
    } catch (error) {
      diagnostics += `${String(error?.stack ?? error)}\n`;
      if (!response.headersSent) response.writeHead(500, { 'content-type': 'application/json' });
      response.end(JSON.stringify({ error: String(error?.message ?? error) }));
    }
  });
  await new Promise(resolve => gateway.listen(0, '127.0.0.1', resolve));

  child = spawn(process.execPath, [path.join(resourcesDir, 'runner.mjs')], { cwd: workspace, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  child.stderr.on('data', chunk => { diagnostics += chunk.toString(); });
  const closed = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code, signal) => resolve({ code, signal }));
  });
  createInterface({ input: child.stdout, crlfDelay: Infinity }).on('line', line => {
    let event;
    try { event = JSON.parse(line); } catch { diagnostics += `Invalid runner JSON: ${line}\n`; return; }
    events.push(event);
    if (event.kind === 'permission') {
      permissionCalls++;
      child.stdin.write(`${JSON.stringify({ kind: 'reply', id: event.id, value: true })}\n`);
    } else if (event.kind === 'tool') {
      toolCalls++;
      bridgedCall = { name: event.name, arguments: event.args };
      const value = { ok: true, value: event.args.value };
      child.stdin.write(`${JSON.stringify({ kind: 'reply', id: event.id, value })}\n`);
    }
  });

  const gatewayUrl = `http://127.0.0.1:${gateway.address().port}/v1`;
  const config = { kind: 'start', workspace, codexHome, resourcesDir, nodeExecutable: process.execPath,
    gatewayUrl, conversationId: 'codex-e2e-conversation', effort: 'off', approvalMode: 'ask-every-time', model: 'opencore', resume: null,
    content: 'Call the test action with value bridge me, then report the result.',
    instructions: 'Use the provided test action once and then answer briefly.', tools: [tool],
    subagentsEnabled: false, maxSubagents: 1, projectSkillsEnabled: false, contextWindowTokens: 16384, compactAtTokens: 12000 };
  child.stdin.write(`${JSON.stringify(config)}\n`);

  timeout = setTimeout(() => {
    if (process.platform === 'win32' && child.pid) {
      spawnSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    } else child.kill();
  }, 120_000);
  const result = await closed;
  clearTimeout(timeout);
  timeout = null;
  assert.equal(result.code, 0, diagnostics || JSON.stringify({ ...result, events }));
  const compactInputs = modelRequests.map(request => ({
    tools: request.tools?.map(tool => ({ type: tool.type, name: tool.name,
      nested: tool.tools?.map(child => child.name) })),
    input: Array.isArray(request.input) ? request.input.map(item => ({ type: item.type, role: item.role,
      name: item.name, call_id: item.call_id, output: item.output, arguments: item.arguments,
      content: typeof item.content === 'string' ? item.content.slice(0, 120) : undefined })) : request.input,
  }));
  assert.equal(requestCount, 2, `expected one tool-selection inference and one resumed inference; got ${requestCount}: ${JSON.stringify({ compactInputs, events, diagnostics })}`);
  const providedTools = (modelRequests[0]?.tools ?? []).flatMap(value => value.type === 'namespace'
    ? (value.tools ?? []).map(nested => ({ ...nested, name: `${value.name}__${nested.name}` })) : [value]);
  assert.ok(providedTools.some(value => value.name === 'mcp__opencore__test_action'),
    `the local model request must contain the dynamically registered OpenCore MCP tool: ${JSON.stringify(modelRequests[0]?.tools)}`);
  const toolOutputs = (modelRequests[1]?.input ?? []).filter(item => ['function_call_output', 'custom_tool_call_output'].includes(item.type));
  assert.ok(toolOutputs.some(item => JSON.stringify(item).includes('bridge me')),
    `the model must receive the completed MCP tool result before it answers: ${JSON.stringify(modelRequests[1]?.input)}`);
  const evidence = JSON.stringify({ permissionCalls, toolCalls, bridgedCall, modelRequests, events, diagnostics });
  assert.equal(permissionCalls, 1, evidence);
  assert.equal(toolCalls, 1, evidence);
  assert.deepEqual(bridgedCall, { name: 'test_action', arguments: { value: 'bridge me' } }, evidence);
  assert.ok(events.some(event => event.kind === 'assistant' && event.text === 'OpenCore MCP tool loop passed.'), evidence);
  assert.ok(events.some(event => event.kind === 'turn_completed'), evidence);
  console.log(JSON.stringify({ sdk: '0.160.0', modelRequests: requestCount, permissionChecks: permissionCalls,
    mcpCalls: toolCalls, assistantResponse: 'OpenCore MCP tool loop passed.', passed: true }));
} finally {
  if (timeout) clearTimeout(timeout);
  if (child && child.exitCode === null && child.signalCode === null) {
    if (process.platform === 'win32' && child.pid) spawnSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    else child.kill();
  }
  gateway?.closeAllConnections?.();
  if (gateway?.listening) await new Promise(resolve => gateway.close(resolve));
  rmSync(workspace, { recursive: true, force: true });
}
