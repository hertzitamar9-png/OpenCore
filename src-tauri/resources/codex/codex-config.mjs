const integer = (value) => Number.isSafeInteger(value) && value > 0 ? value : null;

function loopbackHttpUrl(value, name) {
  let url;
  try { url = new URL(value); } catch { throw new TypeError(`${name} must be a valid URL`); }
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
    throw new TypeError(`${name} must use HTTP on the local loopback interface`);
  }
  return url.toString().replace(/\/$/, '');
}

export function agentPermissions(approvalMode) {
  switch (approvalMode) {
    case 'allow-all': return { sandboxMode: 'danger-full-access', approvalPolicy: 'never', networkAccess: true };
    case 'allow-chat': return { sandboxMode: 'workspace-write', approvalPolicy: 'never', networkAccess: false };
    // OpenCore's MCP permission RPC performs per-action prompts in these modes.
    // Keep Codex-native shell and filesystem access read-only.
    case 'approve-for-me':
    case 'ask-every-time':
    default: return { sandboxMode: 'read-only', approvalPolicy: 'never', networkAccess: false };
  }
}

export function buildCodexConfiguration(options) {
  const contextWindowTokens = integer(options.contextWindowTokens);
  const requestedCompactAt = integer(options.compactAtTokens);
  if (!contextWindowTokens || contextWindowTokens < 8_192) {
    throw new RangeError('Codex context window must be at least 8,192 tokens');
  }
  if (!requestedCompactAt || requestedCompactAt < 1_024) throw new RangeError('Compaction threshold must be at least 1,024 tokens');
  if (!['read-only', 'workspace-write', 'danger-full-access'].includes(options.sandboxMode)) {
    throw new TypeError('Unsupported Codex sandbox mode');
  }
  if (!['never', 'on-request', 'on-failure', 'untrusted'].includes(options.approvalPolicy)) {
    throw new TypeError('Unsupported Codex approval policy');
  }
  if (typeof options.bridgeToken !== 'string' || options.bridgeToken.length < 24) {
    throw new TypeError('The per-run MCP bridge token must contain at least 24 characters');
  }
  const gatewayUrl = loopbackHttpUrl(options.gatewayUrl, 'OpenCore gateway');
  const bridgeUrl = loopbackHttpUrl(options.bridgeUrl, 'MCP bridge');
  const compactAtTokens = Math.min(requestedCompactAt, contextWindowTokens - 2_048, Math.floor(contextWindowTokens * 0.85));
  const agentCount = options.subagentsEnabled
    ? Math.max(1, Math.min(8, Math.floor(Number(options.maxSubagents) || 1)))
    : 1;
  const headers = {
    'x-opencore-harness': 'codex-sdk',
    'x-echo-conversation': options.conversationId,
    'x-opencore-effort': options.effort || 'medium',
    'x-opencore-timeline-owner': 'app',
  };
  return {
    model_provider: 'opencore',
    model: options.model,
    model_context_window: contextWindowTokens,
    model_auto_compact_token_limit: compactAtTokens,
    developer_instructions: options.developerInstructions,
    model_providers: {
      opencore: {
        name: 'OpenCore local model',
        base_url: gatewayUrl,
        wire_api: 'responses',
        requires_openai_auth: false,
        supports_websockets: false,
        http_headers: headers,
      },
    },
    sandbox_mode: options.sandboxMode,
    sandbox_workspace_write: {
      writable_roots: [options.workspace],
      network_access: Boolean(options.networkAccess),
    },
    approval_policy: options.approvalPolicy,
    mcp_servers: {
      opencore: {
        command: options.nodeExecutable,
        args: [options.mcpServerScript],
        // The app owns the real per-call approval flow in its tool dispatcher.
        // Codex must trust only this private, token-authenticated loopback MCP
        // server so its non-interactive CLI does not reject calls before the
        // OpenCore approval prompt can run.
        default_tools_approval_mode: 'approve',
        env: {
          OPENCORE_MCP_BRIDGE: bridgeUrl,
          OPENCORE_MCP_TOKEN: options.bridgeToken,
          OPENCORE_MCP_TOOLS: options.toolDefinitionsFile,
        },
        startup_timeout_sec: 30,
        tool_timeout_sec: 600,
      },
    },
    project_doc_max_bytes: options.projectSkillsEnabled === false ? 0 : 32_768,
    features: { multi_agent: Boolean(options.subagentsEnabled) },
    agents: { max_threads: agentCount },
  };
}
