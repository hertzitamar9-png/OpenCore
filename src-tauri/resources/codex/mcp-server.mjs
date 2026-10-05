// Codex communicates with this process using MCP JSON-RPC over stdio.
// stdout is reserved for protocol messages; diagnostics go to stderr.
import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { handleMcpMessage } from './mcp-protocol.mjs';

const bridge = process.env.OPENCORE_MCP_BRIDGE;
const token = process.env.OPENCORE_MCP_TOKEN;
const toolsPath = process.env.OPENCORE_MCP_TOOLS;
if (!bridge || !token || token.length < 24 || !toolsPath) throw new Error('OpenCore MCP bridge configuration is incomplete');
const bridgeUrl = new URL(bridge);
if (bridgeUrl.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(bridgeUrl.hostname)) {
  throw new Error('OpenCore MCP bridge must use local loopback HTTP');
}
let toolDefinitionsBytes = readFileSync(toolsPath);
let toolDefinitions = JSON.parse(toolDefinitionsBytes.toString('utf8'));
if (!Array.isArray(toolDefinitions)) throw new Error('OpenCore tool definitions must be an array');

async function callTool(name, args) {
  const response = await fetch(bridgeUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
    body: JSON.stringify({ name, arguments: args }),
    signal: AbortSignal.timeout(600_000),
  });
  const body = await response.text();
  if (!response.ok) throw new Error(`OpenCore tool relay returned HTTP ${response.status}: ${body.slice(0, 1024)}`);
  return JSON.parse(body);
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of input) {
  if (Buffer.byteLength(line) > 8 * 1024 * 1024) {
    process.stderr.write('OpenCore MCP request exceeded 8 MiB.\n');
    process.exitCode = 1;
    break;
  }
  try {
    const currentBytes = readFileSync(toolsPath);
    if (!currentBytes.equals(toolDefinitionsBytes)) {
      const current = JSON.parse(currentBytes.toString('utf8'));
      if (!Array.isArray(current)) throw new Error('OpenCore tool definitions must be an array');
      toolDefinitionsBytes = currentBytes;
      toolDefinitions = current;
      process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', method: 'notifications/tools/list_changed' })}\n`);
    }
    const response = await handleMcpMessage(JSON.parse(line), toolDefinitions, callTool);
    if (response !== null) process.stdout.write(`${JSON.stringify(response)}\n`);
  } catch (error) {
    process.stderr.write(`OpenCore MCP request failed: ${String(error?.message ?? error)}\n`);
  }
}
