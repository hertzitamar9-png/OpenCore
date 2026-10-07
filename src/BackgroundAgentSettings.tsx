import { useEffect, useRef, useState } from 'react';
import { backgroundAgentStatus, configureBackgroundAgent, type BackgroundAgentConfig, type BackgroundAgentStatus } from './background-agent';
import './BackgroundAgentSettings.css';

export function BackgroundAgentSettings() {
  const [status, setStatus] = useState<BackgroundAgentStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);
  const pending = useRef(false);
  useEffect(() => {
    alive.current = true;
    void backgroundAgentStatus().then(value => { if (alive.current) setStatus(value); }).catch(reason => { if (alive.current) setError(String(reason)); });
    return () => { alive.current = false; };
  }, []);
  async function save(configuration: BackgroundAgentConfig) {
    if (pending.current) return;
    pending.current = true; setSaving(true); setError('');
    try { const next = await configureBackgroundAgent(configuration); if (alive.current) setStatus(next); }
    catch (reason) { if (alive.current) setError(String(reason).replace(/^Error:\s*/, '')); }
    finally { pending.current = false; if (alive.current) setSaving(false); }
  }
  return <section className="background-agent-settings" aria-label="Background agent">
    <div className="background-agent-heading"><h3>Background agent</h3><span role="status">{saving ? 'Saving…' : status ? 'Saved automatically' : 'Loading…'}</span></div>
    <label><input type="checkbox" checked={status?.configuration.enabled ?? false} disabled={!status?.trayAvailable || saving} onChange={event => status && void save({ ...status.configuration, enabled: event.target.checked, startAtLogin: event.target.checked && status.configuration.startAtLogin })} />Keep jobs running when the window closes</label>
    <label><input type="checkbox" checked={status?.configuration.startAtLogin ?? false} disabled={!status?.configuration.enabled || !status.loginStartupSupported || saving} onChange={event => status && void save({ ...status.configuration, startAtLogin: event.target.checked })} />Start the background agent when I sign in to Windows</label>
    <p>{status?.configuration.enabled ? 'Closing the window keeps schedules, workers and webhooks running. Reopen OpenCore from its tray icon or shortcut.' : 'Enable the background agent to continue schedules, workers and webhooks after closing the window.'} Quit OpenCore from its tray menu to stop background work. The computer must remain awake and signed in.</p>
    {status && !status.trayAvailable && <p>The system tray is unavailable. Closing the window will quit OpenCore.</p>}
    {status?.configuration.startAtLogin && !status.loginRegistered && <p role="alert">Windows login startup is not registered. Turn the option off and on to repair registration.</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
