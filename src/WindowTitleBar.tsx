import { useEffect, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Copy, Minus, Square, X } from "lucide-react";
import { useFluidUiScale } from "./useFluidUiScale";

function desktopWindow() {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window ? getCurrentWindow() : null;
}

export function WindowTitleBar() {
  useFluidUiScale();
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    const app = desktopWindow();
    if (!app) return;
    let disposed = false;
    const update = () => { void app.isMaximized().then(value => { if (!disposed) setMaximized(value); }).catch(() => {}); };
    update();
    let unlisten: (() => void) | undefined;
    void app.onResized(update)
      .then((value) => { if (disposed) value(); else unlisten = value; }).catch(() => {});
    return () => { disposed = true; unlisten?.(); };
  }, []);
  const invoke = (action: "minimize" | "toggleMaximize" | "close") => {
    const app = desktopWindow();
    if (!app) return;
    void app[action]().catch(() => {});
  };
  return <header className="window-titlebar"
    onPointerDown={(event) => { if (event.button === 0 && event.target instanceof HTMLElement && !event.target.closest("button")) void desktopWindow()?.startDragging().catch(() => {}); }}>
    <div className="window-titlebrand"><img src="/opencore-logo.png" alt="" /><span>OpenCore</span></div>
    <div className="window-controls">
      <button type="button" aria-label="Minimize OpenCore" title="Minimize" onClick={() => invoke("minimize")}><Minus size={14} strokeWidth={1.8} aria-hidden="true" /></button>
      <button type="button" className="window-maximize" aria-label={maximized ? "Restore OpenCore" : "Maximize OpenCore"} title={maximized ? "Restore" : "Maximize"} onClick={() => invoke("toggleMaximize")}>{maximized ? <Copy size={14} strokeWidth={1.8} aria-hidden="true" /> : <Square size={14} strokeWidth={1.8} aria-hidden="true" />}</button>
      <button type="button" className="window-close" aria-label="Close OpenCore" title="Close" onClick={() => invoke("close")}><X size={14} strokeWidth={1.8} aria-hidden="true" /></button>
    </div>
  </header>;
}
