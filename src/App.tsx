import { EchoContextStatus } from "./EchoContextStatus";
import { EchoMemorySettings } from "./EchoMemorySettings";
import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { listen } from "@tauri-apps/api/event";
import { getVersion } from "@tauri-apps/api/app";
import {
  Activity,
  Archive,
  Box,
  BrainCircuit,
  ChevronDown,
  CircleAlert,
  CircleStop,
  Copy,
  Database,
  Download,
  ExternalLink,
  FileDown,
  FolderOpen,
  Gauge,
  HardDrive,
  Home,
  LayoutDashboard,
  MemoryStick,
  MessageSquare,
  Music2,
  MoreHorizontal,
  Network,
  Play,
  Pin,
  PinOff,
  RefreshCw,
  RotateCw,
  Search,
  Settings,
  SlidersHorizontal,
  ShieldCheck,
  SquareTerminal,
  Trash2,
  Unplug,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import * as api from "./api";
import opencoreLogo from "./assets/opencore-logo.png";
import { AssistantConversation, type ComposerDraft } from "./AssistantConversation";
import { WindowTitleBar } from "./WindowTitleBar";
import { ProjectActionsMenu } from "./ProjectActionsMenu";
import { FloatingWindow } from "./FloatingWindow";
import { ModelProfileOptions, profileDescription, profileLabel, selectableModelProfiles } from "./ModelProfiles";
import { ModelLibrary } from "./ModelLibrary";
import { MusicStudio } from './MusicStudio';
import { AssetsStudio } from './AssetsStudio';
import type { AppSnapshot, ArchiveEvent, ArchivePageRef, ConversationSummary, LogEntry, OperationRecord, ProjectSummary, RuntimeProfile, TimelineEntry } from "./types";

type View = "overview" | "conversations" | "context" | "memory" | "runtime" | "models" | "music" | "assets" | "connectors" | "settings" | "troubleshooting";
type ConversationDialog = { kind: "rename"; value: string } | { kind: "delete" } | null;
type ProjectDialog = { kind: "rename"; project: ProjectSummary; value: string } | { kind: "delete"; project: ProjectSummary } | null;
type Appearance = {
  chatFontSize: number; terminalFontSize: number; compactMessages: boolean; keepUserWindowInFront: boolean;
  defaultComputerUse: boolean; defaultBrowserUse: boolean; defaultChromeControl: boolean;
  subagentsEnabled: boolean; maxSubagents: number;
  projectSkillsEnabled: boolean; compactAtTokens: number;
};
const appearanceKey = "opencore.appearance.v2";
const defaultAppearance: Appearance = { chatFontSize: 15, terminalFontSize: 13, compactMessages: false, keepUserWindowInFront: false, defaultComputerUse: false, defaultBrowserUse: false, defaultChromeControl: false, subagentsEnabled: true, maxSubagents: 3, projectSkillsEnabled: true, compactAtTokens: 200000 };

function effectiveCompactAtTokens(requestedTokens: number, configuredWindowTokens: number): number {
  const contextWindowTokens = Math.max(8_192, Math.floor(configuredWindowTokens) || 32_768);
  const headroomTokens = Math.min(
    Math.floor(contextWindowTokens * 0.3),
    Math.max(10_240, Math.floor(contextWindowTokens * 0.2)),
  );
  return Math.min(
    Math.max(1_024, Math.floor(requestedTokens) || defaultAppearance.compactAtTokens),
    Math.max(1_024, contextWindowTokens - headroomTokens),
    Math.min(contextWindowTokens, 1_000_000),
  );
}

async function revealLocalPath(path: string, onNotice: (message: string) => void) {
  try { await api.openLocalPath(path); }
  catch (error) { onNotice(`Could not open folder: ${String(error)}`); }
}

function savedAppearance(): Appearance {
  try {
    const stored = JSON.parse(window.localStorage.getItem(appearanceKey) || "null") as Partial<Appearance> | null;
    return {
      chatFontSize: Math.max(13, Math.min(18, Number(stored?.chatFontSize) || defaultAppearance.chatFontSize)),
      terminalFontSize: Math.max(10, Math.min(20, Number(stored?.terminalFontSize) || defaultAppearance.terminalFontSize)),
      compactMessages: stored?.compactMessages === true,
      keepUserWindowInFront: stored?.keepUserWindowInFront === true,
      defaultComputerUse: stored?.defaultComputerUse === true,
      defaultBrowserUse: stored?.defaultBrowserUse === true,
      defaultChromeControl: stored?.defaultChromeControl === true,
      subagentsEnabled: stored?.subagentsEnabled !== false,
      maxSubagents: Math.max(1, Math.min(1000, Number(stored?.maxSubagents) || defaultAppearance.maxSubagents)),
      projectSkillsEnabled: stored?.projectSkillsEnabled !== false,
      compactAtTokens: Math.max(1024, Math.min(1000000, Number(stored?.compactAtTokens) || defaultAppearance.compactAtTokens)),
    };
  } catch { return defaultAppearance; }
}

const nav: Array<{ id: View; label: string; icon: typeof Home; group?: boolean }> = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "conversations", label: "Conversations", icon: MessageSquare },
  { id: "context", label: "Live Context", icon: BrainCircuit },
  { id: "memory", label: "Memory", icon: Database },
  { id: "runtime", label: "Runtime & Logs", icon: SquareTerminal, group: true },
  { id: "models", label: "Models", icon: Box },
  { id: "music", label: "Music Studio", icon: Music2 },
  { id: "assets", label: "Assets Studio", icon: Box },
  { id: "connectors", label: "Connectors", icon: Network },
  { id: "settings", label: "Settings", icon: Settings },
  { id: "troubleshooting", label: "Troubleshooting", icon: CircleAlert, group: true },
];

const shortTime = (value?: string | null) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

const shortDate = (value?: string | null) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
};

function recentDecoderSpeed(logs: LogEntry[]): number | null {
  const cutoff = Date.now() - 8000;
  for (let index = logs.length - 1; index >= 0; index--) {
    const entry = logs[index];
    if (entry.source !== "runtime" || new Date(entry.timestamp).getTime() < cutoff) continue;
    const match = entry.message.match(/tg_3s\s*=\s*(\d+(?:\.\d+)?)/);
    if (match) return Number(match[1]);
  }
  return null;
}

export function recentPromptProgress(logs: LogEntry[]): { label: string; speed: number | null } | null {
  const matches = logs.filter((entry) => entry.source === "runtime")
    .map((entry) => ({ entry, match: entry.message.match(/prompt processing,\s*n_tokens\s*=\s*(\d+),\s*progress\s*=\s*([\d.]+)/) }))
    .filter((item): item is { entry: LogEntry; match: RegExpMatchArray } => Boolean(item.match));
  const latest = matches.at(-1);
  if (!latest || Date.now() - new Date(latest.entry.timestamp).getTime() > 20000) return null;
  const previous = matches.at(-2);
  const seconds = previous ? (new Date(latest.entry.timestamp).getTime() - new Date(previous.entry.timestamp).getTime()) / 1000 : 0;
  const speed = previous && seconds > 0 ? (Number(latest.match[1]) - Number(previous.match[1])) / seconds : null;
  return { label: `Reading prompt · ${Number(latest.match[1]).toLocaleString()} tokens · ${Math.round(Number(latest.match[2]) * 100)}%`, speed: speed && speed > 0 ? speed : null };
}

function modelLoaderDetail(snapshot: AppSnapshot): { text: string; loaded: number | null; total: number | null } {
  const since = snapshot.runtime.startedAt ? new Date(snapshot.runtime.startedAt).getTime() : 0;
  const logs = snapshot.logs.filter((entry) => ["runtime", "echo"].includes(entry.source) && new Date(entry.timestamp).getTime() >= since);
  const latest = logs.at(-1);
  const text = latest?.message.trim() || snapshot.runtime.loadingPhase || "Waiting for loader output";
  const fraction = text.match(/(?:tensor|tensors).*?(\d+)\s*\/\s*(\d+)/i) || text.match(/(\d+)\s*\/\s*(\d+).*?(?:tensor|tensors)/i);
  return { text, loaded: fraction ? Number(fraction[1]) : null, total: fraction ? Number(fraction[2]) : null };
}

const readProfilePreference = (): RuntimeProfile => {
  try {
    const stored = window.localStorage.getItem("opencore.model-profile");
    if (stored === "unsloth-echo" || selectableModelProfiles.some((profile) => profile.id === stored)) return stored as RuntimeProfile;
  } catch { /* Use the first-run profile when storage is unavailable. */ }
  return "doucode";
};

function StatusDot({ state }: { state: string }) {
  const kind = ["running", "ready", "detected", "configured", "observed", "stop", "active"].includes(state) ? "good" : state === "error" ? "bad" : state === "starting" ? "warn" : "muted";
  return <span className={`status-dot ${kind}`} aria-label={state} />;
}

function Navigation({ active, onChange, running, compact = false }: { active: View; onChange: (view: View) => void; running: boolean; compact?: boolean }) {
  const [appVersion, setAppVersion] = useState<string | null>(null);
  useEffect(() => {
    let mounted = true;
    getVersion().then(version => { if (mounted) setAppVersion(version); }).catch(() => {});
    return () => { mounted = false; };
  }, []);
  return <aside className={`nav-rail ${compact ? "conversation-app-rail" : ""}`}>
    <button className="brand-mini" onClick={() => onChange("overview")} title="OpenCore overview"><span className="brand-mark"><img src="/opencore-logo.png" alt="OpenCore" /></span><span>OpenCore</span></button>
    <nav>
      {nav.map((item) => <div key={item.id} className={item.group ? "nav-group-start" : ""}>
        <button title={item.label} aria-label={item.label} className={`nav-item ${active === item.id ? "active" : ""}`} onClick={() => onChange(item.id)}>
          <item.icon size={17} /><span>{item.label}</span>
        </button>
      </div>)}
    </nav>
    <div className="nav-footer">
      <div><StatusDot state={running ? "running" : "stopped"} />{running ? "Runtime active" : "Runtime stopped"}</div>
      <small>{appVersion ? `OpenCore v${appVersion}` : "OpenCore"}</small>
    </div>
  </aside>;
}

function Header({ snapshot, busy, runtimeAction, selectedProfile, setSelectedProfile, onStart, onStop, onRestart, onExport }: {
  snapshot: AppSnapshot; busy: boolean; runtimeAction: "starting" | "stopping" | null; selectedProfile: RuntimeProfile; setSelectedProfile: (profile: RuntimeProfile) => void;
  onStart: () => void; onStop: () => void; onRestart: () => void; onExport: () => void;
}) {
  const running = snapshot.runtime.status === "running";
  const active = running || snapshot.runtime.status === "starting" || runtimeAction !== null;
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const profilePickerRef = useRef<HTMLDivElement>(null);
  const profileTriggerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!profileMenuOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (!profilePickerRef.current?.contains(event.target as Node)) setProfileMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setProfileMenuOpen(false); profileTriggerRef.current?.focus(); }
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => { document.removeEventListener("pointerdown", closeOutside); document.removeEventListener("keydown", closeOnEscape); };
  }, [profileMenuOpen]);
  const chooseProfile = (profile: RuntimeProfile) => {
    setSelectedProfile(profile);
    setProfileMenuOpen(false);
    profileTriggerRef.current?.focus();
  };
  const profileLocked = active || busy;
  return <header className="topbar">
    <div className="brand"><span className="brand-mark"><img src="/opencore-logo.png" alt="OpenCore" /></span><div><strong>OpenCore</strong><small>Observe · Understand · Trust</small></div></div>
    <div className="runtime-actions">
      <button className={active ? "runtime-stop-button" : "primary"} disabled={runtimeAction === "stopping"} onClick={active ? onStop : onStart}>{active ? <CircleStop size={15} /> : <Play size={15} />}{runtimeAction === "stopping" ? "Stopping…" : active ? "Stop" : "Start"}</button>
      <button disabled={busy || runtimeAction !== null || !running} onClick={onRestart}><RotateCw size={15} /> Restart</button>
      <div className="model-picker" ref={profilePickerRef}>
        <span className="model-picker-label">Model</span>
        <button ref={profileTriggerRef} className={`model-picker-trigger ${profileMenuOpen ? "open" : ""}`} type="button" aria-label={`Choose model profile, currently ${profileLabel(selectedProfile)}`} aria-expanded={profileMenuOpen} aria-controls="model-profile-options" disabled={profileLocked} onClick={() => setProfileMenuOpen((open) => !open)}>
          <span className="model-picker-copy"><strong>{profileLabel(selectedProfile)}</strong><small>{profileDescription(selectedProfile)}</small></span>
          <ChevronDown size={15} aria-hidden="true" />
        </button>
        {profileMenuOpen && !profileLocked ? <ModelProfileOptions selectedProfile={selectedProfile} onSelect={chooseProfile} id="model-profile-options" /> : null}
      </div>
    </div>
    <div className="topbar-right">
      <button onClick={onExport} title="Export conversation" aria-label="Export conversation"><FileDown size={15} /> Export</button>
      <div className="connection-state"><StatusDot state={running ? "running" : snapshot.runtime.status} /><div><strong>{running ? "Connected" : snapshot.runtime.status}</strong><small>{profileLabel(running ? snapshot.runtime.profile : selectedProfile)}</small></div></div>
    </div>
  </header>;
}

const ConversationRows = memo(function ConversationRows({ items, selected, onSelect, onTogglePin }: { items: ConversationSummary[]; selected?: string; onSelect: (id: string) => void; onTogglePin: (item: ConversationSummary) => void }) {
  return <>{items.map((item) => <div key={item.id} className={`conversation-row-shell ${selected === item.id ? "selected" : ""}`}><button className={`conversation-row ${selected === item.id ? "selected" : ""}`} onClick={() => onSelect(item.id)}>
    <span className={`client-dot client-${item.client.toLowerCase().replaceAll(" ", "-")}`} />
    <span className="conversation-copy"><strong>{item.title || "Untitled conversation"}</strong><small>{item.client}{item.project && item.project !== item.client ? ` · ${item.project}` : ""}</small></span>
    <time>{shortTime(item.updatedAt)}</time>
  </button><button className={`conversation-row-pin ${item.pinned ? "is-pinned" : ""}`} onClick={() => onTogglePin(item)} title={item.pinned ? "Unpin chat" : "Pin chat"} aria-label={`${item.pinned ? "Unpin" : "Pin"} ${item.title}`}>
    {item.pinned ? <PinOff size={14} /> : <Pin size={14} />}
  </button></div>)}</>;
});

function CollapsibleConversationGroup({ id, label, count, collapsed, onToggle, children }: { id: string; label: string; count: number; collapsed: boolean; onToggle: (id: string) => void; children: React.ReactNode }) {
  return <section className={`conversation-group ${collapsed ? "collapsed" : ""}`}>
    <button className="conversation-group-heading" onClick={() => onToggle(id)} aria-label={`${label} group, ${count}`} aria-expanded={!collapsed}><ChevronDown size={13} /><strong>{label}</strong><span>{count}</span></button>
    {!collapsed ? <div className="conversation-group-body">{children}</div> : null}
  </section>;
}

