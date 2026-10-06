import { useEffect, useState } from 'react';
import { CircleStop, FolderOpen, Play, RefreshCw } from 'lucide-react';
import * as api from './api';
import { StudioModelSetup } from './StudioModelSetup';
import { selectStudioModel } from './studio-model-selection';
import './MediaStudio.css';

export const MEDIA_CATEGORIES = [
  ['video', 'Video'], ['tts', 'Speech synthesis'], ['voice-cloning', 'Reference voice'],
  ['ocr', 'Document extraction'], ['omni', 'Audio and multimodal'], ['policy', 'Robotics policy'],
] as const;
type MediaModel = api.InstalledModel & {runtimeConnected?: boolean};
type Control = {key: string; label: string; value: string | number | boolean; kind: 'text' | 'number' | 'select' | 'checkbox' | 'textarea'; min?: number; max?: number; step?: number; options?: string[]};
const controlsByCategory: Record<string, Control[]> = {
  video: [
    {key: 'mode', label: 'Generation mode', kind: 'select', value: 'text-to-video', options: ['text-to-video', 'image-to-video']},
    {key: 'seed', label: 'Seed', kind: 'number', value: 831001, min: 0, max: 4294967295},
    {key: 'width', label: 'Width', kind: 'number', value: 768, min: 128, max: 4096, step: 64},
    {key: 'height', label: 'Height', kind: 'number', value: 512, min: 128, max: 4096, step: 64},
    {key: 'frameCount', label: 'Frame count', kind: 'number', value: 81, min: 1, max: 2400},
    {key: 'fps', label: 'Frames per second', kind: 'number', value: 24, min: 1, max: 120},
    {key: 'steps', label: 'Inference steps', kind: 'number', value: 30, min: 1, max: 256},
    {key: 'guidanceScale', label: 'Guidance scale', kind: 'number', value: 4, min: 0, max: 30, step: 0.1},
    {key: 'negativePrompt', label: 'Negative prompt', kind: 'textarea', value: ''},
    {key: 'outputFormat', label: 'Output format', kind: 'select', value: 'mp4', options: ['mp4', 'webm', 'gif']},
  ],
  tts: [
    {key: 'voice', label: 'Voice or speaker ID', kind: 'text', value: 'default'},
    {key: 'language', label: 'Language', kind: 'text', value: 'auto'},
    {key: 'seed', label: 'Seed', kind: 'number', value: 831001, min: 0, max: 4294967295},
    {key: 'speed', label: 'Speech speed', kind: 'number', value: 1, min: 0.25, max: 4, step: 0.05},
    {key: 'sampleRate', label: 'Sample rate (Hz)', kind: 'number', value: 24000, min: 8000, max: 192000},
    {key: 'outputFormat', label: 'Output format', kind: 'select', value: 'wav', options: ['wav', 'flac', 'mp3']},
  ],
  'voice-cloning': [
    {key: 'referenceText', label: 'Reference transcript', kind: 'textarea', value: ''},
    {key: 'language', label: 'Language', kind: 'text', value: 'auto'},
    {key: 'seed', label: 'Seed', kind: 'number', value: 831001, min: 0, max: 4294967295},
    {key: 'speed', label: 'Speech speed', kind: 'number', value: 1, min: 0.25, max: 4, step: 0.05},
    {key: 'sampleRate', label: 'Sample rate (Hz)', kind: 'number', value: 24000, min: 8000, max: 192000},
    {key: 'outputFormat', label: 'Output format', kind: 'select', value: 'wav', options: ['wav', 'flac', 'mp3']},
  ],
  ocr: [
    {key: 'language', label: 'Language', kind: 'text', value: 'auto'},
    {key: 'pageStart', label: 'First page', kind: 'number', value: 1, min: 1, max: 100000},
    {key: 'pageEnd', label: 'Last page (0 means all)', kind: 'number', value: 0, min: 0, max: 100000},
    {key: 'preserveLayout', label: 'Preserve document layout', kind: 'checkbox', value: true},
    {key: 'outputFormat', label: 'Output format', kind: 'select', value: 'md', options: ['txt', 'md', 'json']},
  ],
  omni: [
    {key: 'responseMode', label: 'Response mode', kind: 'select', value: 'text', options: ['text', 'speech', 'text-and-speech']},
    {key: 'language', label: 'Language', kind: 'text', value: 'auto'},
    {key: 'maxTokens', label: 'Maximum response tokens', kind: 'number', value: 1024, min: 1, max: 32768},
    {key: 'temperature', label: 'Temperature', kind: 'number', value: 0.2, min: 0, max: 2, step: 0.05},
    {key: 'outputFormat', label: 'Output format', kind: 'select', value: 'json', options: ['json', 'txt', 'wav']},
  ],
  policy: [
    {key: 'embodiment', label: 'Embodiment', kind: 'text', value: ''},
    {key: 'normalizationKey', label: 'Normalization or dataset key', kind: 'text', value: ''},
    {key: 'actionHorizon', label: 'Action horizon', kind: 'number', value: 16, min: 1, max: 1024},
    {key: 'controlRateHz', label: 'Control rate (Hz)', kind: 'number', value: 30, min: 1, max: 500, step: 0.5},
    {key: 'outputFormat', label: 'Output format', kind: 'select', value: 'json', options: ['json', 'npz']},
  ],
};
const inputLabels: Record<string, string> = {video: 'Choose reference image', 'voice-cloning': 'Choose reference audio', ocr: 'Choose document or image', omni: 'Choose input media', policy: 'Choose observation file'};
const prompts: Record<string, [string, string]> = {tts: ['Text to speak', ''], 'voice-cloning': ['Text to speak', ''], ocr: ['Extraction instruction', 'Extract the text and structure of this document.'], omni: ['Question or instruction', ''], policy: ['Task instruction', '']};
const active = (job: api.StudioJob) => ['queued', 'starting', 'running'].includes(job.status);
const inputRequired = (category: string, values: Record<string, unknown>) => ['voice-cloning', 'ocr', 'omni', 'policy'].includes(category) || (category === 'video' && values.mode === 'image-to-video');
function controlsForModel(category: string, modelId: string): Control[] {
  const base = controlsByCategory[category] || [];
  if (modelId === 'omni-voxtral-mini-4b-realtime-2602') return [
    {key: 'responseMode', label: 'Response mode', kind: 'select', value: 'text', options: ['text']},
    {key: 'outputFormat', label: 'Output format', kind: 'select', value: 'txt', options: ['txt', 'json']},
  ];
  if (modelId.startsWith('tts-qwen3-') || modelId.startsWith('voice-cloning-qwen3-')) {
    const languages = ['auto', 'Chinese', 'English', 'Japanese', 'Korean', 'German', 'French', 'Russian', 'Portuguese', 'Spanish', 'Italian'];
    const design = modelId.includes('voicedesign');
    return [...base.filter(control => control.key !== 'speed' && !(design && control.key === 'voice')).map(control => {
      if (control.key === 'voice') return {...control, kind: 'select', value: 'Ryan', options: ['Vivian', 'Serena', 'Uncle_Fu', 'Dylan', 'Eric', 'Ryan', 'Aiden', 'Ono_Anna', 'Sohee']} as Control;
      if (control.key === 'language') return {...control, kind: 'select', options: languages} as Control;
      if (control.key === 'sampleRate') return {...control, value: 24000, min: 24000, max: 24000};
      return control;
    }), ...(modelId === 'tts-qwen3-customvoice-1-7b' || design ? [{key: 'voiceInstruction', label: design ? 'Voice description' : 'Voice style instruction', kind: 'textarea', value: ''}] as Control[] : [])];
  }
  if (category !== 'video' || !modelId.startsWith('ltx-25')) return base;
  const distilled = modelId.includes('distilled');
  const values: Record<string, number> = {width: 960, height: 544, frameCount: 121, steps: distilled ? 8 : 30, guidanceScale: distilled ? 1 : 4};
  return base.map(control => control.key in values ? {...control, value: values[control.key],
    ...(['width', 'height'].includes(control.key) ? {step: 32} : {}),
    ...(distilled && control.key === 'steps' ? {min: 8, max: 8} : {}),
    ...(distilled && control.key === 'guidanceScale' ? {min: 1, max: 1} : {}),
  } : control);
}
const defaults = (category: string, modelId = '') => Object.fromEntries(controlsForModel(category, modelId).map(control => [control.key, control.value]));

