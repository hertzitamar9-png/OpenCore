import { useEffect, useRef, useState } from 'react';
import { AppWindow, Check, Copy, Globe2, RefreshCw, ShieldOff, Square } from 'lucide-react';
import { listen } from '@tauri-apps/api/event';
import * as api from './api';
import './AutomationSettings.css';

type Props = { onNotice: (message: string) => void };

export function ComputerAccessSettings({ onNotice }: Props) {
  const [policy, setPolicy] = useState<api.ComputerAccess | null>(null);
  const [apps, setApps] = useState<api.ComputerAccessWindow[]>([]);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const revision = useRef(0);
  const saveVersion = useRef(0);
  const saving = useRef(false);
  const alive = useRef(true);
  const applied = useRef<api.ComputerAccess | null>(null);
  const accept = (next: api.ComputerAccess) => {
    if (!alive.current || (next.revision ?? 0) < (applied.current?.revision ?? 0)) return;
    applied.current = next; setPolicy(next);
  };
  const refreshApps = () => api.computerAccessWindows().then(windows => { if (alive.current) setApps(windows); }).catch(e => { if (alive.current) setError(String(e)); });
  useEffect(() => {
    alive.current = true;
    void api.computerAccess().then(accept).catch(e => { if (alive.current) setError(String(e)); });
    void refreshApps();
    const events = listen<api.ComputerAccess>('opencore-computer-access', event => {
      revision.current++; accept(event.payload);
    }).catch(() => () => {});
    const timer = window.setInterval(() => {
      if (saving.current) return;
      const version = revision.current;
      void api.computerAccess().then(settings => { if (version === revision.current) accept(settings); }).catch(e => { if (alive.current && version === revision.current) setError(String(e)); });
    }, 2500);
    return () => { alive.current = false; window.clearInterval(timer); void events.then(unlisten => unlisten()); };
  }, []);
  const save = async (next: api.ComputerAccess, urgent = false) => {
    if (saving.current && !urgent) return;
    saving.current = true; const version = ++revision.current, write = ++saveVersion.current;
    setBusy(true); setError('');
    try { accept(await api.setComputerAccess(next)); }
    catch (e) {
      if (alive.current && version === revision.current) {
        setError(String(e));
        await api.computerAccess().then(accept).catch(() => {});
      }
    }
    finally { if (write === saveVersion.current) { saving.current = false; if (alive.current) setBusy(false); } }
  };
  const permission = (app: {path: string; name: string}, access: 'allow' | 'deny') => {
    if (!policy) return;
    const identity = (path: string) => path.replaceAll('/', '\\').toLowerCase();
    void save({ ...policy, apps: [...policy.apps.filter(item => identity(item.path) !== identity(app.path)), { path: app.path, name: app.name, access }] });
  };
  return <div className="automation-settings">
    <div className="automation-heading"><div><strong>Enable computer use</strong><p className="appearance-note">Allow OpenCore to inspect and control permitted Windows apps. New apps require permission, even when tool approval is set to All.</p></div>
      <button role="switch" aria-label="Enable computer use" aria-checked={policy?.enabled ?? false} className="automation-switch" disabled={!policy || busy} onClick={() => policy && void save({ ...policy, enabled: !policy.enabled })}><span /></button>
    </div>
    <button className="automation-stop" disabled={!policy || (!policy.enabled && !busy)} onClick={() => policy && void save({ ...policy, enabled: false }, true)}><Square size={14} /> Stop computer use</button>
    <p className="appearance-note">Stopping disables further desktop actions and cancels active OpenCore tasks. Press Escape twice for an emergency stop. Actions already sent to Windows may finish.</p>
    <div className="automation-app-picker"><select aria-label="Application permission" value={selected} onChange={e => setSelected(e.target.value)}><option value="">Choose a running app</option>{apps.map(app => <option key={app.windowId} value={app.windowId}>{app.name} — {app.title}</option>)}</select><button title="Refresh running apps" onClick={() => void refreshApps()}><RefreshCw size={14} /></button></div>
    <div className="appearance-choices"><button disabled={!policy || busy || !selected} onClick={() => { const app = apps.find(item => String(item.windowId) === selected); if (app) permission(app, 'allow'); }}><Check size={14} /> Allow app</button><button disabled={!policy || busy || !selected} onClick={() => { const app = apps.find(item => String(item.windowId) === selected); if (app) permission(app, 'deny'); }}><ShieldOff size={14} /> Deny app</button></div>
    {(['allow', 'deny'] as const).map(access => <div className="automation-apps" key={access}><strong>{access === 'allow' ? 'Allowed apps' : 'Denied apps'}</strong>
      {policy?.apps.some(app => app.access === access) ? policy.apps.filter(app => app.access === access).map(app => <div className="automation-app" key={app.path}><AppWindow size={16} /><div><strong>{app.name}</strong><small title={app.path}>{app.path}</small></div><button aria-label={`${access === 'allow' ? 'Deny' : 'Allow'} ${app.name}`} disabled={busy} onClick={() => permission(app, access === 'allow' ? 'deny' : 'allow')}>{access === 'allow' ? 'Deny' : 'Allow'}</button><button aria-label={`Forget permission for ${app.name}`} disabled={busy} onClick={() => policy && void save({ ...policy, apps: policy.apps.filter(item => item.path !== app.path) })}>Remove</button></div>) : <p className="appearance-note">No {access === 'allow' ? 'allowed' : 'denied'} apps.</p>}
    </div>)}
    <p className="appearance-note">Background control supports accessible app controls and standard Windows push buttons and text fields. Games and custom canvases without accessible controls need foreground input or a browser integration. Commands or actions in allowed apps can affect other apps indirectly.</p>
    {error && <p role="alert">{error}</p>}
    {!policy && !error && <p className="appearance-note">Loading permissions…</p>}
    <button className="automation-help-link" onClick={() => onNotice('Use /computer-use in a prompt after enabling access. Allow an app here or approve its first access request in the conversation.')}>How to use computer access</button>
  </div>;
}

