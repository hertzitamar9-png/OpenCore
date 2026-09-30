import { useEffect, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Minus, Square, X } from "lucide-react";

function desktopWindow() {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window ? getCurrentWindow() : null;
}

export function WindowTitleBar() {
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    const app = desktopWindow();
    if (!app) return;
    void app.isMaximized().then(setMaximized).catch(() => {});
    let unlisten: (() => void) | undefined;
    void app.onResized(() => { void app.isMaximized().then(setMaximized).catch(() => {}); })
      .then((value) => { unlisten = value; }).catch(() => {});
    return () => unlisten?.();
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
      <button type="button" aria-label="Minimize OpenCore" title="Minimize" onClick={() => invoke("minimize")}><Minus size={14} /></button>
      <button type="button" className="window-maximize" aria-label={maximized ? "Restore OpenCore" : "Maximize OpenCore"} title={maximized ? "Restore" : "Maximize"} onClick={() => invoke("toggleMaximize")}><Square size={14} strokeWidth={2} /></button>
      <button type="button" className="window-close" aria-label="Close OpenCore" title="Close" onClick={() => invoke("close")}><X size={15} /></button>
    </div>
  </header>;
}
