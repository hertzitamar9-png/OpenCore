import { createContext, memo, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  ActionBarPrimitive,
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import type { ThreadMessageLike } from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";
import { open } from "@tauri-apps/plugin-dialog";
import { listen } from "@tauri-apps/api/event";
import {
  AppWindow, BrainCircuit, ChevronDown, Copy, FileDown, Globe2, Paperclip, Pencil, Pin, PinOff, Send, ShieldCheck, Square, Trash2, X,
  CheckCircle2, CircleAlert, Code2, CornerUpLeft, FilePenLine, FileSearch, FileText, FolderOpen, Gamepad2, Terminal, Wrench, Zap
} from "lucide-react";
import * as api from "./api";
import { messageUrlTransform, parseArtifactLink } from "./artifact-links";
import { sanitizeMessageMarkdown } from "./message-markdown";
import { COMPOSER_SKILLS, filterComposerSkills, resolveSlashSkill, type ComposerSkillId } from "./composer-skills";
import { formatMessageTimestamp } from "./message-time";
import { groupConversationTurns, type ConversationTurn } from "./conversation-turns";
import { buildResponseSegments, type ToolStep } from "./response-segments";
import { ProjectPicker } from "./ProjectPicker";
import { NativeBrowserPanel } from "./NativeBrowserPanel";
import { DesktopPanel } from "./DesktopPanel";
import { FloatingWindow } from "./FloatingWindow";
import type { ApprovalMode, ChatQueueItem, ProjectSummary, ReasoningEffort, RuntimeSnapshot, TelemetrySnapshot, TimelineEntry } from "./types";

const REASONING_MODES: { value: ReasoningEffort; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "extra-high", label: "Extra high" },
  { value: "max", label: "Max" },
  { value: "opencore", label: "OpenCore" },
];

const APPROVAL_MODES: { value: ApprovalMode; label: string; short: string; detail: string }[] = [
  { value: "ask-every-time", label: "Ask every time", short: "Ask", detail: "Ask before each project tool action." },
  { value: "approve-for-me", label: "Approve for me", short: "Auto", detail: "Review safe project reads and searches automatically." },
  { value: "allow-chat", label: "Allow everything in this chat", short: "Chat", detail: "Allow available tools for this chat." },
  { value: "allow-all", label: "Allow everything", short: "All", detail: "Allow available tools across chats on this computer." },
];

function savedApprovalMode(conversationId?: string): ApprovalMode {
  try {
    if (conversationId && window.sessionStorage.getItem(`opencore.approval-chat.${conversationId}`) === "allow-chat") return "allow-chat";
    return window.localStorage.getItem("opencore.approval-global.v1") === "allow-all" ? "allow-all" : "ask-every-time";
  } catch { return "ask-every-time"; }
}

type ToolApprovalRequest = { requestId: string; conversationId: string; name: string; arguments: string };

function savedReasoningEffort(): ReasoningEffort {
  try {
    const saved = window.localStorage.getItem("opencore.reasoning-effort.v1");
    return REASONING_MODES.find((mode) => mode.value === saved)?.value ?? "medium";
  } catch { return "medium"; }
}

type Props = {
  conversationId?: string;
  title: string;
  client: string;
  entries: TimelineEntry[];
  runtimeRunning: boolean;
  runtimeSnapshot: RuntimeSnapshot;
  telemetry: TelemetrySnapshot;
  liveTokenSpeed: number | null;
  promptProgress: { label: string; speed: number | null } | null;
  backendActive: boolean;
  onConversationId: (id: string) => void;
  onRefresh: () => Promise<void>;
  onNotice: (message: string) => void;
  onExport: () => void;
  onRename: () => void;
  onDelete: () => void;
  pinned: boolean;
  project: string;
  projectId: string | null;
  projects: ProjectSummary[];
  onPin: () => void;
  onMoveProject: (projectId: string | null) => void;
  onCreateProject: (name: string, folderPath: string) => Promise<boolean>;
};

function displayText(value: string) {
  const visible = value
    .replace(/<oai-mem-citation>[\s\S]*?<\/oai-mem-citation>/gi, "")
    .replace(/<recommended_plugins>[\s\S]*?<\/recommended_plugins>/gi, "");
  return sanitizeMessageMarkdown(visible).trim();
}

function convertTurn(turn: ConversationTurn, active: boolean): ThreadMessageLike {
  const entry = turn.entries[0];
  const response = turn.role === "assistant" &&
    ["thinking", "progress", "tool_call", "tool_result", "message", "echo", "file", "error"].includes(entry.kind);
  const visible = response
    ? turn.entries.filter((item) => item.kind === "message" && item.role === "assistant").map((item) => displayText(item.content)).filter(Boolean).join("\n\n")
    : displayText(entry.content);
  return {
    role: turn.role,
    content: [{ type: "text", text: visible || " " }],
    metadata: { custom: { source: entry.source, timestamp: entry.timestamp, kind: response ? "response" : entry.kind,
      title: entry.title, raw: visible, details: entry.metadata, events: response ? turn.entries : undefined,
      active: response && active && !turn.entries.some((item) => item.kind === "message" && item.role === "assistant") } },
  };
}

type ArtifactActions = { preview: (id: string) => void; download: (id: string) => void; remoteImage: (url: string) => void };
const ArtifactActionsContext = createContext<ArtifactActions | null>(null);

function MessageImage({ src, alt }: { src?: string; alt?: string }) {
  const actions = useContext(ArtifactActionsContext);
  const artifact = parseArtifactLink(src);
  const [imageUrl, setImageUrl] = useState(artifact ? "" : src || "");
  useEffect(() => {
    if (!artifact || artifact.action !== "preview") { setImageUrl(src || ""); return; }
    let active = true;
    api.previewArtifact(artifact.id).then((item) => { if (active && item.mime.startsWith("image/")) setImageUrl(item.dataUrl); }).catch(() => {});
    return () => { active = false; };
  }, [src]);
  if (!imageUrl) return <span className="inline-image-loading">{alt || "Image"}</span>;
  return <button type="button" className="inline-image-button" aria-label={`Preview ${alt || "image"}`} onClick={() => artifact ? actions?.preview(artifact.id) : src && actions?.remoteImage(src)}>
    <img src={imageUrl} alt={alt || "Image"} loading="lazy" />
  </button>;
}

type AttachedFile = { name: string; path?: string; artifactId?: string };

