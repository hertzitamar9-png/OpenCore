import { useEffect, useRef, useState } from "react";
import type { CSSProperties, PointerEvent, ReactNode } from "react";
import { createPortal } from "react-dom";
import { Maximize2, Minimize2, X } from "lucide-react";
import { clampFloatingRect, moveFloatingRect, resizeFloatingRect } from "./floating-geometry";
import type { FloatingRect, ResizeEdge } from "./floating-geometry";

let topLayer = 60;
let topModalLayer = 1010;

type Props = {
  id: string;
  title: string;
  icon?: ReactNode;
  status?: ReactNode;
  children: ReactNode;
  onClose: () => void;
  initialWidth?: number;
  initialHeight?: number;
  minWidth?: number;
  minHeight?: number;
  className?: string;
  ariaLabel?: string;
  domId?: string;
  place?: "right" | "left" | "center" | "composer";
  modal?: boolean;
  startMaximized?: boolean;
  maximizable?: boolean;
};

function initialRect(id: string, width: number, height: number, place: Props["place"], minWidth: number, minHeight: number): FloatingRect {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let x = place === "center" ? (vw - width) / 2 : place === "left" ? 62 : vw - width - 20;
  let y = place === "center" ? (vh - height) / 2 : place === "left" ? 8 : 76;
  if (place === "composer") {
    const anchor = document.querySelector(id.startsWith("effort") ? ".effort-trigger" : ".approval-trigger")?.getBoundingClientRect();
    const chat = document.querySelector(".chat-composer-wrap")?.getBoundingClientRect();
    const composer = document.querySelector(".chat-composer")?.getBoundingClientRect();
    if (chat) width = Math.min(width, Math.max(280, chat.width - 16));
    minWidth = Math.min(minWidth, width);
    if (anchor) {
      const right = chat?.right ?? vw;
      const left = chat?.left ?? 0;
      x = Math.max(left + 8, Math.min(anchor.left, right - width - 12));
      y = (composer?.top ?? anchor.top) - height - 14;
    }
    return clampFloatingRect({ x, y, width, height }, vw, vh, minWidth, minHeight);
  }
  try {
    const saved = JSON.parse(window.localStorage.getItem(`opencore.surface.${id}`) || "null");
    if ([saved?.x, saved?.y, saved?.width, saved?.height].every((n) => typeof n === "number" && Number.isFinite(n))) {
      return clampFloatingRect(saved, vw, vh, minWidth, minHeight);
    }
  } catch { /* An invalid saved layout uses the default. */ }
  return clampFloatingRect({ x, y, width, height }, vw, vh, minWidth, minHeight);
}

function initialMaximized(id: string, fallback: boolean): boolean {
  try {
    const saved = window.localStorage.getItem(`opencore.surface.maximized.${id}`);
    return saved === null ? fallback : saved === "true";
  } catch { return fallback; }
}

function maximizedRect(minWidth: number, minHeight: number): FloatingRect {
  const top = document.querySelector(".window-titlebar")?.getBoundingClientRect().bottom ?? 0;
  return clampFloatingRect({ x: 8, y: top + 8, width: window.innerWidth - 16, height: window.innerHeight - top - 16 }, window.innerWidth, window.innerHeight, minWidth, minHeight);
}

