import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import nodeTest from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../chrome-extension/background.js", import.meta.url), "utf8");
const popupSource = readFileSync(new URL("../chrome-extension/popup.js", import.meta.url), "utf8");
const FIRST = "11111111-1111-4111-8111-111111111111";
const SECOND = "22222222-2222-4222-8222-222222222222";
const flush = () => new Promise(resolve => setImmediate(resolve));
const test = (name, run) => nodeTest(name, { timeout: 2000 }, run);

function worker(initialToken = "", setStorage = null) {
  const storage = { pairingToken: initialToken };
  const sockets = [];
  const badges = [];
  const timers = new Map();
  let timerId = 0, listener, alarmListener;
  class Socket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
    open() { this.readyState = 1; this.onopen?.(); }
    close() { this.readyState = 3; queueMicrotask(() => this.onclose?.()); }
    send(value) { this.sent.push(JSON.parse(value)); }
    receive(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
  }
  const chrome = {
    storage: { local: { get: async () => ({ ...storage }), set: async value => {
      if (setStorage) await setStorage(value);
      Object.assign(storage, value);
    } } },
    action: { setBadgeText: async ({ text }) => badges.push(text) },
    debugger: { onDetach: { addListener() {} } },
    alarms: { create() {}, onAlarm: { addListener(value) { alarmListener = value; } } },
    runtime: { onMessage: { addListener(value) { listener = value; } } },
    tabs: { query: async () => [{ id: 7, title: "Fixture tab", url: "https://example.test", active: true }] },
  };
  const context = vm.createContext({ chrome, WebSocket: Socket, URL, console,
    setTimeout(callback, ms) { const id = ++timerId; timers.set(id, { callback, ms }); return id; },
    clearTimeout(id) { timers.delete(id); }, setInterval() {} });
  vm.runInContext(source, context);
  const message = (value, replies = []) => new Promise(resolve => {
    listener(value, {}, result => { replies.push(result); resolve(result); });
  });
  function timer(ms) {
    const entry = [...timers.entries()].find(([, value]) => value.ms === ms);
    assert.ok(entry, `Missing ${ms}ms timer`);
    timers.delete(entry[0]);
    entry[1].callback();
  }
  return { sockets, badges, storage, message, timer, alarm() { alarmListener({ name: "opencore-reconnect" }); } };
}

test("pair reports success only after the actual handshake opens", async () => {
  const harness = worker();
  await flush();
  const replies = [];
  const paired = harness.message({ type: "pair", token: FIRST }, replies);
  await flush();
  assert.equal(harness.sockets.length, 1);
  assert.equal(replies.length, 0);
  assert.ok(!harness.badges.includes("ON"));
  harness.sockets[0].open();
  const result = await paired;
  assert.equal(result.ok, true);
  assert.equal(result.connected, true);
  assert.equal(harness.badges.at(-1), "ON");
});

test("rejected handshake is a failed pair, not a successful connection", async () => {
  const harness = worker();
  await flush();
  const paired = harness.message({ type: "pair", token: FIRST });
  await flush();
  harness.sockets[0].close();
  const result = await paired;
  assert.equal(result.ok, false);
  assert.match(result.error, /connect|pair/i);
  assert.ok(!harness.badges.includes("ON"));
});

test("an unresponsive handshake times out and releases the attempt", async () => {
  const harness = worker();
  await flush();
  const paired = harness.message({ type: "pair", token: FIRST });
  await flush();
  harness.timer(8000);
  const result = await paired;
  assert.equal(result.ok, false);
  assert.match(result.error, /time|connect/i);
  assert.equal(harness.sockets[0].readyState, 3);
});

test("re-pairing supersedes an old pending socket without clearing the new badge", async () => {
  const harness = worker();
  await flush();
  const first = harness.message({ type: "pair", token: FIRST });
  await flush();
  const old = harness.sockets[0];
  const second = harness.message({ type: "pair", token: SECOND });
  await flush();
  assert.equal(harness.sockets.length, 2);
  harness.sockets[1].open();
  assert.equal((await second).ok, true);
  assert.equal((await first).ok, false);
  old.onclose?.();
  assert.equal(harness.badges.at(-1), "ON");
  assert.equal(harness.storage.pairingToken, SECOND);
});