function attachedFiles(value: unknown): AttachedFile[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item): AttachedFile[] => {
    if (typeof item === "string") return [{ name: item.split(/[\\/]/).pop() || "Attachment", path: item }];
    if (!item || typeof item !== "object") return [];
    const file = item as Record<string, unknown>;
    if (typeof file.name !== "string") return [];
    return [{ name: file.name, path: typeof file.path === "string" ? file.path : undefined,
      artifactId: typeof file.artifactId === "string" ? file.artifactId : undefined }];
  });
}

function AttachedFilePreview({ file, onRemove }: { file: AttachedFile; onRemove?: () => void }) {
  const actions = useContext(ArtifactActionsContext);
  const isImage = /\.(png|jpe?g|gif|webp)$/i.test(file.name);
  const [imageUrl, setImageUrl] = useState("");
  useEffect(() => {
    if (!isImage) return;
    let active = true;
    const preview = file.artifactId
      ? api.previewArtifact(file.artifactId).then((item) => item.dataUrl)
      : file.path ? api.previewAttachmentImage(file.path) : Promise.resolve("");
    preview.then((url) => { if (active) setImageUrl(url); }).catch(() => { if (active) setImageUrl(""); });
    return () => { active = false; };
  }, [file.artifactId, file.path, isImage]);
  return <div className={`attachment-preview ${imageUrl ? "has-image" : ""}`}>
    {imageUrl ? <button type="button" className="attachment-preview-image" aria-label={`Preview ${file.name}`} onClick={() => file.artifactId ? actions?.preview(file.artifactId) : actions?.remoteImage(imageUrl)}><img src={imageUrl} alt={file.name} /></button> : <FileText size={18} aria-hidden="true" />}
    <span title={file.name}>{file.name}</span>
    {onRemove ? <button type="button" className="attachment-remove" aria-label={`Remove ${file.name}`} onClick={onRemove}><X size={13} /></button> : null}
  </div>;
}

function GeneratedArtifact({ id, name, mime, size }: { id: string; name: string; mime?: string; size?: number }) {
  const actions = useContext(ArtifactActionsContext);
  return <div className="generated-artifact">
    {mime?.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(name) ? <MessageImage src={`artifact://${id}`} alt={name} /> : null}
    <div className="artifact-card"><FileText size={21} /><div><strong>{name}</strong><small>{typeof size === "number" ? `${Math.ceil(size / 1024)} KB` : "File"}</small></div><button type="button" onClick={() => actions?.preview(id)}>Preview</button><button type="button" onClick={() => actions?.download(id)}><FileDown size={14} /> Download</button></div>
  </div>;
}

function MarkdownText() {
  const actions = useContext(ArtifactActionsContext);
  return <MarkdownTextPrimitive remarkPlugins={[remarkGfm]} className="aui-md" urlTransform={messageUrlTransform} components={{
    a: ({ href, children }) => {
      const artifact = parseArtifactLink(href);
      return artifact ? <a href={href} onClick={(event) => { event.preventDefault(); artifact.action === "download" ? actions?.download(artifact.id) : actions?.preview(artifact.id); }}>{children}</a> : <a href={href}>{children}</a>;
    },
    img: ({ src, alt }) => <MessageImage src={src} alt={alt} />,
  }} />;
}

function toolPayload(raw: string): Record<string, unknown> {
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    const nested = value.function && typeof value.function === "object" ? (value.function as Record<string, unknown>).arguments : value.arguments ?? value.input;
    if (typeof nested === "string") return JSON.parse(nested) as Record<string, unknown>;
    if (nested && typeof nested === "object") return nested as Record<string, unknown>;
    return value;
  } catch { return {}; }
}

function toolSummary(title = "", raw = "", result = false) {
  const payload = toolPayload(raw);
  const action = String(payload.action || "");
  const probe = `${title} ${raw.slice(0, 500)}`.toLowerCase();
  if (result) return payload.error ? { label: "Action failed", icon: CircleAlert } : { label: "Tool completed", icon: CheckCircle2 };
  if (title === "browser_use" || title === "chrome_use") {
    const surface = title === "chrome_use" ? "Chrome" : "OpenCore Browser";
    const verb = ({ open: "Opening", navigate: "Opening", inspect: "Checking", read_screen: "Reading", click: "Clicking in", click_text: "Clicking in", type: "Typing in", key: "Pressing a key in", scroll: "Scrolling", back: "Going back in", forward: "Going forward in", reload: "Reloading" } as Record<string, string>)[action] || "Using";
    return { label: `${verb} ${surface}`, icon: Globe2 };
  }
  if (title === "desktop_use") return { label: action === "list" ? "Checking open windows" : action === "read_screen" ? "Reading the screen" : action === "navigate_url" ? "Opening a page in Chrome" : action === "click" || action === "interact" ? "Clicking in a window" : "Controlling a window", icon: AppWindow };
  if (title === "system_use") return { label: action === "find_apps" ? "Finding an app" : action === "launch_app" ? "Opening an app" : "Running a command", icon: Terminal };
  if (title === "reflex_use") return action === "play_snake" ? { label: "Played Snake", icon: Gamepad2 } : { label: "Found a control", icon: Zap };
  if (probe.includes("apply_patch") || probe.includes("patch")) return { label: "Patched files", icon: FilePenLine };
  if (probe.includes("write") || probe.includes("edit")) return { label: "Edited files", icon: FilePenLine };
  if (probe.includes("grep") || probe.includes("rg ") || probe.includes("search") || probe.includes("find")) return { label: "Searched the project", icon: FileSearch };
  if (probe.includes("read") || probe.includes("get-content") || probe.includes("open")) return { label: "Read files", icon: FileText };
  if (probe.includes("exec") || probe.includes("run") || probe.includes("command") || probe.includes("shell")) return { label: "Ran a command", icon: Terminal };
  return { label: `Used ${title || "a tool"}`, icon: Wrench };
}

