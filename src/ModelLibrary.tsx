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
  const [speech, setSpeech] = useState<api.SpeechStatus>({ modelId: "whisper-large-v3-turbo", installed: false, enabled: false, idleMode: "cold", workerReady: false, coldStartMs: null, warmWakeMs: null, phase: "off" });
  const refresh = useCallback(async () => {
    try {
      const [models, status] = await Promise.all([api.modelLibrary(), api.speechStatus()]);
      setLibrary(models); setSpeech(status); setError("");
    }
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
  async function updateSpeech(changeSetting: () => Promise<api.SpeechStatus>) {
    setPending(speech.modelId);
    try { setSpeech(await changeSetting()); }
    catch (cause) { onNotice(String(cause)); }
    finally { setPending(null); }
  }
  const progress = library?.progress;
  return <section className="model-library" aria-label="Install local models">
    <div className="model-library-heading"><div><h2>Your model library</h2><p>Choose what to install. Weights download from pinned Hugging Face revisions and are verified before use.</p></div>
      {library ? <span className="model-storage"><HardDrive size={16} /> {gb(library.diskFreeBytes)} free</span> : null}
    </div>
    <p className="model-library-note">No model weights come with the app. Downloads preserve at least 100 GB of free space. Variants share their weight files.</p>
    {runtimeActive ? <p className="model-library-note">Stop the runtime to install, uninstall, or select a different model.</p> : null}
    {error ? <div role="alert">{error}<button onClick={() => void refresh()}>Retry</button></div> : null}
    {progress ? <div className="model-install-progress" role={progress.error ? "alert" : "status"}>
      <div><strong>{library?.models.find((model) => model.id === progress.modelId)?.label || progress.modelId}</strong><span>{progress.phase}</span></div>
      {installing ? <><progress aria-label="Model download" value={progress.phase === "preparing" ? undefined : progress.downloadedBytes} max={Math.max(1, progress.totalBytes)} /><small>{progress.currentFile === "Preparing speech runtime" ? "Setting up speech recognition for the microphone." : `${gb(progress.downloadedBytes)} / ${gb(progress.totalBytes)} · ${progress.currentFile || "Preparing download"}`}</small><button onClick={() => void api.cancelModelInstall().catch((cause) => onNotice(String(cause)))}>Cancel download</button></> : null}
      {progress.error ? <p>{progress.error}</p> : null}
    </div> : null}
    <div className="model-library-grid">{library?.models.map((model) => <article key={model.id} className={`model-library-card ${(model.speechLanguage ? model.id === speech.modelId : model.id === selectedProfile) ? "selected" : ""}`}>
      <header><div><h3>{model.label}</h3><span>{model.precision}{model.speechLanguage ? <> · <b>{model.speechLanguage}</b></> : null}</span></div><span className={`model-install-state ${model.installed || model.externalManaged ? "installed" : ""}`}>{model.installed ? "Installed" : model.externalManaged ? "Local weights found" : "Not installed"}</span></header>
      <p>{model.description}</p>
      <dl><div><dt>{model.selectable ? "Active context" : "Load mode"}</dt><dd>{model.selectable ? `${model.contextTokens.toLocaleString()} tokens` : "On demand"}</dd></div><div><dt>Download</dt><dd>{model.downloadBytes ? gb(model.downloadBytes) : "Already downloaded"}</dd></div></dl>
      <small>{model.note}</small>
      {model.speechLanguage && model.id === speech.modelId ? <div className="whisper-controls" aria-label="Speech settings">
        <div className="whisper-enable-row"><div><strong>Microphone dictation</strong><small>GPU memory is released after transcription; RAM standby keeps only CPU weights.</small></div>
          <button type="button" role="switch" aria-checked={speech.enabled} aria-label="Speech to text" className={`whisper-toggle ${speech.enabled ? "on" : ""}`}
            disabled={!model.installed || Boolean(pending)} onClick={() => void updateSpeech(() => api.setSpeechEnabled(!speech.enabled))}><span />{speech.enabled ? "On" : "Off"}</button>
        </div>
        <fieldset disabled={!model.installed || Boolean(pending)}><legend>When the microphone starts</legend>
          <label><input type="radio" name="whisper-idle-mode" checked={speech.idleMode === "cold"} onChange={() => void updateSpeech(() => api.setSpeechIdleMode("cold"))} />
            <span><strong>Load from disk each time</strong><small>Default · about {speech.coldStartMs == null ? "measured on first use" : `${(speech.coldStartMs / 1000).toFixed(2)} s on this PC`}</small></span>
          </label>
          <label><input type="radio" name="whisper-idle-mode" checked={speech.idleMode === "ram"} onChange={() => void updateSpeech(() => api.setSpeechIdleMode("ram"))} />
            <span><strong>Keep sleeping in RAM</strong><small>Faster wake · about {speech.warmWakeMs == null ? "measured when enabled" : `${(speech.warmWakeMs / 1000).toFixed(2)} s on this PC`}; weights leave the GPU while asleep.</small></span>
          </label>
        </fieldset>
        <p className="whisper-runtime-status" role="status">{!model.installed ? model.externalManaged ? "Local weights found. Prepare the speech runtime to enable the microphone." : "Install this speech model to enable the microphone." : speech.phase === "warming" ? "Loading speech weights into system RAM for standby…" : speech.phase === "error" ? "Could not restore the saved standby mode. Check available RAM and the speech runtime, or select Cold start." : speech.enabled ? speech.idleMode === "ram" ? speech.workerReady ? "Sleeping in system RAM; moves to GPU when dictation starts." : "RAM standby will load before the next recording." : "Loads from disk when you click the microphone." : "Speech is off. The model stays installed on disk."}</p>
      </div> : null}
      <footer><button disabled={runtimeActive || installing || Boolean(pending) || (model.externalManaged && model.installed)} onClick={() => void change(model)}>
        {model.installed && !model.externalManaged ? <Trash2 size={15} /> : model.installed ? <Check size={15} /> : <Download size={15} />}{pending === model.id ? "Working…" : model.installed && model.externalManaged ? "Using local weights" : model.externalManaged ? "Prepare speech runtime" : model.installed ? "Uninstall" : "Install"}
      </button>{model.selectable ? <button className={selectedProfile === model.id ? "active" : ""} disabled={!model.installed || runtimeActive || installing || Boolean(pending)} onClick={() => onSelect(model.id as RuntimeProfile)}>
        {selectedProfile === model.id ? <Check size={15} /> : null}{selectedProfile === model.id ? "Selected" : "Use model"}
      </button> : model.speechLanguage ? <button disabled={!model.installed || installing || Boolean(pending)} aria-label={model.id === speech.modelId ? `${model.label} selected for dictation` : `Use ${model.label} for dictation`} onClick={() => void updateSpeech(() => api.setSpeechModel(model.id))}>
        {model.id === speech.modelId ? <Check size={15} /> : null}{model.id === speech.modelId ? "Dictation selected" : "Use for dictation"}
      </button> : null}</footer>
      <span className="model-license">{model.experimental ? "Experimental · " : ""}{model.license}</span>
    </article>)}</div>
  </section>;
}
