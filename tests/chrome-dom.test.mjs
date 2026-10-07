import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { JSDOM } from 'jsdom';

function fixture() {
  const page = new JSDOM('<button id="save">Save</button><input id="name" aria-label="Name">', { url: 'https://fixture.test', runScripts: 'outside-only' });
  const sent = [];
  for (const element of page.window.document.querySelectorAll('button,input')) {
    element.getBoundingClientRect = () => ({ left: 10, top: 20, right: 110, bottom: 60, width: 100, height: 40 });
  }
  let hit = page.window.document.querySelector('button');
  page.window.document.elementFromPoint = () => hit;
  const chrome = {
    storage: { local: { get: async () => ({}) } },
    action: { setBadgeText() {} },
    runtime: { onMessage: { addListener() {} } },
    alarms: { create() {}, onAlarm: { addListener() {} } },
    debugger: { onDetach: { addListener() {} }, attach: async () => {}, detach: async () => {}, sendCommand: async (_, method, params) => {
      sent.push({ method, params });
      if (method === 'Page.getFrameTree') return { frameTree: { frame: { id: 'frame-1' } } };
      if (method === 'Page.createIsolatedWorld') return { executionContextId: 17 };
      if (method === 'Runtime.evaluate') {
        try { return { result: { value: page.window.eval(params.expression) } }; }
        catch (error) { return { exceptionDetails: { text: error.message } }; }
      }
      return {};
    } },
    tabs: { query: async () => [{ id: 7 }] },
  };
  const context = vm.createContext({ chrome, URL, WebSocket: { OPEN: 1, CONNECTING: 0 }, console, setTimeout, clearTimeout, setInterval() {} });
  vm.runInContext(readFileSync(new URL('../chrome-extension/background.js', import.meta.url), 'utf8'), context);
  const command = (action, args = {}) => { context.command = { action, args: { tabId: 7, ...args } }; return vm.runInContext('handle(command)', context); };
  return { page, command, sent, context, chrome, setHit: element => { hit = element; } };
}

test('DOM references resolve current bounds in a Chrome isolated world', async () => {
  const { page, command, sent } = fixture();
  const inspected = await command('inspect');
  assert.equal(typeof inspected.snapshotId, 'string');
  assert.ok(inspected.snapshotId.length > 0);
  page.window.document.querySelector('button').getBoundingClientRect = () => ({ left: 30, top: 40, right: 130, bottom: 80, width: 100, height: 40 });
  const result = await command('click_element', { snapshotId: inspected.snapshotId, elementId: 0 });
  assert.equal(result.clicked, true);
  assert.equal(sent.find(call => call.method === 'Runtime.evaluate').params.contextId, 17);
  assert.deepEqual(JSON.parse(JSON.stringify(sent.find(call => call.params?.type === 'mousePressed').params)), { type: 'mousePressed', x: 80, y: 60, button: 'left', clickCount: 1 });
});

test('stale, replaced and covered controls are rejected before input', async () => {
  const { page, command, sent, setHit } = fixture();
  const old = await command('inspect');
  await command('inspect');
  await assert.rejects(command('click_element', { snapshotId: old.snapshotId, elementId: 0 }), /inspect|stale/i);
  const current = await command('inspect');
  page.window.document.querySelector('button').remove();
  await assert.rejects(command('click_element', { snapshotId: current.snapshotId, elementId: 0 }), /inspect|stale/i);
  const last = await command('inspect');
  setHit(page.window.document.body);
  await assert.rejects(command('type_element', { snapshotId: last.snapshotId, elementId: 0, text: 'hello' }), /covered|inspect/i);
  assert.ok(!sent.some(call => call.method.startsWith('Input.')));
});

test('type validates text before input and focuses only the inspected writable field', async () => {
  const { page, command, sent, setHit } = fixture();
  const input = page.window.document.querySelector('input');
  setHit(input);
  const inspected = await command('inspect');
  await assert.rejects(command('type_element', { snapshotId: inspected.snapshotId, elementId: 1, text: 'x'.repeat(4001) }), /long/i);
  assert.ok(!sent.some(call => call.method.startsWith('Input.')));
  const result = await command('type_element', { snapshotId: inspected.snapshotId, elementId: 1, text: 'hello' });
  assert.equal(page.window.document.activeElement, input);
  assert.equal(result.typed, 5);
  assert.equal(sent.find(call => call.method === 'Input.insertText').params.text, 'hello');
});

