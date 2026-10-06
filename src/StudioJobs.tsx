import { useEffect, useState } from 'react';
import { CircleStop, FolderOpen, Play, RefreshCw } from 'lucide-react';
import * as api from './api';
import { StudioModelSetup } from './StudioModelSetup';
import { selectStudioModel } from './studio-model-selection';
import './GameDevStudio.css';

export const GAME_DEV_CATEGORIES = [
  ['image', '2D images'],
  ['3d', '3D assets'],
  ['3d-animation', '3D animation'],
  ['2d-animation', '2D animation'],
] as const;
export const ASSET_CATEGORIES = GAME_DEV_CATEGORIES;

type Control =
  | { key: string; label: string; kind: 'number'; value: number; min: number; max: number; step?: number; help?: string }
  | { key: string; label: string; kind: 'text' | 'textarea'; value: string; help?: string }
  | { key: string; label: string; kind: 'select'; value: string; options: string[]; help?: string }
  | { key: string; label: string; kind: 'checkbox'; value: boolean; help?: string };
type StudioPreset = { id: string; name: string; modelId: string; prompt: string; inputPath: string; advancedJson: string; controls: Record<string, unknown> };
const presetStorageKey = (category: string) => `opencore.game-dev-studio.presets.v1.${category}`;

const settingsForCategory: Record<string, Control[]> = {
  image: [
    { key: 'negativePrompt', label: 'Negative prompt', kind: 'textarea', value: '', help: 'Describe elements to avoid.' },
    { key: 'seed', label: 'Seed', kind: 'number', value: 831001, min: 0, max: 4294967295, help: 'Reuse a seed to reproduce a result.' },
    { key: 'steps', label: 'Inference steps', kind: 'number', value: 30, min: 1, max: 100 },
    { key: 'width', label: 'Width', kind: 'number', value: 768, min: 128, max: 2048, step: 64 },
    { key: 'height', label: 'Height', kind: 'number', value: 768, min: 128, max: 2048, step: 64 },
    { key: 'guidanceScale', label: 'Guidance scale', kind: 'number', value: 4, min: 0, max: 30, step: 0.1 },
    { key: 'numImages', label: 'Images per job', kind: 'number', value: 1, min: 1, max: 8 },
    { key: 'outputFormat', label: 'Output format', kind: 'select', value: 'png', options: ['png', 'webp', 'jpeg'] },
  ],
  '3d': [
    { key: 'seed', label: 'Seed', kind: 'number', value: 831001, min: 0, max: 4294967295 },
    { key: 'resolution', label: 'Mesh resolution', kind: 'number', value: 256, min: 32, max: 512, step: 32, help: 'Higher resolution uses more time and memory.' },
    { key: 'chunkSize', label: 'Render chunk size', kind: 'number', value: 8192, min: 256, max: 32768, step: 256 },
    { key: 'outputFormat', label: 'Mesh format', kind: 'select', value: 'glb', options: ['glb', 'obj', 'ply'] },
  ],
  '3d-animation': [
    { key: 'motionPrompt', label: 'Motion description', kind: 'textarea', value: '', help: 'Describe pose, motion, timing, and acting.' },
    { key: 'seed', label: 'Seed', kind: 'number', value: 831001, min: 0, max: 4294967295 },
    { key: 'durationSeconds', label: 'Duration (seconds)', kind: 'number', value: 4, min: 1, max: 60, step: 0.5 },
    { key: 'fps', label: 'Frames per second', kind: 'number', value: 24, min: 1, max: 120 },
    { key: 'frameCount', label: 'Frame count', kind: 'number', value: 96, min: 1, max: 2400 },
    { key: 'loop', label: 'Loop animation', kind: 'checkbox', value: false },
    { key: 'outputFormat', label: 'Output format', kind: 'select', value: 'glb', options: ['glb', 'fbx', 'bvh'] },
  ],
  '2d-animation': [
    { key: 'motionPrompt', label: 'Motion description', kind: 'textarea', value: '', help: 'Describe the action and how it should start and end.' },
    { key: 'seed', label: 'Seed', kind: 'number', value: 831001, min: 0, max: 4294967295 },
    { key: 'steps', label: 'Inference steps', kind: 'number', value: 25, min: 1, max: 100 },
    { key: 'width', label: 'Width', kind: 'number', value: 512, min: 128, max: 2048, step: 64 },
    { key: 'height', label: 'Height', kind: 'number', value: 512, min: 128, max: 2048, step: 64 },
    { key: 'frameCount', label: 'Frame count', kind: 'number', value: 16, min: 2, max: 240 },
    { key: 'fps', label: 'Frames per second', kind: 'number', value: 12, min: 1, max: 60 },
    { key: 'loop', label: 'Loop animation', kind: 'checkbox', value: false },
    { key: 'outputFormat', label: 'Output format', kind: 'select', value: 'gif', options: ['gif', 'webp'] },
  ],
};

