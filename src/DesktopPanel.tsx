import { useCallback, useEffect, useRef, useState } from "react";
import { AppWindow, CornerDownLeft, RefreshCw } from "lucide-react";
import * as api from "./api";
import { browserPoint } from "./browser-coordinates";
import { FloatingWindow } from "./FloatingWindow";

type Props = { onClose: () => void; onNotice: (message: string) => void };
type Point = { x: number; y: number };
type Interaction = { editable?: boolean; value?: string; activated?: boolean; inputMode?: string };

export function DesktopPanel({ onClose, onNotice }: Props) {
  const [windows, setWindows] = useState<api.DesktopWindow[]>([]);
  const [windowId, setWindowId] = useState<number | null>(null);
  const [shot, setShot] = useState<api.DesktopShot | null>(null);
  const [inputAt, setInputAt] = useState<Point | null>(null);
  const [nativeInput, setNativeInput] = useState(false);
  const [typing, setTyping] = useState("");
  const [busy, setBusy] = useState(false);
  const refreshing = useRef(false);
  const errorShown = useRef("");
  const typingRef = useRef<HTMLInputElement>(null);
  const pending = useRef<number | null>(null);
  const updates = useRef<Promise<void>>(Promise.resolve());

  const refresh = useCallback(async (preferred?: number) => {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const listed = await api.desktopCommand<{ windows: api.DesktopWindow[] }>("list");
      setWindows(listed.windows);
      const selected = preferred ?? windowId;
      if (selected == null) { setShot(null); return; }
      if (!listed.windows.some((item) => item.windowId === selected)) { setWindowId(null); setShot(null); return; }
      setWindowId(selected);
      setShot(await api.desktopCommand<api.DesktopShot>("screenshot", { windowId: selected }));
      errorShown.current = "";
    } catch (error) {
      const message = String(error);
      if (errorShown.current !== message) { onNotice(`Desktop: ${message}`); errorShown.current = message; }
    } finally { refreshing.current = false; }
  }, [windowId, onNotice]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 450);
    return () => window.clearInterval(timer);
  }, [refresh]);
  useEffect(() => () => { if (pending.current != null) window.clearTimeout(pending.current); }, []);

  const interact = async (at: Point) => {
    if (windowId == null) return;
    if (pending.current != null) window.clearTimeout(pending.current);
    pending.current = null;
    setBusy(true);
    try {
      if (windowId === 0) {
        await api.desktopCommand("click", { windowId, ...at });
      } else {
        const result = await api.desktopCommand<Interaction>("interact", { windowId, ...at });
        if (result.editable) {
          setInputAt(at);
          setNativeInput(false);
          setTyping(result.value ?? "");
          window.setTimeout(() => typingRef.current?.focus(), 0);
        } else if (result.inputMode === "pointer") {
          setInputAt(at);
          setNativeInput(true);
          setTyping("");
          window.setTimeout(() => typingRef.current?.focus(), 0);
        } else { setInputAt(null); setNativeInput(false); }
      }
      void refresh(windowId);
    } catch (error) { onNotice(`Desktop: ${String(error)}`); }
    finally { setBusy(false); }
  };

  const edit = (text: string) => {
    setTyping(text);
    if (pending.current != null) window.clearTimeout(pending.current);
    if (windowId == null || !inputAt || nativeInput) return;
    pending.current = window.setTimeout(() => {
      updates.current = updates.current.catch(() => {}).then(async () => {
        await api.desktopCommand("set_at", { windowId, ...inputAt, text });
      });
      void updates.current.catch((error) => onNotice(`Desktop: ${String(error)}`));
    }, 130);
  };

  const submit = async () => {
    if (windowId == null || !inputAt || busy) return;
    if (pending.current != null) window.clearTimeout(pending.current);
    pending.current = null;
    setBusy(true);
    try {
      if (nativeInput) {
        await api.desktopCommand("commit_text", { windowId, ...inputAt, text: typing });
      } else {
        await updates.current;
        await api.desktopCommand("set_at", { windowId, ...inputAt, text: typing });
        await api.desktopCommand("commit_enter", { windowId, ...inputAt });
      }
      setInputAt(null);
      setNativeInput(false);
      void refresh(windowId);
    } catch (error) { onNotice(`Desktop: ${String(error)}`); }
    finally { setBusy(false); }
  };

  const point = (event: React.MouseEvent<HTMLImageElement>) => {
    if (!shot) return null;
    const rect = event.currentTarget.getBoundingClientRect();
    return browserPoint(event.clientX, event.clientY, rect.left, rect.top, rect.width, rect.height, shot.bounds.width, shot.bounds.height);
  };

  return <FloatingWindow id="desktop" title="Desktop" icon={<AppWindow size={17} />} status={<span className="connected">Windows</span>} onClose={onClose} className="desktop-panel" ariaLabel="Windows desktop" initialWidth={790} initialHeight={720} minWidth={440} minHeight={320}>
    <div className="browser-subbar"><select aria-label="Window" value={windowId ?? ""} onChange={(event) => { const value = event.target.value; const id = value === "" ? null : Number(value); setWindowId(id); setInputAt(null); setNativeInput(false); setShot(null); if (id != null) void refresh(id); }}><option value="">Select a window</option>{windows.map((item) => <option key={item.windowId} value={item.windowId}>{item.title}</option>)}</select><button title="Refresh window" onClick={() => void refresh()} disabled={busy}><RefreshCw size={15} /></button></div>
    <div className="browser-stage">{shot ? <div className="browser-screen"><img src={shot.dataUrl} alt="Selected Windows app" draggable={false} onClick={(event) => { const at = point(event); if (at) void interact(at); }} /></div> : <div className="browser-empty">Select a window to view and control it</div>}</div>
    <div className="desktop-inputbar"><input ref={typingRef} aria-label="Type in selected window" placeholder={inputAt ? "Type here; Enter opens the app" : "Select a text field in the window"} value={typing} onChange={(event) => edit(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void submit(); } }} disabled={!inputAt} /><button title="Enter in selected app" disabled={!inputAt || busy} onClick={() => void submit()}><CornerDownLeft size={15} /><span>Enter</span></button></div>
  </FloatingWindow>;
}
