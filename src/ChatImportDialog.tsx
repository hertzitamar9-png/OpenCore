import { useEffect, useRef, useState } from "react";
import { FileUp, FolderOpen } from "lucide-react";
import { open } from "@tauri-apps/plugin-dialog";
import { FloatingWindow } from "./FloatingWindow";
import type { ImportFormat, ImportPreview, ImportReport } from "./chat-import-types";
import "./ChatImportDialog.css";

export type ChatImportDialogProps = {
  onClose: () => void;
  onImport: (path: string, format: ImportFormat) => Promise<ImportReport>;
  onPreview?: (path: string, format: ImportFormat) => Promise<ImportPreview>;
  onImported?: (report: ImportReport) => void | Promise<void>;
  onCancelImport?: () => void | Promise<void>;
  progress?: { current: number; total: number } | null;
};

const formats: Array<[ImportFormat, string]> = [
  ["auto", "Auto detect"], ["opencore", "OpenCore"], ["hermes", "Hermes Agent"],
  ["codex", "Codex"], ["claude", "Claude Code"], ["generic", "Generic JSON"],
];
const sourceLabel = (source: string) => formats.find(([value]) => value === source)?.[1] ?? source;

export function ChatImportDialog({ onClose, onImport, onPreview, onImported, onCancelImport, progress }: ChatImportDialogProps) {
  const [format, setFormat] = useState<ImportFormat>("auto");
  const [path, setPath] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [reviewAttempt, setReviewAttempt] = useState(0);
  const chooseButton = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef(document.activeElement as HTMLElement | null);
  const mounted = useRef(true);
  const request = useRef(0);
  const inFlight = useRef(false);

  useEffect(() => {
    mounted.current = true;
    chooseButton.current?.focus();
    return () => { mounted.current = false; request.current += 1; previousFocus.current?.focus(); };
  }, []);

  useEffect(() => {
    const generation = ++request.current;
    setPreview(null); setReport(null); setError("");
    if (!path || !onPreview) { setReviewing(false); return; }
    setReviewing(true);
    void onPreview(path, format).then((result) => {
      if (mounted.current && generation === request.current) setPreview(result);
    }).catch((cause) => {
      if (mounted.current && generation === request.current) setError(String(cause));
    }).finally(() => {
      if (mounted.current && generation === request.current) setReviewing(false);
    });
  }, [path, format, onPreview, reviewAttempt]);

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); if (!inFlight.current && !picking) onClose(); }
      if (event.key !== "Tab") return;
      const elements = Array.from(document.querySelectorAll<HTMLElement>(
        "#chat-import-dialog button:not(:disabled), #chat-import-dialog select:not(:disabled), #chat-import-dialog summary, #chat-import-dialog [tabindex='0']"));
      const first = elements[0], last = elements.at(-1);
      const focused = document.activeElement as HTMLElement;
      if (event.shiftKey && (focused === first || !elements.includes(focused))) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && (focused === last || !elements.includes(focused))) {
        event.preventDefault(); first?.focus();
      }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose, picking]);

  async function chooseFile() {
    if (inFlight.current || picking) return;
    setPicking(true);
    try {
      const selected = await open({ title: "Import chats", directory: false, multiple: false,
        filters: [{ name: "Chat history", extensions: ["json", "jsonl", "db", "sqlite", "sqlite3"] }] });
      if (!mounted.current || selected === null) return;
      if (typeof selected !== "string") throw new Error("Choose one chat history file.");
      setPath(selected); setReviewAttempt((value) => value + 1);
    } catch (cause) { if (mounted.current) setError(String(cause)); }
    finally { if (mounted.current) setPicking(false); }
  }

  const canImport = Boolean(path && !busy && !picking && !reviewing && !report && (!onPreview || preview));
  async function importChats() {
    if (!canImport || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(""); setCancelling(false);
    try {
      const result = await onImport(path, format);
      if (!mounted.current) return;
      setReport(result);
      try { await onImported?.(result); }
      catch (cause) { if (mounted.current) setError(`Import finished, but refreshing the view failed: ${String(cause)}`); }
    } catch (cause) { if (mounted.current) setError(String(cause)); }
    finally {
      inFlight.current = false;
      if (mounted.current) { setBusy(false); setCancelling(false); }
    }
  }

  async function cancelImport() {
    if (!onCancelImport || !busy || cancelling) return;
    setCancelling(true);
    try { await onCancelImport(); }
    catch (cause) { if (mounted.current) { setError(String(cause)); setCancelling(false); } }
  }
  const close = () => { if (!inFlight.current && !picking) onClose(); };
  const warnings = report?.warnings ?? preview?.warnings ?? [];

  return <>
    <div className="modal-backdrop" role="presentation" onMouseDown={close} />
    <FloatingWindow id="chat-import" domId="chat-import-dialog" title="Import chats" ariaLabel="Import chats"
      icon={<FileUp size={17} />} onClose={close} place="center" modal
      className="opencore-modal dialog-floating chat-import-dialog" initialWidth={620} initialHeight={570} minWidth={350} minHeight={330}>
      <div className="chat-import-heading"><span><FileUp size={22} /></span><div><h2>Bring your chats to OpenCore</h2>
        <p>Import a copy, then continue the conversation here.</p></div></div>
      <label className="chat-import-format">Source format
        <select value={format} disabled={busy || picking} onChange={(event) => setFormat(event.target.value as ImportFormat)}>
          {formats.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <div className="chat-import-file"><button ref={chooseButton} disabled={busy || picking} onClick={() => void chooseFile()}>
        <FolderOpen size={16} />{picking ? "Choosing…" : "Choose file"}</button>
        {path ? <code title={path}>{path}</code> : <span>JSON, JSONL or a Hermes SQLite database</span>}</div>
      {reviewing ? <p role="status">Reading chat history…</p> : null}
      {busy ? <p role="status">{progress && progress.total > 0
        ? `Importing ${progress.current} of ${progress.total} conversations…` : "Importing chat history…"}</p> : null}
      {error ? <div className="chat-import-error" role="alert"><p>{error}</p>
        {path && onPreview && !preview && !busy && !reviewing ? <button onClick={() => setReviewAttempt((value) => value + 1)}>Preview again</button> : null}
      </div> : null}
      {preview && !report ? <section className="chat-import-summary" aria-label="Source preview">
        <strong>{sourceLabel(preview.sourceFormat)}</strong><p>{preview.conversations} conversations · {preview.entries} entries</p>
        <ul>{preview.samples.map((sample, index) => <li key={`${sample.sourceConversationId}-${index}`}>
          <span>{sample.title}</span><small>{sample.entries} entries</small>
          {sample.error ? <p className="chat-import-item-error">{sample.error}</p> : null}
          {sample.warnings.map((warning, warningIndex) => <p className="chat-import-item-warning" key={warningIndex}>{warning}</p>)}
        </li>)}</ul>
      </section> : null}
      {report ? <section className="chat-import-summary" aria-label="Import results" aria-live="polite">
        <strong>{report.cancelled ? "Import cancelled" : "Import results"}</strong>
        <p>{report.imported} imported · {report.updated} updated · {report.skipped} skipped{report.failed > 0 ? ` · ${report.failed} failed` : ""}</p>
        <ul>{report.conversations.map((conversation, index) => <li key={`${conversation.conversationId}-${index}`}>
          <span>{conversation.title}</span><small>{({ imported: "Imported", updated: "Updated", skipped: "Already copied", failed: "Failed" })[conversation.status]}</small>
          {conversation.error ? <p className="chat-import-item-error">{conversation.error}</p> : null}
          {conversation.warnings.map((warning, warningIndex) => <p className="chat-import-item-warning" key={warningIndex}>{warning}</p>)}
        </li>)}</ul>
      </section> : null}
      {warnings.length ? <ul className="chat-import-warnings" aria-label="Import notes">{warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul> : null}
      <p className="chat-import-copy-note">Your original files stay in place. Messages, tool activity and reasoning are copied as history.</p>
      <p className="chat-import-limits">Per file: up to 64 MiB of copied history, 50,000 entries and 1,000 chats. Hermes database snapshots can be up to 512 MiB.</p>
      <div className="modal-actions chat-import-actions">
        {busy && onCancelImport ? <button disabled={cancelling} onClick={() => void cancelImport()}>{cancelling ? "Cancelling…" : "Cancel import"}</button> : null}
        <button disabled={busy || picking} onClick={close}>{report ? "Done" : "Cancel"}</button>
        {!report ? <button className="primary" disabled={!canImport} onClick={() => void importChats()}><FileUp size={15} />{busy ? "Importing…" : "Import chats"}</button> : null}
      </div>
    </FloatingWindow>
  </>;
}