export function BrowserAccessSettings({ onNotice }: Props) {
  const [status, setStatus] = useState<api.BrowserStatus | null>(null);
  const [tabs, setTabs] = useState<api.BrowserTab[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const revision = useRef(0);
  const saving = useRef(false);
  useEffect(() => {
    let alive = true, pending = false;
    const refresh = async () => {
      if (pending || saving.current) return; pending = true;
      const version = revision.current;
      try {
        const next = await api.browserBridgeStatus();
        if (!alive || version !== revision.current) return;
        setStatus(next); if (!next.connected || next.enabled === false) setTabs([]);
        const listed = next.connected && next.enabled !== false ? await api.browserCommand<{tabs: api.BrowserTab[]}>('list') : { tabs: [] };
        if (alive && version === revision.current) { setTabs(listed.tabs); setError(''); }
      } catch (e) { if (alive && version === revision.current) { setError(String(e)); setTabs([]); } }
      finally { pending = false; }
    };
    void refresh(); const timer = window.setInterval(() => void refresh(), 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);
  const toggle = async () => {
    if (saving.current) return;
    saving.current = true; revision.current++;
    setBusy(true); setError('');
    try { const next = await api.setBrowserAccess(status?.enabled === false); setStatus(next); setTabs([]); }
    catch (e) { setError(String(e)); }
    finally { saving.current = false; setBusy(false); }
  };
  return <div className="automation-settings">
    <strong>{status?.connected ? 'Connected browser' : 'No browsers connected'}</strong>
    <div className="automation-browser"><Globe2 size={22} /><div><strong>Chrome</strong><p className="appearance-note">{status?.enabled === false ? 'Browser access disabled' : status?.connected ? 'Connected through the OpenCore extension' : 'Waiting for the Chrome extension'}</p></div><button disabled={!status || busy} onClick={() => void toggle()}>{status?.enabled === false ? 'Reconnect Chrome' : 'Disconnect Chrome'}</button></div>
    {tabs.length > 0 && <ul className="automation-tabs" aria-label="Connected Chrome tabs">{tabs.map(tab => <li key={tab.tabId}><span>{tab.title || 'Untitled tab'}</span><small>{tab.url}</small></li>)}</ul>}
    <p className="appearance-note">Connect Chrome on this computer using the OpenCore extension and its local Chrome profile. Disconnect blocks extension commands and automatic reconnection until you reconnect.</p>
    {status?.extensionPath && <button onClick={() => void api.openLocalPath(status.extensionPath!).catch(e => setError(String(e)))}>Open extension folder</button>}
    <code className="settings-token">{status?.token || 'Open the desktop app to pair Chrome'}</code>
    <button disabled={!status?.token} onClick={() => void navigator.clipboard.writeText(status!.token).then(() => onNotice('Pairing code copied')).catch(e => setError(String(e)))}><Copy size={14} /> Copy pairing token</button>
    <p className="appearance-note">In chrome://extensions, enable Developer mode, choose Load unpacked, then choose the extension folder. Paste the pairing token in its popup.</p>
    {error && <p role="alert">{error}</p>}
  </div>;
}