test('changed labels, disabled fields and invisible controls require fresh inspection', async () => {
  const { page, command, sent, setHit } = fixture();
  const button = page.window.document.querySelector('button');
  const inspected = await command('inspect');
  button.textContent = 'Delete';
  await assert.rejects(command('click_element', { snapshotId: inspected.snapshotId, elementId: 0 }), /changed|inspect/i);
  const next = await command('inspect');
  button.disabled = true;
  await assert.rejects(command('click_element', { snapshotId: next.snapshotId, elementId: 0 }), /disabled/i);
  const input = page.window.document.querySelector('input');
  setHit(input);
  input.readOnly = true;
  await assert.rejects(command('type_element', { snapshotId: next.snapshotId, elementId: 1, text: 'no' }), /editable|read.only/i);
  input.readOnly = false;
  input.style.visibility = 'hidden';
  await assert.rejects(command('type_element', { snapshotId: next.snapshotId, elementId: 1, text: 'no' }), /visible|hidden/i);
  assert.ok(!sent.some(call => call.method.startsWith('Input.')));
});

test('HTTP pages without randomUUID still get valid DOM references', async () => {
  const { page, command } = fixture();
  Object.defineProperty(page.window.crypto, 'randomUUID', { value: undefined });
  const inspected = await command('inspect');
  assert.equal(typeof inspected.snapshotId, 'string');
  assert.equal((await command('click_element', { snapshotId: inspected.snapshotId, elementId: 0 })).clicked, true);
});

test('coordinate typing rejects invalid text before clicking a page control', async () => {
  const { command, sent } = fixture();
  await assert.rejects(command('type', { x: 20, y: 30, text: 'x'.repeat(4001) }), /long/i);
  assert.ok(!sent.some(call => call.method.startsWith('Input.')));
});

test('a disconnected command cannot continue sending pointer events after reconnect', async () => {
  const { command, sent, context } = fixture();
  const pending = command('click', { x: 20, y: 30 });
  vm.runInContext('resetConnection()', context);
  await assert.rejects(pending, /disconnected|replaced|cancelled/i);
  assert.ok(!sent.some(call => call.method.startsWith('Input.')));
});

test('a disconnected activation cannot focus Chrome after its pending tab lookup', async () => {
  const { command, context, chrome } = fixture();
  let lookup;
  let activated = 0;
  chrome.tabs.get = () => new Promise(resolve => { lookup = resolve; });
  chrome.tabs.update = async () => { activated += 1; return {}; };
  chrome.windows = { update: async () => { activated += 1; } };
  const pending = command('activate');
  await Promise.resolve();
  vm.runInContext('resetConnection()', context);
  lookup({ windowId: 2 });
  await assert.rejects(pending, /disconnected|replaced|cancelled/i);
  assert.equal(activated, 0);
});

test('hover-triggered overlays cannot redirect a precise element click', async () => {
  const { page, command, sent, chrome, setHit } = fixture();
  const original = chrome.debugger.sendCommand;
  chrome.debugger.sendCommand = async (...args) => {
    const result = await original(...args);
    if (args[2]?.type === 'mouseMoved') setHit(page.window.document.body);
    return result;
  };
  const inspected = await command('inspect');
  await assert.rejects(command('click_element', { snapshotId: inspected.snapshotId, elementId: 0 }), /covered|changed|inspect/i);
  assert.ok(!sent.some(call => call.params?.type === 'mousePressed'));
});

test('focus-triggered overlays and same-document navigation invalidate precise typing', async () => {
  const { page, command, sent, setHit } = fixture();
  const input = page.window.document.querySelector('input');
  setHit(input);
  const inspected = await command('inspect');
  input.addEventListener('focus', () => setHit(page.window.document.body), { once: true });
  await assert.rejects(command('type_element', { snapshotId: inspected.snapshotId, elementId: 1, text: 'no' }), /covered|changed|inspect/i);
  setHit(input);
  page.window.history.pushState({}, '', '/other-page');
  await assert.rejects(command('type_element', { snapshotId: inspected.snapshotId, elementId: 1, text: 'no' }), /stale|changed|inspect/i);
  assert.ok(!sent.some(call => call.method === 'Input.insertText'));
});