function ProjectConversationGroup({ project, items, selected, collapsed, onToggle, onSelect, onTogglePin, onEdit, onRemove, onOpenFolder, onChangeFolder }: {
  project: ProjectSummary | undefined; items: ConversationSummary[]; selected?: string; collapsed: boolean; onToggle: () => void;
  onSelect: (id: string) => void; onTogglePin: (item: ConversationSummary) => void;
  onEdit: (project: ProjectSummary) => void; onRemove: (project: ProjectSummary) => void;
  onOpenFolder: (project: ProjectSummary) => Promise<string>; onChangeFolder: (project: ProjectSummary) => Promise<string>;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const sourceLocked = items.some((item) => /claude|codex/i.test(item.client));
  return <section className={`conversation-group project-group ${collapsed ? "collapsed" : ""}`}>
    <div className="project-group-toolbar">
      <button className="conversation-group-heading" onClick={onToggle} aria-label={`${project?.name || items[0]?.project} group, ${items.length}`} aria-expanded={!collapsed}><ChevronDown size={14} /><strong>{project?.name || items[0]?.project}</strong>{project?.needsFolder ? <em>Needs folder</em> : project && !project.folderAvailable ? <em>Folder unavailable</em> : null}<span>{items.length}</span></button>
      {project ? <div className="project-group-actions"><button ref={trigger} className="project-more" aria-label={`Options for ${project.name}`} aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((current) => !current)}><MoreHorizontal size={17} /></button>
        {menuOpen && trigger.current ? <ProjectActionsMenu project={project} locked={sourceLocked} anchor={trigger.current} onClose={() => setMenuOpen(false)} onOpenFolder={() => onOpenFolder(project)} onChangeFolder={() => onChangeFolder(project)} onRename={() => onEdit(project)} onRemove={() => onRemove(project)} /> : null}
      </div> : null}
    </div>
    {!collapsed ? <div className="conversation-group-body"><ConversationRows items={items} selected={selected} onSelect={onSelect} onTogglePin={onTogglePin} /></div> : null}
  </section>;
}

function ConversationsList({ conversations, projects: projectDefinitions, selected, onSelect, onNew, onExit, onCreateProject, onTogglePin, onEditProject, onRemoveProject, onOpenProjectFolder, onChangeProjectFolder }: {
  conversations: ConversationSummary[]; projects: ProjectSummary[]; selected?: string; onSelect: (id: string) => void; onNew: () => void; onExit: () => void; onCreateProject: (name: string, folderPath: string) => Promise<boolean>;
  onTogglePin: (item: ConversationSummary) => void; onEditProject: (project: ProjectSummary) => void; onRemoveProject: (project: ProjectSummary) => void;
  onOpenProjectFolder: (project: ProjectSummary) => Promise<string>; onChangeProjectFolder: (project: ProjectSummary) => Promise<string>;
}) {
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [section, setSection] = useState<"all" | "recent" | "opencore" | "claude" | "codex" | "projects">("all");
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(["all-projects"]));
  const [creatingProject, setCreatingProject] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [projectFolder, setProjectFolder] = useState("");
  const [projectError, setProjectError] = useState("");
  const sections = [["all", "All"], ["recent", "Recent"], ["opencore", "OpenCore"], ["claude", "Claude Code"], ["codex", "Codex"], ["projects", "Projects"]] as const;
  const needle = deferredQuery.trim().toLowerCase();
  const searched = useMemo(() => conversations.filter((item) => !needle || `${item.title} ${item.client} ${item.project || ""}`.toLowerCase().includes(needle)), [conversations, needle]);
  const sourceItems = useMemo(() => {
    const isOpenCore = (item: ConversationSummary) => {
      const client = item.client.toLowerCase();
      return client.includes("opencore") || client.includes("unsloth") || item.id.startsWith("opencore:") || item.id.startsWith("local:");
    };
    return {
      pinned: searched.filter((item) => item.pinned),
      recent: searched.slice(0, 20),
      opencore: searched.filter(isOpenCore),
      claude: searched.filter((item) => item.client.toLowerCase().includes("claude")),
      codex: searched.filter((item) => item.client.toLowerCase().includes("codex")),
    };
  }, [searched]);
  const projects = useMemo(() => {
    const defined = projectDefinitions.map((definition) => ({ key: definition.id, name: definition.name, definition, items: searched.filter((item) => item.projectId === definition.id) }));
    const orphanNames = new Set(searched.filter((item) => item.project?.trim() && !projectDefinitions.some((project) => project.id === item.projectId)).map((item) => item.project));
    const orphans = Array.from(orphanNames).map((name) => ({ key: `orphan-${name}`, name, definition: undefined as ProjectSummary | undefined, items: searched.filter((item) => item.project === name && !projectDefinitions.some((project) => project.id === item.projectId)) }));
    return [...defined, ...orphans].sort((a, b) => a.name.localeCompare(b.name));
  }, [projectDefinitions, searched]);
  const toggle = useCallback((id: string) => setCollapsed((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  }), []);
  const projectGroups = projects.map((group) => <ProjectConversationGroup key={group.key} project={group.definition} items={group.items} selected={selected} collapsed={collapsed.has(`project-${group.key}`)} onToggle={() => toggle(`project-${group.key}`)} onSelect={onSelect} onTogglePin={onTogglePin} onEdit={onEditProject} onRemove={onRemoveProject} onOpenFolder={onOpenProjectFolder} onChangeFolder={onChangeProjectFolder} />);

  const chooseFolder = async () => {
    setProjectError("");
    try { const path = await api.chooseProjectFolder(); if (path) setProjectFolder(path); }
    catch (error) { setProjectError(String(error)); }
  };

  return <section className="conversation-list conversation-list-focus">
    <div className="conversation-list-head">
      <button className="conversation-home" onClick={onExit} title="Overview"><span className="brand-mark"><img src="/opencore-logo.png" alt="OpenCore" /></span></button>
      <div className="conversation-list-title"><h2>Conversations</h2><span>{conversations.length}</span></div><div className="conversation-list-actions"><button className="new-chat-button secondary" onClick={() => setCreatingProject(true)}>+ Project</button><button className="new-chat-button" onClick={onNew}>+ New</button></div>
    </div>
    {creatingProject ? <form className="project-create" onSubmit={async (event) => { event.preventDefault(); const name = projectName.trim(); if (!name || !projectFolder) return; if (!(await onCreateProject(name, projectFolder))) { setProjectError("Could not create project. Check the app notification for details."); return; } setProjectName(""); setProjectFolder(""); setProjectError(""); setCreatingProject(false); setSection("projects"); }}><input autoFocus aria-label="Project name" value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="Project name" maxLength={120} /><button type="button" onClick={() => void chooseFolder()}>Choose folder</button>{projectFolder ? <small className="project-selected-folder" title={projectFolder}>{projectFolder}</small> : null}{projectError ? <small className="project-error" role="alert">{projectError}</small> : null}<button type="submit" disabled={!projectName.trim() || !projectFolder}>Create project</button><button type="button" onClick={() => { setCreatingProject(false); setProjectError(""); }}>Cancel</button></form> : null}
    <div className="search conversation-search"><Search size={14} /><input aria-label="Search conversations" placeholder="Search…" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
    <div className="conversation-sections">{sections.map(([id, label]) => <button key={id} className={section === id ? "active" : ""} onClick={() => setSection(id)}>{label}</button>)}</div>
    <div className="conversation-scroll">
      {searched.length === 0 ? <div className="empty-state"><MessageSquare /><strong>No conversations here</strong><span>Start a new OpenCore chat or choose another section.</span></div> : null}
      {section === "all" ? <>
        {sourceItems.pinned.length ? <CollapsibleConversationGroup id="all-pinned" label="Pinned" count={sourceItems.pinned.length} collapsed={collapsed.has("all-pinned")} onToggle={toggle}><ConversationRows items={sourceItems.pinned} selected={selected} onSelect={onSelect} onTogglePin={onTogglePin} /></CollapsibleConversationGroup> : null}
        <CollapsibleConversationGroup id="all-recent" label="Recent" count={sourceItems.recent.length} collapsed={collapsed.has("all-recent")} onToggle={toggle}><ConversationRows items={sourceItems.recent} selected={selected} onSelect={onSelect} onTogglePin={onTogglePin} /></CollapsibleConversationGroup>
        <CollapsibleConversationGroup id="all-opencore" label="OpenCore" count={sourceItems.opencore.length} collapsed={collapsed.has("all-opencore")} onToggle={toggle}><ConversationRows items={sourceItems.opencore} selected={selected} onSelect={onSelect} onTogglePin={onTogglePin} /></CollapsibleConversationGroup>
        <CollapsibleConversationGroup id="all-claude" label="Claude Code" count={sourceItems.claude.length} collapsed={collapsed.has("all-claude")} onToggle={toggle}><ConversationRows items={sourceItems.claude} selected={selected} onSelect={onSelect} onTogglePin={onTogglePin} /></CollapsibleConversationGroup>
        <CollapsibleConversationGroup id="all-codex" label="Codex" count={sourceItems.codex.length} collapsed={collapsed.has("all-codex")} onToggle={toggle}><ConversationRows items={sourceItems.codex} selected={selected} onSelect={onSelect} onTogglePin={onTogglePin} /></CollapsibleConversationGroup>
        <CollapsibleConversationGroup id="all-projects" label="Projects" count={projects.length} collapsed={collapsed.has("all-projects")} onToggle={toggle}>{projectGroups}</CollapsibleConversationGroup>
      </> : section === "projects" ? projectGroups : <ConversationRows items={sourceItems[section]} selected={selected} onSelect={onSelect} onTogglePin={onTogglePin} />}
    </div>
  </section>;
}

function Meter({ label, value, max, suffix = "" }: { label: string; value: number; max: number; suffix?: string }) {
  const percent = max ? Math.min(100, Math.round(value / max * 100)) : 0;
  return <div className="meter"><div><span>{label}</span><strong>{value.toLocaleString()}{suffix} / {max.toLocaleString()}{suffix}</strong><em>{percent}%</em></div><div className="meter-track"><span style={{ width: `${percent}%` }} /></div></div>;
}

const Telemetry = memo(function Telemetry({ snapshot }: { snapshot: AppSnapshot }) {
  const { runtime, telemetry } = snapshot;
  return <aside className="telemetry panel-edge">
    <div className="section-title"><h2>Telemetry</h2><span className="live"><StatusDot state={runtime.status} />Live</span></div>
    <div className="fact-table">
      <div><span>Mode</span><strong>{profileLabel(runtime.profile)}</strong></div>
      <div><span>Context</span><strong>{runtime.contextSize.toLocaleString()} {runtime.profile === "echo" ? "live scratchpad" : "tokens"}</strong></div>
      <div><span>Model</span><strong>{runtime.status}</strong></div>
    </div>
    <div className="metric-pair"><div><span>VRAM</span><strong>{(telemetry.vramUsedMib / 1024).toFixed(1)} / {(telemetry.vramTotalMib / 1024).toFixed(0)} GB</strong><small>Live hardware sample</small></div><div><span>Tokens/s</span><strong>{telemetry.tokensPerSecond.toFixed(1)}</strong><small>Last model response</small></div></div>
    <Meter label="Context usage" value={telemetry.promptTokens} max={runtime.contextSize} />
    <div className="telemetry-box"><h3>Active experts <span>{telemetry.activeExperts.length ? `${telemetry.activeExperts.length} / 10,000` : "No route data"}</span></h3>{(telemetry.activeExperts.length ? telemetry.activeExperts : ["Awaiting model telemetry"]).map((expert) => <div className="expert" key={expert}><StatusDot state={telemetry.activeExperts.length ? "active" : "stopped"} /><span>{expert}</span><em>{telemetry.activeExperts.length ? "Active" : "—"}</em></div>)}</div>
    <div className="telemetry-box"><h3>Client route status</h3>{snapshot.connectors.map((connector) => { const connected = connector.observable || connector.status === "configured" || connector.status === "observed"; return <div className="route" key={connector.id}><StatusDot state={connected ? "active" : connector.status} /><span>{connector.name}</span><b className={connected ? "ok" : "warn"}>{connector.observable ? "Observable" : connector.status === "configured" ? "Connected" : connector.status === "detected" ? "Ready" : "Offline"}</b></div>; })}</div>
    <div className="telemetry-box"><h3>Event timeline</h3>{snapshot.logs.slice(-5).reverse().map((log) => <div className="event" key={log.id}><time>{shortTime(log.timestamp)}</time><b>{log.source}</b><span>{log.message}</span></div>)}</div>
  </aside>;
});

function RuntimeTable({ snapshot, onRestart }: { snapshot: AppSnapshot; onRestart: () => void }) {
  const runtime = snapshot.runtime;
  const rows = [
    { name: "Control Gateway", detail: "Captures routing and events", status: "running", port: runtime.gatewayPort, pid: "this app", observable: true, restartable: false },
    { name: "llama-server", detail: profileLabel(runtime.profile), status: runtime.modelPid ? runtime.status : "stopped", port: runtime.backendPort, pid: runtime.modelPid || "—", observable: true, restartable: Boolean(runtime.modelPid) },
    { name: "ECHO proxy", detail: "Memory control and retrieval", status: runtime.echoPid ? runtime.status : "stopped", port: runtime.echoPort, pid: runtime.echoPid || "—", observable: runtime.profile.includes("echo") || runtime.profile === "doucode", restartable: Boolean(runtime.echoPid) },
    ...snapshot.connectors.map((item) => ({ name: item.name, detail: item.details, status: item.status, port: item.kind === "history" ? "local" : item.endpoint.split(":").pop() || "—", pid: "—", observable: item.observable, restartable: false })),
  ];
  return <div className="runtime-table">
    <div className="runtime-row header"><span>Component</span><span>Status</span><span>Port</span><span>PID</span><span>Observability</span><span>Actions</span></div>
    {rows.map((row) => <div className="runtime-row" key={row.name}><span><strong>{row.name}</strong><small>{row.detail}</small></span><span><StatusDot state={row.status} />{row.status}</span><span>{row.port}</span><span>{row.pid}</span><span className={row.observable ? "good-text" : "warn-text"}>{row.observable ? "Observable" : "Bypassing / offline"}</span><span>{row.restartable ? <button className="icon-button" onClick={onRestart} title="Restart OpenCore runtime"><RefreshCw size={14} /></button> : <em>—</em>}</span></div>)}
  </div>;
}

