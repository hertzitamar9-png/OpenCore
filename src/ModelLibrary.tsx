import { useCallback, useEffect, useState } from "react";
import { Check, Download, HardDrive, Trash2 } from "lucide-react";
import * as api from "./api";
import type { RuntimeProfile } from "./types";

const gb = (bytes: number) => `${(bytes / 1e9).toFixed(2)} GB`;
export function ModelLibrary({ selectedProfile, onSelect, runtimeActive, onNotice }: {
  selectedProfile: RuntimeProfile; onSelect: (profile: RuntimeProfile) => void;
  runtimeActive: boolean; onNotice: (notice: string) => void;
}) {
  const [library, setLibrary] = useState<api.ModelLibrary | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try { setLibrary(await api.modelLibrary()); setError(""); }
    catch (cause) { setError(String(cause)); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const installing = Boolean(library?.progress && ["preparing", "downloading", "verifying", "uninstalling"].includes(library.progress.phase));
  useEffect(() => {
    if (!installing) return;
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [installing, refresh]);
  async function change(model: api.InstalledModel) {
    setPending(model.id);
    try {
      if (model.installed) await api.uninstallModel(model.id);
      else await api.installModel(model.id);
      await refresh();
    } catch (cause) { onNotice(String(cause)); }
    finally { setPending(null); }
  }
  const progress = library?.progress;
  return <section className="model-library" aria-label="Install local models">
    <div className="model-library-heading"><div><h2>Your model library</h2><p>Choose what to install. Weights download from pinned Hugging Face revisions and are verified before use.</p></div>
      {library ? <span className="model-storage"><HardDrive size={16} /> {gb(library.diskFreeBytes)} free</span> : null}
    </div>
    <p className="model-library-note">No model weights come with the app. Downloads preserve at least 200 GB of free space. Variants share their weight files.</p>
    {runtimeActive ? <p className="model-library-note">Stop the runtime to install, uninstall, or select a different model.</p> : null}
    {error ? <div role="alert">{error}<button onClick={() => void refresh()}>Retry</button></div> : null}
    {progress ? <div className="model-install-progress" role={progress.error ? "alert" : "status"}>
      <div><strong>{library?.models.find((model) => model.id === progress.modelId)?.label || progress.modelId}</strong><span>{progress.phase}</span></div>
      {installing ? <><progress aria-label="Model download" value={progress.downloadedBytes} max={Math.max(1, progress.totalBytes)} /><small>{gb(progress.downloadedBytes)} / {gb(progress.totalBytes)} · {progress.currentFile || "Preparing download"}</small><button onClick={() => void api.cancelModelInstall().catch((cause) => onNotice(String(cause)))}>Cancel download</button></> : null}
      {progress.error ? <p>{progress.error}</p> : null}
    </div> : null}
    <div className="model-library-grid">{library?.models.map((model) => <article key={model.id} className={`model-library-card ${model.id === selectedProfile ? "selected" : ""}`}>
      <header><div><h3>{model.label}</h3><span>{model.precision}</span></div><span className={`model-install-state ${model.installed ? "installed" : ""}`}>{model.installed ? "Installed" : "Not installed"}</span></header>
      <p>{model.description}</p>
      <dl><div><dt>{model.selectable ? "Active context" : "Load mode"}</dt><dd>{model.selectable ? `${model.contextTokens.toLocaleString()} tokens` : "On demand"}</dd></div><div><dt>Download</dt><dd>{model.downloadBytes ? gb(model.downloadBytes) : "Already downloaded"}</dd></div></dl>
      <small>{model.note}</small>
      <footer><button disabled={runtimeActive || installing || Boolean(pending)} onClick={() => void change(model)}>
        {model.installed ? <Trash2 size={15} /> : <Download size={15} />}{pending === model.id ? "Working…" : model.installed ? "Uninstall" : "Install"}
      </button>{model.selectable ? <button className={selectedProfile === model.id ? "active" : ""} disabled={!model.installed || runtimeActive || installing || Boolean(pending)} onClick={() => onSelect(model.id as RuntimeProfile)}>
        {selectedProfile === model.id ? <Check size={15} /> : null}{selectedProfile === model.id ? "Selected" : "Use model"}
      </button> : null}</footer>
      <span className="model-license">{model.experimental ? "Experimental · " : ""}{model.license}</span>
    </article>)}</div>
  </section>;
}
