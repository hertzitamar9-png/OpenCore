import { useEffect, useRef, useState } from 'react';
import { FolderOpen, Music2, Play, RefreshCw } from 'lucide-react';
import * as api from './api';
export function MusicStudio({ runtimeActive, onNotice }: { runtimeActive: boolean; onNotice: (message: string) => void }) {
  const [status, setStatus] = useState<api.MusicStudioStatus>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [model,setModel]=useState<api.InstalledModel>();
  const upgrading = useRef(false);
  const upgradeAttempted = useRef(false);
  useEffect(()=>{const refresh=async()=>{try {setModel((await api.modelLibrary()).models.find(m=>m.id==='yue2'));}catch{}};void refresh();const timer=setInterval(()=>void refresh(),3000);return()=>clearInterval(timer);},[]);
  const refresh = async () => {
    if (upgrading.current) return;
    try {
      const value = await api.musicStudioStatus(); setStatus(value);
      if (value.running && value.integrationCurrent === false && !upgradeAttempted.current) {
        upgradeAttempted.current = true;
        await start();
      }
    }
    catch (cause) { setError(String(cause)); }
  };
  useEffect(() => { void refresh(); const timer = setInterval(() => void refresh(), 5000); return () => clearInterval(timer); }, []);
  async function start() {
    if (upgrading.current) return;
    upgrading.current = true;
    setBusy(true); setError('');
    try { setStatus(await api.startMusicStudio()); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); upgrading.current = false; }
  }
  const embedded = status?.running && status.url === 'http://127.0.0.1:7860';
  return <section className={`music-studio${embedded ? ' music-studio-embedded' : ''}`} aria-label="Music Studio">
    {!embedded && <header><div><h1><Music2 size={24} /> Music Studio</h1><p>YuE2 · songs, editable scores, and generation controls.</p></div><div>
      <button onClick={() => void refresh()} aria-label="Refresh Music Studio"><RefreshCw size={16} /></button>
      {status?.installed && <button onClick={() => void api.openLocalPath(status.folder).catch(cause => onNotice(String(cause)))}><FolderOpen size={16} /> Open folder</button>}
    </div></header>}
    {!embedded && model && !model.installed && <button disabled={busy || runtimeActive} onClick={()=>{setBusy(true);void api.installModel('yue2').catch(cause=>setError(String(cause))).finally(()=>setBusy(false));}}>{model.externalManaged?'Use existing YuE2 weights':'Download YuE2 model'}</button>}
    {runtimeActive && <p className="model-library-note">Studio jobs switch models automatically after chat finishes. Stop the chat model before generating through the advanced YuE2 interface.</p>}
    {(error || status?.error) && <p role="alert">{error || status?.error}</p>}
    {status?.running && status.integrationCurrent === false && <button disabled={busy} onClick={()=>void start()}>{busy?'Updating Music Studio controls…':'Update Music Studio controls'}</button>}
    {embedded
      ? <iframe key={status.integrationCurrent === false ? 'legacy' : 'current'} title="YuE2 Music Studio" src={status.url!} sandbox="allow-scripts allow-same-origin allow-forms allow-downloads" allowFullScreen />
      : <div className="music-studio-start"><Music2 size={42} /><h2>Music workspace</h2><p>{!status ? 'Checking the YuE2 installation…' : status.installed ? 'Open the full YuE2 interface, including generation history and advanced controls.' : 'Install the YuE2 runtime locally to use this workspace.'}</p>
        <button disabled={busy || !status?.installed} onClick={() => void start()}><Play size={17} />{busy ? 'Starting Music Studio…' : 'Open Music Studio'}</button>
      </div>}
  </section>;
}