function RuntimeLogs({ logs }: { logs: LogEntry[] }) {
  const [filter, setFilter] = useState("all");
  const [autoScroll, setAutoScroll] = useState(true);
  const [clearing, setClearing] = useState(false);
  const canvasRef = useRef<HTMLDivElement>(null);
  const options = ["all", "runtime", "echo", "gateway", "client", "error"];
  const visible = logs.filter((log) => filter === "all" || log.source === filter || (filter === "error" && log.level === "error"));

  useEffect(() => {
    if (autoScroll && canvasRef.current) canvasRef.current.scrollTop = canvasRef.current.scrollHeight;
  }, [autoScroll, visible.length]);

  const clear = async () => {
    setClearing(true);
    try { await api.clearLogs(); } finally { setClearing(false); }
  };

  return <section className="logs-panel">
    <div className="logs-toolbar">
      <h3>Runtime Logs</h3>
      <div>{options.map((option) => <button key={option} className={filter === option ? "active" : ""} onClick={() => setFilter(option)}>{option[0].toUpperCase() + option.slice(1)}</button>)}</div>
      <button onClick={clear} disabled={clearing}>{clearing ? "Clearing…" : "Clear"}</button>
      <label><input type="checkbox" checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)} /> Auto-scroll</label>
    </div>
    <div ref={canvasRef} className="log-canvas">{visible.length === 0 && <span className="log-empty">No matching logs yet.</span>}{visible.map((log) => <div className={`log-line level-${log.level}`} key={log.id}><time>{new Date(log.timestamp).toLocaleString()}</time><b>{log.level.toUpperCase()}</b><em>{log.source}</em><span>{log.message}</span></div>)}</div>
  </section>;
}

function RuntimeView({ snapshot, selectedProfile, setSelectedProfile, runtimeAction, actions }: { snapshot: AppSnapshot; selectedProfile: RuntimeProfile; setSelectedProfile: (p: RuntimeProfile) => void; runtimeAction: "starting" | "stopping" | null; actions: { start: () => void; stop: () => void; restart: () => void; navigate: (view: View) => void; notice: (message: string) => void } }) {
  const runtime = snapshot.runtime;
  const active = runtime.status === "running" || runtime.status === "starting" || runtimeAction !== null;
  const modelDir = selectedProfile === "doucode"
    ? runtime.modelPath
    : runtime.modelPath.slice(0, Math.max(runtime.modelPath.lastIndexOf("\\"), runtime.modelPath.lastIndexOf("/")));
  const exportDiagnostics = async () => {
    try { actions.notice(`Diagnostics exported to ${await api.exportDiagnostics()}`); }
    catch (error) { actions.notice(String(error)); }
  };
  return <div className="workspace runtime-workspace">
    <section className="runtime-main">
      <div className="page-heading"><div><h1>Runtime & Logs</h1><p>Monitor and control OpenCore processes, routes and model runtime.</p></div><div className="profile-switch"><span>Model profile · one runtime at a time</span>{selectableModelProfiles.map((model) => <button key={model.id} className={selectedProfile === model.id ? "active" : ""} onClick={() => setSelectedProfile(model.id)} disabled={active}><b>{model.label}</b><small>{model.description}</small></button>)}</div></div>
      <section className="topology section-frame"><div className="frame-title"><h2>Runtime Topology</h2><span><StatusDot state={runtime.status} />{profileLabel(runtime.profile)} · {runtime.status}</span><div><button className={active ? "runtime-stop-button" : "primary"} onClick={active ? actions.stop : actions.start} disabled={runtimeAction === "stopping"}>{active ? <CircleStop size={14} /> : <Play size={14} />}{runtimeAction === "stopping" ? "Stopping…" : active ? "Stop" : "Start"}</button><button onClick={actions.restart} disabled={runtime.status !== "running" || runtimeAction !== null}><RefreshCw size={14} /> Restart all</button></div></div><RuntimeTable snapshot={snapshot} onRestart={actions.restart} /></section>
      <RuntimeLogs logs={snapshot.logs} />
    </section>
    <aside className="runtime-inspector">
      <InspectorSection title="Model & Download"><div className="model-line"><div><span>{profileLabel(selectedProfile)}</span><strong>{selectedProfile === "doucode" ? "K2 + Nanbeige candidate selection" : "OpenCore-Code-Single-File.gguf"}</strong><small>{runtime.modelPath}</small></div><StatusDot state={runtime.profile === selectedProfile ? runtime.status : "stopped"} /></div><KeyValue label="Runtime status" value={runtime.profile === selectedProfile ? runtime.status : "Selected · model unloaded"} />{selectedProfile === "doucode" ? <><KeyValue label="Backbones" value="K2 + Nanbeige generate and score answers together" /><p className="appearance-note">Selecting DuoCore does not load either model. Press Start to launch both backbones and the ECHO archive.</p></> : null}<button className="wide" onClick={() => void revealLocalPath(modelDir, actions.notice)}><FolderOpen size={14} /> Open model folder</button></InspectorSection>
      <InspectorSection title="Resource Usage"><ResourceRow label="GPU VRAM" value={`${(snapshot.telemetry.vramUsedMib / 1024).toFixed(1)} / ${(snapshot.telemetry.vramTotalMib / 1024).toFixed(0)} GB`} /><ResourceRow label="System RAM" value={`${(snapshot.telemetry.systemMemoryUsedMib / 1024).toFixed(1)} / ${(snapshot.telemetry.systemMemoryTotalMib / 1024).toFixed(0)} GB`} /><ResourceRow label="Disk free" value={`${snapshot.telemetry.diskFreeGib.toFixed(1)} GiB`} /></InspectorSection>
      <InspectorSection title="Endpoints"><Endpoint label="Gateway · use this" value={`http://127.0.0.1:${runtime.gatewayPort}/v1`} /><Endpoint label="Direct · bypasses capture" value={`http://127.0.0.1:${runtime.backendPort}/v1`} /><Endpoint label="ECHO internal" value={`http://127.0.0.1:${runtime.echoPort}/v1`} /></InspectorSection>
      <InspectorSection title="Process Supervision"><KeyValue label="Status" value={runtime.status} /><KeyValue label="Recovery" value="Manual restart available" /><KeyValue label="No console windows" value="Enabled" /><KeyValue label="Last error" value={runtime.error || "None"} /></InspectorSection>
      <InspectorSection title="Tools"><button className="wide" onClick={exportDiagnostics}><FileDown size={14} /> Export diagnostics</button><button className="wide" onClick={() => void revealLocalPath(runtime.archivePath, actions.notice)}><Archive size={14} /> Open archive</button><button className="wide" onClick={() => actions.navigate("connectors")}><Network size={14} /> Manage connectors</button></InspectorSection>
    </aside>
  </div>;
}

function InspectorSection({ title, children }: { title: string; children: React.ReactNode }) { return <section className="inspector-section"><h3>{title}</h3>{children}</section>; }
function ResourceRow({ label, value }: { label: string; value: string }) { return <div className="resource-row"><div><span>{label}</span><strong>{value}</strong></div><small>Live sample</small></div>; }
function KeyValue({ label, value }: { label: string; value: string }) { return <div className="key-value"><span>{label}</span><strong>{value}</strong></div>; }
function Endpoint({ label, value }: { label: string; value: string }) { const copy = () => navigator.clipboard.writeText(value); return <div className="endpoint"><span>{label}</span><code>{value}</code><button onClick={copy} title="Copy endpoint"><Copy size={13} /></button></div>; }

