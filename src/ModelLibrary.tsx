import { useCallback, useEffect, useState } from "react";
import { Check, Download, ExternalLink, HardDrive, PackageMinus } from "lucide-react";
import * as api from "./api";
import type { RuntimeProfile } from "./types";
import { ModelDeleteDialog } from "./ModelDeleteDialog";
import { estimateGgufVramRange, filterGroupsByMemoryMode, groupModelVariants, matchingModelVariant, modelMemoryMode } from "./model-variants";

const gb = (bytes: number) => `${(bytes / 1e9).toFixed(3)} GB`;
const exactFileSize = (bytes: number) => `${gb(bytes)} · ${bytes.toLocaleString("en-US")} bytes`;
const downloadLabel = (model: api.InstalledModel) => model.totalBytes > 0
  ? model.externalManaged ? `Existing weights · ${exactFileSize(model.totalBytes)}` : `${exactFileSize(model.totalBytes)} download`
  : model.runtimeConnected ? "Runtime manages weights" : model.installable === false
    ? "External setup · no app download" : model.installed ? "No download required" : "Download size unavailable";
const vramEstimate = (model: api.InstalledModel) => model.selectable && model.backend !== "external" && (model.weightBytes || model.totalBytes)
  ? estimateGgufVramRange(model.weightBytes || model.totalBytes, model.contextTokens, model.vramWeightMultiplier || 1) : null;
