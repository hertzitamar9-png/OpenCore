// OpenCore embeds the open-source Codex agent SDK. JSONL on stdio is host IPC;
// model inference is sent only to the configured local OpenCore Responses URL.
import { Codex } from '@openai/codex-sdk';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createInterface } from 'node:readline';
import { agentPermissions, buildCodexConfiguration } from './codex-config.mjs';
import { projectCodexEvent, reasoningEffortForCodex } from './codex-events.mjs';
import { createOpenCoreToolDispatcher } from './tool-relay.mjs';
import { CodingVerification, codingBoundaryGuidance } from '../agent/coding-verification.mjs';

const write = value => process.stdout.write(JSON.stringify(value) + '\n');
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
const pending = new Map();
let sequence = 0, started = false, activeCodex, activeBridge, currentAbort, backgroundHandoff;
const pendingRpc = (kind, data) => new Promise((resolve, reject) => {
  const id = String(++sequence);
  pending.set(id, { resolve, reject });
  write({ kind, id, ...data });
});

input.on('line', line => {
  try {
    const value = JSON.parse(line);
    if (value.kind === 'start' && !started) {
      started = true;
      run(value).catch(error => write({ kind: 'fatal', error: String(error?.stack ?? error) }))
        .finally(async () => {
          for (const item of pending.values()) item.reject(new Error('Codex turn ended'));
          pending.clear();
          await closeBridge();
          input.close();
        });
    } else if (value.kind === 'reply') {
      const item = pending.get(String(value.id));
      pending.delete(String(value.id));
      item?.resolve(value.value);
    } else if (value.kind === 'cancel') {
      currentAbort?.abort();
      activeCodex?.close?.();
      void closeBridge();
      input.close();
    }
  } catch (error) { write({ kind: 'fatal', error: String(error?.message ?? error) }); }
});

function remoteIsLoopback(request) {
  return ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(request.socket.remoteAddress);
}

function bearerMatches(value, token) {
  if (typeof value !== 'string' || !value.startsWith('Bearer ')) return false;
  const actual = Buffer.from(value.slice(7));
  const expected = Buffer.from(token);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 16 * 1024 * 1024) throw new Error('OpenCore tool request exceeds 16 MiB');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function closeBridge() {
  if (!activeBridge) return;
  const server = activeBridge;
  activeBridge = undefined;
  server.closeAllConnections?.();
  await new Promise(resolve => server.close(() => resolve()));
}

async function startBridge(callTool) {
  const token = randomBytes(32).toString('hex');
  const server = createServer(async (request, response) => {
    if (request.method !== 'POST' || request.url !== '/tool' || !remoteIsLoopback(request) || !bearerMatches(request.headers.authorization, token)) {
      response.writeHead(403, { 'content-type': 'application/json' }).end('{"error":"Forbidden"}');
      return;
    }
    try {
      const body = await readJsonBody(request);
      if (typeof body.name !== 'string' || !body.arguments || typeof body.arguments !== 'object' || Array.isArray(body.arguments)) {
        response.writeHead(400, { 'content-type': 'application/json' }).end('{"error":"Invalid tool request"}');
        return;
      }
      const result = await callTool(body.name, body.arguments);
      const { handoff, ...wireResult } = result;
      response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(JSON.stringify(wireResult));
    } catch (error) {
      response.writeHead(500, { 'content-type': 'application/json', 'cache-control': 'no-store' })
        .end(JSON.stringify({ error: String(error?.message ?? error) }));
    }
  });
  activeBridge = server;
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Could not bind the OpenCore MCP bridge to loopback');
  return { url: 'http://127.0.0.1:' + address.port + '/tool', token };
}

function tomlString(value) {
  return '"' + String(value).replaceAll('\\', '\\\\').replaceAll('"', '\\"').replaceAll('\n', '\\n').replaceAll('\r', '\\r') + '"';
}