test("invalid input preserves the existing saved pairing and connection", async () => {
  const harness = worker(FIRST);
  await flush();
  harness.sockets[0].open();
  await flush();
  const result = await harness.message({ type: "pair", token: "not-a-pairing-code" });
  assert.equal(result.ok, false);
  assert.equal(harness.storage.pairingToken, FIRST);
  assert.equal(harness.sockets.length, 1);
  assert.equal(harness.sockets[0].readyState, 1);
});

test("delayed storage cannot make an older pair replace the latest request", async () => {
  const release = deferred(), writes = [];
  const harness = worker("", async value => {
    writes.push(value.pairingToken);
    if (value.pairingToken === FIRST) await release.promise;
  });
  await flush();
  const first = harness.message({ type: "pair", token: FIRST });
  await flush();
  const second = harness.message({ type: "pair", token: SECOND });
  await flush();
  assert.deepEqual(writes, [FIRST]);
  release.resolve();
  await flush();
  assert.deepEqual(writes, [FIRST, SECOND]);
  assert.equal(harness.sockets.length, 1);
  harness.sockets[0].open();
  assert.equal((await second).ok, true);
  assert.equal((await first).ok, false);
  assert.equal(harness.storage.pairingToken, SECOND);
});

test("reconnect reuses the saved identity and still handles browser commands", async () => {
  const harness = worker(FIRST);
  await flush();
  harness.sockets[0].open();
  await flush();
  harness.sockets[0].close();
  await flush();
  harness.timer(3000);
  await flush();
  assert.equal(new URL(harness.sockets[1].url).searchParams.get("token"), FIRST);
  harness.sockets[1].open();
  await flush();
  const status = await harness.message({ type: "connection_status" });
  assert.equal(status.connected, true);
  harness.sockets[1].receive({ id: "list-request", action: "list", args: {} });
  await flush();
  assert.equal(harness.sockets[1].sent.at(-1).ok, true);
  assert.equal(harness.sockets[1].sent.at(-1).result.tabs[0].tabId, 7);
});

test("a reconnect during a pending write cannot confirm the previous pairing code", async () => {
  const release = deferred();
  const harness = worker(FIRST, () => release.promise);
  await flush();
  harness.sockets[0].open();
  await flush();
  const paired = harness.message({ type: "pair", token: SECOND });
  await flush();
  harness.alarm();
  await flush();
  harness.sockets[1].open();
  await flush();
  release.resolve();
  await flush();
  assert.equal(harness.sockets.length, 3);
  assert.equal(new URL(harness.sockets[2].url).searchParams.get("token"), SECOND);
  harness.sockets[2].open();
  assert.equal((await paired).ok, true);
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function popup() {
  const statusReply = deferred(), pairReply = deferred();
  const input = { value: "" }, status = { textContent: "" };
  const button = { disabled: false, addEventListener(_event, callback) { this.click = callback; } };
  vm.runInNewContext(popupSource, {
    document: { getElementById(id) { return { token: input, status, connect: button }[id]; } },
    chrome: { storage: { local: { get: async () => ({ pairingToken: FIRST }) } },
      runtime: { sendMessage(message) { return message.type === "pair" ? pairReply.promise : statusReply.promise; } } },
  });
  return { input, status, button, statusReply, pairReply };
}

test("late popup status cannot overwrite a newer successful pairing", async () => {
  const view = popup();
  await flush();
  const clicking = view.button.click();
  assert.equal(view.button.disabled, true);
  view.pairReply.resolve({ ok: true, connected: true });
  await clicking;
  assert.equal(view.status.textContent, "Connected to OpenCore.");
  view.statusReply.resolve({ connected: false, error: "Old disconnected status" });
  await flush();
  assert.equal(view.status.textContent, "Connected to OpenCore.");
  assert.equal(view.button.disabled, false);
});

test("failed popup message releases the Connect button and shows the failure", async () => {
  const view = popup();
  view.statusReply.resolve({ connected: false });
  await flush();
  const clicking = view.button.click();
  assert.equal(view.button.disabled, true);
  view.pairReply.reject(new Error("Fixture worker disconnected"));
  await clicking;
  assert.equal(view.button.disabled, false);
  assert.match(view.status.textContent, /Could not connect/);
});
