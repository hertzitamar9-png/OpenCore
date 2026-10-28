'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

function phone(rootChildren = 1) {
  class Storage {
    constructor() { this.values = new Map(); }
    get length() { return this.values.size; }
    key(index) { return [...this.values.keys()][index] ?? null; }
    getItem(key) { return this.values.get(key) ?? null; }
    setItem(key, value) { this.values.set(String(key), String(value)); }
    removeItem(key) { this.values.delete(key); }
    clear() { this.values.clear(); }
  }
  const events = new Map(), outgoing = [];
  const root = {childElementCount: rootChildren};
  let mutation;
  const port = {postMessage: raw => outgoing.push(JSON.parse(raw)), start() {}};
  const scope = {
    localStorage: new Storage(), location: {href: 'https://appassets.opencore.local/index.html'},
    document: {
      readyState: 'complete', documentElement: {},
      querySelector: selector => selector.includes('nonce') ? {content: 'trusted-page'} : null,
      getElementById: id => id === 'root' ? root : null,
      addEventListener(name, callback) { events.set(name, callback); },
    },
    addEventListener(name, callback) { events.set(name, callback); }, dispatchEvent() {},
    MutationObserver: class {constructor(callback) {mutation = callback;} observe() {} disconnect() {}},
    fetch: async () => new Response('{}'), Headers, Response, ReadableStream, URL, TextEncoder,
    StorageEvent: class {}, Map, Promise, Error, Object,
  };
  scope.window = scope; scope.globalThis = scope;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../assets/mobile-shim.js'), 'utf8'), scope);
  events.get('message')({data: 'opencore-mobile-port:trusted-page', ports: [port]});
  return {scope, port, outgoing, root, mutate: () => mutation?.()};
}

test('native loading stays visible until the real app root renders', () => {
  const client = phone(0);
  assert.equal(client.outgoing.some(item => item.kind === 'phone-ready'), false);
  client.root.childElementCount = 1; client.mutate();
  assert.equal(client.outgoing.filter(item => item.kind === 'phone-ready').length, 1);
  client.mutate();
  assert.equal(client.outgoing.filter(item => item.kind === 'phone-ready').length, 1);
});

test('a lost PC connection rejects pending and new commands instead of leaving them hanging', async () => {
  const client = phone();
  const pending = client.scope.__TAURI_INTERNALS__.invoke('get_snapshot');
  await Promise.resolve();
  client.port.onmessage({data: JSON.stringify({type: 'connection-lost', error: 'PC disconnected. Reconnecting…'})});
  const result = await Promise.race([pending.then(() => 'resolved', error => error.message), new Promise(resolve => setTimeout(() => resolve('still waiting'), 30))]);
  assert.equal(result, 'PC disconnected. Reconnecting…');
  await assert.rejects(client.scope.__TAURI_INTERNALS__.invoke('get_snapshot'), /PC disconnected/);
});