export function ModelLibrary({ selectedProfile, onSelect, runtimeActive, onNotice }: {
  selectedProfile: RuntimeProfile; onSelect: (profile: RuntimeProfile) => void;
  runtimeActive: boolean; onNotice: (notice: string) => void;
}) {
  const [library, setLibrary] = useState<api.ModelLibrary | null>(null);
  const [error, setError] = useState("");
  const [category, setCategory] = useState('all');
  const [memoryMode, setMemoryMode] = useState<"all" | "native" | "echo">("all");
  const [pending, setPending] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<api.InstalledModel | null>(null);
  const [quantization, setQuantization] = useState<Record<string, string>>({});
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
      await api.installModel(model.id);
      await refresh();
    } catch (cause) { onNotice(String(cause)); }
    finally { setPending(null); }
  }
  async function deleteModel(plan: api.ModelRemovalPlan) {
    setPending(plan.modelId);
    try {
      if (runtimeActive) await api.stopRuntime();
      await api.uninstallModel(plan.modelId, plan.confirmationToken);
      await refresh();
      setDeleting(null);
      onNotice(`Uninstalled ${plan.label}. It can be installed again from the model library. Shared files and conversation history were kept.`);
    } finally { setPending(null); }
  }
  async function updateSpeech(changeSetting: () => Promise<api.SpeechStatus>) {
    setPending(speech.modelId);
    try { setSpeech(await changeSetting()); }
    catch (cause) { onNotice(String(cause)); }
    finally { setPending(null); }
  }
  const progress = library?.progress;
  const categoryOf = (model: api.InstalledModel) => model.category || (model.speechLanguage ? 'speech' : model.selectable ? 'text' : 'computer-use');
  const categories = [['all','All models'],['text','Text'],['speech','Speech'],['computer-use','Computer use'],['music','Music'],['image','2D images'],['3d','3D assets'],['3d-animation','3D animation'],['2d-animation','2D animation'],['video','Video'],['tts','Speech synthesis'],['voice-cloning','Reference voice'],['ocr','Document extraction'],['omni','Omni'],['policy','Robotics policy']];
  const allModelGroups = groupModelVariants(library?.models || []);
  const modelGroups = filterGroupsByMemoryMode(allModelGroups, memoryMode);
  const visibleModels = modelGroups.filter(group => category === 'all' || categoryOf(group.model) === category);
  return <section className="model-library" aria-label="Install local models">
    <div className="model-library-heading"><div><h2>Model library</h2><p>Browse and install models for local use.</p></div>
      {library ? <span className="model-storage"><HardDrive size={16} /> {gb(library.diskFreeBytes)} free</span> : null}
    </div>
    <p className="model-library-note">Downloads show the pinned package size in GB and bytes. External setup entries have no app download. Text runtime VRAM estimates assume full GPU weight offload and system RAM for KV/state; actual use varies by backend. Media packages may require separate components and a compatible runtime. ECHO is available for text models.</p>
    {runtimeActive ? <p className="model-library-note">Stop the runtime to install or select another model. Uninstalling an active model requires confirmation to stop it first.</p> : null}
    {error ? <div role="alert">{error}<button onClick={() => void refresh()}>Retry</button></div> : null}
    {progress ? <div className="model-install-progress" role={progress.error ? "alert" : "status"}>
      <div><strong>{library?.models.find((model) => model.id === progress.modelId)?.label || progress.modelId}</strong><span>{progress.phase}</span></div>
      {installing && progress.phase !== "uninstalling" ? <><progress aria-label="Model download" value={progress.phase === "preparing" ? undefined : progress.downloadedBytes} max={Math.max(1, progress.totalBytes)} /><small>{progress.currentFile === "Preparing speech runtime" ? "Setting up speech recognition for the microphone." : `${gb(progress.downloadedBytes)} / ${gb(progress.totalBytes)} · ${progress.currentFile || "Preparing download"}`}</small><button onClick={() => void api.cancelModelInstall().catch((cause) => onNotice(String(cause)))}>Cancel download</button></> : null}
      {progress.error ? <p>{progress.error}</p> : null}
    </div> : null}
    <div className="model-category-tabs" role="group" aria-label="Model categories">{categories.map(([id,label]) => <button key={id} aria-pressed={category === id} onClick={() => setCategory(id)}>{label}<span>{modelGroups.filter(group => id === 'all' || categoryOf(group.model) === id).length}</span></button>)}</div>
    <div className="model-category-tabs model-mode-tabs" role="group" aria-label="Model modes">{([['all','All modes'],['native','Native models'],['echo','ECHO models']] as const).map(([id,label]) => <button key={id} aria-pressed={memoryMode === id} onClick={() => setMemoryMode(id)}>{label}<span>{filterGroupsByMemoryMode(allModelGroups, id).filter(group => category === 'all' || categoryOf(group.model) === category).length}</span></button>)}</div>
    <div className="model-library-grid">{visibleModels.map((group) => {
      const remembered = library?.models.find(item => item.id === quantization[group.id]);
      const matchingRemembered = matchingModelVariant(group, remembered);
      const selected = library?.models.find(item => item.id === selectedProfile);
      const matchingSelection = matchingModelVariant(group, selected);
      const chosenId = matchingRemembered?.id || matchingSelection?.id || group.variants[0]?.id || group.id;
      const model = group.variants.find(item => item.id === chosenId) || group.model;
      const profileSelected = group.aliases[selectedProfile] === model.id;
      const vram = vramEstimate(model);
      const modeVariants = group.variants;
      return <article key={group.id} className={`model-library-card ${(model.speechLanguage ? model.id === speech.modelId : profileSelected) ? "selected" : ""}`}>
      <header><div><h3>{group.model.label}</h3><span>{modelMemoryMode(model) === "echo" ? "ECHO" : "Native"} · {model.precision}{model.speechLanguage ? <> · <b>{model.speechLanguage}</b></> : null}</span></div><span className={`model-install-state ${model.installed || model.externalManaged ? "installed" : ""}`}>{model.installed ? model.runtimeReady === false ? "Weights downloaded" : "Installed" : model.externalManaged ? "Local weights found" : model.installable === false ? "Setup needed" : "Not installed"}</span></header>
      <p>{model.description}</p>
      {modeVariants.length > 1 ? <label className="model-quantization-picker">Mode and quantization
        <select aria-label={`Quantization for ${group.model.label}`} value={model.id} disabled={Boolean(pending)} onChange={event => setQuantization(current => ({ ...current, [group.id]: event.target.value }))}>
          {modeVariants.map(variant => { const estimate = vramEstimate(variant); const needsPackageLabel = variant.backend === "external" || modeVariants.some(other => other.id !== variant.id && other.precision === variant.precision && modelMemoryMode(other) === modelMemoryMode(variant)); return <option key={variant.id} value={variant.id}>{modelMemoryMode(variant) === "echo" ? "ECHO" : "Native"} · {variant.precision}{needsPackageLabel ? ` · ${variant.label}` : ""} · {downloadLabel(variant)}{estimate ? ` · ${gb(estimate.minBytes)}–${gb(estimate.maxBytes)} VRAM est.` : " · VRAM estimate unavailable"}</option>; })}
        </select>
      </label> : null}
      <dl><div><dt>{model.selectable ? "Active context" : "Load mode"}</dt><dd>{model.selectable ? `${model.contextTokens.toLocaleString()} tokens` : model.runtimeReady === false ? "Setup needed" : "On demand"}</dd></div><div><dt>Download</dt><dd>{downloadLabel(model)}</dd></div>{vram ? <div><dt>Estimated VRAM (full GPU offload)</dt><dd>{gb(vram.minBytes)}–{gb(vram.maxBytes)}</dd></div> : model.runtimeReady === false || model.installable === false ? <div><dt>Estimated VRAM</dt><dd>Requires a compatible runtime and its complete component set</dd></div> : null}</dl>
      <small>{model.note}</small>
      {model.runtimeConnected ? <p className="model-library-note">Connected runtime. Model weights are managed separately by this runtime.</p> : null}
      {['video','tts','voice-cloning','ocr','omni','policy'].includes(categoryOf(model)) ? <button onClick={()=>window.dispatchEvent(new CustomEvent('opencore-open-studio',{detail:categoryOf(model)}))}>Open Media Studio</button> : null}
      {model.runtimePrecision ? <section className="model-runtime-precision" aria-label={`${model.label} download and runtime precision`}>
        <strong>Download and runtime precision</strong>
        <p>Download: {model.runtimePrecision.sourceFormat}. Runtime: {model.runtimePrecision.runtimeDtype}.</p>
        <p>Estimated runtime memory: {gb(model.runtimePrecision.estimatedRuntimeBytes)}. {model.runtimePrecision.runtimeMemoryNote}</p>
        <p>{model.runtimePrecision.runtimeComponent}</p>
      </section> : null}
      {model.speechLanguage && model.id === speech.modelId ? <div className="whisper-controls" aria-label="Speech settings">
        <div className="whisper-enable-row"><div><strong>Microphone dictation</strong><small>GPU memory is released after transcription; RAM standby keeps only CPU weights.</small></div>
          <button type="button" role="switch" aria-checked={speech.enabled} aria-label="Speech to text" className={`whisper-toggle ${speech.enabled ? "on" : ""}`}
            disabled={!model.installed || Boolean(pending)} onClick={() => void updateSpeech(() => api.setSpeechEnabled(!speech.enabled))}><span />{speech.enabled ? "On" : "Off"}</button>
        </div>
        <fieldset disabled={!model.installed || Boolean(pending)}><legend>When the microphone starts</legend>
          <label><input type="radio" name="whisper-idle-mode" checked={speech.idleMode === "cold"} onChange={() => void updateSpeech(() => api.setSpeechIdleMode("cold"))} />
            <span><strong>Load from disk each time</strong><small>Default · about {speech.coldStartMs == null ? "measured on first use" : `${(speech.coldStartMs / 1000).toFixed(2)} s on this device`}</small></span>
          </label>
          <label><input type="radio" name="whisper-idle-mode" checked={speech.idleMode === "ram"} onChange={() => void updateSpeech(() => api.setSpeechIdleMode("ram"))} />
            <span><strong>Keep sleeping in RAM</strong><small>Faster wake · about {speech.warmWakeMs == null ? "measured when enabled" : `${(speech.warmWakeMs / 1000).toFixed(2)} s on this device`}; weights leave the GPU while asleep.</small></span>
          </label>
        </fieldset>
        <p className="whisper-runtime-status" role="status">{!model.installed ? model.externalManaged ? "Local weights found. Prepare the speech runtime to enable the microphone." : "Install this speech model to enable the microphone." : speech.phase === "warming" ? "Loading speech weights into system RAM for standby…" : speech.phase === "error" ? "Could not restore the saved standby mode. Check available RAM and the speech runtime, or select Cold start." : speech.enabled ? speech.idleMode === "ram" ? speech.workerReady ? "Sleeping in system RAM; moves to GPU when dictation starts." : "RAM standby will load before the next recording." : "Loads from disk when you click the microphone." : "Speech is off. The model stays installed on disk."}</p>
      </div> : null}
      {model.runtimeReady === false && <small className="model-setup-note">{model.installed ? 'Weights downloaded · runtime setup required' : 'Runtime setup required'}</small>}
      <footer>{!model.installed && model.installable !== false ? <button disabled={runtimeActive || installing || Boolean(pending)} onClick={() => void change(model)}>
        <Download size={15} />{pending === model.id ? "Working…" : model.externalManaged ? model.category === 'music' ? 'Use existing weights' : "Prepare speech runtime" : "Install"}
      </button> : null}{model.selectable ? <button className={profileSelected ? "active" : ""} disabled={!model.installed || runtimeActive || installing || Boolean(pending)} onClick={() => onSelect(model.id as RuntimeProfile)}>
        {profileSelected ? <Check size={15} /> : null}{profileSelected ? "Selected" : "Use model"}
      </button> : model.speechLanguage ? <button disabled={!model.installed || installing || Boolean(pending)} aria-label={model.id === speech.modelId ? `${model.label} selected for dictation` : `Use ${model.label} for dictation`} onClick={() => void updateSpeech(() => api.setSpeechModel(model.id))}>
        {model.id === speech.modelId ? <Check size={15} /> : null}{model.id === speech.modelId ? "Dictation selected" : "Use for dictation"}
      </button> : null}{model.setupUrl && <a href={model.setupUrl} target="_blank" rel="noreferrer" className="model-setup-link"><ExternalLink size={15} />Setup</a>}{model.installed ? <button className="model-delete-button" disabled={installing || Boolean(pending)} aria-label={`Uninstall ${model.label}`} onClick={() => setDeleting(model)}><PackageMinus size={15} />Uninstall</button> : null}</footer>
      <span className="model-license">{model.experimental ? "Experimental · " : ""}{model.license}</span>
    </article>; })}</div>
    {deleting ? <ModelDeleteDialog model={deleting} runtimeActive={runtimeActive} onCancel={() => setDeleting(null)} onDelete={deleteModel} /> : null}
  </section>;
}
