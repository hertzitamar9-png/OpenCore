import { useEffect, useState } from 'react';
import { Download, ExternalLink } from 'lucide-react';
import * as api from './api';
import { RuntimeSetupControls } from './RuntimeSetupControls';
import { hasManagedRuntime } from './runtimeSetupApi';
import './StudioModelSetup.css';

const size = (bytes: number) => `${(bytes / 1_000_000_000).toLocaleString(undefined, {maximumFractionDigits: 2})} GB`;

export function StudioModelSetup({model, connected, disabled, onRefresh, onNotice}: {
  model: api.InstalledModel; connected: boolean; disabled?: boolean;
  onRefresh: () => Promise<void>; onNotice: (message: string) => void;
}) {
  const [starting, setStarting] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [progress, setProgress] = useState<api.ModelLibrary['progress']>(null);
  const [error, setError] = useState('');
  const managed = hasManagedRuntime(model.id);
  useEffect(() => {
    if (!installing) return;
    let alive = true;
    let pending = false;
    const refresh = (fresh = false) => {
      if (pending) return;
      pending = true;
      return api.modelLibrary({ fresh }).then(async library => {
        if (!alive) return;
        setProgress(library.progress);
        if (library.progress?.modelId !== model.id) return;
        if (library.progress.phase === 'complete') {
          setInstalling(false); await onRefresh();
          onNotice(`${model.label} weights are verified. ${managed ? 'Its runtime is being prepared automatically.' : 'Its publisher runtime requirements are listed here.'}`);
        } else if (library.progress.phase === 'failed') {
          setInstalling(false); setError(library.progress.error || 'Weight installation failed.');
        }
      }).catch(cause => {if (alive) {setError(String(cause)); setInstalling(false);}})
        .finally(() => { pending = false; });
    };
    void refresh(true); const timer = setInterval(() => void refresh(), 1000);
    return () => {alive = false; clearInterval(timer);};
  }, [installing]);
  async function install() {
    setStarting(true); setError(''); setProgress(null);
    try {
      await api.installModel(model.id);
      setInstalling(true);
      onNotice(`${model.label} weight download started. Its progress appears here.`);
    } catch (cause) {setError(String(cause)); setInstalling(false);}
    finally {setStarting(false);}
    // The desktop command starts a background transfer; completion comes from its receipt.
  }
  const current = progress?.modelId === model.id ? progress : null;
  return <section className="studio-model-setup" aria-label="Model availability">
    <div className="studio-model-setup-heading"><strong>{connected ? model.installed ? 'Ready · verified weights' : 'Ready · external weights' : model.installed ? managed ? 'Weights verified · preparing runtime' : 'Publisher runtime required' : 'Setup needed'}</strong><span>{model.precision} · {model.license}</span></div>
    <span>Verified installed weights: {model.installed ? 'Yes' : 'No'}</span>
    <p>{model.description}</p>
    <div className="studio-model-setup-actions">
      {!managed && !model.installed && model.installable !== false && model.downloadBytes > 0 && <button type="button" disabled={disabled || starting || installing} onClick={() => void install()}><Download size={15} />{starting || installing ? 'Installing weights…' : `Install weights · ${size(model.downloadBytes)}`}</button>}
      {model.sourceUrl && <a href={model.sourceUrl} target="_blank" rel="noreferrer"><ExternalLink size={14} />Pinned publisher source</a>}
      {model.setupUrl && <a href={model.setupUrl} target="_blank" rel="noreferrer"><ExternalLink size={14} />Runtime setup</a>}
    </div>
    {!model.installed && model.installable === false && <small>Obtain weights through the publisher, then connect its compatible runtime.</small>}
    {current && <div className="studio-weight-progress" role="status"><span>{current.phase} · {size(current.downloadedBytes)} / {size(current.totalBytes)}</span><progress aria-label="Weight download progress" max={current.totalBytes || 1} value={current.downloadedBytes} />{installing && <button type="button" onClick={() => void api.cancelModelInstall().catch(cause => setError(String(cause)))}>Cancel download</button>}</div>}
    {model.note && <details><summary>Model requirements and limitations</summary><p>{model.note}</p></details>}
    <RuntimeSetupControls targetId={model.id} label={model.label} installed={model.installed} autoStart={managed && model.installed && !connected} disabled={disabled} onRefresh={onRefresh} onNotice={onNotice} />
    {error && <p role="alert">{error}</p>}
  </section>;
}
