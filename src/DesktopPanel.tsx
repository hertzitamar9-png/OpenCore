import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent, WheelEvent } from "react";
import { AppWindow, Check, Maximize2, Minimize2, RefreshCw, ShieldCheck } from "lucide-react";
import * as api from "./api";
import { browserPoint } from "./browser-coordinates";
import { FloatingWindow } from "./FloatingWindow";

type Props = {
  onClose: () => void;
  onNotice: (message: string) => void;
  embedded?: boolean;
  active?: boolean;
  onExpandedChange?: (expanded: boolean) => void;
};
type Point = { x: number; y: number };
type Context = { windowId: number | null; revision: number; activity: number };
type Editor = { windowId: number; at: Point };
type BackgroundResult = { message?: string; backgroundVerified?: boolean; warning?: { message?: string }; inputMode?: string };
type Interaction = BackgroundResult & { editable?: boolean; value?: string; activated?: boolean; inputMode?: string };
type TextResult = BackgroundResult & { updated?: boolean; submitted?: boolean };
type Feedback = { error: boolean; warning?: boolean; message: string };
type PendingEdit = { context: Context; at: Point; text: string };
const BACKGROUND_CONTROL = { backgroundOnly: true, allowForegroundFallback: false, manualControl: true };
const DESKTOP_VIEW_ONLY = "Entire desktop is view only. Select an app window to use background controls.";

function requireBackgroundInput(result: BackgroundResult) {
  if (result.inputMode && !["accessibility", "window-message"].includes(result.inputMode)) {
    throw new Error("This control does not support background interaction. Foreground input is disabled in Computer; use a supported background control in the app.");
  }
}

