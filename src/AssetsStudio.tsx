import { useState } from 'react';
import { ASSET_CATEGORIES, GenerationForm, StudioJobs } from './StudioJobs';
export function AssetsStudio({onNotice,initialCategory='image'}:{onNotice:(message:string)=>void;initialCategory?:string}) {
  const [category,setCategory]=useState(initialCategory);
  return <section className="assets-studio"><header><h1>Assets Studio</h1><p>Create images, 3D assets, and animations. Inspect every prompt, generation, and output.</p></header>
    <div className="model-category-tabs" role="group" aria-label="Asset categories">{ASSET_CATEGORIES.map(([id,label])=><button key={id} aria-pressed={category===id} onClick={()=>setCategory(id)}>{label}</button>)}</div>
    {category==='background'?<p>OpenCore waits for local jobs without keeping the text model loaded, then resumes the originating conversation.</p>:<GenerationForm key={category} category={category} onNotice={onNotice}/>}<StudioJobs category={category} onNotice={onNotice}/>
  </section>;
}
