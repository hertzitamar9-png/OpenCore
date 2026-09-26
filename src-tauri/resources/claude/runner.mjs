// Official Claude Agent SDK owns the agent loop, tools and resumable sessions.
// JSON lines on stdin/stdout are private IPC with OpenCore, never model input.
import { query, tool, createSdkMcpServer } from '@anthropic-ai/claude-agent-sdk';
import { z } from 'zod';
import { createInterface } from 'node:readline';
import { mkdirSync } from 'node:fs';
import { contextBudgetEnvironment, deriveContextBudget } from './context-budget.mjs';
const write = value => process.stdout.write(JSON.stringify(value) + '\n');
const input = createInterface({ input: process.stdin });
const pending = new Map();
let sequence = 0, agent, started = false;
const abortController = new AbortController();
const rpc = (kind, data) => new Promise((resolve, reject) => {
  const id = String(++sequence); pending.set(id, { resolve, reject }); write({ kind, id, ...data });
});
input.on('close', () => { abortController.abort(); agent?.close(); for (const p of pending.values()) p.reject(new Error('OpenCore disconnected')); });
input.on('line', line => {
  try {
    const value = JSON.parse(line);
    if (value.kind === 'start' && !started) { started = true; run(value).catch(error => { write({ kind: 'fatal', error: String(error) }); process.exitCode = 1; }).finally(() => { input.close(); }); }
    else if (value.kind === 'reply') { const p = pending.get(value.id); pending.delete(value.id); p?.resolve(value.value); }
    else if (value.kind === 'cancel') { abortController.abort(); agent?.close(); input.close(); }
  } catch (error) { write({ kind: 'fatal', error: String(error) }); }
});

