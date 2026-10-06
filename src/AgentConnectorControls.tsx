import { useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { Cable, FolderOpen, RefreshCw, Trash2 } from "lucide-react";

export type AgentConnectorId = "opencode" | "hermes";
export type AgentConnectorControlsProps = {
  id: AgentConnectorId;
  busy?: boolean;
  onConfigure: (id: AgentConnectorId, profileFolder?: string) => Promise<unknown>;
  onSelectFolder: (id: AgentConnectorId, folder: string) => Promise<unknown>;
  onSync: (id: AgentConnectorId) => Promise<unknown>;
  onClear?: (id: AgentConnectorId) => Promise<unknown>;
  onResult?: (message: string) => void;
  onError?: (message: string) => void;
};

export function AgentConnectorControls({ id, busy = false, onConfigure, onSelectFolder, onSync, onClear, onResult, onError }: AgentConnectorControlsProps) {
  const [working, setWorking] = useState(false);
  const [folder, setFolder] = useState<string>();
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const title = id === "opencode" ? "OpenCode" : "Hermes Agent";

  async function run(action: () => Promise<unknown>, success: string) {
    if (busy || inFlight.current) return;
    inFlight.current = true; setWorking(true); setError("");
    try {
      const result = await action();
      if (result !== null) onResult?.(typeof result === "string" ? result : success);
    } catch (cause) {
      const message = String(cause); setError(message); onError?.(message);
    } finally { inFlight.current = false; setWorking(false); }
  }

  async function chooseFolder() {
    await run(async () => {
      const selected = await open({ title: `Choose ${title} history profile folder`, directory: true, multiple: false });
      if (selected === null) return null;
      if (typeof selected !== "string") throw new Error("Choose one history profile folder.");
      const result = await onSelectFolder(id, selected);
      setFolder(selected);
      return result;
    }, "Source folder selected");
  }

  const disabled = busy || working;
  return <div className="agent-connector-controls">
    <div className="connector-actions">
      <button disabled={disabled} onClick={() => void run(() => onConfigure(id, folder), `${title} connected to OpenCore`)}><Cable size={14} />Connect OpenCore</button>
      <button disabled={disabled} onClick={() => void run(() => onSync(id), `${title} history import started`)}><RefreshCw size={14} />Sync chats and projects</button>
      <button disabled={disabled} onClick={() => void chooseFolder()}><FolderOpen size={14} />Choose history folder</button>
      {onClear ? <button disabled={disabled} onClick={() => void run(() => onClear(id), `${title} copied history cleared`)}><Trash2 size={14} />Clear imported</button> : null}
    </div>
    <p>{id === "opencode" ? "Select OpenCore in OpenCode's model picker after connecting." : "Select the OpenCore profile in Hermes after connecting."} Chats link to the original project folders; source files stay in place.</p>
    {folder ? <p>History profile: <code>{folder}</code></p> : null}
    {working ? <p role="status">Working…</p> : null}
    {error ? <p role="alert">{error}</p> : null}
  </div>;
}
