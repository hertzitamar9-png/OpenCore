import { useEffect, useRef, useState } from 'react';
import { Download, RefreshCw, Square, Terminal } from 'lucide-react';
import * as setup from './runtimeSetupApi';
import './RuntimeSetupControls.css';

const bytes = (value: number) => `${(value / 1e9).toLocaleString(undefined, {maximumFractionDigits: 2})} GB`;
const human = (value: string) => value.replaceAll('-', ' ');

export function RuntimeSetupControls({targetId, label, installed = false, disabled = false, onRefresh, onNotice}: {
  targetId: string; label: string; installed?: boolean; disabled?: boolean;
  onRefresh?: () => Promise<void>; onNotice?: (message: string) => void;
}) {
  const [snapshot, setSnapshot] = useState<setup.SetupSnapshot | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const [acceptLicenses, setAcceptLicenses] = useState(false);
  const [allowAdministrator, setAllowAdministrator] = useState(false);
  const [isoPath, setIsoPath] = useState('');
  const [guestUser, setGuestUser] = useState('opencore');
  const [passwordEnv, setPasswordEnv] = useState('OPENCORE_TEST_GUEST_PASSWORD');
  const reported = useRef<string | null>(null);
  const callbacks = useRef({onRefresh, onNotice});
  callbacks.current = {onRefresh, onNotice};
  useEffect(() => {
    let alive = true;
    async function refresh() {
      try {
        const next = await setup.runtimeSetupStatus();
        if (!alive) return;
        setSnapshot(next);
        const job = [...next.jobs].reverse().find(value => value.targetId === targetId);
        if (job && !setup.isSetupActive(job) && job.id !== reported.current) {
          reported.current = job.id;
          if (job.status === 'dependencies-verified') {
            await callbacks.current.onRefresh?.();
            callbacks.current.onNotice?.(`${label} dependencies are verified and configured. Run a real generation or transcription to verify inference.`);
          }
        }
      } catch (cause) { if (alive) setError(String(cause)); }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 1500);
    return () => { alive = false; clearInterval(timer); };
  }, [targetId, label]);
  const recipe = snapshot?.recipes.find(value => value.modelIds.includes(targetId));
  const job = snapshot ? [...snapshot.jobs].reverse().find(value => value.targetId === targetId) : undefined;
  const receipt = snapshot?.receipts.find(value => value.targetId === targetId);
  const active = setup.isSetupActive(job);
  const otherSetup = !!snapshot?.activeJobId && snapshot.activeJobId !== job?.id;
  const isModel = recipe?.kind === 'studio' || recipe?.kind === 'speech';
  async function start() {
    setStarting(true); setError('');
    try {
      await setup.runtimeSetupStart(targetId, {installWeights: !!isModel && !installed,
        acceptLicenses, allowAdministrator,
        ...(recipe?.kind === 'virtualbox-guest' ? {isoPath: isoPath.trim(), guestUser: guestUser.trim(), passwordEnv: passwordEnv.trim()} : {})});
      setSnapshot(await setup.runtimeSetupStatus());
      onNotice?.(`${label} setup started. Its progress and diagnostics are saved across app restarts.`);
    } catch (cause) { setError(String(cause)); }
    finally { setStarting(false); }
  }
  async function cancel() {
    if (!job) return;
    try { await setup.runtimeSetupCancel(job.id); setSnapshot(await setup.runtimeSetupStatus()); }
    catch (cause) { setError(String(cause)); }
  }
  if (!snapshot && !error) return <div className="runtime-setup-controls" role="status">Checking automatic runtime setup…</div>;
  return <div className="runtime-setup-controls" aria-label={`${label} automatic setup`}>
    {!recipe ? <small>Automatic runtime setup is unavailable for this architecture. Connect its publisher-compatible worker and dependencies.</small> : <>
      <div className="runtime-setup-summary"><strong>{receipt?.inferenceVerified ? 'Inference verified'
        : receipt?.dependenciesVerified ? isModel ? 'Dependencies verified · inference not verified' : receipt.environmentVerified ? 'Testing environment verified' : 'Host tools verified'
        : 'Automatic setup available'}</strong></div>
      <small>{recipe.label} · {bytes(recipe.minimumDiskBytes)} free disk for dependencies · {bytes(recipe.minimumRamBytes)} host RAM minimum{recipe.requiresCuda ? ' · NVIDIA CUDA required' : ''}</small>
      {recipe.requiresLicenseAcceptance && <label className="runtime-setup-check"><input type="checkbox" checked={acceptLicenses} onChange={event => setAcceptLicenses(event.target.checked)} disabled={active} />I accept the publisher licenses for this setup{recipe.kind === 'virtualbox-guest' ? ' and confirm I am entitled to use the selected OS image' : ''}.</label>}
      {recipe.requiresLicenseAcceptance && <div className="runtime-setup-sources">{recipe.sourceUrls.map((url, index) => <a key={url} href={url} target="_blank" rel="noreferrer">{index === 0 ? 'Publisher and license information' : `Publisher setup reference ${index + 1}`}</a>)}{recipe.kind === 'android' && <a href="https://developer.android.com/studio/terms" target="_blank" rel="noreferrer">Android SDK license terms</a>}</div>}
      {recipe.requiresAdministrator && <label className="runtime-setup-check"><input type="checkbox" checked={allowAdministrator} onChange={event => setAllowAdministrator(event.target.checked)} disabled={active} />Allow Windows to request administrator permission for the VirtualBox host drivers.</label>}
      {recipe.kind === 'virtualbox-guest' && <div className="runtime-setup-fields">
        <label>Licensed operating-system ISO<input aria-label="Licensed operating-system ISO" value={isoPath} onChange={event => setIsoPath(event.target.value)} placeholder="C:\\Images\\licensed-system.iso" disabled={active} /></label>
        <label>Guest user<input value={guestUser} onChange={event => setGuestUser(event.target.value)} disabled={active} /></label>
        <label>Guest password environment variable<input value={passwordEnv} onChange={event => setPasswordEnv(event.target.value)} disabled={active} /></label>
      </div>}
      <div className="runtime-setup-actions">
        <button type="button" disabled={disabled || active || starting || otherSetup || !setup.setupDesktopAvailable() || !!recipe.requiresLicenseAcceptance && !acceptLicenses || recipe.kind === 'virtualbox-guest' && !isoPath.trim()} onClick={() => void start()}>{receipt ? <RefreshCw size={14} /> : <Download size={14} />}{starting ? 'Starting setup…' : receipt ? 'Recheck runtime setup' : 'Set up automatically'}</button>
        {active && <button type="button" onClick={() => void cancel()} disabled={job?.status === 'cancelling'}><Square size={13} />{job?.status === 'cancelling' ? 'Cancelling setup…' : 'Cancel setup'}</button>}
      </div>
      {!setup.setupDesktopAvailable() && <small>Automatic installation runs in the desktop app.</small>}
      {otherSetup && <small>Another setup is running. Its saved progress is available in Runtime setup.</small>}
      {job && <div role="status" className="runtime-setup-progress"><span>{human(job.stage)}</span><span>{job.detail}</span>{job.totalBytes > 0 && <><progress aria-label="Runtime setup download" max={job.totalBytes} value={job.downloadedBytes} /><small>{bytes(job.downloadedBytes)} / {bytes(job.totalBytes)}</small></>}</div>}
      {receipt?.python && <small className="runtime-setup-path">Managed Python: {receipt.python}</small>}
      <details><summary>Setup requirements and diagnostics</summary><p>{recipe.limitations}</p>{job?.diagnostics.length ? <pre><Terminal size={13} />{job.diagnostics.join('\n')}</pre> : <small>No setup diagnostics recorded yet.</small>}</details>
      {job?.error && <p role="alert">{job.error}</p>}
    </>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

export function RuntimeSetupPanel({onNotice}: {onNotice?: (message: string) => void}) {
  const [inventory, setInventory] = useState<setup.SetupInventory | null>(null);
  const [probing, setProbing] = useState(false);
  const [error, setError] = useState('');
  async function probe() {
    setProbing(true); setError('');
    try { setInventory(await setup.runtimeSetupProbe()); }
    catch (cause) { setError(String(cause)); }
    finally { setProbing(false); }
  }
  return <section className="runtime-setup-panel" aria-label="Runtime setup">
    <h3>Runtime setup</h3><p>Model setup is available beside each supported studio model. Testing environment setup uses isolated app-managed SDK and VM folders.</p>
    <button type="button" disabled={probing || !setup.setupDesktopAvailable()} onClick={() => void probe()}><RefreshCw size={14} />{probing ? 'Detecting environments…' : 'Detect Python, GPU and testing tools'}</button>
    {inventory && <div role="status" className="runtime-setup-inventory">
      <span>{inventory.ramBytes ? `${bytes(inventory.ramBytes)} RAM` : 'RAM unavailable'}{inventory.diskFreeBytes ? ` · ${bytes(inventory.diskFreeBytes)} free disk` : ''}</span>
      {inventory.python.map(python => <span key={python.path}>Python {python.version.join('.')} · {python.compatible ? 'compatible' : 'unsupported version'} · {python.path}</span>)}
      {inventory.pythonError && <span>{inventory.pythonError}</span>}
      {inventory.gpus.map(gpu => <span key={gpu.name}>{gpu.name} · {bytes(gpu.totalBytes)} VRAM · driver {gpu.driver}</span>)}
      <span>Android SDKs: {inventory.tools.android?.length || 0} · Java runtimes: {inventory.tools.java?.length || 0} · VirtualBox: {inventory.tools.virtualbox?.version || 'not detected'}</span>
    </div>}
    <RuntimeSetupControls targetId="testing-android" label="Android emulator" onNotice={onNotice} />
    <RuntimeSetupControls targetId="testing-virtualbox" label="VirtualBox" onNotice={onNotice} />
    <RuntimeSetupControls targetId="testing-pc-vm" label="PC testing VM" onNotice={onNotice} />
    {error && <p role="alert">{error}</p>}
  </section>;
}
