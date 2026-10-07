import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Download, ExternalLink, HardDrive, PackageMinus, Search } from "lucide-react";
import * as api from "./api";
import type { RuntimeProfile } from "./types";
import { ModelDeleteDialog } from "./ModelDeleteDialog";
import { estimateGgufVramRange, filterGroupsByMemoryMode, groupModelVariants, matchingModelVariant, modelMemoryMode } from "./model-variants";
import { speechLoadingMessage } from "./speech-progress";
import { SpeechRuntimeControls } from "./SpeechRuntimeControls";
import "./ModelLibrary.css";

const gb = (bytes: number) => `${(bytes / 1e9).toFixed(3)} GB`;
const exactFileSize = (bytes: number) => `${gb(bytes)} · ${bytes.toLocaleString("en-US")} bytes`;
const downloadLabel = (model: api.InstalledModel) => model.totalBytes > 0
  ? model.id === 'phonon-2' && model.weightBytes ? `${Math.ceil(model.weightBytes / 1e6)} MB weights + ${((model.totalBytes - model.weightBytes) / 1e6).toFixed(1)} MB support files`
    : model.externalManaged ? `Existing weights · ${exactFileSize(model.totalBytes)}` : `${exactFileSize(model.totalBytes)} download`
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
  const [query, setQuery] = useState('');
  const [memoryMode, setMemoryMode] = useState<"all" | "native" | "echo">("all");
  const [pending, setPending] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<api.InstalledModel | null>(null);
  const [selectedModes, setSelectedModes] = useState<Record<string, "native" | "echo">>({});
  const [quantization, setQuantization] = useState<Record<string, string>>({});
  const [speech, setSpeech] = useState<api.SpeechStatus>({ modelId: "whisper-large-v3-turbo", installed: false, enabled: false, idleMode: "cold", workerReady: false, coldStartMs: null, warmWakeMs: null, phase: "off" });
  const refresh = useCallback(async (fresh = true) => {
    // A slow speech worker must not block browsing the model catalog.
    void api.speechStatus().then(setSpeech).catch(() => {});
    try {
      const models = await api.modelLibrary({ fresh });
      setLibrary(models); setError("");
    }
    catch (cause) { setError(String(cause)); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!speech.enabled && !pending) return;
    let disposed = false;
    let checking = false;
    const timer = window.setInterval(async () => {
      if (checking) return;
      checking = true;
      try { const status = await api.speechStatus(); if (!disposed) setSpeech(status); }
      catch { /* The controls report command failures. */ }
      finally { checking = false; }
    }, 1000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [speech.enabled, pending]);
  const installing = Boolean(library?.progress && ["preparing", "downloading", "verifying", "uninstalling"].includes(library.progress.phase));
  useEffect(() => {
    if (!installing && !pending) return;
    const timer = window.setInterval(() => void refresh(false), 1000);
    return () => window.clearInterval(timer);
  }, [installing, pending, refresh]);
  async function change(model: api.InstalledModel) {
    setPending(model.id);
    try {
      await api.installModel(model.id);
      await refresh();
    } catch (cause) { onNotice(String(cause)); }
    finally { setPending(null); }
  }
  async function prepareRuntime(model: api.InstalledModel) {
    if (model.preparedRuntime?.kind !== 'woof-mlx-affine4-bf16' || !model.sourceDownloaded || runtimeActive || pending) return;
    setPending(model.id);
    try {
      const files = await api.choosePreparedModelFiles();
      if (!files) return;
      await api.registerPreparedModel(model.id, files.path, files.manifestPath);
      await refresh();
      onNotice(`${model.label} is ready for the Windows runtime.`);
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
  const categories = [['all','All models'],['installed','Installed'],['text','Text'],['speech','Speech'],['computer-use','Computer use'],['music','Music'],['image','2D images'],['3d','3D assets'],['3d-animation','3D animation'],['2d-animation','2D animation'],['video','Video'],['tts','Speech synthesis'],['voice-cloning','Reference voice'],['ocr','Document extraction'],['omni','Omni'],['policy','Robotics policy']];
  const modelById = useMemo(() => new Map((library?.models || []).map(model => [model.id, model])), [library?.models]);
  const allModelGroups = useMemo(() => groupModelVariants(library?.models || []), [library?.models]);
  const modelGroups = useMemo(() => filterGroupsByMemoryMode(allModelGroups, memoryMode), [allModelGroups, memoryMode]);
  const categoryMatches = (group: typeof modelGroups[number], id: string) => id === 'all' || (id === 'installed' ? group.variants.some(model => model.installed) : categoryOf(group.model) === id);
  const categoryGroups = category === 'installed' ? modelGroups.flatMap(group => {
    const variants = group.variants.filter(model => model.installed);
    return variants.length ? [{ ...group, variants }] : [];
  }) : modelGroups;
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const visibleModels = categoryGroups.filter(group => categoryMatches(group, category)
    && words.every(word => `${group.model.label} ${group.model.description} ${group.variants.map(model => `${model.id} ${model.label} ${model.precision}`).join(' ')}`.toLowerCase().includes(word)));
  return <section className="model-library" aria-label="Install local models">
    <div className="model-library-heading"><div><h2>Model library</h2><p>Browse and install models for local use.</p></div>
      {library ? <span className="model-storage"><HardDrive size={16} /> {gb(library.diskFreeBytes)} free</span> : null}
    </div>
    <p className="model-library-note">Downloads show the pinned package size in GB and bytes. External setup entries have no app download. Text runtime VRAM estimates assume full GPU weight offload and system RAM for KV/state; actual use varies by backend. Media packages may require separate components and a compatible runtime. ECHO is available for text models.</p>
    {runtimeActive ? <p className="model-library-note">Use the bottom model menu to change an idle running model. Stop the runtime before installing. Uninstalling an active model requires confirmation to stop it first.</p> : null}
    {error ? <div role="alert">{error}<button onClick={() => void refresh()}>Retry</button></div> : null}
    {!library && !error ? <p role="status">Loading model options… You can choose a category while they load.</p> : null}
    {progress ? <div className="model-install-progress" role={progress.error ? "alert" : "status"}>
      <div><strong>{library?.models.find((model) => model.id === progress.modelId)?.label || progress.modelId}</strong><span>{progress.phase}</span></div>
      {installing && progress.phase !== "uninstalling" ? <><progress aria-label="Model download" value={progress.phase === "preparing" ? undefined : progress.downloadedBytes} max={Math.max(1, progress.totalBytes)} /><small>{progress.currentFile === "Preparing speech runtime" ? "Setting up speech recognition for the microphone." : `${gb(progress.downloadedBytes)} / ${gb(progress.totalBytes)} · ${progress.currentFile || "Preparing download"}`}</small><button onClick={() => void api.cancelModelInstall().catch((cause) => onNotice(String(cause)))}>Cancel download</button></> : null}
      {progress.error ? <p>{progress.error}</p> : null}
    </div> : null}
    <div className="model-filter-row"><span className="model-filter-label">Category</span><div className="model-category-tabs" role="group" aria-label="Model categories">{categories.map(([id,label]) => <button key={id} aria-pressed={category === id} onClick={() => setCategory(id)}>{label}<span>{modelGroups.filter(group => categoryMatches(group, id)).length}</span></button>)}</div></div>
    <div className="model-filter-row"><span className="model-filter-label">Memory mode</span><div className="model-category-tabs model-mode-tabs" role="group" aria-label="Model modes">{([['all','All modes'],['native','Native models'],['echo','ECHO models']] as const).map(([id,label]) => <button key={id} aria-pressed={memoryMode === id} onClick={() => setMemoryMode(id)}>{label}<span>{filterGroupsByMemoryMode(allModelGroups, id).filter(group => categoryMatches(group, category)).length}</span></button>)}</div></div>
    <div className="model-library-search"><Search size={17} aria-hidden="true" /><input aria-label="Search models" placeholder="Find a model or quantization…" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Escape') setQuery(''); }} />{query ? <button onClick={() => setQuery('')}>Clear search</button> : null}<span>{visibleModels.length} models</span></div>
    {library && visibleModels.length === 0 ? <p role="status">No models match these filters.</p> : null}
    <div className="model-library-grid">{visibleModels.map((group) => {
      const remembered = modelById.get(quantization[group.id]);
      const selected = modelById.get(selectedProfile);
      // Work within both active filters so Installed never offers missing deliveries.
      const availableModes = [...new Set(group.variants.map(modelMemoryMode))];
      const preferredMode = selectedModes[group.id] || (remembered ? modelMemoryMode(remembered) : selected && matchingModelVariant(group, selected) ? modelMemoryMode(selected) : modelMemoryMode(group.model));
      const activeMode = availableModes.includes(preferredMode) ? preferredMode : availableModes[0];
      const modeVariants = group.variants.filter(variant => modelMemoryMode(variant) === activeMode);
      const modeGroup = {...group, variants: modeVariants};
      const matchingRemembered = matchingModelVariant(modeGroup, remembered);
      const matchingSelection = matchingModelVariant(modeGroup, selected);
      const model = matchingRemembered || matchingSelection || modeVariants[0] || group.model;
      const profileSelected = group.aliases[selectedProfile] === model.id;
      const vram = vramEstimate(model);
      const showQuantization = modeVariants.length > 1 || (category !== "installed" && categoryOf(model) === "text" && model.backend === "gguf");
      const preparedRuntime = model.preparedRuntime?.kind === 'woof-mlx-affine4-bf16' ? model.preparedRuntime : undefined;
      const selectedPhonon = model.id === 'phonon-2' && speech.modelId === model.id;
      const phononPrecision = speech.runtimePrecision || 'bf16';
      const loadingSpeech = speechLoadingMessage(speech);
      return <article key={group.id} className={`model-library-card ${(model.speechLanguage ? model.id === speech.modelId : profileSelected) ? "selected" : ""}`}>
      <header><div><h3>{group.model.label}</h3><span>{modelMemoryMode(model) === "echo" ? "ECHO" : "Native"} · {model.precision}{model.speechLanguage ? <> · <b>{model.speechLanguage}</b></> : null}</span></div><span className={`model-install-state ${model.installed || model.externalManaged ? "installed" : ""}`}>{model.installed ? model.runtimeReady === false ? "Weights downloaded" : "Installed" : model.externalManaged ? "Local weights found" : model.installable === false ? "Setup needed" : "Not installed"}</span></header>
      <p>{model.description}</p>
      {availableModes.length > 1 || showQuantization ? <div className="model-variant-controls">
        {availableModes.length > 1 ? <label className="model-quantization-picker">Memory mode
          <select aria-label={`Memory mode for ${group.model.label}`} value={activeMode} disabled={Boolean(pending)} onChange={event => {
            setSelectedModes(current => ({...current, [group.id]: event.target.value as "native" | "echo"}));
            setQuantization(current => ({...current, [group.id]: model.id}));
          }}>
            {availableModes.map(mode => <option key={mode} value={mode}>{mode === "echo" ? "ECHO" : "Native"}</option>)}
          </select>
        </label> : null}
        {showQuantization ? <label className="model-quantization-picker">Quantization
          <select aria-label={`Quantization for ${group.model.label}`} value={model.id} disabled={Boolean(pending) || modeVariants.length <= 1} onChange={event => setQuantization(current => ({...current, [group.id]: event.target.value}))}>
            {modeVariants.map(variant => { const estimate = vramEstimate(variant); const needsPackageLabel = variant.backend === "external" || modeVariants.some(other => other.id !== variant.id && other.precision === variant.precision); return <option key={variant.id} value={variant.id}>{variant.precision}{needsPackageLabel ? ` · ${variant.label}` : ""} · {downloadLabel(variant)}{estimate ? ` · ${gb(estimate.minBytes)}–${gb(estimate.maxBytes)} VRAM est.` : " · VRAM estimate unavailable"}</option>; })}
          </select>
          {modeVariants.length === 1 ? <small>Only verified quantization available for this model.</small> : null}
        </label> : null}
      </div> : null}
      <dl><div><dt>{model.selectable ? "Active context" : "Load mode"}</dt><dd>{model.selectable ? `${model.contextTokens.toLocaleString()} tokens` : model.runtimeReady === false ? "Setup needed" : "On demand"}</dd></div><div><dt>Download</dt><dd>{downloadLabel(model)}</dd></div>{vram ? <div><dt>Estimated VRAM (full GPU offload)</dt><dd>{gb(vram.minBytes)}–{gb(vram.maxBytes)}</dd></div> : model.runtimeReady === false || model.installable === false ? <div><dt>Estimated VRAM</dt><dd>Requires a compatible runtime and its complete component set</dd></div> : null}</dl>
      <small>{model.note}</small>
      {model.runtimeConnected ? <p className="model-library-note">Connected runtime. Model weights are managed separately by this runtime.</p> : null}
      {preparedRuntime ? <p className="model-library-note">Prepared Windows runtime · BF16 GGUF · {gb(preparedRuntime.bytes)}. {model.preparedReady ? 'Verified and ready.' : model.sourceDownloaded ? 'Choose the prepared GGUF and its adjacent conversion manifest to verify setup.' : 'Install the original source checkpoint before setting up the prepared runtime.'}</p> : null}
      {['video','tts','voice-cloning','ocr','omni','policy'].includes(categoryOf(model)) ? <button onClick={()=>window.dispatchEvent(new CustomEvent('opencore-open-studio',{detail:categoryOf(model)}))}>Open Media Studio</button> : null}
      {model.runtimePrecision ? <section className="model-runtime-precision" aria-label={`${model.label} download and runtime precision`}>
        <strong>Download and runtime precision</strong>
        <p>Download: {model.runtimePrecision.sourceFormat}{model.id === 'phonon-2' ? ` · ${Math.ceil((model.weightBytes || 163515201) / 1e6)} MB` : ''}. Runtime: {selectedPhonon ? phononPrecision === 'original' ? 'Original (164 MB)' : phononPrecision.toUpperCase() : model.runtimePrecision.runtimeDtype}.</p>
        {selectedPhonon && phononPrecision === 'original' ? <p>Uses the original checkpoint with the publisher’s packed CPU kernels and a lightweight audio runtime. Runtime RAM is measured separately from the 164 MB download.</p>
          : model.id === 'phonon-2' && !selectedPhonon ? <p>{model.runtimePrecision.runtimeMemoryNote}</p>
          : <p>Estimated weight memory: {gb(selectedPhonon ? phononPrecision === 'bf16' ? 1_255_000_000 : 2_510_000_000 : model.runtimePrecision.estimatedRuntimeBytes)}. {selectedPhonon ? 'Buffers and runtime overhead use additional memory.' : model.runtimePrecision.runtimeMemoryNote}</p>}
        {selectedPhonon && speech.runtimeResidentBytes != null ? <p>Last measured startup RAM: {gb(speech.runtimeResidentBytes)}. Includes the worker and its loaded weights.</p> : null}
        <p>{model.runtimePrecision.runtimeComponent}</p>
      </section> : null}
      {model.speechLanguage && model.id === speech.modelId ? <div className="whisper-controls" aria-label="Speech settings">
        <div className="whisper-enable-row"><div><strong>Microphone dictation</strong><small>{selectedPhonon && phononPrecision === 'original' ? 'Runs on CPU; RAM standby keeps it ready between recordings.' : 'GPU memory is released after transcription; RAM standby keeps only CPU weights.'}</small></div>
          <button type="button" role="switch" aria-checked={speech.enabled} aria-label="Speech to text" className={`whisper-toggle ${speech.enabled ? "on" : ""}`}
            disabled={!model.installed || Boolean(pending) && !speech.enabled} onClick={() => void updateSpeech(() => api.setSpeechEnabled(!speech.enabled))}><span />{speech.enabled ? "On" : "Off"}</button>
        </div>
        {selectedPhonon ? <fieldset disabled={!model.installed || Boolean(pending) || speech.phase === 'recording'}><legend>Phonon-2 version</legend>
          <label><input type="radio" name="phonon-runtime-precision" checked={phononPrecision === 'original'} onChange={() => void updateSpeech(() => api.setSpeechRuntimePrecision('original'))} /><span><strong>Original (164 MB)</strong><small>Original checkpoint. Prepared automatically on first use.</small></span></label>
          <label><input type="radio" name="phonon-runtime-precision" checked={phononPrecision === 'bf16'} onChange={() => void updateSpeech(() => api.setSpeechRuntimePrecision('bf16'))} /><span><strong>BF16</strong><small>About 1.25 GB of runtime weights.</small></span></label>
          <label><input type="radio" name="phonon-runtime-precision" checked={phononPrecision === 'fp32'} onChange={() => void updateSpeech(() => api.setSpeechRuntimePrecision('fp32'))} /><span><strong>FP32</strong><small>About 2.51 GB of runtime weights.</small></span></label>
        </fieldset> : null}
        <fieldset disabled={!model.installed}><legend>When the microphone starts</legend>
          <label><input type="radio" name="whisper-idle-mode" checked={speech.idleMode === "cold"} onChange={() => void updateSpeech(() => api.setSpeechIdleMode("cold"))} />
            <span><strong>Load from disk each time</strong><small>Cold start · {selectedPhonon && phononPrecision !== 'original' ? 'starts runtime and loads its verified dense cache when available; ' : ''}{speech.coldStartMs == null ? "startup time is measured on first use" : `about ${(speech.coldStartMs / 1000).toFixed(2)} s on this device`}</small></span>
          </label>
          <label><input type="radio" name="whisper-idle-mode" checked={speech.idleMode === "ram"} disabled={Boolean(pending)} onChange={() => void updateSpeech(() => api.setSpeechIdleMode("ram"))} />
            <span><strong>Keep sleeping in RAM</strong><small>Recommended for frequent dictation · {speech.warmWakeMs == null ? "wake time is measured when enabled" : `about ${(speech.warmWakeMs / 1000).toFixed(2)} s on this device`}; CPU weights stay in RAM and leave the GPU while asleep.</small></span>
          </label>
        </fieldset>
        {selectedPhonon ? <SpeechRuntimeControls speech={speech} onRefresh={refresh} onNotice={onNotice} /> : null}
        <p className="whisper-runtime-status" role="status">{!model.installed ? model.externalManaged ? "Local weights found. Prepare the speech runtime to enable the microphone." : "Install this speech model to enable the microphone." : loadingSpeech || (speech.phase === "error" ? "Could not restore the saved standby mode. Check available RAM and the speech runtime, or select Cold start." : speech.enabled ? speech.idleMode === "ram" ? speech.workerReady ? selectedPhonon && phononPrecision === 'original' ? "Sleeping in system RAM; ready for CPU dictation." : "Sleeping in system RAM; moves to GPU when dictation starts." : "RAM standby will load before the next recording." : "Loads from disk when you click the microphone." : "Speech is off. The model stays installed on disk.")}</p>
      </div> : null}
      {model.runtimeReady === false && <small className="model-setup-note">{model.installed ? 'Weights downloaded · runtime setup required' : 'Runtime setup required'}</small>}
      <footer>{preparedRuntime && model.runtimeReady === false ? <button disabled={!model.sourceDownloaded || runtimeActive || installing || Boolean(pending)} onClick={() => void prepareRuntime(model)}>{pending === model.id ? 'Verifying setup…' : 'Use prepared GGUF'}</button> : null}{!model.installed && model.installable !== false ? <button disabled={runtimeActive || installing || Boolean(pending)} onClick={() => void change(model)}>
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
