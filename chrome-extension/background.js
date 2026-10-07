const PORT = 8814;
let socket = null;
let reconnectTimer = null;
let connectionPromise = null;
let connectionVersion = 0;
let rejectHandshake = null;
let connectionError = "";
let pairingRevision = 0;
let pairingWrites = Promise.resolve();
const attachedTabs = new Set();
const attachingTabs = new Map();
const controlWorlds = new Map();
let debuggerCleanup = Promise.resolve();

function requireSession(version) {
  if (version !== connectionVersion) throw new Error('Browser command cancelled because the connection was disconnected or replaced');
}

function isHttpUrl(value) {
  try { return ["http:", "https:"].includes(new URL(value).protocol) && value.length <= 2048; }
  catch { return false; }
}

async function activeTabId(args) {
  if (Number.isInteger(args.tabId) && args.tabId >= 0) return args.tabId;
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (tab?.id == null) throw new Error("No active Chrome tab");
  return tab.id;
}

async function attach(tabId, version) {
  await debuggerCleanup;
  requireSession(version);
  if (attachedTabs.has(tabId)) return;
  if (attachingTabs.has(tabId)) { await attachingTabs.get(tabId); requireSession(version); return; }
  const attempt = (async () => {
    await chrome.debugger.attach({ tabId }, "1.3");
    if (version !== connectionVersion) {
      await chrome.debugger.detach({ tabId }).catch(() => {});
      requireSession(version);
    }
    attachedTabs.add(tabId);
    await chrome.debugger.sendCommand({ tabId }, "Page.enable");
    requireSession(version);
    await chrome.debugger.sendCommand({ tabId }, "Runtime.enable");
    requireSession(version);
  })();
  attachingTabs.set(tabId, attempt);
  try { await attempt; }
  finally { if (attachingTabs.get(tabId) === attempt) attachingTabs.delete(tabId); }
}

chrome.debugger.onDetach.addListener((source) => {
  if (source.tabId != null) { attachedTabs.delete(source.tabId); controlWorlds.delete(source.tabId); }
});

function releaseDebuggerTabs() {
  const tabs = [...attachedTabs], pending = [...attachingTabs.values()];
  attachedTabs.clear();
  controlWorlds.clear();
  debuggerCleanup = debuggerCleanup.catch(() => {}).then(async () => {
    await Promise.allSettled(pending);
    await Promise.all(tabs.map(tabId => chrome.debugger.detach({ tabId }).catch(() => {})));
  });
}

async function sendCdp(tabId, method, params = {}, version = connectionVersion) {
  await attach(tabId, version);
  requireSession(version);
  return chrome.debugger.sendCommand({ tabId }, method, params);
}

async function pointerClick(tabId, x, y, version, beforePress) {
  await sendCdp(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y }, version);
  if (beforePress) await beforePress();
  await sendCdp(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 }, version);
  await sendCdp(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 }, version);
}

const CONTROL_SIGNATURE = `el => JSON.stringify([el.tagName, el.getAttribute('role'), el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('type'), el.getAttribute('name'), el.getAttribute('placeholder'), el.href, el.getAttribute('formaction'), el.form?.action, el.textContent?.slice(0, 300)])`;
const INSPECT_EXPRESSION = `(() => {
  const refs = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"]'))
    .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= innerHeight && r.right >= 0 && r.left <= innerWidth; })
    .slice(0, 100);
  const snapshotId = typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  const signature = ${CONTROL_SIGNATURE};
  globalThis.__opencoreControlSnapshot = { id: snapshotId, document, url: location.href, refs, signatures: refs.map(signature) };
  const controls = refs.map((el, index) => { const r = el.getBoundingClientRect(); return {
      index, elementId: index, tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
      name: (el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || el.getAttribute('placeholder') || '').trim().slice(0, 140),
      type: el.getAttribute('type') || '', href: el.tagName === 'A' ? el.href.slice(0, 300) : '',
      x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)
    }; });
  return { snapshotId, url: location.href, title: document.title, text: (document.body?.innerText || '').slice(0, 14000),
    controls, viewport: { width: innerWidth, height: innerHeight },
    capabilities: { elementReferences: 'top-document', canvasInput: 'CDP coordinates after screenshot', systemPointer: false } };
})()`;

