import { useEffect, useRef, useState } from "react";
import { AlertTriangle, HardDrive, Trash2 } from "lucide-react";
import * as api from "./api";
import { FloatingWindow } from "./FloatingWindow";

const gb = (bytes: number) => `${(bytes / 1e9).toFixed(2)} GB`;

export function ModelDeleteDialog({ model, runtimeActive, onCancel, onDelete }: {
  model: api.InstalledModel; runtimeActive: boolean; onCancel: () => void;
  onDelete: (plan: api.ModelRemovalPlan) => Promise<void>;
}) {
  const [plan, setPlan] = useState<api.ModelRemovalPlan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [externalAccepted, setExternalAccepted] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const cancel = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef(document.activeElement as HTMLElement | null);
  useEffect(() => {
    let current = true;
    setPlan(null); setError(""); setExternalAccepted(false);
    void api.modelRemovalPlan(model.id).then((result) => { if (current) setPlan(result); })
      .catch((cause) => { if (current) setError(String(cause)); });
    return () => { current = false; };
  }, [model.id, attempt]);
  useEffect(() => {
    cancel.current?.focus();
    return () => { previousFocus.current?.focus(); };
  }, []);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); if (!busy) onCancel(); }
      if (event.key !== "Tab") return;
      const elements = Array.from(document.querySelectorAll<HTMLElement>(
        "#model-delete-dialog button:not(:disabled), #model-delete-dialog input:not(:disabled), #model-delete-dialog summary, #model-delete-dialog [tabindex='0']"));
      const first = elements[0], last = elements.at(-1);
      if (event.shiftKey && (document.activeElement === first || !elements.includes(document.activeElement as HTMLElement))) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !elements.includes(document.activeElement as HTMLElement))) {
        event.preventDefault(); first?.focus();
      }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [busy, onCancel]);
  const external = Boolean(plan?.files.some((file) => file.external) || model.externalManaged);
  const canDelete = Boolean(plan?.files.length && plan.confirmationToken && (!external || externalAccepted) && !busy);
  async function confirm() {
    if (!plan || !canDelete) return;
    setBusy(true); setError("");
    try { await onDelete(plan); }
    catch (cause) { setError(String(cause)); setPlan(null); }
    finally { setBusy(false); }
  }
  const close = () => { if (!busy) onCancel(); };
  return <><div className="modal-backdrop" role="presentation" onMouseDown={close} />
    <FloatingWindow id="model-delete" domId="model-delete-dialog" title="Delete model" ariaLabel={`Delete ${model.label}?`}
      icon={<Trash2 size={17} />} onClose={close} place="center" modal className="opencore-modal dialog-floating model-delete-dialog"
      initialWidth={590} initialHeight={520} minWidth={360} minHeight={320}>
      <div className="model-delete-heading"><span><Trash2 size={22} /></span><div><h2>Delete {model.label}?</h2><p>Remove this model’s local files from your device.</p></div></div>
      {!plan && !error ? <p role="status">Checking local files…</p> : null}
      {error ? <div className="model-delete-warning" role="alert"><p>{error}</p><button disabled={busy} onClick={() => setAttempt((value) => value + 1)}>Check files again</button></div> : null}
      {plan ? <>
        {plan.files.length ? <>
          <div className="model-delete-size"><HardDrive size={17} /><strong>{gb(plan.totalBytes)}</strong><span>in {plan.files.length} local files</span></div>
          <details className="model-delete-files"><summary>Files to delete</summary><ul>{plan.files.map((file) => <li key={file.path}><code>{file.path}</code><span>{gb(file.bytes)}</span></li>)}</ul></details>
          <p>You can reinstall this model from the library later. Chats, projects and ECHO history stay on your device.</p>
          {external ? <label className="model-delete-warning model-delete-external"><AlertTriangle size={18} /><span>These are existing local checkpoint files and may also be used by other apps.
            <span><input type="checkbox" checked={externalAccepted} disabled={busy} onChange={(event) => setExternalAccepted(event.target.checked)} />I also want to delete these existing checkpoint files.</span></span></label> : null}
          {runtimeActive ? <p className="model-delete-warning">Confirming will stop the model runtime and cancel any active generation before deleting files.</p> : null}
        </> : <p>This model has no local files to delete.</p>}
        {plan.retainedFiles.length ? <details className="model-delete-files model-delete-shared"><summary>{plan.retainedFiles.length} shared files will be kept</summary><ul>{plan.retainedFiles.map((file) => <li key={file.path}><code>{file.path}</code><span>Used by {file.sharedWith.join(", ")}</span></li>)}</ul></details> : null}
      </> : null}
      <div className="modal-actions"><button ref={cancel} disabled={busy} onClick={close}>Cancel</button>
        <button className="danger" disabled={!canDelete} onClick={() => void confirm()}><Trash2 size={15} />{busy ? "Deleting…" : runtimeActive && plan?.files.length ? "Stop runtime and delete" : "Delete model"}</button></div>
    </FloatingWindow>
  </>;
}