function validatedSettings(category: string, values: Record<string, unknown>, inputPath: string, advancedJson: string, modelId: string) {
  const parsed: unknown = JSON.parse(advancedJson);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Advanced settings must be a JSON object.');
  const settings: Record<string, unknown> = {...values, ...(inputPath ? {inputPath} : {}), ...parsed};
  for (const control of controlsForModel(category, modelId)) {
    const value = settings[control.key];
    if (control.kind === 'number') {
      if (typeof value !== 'number' || !Number.isFinite(value) || value < control.min! || value > control.max! || ((control.step ?? 1) === 1 && !Number.isInteger(value))) throw new Error(`${control.label} must be between ${control.min} and ${control.max}${(control.step ?? 1) === 1 ? ' and a whole number' : ''}.`);
    } else if (control.kind === 'checkbox' ? typeof value !== 'boolean' : typeof value !== 'string' || (control.options && !control.options.includes(value))) {
      throw new Error(`Invalid ${control.label.toLowerCase()}.`);
    }
  }
  if (inputRequired(category, settings) && (typeof settings.inputPath !== 'string' || !settings.inputPath.trim())) throw new Error('Choose the input file required by this category.');
  if (category === 'ocr' && Number(settings.pageEnd) > 0 && Number(settings.pageEnd) < Number(settings.pageStart)) throw new Error('Last page must be at or after first page.');
  if (modelId.startsWith('ltx-25')) {
    if (Number(settings.frameCount) % 8 !== 1) throw new Error('LTX 2.5 frame count must be 8n + 1, for example 121.');
    if (Number(settings.width) % 32 || Number(settings.height) % 32) throw new Error('LTX 2.5 width and height must be divisible by 32.');
  }
  return settings;
}