const active = (job: api.StudioJob) => ['queued', 'starting', 'running'].includes(job.status);

function controlsForModel(category: string, modelId: string): Control[] {
  const controls = settingsForCategory[category] || [];
  if (modelId === 'trellis-2-4b') return controls.filter(control => control.key !== 'chunkSize').map(control => control.key === 'resolution' ? {...control, kind: 'number', value: 512, min: 512, max: 1536, step: 512, help: 'Publisher pipeline resolutions: 512, 1024, or 1536. Requires at least 24 GB VRAM.'} : control) as Control[];
  if (modelId.startsWith('wan-animate-2-')) {
    const distilled = modelId.endsWith('distilled');
    const changes: Record<string, number | string> = {steps: distilled ? 10 : 40, width: 640, height: 800, frameCount: 81, fps: 24, outputFormat: 'mp4'};
    return [...controls.filter(control => control.key !== 'motionPrompt').map(control => control.key in changes ? {...control, value: changes[control.key], ...(['width', 'height'].includes(control.key) ? {step: 32} : {}), ...(control.key === 'outputFormat' ? {options: ['mp4', 'gif', 'webp']} : {})} as Control : control),
      {key: 'drivingVideoPath', label: 'Driving video path', kind: 'text', value: '', help: 'Required: the video that drives the character’s motion.'},
      ...(distilled ? [{key: 'guidanceScale', label: 'Guidance scale', kind: 'number', value: 1, min: 1, max: 1}, {key: 'flowSolver', label: 'Flow solver', kind: 'select', value: 'euler', options: ['euler']}] as Control[] : [])];
  }
  const values: Record<string, number> = modelId === 'qwen-image-21' ? {steps: 40, width: 2048, height: 2048}
    : modelId.startsWith('flux-2-klein') ? {steps: modelId.includes('base') ? 50 : 4, guidanceScale: modelId.includes('base') ? 4 : 1, width: 1024, height: 1024}
    : modelId === 'z-image-turbo' ? {steps: 9, guidanceScale: 0, width: 1024, height: 1024}
    : {};
  return controls.map(control => control.kind === 'number' && control.key in values ? {...control, value: values[control.key]} : control);
}

