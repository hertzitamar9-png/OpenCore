import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, ExternalLink, FileDown, Globe2, Grip, Maximize2, Minimize2, RefreshCw, X } from "lucide-react";
import * as api from "./api";
import { BrowserPanel } from "./BrowserPanel";

type Preview = api.ArtifactPreview | { remoteImage: string } | null;
type Props = {
  onClose: () => void;
  onNotice: (message: string) => void;
  preview: Preview;
  onDownload: (id: string) => void;
  full: boolean;
  onFullChange: (full: boolean) => void;
  width: number;
  onWidthChange: (width: number) => void;
  side: "left" | "right";
  onSideChange: (side: "left" | "right") => void;
  snapPx: number;
  onSnapChange: (snap: number) => void;
  obscured?: boolean;
};

export function NativeBrowserPanel({ onClose, onNotice, preview, onDownload, full, onFullChange, width, onWidthChange, side, onSideChange, snapPx, onSnapChange, obscured = false }: Props) {
  const root = useRef<HTMLElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const headDrag = useRef<number | null>(null);
  const [source, setSource] = useState<"web" | "files" | "chrome">("web");
  const [address, setAddress] = useState("https://www.google.com");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [snapOpen, setSnapOpen] = useState(false);

  useEffect(() => {
    let active = true;
    // A model tool can create and navigate the browser before this panel mounts.
    // Opening Google unconditionally here would replace the page it selected.
    void api.nativeBrowserCommand<{ open: boolean; url?: string }>("status").then((status) =>
      status.open ? status : api.nativeBrowserCommand<{ open: boolean; url?: string }>("open", { url: "https://www.google.com" })
    ).then((result) => {
      if (active) { setAddress(result.url || address); setLoading(false); }
    }).catch((reason) => { if (active) { setError(String(reason)); setLoading(false); } });
    return () => { active = false; void api.nativeBrowserCommand("close").catch(() => {}); };
  }, []);

  useEffect(() => { if (preview) setSource("files"); }, [preview]);
  useEffect(() => {
    if (loading || error) return;
    void api.nativeBrowserCommand(source === "web" && !obscured ? "show" : "hide").catch(() => {});
  }, [source, loading, error, obscured]);

  useEffect(() => {
    if (loading || error || source !== "web") return;
    let animation = 0;
    let last = "";
    let pending = false;
    const position = () => {
      const rect = stage.current?.getBoundingClientRect();
      if (rect) {
        const bounds = { x: Math.max(0, rect.left), y: Math.max(0, rect.top), width: Math.max(1, rect.width), height: Math.max(1, rect.height) };
        const key = Object.values(bounds).map((value) => Math.round(value)).join(",");
        if (key !== last && !pending) {
          last = key; pending = true;
          void api.nativeBrowserCommand("bounds", bounds).catch((reason) => onNotice(`Browser: ${String(reason)}`)).finally(() => { pending = false; });
        }
      }
      animation = requestAnimationFrame(position);
    };
    animation = requestAnimationFrame(position);
    return () => cancelAnimationFrame(animation);
  }, [loading, error, source, onNotice]);

  useEffect(() => {
    if (loading || error) return;
    const timer = window.setInterval(() => {
      void api.nativeBrowserCommand<{ url: string }>("status").then((result) => {
        if (result.url && document.activeElement?.getAttribute("aria-label") !== "Browser address") setAddress(result.url);
      }).catch(() => {});
    }, 1300);
    return () => window.clearInterval(timer);
  }, [loading, error]);

  const act = async (action: string, args: Record<string, unknown> = {}) => {
    try { const result = await api.nativeBrowserCommand<{ url?: string }>(action, args); if (result.url) setAddress(result.url); }
    catch (reason) { onNotice(`Browser: ${String(reason)}`); }
  };
  const close = () => { void api.nativeBrowserCommand("close").catch(() => {}); onClose(); };
  const resizeEnd = () => {
    drag.current = null;
    const parent = root.current?.parentElement?.getBoundingClientRect();
    if (!parent || snapPx <= 0) return;
    const stops = [parent.width * .35, parent.width * .5, parent.width * .65];
    const target = stops.find((stop) => Math.abs(stop - width) <= snapPx);
    if (target) onWidthChange(Math.round(target));
  };

  return <section ref={root} className={`workspace-browser ${full ? "browser-expanded" : "browser-split"} browser-${side}`} aria-label="OpenCore Browser">
    {!full ? <div className="workspace-browser-resizer" role="separator" aria-label="Resize OpenCore Browser" onPointerDown={(event) => { drag.current = { x: event.clientX, width }; event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={(event) => { if (drag.current) onWidthChange(Math.max(420, Math.min(window.innerWidth - 420, drag.current.width + (side === "right" ? drag.current.x - event.clientX : event.clientX - drag.current.x)))); }} onPointerUp={(event) => { resizeEnd(); event.currentTarget.releasePointerCapture(event.pointerId); }} /> : null}
    <header className="workspace-browser-head" onPointerDown={(event) => { if (!(event.target as HTMLElement).closest("button,input")) { headDrag.current = event.clientX; event.currentTarget.setPointerCapture(event.pointerId); } }} onPointerUp={(event) => { if (headDrag.current != null && Math.abs(event.clientX - headDrag.current) > 60) { onFullChange(false); onSideChange(event.clientX < window.innerWidth / 2 ? "left" : "right"); } headDrag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}>
      <span className="workspace-browser-logo"><Globe2 size={16} /></span><strong>OpenCore Browser</strong><Grip size={15} className="workspace-browser-grip" />
      <button aria-label="Snap settings" title="Snap settings" onClick={() => setSnapOpen((open) => !open)}><span className="snap-symbol">◈</span></button>
      <button aria-label={full ? "Split browser" : "Expand browser"} title={full ? "Split browser" : "Expand browser"} onClick={() => onFullChange(!full)}>{full ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button>
      <button aria-label="Close browser" title="Close browser" onClick={close}><X size={17} /></button>
    </header>
    {snapOpen ? <div className="workspace-snap-control"><span>Snap</span><input type="range" aria-label="Snap strength" min="0" max="80" value={snapPx} onChange={(event) => onSnapChange(Number(event.target.value))} /><strong>{snapPx}</strong></div> : null}
    <nav className="workspace-browser-tabs" aria-label="Browser views"><button className={source === "web" ? "active" : ""} onClick={() => setSource("web")}>Web</button><button className={source === "files" ? "active" : ""} onClick={() => setSource("files")}>Files</button><button className={source === "chrome" ? "active" : ""} onClick={() => setSource("chrome")}>Chrome tabs <ExternalLink size={12} /></button></nav>
    {source === "web" ? <><div className="native-browser-toolbar"><button aria-label="Back" title="Back" onClick={() => void act("back")}><ArrowLeft size={16} /></button><button aria-label="Forward" title="Forward" onClick={() => void act("forward")}><ArrowRight size={16} /></button><button aria-label="Reload" title="Reload" onClick={() => void act("reload")}><RefreshCw size={16} /></button><div className="native-browser-address"><Globe2 size={15} /><input aria-label="Browser address" value={address} onChange={(event) => setAddress(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void act("navigate", { url: address }); }} placeholder="Enter a URL" /></div><button className="native-browser-go" onClick={() => void act("navigate", { url: address })}>Go</button></div><div className="native-browser-stage" ref={stage}>{loading ? <span>Opening browser…</span> : error ? <span>{error} <button onClick={() => { setError(""); setLoading(true); void api.nativeBrowserCommand<{ url: string }>("open", { url: address }).then((result) => { setAddress(result.url || address); setLoading(false); }).catch((reason) => { setError(String(reason)); setLoading(false); }); }}>Retry</button></span> : null}</div></> : null}
    {source === "files" ? <div className="workspace-file-view">{preview ? <><div className="workspace-file-toolbar"><strong>{"remoteImage" in preview ? "Image" : preview.name}</strong>{!("remoteImage" in preview) ? <button onClick={() => onDownload(preview.id)}><FileDown size={14} /> Download</button> : null}</div>{"remoteImage" in preview ? <img src={preview.remoteImage} alt="Preview" /> : preview.mime.startsWith("image/") ? <img src={preview.dataUrl} alt={preview.name} /> : preview.mime === "text/html" ? <iframe title={preview.name} sandbox="allow-scripts" srcDoc={preview.text || ""} /> : preview.mime === "application/pdf" ? <iframe title={preview.name} src={preview.dataUrl} /> : <pre>{preview.text || "Preview unavailable"}</pre>}</> : <div className="browser-empty">Open a generated file or image from the conversation.</div>}</div> : null}
    {source === "chrome" ? <BrowserPanel embedded onNotice={onNotice} /> : null}
  </section>;
}