function recordedToolNarration(title: string, raw: string): string {
  const action = String(toolPayload(raw).action || "");
  if (title === "desktop_use") {
    if (action === "list") return "I'll check which Windows apps are open.";
    if (["inspect", "read_screen", "screenshot"].includes(action)) return "I'll inspect the selected window before acting.";
    return "I'll use the selected window and check what changed.";
  }
  if (title === "browser_use") return action === "inspect" || action === "read_screen"
    ? "I'll inspect OpenCore Browser before acting." : "I'll use OpenCore Browser and check the page.";
  if (title === "chrome_use") return "I'll use the paired Chrome tab and check the page.";
  if (title === "system_use") {
    if (action === "find_apps") return "I'll look for the installed app.";
    if (action === "launch_app") return "I'll open the selected app.";
    return "I'll run the command and check its output.";
  }
  if (title === "reflex_use") return action === "play_snake" ? "I'll play the game live with Reflex." : "I'll find the right control with Reflex.";
  if (title === "read_project_file") return "I'll read the selected project file.";
  if (title === "search_project") return "I'll search the project and check the matches.";
  return "I'll run the next tool and check its result.";
}

function toolTarget(raw: string) {
  try {
    const payload = toolPayload(raw);
    const candidate = payload.path ?? payload.cmd ?? payload.query ?? payload.pattern ?? payload.url ?? payload.text ?? payload.prompt;
    if (typeof candidate === "string") return candidate.replace(/\s+/g, " ").slice(0, 150);
  } catch { /* Non-JSON tool text is still available in details. */ }
  return "";
}

function ToolActivity({ kind, title, raw, resultRaw }: { kind: string; title?: string; raw: string; resultRaw?: string }) {
  const failed = resultRaw ? !!toolPayload(resultRaw).error : false;
  const presentation = failed ? { label: "Action failed", icon: CircleAlert } : toolSummary(title, raw, kind === "tool_result");
  const Icon = presentation.icon;
  const target = toolTarget(raw);
  const pretty = (value: string) => { try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; } };
  const details = resultRaw ? `Action\n${pretty(raw)}\n\nResult\n${pretty(resultRaw)}` : pretty(raw);
  return <details className={`tool-activity ${kind === "tool_result" ? "tool-result" : ""} ${resultRaw ? "completed" : ""} ${failed ? "failed" : ""}`}>
    <summary><Icon size={15} /><strong>{presentation.label}</strong>{target ? <span>{target}</span> : null}{resultRaw ? <CheckCircle2 size={13} className="tool-status-icon" /> : null}<em>Details</em></summary>
    <pre>{details}</pre>
  </details>;
}

function EchoReceipt({ entry }: { entry: TimelineEntry }) {
  const artifact = entry.metadata.artifact && typeof entry.metadata.artifact === "object" ? entry.metadata.artifact as Record<string, unknown> : null;
  return <details className="echo-storage-card"><summary><BrainCircuit size={15} /><strong>{artifact?.status === "complete" ? "Saved to ECHO" : "ECHO memory"}</strong><span>{typeof artifact?.words === "number" ? `${artifact.words} words archived` : "Memory receipt available"}</span></summary><pre>{displayText(entry.content).trim() || "No receipt text saved"}{Object.keys(entry.metadata).length ? `\n\nReceipt metadata\n${JSON.stringify(entry.metadata, null, 2)}` : ""}</pre></details>;
}

function ReasoningDisclosure({ entries, active }: { entries: TimelineEntry[]; active: boolean }) {
  const [expanded, setExpanded] = useState(active);
  useEffect(() => { setExpanded(active); }, [active]);
  return <details className="assistant-disclosure kind-thinking" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary><BrainCircuit size={14} /><strong>{active ? "Reasoning" : "Reasoned"}</strong><span>{active ? "Working…" : "Show thinking"}</span></summary>
    <div className="reasoning-text">{entries.map((entry) => displayText(entry.content).trim()).filter(Boolean).join("\n\n")}</div>
  </details>;
}

function ToolGroup({ steps, active }: { steps: ToolStep[]; active: boolean }) {
  const [expanded, setExpanded] = useState(false);
  if (steps.length === 1) {
    const [{ call, result }] = steps;
    return <div className="tool-step"><ToolActivity kind={call.kind} title={call.title} raw={call.content} resultRaw={result?.content} /></div>;
  }
  const labels = Array.from(new Set(steps.map(({ call }) => toolSummary(call.title, call.content, call.kind === "tool_result").label)));
  const failed = steps.some(({ result }) => result && !!toolPayload(result.content).error);
  return <details className={`tool-group ${active ? "active" : ""} ${failed ? "failed" : ""}`} open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary><Wrench size={15} /><strong>{active ? "Using tools" : `Used ${steps.length} tools`}</strong><span>{labels.join(" · ")}</span><em>{expanded ? "Hide" : "Show"}</em></summary>
    <ol className="tool-chain">{steps.map(({ call, result }) => <li key={call.id} className={result ? "done" : active ? "running" : ""}>
      <ToolActivity kind={call.kind} title={call.title} raw={call.content} resultRaw={result?.content} />
    </li>)}</ol>
  </details>;
}

function ResponseActivity({ events, active }: { events: TimelineEntry[]; active: boolean }) {
  const hasAnswer = events.some((entry) => entry.kind === "message" && entry.role === "assistant");
  const latest = events.filter((entry) => entry.kind !== "echo").at(-1);
  const working = active && !hasAnswer;
  return <div className="assistant-response">
    {buildResponseSegments(events).map((segment) => segment.type === "reasoning"
      ? <ReasoningDisclosure key={segment.key} entries={segment.entries} active={working && latest?.kind === "thinking" && segment.entries.includes(latest)} />
      : segment.type === "narration"
        ? <p key={segment.key} className="assistant-progress">{displayText(segment.entry.content)}</p>
      : segment.type === "inferred"
        ? <p key={segment.key} className="assistant-progress inferred" title="Action summary from the recorded tool call">{recordedToolNarration(segment.call.title, segment.call.content)}</p>
      : segment.type === "tools"
        ? <ToolGroup key={segment.key} steps={segment.steps} active={working && segment.steps.some(({ call, result }) => call === latest && !result)} />
      : segment.entry.kind === "file" && typeof segment.entry.metadata.id === "string"
        ? <GeneratedArtifact key={segment.key} id={segment.entry.metadata.id} name={segment.entry.title || "Generated file"} mime={typeof segment.entry.metadata.mime === "string" ? segment.entry.metadata.mime : undefined} size={typeof segment.entry.metadata.size === "number" ? segment.entry.metadata.size : undefined} />
      : segment.entry.kind === "error"
        ? <details key={segment.key} className="assistant-disclosure kind-error" open><summary><Code2 size={14} /><strong>Error</strong><span>{segment.entry.title}</span></summary><div>{displayText(segment.entry.content)}</div></details>
      : null)}
    {hasAnswer ? <div className="assistant-response-answer"><MessagePrimitive.Parts components={{ Text: MarkdownText }} /></div> : null}
    {events.filter((entry) => entry.kind === "echo").map((entry) => <EchoReceipt key={entry.id} entry={entry} />)}
  </div>;
}

