import { getCurrentWindow } from "@tauri-apps/api/window";
import { Minus, X } from "lucide-react";

function desktopWindow() {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window ? getCurrentWindow() : null;
}

export function WindowTitleBar() {
  const invoke = (action: "minimize" | "close") => {
    const app = desktopWindow();
    if (!app) return;
    void app[action]().catch(() => {});
  };
  return <header className="window-titlebar" data-tauri-drag-region
    onPointerDown={(event) => { if (event.button === 0 && event.target instanceof HTMLElement && !event.target.closest("button")) void desktopWindow()?.startDragging().catch(() => {}); }}>
    <div className="window-titlebrand" data-tauri-drag-region><img src="/opencore-logo.png" alt="" /><span data-tauri-drag-region>OpenCore</span></div>
    <div className="window-controls">
      <button type="button" aria-label="Minimize OpenCore" title="Minimize" onClick={() => invoke("minimize")}><Minus size={14} /></button>
      <button type="button" className="window-close" aria-label="Close OpenCore" title="Close" onClick={() => invoke("close")}><X size={15} /></button>
    </div>
  </header>;
}
