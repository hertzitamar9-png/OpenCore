import { useState } from 'react';
import { GAME_DEV_CATEGORIES, GenerationForm, StudioJobs } from './StudioJobs';
import './GameDevStudio.css';
export function GameDevStudio({onNotice,initialCategory='image',onBrowseModels}:{onNotice:(message:string)=>void;initialCategory?:string;onBrowseModels?:(category:string)=>void}) {
  const [category,setCategory]=useState(initialCategory);
  return <section className="game-dev-studio" aria-label="Game Dev Studio"><header><h1>Game Dev Studio</h1><p>Create images, 3D assets, and 2D or 3D animations. Choose a model, customize generation, and save reusable presets.</p></header>
    <div className="model-category-tabs" role="group" aria-label="Game development categories">{GAME_DEV_CATEGORIES.map(([id,label])=><button key={id} aria-pressed={category===id} onClick={()=>setCategory(id)}>{label}</button>)}</div>
    {category==='background'?<p>OpenCore waits for local jobs without keeping the text model loaded, then resumes the originating conversation.</p>:<GenerationForm key={category} category={category} onNotice={onNotice} onBrowseModels={onBrowseModels}/>}<StudioJobs category={category} onNotice={onNotice}/>
  </section>;
}

// Kept as an export alias for integrations written against the previous component name.
export { GameDevStudio as AssetsStudio };