function targetExpression(args, focus) {
  return `(() => {
    const state = globalThis.__opencoreControlSnapshot;
    if (!state || state.id !== ${JSON.stringify(args.snapshotId)} || state.document !== document || state.url !== location.href) throw new Error('Stale control reference; inspect again');
    const el = state.refs[${args.elementId}];
    const signature = ${CONTROL_SIGNATURE};
    const resolve = () => {
      if (!el?.isConnected || el.ownerDocument !== document) throw new Error('Stale control reference; inspect again');
      if (signature(el) !== state.signatures[${args.elementId}]) throw new Error('Control changed; inspect again');
      if (el.disabled || el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true' || el.closest('[inert]')) throw new Error('This control is disabled');
      const style = getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || style.pointerEvents === 'none') throw new Error('This control is not visible or interactive');
      const r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
      if (r.width <= 0 || r.height <= 0 || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) throw new Error('Control is outside the viewport; scroll and inspect again');
      const hit = document.elementFromPoint(x, y);
      if (!hit || (hit !== el && !el.contains(hit))) throw new Error('Control is covered; inspect again');
      return { x, y };
    };
    let at = resolve();
    if (${focus}) {
      if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el.isContentEditable) || el.readOnly || (el instanceof HTMLInputElement && ['button','submit','reset','checkbox','radio','file','hidden','range','color'].includes(el.type))) throw new Error('Choose a writable editable control from inspect');
      el.focus({ preventScroll: true });
      if (document.activeElement !== el) throw new Error('Could not focus the inspected control');
      at = resolve();
      if (state.url !== location.href) throw new Error('Page changed while focusing the control; inspect again');
    }
    return at;
  })()`;
}