function writeCodexHomeConfig(codexHome, workspace, projectSkillsEnabled) {
  mkdirSync(codexHome, { recursive: true });
  const trust = projectSkillsEnabled ? 'trusted' : 'untrusted';
  const content = '[projects.' + tomlString(workspace) + ']\ntrust_level = ' + tomlString(trust)
    + '\n\n[features]\nmemories = false\n\n[analytics]\nenabled = false\n';
  writeFileSync(path.join(codexHome, 'config.toml'), content, { encoding: 'utf8', mode: 0o600 });
}

function codexInput(content, scratchDir) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return String(content ?? '');
  const items = [];
  for (const part of content) {
    if (part?.type === 'text' && typeof part.text === 'string') {
      items.push({ type: 'text', text: part.text });
      continue;
    }
    const source = part?.type === 'image' ? part.source : part;
    if (source?.type === 'base64' && typeof source.data === 'string' && typeof source.media_type === 'string') {
      const extensions = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp', 'image/bmp': 'bmp' };
      const extension = extensions[source.media_type];
      if (!extension) throw new Error('Unsupported Codex image input type: ' + source.media_type);
      const data = Buffer.from(source.data, 'base64');
      if (data.length > 32 * 1024 * 1024) throw new Error('Image inputs are limited to 32 MiB each');
      const imagePath = path.join(scratchDir, 'attachment-' + items.length + '.' + extension);
      writeFileSync(imagePath, data, { mode: 0o600 });
      items.push({ type: 'local_image', path: imagePath });
    }
  }
  return items.length === 1 && items[0].type === 'text' ? items[0].text : items;
}

function scrubEnvironment() {
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (/^(OPENAI_API_KEY|CODEX_API_KEY|OPENAI_BASE_URL|OPENAI_ORG_ID|OPENAI_PROJECT|CODEX_HOME|CODEX_CLI_PATH)$/i.test(key)) delete env[key];
  }
  return env;
}

function codingGuidance(tools) {
  const hasDev = tools.some(spec => spec.function?.name === 'dev');
  const verification = hasDev
    ? 'For code changes, use OpenCore dev with action "run" and verifyPaths for the changed files; rely on its source-hash-bound pass result.'
    : 'For code changes, run a narrow relevant test or compiler check in the sandbox and report its actual result.';
  return 'You are OpenCore, running the Codex agent through the official OpenAI Codex SDK and CLI with the selected local model. '
    + verification + ' Inspect current files before editing, check exit status, preserve tests and public contracts, and never report an unverified change as passed.';
}

