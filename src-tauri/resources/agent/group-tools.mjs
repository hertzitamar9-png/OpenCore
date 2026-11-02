// A group task grants only access to its local, conversation-scoped state relay.
// It grants no native PC tools and never reads a Hub or mobile access credential.
import fs from 'node:fs';
import path from 'node:path';
import { request } from 'node:http';
import { randomUUID } from 'node:crypto';

const ID = /^[A-Za-z0-9_-]{1,100}$/;
const ENDPOINT = /^http:\/\/127\.0\.0\.1:([1-9][0-9]{0,4})\/api\/groups\/agent\/tool$/;
const MAX_CONTEXT_BYTES = 16 * 1024;
const MAX_BODY_BYTES = 64 * 1024;
const MAX_RESULT_BYTES = 1024 * 1024;
const ACTIONS = {group_plan: 'plan', group_message: 'message', group_delegate: 'delegate', group_context: 'context'};
const statuses = ['pending', 'in_progress', 'completed'];
const text = {type: 'string', minLength: 1, maxLength: 16000};
const parameters = (properties, required = []) => ({type: 'object', properties, required, additionalProperties: false});
const definition = (name, description, input) => ({type: 'function', function: {name, description, parameters: input}});

const definitions = [
  definition('group_plan', 'Publish progress for this already-approved group task. Keep steps current; completed means verified completed work.',
    parameters({steps: {type: 'array', maxItems: 30, items: parameters({
      id: {type: 'string', minLength: 1, maxLength: 100}, text: {...text, maxLength: 300},
      status: {type: 'string', enum: statuses},
    }, ['text', 'status'])}}, ['steps'])),
  definition('group_message', 'Send a progress message to this already-approved group task. A message is not proof that another PC completed work.', parameters({text}, ['text'])),
  definition('group_delegate', 'Request a task from another PC worker that has locally opted in. Report the returned queued/running/completed state truthfully.',
    parameters({agentId: {type: 'string', minLength: 1, maxLength: 100}, text}, ['agentId', 'text'])),
  definition('group_context', 'Read this approved group task, current shared plan, messages, and available opted-in workers.', parameters({})),
];

class GroupToolError extends Error {}

function readContext(file, appData, conversationId, now) {
  for (const current of [appData, path.dirname(path.dirname(file)), path.dirname(file), file]) {
    const info = fs.lstatSync(current);
    if (info.isSymbolicLink() || (current === file ? !info.isFile() : !info.isDirectory())) throw new GroupToolError('Invalid group context file');
  }
  const descriptor = fs.openSync(file, 'r');
  let raw;
  try {
    const info = fs.fstatSync(descriptor);
    if (!info.isFile() || info.size > MAX_CONTEXT_BYTES) throw new GroupToolError('Invalid group context file');
    const buffer = Buffer.alloc(MAX_CONTEXT_BYTES + 1);
    const size = fs.readSync(descriptor, buffer, 0, buffer.length, 0);
    if (size > MAX_CONTEXT_BYTES) throw new GroupToolError('Invalid group context file');
    raw = buffer.subarray(0, size).toString('utf8');
  } finally { fs.closeSync(descriptor); }
  const value = JSON.parse(raw);
  const endpoint = typeof value?.url === 'string' && ENDPOINT.exec(value.url);
  if (value?.schema !== 1 || value.conversationId !== conversationId || !endpoint || Number(endpoint[1]) > 65535
      || typeof value.token !== 'string' || !/^[\x21-\x7e]{32,4096}$/.test(value.token)
      || !Number.isSafeInteger(value.expiresAt) || value.expiresAt <= Math.floor(now()/1000)) {
    throw new GroupToolError('Invalid or expired group context');
  }
  return {conversationId, url: value.url, token: value.token, expiresAt: value.expiresAt};
}

function toolName(name) {
  const plain = typeof name === 'string' ? name.replace(/^mcp__opencore__/, '') : '';
  return Object.hasOwn(ACTIONS, plain) ? plain : null;
}

function object(value, allowed, required = []) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !allowed.includes(key))
      || required.some(key => !Object.hasOwn(value, key))) throw new GroupToolError('Invalid group tool arguments');
}

function string(value, maximum) {
  if (typeof value !== 'string' || !value.trim() || value.length > maximum) throw new GroupToolError('Invalid group tool text or identifier');
  return value;
}