async function run(config) {
  mkdirSync(config.cwd, { recursive: true });
  const contextBudget = deriveContextBudget(config);
  const maxSubagents = config.subagentsEnabled ? Math.max(1, Math.min(1000, Number(config.maxSubagents) || 3)) : 0;
  let spawnedSubagents = 0;
  mkdirSync(config.configDir, { recursive: true });
  const mcpTools = (config.tools ?? []).map(spec => {
    const f = spec.function;
    const shape = z.fromJSONSchema(f.parameters).shape;
    return tool(f.name, f.description, shape, async args => {
      const value = await rpc('tool', { name: f.name, args });
      const { dataUrl, ...record } = value;
      const content = [{ type: 'text', text: JSON.stringify(record) }];
      if (dataUrl?.startsWith('data:image/')) {
        const match = /^data:([^;]+);base64,(.*)$/s.exec(dataUrl);
        if (match) content.push({ type: 'image', mimeType: match[1], data: match[2] });
      }
      return { content, isError: Boolean(value.error) };
    });
  });
  // Isolate credentials/config from the user's independent Claude installation.
  const env = { ...process.env };
  for (const key of Object.keys(env)) if (/^(ANTHROPIC_|CLAUDE_CODE_USE_|CLAUDE_CODE_OAUTH_TOKEN|CLAUDE_CONFIG_DIR)/.test(key)) delete env[key];
  Object.assign(env, {
    ANTHROPIC_BASE_URL: config.gateway ?? 'http://127.0.0.1:8812', ANTHROPIC_AUTH_TOKEN: 'opencore-local',
    ANTHROPIC_MODEL: `opencore:${config.effort}`, ANTHROPIC_DEFAULT_HAIKU_MODEL: `opencore:${config.effort}`,
    ANTHROPIC_DEFAULT_SONNET_MODEL: `opencore:${config.effort}`, ANTHROPIC_DEFAULT_OPUS_MODEL: `opencore:${config.effort}`,
    ANTHROPIC_CUSTOM_HEADERS: `x-opencore-harness: claude-agent-sdk\nx-echo-conversation: ${config.conversationId}`,
    CLAUDE_CONFIG_DIR: config.configDir, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1',
    CLAUDE_AGENT_SDK_CLIENT_APP: 'opencore/0.1.0',
    ENABLE_TOOL_SEARCH: 'false',
    // Keep headroom for the local model's variable token estimates, SDK prompt,
    // and tool replies. A near-limit trigger can make the SDK compact repeatedly.
    ...contextBudgetEnvironment(contextBudget),
    CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS: String(maxSubagents || 1),
    CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS: String(contextBudget.toolOutputTokens),
    MAX_MCP_OUTPUT_TOKENS: String(contextBudget.toolOutputTokens),
    BASH_MAX_OUTPUT_LENGTH: String(contextBudget.bashOutputLength),
    CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY: String(contextBudget.maxConcurrentToolUses),
    CLAUDE_CODE_ATTRIBUTION_HEADER: '0',
  });
  const options = {
    cwd: config.cwd, env, abortController, model: `opencore:${config.effort}`, resume: config.resume || undefined,
    settingSources: config.projectSkillsEnabled ? ['project'] : [], settings: { autoCompactEnabled: true },
    systemPrompt: { type: 'preset', preset: 'claude_code', append: `${config.instructions}\nFor large files or outputs, use offsets and chunks sized to the available context. Continue reading further chunks when needed; do not skip project content just to stay within one tool result.` },
    // Keep the official Claude Code tool surface as the default. OpenCore MCP
    // tools are added below; subagent policy and approval still pass through
    // OpenCore's existing limit and permission bridge.
    tools: { type: 'preset', preset: 'claude_code' },
    disallowedTools: maxSubagents ? [] : ['Agent'],
    maxTurns: 128,
    agents: maxSubagents ? {
      'opencore-explorer': {
        description: 'Read-only specialist for locating implementations, tracing behavior, finding tests, and reducing uncertainty before edits.',
        prompt: 'Explore only. Read and search exact source, tests, configs, and history relevant to the delegated question. Do not edit files. Return concise evidence with file paths, symbols, likely root cause, and the smallest promising change.',
        tools: ['Read', 'Glob', 'Grep', 'Bash'], model: 'inherit', maxTurns: 24,
      },
      'opencore-debugger': {
        description: 'Focused implementation/debugging specialist for an isolated failing behavior or test.',
        prompt: 'Own only the delegated bug or implementation slice. Reproduce or inspect the failure, apply a minimal root-cause fix, run the narrow relevant check, and report exact files changed and observed results. Avoid unrelated refactors.',
        tools: ['Read', 'Edit', 'Write', 'Bash', 'Glob', 'Grep'], model: 'inherit', maxTurns: 40,
      },
      'opencore-reviewer': {
        description: 'Independent reviewer for correctness, regressions, edge cases, and missing verification after an implementation.',
        prompt: 'Review independently. Read the changed code and nearby contracts/tests. Look for concrete correctness bugs, regressions, security or reliability issues, and missing test coverage. Do not edit files. Return findings ordered by severity and name the verification that would resolve each one.',
        tools: ['Read', 'Glob', 'Grep', 'Bash'], model: 'inherit', maxTurns: 24,
      },
    } : undefined,
    includePartialMessages: true, enableFileCheckpointing: true,
    // OpenCore's PreToolUse hook is the approval authority for every action.
    // Let the SDK proceed after that gate instead of adding a second approval layer.
    permissionMode: 'bypassPermissions', strictMcpConfig: true,
    mcpServers: mcpTools.length ? { opencore: createSdkMcpServer({ name: 'opencore', version: '1.0.0', tools: mcpTools }) } : {},
    hooks: { PreToolUse: [{ hooks: [async data => {
      const allow = await rpc('permission', { name: data.tool_name, args: data.tool_input });
      if (!allow) return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'deny', permissionDecisionReason: 'Denied by OpenCore' } };
      if (data.tool_name === 'Agent' || data.tool_name === 'Task') {
        if (!maxSubagents || spawnedSubagents >= maxSubagents) return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'deny', permissionDecisionReason: 'OpenCore subagent spawn limit reached' } };
        spawnedSubagents += 1;
      }
      return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'allow', permissionDecisionReason: 'Approved by OpenCore permission policy' } };
    }] }] },
    canUseTool: async (name, args) => (await rpc('permission', { name, args }))
      ? { behavior: 'allow', updatedInput: args } : { behavior: 'deny', message: 'Denied by OpenCore' },
    stderr: text => write({ kind: 'diagnostic', text }),
  };
  async function* prompt() { yield { type: 'user', message: { role: 'user', content: config.content }, parent_tool_use_id: null, session_id: config.resume ?? '' }; }
  agent = query({ prompt: prompt(), options });
  const reportContext = async () => {
    try {
      const usage = await agent.getContextUsage({ detail: 'summary' });
      write({kind:'context',usage:{totalTokens:usage.totalTokens,maxTokens:usage.maxTokens,
        rawMaxTokens:usage.rawMaxTokens,autoCompactThreshold:usage.autoCompactThreshold,
        isAutoCompactEnabled:usage.isAutoCompactEnabled}});
    } catch (error) { write({kind:'diagnostic',text:`Context telemetry: ${error}`}); }
  };
  try { for await (const message of agent) {
    write({ kind: 'sdk', message });
    if ((message.type === 'system' && (message.subtype === 'init' || message.subtype === 'compact_boundary'))
      || message.type === 'assistant' || message.type === 'user') await reportContext();
  } }
  finally { agent.close(); }
}
