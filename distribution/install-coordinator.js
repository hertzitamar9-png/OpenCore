/* Shared installation admission and progress for the recovered v0.2.305 UI. */
function createInstallNotificationPolicy(options = {}) {
  const scope = options.scope || (() => globalThis.__opencoreModelLibrary?.getScope?.() || '');
  const read = options.read || (key => globalThis.localStorage?.getItem(key));
  const write = options.write || ((key, value) => globalThis.localStorage?.setItem(key, value));
  const terminal = new Set(['ready', 'failed', 'paused', 'cancelled', 'canceled', 'completed']);
  let loadedScope, dismissed = new Map();
  const operation = value => {
    const id = value?.operationId || value?.jobId;
    return typeof id === 'string' ? id : typeof id === 'number' ? String(id) : null;
  };
  function load() {
    const current = String(scope());
    if (current === loadedScope) return;
    loadedScope = current; dismissed = new Map();
    if (!current) return;
    try {
      const saved = JSON.parse(read('opencore.install-dismissals.v1:' + current) || 'null');
      if (saved?.schema === 1 && Array.isArray(saved.items)) for (const item of saved.items.slice(-128)) {
        if (typeof item.modelId === 'string' && item.modelId.length < 512 &&
            (item.operationId === null || typeof item.operationId === 'string' && item.operationId.length < 1024)) {
          dismissed.set(JSON.stringify([item.modelId, item.operationId]), item);
        }
      }
    } catch {}
  }
  function save() {
    while (dismissed.size > 128) dismissed.delete(dismissed.keys().next().value);
    if (loadedScope) try {
      write('opencore.install-dismissals.v1:' + loadedScope, JSON.stringify({schema: 1, items: [...dismissed.values()]}));
    } catch {}
  }
  function begin(modelId) {
    load();
    let changed = false;
    for (const [key, item] of dismissed) if (item.modelId === modelId) { dismissed.delete(key); changed = true; }
    if (changed) save();
  }
  function observe(progress) {
    load();
    const previous = [...dismissed.values()].filter(item => item.modelId === progress?.modelId), id = operation(progress);
    if (previous.length && id && !previous.some(item => item.operationId === id) && !terminal.has(progress.phase)) begin(progress.modelId);
  }
  function hidden(progress) {
    load();
    if (!terminal.has(progress?.phase)) return false;
    const previous = [...dismissed.values()].filter(item => item.modelId === progress.modelId), id = operation(progress);
    // Legacy catalog progress can omit the ID present in runtime status. Keep
    // its acknowledgement until this model starts another real operation.
    return previous.some(item => !item.operationId || !id || item.operationId === id);
  }
  function dismiss(progress) {
    load();
    if (!progress?.modelId || !terminal.has(progress.phase)) return;
    const key = JSON.stringify([progress.modelId, operation(progress)]);
    dismissed.delete(key);
    dismissed.set(key, {modelId: progress.modelId, operationId: operation(progress)});
    save();
  }
  function same(left, right) {
    return !!left && !!right && left.modelId === right.modelId && operation(left) === operation(right);
  }
  return {begin, observe, hidden, dismiss, same};
}

