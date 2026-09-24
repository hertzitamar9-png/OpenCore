const PORT = 8814;
let socket = null;
let reconnectTimer = null;
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
  if (action === "back" || action === "forward") {
    const history = await cdp(tabId, "Page.getNavigationHistory");
    const next = history.currentIndex + (action === "back" ? -1 : 1);
    if (!history.entries[next]) throw new Error("No page in that direction");
    await cdp(tabId, "Page.navigateToHistoryEntry", { entryId: history.entries[next].id });
    return { tabId, url: history.entries[next].url };
  }
  throw new Error("Unsupported browser action");
}

async function connect() {
  if (socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.readyState)) return;
  const { pairingToken } = await chrome.storage.local.get("pairingToken");
  if (!pairingToken) return;
  const activeSocket = new WebSocket(`ws://127.0.0.1:${PORT}/ws?token=${encodeURIComponent(pairingToken)}`);
  socket = activeSocket;
  activeSocket.onopen = () => chrome.action.setBadgeText({ text: "ON" });
  activeSocket.onmessage = async (event) => {
    let command;
    try { command = JSON.parse(event.data); } catch { return; }
    if (!command?.id) return;
    try {
      const result = await handle(command);
      if (activeSocket.readyState === WebSocket.OPEN) activeSocket.send(JSON.stringify({ id: command.id, ok: true, result }));
    } catch (error) {
      if (activeSocket.readyState === WebSocket.OPEN) activeSocket.send(JSON.stringify({ id: command.id, ok: false, error: String(error?.message || error) }));
    }
  };
  activeSocket.onclose = () => {
    if (socket !== activeSocket) return;
    chrome.action.setBadgeText({ text: "" });
    socket = null;
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 3000);
  };
  activeSocket.onerror = () => {};
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "pair") return;
  chrome.storage.local.set({ pairingToken: String(message.token || "").trim() })
    .then(() => { socket?.close(); socket = null; return connect(); })
    .then(() => sendResponse({ ok: true }))
    .catch(error => sendResponse({ ok: false, error: String(error) }));
  return true;
});

chrome.alarms.create("opencore-reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "opencore-reconnect") connect(); });
setInterval(() => { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping" })); }, 20000);
connect();
