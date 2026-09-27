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

async function attach(tabId) {
  if (attachedTabs.has(tabId)) return;
  await chrome.debugger.attach({ tabId }, "1.3");
  attachedTabs.add(tabId);
  await chrome.debugger.sendCommand({ tabId }, "Page.enable");
  await chrome.debugger.sendCommand({ tabId }, "Runtime.enable");
}

chrome.debugger.onDetach.addListener((source) => { if (source.tabId != null) attachedTabs.delete(source.tabId); });

async function cdp(tabId, method, params = {}) {
  await attach(tabId);
  return chrome.debugger.sendCommand({ tabId }, method, params);
}

async function pointerClick(tabId, x, y) {
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
}

const INSPECT_EXPRESSION = `(() => {
  const controls = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role="button"],[role="link"]'))
    .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.bottom >= 0 && r.top <= innerHeight && r.right >= 0 && r.left <= innerWidth; })
    .slice(0, 100).map((el, index) => { const r = el.getBoundingClientRect(); return {
      index, tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
      name: (el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || el.getAttribute('placeholder') || '').trim().slice(0, 140),
      type: el.getAttribute('type') || '', href: el.tagName === 'A' ? el.href.slice(0, 300) : '',
      x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)
    }; });
  return { url: location.href, title: document.title, text: (document.body?.innerText || '').slice(0, 14000),
    controls, viewport: { width: innerWidth, height: innerHeight } };
})()`;

async function handle(command) {
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
  if (action === "activate") {
    const tab = await chrome.tabs.get(tabId);
    await chrome.tabs.update(tabId, { active: true });
    if (tab.windowId != null) await chrome.windows.update(tab.windowId, { focused: true });
    return { tabId, active: true };
  }
  if (action === "close") {
    await chrome.tabs.remove(tabId);
    attachedTabs.delete(tabId);
    return { tabId, closed: true };
  }
  if (action === "navigate") {
    if (!isHttpUrl(args.url)) throw new Error("Only HTTP and HTTPS URLs are supported");
    const tab = await chrome.tabs.update(tabId, { url: args.url, active: true });
    return { tabId, url: tab.url || args.url };
  }
  if (action === "inspect") {
    const output = await cdp(tabId, "Runtime.evaluate", { expression: INSPECT_EXPRESSION, returnByValue: true });
    if (output.exceptionDetails) throw new Error("Could not inspect this page");
    return { tabId, ...output.result.value };
  }
  if (action === "screenshot") {
    const [shot, viewport] = await Promise.all([
      cdp(tabId, "Page.captureScreenshot", { format: "png", captureBeyondViewport: false }),
      cdp(tabId, "Runtime.evaluate", { expression: "({width:innerWidth,height:innerHeight})", returnByValue: true }),
    ]);
    return { tabId, dataUrl: `data:image/png;base64,${shot.data}`, viewport: viewport.result.value };
  }
  if (action === "click" || action === "type" || action === "scroll") {
    const x = Number(args.x), y = Number(args.y);
    if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 || x > 10000 || y > 10000) throw new Error("Invalid browser coordinates");
    if (action === "scroll") {
      const deltaY = Math.max(-2000, Math.min(2000, Number(args.deltaY) || 0));
      await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseWheel", x, y, deltaX: 0, deltaY });
      return { tabId, scrolled: deltaY };
    }
    await pointerClick(tabId, x, y);
    if (action === "type") {
      if (typeof args.text !== "string" || args.text.length > 4000) throw new Error("Text is too long");
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
