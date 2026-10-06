import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { openUrl } from '@tauri-apps/plugin-opener';
import { Download } from 'lucide-react';
import * as api from './api';

type CheckedVersion = Awaited<ReturnType<typeof api.checkLatestAppVersion>>;

let pendingVersionCheck: Promise<CheckedVersion> | null = null;
const versionCheckListeners = new Set<(checked: CheckedVersion | null) => void>();
let installingUpdate = false;
const installListeners = new Set<() => void>();
const subscribeInstall = (listener: () => void) => { installListeners.add(listener); return () => { installListeners.delete(listener); }; };
export const useAppUpdateInstalling = () => useSyncExternalStore(subscribeInstall, () => installingUpdate);

function setInstallingUpdate(value: boolean) {
  installingUpdate = value;
  installListeners.forEach(listener => listener());
}

function checkAppVersion() {
  if (!pendingVersionCheck) {
    pendingVersionCheck = api.checkLatestAppVersion().finally(() => { pendingVersionCheck = null; });
  }
  return pendingVersionCheck;
}

function useManualUpdate(checkOnMount = false) {
  const [checked, setChecked] = useState<CheckedVersion | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const installing = useAppUpdateInstalling();

  useEffect(() => {
    versionCheckListeners.add(setChecked);
    return () => { versionCheckListeners.delete(setChecked); };
  }, []);

  const check = useCallback(async () => {
    setBusy(true);
    setMessage('Checking the latest version…');
    try {
      const result = await checkAppVersion();
      versionCheckListeners.forEach(listener => listener(result));
      setMessage(result.available && result.version
        ? `OpenCore ${result.version} is available.`
        : `OpenCore ${result.currentVersion} is up to date.`);
    } catch (error) {
      versionCheckListeners.forEach(listener => listener(null));
      setMessage(`Could not check for updates: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (checkOnMount) void check();
  }, [checkOnMount, check]);

  async function install() {
    if (!checked?.available || installingUpdate) return;
    setInstallingUpdate(true);
    setBusy(true);
    setMessage(`Stopping active model work, then installing OpenCore ${checked.version}…`);
    try {
      await api.installLatestAppUpdate();
      setMessage('The installer is starting. OpenCore will reopen after the update.');
    } catch (error) {
      setMessage(`Could not install the update: ${String(error)}`);
    } finally {
      setInstallingUpdate(false);
      setBusy(false);
    }
  }

  return { checked, message, busy: busy || installing, check, install };
}

export function UpdateButton() {
  const [open, setOpen] = useState(false);
  const [showMessage, setShowMessage] = useState(false);
  const { checked, message, busy, check, install } = useManualUpdate(true);
  useEffect(() => {
    if (!open || !message) { setShowMessage(false); return; }
    setShowMessage(true);
    const timer = window.setTimeout(() => setShowMessage(false), 5000);
    return () => window.clearTimeout(timer);
  }, [open, message]);
  async function openUpdate() {
    setOpen((current) => !current);
    if (!open) await check();
  }

  if (!checked?.available) return null;

  return <div className="manual-update-control">
    {open && <div className="manual-update-inline">
      {showMessage && <span className="manual-update-message" role="status" aria-live="polite">{message}</span>}
      <button type="button" disabled={busy} onClick={() => void install()}>{busy ? 'Updating…' : 'Install update'}</button>
    </div>}
    <button type="button" className="manual-update-button" aria-label="Update" aria-expanded={open} disabled={busy} onClick={() => void openUpdate()}>
      <Download size={14} /> Update
    </button>
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
      {checked?.available && <button type="button" onClick={() => void install()} disabled={busy}><Download size={14} />{busy ? 'Updating…' : `Update to ${checked.version}`}</button>}
      <button type="button" onClick={() => void downloadAgain()}>Download installer again</button>
    </div>
    {message && <p role="status" aria-live="polite">{message}</p>}
    {installerMessage && <p role="status" aria-live="polite">{installerMessage}</p>}
    <small>Reinstalling downloads the signed installer from the official OpenCore releases page.</small>
  </div>;
}