export function MediaStudio({category, models, onCategoryChange, onNotice = () => {}}: {category: string; models?: MediaModel[]; onCategoryChange?: (category: string) => void; onNotice?: (message: string) => void}) {
  const label = MEDIA_CATEGORIES.find(([id]) => id === category)?.[1] || category;
  const [revision, setRevision] = useState(0);
  return <section className="media-studio" aria-label="Media Studio"><header><h1>Media Studio</h1><p>Generate and inspect video, voices, document extractions, multimodal responses, and offline policy predictions with a connected model runtime.</p></header>
    {onCategoryChange && <div className="model-category-tabs" role="group" aria-label="Media categories">{MEDIA_CATEGORIES.map(([id, name]) => <button key={id} aria-pressed={category === id} onClick={() => onCategoryChange(id)}>{name}</button>)}</div>}
    <MediaGenerationForm key={category} category={category} label={label} models={models} onNotice={onNotice} onSubmitted={() => setRevision(current => current + 1)} />
    <MediaHistory category={category} revision={revision} onNotice={onNotice} />
  </section>;
}

function MediaGenerationForm({category, label, models: providedModels, onNotice, onSubmitted}: {category: string; label: string; models?: MediaModel[]; onNotice: (message: string) => void; onSubmitted: () => void}) {
  const [models, setModels] = useState<MediaModel[]>(providedModels?.filter(model => model.category === category) || []);
  const [modelId, setModelId] = useState(() => selectStudioModel(models, category));
  const [prompt, setPrompt] = useState(prompts[category]?.[1] || '');
  const [values, setValues] = useState<Record<string, unknown>>(() => defaults(category, modelId));
  const [inputPath, setInputPath] = useState('');
  const [advancedJson, setAdvancedJson] = useState('{}');
  const [runtime, setRuntime] = useState<api.StudioRuntime | null>(null);
  const [busy, setBusy] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState('');
  const selected = models.find(model => model.id === modelId);
  const connected = Boolean(runtime?.modelId === modelId || selected?.runtimeConnected);
  const canGenerate = connected && Boolean(selected?.installed || runtime?.runner && runtime?.sourceDir);
  useEffect(() => {setValues(defaults(category, modelId));}, [category, modelId]);
  useEffect(() => {
    let alive = true;
    const apply = (all: MediaModel[]) => {
      if (!alive) return;
      const candidates = all.filter(model => model.category === category);
      setModels(candidates); setModelId(current => selectStudioModel(candidates, category, current));
    };
    if (providedModels) { apply(providedModels); return () => { alive = false; }; }
    const refresh = () => api.modelLibrary().then(library => apply(library.models)).catch(cause => { if (alive) setError(String(cause)); });
    void refresh(); const timer = setInterval(() => void refresh(), 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [category, providedModels]);
  useEffect(() => {
    let alive = true;
    setRuntime(null);
    if (!modelId) return () => { alive = false; };
    const refresh = () => api.studioRuntime(modelId).then(value => { if (alive) setRuntime(value); }).catch(cause => { if (alive) { setRuntime(null); setError(String(cause)); } });
    void refresh(); const timer = setInterval(() => void refresh(), 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [modelId]);
  async function connect() {
    setConnecting(true); setError('');
    try {
      const python = await api.pickStudioFile('python'); if (!python) return;
      const runner = await api.pickStudioFile('worker'); if (!runner) return;
      const sourceDir = await api.pickStudioSourceDirectory(); if (!sourceDir) return;
      const value = {modelId, python, runner, sourceDir};
      await api.configureStudioRuntime(value); setRuntime(value);
      onNotice('Runtime connected. Its worker will receive the exact prompt and settings.');
    } catch (cause) { setError(String(cause)); }
    finally { setConnecting(false); }
  }
  async function submit() {
    setBusy(true); setError('');
    try {
      if (!canGenerate) throw new Error('Connect the selected model runtime and installed or existing publisher weights before generating.');
      if (!prompt.trim() || new TextEncoder().encode(prompt).length > 64 * 1024) throw new Error('Enter a prompt of at most 64 KiB.');
      const settings = validatedSettings(category, values, inputPath, advancedJson, modelId);
      await api.submitStudioJob({modelId, prompt, settings});
      onSubmitted(); onNotice('Generation queued. Review its exact request and progress below.');
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  function controlField(control: Control) {
    const value = values[control.key] ?? control.value;
    const update = (next: unknown) => setValues(current => ({...current, [control.key]: next}));
    if (control.kind === 'checkbox') return <label className="media-checkbox" key={control.key}><input type="checkbox" aria-label={control.label} checked={Boolean(value)} onChange={event => update(event.target.checked)} />{control.label}</label>;
    if (control.kind === 'select') return <label key={control.key}>{control.label}<select aria-label={control.label} value={String(value)} onChange={event => update(event.target.value)}>{control.options!.map(option => <option key={option} value={option}>{option}</option>)}</select></label>;
    return <label key={control.key}>{control.label}{control.kind === 'textarea' ? <textarea aria-label={control.label} value={String(value)} onChange={event => update(event.target.value)} /> : <input aria-label={control.label} type={control.kind === 'number' ? 'number' : 'text'} min={control.min} max={control.max} step={control.step ?? 1} value={String(value)} onChange={event => update(control.kind === 'number' ? Number(event.target.value) : event.target.value)} />}</label>;
  }
  return <form className="media-form" aria-label={`New ${label.toLowerCase()} job`} onSubmit={event => {event.preventDefault(); void submit();}}><h2>{label}</h2>
    {!models.length ? <p>No catalog models in this category are available. Open Models to inspect publisher sources and setup requirements.</p> : <>
      <label>Model<select aria-label="Model" value={modelId} disabled={connecting || busy} onChange={event => setModelId(event.target.value)}>{models.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}</select></label>
      {selected && <StudioModelSetup key={modelId} model={selected} connected={connected} disabled={connecting || busy} onRefresh={async () => {const library = await api.modelLibrary(); setModels(library.models.filter(model => model.category === category));}} onNotice={onNotice} />}
      <label>{prompts[category]?.[0] || 'Prompt'}<textarea aria-label={prompts[category]?.[0] || 'Prompt'} value={prompt} onChange={event => setPrompt(event.target.value)} /></label>
      {inputLabels[category] && <div className="media-input"><button type="button" onClick={() => void api.pickStudioFile('input').then(path => { if (path) setInputPath(path); }).catch(cause => setError(String(cause)))}>{inputLabels[category]}</button><span>{inputPath || (inputRequired(category, values) ? 'Required input file' : 'Optional reference image')}</span>{inputPath && <button type="button" onClick={() => setInputPath('')}>Clear input</button>}</div>}
      {category === 'policy' && <p>Save observations in the publisher SDK’s JSON or NPZ schema. This job produces action predictions to inspect; deploying them uses the robot’s configured SDK.</p>}
      <fieldset><legend>Job controls</legend><p>{modelId.startsWith('ltx-25') ? 'LTX 2.5 uses dimensions divisible by 32 and frame counts of 8n + 1. Distilled checkpoints use 8 steps and guidance 1.' : 'Prepare settings before installation. The model adapter defines supported modes, sizes, voices, and formats.'}</p><div className="media-controls">{controlsForModel(category, modelId).map(controlField)}</div></fieldset>
      <details className="media-advanced"><summary>Advanced model settings</summary><p>Additional SDK options and overrides are saved with the request.</p><textarea aria-label="Advanced settings JSON" value={advancedJson} onChange={event => setAdvancedJson(event.target.value)} /></details>
      <section className="media-runtime"><button type="button" disabled={connecting || busy} onClick={() => void connect()}>{connecting ? 'Connecting…' : 'Connect runtime'}</button><details><summary>Runtime connection · {connected ? 'Connected' : 'Setup needed'}</summary><p>Use a publisher-compatible local adapter. Choose its Python environment, worker, and existing SDK/model folder. Saving a connection does not verify generation.</p>{runtime && <pre>{JSON.stringify(runtime, null, 2)}</pre>}</details></section>
      <button type="submit" className="media-generate" disabled={busy || connecting || !canGenerate || !prompt.trim() || (inputRequired(category, values) && !inputPath)}><Play size={16} />{busy ? 'Submitting…' : 'Generate'}</button>
      <p className="media-queue-note">Jobs share OpenCore’s durable GPU queue. The text model is released before generation, and chat continuations resume after the job.</p>
    </>}{error && <p role="alert">{error}</p>}
  </form>;
}

function MediaHistory({category, revision, onNotice}: {category: string; revision: number; onNotice: (message: string) => void}) {
  const [jobs, setJobs] = useState<api.StudioJob[]>([]);
  const [error, setError] = useState('');
  const [previews, setPreviews] = useState<Record<string, {dataUrl: string; mime: string; text?: string}>>({});
  useEffect(() => {
    let alive = true;
    async function refresh() { try { const jobs = await api.listStudioJobs(); if (alive) { setJobs(jobs.filter(job => job.category === category)); setError(''); } } catch (cause) { if (alive) setError(String(cause)); } }
    void refresh(); const timer = setInterval(() => void refresh(), 1500);
    return () => { alive = false; clearInterval(timer); };
  }, [category, revision]);
  async function refresh() { try { setJobs((await api.listStudioJobs()).filter(job => job.category === category)); setError(''); } catch (cause) { setError(String(cause)); } }
  async function preview(job: api.StudioJob, path: string) {
    try {
      const result = await api.studioOutputPreview(job.id, path);
      const text = result.mime.startsWith('text/') || result.mime === 'application/json' ? new TextDecoder().decode(Uint8Array.from(atob(result.dataUrl.slice(result.dataUrl.indexOf(',') + 1)), char => char.charCodeAt(0))) : undefined;
      setPreviews(current => ({...current, [`${job.id}:${path}`]: {...result, text}}));
    } catch (cause) { onNotice(String(cause)); }
  }
  return <section className="media-history" aria-label="Media job history"><header><h2>Job history and outputs</h2><button type="button" aria-label="Refresh media jobs" onClick={() => void refresh()}><RefreshCw size={16} /></button></header>{error && <p role="alert">{error}</p>}{!jobs.length && <p>No jobs yet. Forms and enabled category skills in chat use this queue.</p>}
    {jobs.map(job => <article className="media-job" key={job.id}><header><div><strong>{job.request.modelId}</strong><span>{job.status} · {job.stage}</span><time dateTime={job.createdAt}>{job.createdAt}</time></div>{active(job) && <button type="button" onClick={() => void api.cancelStudioJob(job.id).then(refresh).catch(cause => onNotice(String(cause)))}><CircleStop size={15} />Cancel job</button>}</header><p>{job.request.prompt}</p>{job.error && <p role="alert">{job.error}</p>}<details><summary>Request and settings</summary><pre>{JSON.stringify(job.request, null, 2)}</pre></details>{Object.keys(job.progress).length > 0 && <details><summary>Progress metadata</summary><pre>{JSON.stringify(job.progress, null, 2)}</pre></details>}
      {job.outputs.map(path => { const name = path.split(/[\\/]/).pop() || path; const output = previews[`${job.id}:${path}`]; return <div className="media-output" key={path}><strong>{name}</strong><button type="button" onClick={() => void api.openStudioOutput(job.id, path).catch(cause => onNotice(String(cause)))}><FolderOpen size={14} />Open output folder</button>{/\.(png|jpe?g|webp|gif|flac|wav|mp3|ogg|opus|mp4|webm|txt|md|json|jsonl|csv|srt|vtt)$/i.test(path) && <button type="button" onClick={() => void preview(job, path)}>Preview {name}</button>}{output && (output.text !== undefined ? <pre>{output.text}</pre> : output.mime.startsWith('image/') ? <img src={output.dataUrl} alt={`Output ${name}`} /> : output.mime.startsWith('audio/') ? <audio controls src={output.dataUrl} /> : <video controls src={output.dataUrl} />)}</div>; })}
    </article>)}
  </section>;
}