const ChatMessage = memo(function ChatMessage() {
  const role = useAuiState((state) => state.message.role);
  const custom = useAuiState((state) => state.message.metadata.custom) as { source?: string; timestamp?: string; kind?: string; title?: string; raw?: string; details?: Record<string, unknown>; events?: TimelineEntry[]; active?: boolean } | undefined;
  const time = formatMessageTimestamp(custom?.timestamp);
  const user = role === "user";
  const kind = custom?.kind || "message";
  const artifactId = typeof custom?.details?.id === "string" && parseArtifactLink(`artifact://${custom.details.id}`) ? custom.details.id : null;
  const files = user ? attachedFiles(custom?.details?.files) : [];
  const structured = kind === "tool_call" || kind === "tool_result" || kind === "thinking" || kind === "error" || kind === "echo_import" || kind === "echo";
  const importTotal = Number(custom?.details?.total) || 0;
  const importCurrent = Number(custom?.details?.current) || 0;
  const echoArtifact = custom?.details?.artifact && typeof custom.details.artifact === "object" ? custom.details.artifact as Record<string, unknown> : null;

  return <MessagePrimitive.Root className={`aui-message ${user ? "aui-user-message" : "aui-assistant-message"}`}>
    {user ? <div className="aui-user-column">
      <div className="aui-message-meta"><time>{time}</time></div>
      <div className="aui-user-bubble"><MessagePrimitive.Parts components={{ Text: MarkdownText }} />{files.length > 0 ? <div className="message-attachments">{files.map((file, index) => <AttachedFilePreview key={`${file.path || file.artifactId || file.name}-${index}`} file={file} />)}</div> : null}</div>
      <MessageActions />
    </div> : <>
      <div className="aui-avatar"><img src="/opencore-logo.png" alt="" /></div>
      <div className="aui-assistant-column">
        <div className="aui-message-meta"><strong>OpenCore</strong><time>{time}</time></div>
        <div className="aui-assistant-content">{kind === "response" && custom?.events
          ? <ResponseActivity events={custom.events} active={!!custom.active} />
          : kind === "file" && artifactId
          ? <GeneratedArtifact id={artifactId} name={custom?.title || "Generated file"} mime={typeof custom?.details?.mime === "string" ? custom.details.mime : undefined} size={typeof custom?.details?.size === "number" ? custom.details.size : undefined} />
          : structured
          ? kind === "echo_import"
            ? <div className={`echo-import-card ${custom?.details?.status || "indexing"}`} role="status">
              <strong>{custom?.details?.status === "ready" ? "Imported history ready" : custom?.details?.status === "failed" ? "Import needs attention" : "Preparing ECHO history"}</strong>
              <span>{custom?.raw}</span>
              {importTotal > 0 ? <progress aria-label="ECHO import progress" value={importCurrent} max={importTotal} /> : null}
            </div>
          : kind === "echo"
            ? <details className="echo-storage-card"><summary><BrainCircuit size={15} /><strong>{echoArtifact?.status === "complete" ? "Saved to ECHO" : "ECHO memory"}</strong><span>{typeof echoArtifact?.words === "number" ? `${echoArtifact.words} words archived` : "Memory receipt available"}</span></summary><pre>{custom?.raw?.trim() || "No receipt text saved"}{custom?.details && Object.keys(custom.details).length ? `\n\nReceipt metadata\n${JSON.stringify(custom.details, null, 2)}` : ""}</pre></details>
          : kind === "tool_call" || kind === "tool_result"
            ? <ToolActivity kind={kind} title={custom?.title} raw={custom?.raw || ""} />
          : <details className={`assistant-disclosure kind-${kind}`} open={kind === "error" ? true : undefined}><summary><Code2 size={14} /><strong>{kind === "thinking" ? "Reasoning" : "Error"}</strong><span>{custom?.title}</span></summary><div><MessagePrimitive.Parts components={{ Text: MarkdownText }} /></div></details>
          : <MessagePrimitive.Parts components={{ Text: MarkdownText }} />}</div>
        <MessageActions />
      </div>
    </>}
  </MessagePrimitive.Root>;
});