export function StudioJobs({ category, onNotice }: { category: string; onNotice: (message: string) => void }) {
  const [jobs, setJobs] = useState<api.StudioJob[]>([]);
  const [error, setError] = useState('');
  const [previews, setPreviews] = useState<Record<string, { dataUrl: string; mime: string }>>({});
  async function refresh() { try { setJobs((await api.listStudioJobs()).filter(job => job.category === category)); } catch (cause) { setError(String(cause)); } }
  useEffect(() => { void refresh(); const timer = setInterval(() => void refresh(), 1500); return () => clearInterval(timer); }, [category]);
  async function cancel(id: string) { try { await api.cancelStudioJob(id); await refresh(); } catch (cause) { onNotice(String(cause)); } }
  async function preview(job: api.StudioJob, path: string) { try { const value = await api.studioOutputPreview(job.id, path); setPreviews(current => ({ ...current, [path]: value })); } catch (cause) { onNotice(String(cause)); } }
  return <section className="studio-jobs" aria-label="Generation jobs"><header><h2>Generations</h2><button onClick={() => void refresh()} aria-label="Refresh generations"><RefreshCw size={16} /></button></header>
    {error && <p role="alert">{error}</p>}
    {!jobs.length && <p>No generations yet. Submit a prompt here or use the category skill in chat.</p>}
    {jobs.map(job => <article key={job.id} className="studio-job"><header><div><strong>{String(job.request.settings.title || job.request.modelId)}</strong><small>{job.status} · {job.stage}</small></div>{active(job) && <button onClick={() => void cancel(job.id)}><CircleStop size={15} />Cancel</button>}</header>
      <p>{job.request.prompt}</p>
      {job.error && <p role="alert">{job.error}</p>}
      <details><summary>Prompt and generation settings</summary><pre>{JSON.stringify(job.request, null, 2)}</pre></details>
      {Object.keys(job.progress).length > 0 && <details><summary>Progress</summary><pre>{JSON.stringify(job.progress, null, 2)}</pre></details>}
      {job.outputs.map(path => <div className="studio-output" key={path}><span>{path.split(/[\\/]/).pop()}</span><button onClick={() => void api.openStudioOutput(job.id, path).catch(cause => onNotice(String(cause)))}><FolderOpen size={14} />Open folder</button>{/\.(png|jpe?g|webp|flac|wav|mp3|mp4|webm|gif)$/i.test(path) && <button onClick={() => void preview(job, path)}>Preview</button>}
        {previews[path] && (previews[path].mime.startsWith('image/') ? <img src={previews[path].dataUrl} alt="Generated output" /> : previews[path].mime.startsWith('audio/') ? <audio controls src={previews[path].dataUrl} /> : <video controls src={previews[path].dataUrl} />)}</div>)}
      {!active(job) && <button onClick={() => void api.submitStudioJob({ ...job.request, conversationId: null }).then(() => refresh()).catch(cause => onNotice(String(cause)))}>Generate another version</button>}
    </article>)}
  </section>;
}

