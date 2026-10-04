import { useState } from 'react';
import { openUrl } from '@tauri-apps/plugin-opener';
import { Download } from 'lucide-react';
import * as api from './api';

type CheckedVersion = Awaited<ReturnType<typeof api.checkLatestAppVersion>>;

function useManualUpdate() {
  const [checked, setChecked] = useState<CheckedVersion | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  async function check() {
    setBusy(true);
    setMessage('Checking the latest version…');
    try {
      const result = await api.checkLatestAppVersion();
      setChecked(result);
      setMessage(result.available && result.version
        ? `OpenCore ${result.version} is available.`
        : `OpenCore ${result.currentVersion} is up to date.`);
    } catch (error) {
      setChecked(null);
      setMessage(`Could not check for updates: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function install() {
    if (!checked?.available) return;
    setBusy(true);
    setMessage(`Installing OpenCore ${checked.version}…`);
    try {
      await api.installLatestAppUpdate();
      setMessage('The installer is starting. OpenCore will reopen after the update.');
    } catch (error) {
      setMessage(`Could not install the update: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  return { checked, message, busy, check, install };
}

export function UpdateButton() {
  const [open, setOpen] = useState(false);
  const { checked, message, busy, check, install } = useManualUpdate();
  async function openUpdate() {
    setOpen((current) => !current);
    if (!open) await check();
  }

  return <div className="manual-update-control">
    <button type="button" className="manual-update-button" aria-label="Update" aria-expanded={open} disabled={busy} onClick={() => void openUpdate()}>
      <Download size={14} /> Update
    </button>
    {open && <div className="manual-update-popover" role="status" aria-live="polite">
      <span>{message || 'Check for an OpenCore update.'}</span>
      {checked?.available && <button type="button" disabled={busy} onClick={() => void install()}>{busy ? 'Installing…' : 'Install update'}</button>}
      {message.startsWith('Could not check') && <button type="button" disabled={busy} onClick={() => void check()}>{busy ? 'Checking…' : 'Try again'}</button>}
    </div>}
  </div>;
}

export function UpdateSettings() {
  const { checked, message, busy, check, install } = useManualUpdate();
  const [installerMessage, setInstallerMessage] = useState('');
  async function downloadAgain() {
    try {
      await openUrl('https://github.com/hertzitamar9-png/OpenCore/releases/latest');
      setInstallerMessage('Opened the official releases page. Download and run the installer to repair or reinstall OpenCore.');
    } catch (error) {
      setInstallerMessage(`Could not open the releases page: ${String(error)}`);
    }
  }
  return <div className="manual-update-settings">
    <p>Check manually. OpenCore never downloads or installs an update without your action.</p>
    <div className="manual-update-actions">
      <button type="button" onClick={() => void check()} disabled={busy}>{busy ? 'Checking…' : 'Check latest version'}</button>
      {checked?.available && <button type="button" onClick={() => void install()} disabled={busy}><Download size={14} />{busy ? 'Installing…' : `Update to ${checked.version}`}</button>}
      <button type="button" onClick={() => void downloadAgain()}>Download installer again</button>
    </div>
    {message && <p role="status" aria-live="polite">{message}</p>}
    {installerMessage && <p role="status" aria-live="polite">{installerMessage}</p>}
    <small>Reinstalling downloads the signed installer from the official OpenCore releases page.</small>
  </div>;
}
