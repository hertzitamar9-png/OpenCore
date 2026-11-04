/* Shared installation admission and progress for the recovered v0.2.305 UI. */
function createInstallNotificationPolicy(options = {}) {
  const scope = options.scope || (() => globalThis.__opencoreModelLibrary?.getScope?.() || '');
  const read = options.read || (key => globalThis.localStorage?.getItem(key));
  const write = options.write || ((key, value) => globalThis.localStorage?.setItem(key, value));
  const terminal = new Set(['ready', 'failed', 'paused', 'cancelled', 'canceled', 'completed']);
  let loadedScope, dismissed = new Map(), startedHere = new Set(), observedActive = new Set();
  const operation = value => {
    const id = value?.operationId || value?.jobId;
    return typeof id === 'string' ? id : typeof id === 'number' ? String(id) : null;
  };
  function load() {
    const current = String(scope());
    if (current === loadedScope) return;
    loadedScope = current; dismissed = new Map(); startedHere = new Set(); observedActive = new Set();
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
    if(modelId)startedHere.add(String(modelId).toLowerCase());
    let changed = false;
    for (const [key, item] of dismissed) if (item.modelId === modelId) { dismissed.delete(key); changed = true; }
    if (changed) save();
  }
  function observe(progress) {
    load();
    // Phonon revalidates its installed runtime on startup. A new verifier ID
    // is not another installation; explicit admission already calls begin().
    if(progress?.modelId==='phonon-2'&&['preparing','verifying'].includes(progress.phase))return;
    if(progress?.modelId)observedActive.add(String(progress.modelId).toLowerCase());
    const previous = [...dismissed.values()].filter(item => item.modelId === progress?.modelId), id = operation(progress);
    if (previous.length && id && !previous.some(item => item.operationId === id) && !terminal.has(progress.phase)) begin(progress.modelId);
  }
  function hidden(progress) {
    load();
    // Startup verification is not an installation the user started. It must
    // not reopen the progress panel on every launch, even with a new job ID.
    if(String(progress?.modelId||'').toLowerCase()==='phonon-2'&&
       ['preparing','verifying','ready'].includes(progress.phase)&&!startedHere.has('phonon-2'))return true;
    if (!terminal.has(progress?.phase)) return false;
    // Persisted terminal records are history, not fresh notifications. A job
    // observed running this session may still report its completion normally.
    const modelId=String(progress?.modelId||'').toLowerCase();
    if(!startedHere.has(modelId)&&!observedActive.has(modelId))return true;
    // Native progress can replay an old Phonon completion on every launch,
    // including before any dismissal was saved. Only show its success when
    // this app session actually started another installation.
    const previous = [...dismissed.values()].filter(item => item.modelId === progress.modelId), id = operation(progress);
    if(progress.modelId==='phonon-2'&&progress.phase==='ready'&&previous.length)return true;
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
  const activePhases = new Set(['preparing', 'downloading', 'verifying', 'uninstalling', 'cancelling']);
  const activeStatuses = new Set(['queued', 'running', 'cancelling']);
  let snapshot = { active: null, progress: null, runtime: null, library: null, queue: null, initialized: false, terminal: 0, receiptsRevision: 0, notificationsRevision: 0 };
  let runtime = null, catalogueProgress = null, starting = null, polling = null, timer = null, cancellingOperation = null;
  let receiptKey = '', queueReadyKey = '', paused = new Set(), cancelled = new Set();

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
    const queue = snapshot.queue, queueActive = queue?.items?.find(item => queue.activeIds.includes(item.id));
    const queuePrevious = snapshot.active?.kind === 'queue' ? queue?.items?.find(item => item.id === snapshot.active.jobId) : null;
    let active = null, progress = snapshot.progress;
    if (queueActive) {
      active = {modelId:queueActive.modelId,jobId:queueActive.id,kind:'queue',pausing:/pausing|stopping/i.test(queueActive.detail||'')};
      progress = {modelId:queueActive.modelId,operationId:queueActive.id,phase:queueActive.phase === 'starting' ? 'preparing' : queueActive.phase,
        downloadedBytes:queueActive.downloadedBytes||0,totalBytes:queueActive.totalBytes||0,bytesPerSecond:queueActive.bytesPerSecond||0,etaSeconds:queueActive.etaSeconds,
        currentFile:queueActive.detail||queueActive.stage||'',error:queueActive.error||null};
    } else if (queuePrevious) {
      progress = {modelId:queuePrevious.modelId,operationId:queuePrevious.id,phase:queuePrevious.phase,
        downloadedBytes:queuePrevious.downloadedBytes||0,totalBytes:queuePrevious.totalBytes||0,
        currentFile:queuePrevious.detail||queuePrevious.stage||'',error:queuePrevious.error||null};
    } else if (cancellingOperation?.pending) {
      active = { ...cancellingOperation.operation, pausing: true, cancelling: true };
      progress = { ...(snapshot.progress || {}), modelId: active.modelId, phase: 'cancelling', error: null, currentFile: 'Stopping installation and removing its owned partial files…' };
    } else if (job) {
      const total = Math.max(0, Number(job.totalBytes) || 0);
      const stage = String(job.stage || 'preparing').replaceAll('-', ' ');
      const verifying = /verif|loading.*model|test.*generation/.test(job.stage || '');
      active = { modelId: job.targetId, jobId: job.id, kind: 'runtime', pausing: job.status === 'cancelling' };
      progress = { modelId: job.targetId, operationId: job.id,
        phase: verifying ? 'verifying' : total > 0 ? 'downloading' : 'preparing',
        downloadedBytes: Math.min(total, Math.max(0, Number(job.downloadedBytes) || 0)), totalBytes: total,
        currentFile: job.detail || stage, error: null };
    } else if (download) {
      active = { modelId: download.modelId, kind: 'download', pausing: download.phase === 'cancelling', cancelling: download.phase === 'cancelling' };
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
      const phase = status === 'ready' ? 'ready' : status === 'cancelled' || status === 'canceled' || /cancelled|canceled/i.test(error || '') ? cancelled.has(previous.modelId) || !paused.has(previous.modelId) && status === 'cancelled' ? 'cancelled' : 'paused' :
        status === 'failed' || error ? 'failed' : paused.has(previous.modelId) ? 'paused' : 'ready';
      progress = { ...(snapshot.progress || {}), modelId: previous.modelId, phase,
        operationId: terminalJob?.id || terminalDownload?.operationId || terminalDownload?.jobId || snapshot.progress?.operationId,
        currentFile: phase === 'paused' ? 'Paused. Completed files are kept for the next attempt.' : phase === 'cancelled' ? 'Installation cancelled. Completed and shared files were kept.' : terminalJob?.detail || terminalDownload?.currentFile || (phase === 'failed' ? 'Installation failed.' : 'Installation complete.'),
        error: phase === 'failed' ? error || 'Installation failed.' : null };
      starting = null;
    }
    const terminal = snapshot.terminal + (snapshot.active && !active ? 1 : 0);
    if (active) notifications.observe(progress);
    const key = JSON.stringify(runtime?.receipts || []);
    const readyKey = JSON.stringify(queue?.items?.filter(item=>item.phase==='ready').map(item=>item.id)||[]);
    const receiptsRevision = snapshot.receiptsRevision + (key !== receiptKey ? 1 : 0);
    const libraryChanged = terminal !== snapshot.terminal || (receiptKey && key !== receiptKey) || (queueReadyKey && readyKey !== queueReadyKey);
    queueReadyKey = readyKey;
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
      nativeInvoke('download_queue_action', {action:'snapshot'}),
    ]).then(([setup, download, queued]) => {
      // A failed status read cannot release an installation lock.
      const setupValid = setup.status === 'fulfilled' && Array.isArray(setup.value?.jobs) && Array.isArray(setup.value?.receipts);
      const downloadValid = download.status === 'fulfilled' && (download.value === null || typeof download.value?.phase === 'string');
      if (setupValid) runtime = setup.value;
      if (downloadValid) catalogueProgress = download.value;
      const queueValid = queued.status === 'fulfilled' && queued.value?.schema === 1 && Array.isArray(queued.value.items) && Array.isArray(queued.value.activeIds);
      const checked = queueValid || setupValid && downloadValid;
      publish({ initialized: checked, ...(queueValid ? {queue:queued.value} : {}) });
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
    if(command==='runtime_setup_start'&&args.options?.installWeights===true){
      args.options={...args.options,verifyInference:true};
    }
    if (starts.has(command)) {
      if (!snapshot.initialized) await refresh(true);
      if (!snapshot.initialized) throw new Error('Cannot check installation status. Retry when the current status is available.');
      if (snapshot.queue && command !== 'register_prepared_model') {
        const modelId = args.targetId || args.id || args.modelId;
        const queue = await nativeInvoke('download_queue_action', {action:'enqueue',items:[{modelId,label:args.label||modelId,command,args}]});
        notifications.begin(modelId);
        publish({queue}); reconcile(); ensurePolling();
        return queue;
      }
      if (snapshot.active || starting?.pending) throw new Error('Another model is installing. Pause it or wait for it to finish.');
      const modelId = args.targetId || args.id || args.modelId;
      notifications.begin(modelId);
      paused.delete(modelId);
      cancelled.delete(modelId);
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
    if (command === 'download_queue_action') {
      const result = await run();
      if(result?.schema===1&&Array.isArray(result.items)&&Array.isArray(result.activeIds)) {
        if(args.action==='enqueue')for(const item of args.items||[])notifications.begin(item.modelId);
        publish({queue:result,initialized:true});reconcile();ensurePolling();
      }
      return result;
    }
    if (command === 'runtime_setup_cancel' || command === 'cancel_model_install' || command === 'cancel_model_installation') {
      const discarding = command === 'cancel_model_installation';
      const current = snapshot.active || (snapshot.progress?.phase === 'paused' ? {modelId:snapshot.progress.modelId} : null);
      const modelId = current?.modelId;
      if (discarding && (!current || args.modelId !== modelId || args.operationId && args.operationId !== snapshot.progress?.operationId))
        throw Error('The active installation changed before cancellation. Refresh Models and retry.');
      if (discarding && cancellingOperation?.pending) throw Error('This installation is already stopping.');
      if (discarding) { cancelled.add(modelId); paused.delete(modelId); cancellingOperation = {pending:true,operation:{kind:'download',...current}}; }
      if (snapshot.active) {
        publish({ active: { ...snapshot.active, pausing: true, cancelling: discarding } });
      }
      if (discarding) reconcile();
      try {
        const result = await run();
        if (modelId && !discarding) paused.add(modelId);
        return result;
      } catch (error) { if (modelId) { paused.delete(modelId); cancelled.delete(modelId); } throw error; }
      finally { starting = null; if (discarding) cancellingOperation = null; await refresh(true); }
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
    if(operation.kind==='queue')return invoke('download_queue_action',{action:'pause',id:operation.jobId},()=>nativeInvoke('download_queue_action',{action:'pause',id:operation.jobId}));
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
  async function cancelInstallation(expected) {
    const queued = snapshot.queue?.items?.find(item=>expected?.operationId ? item.id===expected.operationId&&item.modelId===expected.modelId : item.id===snapshot.active?.jobId || !snapshot.active&&item.modelId===snapshot.progress?.modelId&&item.phase==='paused');
    if(queued){
      if(expected&&expected.modelId!==queued.modelId)throw Error('The current installation changed. Refresh its progress before cancelling.');
      const args={action:'remove',id:queued.id};return invoke('download_queue_action',args,()=>nativeInvoke('download_queue_action',args));
    }
    const pausedJob = snapshot.progress?.phase === 'paused' && runtime?.jobs?.find(job=>job.id===snapshot.progress.operationId&&job.targetId===snapshot.progress.modelId);
    const operation = snapshot.active || (snapshot.progress?.phase === 'paused' ? {modelId:snapshot.progress.modelId,...(pausedJob?{jobId:pausedJob.id,kind:'runtime'}:{kind:'download'})} : null);
    if(expected&&(expected.modelId!==operation?.modelId||expected.operationId&&expected.operationId!==snapshot.progress?.operationId))
      throw Error('The current installation changed. Refresh its progress before cancelling.');
    if (!operation || operation.pausing) return;
    if (operation.kind === 'starting') throw Error('Installation is starting. Cancel will be available in a moment.');
    const args = { modelId: operation.modelId, ...(snapshot.progress?.operationId ? {operationId:snapshot.progress.operationId} : {}), ...(operation.jobId ? {jobId:operation.jobId} : {}) };
    return invoke('cancel_model_installation', args, () => nativeInvoke('cancel_model_installation', args));
  }
  return {
    invoke, refresh, pause, cancel: cancelInstallation, getSnapshot: () => snapshot,
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
globalThis.__opencoreDownloadQueue = {action:args=>$installCoordinator.invoke('download_queue_action',args,()=>$hubInvoke('download_queue_action',args,()=>window.__TAURI_INTERNALS__.invoke('download_queue_action',args)))};
function $useInstallState() {
  return p.useSyncExternalStore($installCoordinator.subscribe, $installCoordinator.getSnapshot, $installCoordinator.getSnapshot);
}
