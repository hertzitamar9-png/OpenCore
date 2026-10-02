import { useState } from 'react';
import { ASSET_CATEGORIES, GenerationForm, StudioJobs } from './StudioJobs';
export function AssetsStudio({onNotice}:{onNotice:(message:string)=>void}) {
  const [category,setCategory]=useState('image');
  return <section className="assets-studio"><header><h1>Assets Studio</h1><p>Create images, 3D assets, and animations. Inspect every prompt, generation, and output.</p></header>
    <div className="model-category-tabs" role="group" aria-label="Asset categories">{ASSET_CATEGORIES.map(([id,label])=><button key={id} aria-pressed={category===id} onClick={()=>setCategory(id)}>{label}</button>)}</div>
    <GenerationForm key={category} category={category} onNotice={onNotice}/><StudioJobs category={category} onNotice={onNotice}/>
  </section>;
}
