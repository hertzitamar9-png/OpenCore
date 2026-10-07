import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Camera, Copy, ExternalLink, Globe2, MousePointer2, RefreshCw } from "lucide-react";
import * as api from "./api";
import { browserPoint } from "./browser-coordinates";
import { FloatingWindow } from "./FloatingWindow";
import "./AutomationSettings.css";

type Props = { onClose?: () => void; onNotice: (message: string) => void; embedded?: boolean };
type Point = { x: number; y: number };

export function BrowserPanel({ onClose = () => {}, onNotice, embedded = false }: Props) {
  const [status, setStatus] = useState<api.BrowserStatus | null>(null);
  const [tabs, setTabs] = useState<api.BrowserTab[]>([]);
  const [tabId, setTabId] = useState<number | null>(null);
  const [address, setAddress] = useState("");
  const [shot, setShot] = useState<api.BrowserShot | null>(null);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [lastPoint, setLastPoint] = useState<Point | null>(null);
  const [typing, setTyping] = useState("");
  const [busy, setBusy] = useState(false);
  const [connectionBusy, setConnectionBusy] = useState(false);
  const mounted = useRef(true);
  const statusRef = useRef(status);
  const notice = useRef(onNotice);
  const selection = useRef({ tabId: null as number | null, revision: 0, url: "" });
  const connectionRevision = useRef(0);
  const captureSequence = useRef(0);
  const capturePending = useRef<number | null>(null);
  const commandBusy = useRef(false);
  const changingConnection = useRef(false);
  const errorShown = useRef("");
  statusRef.current = status;
  notice.current = onNotice;

  const clear = useCallback(() => {
    selection.current = { tabId: null, revision: selection.current.revision + 1, url: "" };
    setTabs([]); setTabId(null); setShot(null); setLastPoint(null); setCursor(null);
  }, []);
  const refresh = useCallback(async (preferred?: number, force = false) => {
    if (!mounted.current || changingConnection.current || (capturePending.current != null && !force)) return;
    const version = connectionRevision.current, sequence = ++captureSequence.current;
    capturePending.current = sequence;
    const latest = () => mounted.current && version === connectionRevision.current && sequence === captureSequence.current;
    try {
      const nextStatus = await api.browserBridgeStatus();
      if (!latest()) return;
      statusRef.current = nextStatus; setStatus(nextStatus);
      if (nextStatus.enabled === false || !nextStatus.connected) { clear(); return; }
      const listed = await api.browserCommand<{ tabs: api.BrowserTab[] }>("list");
      if (!latest()) return;
      setTabs(listed.tabs);
      const wanted = preferred ?? selection.current.tabId;
      const selected = listed.tabs.find(tab => tab.tabId === wanted) ?? listed.tabs.find(tab => tab.active) ?? listed.tabs[0];
      if (!selected) { clear(); return; }
      if (selection.current.tabId !== selected.tabId || selection.current.url !== selected.url) {
        selection.current = { tabId: selected.tabId, revision: selection.current.revision + 1, url: selected.url };
        setShot(null); setLastPoint(null); setCursor(null);
      }
      setTabId(selected.tabId);
      if (selected.url) setAddress(previous => previous === selected.url ? previous : selected.url);
      const targetRevision = selection.current.revision;
      const image = await api.browserCommand<api.BrowserShot>("screenshot", { tabId: selected.tabId });
      if (!latest() || targetRevision !== selection.current.revision) return;
      if (image.tabId !== selected.tabId || image.viewport.width <= 0 || image.viewport.height <= 0) {
        throw new Error("Chrome returned an unavailable capture. Refresh the tab before interacting.");
      }
      setShot(previous => previous?.tabId === image.tabId && previous.dataUrl === image.dataUrl &&
        previous.viewport.width === image.viewport.width && previous.viewport.height === image.viewport.height ? previous : image);
      errorShown.current = "";
    } catch (error) {
      if (!latest()) return;
      setShot(null); setLastPoint(null); setCursor(null);
      const message = String(error);
      if (errorShown.current !== message) { notice.current(`Browser: ${message}`); errorShown.current = message; }
    } finally { if (capturePending.current === sequence) capturePending.current = null; }
  }, [clear]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { mounted.current = false; captureSequence.current++; window.clearInterval(timer); };
  }, [refresh]);

  const toggleConnection = async () => {
    if (!statusRef.current || changingConnection.current) return;
    changingConnection.current = true; setConnectionBusy(true);
    const enabled = statusRef.current.enabled === false, version = ++connectionRevision.current;
    captureSequence.current++; capturePending.current = null;
    commandBusy.current = false; setBusy(false); clear();
    try {
      const next = await api.setBrowserAccess(enabled);
      if (mounted.current && version === connectionRevision.current) { statusRef.current = next; setStatus(next); }
    } catch (error) { if (mounted.current && version === connectionRevision.current) notice.current(`Browser: ${String(error)}`); }
    finally {
      changingConnection.current = false;
      if (mounted.current && version === connectionRevision.current) { setConnectionBusy(false); }
    }
  };
  const act = async (action: string, args: Record<string, unknown> = {}) => {
    if (commandBusy.current || changingConnection.current || !statusRef.current?.connected || statusRef.current.enabled === false) return false;
    const version = connectionRevision.current, target = { ...selection.current };
    commandBusy.current = true; setBusy(true);
    try {
      const result = await api.browserCommand<{ tabId?: number }>(action, { ...(target.tabId != null ? { tabId: target.tabId } : {}), ...args });
      if (!mounted.current || version !== connectionRevision.current) return false;
      if (target.revision === selection.current.revision) await refresh(result.tabId, true);
      return true;
    } catch (error) { if (mounted.current && version === connectionRevision.current) notice.current(`Browser: ${String(error)}`); return false; }
    finally { if (mounted.current && version === connectionRevision.current) { commandBusy.current = false; setBusy(false); } }
  };
  const go = () => {
    const value = address.trim();
    if (!value) return;
    const url = /^https?:\/\//i.test(value) ? value : `https://${value}`;
    void act(selection.current.tabId == null ? "open" : "navigate", { url });
  };
  const point = (event: React.MouseEvent<HTMLImageElement> | React.WheelEvent<HTMLImageElement>) => {
    if (!shot || shot.tabId !== selection.current.tabId) return null;
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return browserPoint(event.clientX, event.clientY, rect.left, rect.top, rect.width, rect.height, shot.viewport.width, shot.viewport.height);
  };
  const sendTyping = async () => {
    if (!lastPoint || !typing) return;
    const text = typing;
    if (await act("type", { ...lastPoint, text })) setTyping(current => current === text ? "" : current);
  };
  const connected = status?.connected && status.enabled !== false && !connectionBusy;
  const content = <>
    <div className="desktop-access-bar"><span>{status?.enabled === false ? "Browser access disabled" : status?.connected ? "Chrome connected" : "Waiting for Chrome"}</span><button type="button" disabled={!status || connectionBusy} onClick={() => void toggleConnection()}>{status?.enabled === false ? "Reconnect Chrome" : "Disconnect Chrome"}</button></div>
    {status && status.enabled !== false && !status.connected ? <div className="browser-pair"><strong>Connect Chrome</strong><span>Load the OpenCore extension in Chrome, then paste this pairing code in its toolbar popup.</span><div><code>{status.token || "Open the desktop app"}</code><button onClick={() => void navigator.clipboard.writeText(status.token).then(() => onNotice("Pairing code copied")).catch(error => onNotice(String(error)))}><Copy size={13} /> Copy</button></div>{status.extensionPath ? <button onClick={() => void api.openLocalPath(status.extensionPath!).catch(error => onNotice(String(error)))}><ExternalLink size={13} /> Extension folder</button> : null}</div> : null}
    <div className="browser-toolbar"><button title="Back" onClick={() => void act("back")} disabled={!connected || busy}><ArrowLeft size={16} /></button><button title="Forward" onClick={() => void act("forward")} disabled={!connected || busy}><ArrowRight size={16} /></button><button title="Refresh screenshot" onClick={() => void refresh(undefined, true)} disabled={!connected || busy}><RefreshCw size={16} /></button><input aria-label="Browser address" value={address} onChange={event => setAddress(event.target.value)} onKeyDown={event => { if (event.key === "Enter") go(); }} placeholder="Enter a URL" /><button onClick={go} disabled={!connected || busy}>Go</button></div>
    <div className="browser-subbar"><select aria-label="Chrome tab" value={tabId ?? ""} disabled={!connected} onChange={event => {
      const id = event.target.value === "" ? null : Number(event.target.value);
      selection.current = { tabId: id, revision: selection.current.revision + 1, url: "" };
      captureSequence.current++; setTabId(id); setShot(null); setLastPoint(null); setCursor(null);
      if (id != null) void refresh(id, true);
    }}><option value="">Select a tab</option>{tabs.map(tab => <option key={tab.tabId} value={tab.tabId}>{tab.title || tab.url || `Tab ${tab.tabId}`}</option>)}</select><button onClick={() => void act("open", { url: address || "https://example.com" })} disabled={!connected || busy}>New tab</button><button title="Capture screenshot" onClick={() => void refresh(undefined, true)} disabled={!connected || busy}><Camera size={15} /></button></div>
    <div className="browser-stage">{shot ? <div className="browser-screen"><img src={shot.dataUrl} alt="Chrome tab screenshot" draggable={false} onMouseMove={event => setCursor(point(event))} onMouseLeave={() => setCursor(null)} onClick={event => { const at = point(event); if (at) { setLastPoint(at); void act("click", at); } }} onWheel={event => { const at = point(event); if (at) { event.preventDefault(); void act("scroll", { ...at, deltaY: event.deltaY }); } }} />{cursor ? <span className="browser-cursor" style={{ left: `${cursor.x / shot.viewport.width * 100}%`, top: `${cursor.y / shot.viewport.height * 100}%` }}><MousePointer2 size={18} /></span> : null}</div> : <div className="browser-empty">{status?.enabled === false ? "Reconnect Chrome to enable browser access" : connected ? "Open a tab to begin" : "Connect the Chrome extension to browse here"}</div>}</div>
    <div className="desktop-inputbar"><input aria-label="Type in browser page" placeholder="Type in the page" value={typing} disabled={!connected} onChange={event => setTyping(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.nativeEvent.isComposing) void sendTyping(); }} /><button disabled={!lastPoint || !typing || busy || !connected} onClick={() => void sendTyping()}>Type</button><button disabled={busy || !connected} onClick={() => void act("key", { key: "Enter" })}>Enter</button></div>
  </>;
  return embedded ? <section className="chrome-tabs-content">{content}</section> : <FloatingWindow id="browser" title="Chrome tabs" icon={<Globe2 size={17} />} status={<span className={connected ? "connected" : ""}>{status?.enabled === false ? "Access disabled" : connected ? "Connected" : "Extension offline"}</span>} onClose={onClose} className="browser-panel" ariaLabel="Chrome tabs" initialWidth={790} initialHeight={760} minWidth={440} minHeight={320}>{content}</FloatingWindow>;
}
