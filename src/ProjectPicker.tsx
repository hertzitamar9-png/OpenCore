import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Folder, FolderPlus } from "lucide-react";
import * as api from "./api";
import type { ProjectSummary } from "./types";

type Props = {
  value: string | null;
  legacyName?: string;
  projects: ProjectSummary[];
  onChange: (projectId: string | null) => void;
  onCreate: (name: string, folderPath: string) => Promise<boolean>;
};

export function ProjectPicker({ value, legacyName, projects, onChange, onCreate }: Props) {
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [folderPath, setFolderPath] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const linked = projects.filter((project) => !project.needsFolder && project.folderAvailable && project.folderPath);
  const selected = projects.find((project) => project.id === value);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); setCreating(false); }
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeEscape);
    };
  }, [open]);

  const select = (next: string | null) => {
    if (next !== value) onChange(next);
    setOpen(false);
    setCreating(false);
  };

  const chooseFolder = async () => {
    setError("");
    try {
      const next = await api.chooseProjectFolder();
      if (next) setFolderPath(next);
    } catch (cause) { setError(String(cause)); }
  };

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    const next = name.trim();
    if (!next || !folderPath || busy) return;
    setBusy(true);
    setError("");
    try {
      if (await onCreate(next, folderPath)) {
        setName(""); setFolderPath(""); setCreating(false); setOpen(false);
      } else { setError("Could not create project. Check the app notification for details."); }
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  };

  const label = selected?.name || legacyName || "No project";
  return <div className="project-picker" ref={root}>
    <button className={`project-picker-trigger ${open ? "active" : ""}`} type="button" aria-label={`Project: ${label}`} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((current) => !current)}>
      <Folder size={16} /><span>{label}</span><ChevronDown size={14} />
    </button>
    {open ? <div className="project-picker-menu" role="menu" aria-label="Move conversation to project">
      <div className="project-picker-options">
        <button type="button" role="menuitemradio" aria-checked={!value} onClick={() => select(null)}><Folder size={16} /><span>No project</span>{!value ? <Check size={15} /> : null}</button>
        {linked.map((item) => <button type="button" role="menuitemradio" aria-checked={value === item.id} key={item.id} onClick={() => select(item.id)} title={item.folderPath || ""}><Folder size={16} /><span>{item.name}<small>{item.folderPath}</small></span>{value === item.id ? <Check size={15} /> : null}</button>)}
      </div>
      {creating ? <form className="project-picker-create" onSubmit={create}>
        <input autoFocus aria-label="New project name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Project name" maxLength={120} />
        <button type="button" onClick={() => void chooseFolder()}>Choose folder</button>
        {folderPath ? <small className="project-selected-folder" title={folderPath}>{folderPath}</small> : null}
        {error ? <small className="project-error" role="alert">{error}</small> : null}
        <button type="submit" disabled={!name.trim() || !folderPath || busy}>{busy ? "Creating…" : "Create project"}</button>
      </form> : <button type="button" className="project-picker-add" role="menuitem" onClick={() => setCreating(true)}><FolderPlus size={16} /> Create project</button>}
    </div> : null}
  </div>;
}
