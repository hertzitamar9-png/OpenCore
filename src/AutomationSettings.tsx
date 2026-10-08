import { useEffect, useRef, useState } from 'react';
import { Check, Copy, Globe2, Square } from 'lucide-react';
import { listen } from '@tauri-apps/api/event';
import * as api from './api';
import './AutomationSettings.css';

type Props = { onNotice: (message: string) => void };

export function ComputerAccessSettings({ onNotice }: Props) {
  const [policy, setPolicy] = useState<api.ComputerAccess | null>(null);
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
  useEffect(() => {
    alive.current = true;
    void api.computerAccess().then(accept).catch(e => { if (alive.current) setError(String(e)); });
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
  return <div className="automation-settings">
    <div className="automation-heading"><div><strong>Enable computer use</strong><p className="appearance-note">OpenCore can inspect and control this PC while computer use is running. Apps do not require individual approval.</p></div>
      <button role="switch" aria-label="Enable computer use" aria-checked={policy?.enabled ?? false} className="automation-switch" disabled={!policy || busy} onClick={() => policy && void save({ ...policy, enabled: !policy.enabled })}><span /></button>
    </div>
    <button className="automation-stop" disabled={!policy || (!policy.enabled && !busy)} onClick={() => policy && void save({ ...policy, enabled: false }, true)}><Square size={14} /> Stop computer use</button>
    <p className="appearance-note">Stopping disables further desktop actions and cancels active OpenCore tasks. Press Escape twice for an emergency stop. Actions already sent to Windows may finish.</p>
    <p className="appearance-note">Background control supports accessible app controls and standard Windows push buttons and text fields. Games and custom canvases without accessible controls need foreground input or a browser integration.</p>
    {error && <p role="alert">{error}</p>}
    {!policy && !error && <p className="appearance-note">Loading computer controls…</p>}
    <button className="automation-help-link" onClick={() => onNotice('Use /computer-use in a prompt or open Computer to control your Windows apps and desktop. Use Stop computer use to pause access.')}>How to use computer access</button>
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
