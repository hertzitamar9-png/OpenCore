import { useEffect, useState } from 'react';
import { CircleStop, FolderOpen, Play, RefreshCw } from 'lucide-react';
import * as api from './api';

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
  const customControls = settingsForCategory[category] || [];

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const library = await api.modelLibrary();
        if (alive) {
          const installed = library.models.filter(model => model.installed && model.category === category);
          setModels(installed);
          setModelId(current => installed.some(model => model.id === current) ? current : installed[0]?.id || '');
        }
      } catch (cause) { if (alive) setError(String(cause)); }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [category]);
  useEffect(() => { setControls(Object.fromEntries(customControls.map(control => [control.key, control.value]))); }, [category]);
  useEffect(() => {
    setPresetId('');
    try {
      const value = JSON.parse(window.localStorage.getItem(presetStorageKey(category)) || '[]');
      setPresets(Array.isArray(value) ? value.filter(item => item && typeof item.id === 'string' && typeof item.name === 'string') : []);
    } catch { setPresets([]); }
  }, [category]);
  useEffect(() => { void api.studioRuntime(modelId).then(setRuntime).catch(() => setRuntime(null)); }, [modelId]);

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
      const parsed = JSON.parse(advancedJson);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Advanced settings must be a JSON object');
      const settings = {
        ...controls,
        ...(inputPath ? { inputPath } : {}),
        ...(category === 'music' ? { title, style, lyrics, memory: { quantization: 'none', offload_ar: true } } : {}),
        ...parsed,
      };
      await api.submitStudioJob({ modelId, prompt, settings });
      onNotice('Generation queued. Its prompt, settings, and progress appear below.');
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  async function connect() {
    try {
      const python = await api.pickStudioFile('python'); if (!python) return;
      const builtin = ['triposr', 'qwen-image-21', 'animation-diffusion-2d'].includes(modelId)
        || models.some(model => model.id === modelId && model.backend === 'diffusers');
      const runner = builtin ? null : await api.pickStudioFile('worker'); if (!builtin && !runner) return;
      const sourceDir = modelId === 'triposr' ? await api.pickStudioSourceDirectory() : null;
      if (modelId === 'triposr' && !sourceDir) return;
      const value = { modelId, python, runner, sourceDir };
      await api.configureStudioRuntime(value); setRuntime(value); onNotice('Runtime connected.');
    } catch (cause) { setError(String(cause)); }
  }

  function renderControl(control: Control) {
    const value = controls[control.key] ?? control.value;
    if (control.kind === 'checkbox') return <label className="studio-checkbox" key={control.key}><input aria-label={control.label} type="checkbox" checked={Boolean(value)} onChange={event => setControl(control.key, event.target.checked)} />{control.label}{control.help && <small>{control.help}</small>}</label>;
    if (control.kind === 'select') return <label key={control.key}>{control.label}<select aria-label={control.label} value={String(value)} onChange={event => setControl(control.key, event.target.value)}>{control.options.map(option => <option key={option} value={option}>{option.toUpperCase()}</option>)}</select>{control.help && <small>{control.help}</small>}</label>;
    const field = control.kind === 'textarea'
      ? <textarea aria-label={control.label} value={String(value)} onChange={event => setControl(control.key, event.target.value)} />
      : <input aria-label={control.label} type="number" min={control.kind === 'number' ? control.min : undefined} max={control.kind === 'number' ? control.max : undefined} step={control.kind === 'number' ? control.step || 1 : undefined} value={String(value)} onChange={event => setControl(control.key, Number(event.target.value))} />;
    return <label key={control.key}>{control.label}{field}{control.help && <small>{control.help}</small>}</label>;
  }

  return <section className="studio-form" aria-label="New generation"><h2>New generation</h2>
    {!models.length ? <p>Install a model in this category from Models to unlock generation and its chat skill.</p> : <>
      <label>Model<select value={modelId} onChange={event => setModelId(event.target.value)}>{models.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}</select></label>
      {GAME_DEV_CATEGORIES.some(([id]) => id === category) && <div className="studio-presets" aria-label="Saved generation presets"><label>Presets<select aria-label="Presets" value={presetId} onChange={event => loadPreset(event.target.value)}><option value="">Select a preset…</option>{presets.map(preset => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label><label>Preset name<input aria-label="Preset name" value={presetName} onChange={event => setPresetName(event.target.value)} placeholder="My style" /></label><div><button type="button" onClick={savePreset}>Save preset</button><button type="button" disabled={!presetId} onClick={deletePreset}>Delete preset</button><button type="button" onClick={resetSettings}>Reset to defaults</button></div></div>}
      <label>Prompt<textarea value={prompt} onChange={event => setPrompt(event.target.value)} placeholder={category === 'music' ? 'A song about AI…' : 'Describe the asset or animation…'} /></label>
      {category === 'music' ? <><label>Title<input value={title} onChange={event => setTitle(event.target.value)} /></label><label>Style<textarea value={style} onChange={event => setStyle(event.target.value)} placeholder="Genre, instruments, mood, vocals…" /></label><label>Lyrics<textarea value={lyrics} onChange={event => setLyrics(event.target.value)} /></label></> : <>
        {['3d', '3d-animation', '2d-animation'].includes(category) && <label>Input asset or reference<button type="button" onClick={() => void api.pickStudioFile('input').then(path => path && setInputPath(path)).catch(cause => setError(String(cause)))}>Choose image or asset</button><small>{inputPath || 'Optional unless required by the selected model. You can reuse files from prior generations.'}</small></label>}
        {customControls.length > 0 && <fieldset className="studio-options"><legend>Generation controls</legend><div className="studio-options-grid">{customControls.map(renderControl)}</div></fieldset>}
      </>}
      <details><summary>Advanced model settings</summary><p>Pass model-specific settings supported by the connected runtime. Advanced values override matching controls above.</p><textarea aria-label="Generation settings JSON" value={advancedJson} onChange={event => setAdvancedJson(event.target.value)} /></details>
      {!['music', 'speech'].includes(category) && <details><summary>Runtime connection · {runtime ? 'Connected' : 'Setup needed'}</summary><p>Use the model’s compatible local worker. Worker support and output formats depend on the model and installed runtime.</p><button onClick={() => void connect()}>Connect runtime</button></details>}
      <button className="studio-submit-action" disabled={busy || !prompt.trim() || (category === 'music' && (!style.trim() || !lyrics.trim()))} onClick={event => { event.preventDefault(); void submit(); }}><Play size={16} />{busy ? 'Submitting…' : 'Generate'}</button>
    </>}{error && <p role="alert">{error}</p>}
  </section>;
}
