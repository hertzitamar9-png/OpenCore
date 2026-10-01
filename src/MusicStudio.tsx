import { useEffect, useState } from 'react';
import { FolderOpen, Music2, Play, RefreshCw } from 'lucide-react';
import * as api from './api';
export function MusicStudio({ runtimeActive, onNotice }: { runtimeActive: boolean; onNotice: (message: string) => void }) {
  const [status, setStatus] = useState<api.MusicStudioStatus>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const refresh = async () => {
    try { const value = await api.musicStudioStatus(); setStatus(value); }
    catch (cause) { setError(String(cause)); }
  };
  useEffect(() => { void refresh(); const timer = setInterval(() => void refresh(), 5000); return () => clearInterval(timer); }, []);
  async function start() {
    setBusy(true); setError('');
    try { setStatus(await api.startMusicStudio()); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <section className="music-studio" aria-label="Music Studio">
    <header><div><h1><Music2 size={24} /> Music Studio</h1><p>YuE2 · your existing models, songs, and generation controls.</p></div><div>
      <button onClick={() => void refresh()} aria-label="Refresh Music Studio"><RefreshCw size={16} /></button>
      {status?.installed && <button onClick={() => void api.openLocalPath(status.folder).catch(cause => onNotice(String(cause)))}><FolderOpen size={16} /> Open folder</button>}
    </div></header>
    {runtimeActive && <p className="model-library-note">The chat model is using the GPU. Stop it before loading the music model.</p>}
    {(error || status?.error) && <p role="alert">{error || status?.error}</p>}
    {status?.running && status.url === 'http://127.0.0.1:7860'
      ? <iframe title="YuE2 Music Studio" src={status.url} sandbox="allow-scripts allow-same-origin allow-forms allow-downloads" />
      : <div className="music-studio-start"><Music2 size={42} /><h2>Your music workspace</h2><p>{!status ? 'Checking your YuE2 installation…' : status.installed ? 'Connect to YuE2 Studio without opening another window. Model loading stays under your control.' : 'Install YuE2 Studio locally to use this workspace.'}</p>
        <button disabled={busy || !status?.installed} onClick={() => void start()}><Play size={17} />{busy ? 'Starting Music Studio…' : 'Open Music Studio'}</button>
      </div>}
  </section>;
}
