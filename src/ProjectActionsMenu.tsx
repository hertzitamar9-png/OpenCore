import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ProjectSummary } from "./types";

type Props = {
  project: ProjectSummary;
  locked?: boolean;
  anchor: HTMLElement;
  onClose: () => void;
  onOpenFolder: () => Promise<string | void> | void;
  onChangeFolder: () => Promise<string | void> | void;
  onRename: () => void;
  onRemove: () => void;
};

export function ProjectActionsMenu({ project, locked = false, anchor, onClose, onOpenFolder, onChangeFolder, onRename, onRemove }: Props) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 8, top: 8 });
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState("");

  useLayoutEffect(() => {
    const update = () => {
      const rect = anchor.getBoundingClientRect();
      const width = menuRef.current?.offsetWidth || 236;
      const height = menuRef.current?.offsetHeight || 210;
      setPosition({
        left: Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)),
        top: Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - height - 8)),
      });
    };
    update();
    menuRef.current?.querySelector<HTMLButtonElement>('button[role="menuitem"]:not(:disabled)')?.focus();
    const resize = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    if (menuRef.current) resize?.observe(menuRef.current);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => { resize?.disconnect(); window.removeEventListener("resize", update); window.removeEventListener("scroll", update, true); };
  }, [anchor]);

  useLayoutEffect(() => {
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !anchor.contains(target)) onClose();
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [anchor, onClose]);

  const run = async (name: string, action: () => Promise<string | void> | void) => {
    setBusy(name); setResult("");
    try { setResult((await action()) || `${name} complete`); }
    catch (error) { setResult(String(error)); }
    finally { setBusy(""); }
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") { event.preventDefault(); onClose(); anchor.focus(); return; }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const actions = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('button[role="menuitem"]:not(:disabled)') || []);
    const at = actions.indexOf(document.activeElement as HTMLButtonElement);
    if (actions.length) actions[(at + (event.key === "ArrowDown" ? 1 : -1) + actions.length) % actions.length].focus();
  };

  return createPortal(<div ref={menuRef} className="project-actions-popover" role="menu" aria-label={`${project.name} project actions`} style={{ position: "fixed", ...position }} onKeyDown={handleKeyDown}>
    <div className="project-actions-heading"><strong>{project.name}</strong><small>{project.folderPath || "Needs folder"}</small>{locked ? <small>Linked to a Claude Code or Codex source</small> : !project.needsFolder && !project.folderAvailable ? <small>Folder unavailable — choose a new location</small> : null}</div>
    <button role="menuitem" disabled={!project.folderAvailable || Boolean(busy)} onClick={() => void run("Open folder", onOpenFolder)}>Open folder</button>
    <button role="menuitem" disabled={locked || Boolean(busy)} onClick={() => void run(project.needsFolder ? "Link folder" : "Change folder", onChangeFolder)}>{project.needsFolder ? "Link folder" : "Change folder"}</button>
    <button role="menuitem" disabled={locked || Boolean(busy)} onClick={() => { onClose(); onRename(); }}>Rename</button>
    <button className="danger-action" role="menuitem" disabled={locked || Boolean(busy)} onClick={() => { onClose(); onRemove(); }}>Remove from OpenCore</button>
    {busy ? <p role="status">{busy}…</p> : result ? <p role="status">{result}</p> : null}
  </div>, document.body);
}