export function FloatingWindow({ id, title, icon, status, children, onClose, initialWidth = 760, initialHeight = 600, minWidth = 340, minHeight = 220, className = "", ariaLabel, domId, place = "right", modal = false, startMaximized = false, maximizable = true }: Props) {
  const [maximized, setMaximized] = useState(() => initialMaximized(id, startMaximized));
  const [box, setBox] = useState(() => maximized
    ? maximizedRect(minWidth, minHeight)
    : initialRect(id, initialWidth, initialHeight, place, minWidth, minHeight));
  const layer = useRef(modal ? ++topModalLayer : ++topLayer);
  const panel = useRef<HTMLElement>(null);
  const restore = useRef<FloatingRect | null>(maximized ? initialRect(id, initialWidth, initialHeight, place, minWidth, minHeight) : null);
  const pointer = useRef<{ startX: number; startY: number; box: FloatingRect; edge?: ResizeEdge } | null>(null);

  useEffect(() => {
    try { if (!maximized) window.localStorage.setItem(`opencore.surface.${id}`, JSON.stringify(box)); } catch { /* Session-only layout. */ }
  }, [box, id, maximized]);
  useEffect(() => {
    try { window.localStorage.setItem(`opencore.surface.maximized.${id}`, String(maximized)); } catch { /* Session-only layout. */ }
  }, [id, maximized]);
  useEffect(() => {
    const clamp = () => setBox((current) => maximized ? maximizedRect(minWidth, minHeight) : clampFloatingRect(current, window.innerWidth, window.innerHeight, minWidth, minHeight));
    window.addEventListener("resize", clamp);
    return () => window.removeEventListener("resize", clamp);
  }, [minWidth, minHeight, maximized]);

  const raise = () => {
    layer.current = modal ? ++topModalLayer : ++topLayer;
    if (panel.current) panel.current.style.zIndex = String(layer.current);
  };
  const begin = (event: PointerEvent<HTMLElement>, edge?: ResizeEdge) => {
    if (event.button !== 0 || (event.target as HTMLElement).closest("button,input,select,textarea")) return;
    if (maximized) return;
    pointer.current = { startX: event.clientX, startY: event.clientY, box, edge };
    event.currentTarget.setPointerCapture(event.pointerId);
    raise();
    event.preventDefault();
  };
  const move = (event: PointerEvent<HTMLElement>) => {
    const active = pointer.current;
    if (!active) return;
    const dx = event.clientX - active.startX;
    const dy = event.clientY - active.startY;
    setBox(active.edge
      ? resizeFloatingRect(active.box, active.edge, dx, dy, window.innerWidth, window.innerHeight, minWidth, minHeight)
      : moveFloatingRect(active.box, dx, dy, window.innerWidth, window.innerHeight));
  };
  const end = (event: PointerEvent<HTMLElement>) => {
    pointer.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const lost = () => { pointer.current = null; };
  const toggleMaximize = () => {
    if (maximized && restore.current) { setBox(restore.current); setMaximized(false); }
    else { restore.current = box; setBox(maximizedRect(minWidth, minHeight)); setMaximized(true); }
    raise();
  };
  const style = { left: box.x, top: box.y, width: box.width, height: box.height, zIndex: layer.current } satisfies CSSProperties;
  return createPortal(<aside ref={panel} id={domId} className={`floating-window ${className}`} role={modal ? "dialog" : "region"} aria-modal={modal ? "true" : undefined} aria-label={ariaLabel || title} style={style} onPointerDownCapture={raise}>
    <header className="floating-window-title" onPointerDown={(event) => begin(event)} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={lost} onDoubleClick={maximizable ? toggleMaximize : undefined} tabIndex={0} aria-label={`Move ${title} panel`} onKeyDown={(event) => {
      if (!event.altKey || !event.key.startsWith("Arrow")) return;
      event.preventDefault();
      const dx = event.key === "ArrowRight" ? 16 : event.key === "ArrowLeft" ? -16 : 0;
      const dy = event.key === "ArrowDown" ? 16 : event.key === "ArrowUp" ? -16 : 0;
      setBox((current) => event.shiftKey ? resizeFloatingRect(current, "se", dx, dy, window.innerWidth, window.innerHeight, minWidth, minHeight) : moveFloatingRect(current, dx, dy, window.innerWidth, window.innerHeight));
    }}>
      <span className="floating-window-icon">{icon}</span><strong>{title}</strong><span className="floating-window-status">{status}</span>
      {maximizable ? <button type="button" aria-label={maximized ? `Restore ${title}` : `Maximize ${title}`} title={maximized ? "Restore" : "Maximize"} onClick={toggleMaximize}>{maximized ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button> : null}
      <button type="button" aria-label={`Close ${title}`} title="Close" onClick={onClose}><X size={16} /></button>
    </header>
    <div className="floating-window-body">{children}</div>
    {!maximized && (["n", "s", "e", "w", "ne", "nw", "se", "sw"] as ResizeEdge[]).map((edge) =>
      <div key={edge} className={`floating-resize floating-resize-${edge}`} role="separator" aria-label={`Resize ${title} ${edge}`} onPointerDown={(event) => begin(event, edge)} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={lost} />)}
  </aside>, document.body);
}
