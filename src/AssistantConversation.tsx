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
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { open } from "@tauri-apps/plugin-dialog";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import {
  AppWindow, ArrowLeft, BrainCircuit, ChevronDown, ChevronRight, Copy, FileDown, Globe2, Paperclip, Pencil, Pin, PinOff, Plus, Send, ShieldCheck, Square, Trash2, X,
  CheckCircle2, CircleAlert, Code2, CornerUpLeft, Crosshair, Eye, FilePenLine, FileSearch, FileText, FolderOpen, Gamepad2, MousePointerClick, Terminal, Wrench, Zap
} from "lucide-react";
import * as api from "./api";
import { messageUrlTransform, parseArtifactLink, studioLinkCategory } from "./artifact-links";
import { sanitizeMessageMarkdown } from "./message-markdown";
import { localFilePath } from "./local-file-links";
import { COMPOSER_SKILLS, filterComposerSkills, resolveSlashSkill, availableComposerSkills, exactSlashSkill, type ComposerSkillId } from "./composer-skills";
import { formatMessageTimestamp } from "./message-time";
import { removePersistedOptimisticDuplicates } from "./visible-entries";
import { groupConversationTurns, type ConversationTurn } from "./conversation-turns";
import { buildResponseSegments, visibleEchoReceiptGroups, type ResponseSegment, type ToolStep } from "./response-segments";
import { ProjectPicker } from "./ProjectPicker";
import { NativeBrowserPanel } from "./NativeBrowserPanel";
import { DesktopPanel } from "./DesktopPanel";
import { FloatingWindow } from "./FloatingWindow";
import { SpeechButton } from "./SpeechButton";
import { ModelProfileOptions, profileLabel } from "./ModelProfiles";
import type { ApprovalMode, ChatQueueItem, ProjectSummary, ReasoningEffort, RuntimeProfile, RuntimeSnapshot, TelemetrySnapshot, TimelineEntry } from "./types";

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
  { value: "ask-every-time", label: "Ask every time", short: "Ask", detail: "Ask before every tool action, including computer controls." },
  { value: "approve-for-me", label: "Approve for me", short: "Auto", detail: "Allow reads automatically; ask before edits, commands, and computer actions." },
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

export type ComposerDraft = { text: string; files: string[] };
type Props = {
  conversationId?: string;
  initialDraft?: ComposerDraft;
  onDraftChange?: (draft: ComposerDraft) => void;
  title: string;
  client: string;
  entries: TimelineEntry[];
  runtimeRunning: boolean;
  runtimeSnapshot: RuntimeSnapshot;
  telemetry: TelemetrySnapshot;
  selectedProfile: RuntimeProfile;
  onSelectProfile: (profile: RuntimeProfile) => void;
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
  defaultSkills: ComposerSkillId[];
  subagentsEnabled: boolean;
  maxSubagents: number;
  projectSkillsEnabled: boolean;
  compactAtTokens: number;
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
      active: response && active } },
  };
}

type ArtifactActions = { preview: (id: string) => void; previewAttachment: (path: string) => void; download: (id: string) => void; remoteImage: (url: string) => void };
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

function filesFromTransfer(transfer: DataTransfer | null): File[] {
  if (!transfer) return [];
  if (transfer.files.length) return Array.from(transfer.files);
  return Array.from(transfer.items)
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file): file is File => file !== null);
}

const LARGE_TEXT_PASTE_THRESHOLD = 12_000;
const MAX_PASTED_TEXT_ATTACHMENT_BYTES = 32 * 1024 * 1024;

