import test from 'node:test';
import assert from 'node:assert/strict';
import { handleMcpMessage, MCP_PROTOCOL_VERSION } from '../src-tauri/resources/codex/mcp-protocol.mjs';

const definitions = [{ type: 'function', function: { name: 'test_action', description: 'Return a deterministic test result.', parameters: {
  type: 'object', properties: { value: { type: 'string' } }, required: ['value'], additionalProperties: false,
} } }];

test('OpenCore MCP server initializes and exposes only the current tool manifest', async () => {
  const initialized = await handleMcpMessage({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: MCP_PROTOCOL_VERSION } }, definitions, () => {});
  assert.equal(initialized.result.protocolVersion, MCP_PROTOCOL_VERSION);
  assert.equal(initialized.result.capabilities.tools.listChanged, true);
  const listed = await handleMcpMessage({ jsonrpc: '2.0', id: 2, method: 'tools/list' }, definitions, () => {});
  assert.deepEqual(listed.result.tools, [{ name: 'test_action', description: 'Return a deterministic test result.', inputSchema: definitions[0].function.parameters }]);
  assert.equal(await handleMcpMessage({ jsonrpc: '2.0', method: 'notifications/initialized' }, definitions, () => {}), null);
  const unsupported = await handleMcpMessage({ jsonrpc: '2.0', id: 9, method: 'initialize', params: { protocolVersion: '2099-01-01' } }, definitions, () => {});
  assert.equal(unsupported.result.protocolVersion, MCP_PROTOCOL_VERSION, 'the server must not claim unsupported MCP versions');
});

test('MCP tool calls preserve typed arguments and structured OpenCore results', async () => {
  const calls = [];
  const result = await handleMcpMessage({ jsonrpc: '2.0', id: 'call-1', method: 'tools/call', params: {
    name: 'test_action', arguments: { value: 'bridge me' },
  } }, definitions, async (name, args) => {
    calls.push({ name, args });
    return { content: [{ type: 'text', text: 'bridge me' }], structuredContent: { ok: true, value: 'bridge me' } };
  });
  assert.deepEqual(calls, [{ name: 'test_action', args: { value: 'bridge me' } }]);
  assert.deepEqual(result.result.content, [{ type: 'text', text: 'bridge me' }]);
  assert.deepEqual(result.result.structuredContent, { ok: true, value: 'bridge me' });
  assert.equal(result.result.isError, false);
});

test('unknown MCP tools fail without calling the OpenCore bridge', async () => {
  let called = false;
  const result = await handleMcpMessage({ jsonrpc: '2.0', id: 4, method: 'tools/call', params: { name: 'desktop_use', arguments: {} } }, definitions,
    async () => { called = true; });
  assert.equal(called, false);
  assert.equal(result.result.isError, true);
  assert.match(result.result.content[0].text, /Unknown OpenCore tool/);
});