async function handle(command) {
  const version = connectionVersion;
  const cdp = (tabId, method, params) => sendCdp(tabId, method, params, version);
  const { action, args = {} } = command;
  if (action === "list") {
    const tabs = await chrome.tabs.query({});
    return { tabs: tabs.filter(tab => tab.id != null).map(tab => ({ tabId: tab.id, title: tab.title || "", url: tab.url || "", active: !!tab.active })) };
  }
  if (action === "open") {
    if (!isHttpUrl(args.url)) throw new Error("Only HTTP and HTTPS URLs are supported");
    const tab = await chrome.tabs.create({ url: args.url, active: true });
    return { tabId: tab.id, url: tab.url || args.url };
  }
  const tabId = await activeTabId(args);
  requireSession(version);
  if (action === "activate") {
    const tab = await chrome.tabs.get(tabId);
    requireSession(version);
    await chrome.tabs.update(tabId, { active: true });
    requireSession(version);
    if (tab.windowId != null) await chrome.windows.update(tab.windowId, { focused: true });
    return { tabId, active: true };
  }
  if (action === "close") {
    await chrome.tabs.remove(tabId);
    attachedTabs.delete(tabId);
    controlWorlds.delete(tabId);
    return { tabId, closed: true };
  }
  if (action === "navigate") {
    if (!isHttpUrl(args.url)) throw new Error("Only HTTP and HTTPS URLs are supported");
    controlWorlds.delete(tabId);
    const tab = await chrome.tabs.update(tabId, { url: args.url, active: true });
    return { tabId, url: tab.url || args.url };
  }
  if (action === "inspect") {
    // Page script cannot replace these references: the snapshot lives in a
    // separate JavaScript world with the same DOM and its own global/prototypes.
    const tree = await cdp(tabId, 'Page.getFrameTree');
    const world = await cdp(tabId, 'Page.createIsolatedWorld', { frameId: tree.frameTree.frame.id, worldName: 'opencore-controls-v1' });
    const output = await cdp(tabId, "Runtime.evaluate", { expression: INSPECT_EXPRESSION, returnByValue: true, contextId: world.executionContextId });
    if (output.exceptionDetails) throw new Error("Could not inspect this page");
    requireSession(version);
    controlWorlds.set(tabId, { contextId: world.executionContextId, snapshotId: output.result.value.snapshotId });
    return { tabId, ...output.result.value };
  }
  if (action === "screenshot") {
    const [shot, viewport] = await Promise.all([
      cdp(tabId, "Page.captureScreenshot", { format: "png", captureBeyondViewport: false }),
      cdp(tabId, "Runtime.evaluate", { expression: "({width:innerWidth,height:innerHeight})", returnByValue: true }),
    ]);
    return { tabId, dataUrl: `data:image/png;base64,${shot.data}`, viewport: viewport.result.value };
  }
  if (action === 'click_element' || action === 'type_element') {
    if (!Number.isInteger(args.elementId) || args.elementId < 0 || args.elementId >= 100 || typeof args.snapshotId !== 'string' || !args.snapshotId.length || args.snapshotId.length > 64) throw new Error('Use elementId and snapshotId from inspect');
    if (action === 'type_element' && (typeof args.text !== 'string' || args.text.length > 4000)) throw new Error('Text is too long');
    const world = controlWorlds.get(tabId);
    if (!world || world.snapshotId !== args.snapshotId) throw new Error('Stale control reference; inspect again');
    const resolve = async focus => {
      const output = await cdp(tabId, 'Runtime.evaluate', { expression: targetExpression(args, focus), returnByValue: true, contextId: world.contextId });
      if (output.exceptionDetails) throw new Error(output.exceptionDetails.exception?.description || output.exceptionDetails.text || 'Control changed; inspect again');
      return output.result.value;
    };
    const at = await resolve(action === 'type_element');
    if (action === 'click_element') await pointerClick(tabId, at.x, at.y, version, async () => {
      const current = await resolve(false);
      if (current.x !== at.x || current.y !== at.y) throw new Error('Control moved after hover; inspect again');
    });
    else await cdp(tabId, 'Input.insertText', { text: args.text });
    return { tabId, elementId: args.elementId, inputMode: 'cdp', ...(action === 'click_element' ? { clicked: true } : { typed: args.text.length }) };
  }
  if (action === "click" || action === "type" || action === "scroll") {
    if (action === 'type' && (typeof args.text !== 'string' || args.text.length > 4000)) throw new Error('Text is too long');
    const x = Number(args.x), y = Number(args.y);
    if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 || x > 10000 || y > 10000) throw new Error("Invalid browser coordinates");
    if (action === "scroll") {
      const deltaY = Math.max(-2000, Math.min(2000, Number(args.deltaY) || 0));
      await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseWheel", x, y, deltaX: 0, deltaY });
      return { tabId, scrolled: deltaY };
    }
    await pointerClick(tabId, x, y, version);
    if (action === "type") {
      await cdp(tabId, "Input.insertText", { text: args.text });
    }
    return { tabId, x, y, ...(action === "type" ? { typed: args.text.length } : { clicked: true }) };
  }
  if (action === "key") {
    const code = { Enter: 13, Tab: 9, Escape: 27, Backspace: 8, ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39 }[args.key];
    if (!code) throw new Error("Unsupported key");
    await cdp(tabId, "Input.dispatchKeyEvent", { type: "rawKeyDown", key: args.key, windowsVirtualKeyCode: code });
    await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyUp", key: args.key, windowsVirtualKeyCode: code });
    return { tabId, key: args.key };
  }
  if (action === "reload") {
    controlWorlds.delete(tabId);
    await cdp(tabId, "Page.reload", { ignoreCache: false });
    return { tabId, reloaded: true };
  }
  if (action === "evaluate") {
    if (typeof args.expression !== "string" || !args.expression.length || args.expression.length > 16000) throw new Error("Invalid DevTools expression");
    const output = await cdp(tabId, "Runtime.evaluate", { expression: args.expression, returnByValue: true, awaitPromise: true, userGesture: true });
    if (output.exceptionDetails) throw new Error(output.exceptionDetails.exception?.description || output.exceptionDetails.text || "DevTools evaluation failed");
    return { tabId, type: output.result.type, value: output.result.value ?? null, description: output.result.description || "" };
  }
  if (action === "back" || action === "forward") {
    controlWorlds.delete(tabId);
    const history = await cdp(tabId, "Page.getNavigationHistory");
    const next = history.currentIndex + (action === "back" ? -1 : 1);
    if (!history.entries[next]) throw new Error("No page in that direction");
    await cdp(tabId, "Page.navigateToHistoryEntry", { entryId: history.entries[next].id });
    return { tabId, url: history.entries[next].url };
  }
  throw new Error("Unsupported browser action");
}

function resetConnection() {
  connectionVersion += 1;
  releaseDebuggerTabs();
  clearTimeout(reconnectTimer);
  const previous = socket;
  socket = null;
  rejectHandshake?.(new Error("Pairing attempt was replaced."));
  rejectHandshake = null;
  connectionPromise = null;
  connectionError = "";
  previous?.close();
  chrome.action.setBadgeText({ text: "" });
}

