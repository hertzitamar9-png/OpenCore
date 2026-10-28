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
  const timers = new Map(); let timerId = 0;
  const root = {childElementCount: rootChildren, workspace: false, querySelector() {return this.workspace ? {} : null;}};
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
    setTimeout(callback, delay) {const id=++timerId; timers.set(id,{callback,delay}); return id;},
    clearTimeout(id) {timers.delete(id);},
  };
  scope.window = scope; scope.globalThis = scope;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../assets/mobile-shim.js'), 'utf8'), scope);
  events.get('message')({data: 'opencore-mobile-port:trusted-page', ports: [port]});
  return {scope, port, outgoing, root, timers, mutate: () => mutation?.()};
}

test('native loading stays visible until the real app root renders', () => {
  const client = phone(0);
  assert.equal(client.outgoing.some(item => item.kind === 'phone-ready'), false);
  client.root.childElementCount = 1; client.mutate();
  assert.equal(client.outgoing.some(item => item.kind === 'phone-ready'), false, 'An account loading card is not a ready workspace');
  client.root.workspace = true; client.mutate();
  assert.equal(client.outgoing.filter(item => item.kind === 'phone-ready').length, 1);
  client.mutate();
  assert.equal(client.outgoing.filter(item => item.kind === 'phone-ready').length, 1);
});

test('a stalled startup command times out and offers native connection recovery', async () => {
  const client=phone();
  const pending=client.scope.__TAURI_INTERNALS__.invoke('account_action',{action:'startup_status',args:{}});
  await Promise.resolve();
  const timeout=[...client.timers.values()].find(item=>item.delay<=20000);
  assert.ok(timeout,'Startup requests need a bounded deadline');
  timeout.callback();
  await assert.rejects(pending,/PC.*respond|timed out/i);
  assert.ok(client.outgoing.some(item=>item.kind==='phone-startup-error'));
});

test('successful replies clear deadlines and streaming responses have no whole-generation deadline', async () => {
  const client=phone();
  const pending=client.scope.__TAURI_INTERNALS__.invoke('get_snapshot');
  await Promise.resolve();
  const request=client.outgoing.find(item=>item.command==='get_snapshot');
  client.port.onmessage({data:JSON.stringify({type:'result',id:request.id,ok:true,value:{}})});
  await pending; assert.equal(client.timers.size,0);
  const stream=client.scope.fetch('http://127.0.0.1:1/api/private/send',{method:'POST',body:'{}'});
  await Promise.resolve();
  const job=client.outgoing.find(item=>item.path==='/api/private/send');
  client.port.onmessage({data:JSON.stringify({type:'stream-start',id:job.id,status:200,contentType:'text/event-stream'})});
  assert.equal(client.timers.size,0);
  await stream;
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