function createInstallCoordinator(nativeInvoke, now = () => Date.now(), notifications = createInstallNotificationPolicy()) {
  const listeners = new Set();
  const starts = new Set(['install_model', 'runtime_setup_start', 'install_publisher_model', 'register_prepared_model', 'hub_prepare_model']);
  const activePhases = new Set(['preparing', 'downloading', 'verifying', 'uninstalling']);
  const activeStatuses = new Set(['queued', 'running', 'cancelling']);
  let snapshot = { active: null, progress: null, runtime: null, library: null, initialized: false, terminal: 0, receiptsRevision: 0, notificationsRevision: 0 };
  let runtime = null, catalogueProgress = null, starting = null, polling = null, timer = null;
  let receiptKey = '', paused = new Set();

  function publish(values) {
    const next = { ...snapshot, ...values };
    if (JSON.stringify(next) === JSON.stringify(snapshot)) return;
    snapshot = next;
    for (const listener of listeners) listener();
  }
  function reconcile() {
    const jobs = runtime?.jobs || [];
    const job = jobs.find(value => value.id === runtime?.activeJobId && activeStatuses.has(value.status)) ||
      [...jobs].reverse().find(value => activeStatuses.has(value.status));
    const download = catalogueProgress && activePhases.has(catalogueProgress.phase) ? catalogueProgress : null;
    let active = null, progress = snapshot.progress;
    if (job) {
      const total = Math.max(0, Number(job.totalBytes) || 0);
      const stage = String(job.stage || 'preparing').replaceAll('-', ' ');
      const verifying = /verif|loading.*model|test.*generation/.test(job.stage || '');
      active = { modelId: job.targetId, jobId: job.id, kind: 'runtime', pausing: job.status === 'cancelling' };
      progress = { modelId: job.targetId, operationId: job.id,
        phase: verifying ? 'verifying' : total > 0 ? 'downloading' : 'preparing',
        downloadedBytes: Math.min(total, Math.max(0, Number(job.downloadedBytes) || 0)), totalBytes: total,
        currentFile: job.detail || stage, error: null };
    } else if (download) {
      active = { modelId: download.modelId, kind: 'download', pausing: false };
      progress = download;
    } else if (starting && (starting.pending || starting.unknown || now() - starting.at < 2000)) {
      active = { modelId: starting.modelId, kind: 'starting', pausing: false };
      progress = { modelId: starting.modelId, phase: 'preparing', downloadedBytes: 0, totalBytes: 0,
        currentFile: 'Starting installation…', error: null };
    } else if (snapshot.active) {
      const previous = snapshot.active;
      const terminalJob = previous.kind === 'download' ? null : [...jobs].reverse().find(value => value.targetId === previous.modelId);
      const terminalDownload = previous.kind !== 'runtime' && catalogueProgress?.modelId === previous.modelId ? catalogueProgress : null;
      const error = terminalJob?.error || terminalDownload?.error;
      const status = terminalJob?.status || terminalDownload?.phase;
      const phase = status === 'ready' ? 'ready' : status === 'cancelled' || /cancelled|canceled/i.test(error || '') ? 'paused' :
        status === 'failed' || error ? 'failed' : paused.has(previous.modelId) ? 'paused' : 'ready';
      progress = { ...(snapshot.progress || {}), modelId: previous.modelId, phase,
        operationId: terminalJob?.id || terminalDownload?.operationId || terminalDownload?.jobId || snapshot.progress?.operationId,
        currentFile: phase === 'paused' ? 'Paused. Completed files are kept for the next attempt.' : terminalJob?.detail || terminalDownload?.currentFile || (phase === 'failed' ? 'Installation failed.' : 'Installation complete.'),
        error: phase === 'failed' ? error || 'Installation failed.' : null };
      starting = null;
    }
    const terminal = snapshot.terminal + (snapshot.active && !active ? 1 : 0);
    if (active) notifications.observe(progress);
    const key = JSON.stringify(runtime?.receipts || []);
    const receiptsRevision = snapshot.receiptsRevision + (key !== receiptKey ? 1 : 0);
    const libraryChanged = terminal !== snapshot.terminal || (receiptKey && key !== receiptKey);
    receiptKey = key;
    publish({ active, progress, runtime, terminal, receiptsRevision });
    if (libraryChanged && typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('opencore-model-library-invalidated'));
    if (!listeners.size && !active && timer) { clearInterval(timer); timer = null; }
  }
  async function refresh(fresh = false) {
    if (polling) {
      if (fresh) { await polling; return refresh(); }
      return polling;
    }
    polling = Promise.allSettled([
      nativeInvoke('runtime_setup_status', {}), nativeInvoke('model_install_progress', {}),
    ]).then(([setup, download]) => {
      // A failed status read cannot release an installation lock.
      const setupValid = setup.status === 'fulfilled' && Array.isArray(setup.value?.jobs) && Array.isArray(setup.value?.receipts);
      const downloadValid = download.status === 'fulfilled' && (download.value === null || typeof download.value?.phase === 'string');
      if (setupValid) runtime = setup.value;
      if (downloadValid) catalogueProgress = download.value;
      const checked = setupValid && downloadValid;
      publish({ initialized: checked });
      if (checked) {
        if (starting) starting.unknown = false;
      }
      reconcile();
      return checked;
    }).finally(() => { polling = null; });
    return polling;
  }
  function ensurePolling() {
    if (!timer) timer = setInterval(() => void refresh(), 1000);
    void refresh();
  }
  async function invoke(command, args, run) {
    if (starts.has(command)) {
      if (!snapshot.initialized) await refresh(true);
      if (!snapshot.initialized) throw new Error('Cannot check installation status. Retry when the current status is available.');
      if (snapshot.active || starting?.pending) throw new Error('Another model is installing. Pause it or wait for it to finish.');
      const modelId = args.targetId || args.id || args.modelId;
      notifications.begin(modelId);
      paused.delete(modelId);
      starting = { modelId, pending: true, at: now() };
      reconcile(); ensurePolling();
      try {
        const result = await run();
        const checked = await refresh(true);
        if (starting) { starting.pending = false; starting.unknown = !checked; }
        reconcile();
        return result;
      } catch (error) {
        starting = { modelId, pending: false, unknown: true, at: now() };
        const checked = await refresh(true);
        if (checked) {
          starting = null; reconcile();
          if (!snapshot.active) publish({ terminal: snapshot.terminal + 1,
            progress: { modelId, phase: 'failed', downloadedBytes: 0, totalBytes: 0, currentFile: '', error: String(error) } });
        }
        throw error;
      }
    }
    if (command === 'runtime_setup_cancel' || command === 'cancel_model_install') {
      const modelId = snapshot.active?.modelId;
      if (snapshot.active) {
        publish({ active: { ...snapshot.active, pausing: true } });
      }
      try {
        const result = await run();
        if (modelId) paused.add(modelId);
        return result;
      } catch (error) { if (modelId) paused.delete(modelId); throw error; }
      finally { starting = null; await refresh(true); }
    }
    if (command === 'dismiss_model_install_progress') {
      const current = snapshot.progress || snapshot.library?.progress || catalogueProgress;
      if (snapshot.active || !notifications.same(args.expected, current)) return false;
      // Acknowledge before native cleanup. Polls and cached library snapshots
      // may still carry the completed operation after native dismissal returns.
      for (const progress of [snapshot.library?.progress, catalogueProgress, snapshot.progress, args.expected]) notifications.dismiss(progress);
      publish({progress: null, notificationsRevision: snapshot.notificationsRevision + 1});
      try { return await run(); } catch { return false; }
    }
    const result = await run();
    if (['uninstall_model', 'register_prepared_model'].includes(command) && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('opencore-model-library-invalidated'));
    }
    if (command === 'runtime_setup_status') { runtime = result; reconcile(); }
    if (command === 'model_install_progress') { catalogueProgress = result; reconcile(); }
    if (command === 'list_model_library') publish({ library: result });
    return result;
  }
  async function pause() {
    const operation = snapshot.active;
    if (!operation || operation.pausing) return;
    if (operation.kind === 'starting') {
      // Keep the lock until native admission supplies the cancellable job.
      await refresh();
      if (snapshot.active?.kind === 'starting') throw new Error('Installation is starting. Pause will be available in a moment.');
      return pause();
    }
    const command = operation.kind === 'runtime' ? 'runtime_setup_cancel' : 'cancel_model_install';
    const args = operation.jobId ? { jobId: operation.jobId } : {};
    return invoke(command, args, () => nativeInvoke(command, args));
  }
  return {
    invoke, refresh, pause, getSnapshot: () => snapshot,
    visibleProgress(...values) {
      const progress = values.find(Boolean) || null;
      return notifications.hidden(progress) ? null : progress;
    },
    subscribe(listener) { listeners.add(listener); ensurePolling(); return () => {
      listeners.delete(listener);
      if (!listeners.size && !snapshot.active && timer) { clearInterval(timer); timer = null; }
    }; },
  };
}

const $installCoordinator = createInstallCoordinator((command, args) => $hubInvoke(command,args,()=>window.__TAURI_INTERNALS__.invoke(command,args)));
globalThis.__opencoreInstallBridge = {invoke:(command,args,run)=>$installCoordinator.invoke(command,args,()=>$hubInvoke(command,args,run))};
function $useInstallState() {
  return p.useSyncExternalStore($installCoordinator.subscribe, $installCoordinator.getSnapshot, $installCoordinator.getSnapshot);
}
