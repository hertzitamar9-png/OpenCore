import { useEffect, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { ArrowLeft, ArrowRight, ExternalLink, FileDown, Globe2, Grip, Plus, RefreshCw, X } from "lucide-react";
import * as api from "./api";
import { BrowserPanel } from "./BrowserPanel";

type Preview = api.ArtifactPreview | api.ComposerAttachmentPreview | { remoteImage: string } | null;
type LoadedPreview = Exclude<Preview, null>;
type WebTab = { id: string; title: string; url: string };
type FileTab = { id: string; title: string; preview: LoadedPreview | null };
type Props = {
  onClose: () => void;
  onNotice: (message: string) => void;
  preview: Preview;
  onDownload: (id: string) => void;
  width: number;
  onWidthChange: (width: number) => void;
  side: "left" | "right";
  onSideChange: (side: "left" | "right") => void;
  snapPx: number;
  onSnapChange: (snap: number) => void;
  obscured?: boolean;
  embedded?: boolean;
  active?: boolean;
  navigateTo?: { url: string; requestId: string } | null;
};

const DEFAULT_WEB_URL = "https://www.google.com";
const browserArgs = (id: string, args: Record<string, unknown> = {}) => id === "default" ? args : { ...args, tabId: id };
function browserCommand<T>(action: string, id: string, args: Record<string, unknown> = {}) {
  if (id === "default" && Object.keys(args).length === 0) return api.nativeBrowserCommand<T>(action);
  return api.nativeBrowserCommand<T>(action, browserArgs(id, args));
}
const fileTitle = (preview: LoadedPreview) => "remoteImage" in preview ? "Image" : preview.name;

export function NativeBrowserPanel({ onClose, onNotice, preview, onDownload, width, onWidthChange, side, onSideChange, snapPx, onSnapChange, obscured = false, embedded = false, active: visible = true, navigateTo }: Props) {
  const root = useRef<HTMLElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const headDrag = useRef<number | null>(null);
  const nextWebTabNumber = useRef(2);
  const visibleRef = useRef(visible);
  const navigatedRequest = useRef<string | null>(null);
  const openingRequest = useRef<string | null>(null);
  const webTabsRef = useRef<WebTab[]>([]);
  const [source, setSource] = useState<"web" | "files" | "chrome">("web");
  const [webTabs, setWebTabs] = useState<WebTab[]>([{ id: "default", title: "Web 1", url: DEFAULT_WEB_URL }]);
  const [activeWebTabId, setActiveWebTabId] = useState<string | null>("default");
  const selectedWebTabRef = useRef(activeWebTabId);
  selectedWebTabRef.current = activeWebTabId;
  visibleRef.current = visible && !obscured && source === "web";
  const [fileTabs, setFileTabs] = useState<FileTab[]>([]);
  const [activeFileTabId, setActiveFileTabId] = useState<string | null>(null);
  const [address, setAddress] = useState(DEFAULT_WEB_URL);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [snapOpen, setSnapOpen] = useState(false);
  webTabsRef.current = webTabs;
  const activeWebTab = webTabs.find((tab) => tab.id === activeWebTabId) ?? null;
  const activeFileTab = fileTabs.find((tab) => tab.id === activeFileTabId) ?? null;

  useEffect(() => {
    let active = true;
    if (!visible || obscured || source !== "web" || !activeWebTab) return;
    setLoading(true);
    setError("");
    void browserCommand<{ open: boolean; url?: string }>("status", activeWebTab.id).then((status) =>
      status.open || !active || !visibleRef.current || selectedWebTabRef.current !== activeWebTab.id
        ? status : browserCommand<{ open: boolean; url?: string }>("open", activeWebTab.id, { url: activeWebTab.url })
    ).then((result) => {
      if (!visibleRef.current || selectedWebTabRef.current !== activeWebTab.id) void browserCommand("hide", activeWebTab.id).catch(() => {});
      if (active) {
        const url = result.url || activeWebTab.url;
        setAddress(url);
        setWebTabs((tabs) => tabs.map((tab) => tab.id === activeWebTab.id ? { ...tab, url } : tab));
        setLoading(false);
      }
    }).catch((reason) => { if (active) { setError(String(reason)); setLoading(false); } });
    return () => { active = false; };
  }, [activeWebTabId, source, visible, obscured]);

  useEffect(() => {
    if (!preview) return;
    const tab = { id: crypto.randomUUID(), title: fileTitle(preview), preview };
    setFileTabs((tabs) => [...tabs, tab]);
    setActiveFileTabId(tab.id);
    setSource("files");
  }, [preview]);

  useEffect(() => () => {
    for (const tab of webTabsRef.current) void browserCommand("close", tab.id).catch(() => {});
  }, []);

  useEffect(() => {
    for (const tab of webTabs) {
      const action = visible && source === "web" && !obscured && !loading && !error && tab.id === activeWebTab?.id ? "show" : "hide";
      void browserCommand(action, tab.id).catch(() => {});
    }
  }, [activeWebTabId, webTabs, source, loading, error, obscured, visible]);

  useEffect(() => {
    if (!visible || !navigateTo || navigatedRequest.current === navigateTo.requestId) return;
    if (!activeWebTab) {
      if (openingRequest.current === navigateTo.requestId) return;
      openingRequest.current = navigateTo.requestId;
      const tab = {id: crypto.randomUUID(), title: `Web ${nextWebTabNumber.current++}`, url: navigateTo.url};
      setLoading(true); setError(""); setSource("web");
      setWebTabs(tabs => [...tabs, tab]); setActiveWebTabId(tab.id);
      return;
    }
    if (loading) return;
    navigatedRequest.current = navigateTo.requestId;
    setSource("web");
    void browserCommand<{ url?: string }>("navigate", activeWebTab.id, { url: navigateTo.url }).then(result => {
      const url = result.url || navigateTo.url;
      setAddress(url);
      setWebTabs(tabs => tabs.map(tab => tab.id === activeWebTab.id ? { ...tab, url } : tab));
    }).catch(reason => onNotice(`Browser: ${String(reason)}`));
  }, [visible, navigateTo, activeWebTabId, loading, onNotice]);

  useEffect(() => {
    if (!visible || obscured || loading || error || source !== "web" || !activeWebTab) return;
    let animation = 0;
    let last = "";
    let pending = false;
    const position = () => {
      const rect = stage.current?.getBoundingClientRect();
      if (rect && rect.width > 0 && rect.height > 0) {
        const bounds = { x: Math.max(0, rect.left), y: Math.max(0, rect.top), width: Math.max(1, rect.width), height: Math.max(1, rect.height) };
        const key = Object.values(bounds).map((value) => Math.round(value)).join(",");
        if (key !== last && !pending) {
          last = key;
          pending = true;
          void browserCommand("bounds", activeWebTab.id, bounds).catch((reason) => onNotice(`Browser: ${String(reason)}`)).finally(() => { pending = false; });
        }
      }
      animation = requestAnimationFrame(position);
    };
    animation = requestAnimationFrame(position);
    return () => cancelAnimationFrame(animation);
  }, [activeWebTabId, loading, error, source, onNotice, visible, obscured]);

  useEffect(() => {
    if (!visible || loading || error || source !== "web" || !activeWebTab) return;
    const timer = window.setInterval(() => {
      void browserCommand<{ url: string }>("status", activeWebTab.id).then((result) => {
        if (result.url && document.activeElement?.getAttribute("aria-label") !== "Browser address") {
          setAddress(result.url);
          setWebTabs((tabs) => tabs.map((tab) => tab.id === activeWebTab.id ? { ...tab, url: result.url } : tab));
        }
      }).catch(() => {});
    }, 1300);
    return () => window.clearInterval(timer);
  }, [activeWebTabId, loading, error, source, visible]);

  const act = async (action: string, args: Record<string, unknown> = {}) => {
    if (!activeWebTab) return;
    try {
      const result = await browserCommand<{ url?: string }>(action, activeWebTab.id, args);
      if (result.url) {
        setAddress(result.url);
        setWebTabs((tabs) => tabs.map((tab) => tab.id === activeWebTab.id ? { ...tab, url: result.url! } : tab));
      }
    } catch (reason) { onNotice(`Browser: ${String(reason)}`); }
  };

  const addWebTab = () => {
    const tab = { id: crypto.randomUUID(), title: `Web ${nextWebTabNumber.current++}`, url: DEFAULT_WEB_URL };
    setWebTabs((tabs) => [...tabs, tab]);
    setActiveWebTabId(tab.id);
    setSource("web");
  };

  const closeWebTab = (id: string) => {
    void browserCommand("close", id).catch((reason) => onNotice(`Browser: ${String(reason)}`));
    setWebTabs((tabs) => tabs.filter((tab) => tab.id !== id));
    if (activeWebTabId === id) {
      const next = webTabs.find((tab) => tab.id !== id);
      setActiveWebTabId(next?.id ?? null);
    }
  };

  const openFileTab = async () => {
    try {
      const selected = await open({ multiple: false, directory: false, title: "Open file in a new tab" });
      if (typeof selected !== "string") return;
      const filePreview = await api.previewComposerAttachment(selected);
      const tab = { id: crypto.randomUUID(), title: filePreview.name, preview: filePreview };
      setFileTabs((tabs) => [...tabs, tab]);
      setActiveFileTabId(tab.id);
      setSource("files");
    } catch (reason) { onNotice(`Could not open file: ${String(reason)}`); }
  };

  const closeFileTab = (id: string) => {
    setFileTabs((tabs) => tabs.filter((tab) => tab.id !== id));
    if (activeFileTabId === id) {
      const next = fileTabs.find((tab) => tab.id !== id);
      setActiveFileTabId(next?.id ?? null);
    }
  };

  const resizeEnd = () => {
    drag.current = null;
    const parent = root.current?.parentElement?.getBoundingClientRect();
    if (!parent || snapPx <= 0) return;
    const stops = [parent.width * .35, parent.width * .5, parent.width * .65];
    const target = stops.find((stop) => Math.abs(stop - width) <= snapPx);
    if (target) onWidthChange(Math.round(target));
  };

  const downloadableId = activeFileTab?.preview && "id" in activeFileTab.preview ? activeFileTab.preview.id : null;

  return <section ref={root} className={`workspace-browser ${embedded ? "workspace-browser-embedded" : `browser-split browser-${side}`}`} aria-label="OpenCore Browser">
    {!embedded ? <><div className="workspace-browser-resizer" role="separator" aria-label="Resize OpenCore Browser" onPointerDown={(event) => { drag.current = { x: event.clientX, width }; event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={(event) => { if (drag.current) onWidthChange(Math.max(420, Math.min(window.innerWidth - 420, drag.current.width + (side === "right" ? drag.current.x - event.clientX : event.clientX - drag.current.x)))); }} onPointerUp={(event) => { resizeEnd(); event.currentTarget.releasePointerCapture(event.pointerId); }} />
    <header className="workspace-browser-head" onPointerDown={(event) => { if (!(event.target as HTMLElement).closest("button,input")) { headDrag.current = event.clientX; event.currentTarget.setPointerCapture(event.pointerId); } }} onPointerUp={(event) => { if (headDrag.current != null && Math.abs(event.clientX - headDrag.current) > 60) { onSideChange(event.clientX < window.innerWidth / 2 ? "left" : "right"); } headDrag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}>
      <span className="workspace-browser-logo"><Globe2 size={16} /></span><strong>OpenCore Browser</strong><Grip size={15} className="workspace-browser-grip" />
      <button aria-label="Snap settings" title="Snap settings" onClick={() => setSnapOpen((open) => !open)}><span className="snap-symbol">◈</span></button>
      <button aria-label="Close browser" title="Close browser" onClick={onClose}><X size={17} /></button>
    </header>
    {snapOpen ? <div className="workspace-snap-control"><span>Snap</span><input type="range" aria-label="Snap strength" min="0" max="80" value={snapPx} onChange={(event) => onSnapChange(Number(event.target.value))} /><strong>{snapPx}</strong></div> : null}</> : null}
    <nav className="workspace-browser-tabs" aria-label="Browser views"><button className={source === "web" ? "active" : ""} onClick={() => setSource("web")}>Web</button>{!embedded ? <button className={source === "files" ? "active" : ""} onClick={() => setSource("files")}>Files</button> : null}<button className={source === "chrome" ? "active" : ""} onClick={() => setSource("chrome")}>Chrome tabs <ExternalLink size={12} /></button></nav>
    {source === "web" ? <>
      <div className="workspace-browser-pages" role="tablist" aria-label="Web tabs">
        {webTabs.map((tab) => <div className="workspace-browser-page-tab" key={tab.id}>
          <button role="tab" aria-selected={tab.id === activeWebTabId} onClick={() => { setActiveWebTabId(tab.id); setSource("web"); }}>{tab.title}</button>
          <button type="button" aria-label={`Close ${tab.title}`} title={`Close ${tab.title}`} onClick={() => closeWebTab(tab.id)}><X size={12} /></button>
        </div>)}
        <button type="button" className="workspace-browser-new-tab" aria-label="New web tab" title="New web tab" onClick={addWebTab}><Plus size={15} /></button>
      </div>
      {activeWebTab ? <>
        <div className="native-browser-toolbar"><button aria-label="Back" title="Back" onClick={() => void act("back")}><ArrowLeft size={16} /></button><button aria-label="Forward" title="Forward" onClick={() => void act("forward")}><ArrowRight size={16} /></button><button aria-label="Reload" title="Reload" onClick={() => void act("reload")}><RefreshCw size={16} /></button><div className="native-browser-address"><Globe2 size={15} /><input aria-label="Browser address" value={address} onChange={(event) => setAddress(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void act("navigate", { url: address }); }} placeholder="Enter a URL" /></div><button className="native-browser-go" onClick={() => void act("navigate", { url: address })}>Go</button></div>
        <div className="native-browser-stage" ref={stage}>{loading ? <span>Opening browser…</span> : error ? <span>{error} <button onClick={() => { if (!activeWebTab) return; setError(""); setLoading(true); void browserCommand<{ url: string }>("open", activeWebTab.id, { url: address }).then((result) => { setAddress(result.url || address); setLoading(false); }).catch((reason) => { setError(String(reason)); setLoading(false); }); }}>Retry</button></span> : null}</div>
      </> : <div className="browser-empty"><span>No web tabs open.</span><button onClick={addWebTab}>Open a web tab</button></div>}
    </> : null}
    {source === "files" ? <>
      <div className="workspace-browser-pages" role="tablist" aria-label="Files tabs">
        {fileTabs.map((tab) => <div className="workspace-browser-page-tab" key={tab.id}>
          <button role="tab" aria-selected={tab.id === activeFileTabId} onClick={() => setActiveFileTabId(tab.id)}>{tab.title}</button>
          <button type="button" aria-label={`Close ${tab.title}`} title={`Close ${tab.title}`} onClick={() => closeFileTab(tab.id)}><X size={12} /></button>
        </div>)}
        <button type="button" className="workspace-browser-new-tab" aria-label="Open file in new tab" title="Open file in new tab" onClick={() => void openFileTab()}><Plus size={15} /></button>
      </div>
      <div className="workspace-file-view">{activeFileTab?.preview ? <><div className="workspace-file-toolbar"><strong>{activeFileTab.title}</strong>{downloadableId ? <button onClick={() => onDownload(downloadableId)}><FileDown size={14} /> Download</button> : null}</div>{"remoteImage" in activeFileTab.preview ? <img src={activeFileTab.preview.remoteImage} alt="Preview" /> : activeFileTab.preview.mime.startsWith("image/") ? <img src={activeFileTab.preview.dataUrl} alt={activeFileTab.preview.name} /> : activeFileTab.preview.mime === "text/html" ? <iframe title={activeFileTab.preview.name} sandbox="allow-scripts" srcDoc={activeFileTab.preview.text ?? ""} /> : activeFileTab.preview.mime === "application/pdf" ? <iframe title={activeFileTab.preview.name} src={activeFileTab.preview.dataUrl} /> : <pre>{activeFileTab.preview.text ?? "Preview unavailable"}</pre>}</> : <div className="browser-empty">Open a generated file or choose a file with the + button.</div>}</div>
    </> : null}
    {source === "chrome" ? <BrowserPanel embedded onNotice={onNotice} /> : null}
  </section>;
}