export function DesktopPanel({ onClose, onNotice, embedded = false, active = true, onExpandedChange }: Props) {
  const [windows, setWindows] = useState<api.DesktopWindow[]>([]);
  const [windowId, setWindowId] = useState<number | null>(null);
  const [shot, setShot] = useState<api.DesktopShot | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [typing, setTyping] = useState("");
  const [busy, setBusy] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [captureUnavailable, setCaptureUnavailable] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [size, setSize] = useState<"fit" | "actual">("fit");
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const mounted = useRef(true);
  const activeRef = useRef(active);
  const noticeRef = useRef(onNotice);
  const selection = useRef({ windowId: null as number | null, revision: 0 });
  const activity = useRef(0);
  const captureSequence = useRef(0);
  const capturePending = useRef<{ revision: number; sequence: number } | null>(null);
  const captureError = useRef("");
  const controlBusy = useRef(false);
  const pending = useRef<{ timer: number; edit: PendingEdit } | null>(null);
  const localDraft = useRef<{ windowId: number; at: Point; text: string } | null>(null);
  const updates = useRef<Promise<void>>(Promise.resolve());
  const shotRef = useRef(shot);
  const typingRef = useRef<HTMLInputElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  activeRef.current = active;
  noticeRef.current = onNotice;
  shotRef.current = shot;

  const context = useCallback((): Context => ({ ...selection.current, activity: activity.current }), []);
  const current = useCallback((target: Context) => mounted.current && activeRef.current &&
    target.windowId === selection.current.windowId && target.revision === selection.current.revision && target.activity === activity.current, []);
  const cancelPending = useCallback(() => {
    const edit = pending.current?.edit;
    if (pending.current) window.clearTimeout(pending.current.timer);
    pending.current = null;
    return edit;
  }, []);
  const report = useCallback((error: unknown) => {
    const message = String(error);
    setFeedback({ error: true, message });
    noticeRef.current(`Computer: ${message}`);
  }, []);
  const completed = (result: BackgroundResult, fallback: string) => {
    const warning = result.warning?.message || (result.backgroundVerified === false
      ? "Desktop state changed during this action. Check the completed result before retrying." : "");
    const message = result.message || fallback;
    setFeedback({ error: false, warning: Boolean(warning), message: warning && !message.includes(warning) ? `${message} ${warning}` : message });
  };
  const select = useCallback((id: number | null) => {
    cancelPending();
    selection.current = { windowId: id, revision: selection.current.revision + 1 };
    captureSequence.current += 1;
    controlBusy.current = false;
    captureError.current = "";
    shotRef.current = null;
    localDraft.current = null;
    setWindowId(id);
    setShot(null);
    setEditor(null);
    setTyping("");
    setBusy(false);
    setCapturing(false);
    setCaptureUnavailable(false);
    setFeedback(id === 0 ? { error: false, message: DESKTOP_VIEW_ONLY } : null);
  }, [cancelPending]);

  const refresh = useCallback(async (force = false, manual = false) => {
    const target = context();
    if (!current(target) || (!force && capturePending.current?.revision === target.revision)) return;
    const sequence = ++captureSequence.current;
    capturePending.current = { revision: target.revision, sequence };
    const latest = () => current(target) && sequence === captureSequence.current;
    if (manual) setCapturing(true);
    try {
      const listed = await api.desktopCommand<{ windows: api.DesktopWindow[] }>("list", BACKGROUND_CONTROL);
      if (!latest()) return;
      setWindows(previous => {
        // Accessibility enumeration can briefly omit a still-open window.
        // Keep its picker entry and verify availability through capture instead.
        const retained = previous.find(item => item.windowId === target.windowId);
        return retained && !listed.windows.some(item => item.windowId === target.windowId)
          ? [...listed.windows, retained] : listed.windows;
      });
      if (target.windowId == null) return;
      const captured = await api.desktopCommand<api.DesktopShot>("screenshot", { windowId: target.windowId, ...BACKGROUND_CONTROL });
      if (!latest()) return;
      if (captured.windowId !== target.windowId || captured.bounds.width <= 0 || captured.bounds.height <= 0) {
        throw new Error("The selected window capture is unavailable. Refresh the capture or choose another window.");
      }
      setShot(previous => previous?.windowId === captured.windowId && previous.dataUrl === captured.dataUrl &&
        previous.bounds.width === captured.bounds.width && previous.bounds.height === captured.bounds.height &&
        previous.bounds.left === captured.bounds.left && previous.bounds.top === captured.bounds.top &&
        (previous.origin?.x ?? 0) === (captured.origin?.x ?? 0) &&
        (previous.origin?.y ?? 0) === (captured.origin?.y ?? 0) ? previous : captured);
      const previousError = captureError.current;
      captureError.current = "";
      setCaptureUnavailable(false);
      if (previousError) setFeedback(previous => previous?.error && previous.message === previousError ? null : previous);
    } catch (error) {
      if (!latest()) return;
      const message = String(error);
      setCaptureUnavailable(true);
      if (captureError.current !== message) { captureError.current = message; report(error); }
    } finally {
      if (capturePending.current?.sequence === sequence) capturePending.current = null;
      if (latest()) setCapturing(false);
    }
  }, [context, current, report]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (!active) { setExpanded(false); setBusy(false); setCapturing(false); return; }
    void refresh(true);
    const timer = window.setInterval(() => void refresh(), 1200);
    return () => {
      window.clearInterval(timer);
      cancelPending();
      activity.current += 1;
      captureSequence.current += 1;
      capturePending.current = null;
      controlBusy.current = false;
    };
  }, [active, refresh, cancelPending]);
  useEffect(() => { onExpandedChange?.(expanded && active); }, [expanded, active, onExpandedChange]);
  useEffect(() => {
    if (!expanded || !active) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      setExpanded(false);
      expandButton.current?.focus();
    };
    document.addEventListener("keydown", escape, true);
    return () => document.removeEventListener("keydown", escape, true);
  }, [expanded, active]);
  useEffect(() => { if (editor && active) typingRef.current?.focus(); }, [editor, active]);

  const queueEdit = (edit: PendingEdit) => {
    updates.current = updates.current.catch(() => {}).then(async () => {
      if (!current(edit.context) || captureError.current) return;
      const result = await api.desktopCommand<TextResult>("set_at", { windowId: edit.context.windowId, ...edit.at, text: edit.text, ...BACKGROUND_CONTROL });
      requireBackgroundInput(result);
      if (!result.updated) throw new Error(result.message || "Background text update could not be confirmed. Your local draft was kept.");
      if (current(edit.context) && (result.warning || result.backgroundVerified === false)) completed(result, "Text updated.");
      if (current(edit.context) && localDraft.current?.windowId === edit.context.windowId &&
        localDraft.current.at.x === edit.at.x && localDraft.current.at.y === edit.at.y && localDraft.current.text === edit.text) localDraft.current = null;
    });
    void updates.current.catch(error => { if (current(edit.context)) report(error); });
  };
  const edit = (text: string) => {
    setTyping(text);
    cancelPending();
    if (!editor || !active || editor.windowId !== selection.current.windowId) return;
    localDraft.current = { windowId: editor.windowId, at: editor.at, text };
    if (captureError.current) return;
    const update = { context: context(), at: editor.at, text };
    const timer = window.setTimeout(() => { pending.current = null; queueEdit(update); }, 180);
    pending.current = { timer, edit: update };
  };
  const control = async <T extends BackgroundResult,>(action: string, args: Record<string, unknown>, done: (result: T, target: Context) => void) => {
    const target = context();
    if (!current(target) || target.windowId == null || controlBusy.current || captureError.current) return;
    if (target.windowId === 0) { setFeedback({ error: false, message: DESKTOP_VIEW_ONLY }); return; }
    if (shotRef.current?.windowId !== target.windowId) return;
    cancelPending();
    const draft = localDraft.current;
    if (draft?.windowId === target.windowId && action !== "commit_text") queueEdit({ context: target, at: draft.at, text: draft.text });
    controlBusy.current = true;
    setBusy(true);
    try {
      const queued = updates.current;
      try { await queued; }
      catch (error) {
        if (updates.current === queued) updates.current = Promise.resolve();
        throw error;
      }
      if (!current(target) || captureError.current) return;
      const result = await api.desktopCommand<T>(action, { windowId: target.windowId, ...args, ...BACKGROUND_CONTROL });
      if (!current(target)) return;
      requireBackgroundInput(result);
      done(result, target);
      void refresh(true);
    } catch (error) { if (current(target)) report(error); }
    finally { if (current(target)) { controlBusy.current = false; setBusy(false); } }
  };
  const interact = (at: Point) => control<Interaction>("interact", at, (result, target) => {
    if (result.editable) {
      localDraft.current = null;
      setEditor({ windowId: target.windowId!, at });
      setTyping(result.value ?? "");
      completed(result, "Text field selected. Apply text below, then click the app's submit button to submit.");
    } else if (result.activated) {
      setEditor(null);
      completed(result, result.backgroundVerified === false ? "App control activated." : "App control activated in the background.");
    } else {
      setEditor(null);
      throw new Error(result.message || "This control does not support background interaction. Select a supported app control.");
    }
  });
  const applyText = () => {
    if (!editor || editor.windowId !== selection.current.windowId) return;
    const appliedText = typing;
    return control<TextResult>("commit_text", { ...editor.at, text: appliedText }, result => {
      if (!result.updated && !result.submitted) throw new Error(result.message || "Background text application could not be confirmed. Refresh the capture and try a supported text field.");
      if (localDraft.current?.windowId === editor.windowId && localDraft.current.text === appliedText) localDraft.current = null;
      completed(result, result.submitted ? "Text submitted." : "Text updated. Click the app's supported submit button to submit.");
      if (result.submitted) setEditor(null);
    });
  };
  const discardDraft = () => {
    cancelPending();
    selection.current = { ...selection.current, revision: selection.current.revision + 1 };
    captureSequence.current += 1;
    updates.current = Promise.resolve();
    localDraft.current = null;
    setEditor(null);
    setTyping("");
    setFeedback({ error: false, message: "Local draft discarded. Select a supported app control." });
    void refresh(true);
  };
  const point = (event: MouseEvent<HTMLImageElement> | WheelEvent<HTMLImageElement>) => {
    if (!shot || shot.windowId !== selection.current.windowId) return null;
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    const captured = browserPoint(event.clientX, event.clientY, rect.left, rect.top, rect.width, rect.height, shot.bounds.width, shot.bounds.height);
    return { x: captured.x + (shot.origin?.x ?? 0), y: captured.y + (shot.origin?.y ?? 0) };
  };
  const scroll = (event: WheelEvent<HTMLImageElement>) => {
    if (size === "actual" || event.deltaY === 0) return;
    event.preventDefault();
    const at = point(event);
    if (!at) return;
    void control<BackgroundResult & { scrolled?: boolean }>("scroll_at", { ...at, direction: event.deltaY < 0 ? "up" : "down" }, result => {
      if (!result.scrolled) throw new Error(result.message || "This control does not expose background scrolling. Select a supported scroll area.");
      completed(result, result.backgroundVerified === false ? "App scrolled." : "App scrolled in the background.");
    });
  };

  const content = <>
    <div className="desktop-toolbar" role="toolbar" aria-label="Computer view controls">
      <div className="desktop-window-picker"><AppWindow size={15} aria-hidden="true" /><select aria-label="Window" value={windowId ?? ""} onChange={event => {
        const id = event.target.value === "" ? null : Number(event.target.value);
        select(id);
        void refresh(true);
      }}><option value="">Select a window</option>{windows.map(item => <option key={item.windowId} value={item.windowId}>{item.windowId === 0 ? `${item.title} (view only)` : item.title}</option>)}</select></div>
      <button type="button" title="Refresh capture" aria-label="Refresh capture" disabled={capturing || !active} onClick={() => void refresh(true, true)}><RefreshCw size={15} className={capturing ? "desktop-refreshing" : undefined} aria-hidden="true" /></button>
      <div className="desktop-size-controls" role="group" aria-label="Capture size">
        <button type="button" title="Fit capture; the wheel scrolls supported app controls" aria-pressed={size === "fit"} onClick={() => setSize("fit")}>Fit</button>
        <button type="button" title="Show actual size; scroll to pan the capture" aria-pressed={size === "actual"} onClick={() => setSize("actual")}>Actual size</button>
      </div>
      <button ref={expandButton} type="button" className="desktop-expand" title={expanded ? "Restore computer view (Escape)" : "Fill the OpenCore window"} aria-label={expanded ? "Restore computer view" : "Expand computer view"} aria-expanded={expanded} disabled={!active} onClick={() => setExpanded(value => !value)}>{expanded ? <Minimize2 size={16} aria-hidden="true" /> : <Maximize2 size={16} aria-hidden="true" />}<span>{expanded ? "Restore" : "Expand"}</span></button>
    </div>
    <div className="desktop-control-status"><ShieldCheck size={14} aria-hidden="true" /><strong>{windowId === 0 ? "View only" : "Background only"}</strong><span>{busy ? "Applying to app…" : expanded ? "Escape restores the panel" : "OpenCore stays in front"}</span></div>
    <div className={`desktop-stage desktop-stage-${size}`} aria-busy={capturing}>{shot ? <div className="desktop-screen"><img src={shot.dataUrl} alt="Selected Windows app" aria-disabled={captureUnavailable} title={captureUnavailable ? "Last captured image. Refresh to resume background controls." : undefined} width={shot.bounds.width} height={shot.bounds.height} draggable={false} onClick={event => { const at = point(event); if (at) void interact(at); }} onWheel={scroll} /></div> : <div className="desktop-empty"><AppWindow size={32} aria-hidden="true" /><strong>{windowId == null ? "Choose a window" : "Capturing selected window…"}</strong><p>View an app and use its supported controls in the background.</p></div>}</div>
    {feedback ? <div className={`desktop-feedback${feedback.error ? " desktop-feedback-error" : feedback.warning ? " desktop-feedback-warning" : ""}`} role={feedback.error ? "alert" : "status"}><span>{feedback.message}</span>{feedback.error && localDraft.current ? <button type="button" aria-label="Discard local draft" disabled={busy || !active} onClick={discardDraft}>Discard draft</button> : null}</div> : null}
    <div className="desktop-inputbar"><input ref={typingRef} aria-label="Type in selected window" placeholder={editor ? "Type here, then apply to the selected field" : "Click a supported text field in the capture"} value={typing} onChange={event => edit(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); void applyText(); } }} disabled={!editor || !active} /><button type="button" title="Apply text to selected window" aria-label="Apply text to selected window" disabled={!editor || busy || !active || captureUnavailable} onClick={() => void applyText()}><Check size={15} aria-hidden="true" /><span>Apply text</span></button></div>
  </>;
  const className = `desktop-panel${expanded ? " desktop-panel-expanded" : ""}`;
  return embedded ? <section className={`${className} desktop-panel-embedded`} aria-label="Windows desktop">{content}</section> : <FloatingWindow id="desktop" title="Computer" icon={<AppWindow size={17} />} status={<span className="connected">Background only</span>} onClose={onClose} className={className} ariaLabel="Windows desktop" initialWidth={790} initialHeight={720} minWidth={440} minHeight={320}>{content}</FloatingWindow>;
}
