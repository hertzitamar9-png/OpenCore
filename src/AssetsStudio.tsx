import { useState } from 'react';
import { GAME_DEV_CATEGORIES, GenerationForm, StudioJobs } from './StudioJobs';
export function GameDevStudio({onNotice,initialCategory='image'}:{onNotice:(message:string)=>void;initialCategory?:string}) {
  const [category,setCategory]=useState(initialCategory);
  return <section className="game-dev-studio"><header><h1>Game Dev Studio</h1><p>Create and refine images, 3D assets, and 2D or 3D animations. Save custom generation controls, inspect every prompt, and review outputs.</p></header>
    <div className="model-category-tabs" role="group" aria-label="Game development categories">{GAME_DEV_CATEGORIES.map(([id,label])=><button key={id} aria-pressed={category===id} onClick={()=>setCategory(id)}>{label}</button>)}</div>
    {category==='background'?<p>OpenCore waits for local jobs without keeping the text model loaded, then resumes the originating conversation.</p>:<GenerationForm key={category} category={category} onNotice={onNotice}/>}<StudioJobs category={category} onNotice={onNotice}/>
  </section>;
}

// Kept as an export alias for integrations written against the previous component name.
export { GameDevStudio as AssetsStudio };
