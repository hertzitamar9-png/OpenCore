import test from 'node:test';
import assert from 'node:assert/strict';
import { handleMcpMessage, MCP_PROTOCOL_VERSION } from '../src-tauri/resources/codex/mcp-protocol.mjs';
import { createOpenCoreToolDispatcher, queuedBackgroundJob } from '../src-tauri/resources/codex/tool-relay.mjs';

const definitions = [{ type: 'function', function: { name: 'music_generate', description: 'Queue generation', parameters: {
  type: 'object', properties: { prompt: { type: 'string' } }, required: ['prompt'],
} } }];

test('OpenCore MCP endpoint initializes and exposes only the app-provided tool manifest', async () => {
  const initialized = await handleMcpMessage({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: MCP_PROTOCOL_VERSION } }, definitions, () => {});
  assert.equal(initialized.result.protocolVersion, MCP_PROTOCOL_VERSION);
  assert.equal(initialized.result.capabilities.tools.listChanged, true);
  const listed = await handleMcpMessage({ jsonrpc: '2.0', id: 2, method: 'tools/list' }, definitions, () => {});
  assert.deepEqual(listed.result.tools, [{ name: 'music_generate', description: 'Queue generation', inputSchema: definitions[0].function.parameters }]);
  assert.equal(await handleMcpMessage({ jsonrpc: '2.0', method: 'notifications/initialized' }, definitions, () => {}), null);
  const unknown = await handleMcpMessage({ jsonrpc: '2.0', id: 9, method: 'initialize', params: { protocolVersion: '2099-01-01' } }, definitions, () => {});
  assert.equal(unknown.result.protocolVersion, MCP_PROTOCOL_VERSION, 'the server must not claim unsupported MCP versions');
});

test('MCP calls require the Rust permission RPC before a tool action and redact image bytes from structured output', async () => {
  const calls = [];
  const rpc = async (kind, payload) => {
    calls.push(kind);
    if (kind === 'permission') return true;
    return { id: 'job-1', status: 'queued', category: 'music', dataUrl: 'data:image/png;base64,AA==' };
  };
  const emitted = [];
  const dispatch = createOpenCoreToolDispatcher({ rpc, emit: event => emitted.push(event) });
  const result = await handleMcpMessage({ jsonrpc: '2.0', id: 'call-1', method: 'tools/call', params: { name: 'music_generate', arguments: { prompt: 'AI song' } } }, definitions, dispatch);
  assert.deepEqual(calls, ['permission', 'tool']);
  assert.equal(result.result.isError, false);
  assert.equal(result.result.content[1].type, 'image');
  assert.equal(result.result.structuredContent.dataUrl, undefined);
  assert.deepEqual(emitted.find(event => event.kind === 'handoff'), { kind: 'handoff', jobId: 'job-1', category: 'music' });
});

test('denied and unknown MCP tools never execute', async () => {
  const calls = [];
  const dispatch = createOpenCoreToolDispatcher({ rpc: async kind => { calls.push(kind); return false; } });
  const denied = await handleMcpMessage({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'music_generate', arguments: {} } }, definitions, dispatch);
  const missing = await handleMcpMessage({ jsonrpc: '2.0', id: 4, method: 'tools/call', params: { name: 'desktop_use', arguments: {} } }, definitions, dispatch);
  assert.equal(denied.result.isError, true);
  assert.equal(missing.result.isError, true);
  assert.deepEqual(calls, ['permission']);
});

test('studio and wait requests are recognized as asynchronous handoffs only when queued', () => {
  assert.deepEqual(queuedBackgroundJob('studio_use', { action: 'generate' }, { id: 'a', status: 'queued', category: '3d' }), { jobId: 'a', category: '3d' });
  assert.equal(queuedBackgroundJob('background_wait', {}, { id: 'b', status: 'complete' }), null);
  assert.equal(queuedBackgroundJob('dev', {}, { id: 'c', status: 'queued' }), null);
});