function connect() {
  if (socket?.readyState === WebSocket.OPEN) return Promise.resolve(true);
  if (connectionPromise) return connectionPromise;
  const version = connectionVersion;
  const attempt = (async () => {
    const { pairingToken } = await chrome.storage.local.get("pairingToken");
    if (version !== connectionVersion) throw new Error("Pairing attempt was replaced.");
    if (!pairingToken) return false;
    return new Promise((resolve, reject) => {
      const activeSocket = new WebSocket(`ws://127.0.0.1:${PORT}/ws?token=${encodeURIComponent(pairingToken)}`);
      socket = activeSocket;
      let settled = false;
      let timeout;
      const fail = error => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        if (rejectHandshake === fail) rejectHandshake = null;
        if (socket === activeSocket) connectionError = error.message;
        reject(error);
      };
      rejectHandshake = fail;
      timeout = setTimeout(() => {
        fail(new Error("Connection timed out. Open OpenCore and check the pairing code."));
        activeSocket.close();
      }, 8000);
      activeSocket.onopen = () => {
        if (socket !== activeSocket || version !== connectionVersion) {
          fail(new Error("Pairing attempt was replaced."));
          activeSocket.close();
          return;
        }
        settled = true;
        clearTimeout(timeout);
        if (rejectHandshake === fail) rejectHandshake = null;
        connectionError = "";
        chrome.action.setBadgeText({ text: "ON" });
        resolve(true);
      };
      activeSocket.onmessage = async event => {
        let command;
        try { command = JSON.parse(event.data); } catch { return; }
        if (!command?.id || socket !== activeSocket) return;
        try {
          const result = await handle(command);
          if (socket === activeSocket && activeSocket.readyState === WebSocket.OPEN) activeSocket.send(JSON.stringify({ id: command.id, ok: true, result }));
        } catch (error) {
          if (socket === activeSocket && activeSocket.readyState === WebSocket.OPEN) activeSocket.send(JSON.stringify({ id: command.id, ok: false, error: String(error?.message || error) }));
        }
      };
      activeSocket.onclose = () => {
        fail(new Error("Could not connect. Open OpenCore and check the pairing code."));
        if (socket !== activeSocket) return;
        connectionVersion += 1;
        releaseDebuggerTabs();
        chrome.action.setBadgeText({ text: "" });
        socket = null;
        if (!connectionError) connectionError = "OpenCore disconnected.";
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => connect().catch(() => {}), 3000);
      };
      activeSocket.onerror = () => {
        fail(new Error("Could not connect. Open OpenCore and check the pairing code."));
        activeSocket.close();
      };
    });
  })();
  connectionPromise = attempt;
  attempt.then(() => {
    if (connectionPromise === attempt) connectionPromise = null;
  }, error => {
    if (connectionPromise === attempt) connectionPromise = null;
    if (version === connectionVersion && !connectionError) connectionError = String(error?.message || error);
  });
  return attempt;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "connection_status") {
    sendResponse({ connected: socket?.readyState === WebSocket.OPEN,
      connecting: socket?.readyState === WebSocket.CONNECTING, error: connectionError });
    return false;
  }
  if (message?.type !== "pair") return;
  const token = String(message.token || "").trim();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(token)) {
    sendResponse({ ok: false, error: "Paste the pairing code shown in OpenCore." });
    return false;
  }
  const revision = ++pairingRevision;
  resetConnection();
  const saved = pairingWrites.then(async () => {
    if (revision !== pairingRevision) throw new Error("Pairing attempt was replaced.");
    await chrome.storage.local.set({ pairingToken: token });
  });
  pairingWrites = saved.catch(() => {});
  saved
    .then(() => {
      if (revision !== pairingRevision) throw new Error("Pairing attempt was replaced.");
      // An alarm may have reconnected with the previous stored code while
      // this write was pending. Authenticate again with the committed code.
      resetConnection();
      return connect();
    })
    .then(() => {
      if (revision !== pairingRevision) throw new Error("Pairing attempt was replaced.");
      const connected = socket?.readyState === WebSocket.OPEN;
      sendResponse(connected ? { ok: true, connected: true }
        : { ok: false, error: "OpenCore disconnected before pairing finished." });
    })
    .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
  return true;
});

chrome.alarms.create("opencore-reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "opencore-reconnect") connect().catch(() => {}); });
setInterval(() => { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping" })); }, 20000);
connect().catch(() => {});
