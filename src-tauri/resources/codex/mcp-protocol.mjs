export const MCP_PROTOCOL_VERSION = '2025-06-18';
const SUPPORTED_PROTOCOL_VERSIONS = new Set(['2024-11-05', '2025-03-26', MCP_PROTOCOL_VERSION]);

function jsonRpcError(id, code, message) {
  return { jsonrpc: '2.0', id: id ?? null, error: { code, message } };
}

export async function handleMcpMessage(message, toolDefinitions, callTool) {
  if (!message || message.jsonrpc !== '2.0' || typeof message.method !== 'string') {
    return jsonRpcError(message?.id, -32600, 'Invalid JSON-RPC request');
  }
  const id = message.id;
  if (message.method === 'notifications/initialized' || message.method.startsWith('notifications/')) return null;
  if (message.method === 'ping') return { jsonrpc: '2.0', id, result: {} };
  if (message.method === 'initialize') {
    const proposed = message.params?.protocolVersion;
    return {
      jsonrpc: '2.0', id,
      result: {
        protocolVersion: SUPPORTED_PROTOCOL_VERSIONS.has(proposed) ? proposed : MCP_PROTOCOL_VERSION,
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: 'opencore-runtime-tools', version: '1.0.0' },
      },
    };
  }
  if (message.method === 'tools/list') {
    const tools = (toolDefinitions ?? []).map(spec => ({
      name: spec.function?.name,
      description: spec.function?.description ?? '',
      inputSchema: spec.function?.parameters ?? { type: 'object', properties: {} },
    })).filter(tool => typeof tool.name === 'string' && tool.name.length > 0);
    return { jsonrpc: '2.0', id, result: { tools } };
  }
  if (message.method === 'tools/call') {
    const name = message.params?.name;
    const tool = (toolDefinitions ?? []).find(item => item.function?.name === name);
    if (!tool) return { jsonrpc: '2.0', id, result: { content: [{ type: 'text', text: `Unknown OpenCore tool: ${String(name)}` }], isError: true } };
    try {
      const result = await callTool(name, message.params?.arguments ?? {});
      const content = Array.isArray(result?.content) ? result.content : [{ type: 'text', text: JSON.stringify(result?.value ?? result ?? null) }];
      return { jsonrpc: '2.0', id, result: { content, isError: Boolean(result?.isError), ...(result?.structuredContent === undefined ? {} : { structuredContent: result.structuredContent }) } };
    } catch (error) {
      return { jsonrpc: '2.0', id, result: { content: [{ type: 'text', text: String(error?.message ?? error) }], isError: true } };
    }
  }
  return jsonRpcError(id, -32601, `Method not found: ${message.method}`);
}