export function GenerationForm({ category, onNotice }: { category: string; onNotice: (message: string) => void }) {
  const [models, setModels] = useState<api.InstalledModel[]>([]);
  const [modelId, setModelId] = useState('');
  const [prompt, setPrompt] = useState('');
  const [title, setTitle] = useState('');
  const [style, setStyle] = useState('');
  const [lyrics, setLyrics] = useState('');
  const [inputPath, setInputPath] = useState('');
  const [advancedJson, setAdvancedJson] = useState('{}');
  const [controls, setControls] = useState<Record<string, unknown>>({});
  const [presets, setPresets] = useState<StudioPreset[]>([]);
  const [presetId, setPresetId] = useState('');
  const [presetName, setPresetName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [runtime, setRuntime] = useState<api.StudioRuntime | null>(null);
  const [connecting, setConnecting] = useState(false);
  const customControls = controlsForModel(category, modelId);
  const selected = models.find(model => model.id === modelId);
  const connected = runtime?.modelId === modelId || Boolean(selected?.runtimeConnected);
  const builtInService = ['yue2', 'whisper-large-v3-turbo', 'whisper-large-v3', 'phonon-2'].includes(modelId);
  const canGenerate = Boolean(selected && (builtInService ? selected.installed : connected && (selected.installed || runtime?.runner && runtime?.sourceDir)));
  const needsImage = ['triposr', 'triposg', 'trellis-image-large', 'trellis-2-4b', 'hunyuan-3d-21', 'pixal3d', 'spar3d'].includes(modelId) || modelId.startsWith('wan-animate-2-');
  const needsDrivingVideo = modelId.startsWith('wan-animate-2-');

  async function refreshModels() {
    const library = await api.modelLibrary();
    const candidates = library.models.filter(model => model.category === category);
    setModels(candidates);
    setModelId(current => selectStudioModel(candidates, category, current));
  }

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const library = await api.modelLibrary();
        if (alive) {
          const candidates = library.models.filter(model => model.category === category);
          setModels(candidates);
          setModelId(current => selectStudioModel(candidates, category, current));
        }
      } catch (cause) { if (alive) setError(String(cause)); }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [category]);
  useEffect(() => { if (!presetId) setControls(Object.fromEntries(customControls.map(control => [control.key, control.value]))); }, [category, modelId]);
  useEffect(() => {
    setPresetId('');
    try {
      const value = JSON.parse(window.localStorage.getItem(presetStorageKey(category)) || '[]');
      setPresets(Array.isArray(value) ? value.filter(item => item && typeof item.id === 'string' && typeof item.name === 'string') : []);
    } catch { setPresets([]); }
  }, [category]);
  useEffect(() => {
    let alive = true; setRuntime(null);
    if (modelId) void api.studioRuntime(modelId).then(value => {if (alive) setRuntime(value);}).catch(() => {if (alive) setRuntime(null);});
    return () => {alive = false;};
  }, [modelId]);

  function setControl(key: string, value: unknown) { setControls(current => ({ ...current, [key]: value })); }
  function savePreset() {
    const name = presetName.trim();
    if (!name) { setError('Enter a name for this preset.'); return; }
    const id = presetId || (globalThis.crypto?.randomUUID?.() || `preset-${Date.now()}`);
    const next = [...presets.filter(item => item.id !== id), { id, name, modelId, prompt, inputPath, advancedJson, controls }];
    try {
      window.localStorage.setItem(presetStorageKey(category), JSON.stringify(next));
      setPresets(next); setPresetId(id); setPresetName(name); setError('');
      onNotice(`Preset “${name}” saved.`);
    } catch (cause) { setError(`Could not save preset: ${String(cause)}`); }
  }
  function loadPreset(id: string) {
    const preset = presets.find(item => item.id === id);
    setPresetId(id); setPresetName(preset?.name || '');
    if (!preset) return;
    setModelId(preset.modelId); setPrompt(preset.prompt); setInputPath(preset.inputPath);
    setAdvancedJson(preset.advancedJson); setControls(preset.controls);
  }
  function deletePreset() {
    if (!presetId) return;
    const next = presets.filter(item => item.id !== presetId);
    try { window.localStorage.setItem(presetStorageKey(category), JSON.stringify(next)); setPresets(next); setPresetId(''); setPresetName(''); }
    catch (cause) { setError(`Could not remove preset: ${String(cause)}`); }
  }
  function resetSettings() {
    setPresetId(''); setPresetName(''); setPrompt(''); setInputPath(''); setAdvancedJson('{}');
    setControls(Object.fromEntries(customControls.map(control => [control.key, control.value])));
  }
  async function submit() {
    setBusy(true); setError('');
    try {
      if (!canGenerate) throw new Error('Install the selected weights and connect its runtime before generating.');
      if (!prompt.trim() || new TextEncoder().encode(prompt).length > 64 * 1024) throw new Error('Enter a prompt of at most 64 KiB.');
      const parsed = JSON.parse(advancedJson);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Advanced settings must be a JSON object');
      const settings = {
        ...controls,
        ...(inputPath ? { inputPath } : {}),
        ...(category === 'music' ? { title, style, lyrics, memory: { quantization: 'none', offload_ar: true } } : {}),
        ...parsed,
      };
      for (const control of customControls) {
        const value = (settings as Record<string, unknown>)[control.key];
        if (control.kind === 'number' && (typeof value !== 'number' || !Number.isFinite(value) || value < control.min || value > control.max || ((control.step ?? 1) >= 1 && !Number.isInteger(value)))) throw new Error(`${control.label} must be between ${control.min} and ${control.max}.`);
        if (control.kind === 'checkbox' && typeof value !== 'boolean' || control.kind === 'select' && !control.options.includes(String(value))) throw new Error(`Invalid ${control.label.toLowerCase()}.`);
      }
      if (needsImage && !(settings as Record<string, unknown>).inputPath) throw new Error('Choose the reference image required by this model.');
      if (needsDrivingVideo && !String((settings as Record<string, unknown>).drivingVideoPath || '').trim()) throw new Error('Choose the driving video required by Wan Animate 2.');
      if (modelId === 'trellis-2-4b' && ![512, 1024, 1536].includes(Number((settings as Record<string, unknown>).resolution))) throw new Error('TRELLIS 2 resolution must be 512, 1024, or 1536.');
      await api.submitStudioJob({ modelId, prompt, settings });
      onNotice('Generation queued. Its prompt, settings, and progress appear below.');
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  async function connect() {
    setConnecting(true); setError('');
    try {
      const python = await api.pickStudioFile('python'); if (!python) return;
      const builtin = ['triposr', 'qwen-image-21', 'animation-diffusion-2d'].includes(modelId)
        || models.some(model => model.id === modelId && model.backend === 'diffusers');
      const runner = builtin ? null : await api.pickStudioFile('worker'); if (!builtin && !runner) return;
      const sourceDir = !builtin || modelId === 'triposr' ? await api.pickStudioSourceDirectory() : null;
      if ((!builtin || modelId === 'triposr') && !sourceDir) return;
      const value = { modelId, python, runner, sourceDir };
      await api.configureStudioRuntime(value); setRuntime(value); onNotice('Runtime connected.');
    } catch (cause) { setError(String(cause)); }
    finally {setConnecting(false);}
  }

  function renderControl(control: Control) {
    const value = controls[control.key] ?? control.value;
    if (control.kind === 'checkbox') return <label className="studio-checkbox" key={control.key}><input aria-label={control.label} type="checkbox" checked={Boolean(value)} onChange={event => setControl(control.key, event.target.checked)} />{control.label}{control.help && <small>{control.help}</small>}</label>;
    if (control.kind === 'select') return <label key={control.key}>{control.label}<select aria-label={control.label} value={String(value)} onChange={event => setControl(control.key, event.target.value)}>{control.options.map(option => <option key={option} value={option}>{option.toUpperCase()}</option>)}</select>{control.help && <small>{control.help}</small>}</label>;
    const field = control.kind === 'textarea'
      ? <textarea aria-label={control.label} value={String(value)} onChange={event => setControl(control.key, event.target.value)} />
      : <input aria-label={control.label} type={control.kind === 'number' ? 'number' : 'text'} min={control.kind === 'number' ? control.min : undefined} max={control.kind === 'number' ? control.max : undefined} step={control.kind === 'number' ? control.step || 1 : undefined} value={String(value)} onChange={event => setControl(control.key, control.kind === 'number' ? Number(event.target.value) : event.target.value)} />;
    return <label key={control.key}>{control.label}{field}{control.key === 'drivingVideoPath' && <button type="button" onClick={() => void api.pickStudioFile('input').then(path => path && setControl(control.key, path)).catch(cause => setError(String(cause)))}>Choose driving video</button>}{control.help && <small>{control.help}</small>}</label>;
  }

  return <section className="studio-form" aria-label="New generation"><h2>New generation</h2>
    {!models.length ? <p>No catalog models are available yet. You can prepare a prompt and save generation presets below.</p> : <>
      <label>Model<select aria-label="Model" value={modelId} disabled={busy || connecting} onChange={event => {setPresetId(''); setModelId(event.target.value);}}>{models.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}</select></label>
      {selected && GAME_DEV_CATEGORIES.some(([id]) => id === category) && <StudioModelSetup key={modelId} model={selected} connected={connected} disabled={busy || connecting} onRefresh={refreshModels} onNotice={onNotice} />}
    </>}
      {GAME_DEV_CATEGORIES.some(([id]) => id === category) && <div className="studio-presets" aria-label="Saved generation presets"><label>Presets<select aria-label="Presets" value={presetId} onChange={event => loadPreset(event.target.value)}><option value="">Select a preset…</option>{presets.map(preset => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label><label>Preset name<input aria-label="Preset name" value={presetName} onChange={event => setPresetName(event.target.value)} placeholder="My style" /></label><div><button type="button" onClick={savePreset}>Save preset</button><button type="button" disabled={!presetId} onClick={deletePreset}>Delete preset</button><button type="button" onClick={resetSettings}>Reset to defaults</button></div></div>}
      <label>Prompt<textarea value={prompt} onChange={event => setPrompt(event.target.value)} placeholder={category === 'music' ? 'A song about AI…' : 'Describe the asset or animation…'} /></label>
      {category === 'music' ? <><label>Title<input value={title} onChange={event => setTitle(event.target.value)} /></label><label>Style<textarea value={style} onChange={event => setStyle(event.target.value)} placeholder="Genre, instruments, mood, vocals…" /></label><label>Lyrics<textarea value={lyrics} onChange={event => setLyrics(event.target.value)} /></label></> : <>
        {['3d', '3d-animation', '2d-animation'].includes(category) && <div className="studio-input-field"><span>Input asset or reference</span><button type="button" onClick={() => void api.pickStudioFile('input').then(path => path && setInputPath(path)).catch(cause => setError(String(cause)))}>Choose image or asset</button><small>{inputPath || (needsImage ? 'Required reference image.' : 'Optional unless required by the selected model. You can reuse files from prior generations.')}</small>{inputPath && <button type="button" onClick={() => setInputPath('')}>Clear reference</button>}</div>}
        {customControls.length > 0 && <fieldset className="studio-options"><legend>Generation controls</legend><p>Prepare settings before installation. The selected adapter defines supported sizes, sampling options, and output formats.</p><div className="studio-options-grid">{customControls.map(renderControl)}</div></fieldset>}
      </>}
      <details><summary>Advanced model settings</summary><p>Pass model-specific settings supported by the connected runtime. Advanced values override matching controls above.</p><textarea aria-label="Generation settings JSON" value={advancedJson} onChange={event => setAdvancedJson(event.target.value)} /></details>
      {!builtInService && selected && <details className="studio-runtime"><summary>Runtime connection · {connected ? 'Connected' : 'Setup needed'}</summary><p>{selected.backend === 'diffusers' || modelId === 'triposr' ? 'Connect the Python environment containing this model’s dependencies to use the built-in adapter.' : 'Connect the model’s publisher-compatible local runtime. Its adapter defines supported inputs and output formats.'}</p><button type="button" disabled={busy || connecting} onClick={() => void connect()}>{connecting ? 'Connecting…' : 'Connect runtime'}</button>{runtime && <details><summary>Connection details</summary><pre>{JSON.stringify(runtime, null, 2)}</pre></details>}</details>}
      <button className="studio-submit-action" disabled={busy || connecting || !canGenerate || !prompt.trim() || (needsImage && !inputPath) || (needsDrivingVideo && !String(controls.drivingVideoPath || '').trim()) || (category === 'music' && (!style.trim() || !lyrics.trim()))} onClick={event => { event.preventDefault(); void submit(); }}><Play size={16} />{busy ? 'Submitting…' : 'Generate'}</button>
      {GAME_DEV_CATEGORIES.some(([id]) => id === category) && <p className="studio-queue-note">One model uses the GPU at a time. The queue releases the chat model for generation and resumes the conversation afterward.</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