function argumentsFor(name, args) {
  if (name === 'group_context') { object(args, []); return {}; }
  if (name === 'group_plan') {
    object(args, ['steps'], ['steps']);
    if (!Array.isArray(args.steps) || args.steps.length > 30) throw new GroupToolError('Invalid group plan steps');
    return {steps: args.steps.map(step => {
      object(step, ['id', 'text', 'status'], ['text', 'status']);
      if (!statuses.includes(step.status)) throw new GroupToolError('Invalid group plan status');
      return {...(Object.hasOwn(step, 'id') ? {id: string(step.id, 100)} : {}), text: string(step.text, 300), status: step.status};
    })};
  }
  object(args, name === 'group_delegate' ? ['agentId', 'text'] : ['text'], name === 'group_delegate' ? ['agentId', 'text'] : ['text']);
  return {...(name === 'group_delegate' ? {agentId: string(args.agentId, 100)} : {}), text: string(args.text, 16000)};
}

function post(context, body, signal) {
  return new Promise((resolve, reject) => {
    const content = JSON.stringify(body);
    if (Buffer.byteLength(content) > MAX_BODY_BYTES) return reject(new GroupToolError('The group tool request is too large'));
    // node:http never follows redirects. The URL was checked before receiving
    // this task bearer; neither the model nor a response can replace it.
    const pending = request(context.url, {method: 'POST', signal: signal
      ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
    headers: {'content-type': 'application/json', 'content-length': Buffer.byteLength(content), authorization: 'Bearer '+context.token}}, response => {
      if (response.statusCode < 200 || response.statusCode >= 300) {
        response.resume();
        return reject(new GroupToolError('The group service did not accept this action (HTTP '+response.statusCode+')'));
      }
      const parts = []; let size = 0;
      response.on('data', part => {
        size += part.length;
        if (size > MAX_RESULT_BYTES) { response.destroy(); reject(new GroupToolError('The group service result is too large')); }
        else parts.push(part);
      });
      response.on('end', () => {
        try {
          const value = JSON.parse(Buffer.concat(parts).toString('utf8'));
          if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
          // Do not publish the task capability even if a broken relay echoes it.
          resolve(JSON.parse(JSON.stringify(value).replaceAll(JSON.stringify(context.token).slice(1, -1), '[scoped credential]')));
        } catch { reject(new GroupToolError('The group service returned an invalid result')); }
      });
      response.on('error', () => reject(new GroupToolError('The group service request ended before its result')));
    });
    pending.on('error', () => reject(new GroupToolError('The group service is unavailable or the request was cancelled')));
    pending.end(content);
  });
}

export function loadGroupTools(conversationId, {appData = process.env.APPDATA, emit = () => {}, warn = () => {}, now = Date.now} = {}) {
  if (!appData || !conversationId) return null;
  if (typeof conversationId !== 'string' || !ID.test(conversationId) || !path.isAbsolute(appData)) { warn('Group tools disabled: invalid conversation context.'); return null; }
  const file = path.join(appData, 'ai.opencore.control-center', 'group-agent-contexts', conversationId+'.json');
  let context;
  try { context = readContext(file, appData, conversationId, now); }
  catch (error) {
    if (error.code !== 'ENOENT') warn('Group tools disabled: invalid or expired conversation context.');
    return null;
  }
  const available = () => {
    try {
      const current = readContext(file, appData, conversationId, now);
      return current.url === context.url && current.token === context.token && current.expiresAt === context.expiresAt;
    } catch { return false; }
  };
  return {
    definitions, hasTool: name => toolName(name) !== null, available,
    guidance: 'This conversation has an approved group task. Use group_context to inspect it, group_plan for shared progress, group_message for updates, and group_delegate for another locally opted-in PC worker. A queued delegation is not a completed task.',
    async callTool(name, args = {}, {signal} = {}) {
      const canonical = toolName(name);
      if (!canonical) throw new GroupToolError('Unknown scoped group tool');
      let safeArgs = {}, failure;
      try {
        safeArgs = argumentsFor(canonical, args);
        if (JSON.stringify(safeArgs).includes(JSON.stringify(context.token).slice(1, -1))) { safeArgs = {}; throw new GroupToolError('Scoped group credentials cannot be included in group content'); }
      } catch (error) { safeArgs = {}; failure = error; }
      const id = randomUUID();
      emit({kind: 'tool_call', id, name: canonical, args: safeArgs});
      let value;
      try {
        if (failure) throw failure;
        if (!available()) throw new GroupToolError('The approved group task expired or was revoked');
        value = await post(context, {...safeArgs, action: ACTIONS[canonical]}, signal);
      } catch (error) { value = {error: error instanceof GroupToolError ? error.message : 'The group action could not be completed'}; }
      emit({kind: 'tool_result', id, name: canonical, args: safeArgs, value});
      return {content: [{type: 'text', text: JSON.stringify(value)}], structuredContent: value, isError: Boolean(value.error) || value.ok === false};
    },
  };
}