function transferHasFiles(transfer: DataTransfer | null): boolean {
  return !!transfer && (Array.from(transfer.types).includes("Files") || filesFromTransfer(transfer).length > 0);
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
  const openPreview = () => {
    if (file.artifactId) actions?.preview(file.artifactId);
    else if (file.path) actions?.previewAttachment(file.path);
  };
  return <div className={`attachment-preview ${imageUrl ? "has-image" : ""}`}>
    <button type="button" className={`attachment-preview-open ${imageUrl ? "has-image" : ""}`} aria-label={`Preview ${file.name}`} onClick={openPreview}>
      {imageUrl ? <img src={imageUrl} alt={file.name} /> : <FileText size={18} aria-hidden="true" />}
      <span title={file.name}>{file.name}</span>
    </button>
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

function MessageLink({ href, children }: { href?: string; children?: React.ReactNode }) {
  const actions = useContext(ArtifactActionsContext);
  const artifact = parseArtifactLink(href);
  const path = localFilePath(href);
  const studio=studioLinkCategory(href);
  if(studio) return <a href={href} onClick={event=>{event.preventDefault();event.stopPropagation();window.dispatchEvent(new CustomEvent('opencore-open-studio',{detail:studio}));}}>{children}</a>;
  if (artifact || path) {
    const open = (event: React.MouseEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (path) actions?.previewAttachment(path);
      else if (artifact) artifact.action === "download" ? actions?.download(artifact.id) : actions?.preview(artifact.id);
    };
    return <a href={href} data-opencore-file-link={path ? "" : undefined} onClick={open} onAuxClick={open}>{children}</a>;
  }
  // A filtered URL must not become href="": that reloads the app and loses chat selection.
  return href ? <a href={href}>{children}</a> : <span>{children}</span>;
}

function MarkdownText() {
  return <MarkdownTextPrimitive remarkPlugins={[remarkGfm]} className="aui-md" urlTransform={messageUrlTransform} components={{
    a: MessageLink,
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
  title = title.replace(/^mcp__opencore__/, "");
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
  if (title === "reflex_use") return ({
    see: { label: "Looked at the screen", icon: Eye },
    ground: { label: "Located a target", icon: Crosshair },
    ground_click: { label: "Clicked a target", icon: MousePointerClick },
    play_snake: { label: "Played Snake", icon: Gamepad2 },
  } as Record<string, { label: string; icon: typeof Zap }>)[action] ?? { label: "Found a control", icon: Zap };
  if (probe.includes("apply_patch") || probe.includes("patch")) return { label: "Patched files", icon: FilePenLine };
  if (probe.includes("write") || probe.includes("edit")) return { label: "Edited files", icon: FilePenLine };
  if (probe.includes("grep") || probe.includes("rg ") || probe.includes("search") || probe.includes("find")) return { label: "Searched the project", icon: FileSearch };
  if (probe.includes("read") || probe.includes("get-content") || probe.includes("open")) return { label: "Read files", icon: FileText };
  if (probe.includes("exec") || probe.includes("run") || probe.includes("command") || probe.includes("shell")) return { label: "Ran a command", icon: Terminal };
  return { label: `Used ${title || "a tool"}`, icon: Wrench };
}

function toolTarget(raw: string) {
  try {
    const payload = toolPayload(raw);
    const candidate = payload.path ?? payload.file_path ?? payload.command ?? payload.cmd ?? payload.query ?? payload.pattern ?? payload.url ?? payload.text ?? payload.prompt;
    if (typeof candidate === "string") return candidate.replace(/\s+/g, " ").slice(0, 150);
  } catch { /* Non-JSON tool text is still available in details. */ }
  return "";
}

function ToolActivity({ kind, title, raw, resultRaw }: { kind: string; title?: string; raw: string; resultRaw?: string }) {
  const outcome = resultRaw ? toolPayload(resultRaw) : {};
  const failed = !!outcome.error || (typeof outcome.exitCode === "number" && outcome.exitCode !== 0);
  const presentation = failed ? { label: "Action failed", icon: CircleAlert } : toolSummary(title, raw, kind === "tool_result");
  const Icon = presentation.icon;
  const target = toolTarget(raw);
  const pretty = (value: string) => { try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; } };
  const details = resultRaw ? `Action\n${pretty(raw)}\n\nResult\n${pretty(resultRaw)}` : pretty(raw);
  return <details className={`tool-activity ${kind === "tool_result" ? "tool-result" : ""} ${resultRaw ? "completed" : ""} ${failed ? "failed" : ""}`}>
    <summary><Icon size={15} /><strong>{presentation.label}</strong><span title={target}>{target}</span>{resultRaw ? failed ? <CircleAlert size={13} className="tool-status-icon" /> : <CheckCircle2 size={13} className="tool-status-icon" /> : <span />}<em>Details</em></summary>
    <pre>{details}</pre>
  </details>;
}

export function EchoReceipt({ entries }: { entries: TimelineEntry[] }) {
  const artifacts = entries.map((entry) => entry.metadata.artifact && typeof entry.metadata.artifact === "object" ? entry.metadata.artifact as Record<string, unknown> : null);
  const allSaved = artifacts.length > 0 && artifacts.every((artifact) => artifact?.status === "complete");
  const words = artifacts.reduce((sum, artifact) => sum + (typeof artifact?.words === "number" ? artifact.words : 0), 0);
  const summary = entries.length > 1 ? `${entries.length} updates combined` : words ? `${words} words archived` : "Memory receipt available";
  const receiptText = entries.map((entry, index) => {
    const title = entry.title.trim() || `ECHO update ${index + 1}`;
    const content = displayText(entry.content).trim() || "No receipt text saved";
    const metadata = Object.keys(entry.metadata).length ? `Receipt metadata\n${JSON.stringify(entry.metadata, null, 2)}` : "";
    return [entries.length > 1 ? `${index + 1}. ${title}` : "", content, metadata].filter(Boolean).join("\n\n");
  }).join("\n\n────────────────────────\n\n");
  return <details className="echo-storage-card"><summary><BrainCircuit size={15} /><strong>{allSaved ? "Saved to ECHO" : "ECHO memory"}</strong><span>{summary}</span></summary><pre>{receiptText}</pre></details>;
}

function ResponseMarkdown({ content }: { content: string }) {
  return <div className="aui-md"><ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={messageUrlTransform} components={{
    a: MessageLink,
    img: ({ src, alt }) => <MessageImage src={src} alt={alt} />,
  }}>{displayText(content)}</ReactMarkdown></div>;
}

function actionSummary(step: ToolStep): string {
  const payload = toolPayload(step.call.content);
  const action = String(payload.action || "");
  const target = toolTarget(step.call.content).split(/[\\/]/).pop() || "the current task";
  const tool = (step.call.title || "").replace(/^mcp__opencore__/, "");
  if (tool === "dev" && action === "read") return `I’ll read ${target} to inspect the current version before changing it.`;
  if (tool === "dev" && ["edit", "patch", "apply_patch"].includes(action)) return `I’ll update ${target} and check the result.`;
  if (tool === "dev" && action === "write") return `I’ll create ${target} in the workspace and verify it.`;
  const label = toolSummary(step.call.title, step.call.content).label.toLowerCase();
  return target === "the current task" ? `I’ll ${label} and check what happened.` : `I’ll ${label} for ${target} and check what happened.`;
}

function reasoningStepSummary(events: TimelineEntry[], segments: ResponseSegment[], index: number): string {
  const next = segments[index + 1];
  if (next?.type === "narration") return displayText(next.entry.content).trim();
  const step = next?.type === "tools" ? next.steps[0] : next?.type === "inferred" ? { call: next.call } : undefined;
  if (!step) return "I’ll use the completed steps to prepare the response.";

  const reasoning = segments[index];
  const reasoningId = reasoning?.type === "reasoning" ? reasoning.entries[0]?.id : undefined;
  const reasoningPosition = events.findIndex((entry) => entry.id === reasoningId);
  const previousFailure = reasoningPosition < 0 ? undefined : events.slice(0, reasoningPosition).reverse()
    .find((entry) => entry.kind === "tool_result" && Boolean(toolPayload(entry.content).error));
  const error = previousFailure ? String(toolPayload(previousFailure.content).error || "") : "";
  const target = toolTarget(step.call.content).split(/[\\/]/).pop() || "the file";
  const action = String(toolPayload(step.call.content).action || "");
  if (/already exists/i.test(error) && action === "read") {
    return `The earlier write found that ${target} already exists, so I’ll read it before editing.`;
  }
  if (error) return `The previous action failed; I’ll ${actionSummary(step).replace(/^I’ll /, "")} to recover.`;
  return `Next: ${actionSummary(step)}`;
}

function ReasoningDisclosure({ summary, content, active }: { summary: string; content: string; active: boolean }) {
  const [expanded, setExpanded] = useState(active);
  useEffect(() => { setExpanded(active); }, [active]);
  return <details className="assistant-disclosure kind-thinking" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary><BrainCircuit size={14} /><strong>{active ? "Reasoning summary" : "Reasoned"}</strong><span title={summary}>{summary}</span></summary>
    <div className="reasoning-text">{content || summary}</div>
  </details>;
}

function ToolGroup({ steps, active }: { steps: ToolStep[]; active: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const labels = Array.from(new Set(steps.map(({ call }) => toolSummary(call.title, call.content, call.kind === "tool_result").label)));
  const failed = steps.some(({ result }) => result && !!toolPayload(result.content).error);
  const countLabel = `${steps.length} ${steps.length === 1 ? "tool" : "tools"}`;
  return <details className={`tool-group ${active ? "active" : ""} ${failed ? "failed" : ""}`} open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary><Wrench size={15} /><strong>{active ? `Using ${countLabel}` : `Used ${countLabel}`}</strong><span>{labels.join(" · ")}</span><em>{expanded ? "Hide" : "Show"}</em></summary>
    <ol className="tool-chain">{steps.map(({ call, result }) => <li key={call.id} className={result ? "done" : active ? "running" : ""}>
      <ToolActivity kind={call.kind} title={call.title} raw={call.content} resultRaw={result?.content} />
    </li>)}</ol>
  </details>;
}

function ResponseActivity({ events, active }: { events: TimelineEntry[]; active: boolean }) {
  const latest = events.filter((entry) => entry.kind !== "echo").at(-1);
  const working = active;
  const segments = buildResponseSegments(events);
  return <div className="assistant-response">
    {segments.map((segment, index) => segment.type === "reasoning"
      ? <ReasoningDisclosure key={segment.key} summary={reasoningStepSummary(events, segments, index)} content={segment.entries.map((entry) => displayText(entry.content)).join("\n\n")} active={segment.entries.some((entry) => entry.metadata.live === true) || (working && latest?.kind === "thinking" && segment.entries.includes(latest))} />
      : segment.type === "narration"
        ? segment.entry.metadata.source === "tool_intent" ? null : <p key={segment.key} className="assistant-progress">{displayText(segment.entry.content)}</p>
      : segment.type === "inferred"
        ? null
      : segment.type === "tools"
        ? <ToolGroup key={segment.key} steps={segment.steps} active={working && segment.steps.some(({ call, result }) => call === latest && !result)} />
      : segment.entry.kind === "file" && typeof segment.entry.metadata.id === "string"
        ? <GeneratedArtifact key={segment.key} id={segment.entry.metadata.id} name={segment.entry.title || "Generated file"} mime={typeof segment.entry.metadata.mime === "string" ? segment.entry.metadata.mime : undefined} size={typeof segment.entry.metadata.size === "number" ? segment.entry.metadata.size : undefined} />
      : segment.entry.kind === "message"
        ? <div key={segment.key} className="assistant-response-answer"><ResponseMarkdown content={segment.entry.content} /></div>
      : segment.entry.kind === "error"
        ? <details key={segment.key} className="assistant-disclosure kind-error" open><summary><Code2 size={14} /><strong>Error</strong><span>{segment.entry.title}</span></summary><div>{displayText(segment.entry.content)}</div></details>
      : null)}
    {visibleEchoReceiptGroups(events, active).map((entries) => <EchoReceipt key={entries.map((entry) => entry.id).join("-")} entries={entries} />)}
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
  conversationId, initialDraft, onDraftChange, title, client, entries, runtimeRunning, runtimeSnapshot, telemetry, selectedProfile, onSelectProfile, liveTokenSpeed, promptProgress, backendActive,
  onConversationId, onRefresh, onNotice, onExport, onRename, onDelete,
  pinned, project, projectId, projects, onPin, onMoveProject, onCreateProject,
  defaultSkills, subagentsEnabled, maxSubagents, projectSkillsEnabled, compactAtTokens,
}: Props) {
  const [draft, setDraft] = useState(initialDraft?.text || "");
  const draftInput = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const input = draftInput.current;
    if (!input) return;
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || !event.deltaY || input.scrollHeight <= input.clientHeight) return;
      event.preventDefault();
      const line = Math.round(parseFloat(getComputedStyle(input).lineHeight) || 24);
      input.scrollTop = Math.max(0, Math.round(input.scrollTop / line) + Math.sign(event.deltaY)) * line;
    };
    input.addEventListener('wheel', wheel, { passive: false });
    return () => input.removeEventListener('wheel', wheel);
  }, []);

  const [selectedSkills, setSelectedSkills] = useState<ComposerSkillId[]>(() => [...defaultSkills]);
  const [skillModels, setSkillModels] = useState<{id:string;category:string;installed:boolean}[]>([]);
  useEffect(() => {
    let alive = true;
    const refresh = () => { void api.installedSkillModels().then(models => { if (alive) setSkillModels(models); }).catch(() => {}); };
    refresh(); const timer = setInterval(refresh, 4000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  const defaultSkillsKey = defaultSkills.join("\u0000");
  useEffect(() => { setSelectedSkills([...defaultSkills]); }, [defaultSkillsKey]);
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const [files, setFiles] = useState<string[]>(() => [...(initialDraft?.files || [])]);
  useEffect(() => { onDraftChange?.({ text: draft, files: [...files] }); }, [draft, files, onDraftChange]);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [composerMenu, setComposerMenu] = useState<"actions" | "model" | null>(null);
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
  const [artifactPreview, setArtifactPreview] = useState<api.ArtifactPreview | api.ComposerAttachmentPreview | { remoteImage: string } | null>(null);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [nativeBrowserOpen, setNativeBrowserOpen] = useState(false);
  const [browserWidth, setBrowserWidth] = useState(() => { try { return Number(window.localStorage.getItem("opencore.browser.width")) || 580; } catch { return 580; } });
  const [browserSide, setBrowserSide] = useState<"left" | "right">(() => { try { return window.localStorage.getItem("opencore.browser.side") === "left" ? "left" : "right"; } catch { return "right"; } });
  const [browserSnap, setBrowserSnap] = useState(() => { try { return Number(window.localStorage.getItem("opencore.browser.snap")) || 24; } catch { return 24; } });
  const [desktopOpen, setDesktopOpen] = useState(false);
  const generationActive = sending || backendActive;
  const pendingToolRef = useRef<ToolApprovalRequest | null>(null);
  const composerMenuRef = useRef<HTMLDivElement>(null);
  const composerMenuTriggerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    let dispose: (() => void) | undefined;
    if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
      void listen("opencore-open-native-browser", () => setNativeBrowserOpen(true)).then((unlisten) => { dispose = unlisten; }).catch(() => {});
    }
    return () => dispose?.();
  }, []);
  useEffect(() => { try { window.localStorage.setItem("opencore.browser.width", String(browserWidth)); window.localStorage.setItem("opencore.browser.side", browserSide); window.localStorage.setItem("opencore.browser.snap", String(browserSnap)); } catch { /* Layout works for this session. */ } }, [browserWidth, browserSide, browserSnap]);
  const controlsRef = useRef<HTMLDivElement>(null);
  const effortIndex = REASONING_MODES.findIndex((mode) => mode.value === reasoningEffort);
  const effortLabel = REASONING_MODES[effortIndex].label;
  const approvalLabel = APPROVAL_MODES.find((mode) => mode.value === approvalMode)?.label;
  const approvalShort = APPROVAL_MODES.find((mode) => mode.value === approvalMode)?.short;
  const approvalIndex = APPROVAL_MODES.findIndex((mode) => mode.value === approvalMode);
  const shownApprovalIndex = approvalPreviewIndex ?? approvalIndex;
  const profileLocked = runtimeRunning || runtimeSnapshot.status === "starting" || generationActive;

  const artifactActions: ArtifactActions = {
    preview: (id) => {
      setArtifactLoading(true);
      void api.previewArtifact(id).then((item) => { setArtifactPreview(item); setNativeBrowserOpen(true); }).catch((error: unknown) => onNotice(`Could not preview file: ${String(error)}`)).finally(() => setArtifactLoading(false));
    },
    previewAttachment: (path) => {
      setArtifactLoading(true);
      void api.previewComposerAttachment(path).then((item) => { setArtifactPreview(item); setNativeBrowserOpen(true); }).catch((error: unknown) => onNotice(`Could not preview file: ${String(error)}`)).finally(() => setArtifactLoading(false));
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
      if (controlsRef.current && !controlsRef.current.contains(event.target as Node) && !(event.target as HTMLElement).closest("#effort-panel,#approval-panel,#tools-panel")) setControlOpen(null);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setControlOpen(null); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [controlOpen]);

  useEffect(() => {
    if (!composerMenu) return;
    const outside = (event: PointerEvent) => { if (!composerMenuRef.current?.contains(event.target as Node)) setComposerMenu(null); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { setComposerMenu(null); composerMenuTriggerRef.current?.focus(); } };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [composerMenu]);

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
    const streaming = entries.filter((entry) => entry.metadata.live === true);
    const persisted = liveEntries.length ? liveEntries : entries.filter((entry) => entry.metadata.live !== true);
    return [...persisted, ...removePersistedOptimisticDuplicates(persisted, optimistic), ...streaming];
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
    const submissionId = crypto.randomUUID();
    const optimisticEntry: TimelineEntry = {
      id: -Date.now(),
      conversationId: id,
      timestamp: new Date().toISOString(),
      kind: "message",
      role: "user",
      source: "OpenCore",
      title: "You",
      content: item.text || `Attached ${item.files.length} file(s)`,
      metadata: { files: item.files, submissionId },
    };
    setOptimistic([optimisticEntry]);
    let failed = false;
    let interrupted = false;

    try {
      await api.sendChatMessage(id, item.text, item.files, item.reasoningEffort, item.approvalMode, item.skills, item.subagentsEnabled, item.maxSubagents, item.projectSkillsEnabled, item.compactAtTokens, submissionId);
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
    let text = draft.trim();
    const available=availableComposerSkills(skillModels).map(skill=>skill.id);
    const slash=exactSlashSkill(text);
    if(slash&&!available.includes(slash.id)){onNotice(`Install a ${slash.category} model in Models to unlock /${slash.id}.`);return;}
    const skills=selectedSkills.filter(id=>available.includes(id));
    if(slash){text=resolveSlashSkill(text,slash.id);if(!skills.includes(slash.id))skills.push(slash.id);}
    if (!text && files.length === 0) return;
    const item: ChatQueueItem = { id: crypto.randomUUID(), text, files: [...files], reasoningEffort, approvalMode, skills, subagentsEnabled, maxSubagents, projectSkillsEnabled, compactAtTokens };
    setDraft("");
    setFiles([]);
    setSelectedSkills([...defaultSkills]);
    setSkillPickerOpen(false);
    if (sendingRef.current) {
      setQueueBoth([...queueRef.current, item]);
    } else {
      void runPromptRef.current?.(item);
    }
  };

  const addFilePaths = (paths: string[]) => {
    if (!paths.length) return;
    setFiles((current) => Array.from(new Set([...current, ...paths])));
  };

  const chooseFiles = async () => {
    try {
      const selected = await open({ multiple: true, directory: false });
      if (selected) {
        const paths = Array.isArray(selected) ? selected : [selected];
        addFilePaths(paths);
      }
    } catch (error) { onNotice(`Could not attach files: ${String(error)}`); }
    finally { await focusDraft(); }
  };

  const focusDraft = async () => {
    // WebView2's native keyboard target can remain outside the renderer after
    // a native dialog or another child webview. DOM focus alone is insufficient.
    if ('__TAURI_INTERNALS__' in window) {
      try { await getCurrentWebview().setFocus(); } catch (error) { onNotice(`Could not focus the editor: ${String(error)}`); }
    }
    draftInput.current?.focus({ preventScroll: true });
  };

  const attachTransferredFiles = async (transfer: DataTransfer | null, returnFocus = false) => {
    const incoming = filesFromTransfer(transfer);
    if (!incoming.length) return;
    const paths: string[] = [];
    const failures: string[] = [];
    for (const file of incoming) {
      try { paths.push(await api.stageComposerAttachment(file)); }
      catch (error) { failures.push(`${file.name || "file"}: ${String(error)}`); }
    }
    addFilePaths(paths);
    if (failures.length) onNotice(`Could not attach ${failures.join("; ")}`);
    if (returnFocus && paths.length) await focusDraft();
  };

  const attachPastedText = async (file: File, characterCount: number) => {
    try {
      const path = await api.stageComposerAttachment(file);
      addFilePaths([path]);
      onNotice(`Attached long paste as pasted-text.txt (${characterCount.toLocaleString()} characters).`);
    } catch (error) { onNotice(`Could not attach pasted text: ${String(error)}`); }
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
  const matchingSkills = skillPickerOpen ? filterComposerSkills(draft, skillModels) : [];
  const selectSkill = (id: ComposerSkillId) => {
    setSelectedSkills((current) => current.includes(id) ? current : [...current, id]);
    setDraft((current) => resolveSlashSkill(current, id));
    setSkillPickerOpen(false);
  };
  return <ArtifactActionsContext.Provider value={artifactActions}><main className={`assistant-thread-panel chat-mode ${nativeBrowserOpen ? "browser-open" : ""} ${browserSide === "left" ? "browser-left" : ""}`} style={nativeBrowserOpen ? { "--browser-width": `${browserWidth}px` } as React.CSSProperties : undefined}>
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

    {nativeBrowserOpen ? <NativeBrowserPanel onClose={() => setNativeBrowserOpen(false)} onNotice={onNotice} preview={artifactPreview} onDownload={artifactActions.download} width={browserWidth} onWidthChange={setBrowserWidth} side={browserSide} onSideChange={setBrowserSide} snapPx={browserSnap} onSnapChange={setBrowserSnap} obscured={controlOpen !== null} /> : null}
    {desktopOpen ? <DesktopPanel onClose={() => setDesktopOpen(false)} onNotice={onNotice} /> : null}

    <AssistantRuntimeProvider runtime={runtime}>
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
    </AssistantRuntimeProvider>

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

      <div
        className={`chat-composer ${draggingFiles ? "file-drop-active" : ""}`}
        ref={controlsRef}
        onDragEnter={(event) => {
          if (!transferHasFiles(event.dataTransfer)) return;
          event.preventDefault();
          setDraggingFiles(true);
        }}
        onDragOver={(event) => {
          if (!transferHasFiles(event.dataTransfer)) return;
          event.preventDefault();
          setDraggingFiles(true);
        }}
        onDragLeave={(event) => {
          const next = event.relatedTarget;
          if (next instanceof Node && event.currentTarget.contains(next)) return;
          setDraggingFiles(false);
        }}
        onDrop={(event) => {
          if (!transferHasFiles(event.dataTransfer)) return;
          event.preventDefault();
          setDraggingFiles(false);
          void attachTransferredFiles(event.dataTransfer, true);
        }}
      >
        <div className="composer-action-anchor" ref={composerMenuRef}>
          <button ref={composerMenuTriggerRef} type="button" className="attach-button composer-plus-button" aria-label="Add files or choose model" aria-expanded={composerMenu !== null} aria-controls="composer-action-menu" onClick={() => setComposerMenu((open) => open === null ? "actions" : null)} title="Add files or choose model"><Plus size={19} /></button>
          {composerMenu === "actions" ? <div className="composer-action-popover" id="composer-action-menu" role="menu" aria-label="Composer actions">
            <button type="button" role="menuitem" onClick={() => { setComposerMenu(null); void chooseFiles(); }}><Paperclip size={16} /><span>Upload files or images</span></button>
            <button type="button" role="menuitem" aria-haspopup="menu" onClick={() => setComposerMenu("model")}><BrainCircuit size={16} /><span className="composer-action-model-copy">Model<small>{profileLabel(selectedProfile)}</small></span><ChevronRight size={15} /></button>
          </div> : null}
          {composerMenu === "model" ? <div className="composer-action-popover composer-model-popover" id="composer-action-menu" role="menu" aria-label="Model selection">
            <div className="composer-model-heading"><button type="button" aria-label="Back to composer actions" onClick={() => setComposerMenu("actions")}><ArrowLeft size={14} /></button><span><strong>Model</strong><small>Current · {profileLabel(selectedProfile)}</small></span></div>
            <ModelProfileOptions selectedProfile={selectedProfile} onSelect={(profile) => { onSelectProfile(profile); setComposerMenu(null); }} disabled={profileLocked} id="composer-model-profile-options" className="composer-model-options" />
          </div> : null}
        </div>
        <SpeechButton key={conversationId || "new"} onTranscript={(text) => setDraft((current) => current + (current && !/\s$/.test(current) ? " " : "") + text)} onError={onNotice} />
        <textarea
          ref={draftInput}
          aria-label="Message OpenCore"
          onPointerDown={() => { void focusDraft(); }}
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
          onPaste={(event) => {
            const pastedFiles = filesFromTransfer(event.clipboardData);
            const pastedText = event.clipboardData.getData("text/plain");
            if (pastedText.length >= LARGE_TEXT_PASTE_THRESHOLD) {
              const textFile = new File([pastedText], "pasted-text.txt", { type: "text/plain" });
              if (textFile.size > MAX_PASTED_TEXT_ATTACHMENT_BYTES) {
                onNotice("This paste exceeds the 32 MiB attachment limit, so it will stay as composer text.");
                return;
              }
              event.preventDefault();
              if (pastedFiles.length) void attachTransferredFiles(event.clipboardData);
              void attachPastedText(textFile, pastedText.length);
              return;
            }
            if (!pastedFiles.length) return;
            if (!event.clipboardData.getData("text/plain")) event.preventDefault();
            void attachTransferredFiles(event.clipboardData);
          }}
          placeholder="Message OpenCore…"
          rows={2}
        />
        {draggingFiles ? <div className="composer-drop-overlay" role="status" aria-live="polite">Drop files to attach</div> : null}
        <div className="composer-controls">
          <button type="button" className={`composer-control-button approval-trigger ${controlOpen === "approval" ? "active" : ""}`} aria-label={`Approval: ${approvalLabel}`} aria-expanded={controlOpen === "approval"} aria-controls="approval-panel" onClick={() => setControlOpen((open) => open === "approval" ? null : "approval")}>
            <ShieldCheck size={16} /><span className="control-copy"><small>Approval</small><strong>{approvalShort}</strong></span><ChevronDown size={13} />
          </button>
          <button type="button" className={`composer-control-button effort-trigger effort-${effortIndex} ${controlOpen === "effort" ? "active" : ""}`} aria-label={`Effort: ${effortLabel}`} aria-expanded={controlOpen === "effort"} aria-controls="effort-panel" onClick={() => setControlOpen((open) => open === "effort" ? null : "effort")}>
            <BrainCircuit size={16} /><span className="control-copy"><small>Effort</small><strong>{effortLabel}</strong></span><ChevronDown size={13} />
          </button>
          {controlOpen === "effort" ? <FloatingWindow id="effort-compact" domId="effort-panel" title="Effort" icon={<BrainCircuit size={16} />} className={`composer-popover effort-popover effort-${effortIndex}`} onClose={() => setControlOpen(null)} place="composer" initialWidth={440} initialHeight={160} minWidth={280} minHeight={145} ariaLabel="Effort settings">
            <div className="effort-bar">
              <div className="effort-rail">
                <div className="effort-segments">{REASONING_MODES.map((mode, index) => <span key={mode.value} className={index === effortIndex ? "lit" : ""} />)}</div>
                <input className="effort-range" type="range" min="0" max="6" step="1" value={effortIndex} aria-label="Reasoning effort" aria-valuetext={effortLabel} onChange={(event) => chooseEffort(Number(event.target.value))} />
              </div>
            </div>
            <div className="effort-labels" aria-hidden="true">{REASONING_MODES.map((mode, index) => <span key={mode.value} className={index === effortIndex ? "selected" : ""}>{mode.label}</span>)}</div>
          </FloatingWindow> : null}
          {controlOpen === "approval" ? <FloatingWindow id="approval" domId="approval-panel" title="Approval" icon={<ShieldCheck size={16} />} className="composer-popover approval-popover" onClose={() => setControlOpen(null)} place="composer" initialWidth={510} initialHeight={160} minWidth={280} minHeight={140} ariaLabel="Approval settings">
            <p className="control-explanation">{APPROVAL_MODES.find(mode => mode.value === approvalMode)?.detail}</p>
            <div className="approval-bar" role="group" aria-label="Approval mode"><div className="approval-rail"><div className="approval-segments" aria-hidden="true">{APPROVAL_MODES.map((mode, index) => <span key={mode.value} className={index <= shownApprovalIndex ? "lit" : ""} />)}</div><input className="approval-range" type="range" min="0" max="3" step="1" value={shownApprovalIndex} aria-label="Approval level" aria-valuetext={APPROVAL_MODES[shownApprovalIndex].label} onChange={(event) => { const index = Number(event.target.value); approvalPreviewRef.current = index; setApprovalPreviewIndex(index); }} onPointerUp={commitApprovalRange} onKeyUp={commitApprovalRange} onBlur={commitApprovalRange} onPointerCancel={() => { approvalPreviewRef.current = null; setApprovalPreviewIndex(null); }} /></div><div className="approval-labels">{APPROVAL_MODES.map((mode) => <button type="button" key={mode.value} aria-pressed={mode.value === approvalMode} onClick={() => chooseApprovalMode(mode.value)}>{mode.label}</button>)}</div></div>
          </FloatingWindow> : null}
        </div>
        <button className={`send-button ${generationActive ? "is-stop" : ""}`} onClick={generationActive ? () => void stop() : submit} disabled={!generationActive && !draft.trim() && files.length === 0} title={generationActive ? "Stop" : "Send"} aria-label={generationActive ? "Stop generation" : "Send message"}>
          {generationActive ? <Square size={17} fill="currentColor" /> : <Send size={18} />}
        </button>
      </div>
      <div className="composer-hint"><span>{generationActive ? "OpenCore is working · press Stop to cancel" : "Enter to send · Shift+Enter for a new line · paste or drop files"}</span><span>OpenCore can make mistakes. Check important results.</span><span className="composer-token-speed">{generationActive && promptProgress?.speed ? `${promptProgress.speed.toFixed(1)} input tokens/s` : (liveTokenSpeed ?? telemetry.tokensPerSecond) > 0 ? `${(liveTokenSpeed ?? telemetry.tokensPerSecond).toFixed(1)} output tokens/s` : "Waiting for token metrics"} · {(telemetry.totalCompletionTokens ?? telemetry.completionTokens).toLocaleString()} output tokens</span></div>
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
  </main></ArtifactActionsContext.Provider>;
});