function MessageActions() {
  return <ActionBarPrimitive.Root className="aui-message-actions">
    <ActionBarPrimitive.Copy asChild>
      <button title="Copy message"><Copy size={13} /> Copy</button>
    </ActionBarPrimitive.Copy>
  </ActionBarPrimitive.Root>;
}
export const AssistantConversation = memo(function AssistantConversation({
  conversationId, title, client, entries, runtimeRunning, runtimeSnapshot, telemetry, liveTokenSpeed, promptProgress, backendActive,
  onConversationId, onRefresh, onNotice, onExport, onRename, onDelete,
  pinned, project, projectId, projects, onPin, onMoveProject, onCreateProject,
}: Props) {
  const [draft, setDraft] = useState("");
  const [selectedSkills, setSelectedSkills] = useState<ComposerSkillId[]>([]);
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const [files, setFiles] = useState<string[]>([]);
  const [queue, setQueue] = useState<ChatQueueItem[]>([]);
  const [sending, setSending] = useState(false);
  const [active, setActive] = useState<ChatQueueItem | null>(null);
  const [optimistic, setOptimistic] = useState<TimelineEntry[]>([]);
  const [liveEntries, setLiveEntries] = useState<TimelineEntry[]>([]);
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>(savedReasoningEffort);
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>(() => savedApprovalMode(conversationId));
  const [controlOpen, setControlOpen] = useState<"effort" | "approval" | null>(null);
  const [approvalPreviewIndex, setApprovalPreviewIndex] = useState<number | null>(null);
  const approvalPreviewRef = useRef<number | null>(null);
  const [confirmApproval, setConfirmApproval] = useState<ApprovalMode | null>(null);
  const [pendingTool, setPendingTool] = useState<ToolApprovalRequest | null>(null);
  const [artifactPreview, setArtifactPreview] = useState<api.ArtifactPreview | { remoteImage: string } | null>(null);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [nativeBrowserOpen, setNativeBrowserOpen] = useState(false);
  const [browserFull, setBrowserFull] = useState(() => { try { return window.localStorage.getItem("opencore.browser.full") !== "false"; } catch { return true; } });
  const [browserWidth, setBrowserWidth] = useState(() => { try { return Number(window.localStorage.getItem("opencore.browser.width")) || 580; } catch { return 580; } });
  const [browserSide, setBrowserSide] = useState<"left" | "right">(() => { try { return window.localStorage.getItem("opencore.browser.side") === "left" ? "left" : "right"; } catch { return "right"; } });
  const [browserSnap, setBrowserSnap] = useState(() => { try { return Number(window.localStorage.getItem("opencore.browser.snap")) || 24; } catch { return 24; } });
  const [desktopOpen, setDesktopOpen] = useState(false);
  const generationActive = sending || backendActive;
  const pendingToolRef = useRef<ToolApprovalRequest | null>(null);
  useEffect(() => {
    let dispose: (() => void) | undefined;
    if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
      void listen("opencore-open-native-browser", () => setNativeBrowserOpen(true)).then((unlisten) => { dispose = unlisten; }).catch(() => {});
    }
    return () => dispose?.();
  }, []);
  useEffect(() => { try { window.localStorage.setItem("opencore.browser.width", String(browserWidth)); window.localStorage.setItem("opencore.browser.side", browserSide); window.localStorage.setItem("opencore.browser.snap", String(browserSnap)); window.localStorage.setItem("opencore.browser.full", String(browserFull)); } catch { /* Layout works for this session. */ } }, [browserWidth, browserSide, browserSnap, browserFull]);
  const controlsRef = useRef<HTMLDivElement>(null);
  const effortIndex = REASONING_MODES.findIndex((mode) => mode.value === reasoningEffort);
  const effortLabel = REASONING_MODES[effortIndex].label;
  const approvalLabel = APPROVAL_MODES.find((mode) => mode.value === approvalMode)?.label;
  const approvalShort = APPROVAL_MODES.find((mode) => mode.value === approvalMode)?.short;
  const approvalIndex = APPROVAL_MODES.findIndex((mode) => mode.value === approvalMode);
  const shownApprovalIndex = approvalPreviewIndex ?? approvalIndex;

  const artifactActions: ArtifactActions = {
    preview: (id) => {
      setArtifactLoading(true);
      void api.previewArtifact(id).then((item) => { setArtifactPreview(item); setNativeBrowserOpen(true); }).catch((error: unknown) => onNotice(`Could not preview file: ${String(error)}`)).finally(() => setArtifactLoading(false));
    },
    download: (id) => { void api.downloadArtifact(id).then((path) => onNotice(`Downloaded to ${path}`)).catch((error: unknown) => onNotice(`Could not download file: ${String(error)}`)); },
    remoteImage: (url) => { setArtifactPreview({ remoteImage: url }); setNativeBrowserOpen(true); },
  };

  const chooseEffort = (index: number) => {
    const chosen = REASONING_MODES[Math.max(0, Math.min(6, index))].value;
    setReasoningEffort(chosen);
    try { window.localStorage.setItem("opencore.reasoning-effort.v1", chosen); } catch { /* Session-only setting. */ }
  };

  const queueRef = useRef<ChatQueueItem[]>([]);
  const sendingRef = useRef(false);
  const conversationRef = useRef<string | undefined>(conversationId);
  const steerNextRef = useRef<ChatQueueItem | null>(null);
  const stopRequestedRef = useRef(false);
  const runPromptRef = useRef<((item: ChatQueueItem) => Promise<void>) | null>(null);

  conversationRef.current = conversationId ?? conversationRef.current;
  pendingToolRef.current = pendingTool;

  useEffect(() => () => {
    if (pendingToolRef.current) void api.resolveToolApproval(pendingToolRef.current.requestId, false).catch(() => {});
  }, []);

  useEffect(() => {
    if (!controlOpen) return;
    const outside = (event: PointerEvent) => {
      if (controlsRef.current && !controlsRef.current.contains(event.target as Node) && !(event.target as HTMLElement).closest("#effort-panel,#approval-panel")) setControlOpen(null);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setControlOpen(null); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [controlOpen]);

  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void listen<ToolApprovalRequest>("opencore-tool-approval-request", (event) => {
      if (!disposed && event.payload.conversationId === conversationRef.current) setPendingTool(event.payload);
    }).then((stop) => { if (disposed) stop(); else unlisten = stop; });
    return () => { disposed = true; unlisten?.(); };
  }, []);

  const chooseApprovalMode = (mode: ApprovalMode) => {
    approvalPreviewRef.current = null;
    setApprovalPreviewIndex(null);
    if (mode === "allow-chat" || mode === "allow-all") {
      setConfirmApproval(mode);
      setControlOpen(null);
      return;
    }
    setApprovalMode(mode);
    setControlOpen(null);
    try { window.localStorage.removeItem("opencore.approval-global.v1");
      if (conversationRef.current) window.sessionStorage.removeItem(`opencore.approval-chat.${conversationRef.current}`);
    } catch { /* This chat still uses the selected mode. */ }
  };
  const commitApprovalRange = () => {
    const index = approvalPreviewRef.current;
    approvalPreviewRef.current = null;
    setApprovalPreviewIndex(null);
    if (index !== null && index !== approvalIndex) chooseApprovalMode(APPROVAL_MODES[index].value);
  };

  const acceptApprovalMode = () => {
    if (!confirmApproval) return;
    setApprovalMode(confirmApproval);
    try {
      if (confirmApproval === "allow-all") window.localStorage.setItem("opencore.approval-global.v1", "allow-all");
      else {
        window.localStorage.removeItem("opencore.approval-global.v1");
        if (conversationRef.current) window.sessionStorage.setItem(`opencore.approval-chat.${conversationRef.current}`, "allow-chat");
      }
    } catch { /* Keep this chat's selection in memory. */ }
    setConfirmApproval(null);
  };

  const answerToolApproval = async (approved: boolean) => {
    if (!pendingTool) return;
    const requestId = pendingTool.requestId;
    setPendingTool(null);
    try { await api.resolveToolApproval(requestId, approved); }
    catch (error) { onNotice(`Could not answer the tool approval: ${String(error)}`); }
  };

  useEffect(() => {
    if (!sending) return;
    const id = conversationRef.current;
    if (!id) return;
    let cancelled = false;
    let busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      try {
        const latest = await api.conversation(id);
        if (!cancelled && latest.length) setLiveEntries(latest);
      } catch { /* A transient read failure must not interrupt generation. */ }
      finally { busy = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [sending, conversationId]);

  const visibleEntries = useMemo(() => {
    const persisted = liveEntries.length ? liveEntries : entries;
    return [...persisted, ...optimistic.filter((pending) => !persisted.some((entry) =>
      entry.role === pending.role && entry.content === pending.content &&
      Math.abs(new Date(entry.timestamp).valueOf() - new Date(pending.timestamp).valueOf()) < 30000
    ))];
  }, [entries, liveEntries, optimistic]);
  const messages = useMemo(() => {
    const turns = groupConversationTurns(visibleEntries);
    return turns.map((turn, index) => convertTurn(turn, generationActive && index === turns.length - 1));
  }, [visibleEntries, generationActive]);
  const chatTurnCount = entries.filter((entry) => entry.kind === "message" && (entry.role === "user" || entry.role === "assistant")).length;
  const activityCount = entries.length - chatTurnCount;
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: (message) => message,
    onNew: async () => {},
  });

  const setQueueBoth = (items: ChatQueueItem[]) => {
    queueRef.current = items;
    setQueue(items);
  };

  const runPrompt = async (item: ChatQueueItem) => {
    if (sendingRef.current) return;
    stopRequestedRef.current = false;
    let id = conversationRef.current;
    if (!id) {
      id = `opencore:${crypto.randomUUID()}`;
      conversationRef.current = id;
      if (item.approvalMode === "allow-chat") {
        try { window.sessionStorage.setItem(`opencore.approval-chat.${id}`, "allow-chat"); } catch { /* This chat still uses the selected mode. */ }
      }
      onConversationId(id);
    }

    sendingRef.current = true;
    setSending(true);
    setLiveEntries([]);
    setActive(item);
    const optimisticEntry: TimelineEntry = {
      id: -Date.now(),
      conversationId: id,
      timestamp: new Date().toISOString(),
      kind: "message",
      role: "user",
      source: "OpenCore",
      title: "You",
      content: item.text || `Attached ${item.files.length} file(s)`,
      metadata: { files: item.files },
    };
    setOptimistic([optimisticEntry]);
    let failed = false;
    let interrupted = false;

    try {
      await api.sendChatMessage(id, item.text, item.files, item.reasoningEffort, item.approvalMode, item.skills);
      await onRefresh();
      setLiveEntries([]);
    } catch (error) {
      if (String(error).includes("__INTERRUPTED_BEFORE_SAVE__")) {
        interrupted = true;
        setDraft((current) => current.trim() ? current : item.text);
        setFiles((current) => Array.from(new Set([...current, ...item.files])));
        setSelectedSkills((current) => Array.from(new Set([...current, ...item.skills])));
      }
      else if (String(error).includes("__INTERRUPTED__")) interrupted = true;
      else {
        failed = true;
        setDraft((current) => current.trim() ? current : item.text);
        setFiles((current) => Array.from(new Set([...current, ...item.files])));
        setSelectedSkills((current) => Array.from(new Set([...current, ...item.skills])));
        onNotice(`Message not sent. Your draft is ready to retry. ${String(error)}`);
      }
      await onRefresh();
      setLiveEntries([]);
    } finally {
      setOptimistic([]);
      setActive(null);
      sendingRef.current = false;
      setSending(false);

      let next = steerNextRef.current;
      if (next) {
        steerNextRef.current = null;
      } else if (!interrupted && !stopRequestedRef.current && queueRef.current.length) {
        next = queueRef.current[0];
        setQueueBoth(queueRef.current.slice(1));
      }
      if (next && !failed) queueMicrotask(() => runPromptRef.current?.(next!));
      if (next && failed) setQueueBoth([next, ...queueRef.current]);
    }
  };
  runPromptRef.current = runPrompt;
  useEffect(() => {
    if (!backendActive && !sendingRef.current && steerNextRef.current) {
      const next = steerNextRef.current;
      steerNextRef.current = null;
      void runPromptRef.current?.(next);
    }
  }, [backendActive]);

  const submit = () => {
    if (backendActive && !sendingRef.current) return;
    const text = draft.trim();
    if (!text && files.length === 0) return;
    const item: ChatQueueItem = { id: crypto.randomUUID(), text, files: [...files], reasoningEffort, approvalMode, skills: [...selectedSkills] };
    setDraft("");
    setFiles([]);
    setSelectedSkills([]);
    setSkillPickerOpen(false);
    if (sendingRef.current) {
      setQueueBoth([...queueRef.current, item]);
    } else {
      void runPromptRef.current?.(item);
    }
  };

  const chooseFiles = async () => {
    const selected = await open({ multiple: true, directory: false });
    if (!selected) return;
    const paths = Array.isArray(selected) ? selected : [selected];
    setFiles((current) => Array.from(new Set([...current, ...paths])));
  };

  const removeQueued = (id: string) => {
    setQueueBoth(queueRef.current.filter((item) => item.id !== id));
  };

  const steer = async (item: ChatQueueItem) => {
    setQueueBoth(queueRef.current.filter((queued) => queued.id !== item.id));
    stopRequestedRef.current = false;
    if (!sendingRef.current && !backendActive) { void runPromptRef.current?.(item); return; }
    steerNextRef.current = item;
    if (conversationRef.current) {
      await api.cancelChatMessage(conversationRef.current);
    }
  };

  const stop = async () => {
    steerNextRef.current = null;
    stopRequestedRef.current = true;
    if (conversationRef.current) {
      try { await api.cancelChatMessage(conversationRef.current); }
      catch (error) { onNotice(`Could not stop the response: ${String(error)}`); }
      await onRefresh();
    }
  };
  const matchingSkills = skillPickerOpen ? filterComposerSkills(draft) : [];
  const selectSkill = (id: ComposerSkillId) => {
    setSelectedSkills((current) => current.includes(id) ? current : [...current, id]);
    setDraft((current) => resolveSlashSkill(current, id));
    setSkillPickerOpen(false);
  };
  return <main className={`assistant-thread-panel chat-mode ${nativeBrowserOpen ? "browser-open" : ""} ${nativeBrowserOpen && browserFull ? "browser-full" : ""} ${browserSide === "left" ? "browser-left" : ""}`} style={nativeBrowserOpen ? { "--browser-width": `${browserWidth}px` } as React.CSSProperties : undefined}>
    <div className="timeline-heading compact">
      <div className="chat-title">
        <h2>{title || "New conversation"}</h2>
        <span>{client || "OpenCore"} · {chatTurnCount} messages{activityCount ? ` · ${activityCount} activities` : ""}</span>
      </div>
      <div className="conversation-actions">
        <button className="workspace-open-button" onClick={() => setNativeBrowserOpen((open) => !open)} title="OpenCore Browser" aria-label="OpenCore Browser"><Globe2 size={16} /><span>Browse</span></button>
        <button className="workspace-open-button" onClick={() => setDesktopOpen((open) => !open)} title="Computer use" aria-label="Computer use"><AppWindow size={16} /><span>Computer</span></button>
        {conversationId ? /claude|codex/i.test(client) ? <span className="source-project-locked" title="Linked to the source project folder"><FolderOpen size={15} /> {projects.find((item) => item.id === projectId)?.name || project || client}</span> : <ProjectPicker value={projectId} legacyName={project && !projectId ? project : undefined} projects={projects} onChange={onMoveProject} onCreate={onCreateProject} /> : null}
        {conversationId ? <button onClick={onPin} title={pinned ? "Unpin conversation" : "Pin conversation"} aria-label={pinned ? "Unpin conversation" : "Pin conversation"}>{pinned ? <PinOff size={16} /> : <Pin size={16} />}</button> : null}
        <button onClick={onExport} title="Export" aria-label="Export conversation"><FileDown size={16} /></button>
        <button onClick={onRename} title="Rename" aria-label="Rename conversation"><Pencil size={16} /></button>
        <button className="danger-action" onClick={onDelete} title="Delete" aria-label="Delete conversation"><Trash2 size={16} /></button>
      </div>
    </div>

    {nativeBrowserOpen ? <NativeBrowserPanel onClose={() => setNativeBrowserOpen(false)} onNotice={onNotice} preview={artifactPreview} onDownload={artifactActions.download} full={browserFull} onFullChange={setBrowserFull} width={browserWidth} onWidthChange={setBrowserWidth} side={browserSide} onSideChange={setBrowserSide} snapPx={browserSnap} onSnapChange={setBrowserSnap} obscured={controlOpen !== null} /> : null}
    {desktopOpen ? <DesktopPanel onClose={() => setDesktopOpen(false)} onNotice={onNotice} /> : null}

    <ArtifactActionsContext.Provider value={artifactActions}><AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="aui-thread-root">
        <ThreadPrimitive.Viewport className="aui-thread-viewport">
          <ThreadPrimitive.Empty>
            <div className="empty-state large chat-empty">
              <img src="/opencore-logo.png" alt="" />
              <strong>Start a new OpenCore conversation</strong>
              <span>Messages can run for long agentic tasks using the loaded model and its native context system.</span>
            </div>
          </ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages components={{ Message: ChatMessage }} />
          {generationActive && <div className="generation-state">
            <span className="generation-pulse" />
            <span>{runtimeSnapshot.status === "starting" ? `${runtimeSnapshot.loadingPhase || "Loading model and ECHO"} · ${((runtimeSnapshot.loadingElapsedMs || 0) / 1000).toFixed(1)}s` : promptProgress?.label || (runtimeRunning ? "OpenCore is working" : "Preparing model and ECHO…")}{active?.files.length ? ` · ${active.files.length} attachment(s)` : ""}</span>
            {runtimeSnapshot.status === "starting" ? <progress aria-label="Model loading" /> : null}
          </div>}
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider></ArtifactActionsContext.Provider>

    <div className="chat-composer-wrap">
      {matchingSkills.length > 0 ? <div className="composer-skill-menu" role="listbox" aria-label="Skills">{matchingSkills.map((skill) => <button type="button" role="option" aria-selected={selectedSkills.includes(skill.id)} key={skill.id} onClick={() => selectSkill(skill.id)}><strong>/{skill.id}</strong><small>{skill.description}</small></button>)}</div> : null}
      {queue.length > 0 && <div className="prompt-queue">
        <div className="queue-heading"><strong>Queued</strong><span>{queue.length}</span></div>
        {queue.map((item, index) => <div className="queue-item" key={item.id}>
          <span className="queue-index">{index + 1}</span>
          <div><strong>{item.text || "Attachments"}</strong><small>{item.files.length ? `${item.files.length} file(s)` : "Waiting"}</small></div>
          <button onClick={() => steer(item)} title="Interrupt current response and send this next"><CornerUpLeft size={13} /> Steer</button>
          <button className="queue-remove" onClick={() => removeQueued(item.id)} title="Remove from queue"><X size={13} /></button>
        </div>)}
      </div>}

      {files.length > 0 && <div className="attachment-row">
        {files.map((path) => <AttachedFilePreview key={path} file={{ name: path.split(/[\\/]/).pop() || "Attachment", path }} onRemove={() => setFiles((current) => current.filter((value) => value !== path))} />)}
      </div>}
      {selectedSkills.length > 0 ? <div className="composer-skill-chips" aria-label="Selected skills">{selectedSkills.map((id) => <span key={id}>{COMPOSER_SKILLS.find((skill) => skill.id === id)?.label}<button type="button" aria-label={`Remove ${id} skill`} onClick={() => setSelectedSkills((current) => current.filter((item) => item !== id))}><X size={12} /></button></span>)}</div> : null}

      <div className="chat-composer" ref={controlsRef}>
        <button className="attach-button" onClick={chooseFiles} title="Attach files"><Paperclip size={18} /></button>
        <textarea
          aria-label="Message OpenCore"
          value={draft}
          onChange={(event) => { setDraft(event.target.value); setSkillPickerOpen(event.target.value.startsWith("/")); }}
          onKeyDown={(event) => {
            if (event.key === "Escape" && skillPickerOpen) { event.preventDefault(); setSkillPickerOpen(false); return; }
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (matchingSkills.length) { selectSkill(matchingSkills[0].id); return; }
              submit();
            }
          }}
          placeholder="Message OpenCore…"
          rows={1}
        />
        <div className="composer-controls">
          <button type="button" className={`composer-control-button approval-trigger ${controlOpen === "approval" ? "active" : ""}`} aria-label={`Approval: ${approvalLabel}`} aria-expanded={controlOpen === "approval"} aria-controls="approval-panel" onClick={() => setControlOpen((open) => open === "approval" ? null : "approval")}>
            <ShieldCheck size={16} /><span className="control-copy"><small>Approval</small><strong>{approvalShort}</strong></span><ChevronDown size={13} />
          </button>
          <button type="button" className={`composer-control-button effort-trigger effort-${effortIndex} ${controlOpen === "effort" ? "active" : ""}`} aria-label={`Effort: ${effortLabel}`} aria-expanded={controlOpen === "effort"} aria-controls="effort-panel" onClick={() => setControlOpen((open) => open === "effort" ? null : "effort")}>
            <BrainCircuit size={16} /><span className="control-copy"><small>Effort</small><strong>{effortLabel}</strong></span><ChevronDown size={13} />
          </button>
          {controlOpen === "effort" ? <FloatingWindow id="effort-compact" domId="effort-panel" title="Effort" icon={<BrainCircuit size={16} />} className={`composer-popover effort-popover effort-${effortIndex}`} onClose={() => setControlOpen(null)} place="composer" initialWidth={440} initialHeight={160} minWidth={280} minHeight={145} maximizable={false} ariaLabel="Effort settings">
            <div className="effort-bar">
              <div className="effort-rail">
                <div className="effort-segments">{REASONING_MODES.map((mode, index) => <span key={mode.value} className={index <= effortIndex ? "lit" : ""} />)}</div>
                <input className="effort-range" type="range" min="0" max="6" step="1" value={effortIndex} aria-label="Reasoning effort" aria-valuetext={effortLabel} onChange={(event) => chooseEffort(Number(event.target.value))} />
              </div>
            </div>
            <div className="effort-labels" aria-hidden="true">{REASONING_MODES.map((mode) => <span key={mode.value}>{mode.label}</span>)}</div>
          </FloatingWindow> : null}
          {controlOpen === "approval" ? <FloatingWindow id="approval" domId="approval-panel" title="Approval" icon={<ShieldCheck size={16} />} className="composer-popover approval-popover" onClose={() => setControlOpen(null)} place="composer" initialWidth={510} initialHeight={160} minWidth={280} minHeight={140} maximizable={false} ariaLabel="Approval settings">
            <div className="approval-bar" role="group" aria-label="Approval mode"><div className="approval-rail"><div className="approval-segments" aria-hidden="true">{APPROVAL_MODES.map((mode, index) => <span key={mode.value} className={index <= shownApprovalIndex ? "lit" : ""} />)}</div><input className="approval-range" type="range" min="0" max="3" step="1" value={shownApprovalIndex} aria-label="Approval level" aria-valuetext={APPROVAL_MODES[shownApprovalIndex].label} onChange={(event) => { const index = Number(event.target.value); approvalPreviewRef.current = index; setApprovalPreviewIndex(index); }} onPointerUp={commitApprovalRange} onKeyUp={commitApprovalRange} onBlur={commitApprovalRange} onPointerCancel={() => { approvalPreviewRef.current = null; setApprovalPreviewIndex(null); }} /></div><div className="approval-labels">{APPROVAL_MODES.map((mode) => <button type="button" key={mode.value} aria-pressed={mode.value === approvalMode} onClick={() => chooseApprovalMode(mode.value)}>{mode.label}</button>)}</div></div>
          </FloatingWindow> : null}
        </div>
        <button className={`send-button ${generationActive ? "is-stop" : ""}`} onClick={generationActive ? () => void stop() : submit} disabled={!generationActive && !draft.trim() && files.length === 0} title={generationActive ? "Stop" : "Send"} aria-label={generationActive ? "Stop generation" : "Send message"}>
          {generationActive ? <Square size={17} fill="currentColor" /> : <Send size={18} />}
        </button>
      </div>
      <div className="composer-hint"><span>{generationActive ? "OpenCore is working · press Stop to cancel" : "Enter to send · Shift+Enter for a new line"}</span><span>OpenCore can make mistakes. Check important results.</span><span className="composer-token-speed">{generationActive && promptProgress?.speed ? `${promptProgress.speed.toFixed(1)} input tokens/s` : (liveTokenSpeed ?? telemetry.tokensPerSecond) > 0 ? `${(liveTokenSpeed ?? telemetry.tokensPerSecond).toFixed(1)} output tokens/s` : "Waiting for token metrics"} · {(telemetry.totalCompletionTokens ?? telemetry.completionTokens).toLocaleString()} output tokens</span></div>
    </div>
    {confirmApproval ? <><div className="modal-backdrop" /><FloatingWindow id="approval-confirm" title="Approval" ariaLabel={`${APPROVAL_MODES.find((mode) => mode.value === confirmApproval)?.label}?`} icon={<ShieldCheck size={17} />} onClose={() => setConfirmApproval(null)} place="center" modal className="opencore-modal dialog-floating approval-confirm" initialWidth={490} initialHeight={290} minWidth={350} minHeight={220}>
      <h2 id="approval-confirm-title">{APPROVAL_MODES.find((mode) => mode.value === confirmApproval)?.label}?</h2>
      <p>OpenCore may use available tools {confirmApproval === "allow-chat" ? "in this chat" : "across chats"} without asking first, including browser control and generated files. Review the model's actions before continuing.</p>
      <div className="modal-actions"><button onClick={() => setConfirmApproval(null)}>Cancel</button><button className="danger" onClick={acceptApprovalMode}>Approve</button></div>
    </FloatingWindow></> : null}
    {pendingTool ? <><div className="modal-backdrop" /><FloatingWindow id="tool-approval" title="Tool action" icon={<ShieldCheck size={17} />} onClose={() => void answerToolApproval(false)} place="center" modal className="opencore-modal dialog-floating tool-approval-dialog" initialWidth={520} initialHeight={320} minWidth={370} minHeight={240}>
      <h2 id="tool-approval-title">Approve this tool?</h2>
      <p>OpenCore wants to use <strong>{pendingTool.name}</strong> in this chat.</p>
      <pre>{pendingTool.arguments}</pre>
      <div className="modal-actions"><button onClick={() => void answerToolApproval(false)}>Deny</button><button className="primary" onClick={() => void answerToolApproval(true)}>Approve</button></div>
    </FloatingWindow></> : null}
    {artifactLoading ? <div className="artifact-loading" role="status">Opening preview…</div> : null}
  </main>;
});
