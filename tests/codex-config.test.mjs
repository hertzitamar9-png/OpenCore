import test from 'node:test';
import assert from 'node:assert/strict';
import { agentPermissions, buildCodexConfiguration } from '../src-tauri/resources/codex/codex-config.mjs';

test('approval modes map to bounded Codex sandbox capabilities', () => {
  assert.deepEqual(agentPermissions('ask-every-time'), { sandboxMode: 'read-only', approvalPolicy: 'never', networkAccess: false });
  assert.deepEqual(agentPermissions('approve-for-me'), { sandboxMode: 'read-only', approvalPolicy: 'never', networkAccess: false });
  assert.deepEqual(agentPermissions('allow-chat'), { sandboxMode: 'workspace-write', approvalPolicy: 'never', networkAccess: false });
  assert.deepEqual(agentPermissions('allow-all'), { sandboxMode: 'danger-full-access', approvalPolicy: 'never', networkAccess: true });
});

test('Codex runs against the selected local OpenCore Responses endpoint with scoped tools', () => {
  const config = buildCodexConfiguration({
    gatewayUrl: 'http://127.0.0.1:8812/v1',
    conversationId: 'conversation-7',
    model: 'opencore:medium',
    contextWindowTokens: 262144,
    compactAtTokens: 200000,
    developerInstructions: 'OpenCore task instructions',
    workspace: 'C:\\workspace',
    nodeExecutable: 'C:\\OpenCore\\node.exe',
    mcpServerScript: 'C:\\OpenCore\\mcp-server.mjs',
    toolDefinitionsFile: 'C:\\OpenCore\\tools.json',
    bridgeUrl: 'http://127.0.0.1:41234/tool',
    bridgeToken: 'one-time-token-with-at-least-24-characters',
    effort: 'off',
    projectSkillsEnabled: true,
    sandboxMode: 'workspace-write',
    approvalPolicy: 'never',
    networkAccess: false,
    subagentsEnabled: true,
    maxSubagents: 3,
  });

  assert.equal(config.model_provider, 'opencore');
  assert.equal(config.model, 'opencore:medium');
  assert.equal(config.model_context_window, 262144);
  assert.equal(config.model_auto_compact_token_limit, 200000);
  assert.equal(config.developer_instructions, 'OpenCore task instructions');
  assert.equal(config.model_providers.opencore.base_url, 'http://127.0.0.1:8812/v1');
  assert.equal(config.model_providers.opencore.wire_api, 'responses');
  assert.equal(config.model_providers.opencore.requires_openai_auth, false);
  assert.equal(config.model_providers.opencore.http_headers['x-echo-conversation'], 'conversation-7');
  assert.equal(config.model_providers.opencore.http_headers['x-opencore-harness'], 'codex-sdk');
  assert.equal(config.model_providers.opencore.http_headers['x-opencore-effort'], 'off');
  assert.equal(config.model_providers.opencore.http_headers['x-opencore-timeline-owner'], 'app');
  assert.equal(config.sandbox_mode, 'workspace-write');
  assert.equal(config.sandbox_workspace_write.network_access, false);
  assert.equal(config.mcp_servers.opencore.command, 'C:\\OpenCore\\node.exe');
  assert.deepEqual(config.mcp_servers.opencore.args, ['C:\\OpenCore\\mcp-server.mjs']);
  assert.equal(config.mcp_servers.opencore.env.OPENCORE_MCP_BRIDGE, 'http://127.0.0.1:41234/tool');
  assert.equal(config.mcp_servers.opencore.env.OPENCORE_MCP_TOKEN, 'one-time-token-with-at-least-24-characters');
  assert.equal(config.mcp_servers.opencore.env.OPENCORE_MCP_TOOLS, 'C:\\OpenCore\\tools.json');
  assert.equal(config.features.multi_agent, true);
  assert.equal(config.agents.max_threads, 3);
});

test('Codex settings reject unusable context budgets and cap agent fanout', () => {
  assert.throws(() => buildCodexConfiguration({
    gatewayUrl: 'http://127.0.0.1:8812/v1', conversationId: 'x', model: 'opencore:low',
    contextWindowTokens: 4096, compactAtTokens: 3000, developerInstructions: '', workspace: 'C:\\w',
    nodeExecutable: 'node.exe', mcpServerScript: 'mcp.mjs', toolDefinitionsFile: 'tools.json',
    bridgeUrl: 'http://127.0.0.1:41234/tool', bridgeToken: 'token-with-at-least-twenty-four-characters', sandboxMode: 'read-only',
    effort: 'medium', projectSkillsEnabled: false,
    approvalPolicy: 'never', networkAccess: false, subagentsEnabled: true, maxSubagents: 3,
  }), /context window must be at least/i);

  const config = buildCodexConfiguration({
    gatewayUrl: 'http://127.0.0.1:8812/v1', conversationId: 'x', model: 'opencore:low',
    contextWindowTokens: 16384, compactAtTokens: 14000, developerInstructions: '', workspace: 'C:\\w',
    nodeExecutable: 'node.exe', mcpServerScript: 'mcp.mjs', toolDefinitionsFile: 'tools.json',
    bridgeUrl: 'http://127.0.0.1:41234/tool', bridgeToken: 'token-with-at-least-twenty-four-characters', sandboxMode: 'read-only',
    effort: 'medium', projectSkillsEnabled: true,
    approvalPolicy: 'never', networkAccess: false, subagentsEnabled: true, maxSubagents: 1000,
  });
  assert.equal(config.agents.max_threads, 8);
  assert.equal(config.project_doc_max_bytes, 32768);
  assert.equal(config.model_auto_compact_token_limit, 13926);
});