function OpenCoreDialog({ dialog, title, onChange, onCancel, onConfirm }: {
  dialog: Exclude<ConversationDialog, null>; title: string; onChange: (value: string) => void; onCancel: () => void; onConfirm: () => void;
}) {
  const rename = dialog.kind === "rename";
  return <><div className="modal-backdrop" role="presentation" onMouseDown={onCancel} />
    <FloatingWindow id="conversation-dialog" title={rename ? "Rename conversation" : "Delete conversation"} icon={<MessageSquare size={17} />} onClose={onCancel} place="center" modal className="opencore-modal dialog-floating" initialWidth={460} initialHeight={260} minWidth={350} minHeight={205}>
      <div className="modal-brand"><span>OpenCore</span></div>
      <h2 id="conversation-dialog-title">{rename ? "Rename conversation" : "Delete conversation?"}</h2>
      {rename
        ? <input autoFocus aria-label="Conversation name" value={dialog.value} onChange={(event) => onChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") onConfirm(); if (event.key === "Escape") onCancel(); }} />
        : <p><strong>{title}</strong> will be removed from local conversation history.</p>}
      <div className="modal-actions"><button onClick={onCancel}>Cancel</button><button className={rename ? "primary" : "danger"} onClick={onConfirm}>{rename ? "Save name" : "Delete"}</button></div>
    </FloatingWindow>
  </>;
}

function ProjectEditDialog({ dialog, onChange, onCancel, onConfirm }: {
  dialog: Exclude<ProjectDialog, null>; onChange: (value: string) => void; onCancel: () => void; onConfirm: () => void;
}) {
  const rename = dialog.kind === "rename";
  return <><div className="modal-backdrop" role="presentation" onMouseDown={onCancel} />
    <FloatingWindow id="project-dialog" title={rename ? "Rename project" : "Delete project"} ariaLabel={rename ? "Rename project" : `Delete ${dialog.project.name}?`} icon={<FolderOpen size={17} />} onClose={onCancel} place="center" modal className="opencore-modal dialog-floating" initialWidth={480} initialHeight={270} minWidth={350} minHeight={205}>
      <h2 id="project-dialog-title">{rename ? "Rename project" : `Delete ${dialog.project.name}?`}</h2>
      {rename
        ? <input autoFocus aria-label="Project name" value={dialog.value} maxLength={120} onChange={(event) => onChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") onConfirm(); if (event.key === "Escape") onCancel(); }} />
        : <p>{dialog.project.conversationCount} conversation{dialog.project.conversationCount === 1 ? "" : "s"} will move to <strong>No project</strong>. Their messages, exports, and pins will be kept. The physical folder will not be changed or deleted.</p>}
      <div className="modal-actions"><button onClick={onCancel}>Cancel</button><button className={rename ? "primary" : "danger"} onClick={onConfirm}>{rename ? "Save name" : "Delete project, keep chats"}</button></div>
    </FloatingWindow>
  </>;
}

function ArchiveEventCard({ event, onNotice }: { event: ArchiveEvent; onNotice: (message: string) => void }) {
  const [image, setImage] = useState<string | null>(null);
  const [exact, setExact] = useState<ArchiveEvent | null>(null);
  const shown = exact || event;
  const displayContent = shown.content.replace(/data:image\/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+/g, "[Archived image data · use View archived image]");
  let receipt: Record<string, unknown> | null = null;
  if (shown.kind === "echo") {
    try { const parsed = JSON.parse(shown.content); if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) receipt = parsed; } catch { /* Older receipts remain readable as text. */ }
  }
  const receiptArtifact = receipt?.artifact && typeof receipt.artifact === "object" ? receipt.artifact as Record<string, unknown> : null;
  const assets = Array.isArray(shown.metadata.assets) ? shown.metadata.assets as { name?: string; asset?: string }[] : [];
  let contentParts: { type?: string; asset?: string }[] = [];
  try { const parsed = JSON.parse(shown.content); if (Array.isArray(parsed)) contentParts = parsed; } catch { /* Plain text remains exact. */ }
  const asset = assets.find((item) => item.asset?.startsWith("echo-asset:"))?.asset || contentParts.find((item) => item.asset?.startsWith("echo-asset:"))?.asset;
  const files = Array.isArray(shown.metadata.files) ? shown.metadata.files as { name?: string; bytes?: number; included?: boolean; note?: string }[] : [];
  const openImage = async () => {
    if (!asset) return;
    try { setImage(await api.readArchiveAsset(asset.slice("echo-asset:".length))); }
    catch (error) { onNotice(`Could not open archived image: ${String(error)}`); }
  };
  return <article className={`archive-event archive-event-${event.kind}`}>
    <header><span className="archive-event-kind">{event.kind.replace(/_/g, " ")}</span><strong>{event.title || event.role}</strong><small>{event.source} · {new Date(event.timestamp * 1000).toLocaleString()}</small></header>
    {receipt ? <details className="archive-event-receipt"><summary>{receiptArtifact?.status === "complete" ? "Response saved" : "ECHO receipt"}{typeof receiptArtifact?.words === "number" ? ` · ${receiptArtifact.words} words` : ""} · Open details</summary><pre>{displayContent}</pre></details> : displayContent.trim() ? <pre>{displayContent}</pre> : null}
    {event.truncated && !exact ? <button className="archive-event-expand" onClick={() => void api.readArchiveEvent(event.eventId).then(setExact).catch((error) => onNotice(`Could not open exact event: ${String(error)}`))}>Open full event · {event.contentBytes.toLocaleString()} bytes</button> : null}
    {files.length ? <div className="archive-event-files">{files.map((file, index) => <span key={`${file.name}:${index}`}>{file.name || "Attachment"} · {typeof file.bytes === "number" ? `${Math.ceil(file.bytes / 1024)} KB` : "size unknown"}{file.note ? ` · ${file.note}` : ""}</span>)}</div> : null}
    {asset ? <div className="archive-event-image"><button onClick={() => void openImage()}>{image ? "Reload image" : "View archived image"}</button>{image ? <img src={image} alt={assets[0]?.name || "Archived image"} /> : null}</div> : null}
  </article>;
}

function SupportingView({ view, snapshot, selectedProfile, onSelectProfile, selectedConversation, onNotice, onRefresh, onNavigate, appearance, onAppearanceChange }: { view: View; snapshot: AppSnapshot; selectedProfile: RuntimeProfile; onSelectProfile: (profile: RuntimeProfile) => void; selectedConversation?: string; onNotice: (message: string) => void; onRefresh: () => Promise<void>; onNavigate: (view: View) => void; appearance: Appearance; onAppearanceChange: (value: Appearance) => void }) {
  const [connectorForm, setConnectorForm] = useState({ name: "", endpoint: "", matchPattern: "", kind: "openai" });
  const [connectorNotice, setConnectorNotice] = useState("");
  const [memoryQuery, setMemoryQuery] = useState("");
  const [memoryHits, setMemoryHits] = useState<import("./types").ArchiveSearchHit[]>([]);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [archiveOverview, setArchiveOverview] = useState<import("./types").ArchiveOverview | null>(null);
  const [memoryScope, setMemoryScope] = useState("all");
  const [memorySearched, setMemorySearched] = useState(false);
  const [memoryPreview, setMemoryPreview] = useState<{ title: string; content: string } | null>(null);
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [openArchiveId, setOpenArchiveId] = useState<string | null>(null);
  const [archivePages, setArchivePages] = useState<ArchivePageRef[]>([]);
  const [archiveEvents, setArchiveEvents] = useState<ArchiveEvent[]>([]);
  const [generalEvents, setGeneralEvents] = useState<ArchiveEvent[] | null>(null);
  const [archiveEventsMore, setArchiveEventsMore] = useState(false);
  const [memoryCategory, setMemoryCategory] = useState("all");
  const [archivePageText, setArchivePageText] = useState<Record<string, string>>({});
  const [archivePageBusy, setArchivePageBusy] = useState<string | null>(null);
  const [archiveHasMore, setArchiveHasMore] = useState(false);
  const archiveRequest = useRef(0);
  const archiveReader = useRef<HTMLElement | null>(null);
  const [operations, setOperations] = useState<OperationRecord[]>([]);
  const operationsRef = useRef<OperationRecord[]>([]);
  const [syncStarting, setSyncStarting] = useState<Record<string, boolean>>({});
  const [syncCancelBusy, setSyncCancelBusy] = useState<Record<string, boolean>>({});
  const [historyClearBusy, setHistoryClearBusy] = useState<Record<string, boolean>>({});
  const [connectorActionBusy, setConnectorActionBusy] = useState<Record<string, boolean>>({});
  const [connectorFeedback, setConnectorFeedback] = useState<Record<string, { message: string; error: boolean }>>({});
  const [profileBusy, setProfileBusy] = useState<Record<string, boolean>>({});
  const [browserStatus, setBrowserStatus] = useState<api.BrowserStatus | null>(null);

  useEffect(() => {
    if (view !== "settings") return;
    let active = true;
    const refreshBrowser = () => void api.browserBridgeStatus().then((status) => { if (active) setBrowserStatus(status); }).catch(() => {});
    refreshBrowser();
    const timer = window.setInterval(refreshBrowser, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [view]);

  useEffect(() => {
    if (view !== "connectors" && view !== "settings") return;
    let active = true;
    const tick = async () => {
      try {
        const next = await api.listOperations();
        if (!active) return;
        const previous = new Map(operationsRef.current.map((item) => [item.id, item.status]));
        operationsRef.current = next;
        setOperations(next);
        if (next.some((item) => item.kind === "history_sync" && previous.has(item.id) && previous.get(item.id) !== item.status && (item.status === "completed" || item.status === "failed" || item.status === "cancelled"))) await onRefresh();
      } catch (error) { if (active) setConnectorNotice(String(error)); }
    };
    void tick();
    const timer = window.setInterval(tick, 1200);
    return () => { active = false; window.clearInterval(timer); };
  }, [view, onRefresh]);

  useEffect(() => {
    if (view !== "memory") return;
    let active = true;
    let pending = false;
    const update = async () => {
      if (pending) return;
      pending = true;
      try { const overview = await api.archiveOverview(); if (active) { setArchiveOverview(overview); setArchiveError(null); } }
      catch (error) { if (active) setArchiveError(String(error)); }
      finally { pending = false; }
    };
    void update();
    const timer = window.setInterval(update, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [view, snapshot.runtime.archivePath, onNotice]);

  const operationFor = (id: string) => operations.find((item) => item.kind === "history_sync" && item.target === id);
  const syncLabel = (id: string) => {
    if (syncStarting[id]) return "Starting sync…";
    const operation = operationFor(id);
    if (!operation) return "Sync history";
    if (operation.status === "queued") return "Queued…";
    if (operation.status === "running") return operation.total ? `Scanning files… ${operation.current}/${operation.total}` : "Scanning folders…";
    if (operation.status === "failed") return "Retry sync";
    if (operation.status === "cancelled") return "Retry sync";
    return operation.summary || "Sync complete";
  };

  const configure = async (id: string, endpoint: string) => {
    setConnectorActionBusy((current) => ({ ...current, [id]: true }));
    setConnectorFeedback((current) => { const next = { ...current }; delete next[id]; return next; });
    try {
      const message = id === "unsloth" ? await api.configureUnsloth() : await api.testConnector(id, endpoint);
      setConnectorFeedback((current) => ({ ...current, [id]: { message, error: false } }));
    } catch (error) {
      setConnectorFeedback((current) => ({ ...current, [id]: { message: String(error), error: true } }));
    } finally { setConnectorActionBusy((current) => ({ ...current, [id]: false })); }
  };
  const connectAgent = async (id: "claude-code" | "codex") => {
    setProfileBusy((current) => ({ ...current, [id]: true }));
    try {
      setConnectorNotice(await api.configureAgentConnector(id));
      await onRefresh();
    } catch (error) { setConnectorNotice(String(error)); }
    finally { setProfileBusy((current) => ({ ...current, [id]: false })); }
  };
  const syncHistory = async (id: "claude-code" | "codex") => {
    setSyncStarting((current) => ({ ...current, [id]: true }));
    try {
      const operation = await api.startHistorySync(id);
      operationsRef.current = [operation, ...operationsRef.current];
      setOperations(operationsRef.current);
    } catch (error) { setConnectorNotice(String(error)); onNotice(String(error)); }
    finally { setSyncStarting((current) => ({ ...current, [id]: false })); }
  };

  const cancelHistory = async (operationId: string, target: string) => {
    setSyncCancelBusy((current) => ({ ...current, [target]: true }));
    try {
      await api.cancelHistorySync(operationId);
      setConnectorFeedback((current) => ({ ...current, [target]: { message: "Cancellation requested…", error: false } }));
    } catch (error) {
      setConnectorFeedback((current) => ({ ...current, [target]: { message: String(error), error: true } }));
    } finally { setSyncCancelBusy((current) => ({ ...current, [target]: false })); }
  };

  const clearHistory = async (id: "claude-code" | "codex") => {
    if (!window.confirm(`Clear imported ${id === "codex" ? "Codex" : "Claude Code"} conversations from OpenCore and ECHO? Original transcript files will not be changed.`)) return;
    setHistoryClearBusy((current) => ({ ...current, [id]: true }));
    setConnectorFeedback((current) => { const next = { ...current }; delete next[id]; return next; });
    try {
      const message = await api.clearImportedHistory(id);
      setConnectorFeedback((current) => ({ ...current, [id]: { message, error: false } }));
      await onRefresh();
    } catch (error) {
      setConnectorFeedback((current) => ({ ...current, [id]: { message: String(error), error: true } }));
    } finally { setHistoryClearBusy((current) => ({ ...current, [id]: false })); }
  };

  const addConnector = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      await api.saveConnector({ ...connectorForm, id: null });
      await onRefresh();
      setConnectorNotice(`Saved ${connectorForm.name}. Send X-OpenCore-Client: ${connectorForm.matchPattern} from that client.`);
      setConnectorForm({ name: "", endpoint: "", matchPattern: "", kind: "openai" });
    } catch (error) { setConnectorNotice(String(error)); }
  };

  const searchMemory = async () => {
    if (!memoryQuery.trim()) return;
    setMemoryBusy(true);
    const conversationIds = memoryScope.startsWith("chat:") ? [memoryScope.slice(5)]
      : memoryScope.startsWith("project:") ? snapshot.conversations.filter((item) => item.projectId === memoryScope.slice(8)).map((item) => item.id)
      : undefined;
    if (memoryScope.startsWith("project:") && conversationIds?.length === 0) { setMemoryHits([]); setMemorySearched(true); setMemoryBusy(false); return; }
    try { setMemoryHits(await api.searchArchive(memoryQuery, 75, conversationIds)); setMemorySearched(true); }
    catch (error) { onNotice(String(error)); }
    finally { setMemoryBusy(false); }
  };

  const showMemoryPage = async (hit: import("./types").ArchiveSearchHit) => {
    setMemoryBusy(true);
    try {
      const content = await api.readArchivePage(hit.archiveFile, hit.pageId);
      const title = snapshot.conversations.find((item) => item.id === hit.conversationId)?.title || hit.conversationId;
      setMemoryPreview({ title, content });
    } catch (error) { onNotice(String(error)); }
    finally { setMemoryBusy(false); }
  };

  const archivePageKey = (page: ArchivePageRef) => `${page.archiveFile}:${page.pageId}`;
  const readArchiveText = async (page: ArchivePageRef) => {
    const key = archivePageKey(page);
    setArchivePageBusy(key);
    try {
      const content = await api.readArchivePage(page.archiveFile, page.pageId);
      setArchivePageText((current) => ({ ...current, [key]: content }));
    } catch (error) { onNotice(String(error)); }
    finally { setArchivePageBusy(null); }
  };
  const fillArchivePages = async (pages: ArchivePageRef[], request: number) => {
    for (let offset = 0; offset < pages.length; offset += 4) {
      const batch = pages.slice(offset, offset + 4);
      const results = await Promise.allSettled(batch.map((page) => api.readArchivePage(page.archiveFile, page.pageId)));
      if (request !== archiveRequest.current) return;
      const texts: Record<string, string> = {};
      results.forEach((result, index) => {
        if (result.status === "fulfilled") texts[archivePageKey(batch[index])] = result.value;
      });
      setArchivePageText((current) => ({ ...current, ...texts }));
      const failed = results.find((result) => result.status === "rejected");
      if (failed?.status === "rejected") onNotice(`Could not open an archive page: ${String(failed.reason)}`);
    }
  };
  const openArchive = async (conversationId: string) => {
    const request = ++archiveRequest.current;
    setMemoryScope(`chat:${conversationId}`);
    setMemoryHits([]);
    setMemorySearched(false);
    setOpenArchiveId(conversationId);
    setGeneralEvents(null);
    setArchivePages([]);
    setArchiveEvents([]);
    setArchiveEventsMore(false);
    setArchivePageText({});
    setMemoryBusy(true);
    try {
      const [pages, events] = await Promise.all([api.listArchivePages(conversationId, 0, 40), api.listArchiveEvents(conversationId, 0, 100)]);
      if (request !== archiveRequest.current) return;
      setArchivePages(pages);
      setArchiveHasMore(pages.length === 40);
      setArchiveEvents(events);
      setArchiveEventsMore(events.length === 100);
      window.setTimeout(() => { if (request === archiveRequest.current) archiveReader.current?.scrollIntoView?.({ behavior: "smooth", block: "start" }); }, 0);
      await fillArchivePages(pages, request);
    } catch (error) { onNotice(String(error)); }
    finally { if (request === archiveRequest.current) setMemoryBusy(false); }
  };
  const openGeneralArchive = async () => {
    const request = ++archiveRequest.current;
    setOpenArchiveId(null);
    setMemoryScope("all");
    setMemoryCategory("all");
    setGeneralEvents(null);
    setMemoryBusy(true);
    try {
      const events = await api.listArchiveEvents("*", 0, 50);
      if (request === archiveRequest.current) setGeneralEvents(events);
    } catch (error) { onNotice(`Could not open general ECHO activity: ${String(error)}`); }
    finally { if (request === archiveRequest.current) setMemoryBusy(false); }
  };
  const loadMoreArchivePages = async () => {
    if (!openArchiveId || memoryBusy) return;
    const request = archiveRequest.current;
    setMemoryBusy(true);
    try {
      const pages = await api.listArchivePages(openArchiveId, archivePages.length, 40);
      if (request !== archiveRequest.current) return;
      setArchivePages((current) => [...current, ...pages]);
      setArchiveHasMore(pages.length === 40);
      await fillArchivePages(pages, request);
    } catch (error) { onNotice(String(error)); }
    finally { if (request === archiveRequest.current) setMemoryBusy(false); }
  };

  const loadMoreArchiveEvents = async () => {
    if (!openArchiveId || memoryBusy) return;
    setMemoryBusy(true);
    try {
      const events = await api.listArchiveEvents(openArchiveId, archiveEvents.length, 100);
      setArchiveEvents((current) => [...current, ...events]);
      setArchiveEventsMore(events.length === 100);
    } catch (error) { onNotice(String(error)); }
    finally { setMemoryBusy(false); }
  };

  const memoryAction = async (action: "trim" | "compact") => {
    const target = openArchiveId || selectedConversation;
    if (!target) return onNotice("Open an archive first.");
    setMemoryBusy(true);
    try { onNotice(await api.echoMemoryAction(action, target)); setArchiveOverview(await api.archiveOverview()); }
    catch (error) { onNotice(String(error)); }
    finally { setMemoryBusy(false); }
  };
  const indexEchoHistory = async () => {
    setMemoryBusy(true);
    try { onNotice(await api.indexEchoHistory()); setArchiveOverview(await api.archiveOverview()); }
    catch (error) { onNotice(String(error)); }
    finally { setMemoryBusy(false); }
  };
  const archiveRows = (archiveOverview?.conversations || []).map((item) => {
    const chat = snapshot.conversations.find((conversation) => conversation.id === item.conversationId);
    const client = chat?.client || (/^codex/i.test(item.conversationId) ? "Codex" : /^claude/i.test(item.conversationId) ? "Claude Code" : "OpenCore");
    return { ...item, chat, client };
  }).sort((a, b) => Number(Boolean(b.chat?.pinned)) - Number(Boolean(a.chat?.pinned)) || b.lastTimestamp - a.lastTimestamp);
  const categoryRows = archiveRows.filter((item, index) => memoryCategory === "all"
    || (memoryCategory === "pinned" && item.chat?.pinned)
    || (memoryCategory === "projects" && item.chat?.projectId)
    || (memoryCategory === "recent" && index < 20)
    || item.client.toLowerCase() === memoryCategory);
  const visibleSummaries = (archiveOverview?.summaries || []).filter((item) => memoryScope === "all" || (memoryScope.startsWith("chat:") ? item.conversationId === memoryScope.slice(5) : snapshot.conversations.some((conversation) => conversation.id === item.conversationId && conversation.projectId === memoryScope.slice(8))));
  if (view === "overview") return <div className="support-page overview-page">
    <div className="page-heading"><div><h1>OpenCore</h1><p>Your local model, persistent ECHO memory, conversations, and client routes in one private workspace.</p></div><button className="primary" onClick={() => onNavigate("conversations")}><MessageSquare size={15} /> Open conversations</button></div>
    <div className="overview-grid">
      <button onClick={() => onNavigate("runtime")}><SquareTerminal /><span><strong>Runtime</strong><small>{profileLabel(["running", "starting"].includes(snapshot.runtime.status) ? snapshot.runtime.profile : selectedProfile)} · {snapshot.runtime.status}</small></span></button>
      <button onClick={() => onNavigate("memory")}><Database /><span><strong>ECHO Memory</strong><small>{snapshot.runtime.contextSize.toLocaleString()} live tokens · persistent archive</small></span></button>
      <button onClick={() => onNavigate("models")}><Box /><span><strong>Model</strong><small>{snapshot.runtime.modelPath.split(/[\\/]/).pop()}</small></span></button>
      <button onClick={() => onNavigate("connectors")}><Network /><span><strong>Connectors</strong><small>{snapshot.connectors.filter((item) => item.observable || item.status === "configured").length} active or observable</small></span></button>
      <button onClick={() => onNavigate("conversations")}><MessageSquare /><span><strong>Conversations</strong><small>{snapshot.conversations.length} indexed across OpenCore, Claude Code, and Codex</small></span></button>
      <button onClick={() => onNavigate("settings")}><SlidersHorizontal /><span><strong>Settings</strong><small>Privacy, storage, exports, and local endpoint</small></span></button>
    </div>
  </div>;
  if (view === "connectors") return <div className="support-page">
    <div className="page-heading"><div><h1>Connectors</h1><p>Connect coding clients directly to the loaded OpenCore model. Transcript sync is optional and separate.</p></div></div>
    <div className="connector-list">{snapshot.connectors.map((connector) => {
      const history = connector.kind === "history";
      const syncOperation = history ? operationFor(connector.id) : undefined;
      const syncActive = syncOperation?.status === "running" || syncOperation?.status === "queued";
      return <article key={connector.id}>
        <div className="connector-icon"><Network /></div>
        <div className="connector-copy"><h2>{connector.name}</h2><p>{connector.details}</p><code>{history ? "OpenCore local model connector" : connector.endpoint}</code></div>
        <div className="connector-state"><StatusDot state={connector.status} /><strong>{connector.status}</strong><span>{history ? (connector.status === "configured" ? "Opt-in profile added" : "Profile not added") : connector.observable ? "Observable" : "Not observable"}</span></div>
        {history ? <div className="connector-actions">
          <button className="primary" disabled={profileBusy[connector.id]} onClick={() => connectAgent(connector.id as "claude-code" | "codex")}>{profileBusy[connector.id] ? "Writing profile…" : connector.status === "configured" ? "Refresh profile" : "Add profile"}</button>
          <button className="sync-history-button" disabled={syncActive || syncStarting[connector.id]} onClick={() => syncHistory(connector.id as "claude-code" | "codex")}>{syncLabel(connector.id)}</button>
          {syncActive && syncOperation ? <button className="cancel-history-button" aria-label={`Cancel ${connector.name} import`} disabled={syncCancelBusy[connector.id]} onClick={() => void cancelHistory(syncOperation.id, connector.id)}>{syncCancelBusy[connector.id] ? "Canceling…" : "Cancel import"}</button> : null}
          <button className="clear-history-button" disabled={syncActive || syncStarting[connector.id] || historyClearBusy[connector.id]} title="Removes the imported copy from OpenCore and ECHO. Source transcript files stay in place." onClick={() => void clearHistory(connector.id as "claude-code" | "codex")}>{historyClearBusy[connector.id] ? "Clearing…" : "Clear imported history"}</button>
          {connectorFeedback[connector.id] ? <div className={`connector-action-feedback ${connectorFeedback[connector.id].error ? "error" : "success"}`} role={connectorFeedback[connector.id].error ? "alert" : "status"}>{connectorFeedback[connector.id].message}</div> : null}
          {syncOperation ? <div className={`connector-operation ${syncOperation.status}`} role="status"><span>{syncOperation.status === "failed" ? syncOperation.error : syncOperation.status === "running" ? `${syncOperation.phase}${syncOperation.total ? ` · ${syncOperation.current}/${syncOperation.total} files` : ""}` : syncOperation.summary || syncOperation.phase}</span><time>{shortDate(syncOperation.finishedAt || syncOperation.startedAt)} · {shortTime(syncOperation.finishedAt || syncOperation.startedAt)}</time>{syncActive && syncOperation.total > 0 ? <progress max={syncOperation.total} value={syncOperation.current} /> : null}</div> : null}
        </div> : <div className="connector-actions single">
          <button disabled={connectorActionBusy[connector.id]} onClick={() => void configure(connector.id, connector.endpoint)}>{connectorActionBusy[connector.id] ? (connector.id === "unsloth" ? "Installing…" : "Testing…") : connector.id === "unsloth" ? "Install" : "Test"}</button>
          {connectorFeedback[connector.id] ? <div className={`connector-action-feedback ${connectorFeedback[connector.id].error ? "error" : "success"}`} role={connectorFeedback[connector.id].error ? "alert" : "status"}>{connectorFeedback[connector.id].message}</div> : null}
        </div>}
      </article>;
    })}</div>
    <form className="custom-connector" onSubmit={addConnector}>
      <div><h2>Add custom provider or client</h2><p>Any OpenAI-compatible endpoint can be tested and saved. Routed requests become observable through the gateway.</p></div>
      <label>Name<input required value={connectorForm.name} onChange={(e) => setConnectorForm({ ...connectorForm, name: e.target.value })} placeholder="My local provider" /></label>
      <label>Endpoint<input required value={connectorForm.endpoint} onChange={(e) => setConnectorForm({ ...connectorForm, endpoint: e.target.value })} placeholder="http://127.0.0.1:9000/v1" /></label>
      <label>Client match<input required value={connectorForm.matchPattern} onChange={(e) => setConnectorForm({ ...connectorForm, matchPattern: e.target.value })} placeholder="my-client" /></label>
      <label>Protocol<select value={connectorForm.kind} onChange={(e) => setConnectorForm({ ...connectorForm, kind: e.target.value })}><option value="openai">OpenAI-compatible</option><option value="ollama">Ollama</option><option value="custom">Custom local</option></select></label>
      <button className="primary" type="submit">Add connector</button>
    </form>
    {connectorNotice && <div className="connector-notice">{connectorNotice}</div>}
    <div className="route-instruction"><ShieldCheck /><div><strong>Universal observable endpoint</strong><code>http://127.0.0.1:{snapshot.runtime.gatewayPort}/v1</code><p>Use this base URL in OpenAI-compatible clients. Claude Code/Codex use local transcript sync because their native protocols differ.</p></div></div>
  </div>;
  if (view === "memory") return <div className="support-page memory-page">
    {archiveError ? <p role="alert">Could not read ECHO archives: {archiveError}. Retrying automatically.</p> : !archiveOverview ? <p role="status">Loading saved ECHO archives…</p> : null}
    <div className="page-heading"><div><h1>ECHO Memory</h1><p>Exact history, tool activity, files, and generated summaries across your conversations and projects.</p></div><button onClick={() => void revealLocalPath(snapshot.runtime.archivePath, onNotice)}><FolderOpen size={15} /> Open folder</button></div>
    <button className="memory-general-button" onClick={() => void openGeneralArchive()}><Database size={17} /><span><strong>General ECHO archive</strong><small>Browse all recorded conversations and projects</small></span><span>{archiveOverview ? `${archiveRows.length.toLocaleString()} archives` : "Loading archives…"}</span></button>
    <div className="memory-summary" aria-label="Archive totals">
      <div><strong>{archiveOverview?.conversations.length.toLocaleString() ?? "—"}</strong><span>conversations</span></div>
      <div><strong>{archiveOverview?.pages.toLocaleString() ?? "—"}</strong><span>exact pages</span></div>
      <div><strong>{archiveOverview ? (archiveOverview.sourceBytes / 1024 / 1024).toFixed(1) : "—"} MB</strong><span>source history</span></div>
      <div><strong>{archiveOverview?.toolCalls?.toLocaleString() ?? "—"}</strong><span>tool calls</span></div>
      <div><strong>{archiveOverview?.toolResults?.toLocaleString() ?? "—"}</strong><span>tool results</span></div>
      <div><strong>{archiveOverview?.imageAssets?.toLocaleString() ?? "—"}</strong><span>archived images</span></div>
    </div>
    <div className="memory-layout">
      <section>
        <h2>Search archive</h2>
        <label className="memory-scope-label">Scope<select aria-label="Memory scope" value={memoryScope} onChange={(event) => { const next = event.target.value; setMemoryScope(next); setMemoryHits([]); setMemorySearched(false); if (next.startsWith("chat:")) void openArchive(next.slice(5)); else { archiveRequest.current += 1; setOpenArchiveId(null); setMemoryBusy(false); } }}>
          <option value="all">All ECHO memory</option>
          {snapshot.projects.map((project) => <option key={project.id} value={`project:${project.id}`}>Project · {project.name}</option>)}
          {snapshot.conversations.map((conversation) => <option key={conversation.id} value={`chat:${conversation.id}`}>Chat · {conversation.title}</option>)}
          {archiveOverview?.conversations.filter((item) => !snapshot.conversations.some((conversation) => conversation.id === item.conversationId)).map((item) => <option key={item.conversationId} value={`chat:${item.conversationId}`}>Archived chat · {item.conversationId}</option>)}
        </select></label>
        <div className="search memory-search"><Search size={15} /><input aria-label="Search archived text" value={memoryQuery} onChange={(e) => setMemoryQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && searchMemory()} placeholder="Search words or code…" /><button onClick={searchMemory} disabled={memoryBusy}>{memoryBusy ? "Searching…" : "Search"}</button></div>
        <div className="memory-results">{memoryHits.length === 0 ? <p>{memorySearched ? "No matching pages in this scope." : "Search or choose a conversation below."}</p> : memoryHits.map((hit) => <button key={`${hit.archiveFile}:${hit.pageId}`} className="memory-hit" onClick={() => void showMemoryPage(hit)}><strong>{snapshot.conversations.find((item) => item.id === hit.conversationId)?.title || hit.conversationId}</strong><small>Exact page</small><span>{hit.preview}</span></button>)}</div>
        <h2 className="memory-list-title">Archived conversations</h2>
        <div className="memory-category-tabs" role="group" aria-label="Archive categories">{[{ id:"all", label:"All" }, { id:"pinned", label:"Pinned" }, { id:"recent", label:"Recent" }, { id:"opencore", label:"OpenCore" }, { id:"codex", label:"Codex" }, { id:"claude code", label:"Claude Code" }, { id:"projects", label:"Projects" }].map((category) => <button key={category.id} className={memoryCategory === category.id ? "active" : ""} onClick={() => setMemoryCategory(category.id)}>{category.label}</button>)}</div>
        <div className="memory-conversation-list">{categoryRows.length ? categoryRows.map((item) => <button key={item.conversationId} className={openArchiveId === item.conversationId ? "active" : ""} onClick={() => void openArchive(item.conversationId)}><span>{item.chat?.pinned ? "◆ " : ""}{item.chat?.title || item.conversationId}<small>{item.client}{item.chat?.project ? ` · ${item.chat.project}` : ""}</small></span><small>{item.pages.toLocaleString()} pages</small></button>) : <p>{archiveOverview ? "No archives in this category." : "Reading saved archives…"}</p>}</div>
      </section>
      <section>
        <h2>Working set</h2>
        <EchoContextStatus conversationId={openArchiveId || selectedConversation} running={snapshot.runtime.status === "running"} configuredContextTokens={snapshot.runtime.contextSize} attentionKvLocation={snapshot.runtime.attentionKvLocation} attentionKvType={snapshot.runtime.attentionKvType} />
        <p>{selectedConversation ? `Selected conversation: ${selectedConversation}` : "Select a conversation before trimming or compacting."}</p>
        <div className="memory-actions">
          <button onClick={indexEchoHistory} disabled={memoryBusy}>{memoryBusy ? "Indexing…" : "Index activity and files"}</button>
          <button onClick={() => memoryAction("compact")} disabled={memoryBusy || !(openArchiveId || selectedConversation)}>Summarize open archive</button>
          <button onClick={() => memoryAction("trim")} disabled={memoryBusy || !(openArchiveId || selectedConversation)}>Trim live context</button>
          <button onClick={async () => { try { onNotice(`Index exported to ${await api.exportArchiveIndex()}`); } catch (error) { onNotice(String(error)); } }}>Export index</button>
        </div>
        <div className="memory-integrity"><ShieldCheck size={16} /><span>Pages keep the original text and code. Opening a page verifies its source hash.</span></div>
        <h2 className="memory-list-title">Generated summaries</h2>
        <div className="memory-summary-list">{visibleSummaries.length ? visibleSummaries.map((item, index) => <button key={`${item.conversationId}:${item.generatedAt}:${index}`} onClick={() => setMemoryPreview({ title: `Generated summary · ${snapshot.conversations.find((conversation) => conversation.id === item.conversationId)?.title || item.conversationId}`, content: item.content })}><strong>{snapshot.conversations.find((conversation) => conversation.id === item.conversationId)?.title || item.conversationId}</strong><small>{item.sourcePages} source pages · {item.modelCalls} model calls{item.incomplete || item.truncated ? " · partial" : ""}</small></button>) : <p>No generated summaries in this scope yet.</p>}</div>
      </section>
    </div>
    {generalEvents ? <section className="archive-reader" aria-label="General ECHO archive"><div className="archive-reader-heading"><div><h2>General ECHO activity</h2><span>Latest 50 exact events across all archives · select a conversation above to open its full history</span></div><button onClick={() => setGeneralEvents(null)}>Close archive</button></div><div className="archive-event-list">{generalEvents.map((event) => <ArchiveEventCard key={event.eventId} event={event} onNotice={onNotice} />)}</div></section> : null}
    {openArchiveId ? <section ref={archiveReader} className="archive-reader" aria-label="Open archive">
      <div className="archive-reader-heading"><div><h2>{snapshot.conversations.find((item) => item.id === openArchiveId)?.title || openArchiveId}</h2><span>{archivePages.length.toLocaleString()} of {(archiveOverview?.conversations.find((item) => item.conversationId === openArchiveId)?.pages || archivePages.length).toLocaleString()} pages · exact source</span></div><button onClick={() => { archiveRequest.current += 1; setOpenArchiveId(null); setMemoryBusy(false); }}>Close archive</button></div>
      <h3 className="archive-section-title">Activity, tools, files, and images</h3>
      {archiveEvents.length ? <div className="archive-event-list">{archiveEvents.map((event) => <ArchiveEventCard key={event.eventId} event={event} onNotice={onNotice} />)}</div> : <p className="archive-legacy-note">This archive has exact transcript pages but no indexed activity yet. Use “Index imported chats” to rebuild its tool and file records from available conversation history.</p>}
      {archiveEventsMore ? <button className="archive-load-more" onClick={() => void loadMoreArchiveEvents()} disabled={memoryBusy}>Load more activity</button> : null}
      <h3 className="archive-section-title">Exact transcript pages</h3>
      {archivePages.length === 0 ? <p>{memoryBusy ? "Opening archive…" : "No pages in this archive."}</p> : archivePages.map((page, index) => <article className="archive-reader-page" key={`${page.archiveFile}:${page.pageId}`}>
        <div><strong>Page {index + 1}</strong><small>Bytes {page.offsetStart.toLocaleString()}–{page.offsetEnd.toLocaleString()}</small><button onClick={() => void readArchiveText(page)} disabled={archivePageBusy === archivePageKey(page)}>{archivePageText[archivePageKey(page)] !== undefined ? "Reload" : archivePageBusy === archivePageKey(page) ? "Opening…" : "Open page"}</button></div>
        {archivePageText[archivePageKey(page)] !== undefined ? <pre>{archivePageText[archivePageKey(page)]}</pre> : <p className="archive-page-loading">{memoryBusy ? "Opening exact text…" : "Open page to retry."}</p>}
      </article>)}
      {archiveHasMore ? <button className="archive-load-more" onClick={() => void loadMoreArchivePages()} disabled={memoryBusy}>{memoryBusy ? "Loading…" : "Load more pages"}</button> : null}
    </section> : null}
    {memoryPreview ? <FloatingWindow id="memory-page" title={memoryPreview.title} icon={<Archive size={17} />} className="memory-page-preview" onClose={() => setMemoryPreview(null)} place="center" initialWidth={760} initialHeight={620} minWidth={390} minHeight={260} ariaLabel="Exact ECHO archive page"><pre>{memoryPreview.content}</pre></FloatingWindow> : null}
  </div>;
  const modelDir = snapshot.runtime.modelPath.replace(/[\\/][^\\/]+$/, "");
  if (view === "context") return <div className="support-page">
    <div className="page-heading"><div><h1>Live Context</h1><p>The actual prompt window and ECHO offload state for the selected conversation.</p></div></div>
    <div className="settings-grid">
      <InspectorSection title="Active live window"><KeyValue label="Profile" value={profileLabel(snapshot.runtime.profile)} /><KeyValue label="Configured inference window" value={`${snapshot.runtime.contextSize.toLocaleString()} tokens`} />{snapshot.runtime.profile === "native1m" ? <KeyValue label="Original trained context" value="262,144 tokens · 1M uses YaRN length extension" /> : null}<KeyValue label="Selected conversation" value={snapshot.conversations.find((item) => item.id === selectedConversation)?.title || "No conversation selected"} /><EchoContextStatus conversationId={selectedConversation} running={snapshot.runtime.status === "running"} configuredContextTokens={snapshot.runtime.contextSize} attentionKvLocation={snapshot.runtime.attentionKvLocation} attentionKvType={snapshot.runtime.attentionKvType} /></InspectorSection>
      <InspectorSection title="ECHO archive"><KeyValue label="Configured model window" value={`${snapshot.runtime.contextSize.toLocaleString()} tokens per inference`} /><KeyValue label="Long-term history" value="Exact disk-backed ECHO archive; limited by available storage" /><KeyValue label="Addressable-history target" value="3T tokens; retrieved into the finite model window when relevant" /></InspectorSection>
    </div>
  </div>;

  if (view === "models") return <div className="support-page">
    <div className="page-heading"><div><h1>Models</h1><p>Install or uninstall local models, then choose which one to use.</p></div><button onClick={() => void revealLocalPath(modelDir, onNotice)}><FolderOpen size={14} /> Open model folder</button></div>
    <ModelLibrary selectedProfile={selectedProfile} onSelect={onSelectProfile} runtimeActive={["running", "starting"].includes(snapshot.runtime.status) || Boolean(snapshot.activeConversationIds?.length)} onNotice={onNotice} />
    <div className="settings-grid">
      <section className="model-activity" aria-label="Model activity">
        <div className="model-activity-heading"><div><h2>Model activity</h2><span>{snapshot.runtime.loadingPhase || snapshot.runtime.status}</span></div><strong>{snapshot.runtime.status === "starting" ? `${((snapshot.runtime.loadingElapsedMs || 0) / 1000).toFixed(1)}s` : snapshot.runtime.loadingElapsedMs != null ? `Loaded in ${(snapshot.runtime.loadingElapsedMs / 1000).toFixed(1)}s` : "—"}</strong></div>
        <progress aria-label="Model loading" value={snapshot.runtime.status === "starting" ? modelLoaderDetail(snapshot).loaded ?? undefined : snapshot.runtime.status === "running" ? 1 : 0} max={snapshot.runtime.status === "starting" ? modelLoaderDetail(snapshot).total ?? undefined : 1} />
        <p className="model-loader-detail">{snapshot.runtime.status === "starting" ? modelLoaderDetail(snapshot).text : snapshot.runtime.status === "running" ? `Loaded in ${((snapshot.runtime.loadingElapsedMs || 0) / 1000).toFixed(1)} seconds` : snapshot.runtime.loadingPhase}</p>
        {snapshot.runtime.status === "starting" && modelLoaderDetail(snapshot).loaded !== null ? <p>{modelLoaderDetail(snapshot).loaded} of {modelLoaderDetail(snapshot).total} tensors reported by the runtime</p> : null}
        <div className="model-usage-grid">
          <div><small>Model calls</small><strong>{(snapshot.telemetry.responseCount || 0).toLocaleString()}</strong></div>
          <div><small>Prompt tokens</small><strong>{(snapshot.telemetry.totalPromptTokens ?? snapshot.telemetry.promptTokens).toLocaleString()}</strong></div>
          <div><small>Output tokens</small><strong>{(snapshot.telemetry.totalCompletionTokens ?? snapshot.telemetry.completionTokens).toLocaleString()}</strong></div>
          <div><small>Token speed</small><strong>{(recentDecoderSpeed(snapshot.logs) ?? snapshot.telemetry.tokensPerSecond).toFixed(1)} /s</strong><small>{recentDecoderSpeed(snapshot.logs) === null ? "Last reported" : "Live decoder sample"}</small></div>
        </div>
      </section>
      <InspectorSection title="Live context"><EchoContextStatus conversationId={selectedConversation} running={snapshot.runtime.status === "running"} configuredContextTokens={snapshot.runtime.contextSize} attentionKvLocation={snapshot.runtime.attentionKvLocation} attentionKvType={snapshot.runtime.attentionKvType} /><button className="wide" onClick={() => onNavigate("context")}><BrainCircuit size={14} /> Open live context</button></InspectorSection>
    </div>
  </div>;

  const effectiveCompactTokens = effectiveCompactAtTokens(appearance.compactAtTokens, snapshot.runtime.contextSize);

  if (view === "settings") return <div className="support-page settings-page">
    <div className="page-heading"><div><h1>Settings</h1><p>Real local controls for storage, privacy, history, and diagnostics.</p></div></div>
    <div className="settings-grid">
      <InspectorSection title="Conversation appearance">
        <label className="appearance-label" htmlFor="chat-font-size">Message text size <strong>{appearance.chatFontSize}px</strong></label>
        <input id="chat-font-size" className="appearance-range" type="range" min="13" max="18" step="1" value={appearance.chatFontSize} onChange={(event) => onAppearanceChange({ ...appearance, chatFontSize: Number(event.target.value) })} />
        <label className="appearance-label" htmlFor="terminal-font-size">Terminal/log text <strong>{appearance.terminalFontSize}px</strong></label>
        <input id="terminal-font-size" className="appearance-range" type="range" min="10" max="20" step="1" value={appearance.terminalFontSize} onChange={(event) => onAppearanceChange({ ...appearance, terminalFontSize: Number(event.target.value) })} />
        <div className="appearance-label">Message spacing</div>
        <div className="appearance-choices"><button className={!appearance.compactMessages ? "active" : ""} aria-pressed={!appearance.compactMessages} onClick={() => onAppearanceChange({ ...appearance, compactMessages: false })}>Comfortable</button><button className={appearance.compactMessages ? "active" : ""} aria-pressed={appearance.compactMessages} onClick={() => onAppearanceChange({ ...appearance, compactMessages: true })}>Compact</button></div>
        <p className="appearance-note">Changes apply to Conversations immediately and remain on this computer.</p>
      </InspectorSection>
      <InspectorSection title="Computer use">
        <div className="appearance-label">Window focus</div>
        <div className="appearance-choices"><button className={!appearance.keepUserWindowInFront ? "active" : ""} aria-pressed={!appearance.keepUserWindowInFront} onClick={() => onAppearanceChange({ ...appearance, keepUserWindowInFront: false })}>Bring OpenCore's work forward</button><button className={appearance.keepUserWindowInFront ? "active" : ""} aria-pressed={appearance.keepUserWindowInFront} onClick={() => onAppearanceChange({ ...appearance, keepUserWindowInFront: true })}>Keep my window in front</button></div>
        <p className="appearance-note">When your window stays in front, OpenCore can use supported app controls without taking focus. Mouse and keyboard actions wait until foreground control is selected. Windows may still foreground newly opened apps.</p>
        <p className="appearance-note">Press Escape twice to stop an active OpenCore run.</p>
      </InspectorSection>
      <InspectorSection title="Tools">
        <div className="appearance-choices"><button className={appearance.projectSkillsEnabled ? "active" : ""} aria-pressed={appearance.projectSkillsEnabled} onClick={() => onAppearanceChange({ ...appearance, projectSkillsEnabled: !appearance.projectSkillsEnabled })}>{appearance.projectSkillsEnabled ? "Project skills enabled" : "Project skills disabled"}</button></div>
        <p className="appearance-note">When enabled, the Claude Agent SDK loads this workspace's Claude settings and skills. These settings control which tools can be used in new prompts.</p>
        <div className="appearance-label">Default skills for every new prompt</div>
        <div className="appearance-choices"><button className={appearance.defaultComputerUse ? "active" : ""} aria-pressed={appearance.defaultComputerUse} onClick={() => onAppearanceChange({ ...appearance, defaultComputerUse: !appearance.defaultComputerUse })}>Computer use</button><button className={appearance.defaultBrowserUse ? "active" : ""} aria-pressed={appearance.defaultBrowserUse} onClick={() => onAppearanceChange({ ...appearance, defaultBrowserUse: !appearance.defaultBrowserUse })}>Browser</button><button className={appearance.defaultChromeControl ? "active" : ""} aria-pressed={appearance.defaultChromeControl} onClick={() => onAppearanceChange({ ...appearance, defaultChromeControl: !appearance.defaultChromeControl })}>Chrome</button></div>
        <p className="appearance-note">All are off by default. Without /computer-use, the 0.8B screen model is not exposed to the agent and cannot wake.</p>
        <div className="appearance-choices"><button className={appearance.subagentsEnabled ? "active" : ""} aria-pressed={appearance.subagentsEnabled} onClick={() => onAppearanceChange({ ...appearance, subagentsEnabled: !appearance.subagentsEnabled })}>{appearance.subagentsEnabled ? "Subagents enabled" : "Subagents disabled"}</button></div>
        <label className="appearance-label" htmlFor="max-subagents">Maximum subagent spawns per prompt <strong>{appearance.maxSubagents}</strong></label>
        <input id="max-subagents" className="appearance-number" type="number" min="1" max="1000" step="1" value={appearance.maxSubagents} disabled={!appearance.subagentsEnabled} onChange={(event) => onAppearanceChange({ ...appearance, maxSubagents: Math.max(1, Math.min(1000, Number(event.target.value) || 1)) })} />
        <p className="appearance-note">Hard ceiling: 1,000. Keep it low on a single-GPU machine; this is a capability limit, not a recommended spawn count.</p>
        <p className="appearance-note">Computer use, OpenCore Browser, and Chrome control are configured above. Type / in the composer to add a skill to one prompt.</p>
      </InspectorSection>
      <InspectorSection title="Model & context">
        <KeyValue label="ECHO 3T model" value="Qwen3.5-derived 5B class + BF16 vision projector" />
        <KeyValue label="ECHO model native window" value="262,144 tokens per inference" />
        <KeyValue label="DuoCore package context" value="Up to 65,536 live tokens · auto-fits free RAM; ECHO keeps the exact archive" />
        <KeyValue label="1M extended profile" value="1,000,000-token YaRN window; original trained context 262,144" />
        <label className="appearance-label" htmlFor="context-compact-tokens">Requested native-model auto-compaction trigger <strong>{appearance.compactAtTokens.toLocaleString()} tokens</strong></label>
        <input id="context-compact-tokens" className="appearance-number" type="number" min="1024" max="1000000" step="1024" value={appearance.compactAtTokens} onChange={(event) => onAppearanceChange({ ...appearance, compactAtTokens: Number(event.target.value) || 0 })} onBlur={() => { if (appearance.compactAtTokens < 1024 || appearance.compactAtTokens > 1000000) onAppearanceChange({ ...appearance, compactAtTokens: Math.max(1024, Math.min(1000000, appearance.compactAtTokens || 1024)) }); }} />
        <p className="appearance-note">Effective trigger for the configured {snapshot.runtime.contextSize.toLocaleString()}-token model window: <strong>{effectiveCompactTokens.toLocaleString()} tokens</strong>{effectiveCompactTokens < appearance.compactAtTokens ? " (lowered to leave room for the response and tool results)" : ""}. Native profiles compact automatically at this point. ECHO profiles preserve the exact conversation history in the archive; compaction only bounds the active model window.</p>
        <p className="appearance-note">This is an exact token count. The configured inference window sets the per-request ceiling. YaRN length extension does not mean the model was trained at that length. ECHO keeps the full conversation archive separately, with storage limited by available disk.</p>
      </InspectorSection>
      <InspectorSection title="ECHO virtual memory">
        <EchoMemorySettings />
      </InspectorSection>
      <InspectorSection title="Chrome extension">
        <KeyValue label="Bridge" value={browserStatus?.connected ? "Connected" : "Not connected"} /><KeyValue label="Local port" value={String(browserStatus?.port || 8814)} />
        <p className="appearance-note">In Chrome Extensions, enable Developer mode, choose Load unpacked, and select the bundled chrome-extension folder. Open the OpenCore extension popup and pair it with the local token below.</p>
        <code className="settings-token">{browserStatus?.token || "Loading pairing token..."}</code>
        <button className="wide" disabled={!browserStatus?.token} onClick={() => browserStatus?.token && navigator.clipboard.writeText(browserStatus.token)}><Copy size={14} /> Copy pairing token</button>
        <p className="appearance-note">/chrome-control can list/open/activate/close tabs, inspect, screenshot, interact, reload, and only for explicit development/debugging, evaluate JavaScript through Chrome DevTools Runtime.</p>
      </InspectorSection>
      <InspectorSection title="Privacy & responsibility"><KeyValue label="Network" value="Localhost only" /><KeyValue label="Credentials" value="Redacted before persistence" /><KeyValue label="AI output" value="Review code and tool actions before use" /><KeyValue label="Ownership" value="You control local data and exported conversations" /></InspectorSection>
      <InspectorSection title="Storage"><KeyValue label="Conversation database" value="Local SQLite" /><KeyValue label="ECHO archive" value={snapshot.runtime.archivePath} /><button className="wide" onClick={() => void revealLocalPath(snapshot.runtime.archivePath, onNotice)}><FolderOpen size={14} /> Open archive</button><button className="wide" onClick={async () => { try { onNotice(`Index exported to ${await api.exportArchiveIndex()}`); } catch (error) { onNotice(String(error)); } }}><Download size={14} /> Export memory index</button></InspectorSection>
      <InspectorSection title="Conversation sources"><KeyValue label="OpenCore" value={`${snapshot.conversations.filter((item) => item.client.toLowerCase().includes("opencore") || item.client.toLowerCase().includes("unsloth")).length} conversations`} /><KeyValue label="Claude Code" value={`${snapshot.conversations.filter((item) => item.client.toLowerCase().includes("claude")).length} conversations`} /><KeyValue label="Codex" value={`${snapshot.conversations.filter((item) => item.client.toLowerCase().includes("codex")).length} conversations`} /><button className="wide" disabled={Boolean(syncStarting["claude-code"] || syncStarting.codex || ["claude-code", "codex"].some((id) => ["queued", "running"].includes(operationFor(id)?.status || "")))} onClick={() => { void syncHistory("claude-code"); void syncHistory("codex"); }}><RefreshCw size={14} /> {(["claude-code", "codex"].some((id) => ["queued", "running"].includes(operationFor(id)?.status || ""))) ? "Scanning local histories…" : "Sync local histories"}</button><div className="settings-sync-results">{(["claude-code", "codex"] as const).map((id) => { const result = operationFor(id); return result ? <div key={id}><strong>{id === "codex" ? "Codex" : "Claude Code"}</strong><span>{result.status === "failed" ? result.error : result.summary || `${result.phase}${result.total ? ` · ${result.current}/${result.total}` : ""}`}</span></div> : null; })}</div></InspectorSection>
      <InspectorSection title="API & diagnostics"><KeyValue label="Gateway" value={`http://127.0.0.1:${snapshot.runtime.gatewayPort}/v1`} /><KeyValue label="Capture" value="Routed API conversations are saved automatically" /><button className="wide" onClick={() => navigator.clipboard.writeText(`http://127.0.0.1:${snapshot.runtime.gatewayPort}/v1`)}><Copy size={14} /> Copy API endpoint</button><button className="wide" onClick={async () => { try { onNotice(`Diagnostics exported to ${await api.exportDiagnostics()}`); } catch (error) { onNotice(String(error)); } }}><FileDown size={14} /> Export diagnostics</button></InspectorSection>
    </div>
  </div>;

  return <div className="support-page">
    <div className="page-heading"><div><h1>Troubleshooting</h1><p>Run real health checks and export a diagnostic snapshot.</p></div></div>
    <div className="settings-grid">
      <InspectorSection title="Health">
        <KeyValue label="Gateway" value={`127.0.0.1:${snapshot.runtime.gatewayPort}`} />
        <KeyValue label="Backend" value={`127.0.0.1:${snapshot.runtime.backendPort}`} />
        <KeyValue label="ECHO" value={`127.0.0.1:${snapshot.runtime.echoPort}`} />
        <button className="wide" onClick={async () => { try { onNotice(await api.healthCheck()); } catch (error) { onNotice(String(error)); } }}><Activity size={14} /> Run full check</button>
      </InspectorSection>
      <InspectorSection title="Diagnostics">
        <KeyValue label="Credentials" value="Redacted before persistence" />
        <KeyValue label="Conversation storage" value="Local SQLite" />
        <KeyValue label="Logs" value="Persistent, rotating" />
        <button className="wide" onClick={async () => { try { onNotice(`Diagnostics exported to ${await api.exportDiagnostics()}`); } catch (error) { onNotice(String(error)); } }}><FileDown size={14} /> Export diagnostics</button>
      </InspectorSection>
    </div>
  </div>;
}


function compactTokenCount(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 1 })}M`;
  if (value >= 1_000) return `${(value / 1_000).toLocaleString(undefined, { maximumFractionDigits: 1 })}K`;
  return value.toLocaleString();
}

function ContextUsageIndicator({ conversationId, profile, runtime }: { conversationId?: string; profile: RuntimeProfile; runtime: AppSnapshot["runtime"] }) {
  const [workingSet, setWorkingSet] = useState<api.EchoWorkingSet | null>(null);
  useEffect(() => {
    setWorkingSet(null);
    if (!conversationId) return;
    let active = true;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try { const next = await api.echoWorkingSet(conversationId); if (active) setWorkingSet(next); }
      catch { if (active) setWorkingSet(null); }
      finally { pending = false; }
    };
    void refresh();
    if (!["running", "starting"].includes(runtime.status)) return () => { active = false; };
    const timer = window.setInterval(refresh, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [conversationId, profile, runtime.profile, runtime.status]);

  if (["echo", "native1m", "unsloth-echo", "doucode", "nanbeige-bf16-echo", "dualcore-echo", "fusioncore-echo"].includes(profile)) {
    const archived = workingSet?.offloadedMessages;
    const reportedLimit = workingSet?.contextMode === "persistent_echo" && workingSet.windowTokens
      ? Math.min(workingSet.windowTokens, workingSet.modelContextTokens ?? workingSet.windowTokens)
      : workingSet?.available ? workingSet.modelContextTokens ?? workingSet.windowTokens : undefined;
    const fallbackLimit = runtime.contextSize || (profile === "native1m" ? 1_000_000 : profile === "doucode" ? 65_536 : 32768);
    const maximum = Math.max(1, reportedLimit ?? fallbackLimit);
    const liveValue = workingSet?.modelActiveTokens ?? (workingSet?.available ? workingSet.promptTokens ?? workingSet.liveTokens : undefined);
    const measured = typeof liveValue === "number";
    const used = measured ? Math.min(maximum, Math.max(0, liveValue as number)) : 0;
    const usageState = workingSet?.active ? "live" : "last";
    const liveLabel = measured ? `${compactTokenCount(used)} / ${compactTokenCount(maximum)} ${usageState}` : `Live — / ${compactTokenCount(maximum)}`;
    const title = measured
      ? `ECHO ${workingSet?.active ? "live" : "last reported"} model window: ${used.toLocaleString()} / ${maximum.toLocaleString()} tokens. The 3T figure is an unvalidated archive scaling goal, not measured archive capacity; it is not simultaneous model attention.`
      : `ECHO live model window capacity: ${maximum.toLocaleString()} tokens. The 3T figure is an unvalidated archive scaling goal, not measured archive capacity; it is not simultaneous model attention.`;
    return <div className="statusbar-context statusbar-echo-context" aria-label="ECHO context and archive" title={title}>
      <span className="statusbar-context-label">ECHO</span>
      <span className="statusbar-context-reading">
        <span>Model context · {liveLabel}</span>
        <small style={{ display: "block" }}>3T archive goal · unvalidated, not live context</small>
        {archived ? <small style={{ display: "block" }} aria-label="ECHO archived messages">{archived} archived</small> : null}
        {workingSet?.autoCompactEnabled === true && typeof workingSet.autoCompactThreshold === "number" && workingSet.autoCompactThreshold > 0
          ? <small className="statusbar-context-compaction">Auto compact · {workingSet.autoCompactThreshold.toLocaleString()} · {workingSet.compactions ?? 0}</small>
          : null}
      </span>
      <progress aria-label="ECHO model context usage" value={used} max={maximum} />
    </div>;
  }

  const fallbackLimit = runtime.contextSize || 262_144;
  const loaded = ["running", "starting"].includes(runtime.status) && runtime.profile === profile;
  const reportedLimit = workingSet?.available ? workingSet.modelContextTokens || workingSet.windowTokens : undefined;
  const maximum = Math.max(1, reportedLimit || (loaded ? runtime.contextSize : 0) || fallbackLimit);
  const rawUsed = workingSet?.modelActiveTokens;
  const measured = Boolean(workingSet?.available && rawUsed != null);
  const used = measured ? Math.min(maximum, Math.max(0, rawUsed as number)) : 0;
  return <div className="statusbar-context" title={measured ? `${used.toLocaleString()} of ${maximum.toLocaleString()} configured model context tokens in use` : `Configured model context capacity: ${maximum.toLocaleString()} tokens. Waiting for usage telemetry.`}>
    <span className="statusbar-context-label">Context</span>
    <progress aria-label="Model context usage" value={used} max={maximum} />
    <span className="statusbar-context-reading">{measured ? compactTokenCount(used) : "—"} / {compactTokenCount(maximum)}</span>
    {workingSet?.autoCompactEnabled === true && typeof workingSet.autoCompactThreshold === "number" && workingSet.autoCompactThreshold > 0
      ? <small className="statusbar-context-compaction">Auto compact · {workingSet.autoCompactThreshold.toLocaleString()} tokens · {workingSet.compactions ?? 0} compactions</small>
      : null}
  </div>;
}

function StatusbarModelSelector({ selectedProfile, onSelect, disabled }: { selectedProfile: RuntimeProfile; onSelect: (profile: RuntimeProfile) => void; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!rootRef.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { setOpen(false); triggerRef.current?.focus(); } };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  const choose = (profile: Exclude<RuntimeProfile, "stopped" | "unsloth-echo">) => { onSelect(profile); setOpen(false); triggerRef.current?.focus(); };
  return <div className="statusbar-model-picker" ref={rootRef}>
    <button ref={triggerRef} type="button" className={`statusbar-model-trigger ${open ? "open" : ""}`} aria-label={`Choose model profile, currently ${profileLabel(selectedProfile)}`} aria-expanded={open} aria-controls="statusbar-model-profile-options" disabled={disabled} onClick={() => setOpen((value) => !value)}>
      <BrainCircuit size={13} aria-hidden="true" /><strong>{profileLabel(selectedProfile)}</strong><ChevronDown size={12} aria-hidden="true" />
    </button>
    {open && !disabled ? <ModelProfileOptions selectedProfile={selectedProfile} onSelect={choose} id="statusbar-model-profile-options" /> : null}
  </div>;
}

function RuntimeStatusBar({ snapshot, selectedProfile, setSelectedProfile, conversationId, className = "" }: {
  snapshot: AppSnapshot;
  selectedProfile: RuntimeProfile;
  setSelectedProfile: (profile: RuntimeProfile) => void;
  conversationId?: string;
  className?: string;
}) {
  const active = ["running", "starting"].includes(snapshot.runtime.status);
  const currentProfile = active && snapshot.runtime.profile !== "stopped" ? snapshot.runtime.profile : selectedProfile;
  return <footer className={`statusbar ${className}`}>
    <span className="statusbar-state"><StatusDot state={snapshot.runtime.status} />{active ? "Runtime active" : "Runtime stopped"}</span>
    <span>{snapshot.conversations.length} conversations</span>
    <span>Gateway :{snapshot.runtime.gatewayPort}</span>
    <span className="push">GPU {snapshot.telemetry.gpuUtilization}%</span>
    <span>{(snapshot.telemetry.vramUsedMib / 1024).toFixed(1)}GB VRAM</span>
    <span>{snapshot.telemetry.tokensPerSecond.toFixed(1)} tokens/s</span>
    <StatusbarModelSelector selectedProfile={currentProfile} onSelect={setSelectedProfile} disabled={active || Boolean(snapshot.activeConversationIds?.length)} />
    <ContextUsageIndicator conversationId={conversationId} profile={currentProfile} runtime={snapshot.runtime} />
  </footer>;
}

export default function App() {
  const [snapshot, setSnapshot] = useState<AppSnapshot | null>(null);
  const [view, setView] = useState<View>("conversations");
  const [selectedProfile, setSelectedProfileState] = useState<RuntimeProfile>(readProfilePreference);
  const [selectedConversation, setSelectedConversation] = useState<string>();
  const [conversationEpoch, setConversationEpoch] = useState(0);
  const composerDrafts = useRef(new Map<string, ComposerDraft>());
  const draftKey = selectedConversation || `new-${conversationEpoch}`;
  const rememberDraft = useCallback((draft: ComposerDraft) => {
    if (draft.text || draft.files.length) composerDrafts.current.set(draftKey, draft);
    else composerDrafts.current.delete(draftKey);
  }, [draftKey]);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [liveGeneration, setLiveGeneration] = useState<{ conversationId: string; runId: string; content?: string; reasoning?: string; segments?: { kind: "thinking" | "text"; content: string }[]; phase?: string }>();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string>();
  useEffect(() => {
    let disposed = false;
    let stop: (() => void) | undefined;
    const announced = new Set<string>();
    void listen<api.StudioJob>("opencore-studio-job", ({payload}) => {
      if (disposed || !['completed','failed','cancelled'].includes(payload.status)) return;
      const conversation = payload.request?.conversationId;
      if (conversation && conversation === selectedConversationRef.current) void api.conversation(conversation).then(setTimeline).catch(() => {});
      if (announced.has(payload.id)) return;
      announced.add(payload.id);
      const studio = payload.category === 'music' ? 'Music Studio' : 'Assets Studio';
      setNotice(payload.status === 'completed' ? `Generation complete. Open ${studio} to view the output.` : `Generation ${payload.status}. Open ${studio} for details.`);
    }).then(unlisten => { if (disposed) unlisten(); else stop = unlisten; }).catch(() => {});
    return () => { disposed = true; stop?.(); };
  }, []);
  const [runtimeAction, setRuntimeAction] = useState<"starting" | "stopping" | null>(null);
  const [conversationDialog, setConversationDialog] = useState<ConversationDialog>(null);
  const [projectDialog, setProjectDialog] = useState<ProjectDialog>(null);
  const [appearance, setAppearance] = useState<Appearance>(savedAppearance);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    try {
      const saved = Number(window.localStorage.getItem("opencore.sidebar.width"));
      return Number.isFinite(saved) && saved >= 230 && saved <= 600 ? saved : 306;
    } catch { return 306; }
  });
  const sidebarResize = useRef<{ x: number; width: number } | null>(null);
  const selectedConversationRef = useRef<string | undefined>(undefined);
  const initializedSelectionRef = useRef(false);
  const snapshotFingerprintRef = useRef("");

  useEffect(() => { selectedConversationRef.current = selectedConversation; }, [selectedConversation]);
  const setSelectedProfile = useCallback((profile: RuntimeProfile) => {
    if (profile === "stopped") return;
    setSelectedProfileState(profile);
    try { window.localStorage.setItem("opencore.model-profile", profile); } catch { /* The choice remains active for this session. */ }
  }, []);
  useEffect(() => {
    if (!snapshot || ["running", "starting"].includes(snapshot.runtime.status)) return;
    void api.selectProfile(selectedProfile).catch((error) => setNotice(String(error)));
  }, [selectedProfile, snapshot?.runtime.status]);
  useEffect(() => {
    const runtime = snapshot?.runtime;
    if (!runtime || !["running", "starting"].includes(runtime.status) || runtime.profile === "stopped" || runtime.profile === selectedProfile) return;
    setSelectedProfile(runtime.profile);
  }, [snapshot?.runtime.profile, snapshot?.runtime.status, selectedProfile, setSelectedProfile]);
  useEffect(() => { try { window.localStorage.setItem(appearanceKey, JSON.stringify(appearance)); } catch { /* The preference still works for this session. */ } }, [appearance]);
  useEffect(() => { void api.setComputerFocusMode(appearance.keepUserWindowInFront).catch((error: unknown) => setNotice(String(error))); }, [appearance.keepUserWindowInFront]);
  useEffect(() => { try { window.localStorage.setItem("opencore.sidebar.width", String(sidebarWidth)); } catch { /* Session-only layout. */ } }, [sidebarWidth]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(undefined), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const refresh = useCallback(async () => {
    try {
      const next = await api.snapshot();
      const fingerprint = JSON.stringify(next);
      if (fingerprint !== snapshotFingerprintRef.current) {
        snapshotFingerprintRef.current = fingerprint;
        setSnapshot(next);
      }
      if (!initializedSelectionRef.current) {
        initializedSelectionRef.current = true;
        if (!selectedConversationRef.current && next.conversations[0]) {
          selectedConversationRef.current = next.conversations[0].id;
          setSelectedConversation(next.conversations[0].id);
        }
      }
    } catch (error) { setNotice(String(error)); }
  }, []);

  useEffect(() => {
    let stopped = false;
    const tick = async () => { if (!stopped && !document.hidden) await refresh(); };
    tick();
    const timer = window.setInterval(tick, view === "conversations" || view === "models" || view === "runtime" || snapshot?.runtime.status === "starting" ? 2000 : 15000);
    const onVisibility = () => { if (!document.hidden) tick(); };
    document.addEventListener("visibilitychange", onVisibility);
    return () => { stopped = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisibility); };
  }, [refresh, view, snapshot?.runtime.status]);
  useEffect(() => { if (selectedConversation) api.conversation(selectedConversation).then(setTimeline).catch((error) => setNotice(String(error))); }, [selectedConversation]);
  useEffect(() => {
    let disposed = false;
    let stop: (() => void) | undefined;
    void listen<NonNullable<typeof liveGeneration> & { done?: boolean; checkpoint?: boolean }>("opencore-generation", ({ payload }) => {
      if (payload.done) {
        void api.conversation(payload.conversationId).then((entries) => {
          if (disposed) return;
          if (selectedConversationRef.current === payload.conversationId) setTimeline(entries);
          setLiveGeneration((current) => current?.runId === payload.runId ? undefined : current);
        }).catch(() => { setLiveGeneration((current) => current?.runId === payload.runId ? undefined : current); });
      } else if (payload.checkpoint) {
        void api.conversation(payload.conversationId).then((entries) => {
          if (disposed) return;
          if (selectedConversationRef.current === payload.conversationId) setTimeline(entries);
          setLiveGeneration((current) => current?.runId === payload.runId
            ? { ...payload, checkpoint: undefined, segments: [], content: "", reasoning: "" } : current);
        }).catch(() => {});
      } else { setLiveGeneration(payload); }
    }).then((unlisten) => { if (disposed) unlisten(); else stop = unlisten; }).catch(() => {});
    return () => { disposed = true; stop?.(); };
  }, []);
  const visibleTimeline = useMemo(() => {
    if (!liveGeneration || liveGeneration.conversationId !== selectedConversation) return timeline;
    const entries = [...timeline];
    const preview = (id: number, kind: string, content: string): TimelineEntry => ({
      id, kind, content, conversationId: selectedConversation, timestamp: new Date().toISOString(),
      role: "assistant", source: "OpenCore", title: "Live generation", metadata: { live: true, phase: liveGeneration.phase },
    });
    const segments = liveGeneration.segments?.length ? liveGeneration.segments : [
      ...(liveGeneration.reasoning ? [{ kind: "thinking" as const, content: liveGeneration.reasoning }] : []),
      ...(liveGeneration.content ? [{ kind: "text" as const, content: liveGeneration.content }] : []),
    ];
    for (const [index, segment] of segments.entries()) {
      if (!segment.content.trim() || segment.content.trimStart().startsWith("<echo>")) continue;
      entries.push(preview(-2 - index, segment.kind === "thinking" ? "thinking" : "message", segment.content));
    }
    return entries;
  }, [timeline, liveGeneration, selectedConversation]);
  const selectedActive = Boolean(selectedConversation && snapshot?.activeConversationIds?.includes(selectedConversation));
  useEffect(() => {
    if (!selectedConversation || !selectedActive) return;
    let live = true;
    const update = () => { void api.conversation(selectedConversation).then((entries) => { if (live) setTimeline(entries); }).catch(() => {}); };
    update();
    const timer = window.setInterval(update, 1200);
    return () => { live = false; window.clearInterval(timer); };
  }, [selectedConversation, selectedActive]);

  const selected = useMemo(() => snapshot?.conversations.find((item) => item.id === selectedConversation), [snapshot, selectedConversation]);
  const act = async (operation: () => Promise<unknown>): Promise<boolean> => { setBusy(true); setNotice(undefined); try { await operation(); await refresh(); return true; } catch (error) { setNotice(String(error)); return false; } finally { setBusy(false); } };
  const start = async () => {
    setRuntimeAction("starting"); setNotice(undefined);
    try { await api.startProfile(selectedProfile); await refresh(); }
    catch (error) { if (!String(error).includes("Runtime loading stopped")) setNotice(String(error)); }
    finally { setRuntimeAction((current) => current === "starting" ? null : current); }
  };
  const stop = async () => {
    setRuntimeAction("stopping");
    try { await api.stopRuntime(); await refresh(); }
    catch (error) { setNotice(String(error)); }
    finally { setRuntimeAction(null); }
  };
  const restart = () => act(api.restartRuntime);
  const exportCurrent = () => selectedConversation ? act(async () => setNotice(`Exported to ${await api.exportConversation(selectedConversation, "markdown")}`)) : setNotice("Select a conversation to export.");
  const renameCurrent = () => {
    if (!selectedConversation || !selected) return setNotice("Select a conversation to rename.");
    setConversationDialog({ kind: "rename", value: selected.title });
  };
  const deleteCurrent = () => {
    if (!selectedConversation || !selected) return setNotice("Select a conversation to delete.");
    setConversationDialog({ kind: "delete" });
  };
  const togglePinned = () => selectedConversation && selected ? act(() => api.setConversationPinned(selectedConversation, !selected.pinned)) : Promise.resolve();
  const toggleRowPinned = (item: ConversationSummary) => { void act(() => api.setConversationPinned(item.id, !item.pinned)); };
  const moveCurrentToProject = (projectId: string | null) => selectedConversation ? act(() => api.moveConversationToProject(selectedConversation, projectId)) : Promise.resolve();
  const createProject = (name: string, folderPath: string) => act(() => api.createProject(name, folderPath));
  const createProjectForCurrent = (name: string, folderPath: string) => act(async () => {
    const created = await api.createProject(name, folderPath);
    if (selectedConversation) await api.moveConversationToProject(selectedConversation, created.id);
  });
  const openProjectFolder = async (project: ProjectSummary): Promise<string> => {
    if (!project.folderPath) throw new Error("Link a folder to this project first.");
    await api.openLocalPath(project.folderPath);
    return "Opened in Windows Explorer";
  };
  const changeProjectFolder = async (project: ProjectSummary): Promise<string> => {
    const folderPath = await api.chooseProjectFolder();
    if (!folderPath) return "Folder selection cancelled";
    await api.changeProjectFolder(project.id, folderPath);
    await refresh();
    return `Linked to ${folderPath}`;
  };
  const confirmProjectDialog = () => {
    if (!projectDialog) return;
    const { project } = projectDialog;
    if (projectDialog.kind === "rename") {
      const name = projectDialog.value.trim();
      if (!name) return;
      setProjectDialog(null);
      void act(() => api.renameProject(project.id, name));
    } else {
      setProjectDialog(null);
      void act(async () => { const count = await api.deleteProject(project.id); setNotice(`Deleted ${project.name}. ${count} conversation${count === 1 ? "" : "s"} kept without a project.`); });
    }
  };
  const confirmConversationDialog = () => {
    if (!conversationDialog || !selectedConversation || !selected) return;
    if (conversationDialog.kind === "rename") {
      const next = conversationDialog.value.trim();
      setConversationDialog(null);
      if (next && next !== selected.title) void act(() => api.renameConversation(selectedConversation, next));
      return;
    }
    const id = selectedConversation;
    setConversationDialog(null);
    void act(async () => {
      await api.removeConversation(id);
      setSelectedConversation(undefined);
      selectedConversationRef.current = undefined;
      setTimeline([]);
      setConversationEpoch((value) => value + 1);
      snapshotFingerprintRef.current = "";
    });
  };

  const selectConversation = (id: string) => {
    selectedConversationRef.current = id;
    setSelectedConversation(id);
    setConversationEpoch((value) => value + 1);
  };

  const acceptConversationId = (id: string) => {
    selectedConversationRef.current = id;
    setSelectedConversation(id);
  };

  const newChat = () => {
    selectedConversationRef.current = undefined;
    setSelectedConversation(undefined);
    setTimeline([]);
    setConversationEpoch((value) => value + 1);
    setNotice(undefined);
  };

  const refreshConversation = async () => {
    await refresh();
    const id = selectedConversationRef.current;
    if (id) {
      try { setTimeline(await api.conversation(id)); }
      catch (error) { setNotice(String(error)); }
    }
  };

  if (!snapshot) return <div className="app-window-frame"><WindowTitleBar /><div className="splash"><span className="brand-mark splash-logo"><img src={opencoreLogo} alt="OpenCore" /></span><strong>OpenCore</strong><p>Loading runtime state…</p></div></div>;
  const running = snapshot.runtime.status === "running";
  const appearanceStyle = { "--chat-font-size": `${appearance.chatFontSize}px`, "--terminal-font-size": `${appearance.terminalFontSize}px` } as CSSProperties;
  const defaultSkills = [
    appearance.defaultComputerUse ? "computer-use" : null,
    appearance.defaultBrowserUse ? "browser-use" : null,
    appearance.defaultChromeControl ? "chrome-control" : null,
  ].filter(Boolean) as ("computer-use" | "browser-use" | "chrome-control")[];

  if (view === "conversations") {
    const conversationList = <ConversationsList conversations={snapshot.conversations} projects={snapshot.projects} selected={selectedConversation} onSelect={selectConversation} onNew={newChat} onExit={() => setView("overview")} onCreateProject={createProject} onTogglePin={toggleRowPinned} onEditProject={(project) => setProjectDialog({ kind: "rename", project, value: project.name })} onRemoveProject={(project) => setProjectDialog({ kind: "delete", project })} onOpenProjectFolder={openProjectFolder} onChangeProjectFolder={changeProjectFolder} />;
    return <div className="app-window-frame"><WindowTitleBar /><div className={`conversation-focus-shell ${appearance.compactMessages ? "compact-messages" : ""}`} style={{ ...appearanceStyle, gridTemplateColumns: `58px ${sidebarWidth}px 7px minmax(0,1fr)`, gridTemplateRows: "minmax(0,1fr) 36px" }}>
      <Navigation active={view} onChange={setView} running={running} compact />
      {conversationList}<div className="conversation-resizer" role="separator" aria-label="Resize conversations" aria-orientation="vertical" onPointerDown={(event) => { sidebarResize.current = { x: event.clientX, width: sidebarWidth }; event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={(event) => { if (sidebarResize.current) setSidebarWidth(Math.min(600, Math.max(230, sidebarResize.current.width + event.clientX - sidebarResize.current.x))); }} onPointerUp={(event) => { sidebarResize.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} />
      <AssistantConversation
        key={`chat-${conversationEpoch}`}
        conversationId={selectedConversation}
        initialDraft={composerDrafts.current.get(draftKey)}
        onDraftChange={rememberDraft}
        title={selected?.title || "New conversation"}
        client={selected?.client || "OpenCore"}
        entries={visibleTimeline}
        runtimeRunning={running}
        runtimeSnapshot={snapshot.runtime}
        telemetry={snapshot.telemetry}
        selectedProfile={selectedProfile}
        onSelectProfile={setSelectedProfile}
        liveTokenSpeed={recentDecoderSpeed(snapshot.logs)}
        promptProgress={recentPromptProgress(snapshot.logs)}
        backendActive={snapshot.activeConversationIds?.includes(selectedConversation || "") || false}
        onConversationId={acceptConversationId}
        onRefresh={refreshConversation}
        onNotice={setNotice}
        onExport={exportCurrent}
        onRename={renameCurrent}
        onDelete={deleteCurrent}
        pinned={selected?.pinned || false}
        project={selected?.project || ""}
        projectId={selected?.projectId || null}
        projects={snapshot.projects}
        onPin={togglePinned}
        onMoveProject={moveCurrentToProject}
        onCreateProject={createProjectForCurrent}
        defaultSkills={defaultSkills}
        subagentsEnabled={appearance.subagentsEnabled}
        maxSubagents={appearance.maxSubagents}
        projectSkillsEnabled={appearance.projectSkillsEnabled}
        compactAtTokens={appearance.compactAtTokens}
      />
      <RuntimeStatusBar snapshot={snapshot} selectedProfile={selectedProfile} setSelectedProfile={setSelectedProfile} conversationId={selectedConversation} className="conversation-statusbar" />
      {notice && <div className="toast conversation-toast"><CircleAlert size={17} /><span>{notice}{/Open Models and choose Install|Install this model from the Models tab|GGUF not found:/i.test(notice) && <button className="model-install-action" onClick={() => setView("models")}>Open Models</button>}</span><button onClick={() => setNotice(undefined)}><X size={15} /></button></div>}
      {conversationDialog && <OpenCoreDialog dialog={conversationDialog} title={selected?.title || "This conversation"} onChange={(value) => setConversationDialog({ kind: "rename", value })} onCancel={() => setConversationDialog(null)} onConfirm={confirmConversationDialog} />}
      {projectDialog && <ProjectEditDialog dialog={projectDialog} onChange={(value) => setProjectDialog((current) => current?.kind === "rename" ? { ...current, value } : current)} onCancel={() => setProjectDialog(null)} onConfirm={confirmProjectDialog} />}
    </div></div>;
  }

  return <div className="app-window-frame" style={appearanceStyle}><WindowTitleBar /><div className="app-shell">
    <Header snapshot={snapshot} busy={busy} runtimeAction={runtimeAction} selectedProfile={selectedProfile} setSelectedProfile={setSelectedProfile} onStart={start} onStop={stop} onRestart={restart} onExport={exportCurrent} />
    <Navigation active={view} onChange={setView} running={running} />
    {view === "runtime"
      ? <RuntimeView snapshot={snapshot} selectedProfile={selectedProfile} setSelectedProfile={setSelectedProfile} runtimeAction={runtimeAction} actions={{ start, stop, restart, navigate: setView, notice: setNotice }} />
      : view === 'music' ? <MusicStudio runtimeActive={running} onNotice={setNotice} />
      : view === 'assets' ? <AssetsStudio onNotice={setNotice} />
      : <SupportingView view={view} snapshot={snapshot} selectedProfile={selectedProfile} onSelectProfile={setSelectedProfile} selectedConversation={selectedConversation} onNotice={setNotice} onRefresh={refresh} onNavigate={setView} appearance={appearance} onAppearanceChange={setAppearance} />}
    <RuntimeStatusBar snapshot={snapshot} selectedProfile={selectedProfile} setSelectedProfile={setSelectedProfile} conversationId={selectedConversation} />
    {notice && <div className="toast"><CircleAlert size={17} /><span>{notice}{/Open Models and choose Install|Install this model from the Models tab|GGUF not found:/i.test(notice) && <button className="model-install-action" onClick={() => setView("models")}>Open Models</button>}</span><button onClick={() => setNotice(undefined)}><X size={15} /></button></div>}
  </div></div>;
}
