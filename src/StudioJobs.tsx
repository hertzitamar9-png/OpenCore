import { useEffect, useState } from 'react';
import { CircleStop, FolderOpen, Play, RefreshCw } from 'lucide-react';
import * as api from './api';

export const ASSET_CATEGORIES = [['image','2D images'],['3d','3D assets'],['3d-animation','3D animation'],['2d-animation','2D animation'],['speech','Speech']] as const;
const active = (job: api.StudioJob) => ['queued','starting','running'].includes(job.status);
export function StudioJobs({ category, onNotice }: {category:string; onNotice:(message:string)=>void}) {
  const [jobs,setJobs]=useState<api.StudioJob[]>([]);
  const [error,setError]=useState('');
  const [previews,setPreviews]=useState<Record<string,{dataUrl:string;mime:string}>>({});
  async function refresh() {try {setJobs((await api.listStudioJobs()).filter(job=>job.category===category));}catch(cause){setError(String(cause));}}
  useEffect(()=>{void refresh();const timer=setInterval(()=>void refresh(),1500);return()=>clearInterval(timer);},[category]);
  async function cancel(id:string){try {await api.cancelStudioJob(id);await refresh();}catch(cause){onNotice(String(cause));}}
  async function preview(job:api.StudioJob,path:string){try {setPreviews(current=>({...current}));const value=await api.studioOutputPreview(job.id,path);setPreviews(current=>({...current,[path]:value}));}catch(cause){onNotice(String(cause));}}
  return <section className="studio-jobs" aria-label="Generation jobs"><header><h2>Generations</h2><button onClick={()=>void refresh()} aria-label="Refresh generations"><RefreshCw size={16}/></button></header>
    {error && <p role="alert">{error}</p>}
    {!jobs.length && <p>No generations yet. Submit a prompt here or use the category skill in chat.</p>}
    {jobs.map(job=><article key={job.id} className="studio-job"><header><div><strong>{String(job.request.settings.title || job.request.modelId)}</strong><small>{job.status} · {job.stage}</small></div>{active(job)&&<button onClick={()=>void cancel(job.id)}><CircleStop size={15}/>Cancel</button>}</header>
      <p>{job.request.prompt}</p>
      {job.error && <p role="alert">{job.error}</p>}
      <details><summary>Prompt and generation settings</summary><pre>{JSON.stringify(job.request,null,2)}</pre></details>
      {Object.keys(job.progress).length>0&&<details><summary>Progress</summary><pre>{JSON.stringify(job.progress,null,2)}</pre></details>}
      {job.outputs.map(path=><div className="studio-output" key={path}><span>{path.split(/[\\/]/).pop()}</span><button onClick={()=>void api.openStudioOutput(job.id,path).catch(cause=>onNotice(String(cause)))}><FolderOpen size={14}/>Open folder</button>{/\.(png|jpe?g|webp|flac|wav|mp3|mp4|webm)$/i.test(path)&&<button onClick={()=>void preview(job,path)}>Preview</button>}
        {previews[path]&& (previews[path].mime.startsWith('image/')?<img src={previews[path].dataUrl} alt="Generated output"/>:previews[path].mime.startsWith('audio/')?<audio controls src={previews[path].dataUrl}/>:<video controls src={previews[path].dataUrl}/>)}</div>)}
      {!active(job)&&<button onClick={()=>void api.submitStudioJob({...job.request,conversationId:null}).then(()=>refresh()).catch(cause=>onNotice(String(cause)))}>Generate another version</button>}
    </article>)}
  </section>;
}
export function GenerationForm({category,onNotice}:{category:string;onNotice:(message:string)=>void}) {
  const [models,setModels]=useState<api.InstalledModel[]>([]);const [modelId,setModelId]=useState('');const [prompt,setPrompt]=useState('');
  const [title,setTitle]=useState('');const [style,setStyle]=useState('');const [lyrics,setLyrics]=useState('');const [inputPath,setInputPath]=useState('');const [settings,setSettings]=useState('{}');
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [runtime,setRuntime]=useState<api.StudioRuntime|null>(null);
  useEffect(()=>{let alive=true;const refresh=async()=>{try {const library=await api.modelLibrary();if(alive){const installed=library.models.filter(m=>m.installed&&m.category===category);setModels(installed);setModelId(current=>installed.some(m=>m.id===current)?current:installed[0]?.id||'');}}catch(cause){if(alive)setError(String(cause));}};void refresh();const timer=setInterval(()=>void refresh(),3000);return()=>{alive=false;clearInterval(timer);};},[category]);
  useEffect(()=>{void api.studioRuntime(modelId).then(setRuntime).catch(()=>setRuntime(null));},[modelId]);
  async function submit(){setBusy(true);setError('');try {let parsed=JSON.parse(settings);if(!parsed||Array.isArray(parsed)||typeof parsed!=='object')throw new Error('Settings must be a JSON object');parsed={...parsed,...(inputPath?{inputPath}:{}),...(category==='music'?{title,style,lyrics,memory:{quantization:'none',offload_ar:true}}:{})};await api.submitStudioJob({modelId,prompt,settings:parsed});onNotice('Generation queued. Its prompt, settings, and progress appear below.');}catch(cause){setError(String(cause));}finally{setBusy(false);}}
  async function connect(){try {const python=await api.pickStudioFile('python');if(!python)return;const builtin=['triposr','qwen-image-21','animation-diffusion-2d'].includes(modelId);const runner=builtin?null:await api.pickStudioFile('worker');if(!builtin&&!runner)return;const sourceDir=modelId==='triposr'?await api.pickStudioSourceDirectory():null;if(modelId==='triposr'&&!sourceDir)return;const value={modelId,python,runner,sourceDir};await api.configureStudioRuntime(value);setRuntime(value);onNotice('Runtime connected.');}catch(cause){setError(String(cause));}}
  return <section className="studio-form" aria-label="New generation"><h2>New generation</h2>
    {!models.length?<p>Install a model in this category from Models to unlock generation and its chat skill.</p>:<>
    <label>Model<select value={modelId} onChange={event=>setModelId(event.target.value)}>{models.map(m=><option key={m.id} value={m.id}>{m.label}</option>)}</select></label>
    <label>Prompt<textarea value={prompt} onChange={event=>setPrompt(event.target.value)} placeholder={category==='music'?'A song about AI…':'Describe the asset or animation…'}/></label>
    {category==='music'?<><label>Title<input value={title} onChange={event=>setTitle(event.target.value)}/></label><label>Style<textarea value={style} onChange={event=>setStyle(event.target.value)} placeholder="Genre, instruments, mood, vocals…"/></label><label>Lyrics<textarea value={lyrics} onChange={event=>setLyrics(event.target.value)}/></label></>:<label>Input file<button onClick={()=>void api.pickStudioFile('input').then(path=>path&&setInputPath(path)).catch(cause=>setError(String(cause)))}>Choose image, asset, or audio</button><small>{inputPath||'Required for image-to-3D, asset animation, and transcription models.'}</small></label>}
    <details><summary>Advanced generation settings</summary><textarea aria-label="Generation settings JSON" value={settings} onChange={event=>setSettings(event.target.value)}/></details>
    {!['music','speech'].includes(category)&&<details><summary>Runtime connection · {runtime?'Connected':'Setup needed'}</summary><p>Connect the installed model’s local worker. Workers accept a request JSON and write generated files to the provided output folder.</p><button onClick={()=>void connect()}>Connect runtime</button></details>}
    <button disabled={busy||!prompt.trim()||(category==='music'&&(!style.trim()||!lyrics.trim()))} onClick={()=>void submit()}><Play size={16}/>{busy?'Submitting…':'Generate'}</button>
    </>}{error&&<p role="alert">{error}</p>}
  </section>;
}