async function run(config) {
  if (!config || typeof config.workspace !== 'string' || typeof config.gatewayUrl !== 'string') throw new Error('Invalid OpenCore Codex start request');
  const scratchDir = mkdtempSync(path.join(tmpdir(), 'opencore-codex-'));
  let turnCompleted = false, finalError = null;
  try {
    const toolDefinitions = Array.isArray(config.tools) ? config.tools : [];
    const definitionsFile = path.join(scratchDir, 'tools.json');
    writeFileSync(definitionsFile, JSON.stringify(toolDefinitions), { encoding: 'utf8', mode: 0o600 });
    mkdirSync(config.codexHome, { recursive: true });
    writeCodexHomeConfig(config.codexHome, config.workspace, Boolean(config.projectSkillsEnabled));
    const verification = new CodingVerification(config.workspace, { devAvailable: toolDefinitions.some(spec => spec.function?.name === 'dev') });
    const permissions = agentPermissions(config.approvalMode);
    const mcpServerScript = path.join(config.resourcesDir, 'mcp-server.mjs');
    const nodeExecutable = config.nodeExecutable;
    const bridge = await startBridge(createOpenCoreToolDispatcher({
      rpc: pendingRpc,
      emit: event => {
        if (event.kind === 'handoff') {
          backgroundHandoff = event;
          write(event);
          currentAbort?.abort();
        } else write(event);
      },
      onToolResult: (name, args, value) => verification.recordMcp(name, args, value),
    }));
    const profile = buildCodexConfiguration({
      gatewayUrl: config.gatewayUrl,
      conversationId: config.conversationId,
      model: config.model || 'opencore',
      contextWindowTokens: config.contextWindowTokens,
      compactAtTokens: config.compactAtTokens,
      developerInstructions: (config.instructions || '') + '\n' + codingGuidance(toolDefinitions) + '\n' + codingBoundaryGuidance
        + '\nPreserve OpenCore tool approvals. Use its MCP tools for browser, computer, ECHO, music, image, and 3D actions. A queued long-running studio task must be handed off so the chat model releases its compute.',
      workspace: config.workspace,
      nodeExecutable,
      mcpServerScript,
      toolDefinitionsFile: definitionsFile,
      bridgeUrl: bridge.url,
      bridgeToken: bridge.token,
      effort: config.effort,
      sandboxMode: permissions.sandboxMode,
      approvalPolicy: permissions.approvalPolicy,
      networkAccess: permissions.networkAccess,
      subagentsEnabled: Boolean(config.subagentsEnabled),
      maxSubagents: config.maxSubagents,
      projectSkillsEnabled: Boolean(config.projectSkillsEnabled),
    });
    const env = scrubEnvironment();
    env.CODEX_HOME = config.codexHome;
    const sdk = new Codex({ env, config: profile });
    activeCodex = sdk;
    const threadOptions = {
      model: config.model || 'opencore',
      workingDirectory: config.workspace,
      skipGitRepoCheck: true,
      sandboxMode: permissions.sandboxMode,
      approvalPolicy: permissions.approvalPolicy,
      networkAccessEnabled: permissions.networkAccess,
      webSearchEnabled: false,
      webSearchMode: 'disabled',
    };
    const effort = reasoningEffortForCodex(config.effort);
    if (effort) threadOptions.modelReasoningEffort = effort;
    const thread = config.resume ? sdk.resumeThread(config.resume, threadOptions) : sdk.startThread(threadOptions);
    let prompt = codexInput(config.content, scratchDir);
    const maxVerificationPasses = verification.devAvailable ? 1 : 0;
    for (let pass = 0; pass <= maxVerificationPasses; pass++) {
      const abort = new AbortController();
      currentAbort = abort;
      let completed = false;
      try {
        const turn = await thread.runStreamed(prompt, { signal: abort.signal });
        const priorText = new Map(), completedItems = new Set();
        for await (const event of turn.events) {
          projectCodexEvent(event, {
            emit: value => {
              if (value.kind === 'turn_started') write(value);
              else if (value.kind === 'turn_completed') { completed = true; write(value); }
              else {
                if (value.kind === 'changed_file') verification.recordCodexFileChange(value.path);
                if (value.kind === 'turn_failed') finalError = value.error;
                write(value);
              }
            },
            previousText: priorText,
            completedItems,
            contextWindowTokens: config.contextWindowTokens,
            compactAtTokens: config.compactAtTokens,
          });
          if (backgroundHandoff) break;
        }
      } catch (error) {
        if (!backgroundHandoff) finalError = String(error?.message ?? error);
      } finally { currentAbort = undefined; }
      if (backgroundHandoff) {
        write({ kind: 'turn_completed', handoff: true });
        turnCompleted = true;
        break;
      }
      if (!completed || finalError) break;
      const pendingFiles = verification.pending();
      if (!pendingFiles.length || pass === maxVerificationPasses) { turnCompleted = true; break; }
      prompt = 'Before finishing, verify the current code changes. Run the narrow relevant tests or compiler with OpenCore dev action "run" and verifyPaths: '
        + JSON.stringify(pendingFiles.slice(0, 16))
        + '. Inspect the explicit exit status and output. If a check fails, make a focused repair and run the check again. Do not say verification passed unless the recorded result passed for the final file hashes.';
    }
    if (finalError) throw new Error(finalError);
    if (!turnCompleted) throw new Error('Codex exited before completing its turn');
    const pendingFiles = verification.pending();
    if (pendingFiles.length) write({ kind: 'unverified', paths: pendingFiles.slice(0, 16) });
  } finally {
    currentAbort?.abort();
    activeCodex?.close?.();
    activeCodex = undefined;
    await closeBridge();
    rmSync(scratchDir, { recursive: true, force: true });
  }
}
