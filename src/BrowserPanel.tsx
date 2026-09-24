import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Camera, Copy, ExternalLink, Globe2, MousePointer2, RefreshCw } from "lucide-react";
import * as api from "./api";
import { browserPoint } from "./browser-coordinates";
import { FloatingWindow } from "./FloatingWindow";

type Props = { onClose?: () => void; onNotice: (message: string) => void; embedded?: boolean };

export function BrowserPanel({ onClose = () => {}, onNotice, embedded = false }: Props) {
  const [status, setStatus] = useState<api.BrowserStatus | null>(null);
  const [tabs, setTabs] = useState<api.BrowserTab[]>([]);
  const [tabId, setTabId] = useState<number | null>(null);
  const [address, setAddress] = useState("");
  const [shot, setShot] = useState<api.BrowserShot | null>(null);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [lastPoint, setLastPoint] = useState<{ x: number; y: number } | null>(null);
  const [typing, setTyping] = useState("");
  const [busy, setBusy] = useState(false);
  const refreshing = useRef(false);
  const errorShown = useRef("");

  const refresh = useCallback(async (preferred?: number) => {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const nextStatus = await api.browserBridgeStatus();
      setStatus(nextStatus);
      if (!nextStatus.connected) { setShot(null); return; }
      const listed = await api.browserCommand<{ tabs: api.BrowserTab[] }>("list");
      setTabs(listed.tabs);
      const selected = preferred ?? tabId ?? listed.tabs.find((tab) => tab.active)?.tabId ?? listed.tabs[0]?.tabId;
      if (selected == null) return;
      setTabId(selected);
      const tab = listed.tabs.find((item) => item.tabId === selected);
      if (tab?.url) setAddress(tab.url);
      const image = await api.browserCommand<api.BrowserShot>("screenshot", { tabId: selected });
      setShot(image);
      errorShown.current = "";
    } catch (error) {
      const message = String(error);
      if (errorShown.current !== message) { onNotice(`Browser: ${message}`); errorShown.current = message; }
    }
    finally { refreshing.current = false; }
  }, [tabId, onNotice]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const act = async (action: string, args: Record<string, unknown> = {}) => {
    setBusy(true);
    try {
      const result = await api.browserCommand<{ tabId?: number }>(action, { ...(tabId != null ? { tabId } : {}), ...args });
      await new Promise((resolve) => window.setTimeout(resolve, 300));
      await refresh(result.tabId);
    } catch (error) { onNotice(`Browser: ${String(error)}`); }
    finally { setBusy(false); }
  };

  const go = () => {
    const url = /^https?:\/\//i.test(address.trim()) ? address.trim() : `https://${address.trim()}`;
    if (!address.trim()) return;
    void act(tabId == null ? "open" : "navigate", { url });
  };

  const point = (event: React.MouseEvent<HTMLImageElement>) => {
    if (!shot) return null;
    const rect = event.currentTarget.getBoundingClientRect();
    return browserPoint(event.clientX, event.clientY, rect.left, rect.top, rect.width, rect.height, shot.viewport.width, shot.viewport.height);
  };

  const content = <>
    {status && !status.connected ? <div className="browser-pair"><strong>Connect Chrome</strong><span>Load the OpenCore extension in Chrome, then paste this pairing code in its toolbar popup.</span><div><code>{status.token || "Open the desktop app"}</code><button onClick={() => void navigator.clipboard.writeText(status.token).then(() => onNotice("Pairing code copied"))}><Copy size={13} /> Copy</button></div>{status.extensionPath ? <button onClick={() => void api.openLocalPath(status.extensionPath!).catch((error) => onNotice(String(error)))}><ExternalLink size={13} /> Extension folder</button> : null}</div> : null}
    <div className="browser-toolbar"><button title="Back" onClick={() => void act("back")} disabled={!status?.connected || busy}><ArrowLeft size={16} /></button><button title="Forward" onClick={() => void act("forward")} disabled={!status?.connected || busy}><ArrowRight size={16} /></button><button title="Refresh screenshot" onClick={() => void refresh()} disabled={!status?.connected || busy}><RefreshCw size={16} /></button><input aria-label="Browser address" value={address} onChange={(event) => setAddress(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") go(); }} placeholder="Enter a URL" /><button onClick={go} disabled={!status?.connected || busy}>Go</button></div>
    <div className="browser-subbar"><select aria-label="Chrome tab" value={tabId ?? ""} onChange={(event) => { const id = Number(event.target.value); setTabId(id); void refresh(id); }}><option value="">Select a tab</option>{tabs.map((tab) => <option key={tab.tabId} value={tab.tabId}>{tab.title || tab.url || `Tab ${tab.tabId}`}</option>)}</select><button onClick={() => void act("open", { url: address || "https://example.com" })} disabled={!status?.connected || busy}>New tab</button><button title="Capture screenshot" onClick={() => void refresh()} disabled={!status?.connected || busy}><Camera size={15} /></button></div>
    <div className="browser-stage">{shot ? <div className="browser-screen"><img src={shot.dataUrl} alt="Chrome tab screenshot" draggable={false} onMouseMove={(event) => setCursor(point(event))} onMouseLeave={() => setCursor(null)} onClick={(event) => { const at = point(event); if (at) { setLastPoint(at); void act("click", at); } }} onWheel={(event) => { const at = point(event); if (at) void act("scroll", { ...at, deltaY: event.deltaY }); }} />{cursor ? <span className="browser-cursor" style={{ left: `${cursor.x / shot.viewport.width * 100}%`, top: `${cursor.y / shot.viewport.height * 100}%` }}><MousePointer2 size={18} /></span> : null}</div> : <div className="browser-empty">{status?.connected ? "Open a tab to begin" : "Connect the Chrome extension to browse here"}</div>}</div>
    <div className="desktop-inputbar"><input aria-label="Type in browser page" placeholder="Type in the page" value={typing} onChange={(event) => setTyping(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && lastPoint && typing) { void act("type", { ...lastPoint, text: typing }); setTyping(""); } }} /><button disabled={!lastPoint || !typing || busy || !status?.connected} onClick={() => { if (lastPoint) void act("type", { ...lastPoint, text: typing }); setTyping(""); }}>Type</button><button disabled={busy || !status?.connected} onClick={() => void act("key", { key: "Enter" })}>Enter</button></div>
  </>;
  return embedded ? <section className="chrome-tabs-content">{content}</section> : <FloatingWindow id="browser" title="Chrome tabs" icon={<Globe2 size={17} />} status={<span className={status?.connected ? "connected" : ""}>{status?.connected ? "Connected" : "Extension offline"}</span>} onClose={onClose} className="browser-panel" ariaLabel="Chrome tabs" initialWidth={790} initialHeight={760} minWidth={440} minHeight={320}>{content}</FloatingWindow>;
}
