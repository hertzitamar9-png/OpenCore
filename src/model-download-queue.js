// The same queue controls are used by the source UI and the packaged UI.
export function createDownloadQueueStore(invoke) {
  let state = { available: false, mode: 'sequential', paused: false, items: [], activeIds: [], selected: [], error: '' };
  const listeners = new Set(), selected = new Map(); let poll = null, pending = null;
  const publish = value => { state = { ...state, ...value, selected: [...selected.values()] }; for (const listener of listeners) listener(); };
  const accept = value => {
    if (!value || value.schema !== 1 || !Array.isArray(value.items)) return;
    const next = { available: true, mode: value.mode === 'parallel' ? 'parallel' : 'sequential', paused: !!value.paused, items: value.items, activeIds: value.activeIds || [] };
    if (JSON.stringify(next) !== JSON.stringify({ available: state.available, mode: state.mode, paused: state.paused, items: state.items, activeIds: state.activeIds })) publish(next);
  };
  async function refresh() {
    if (pending) return pending;
    pending = Promise.resolve().then(() => invoke({ action: 'snapshot' })).then(accept).catch(() => {}).finally(() => { pending = null; }); return pending;
  }
  async function action(args) {
    try { const value = await invoke(args); accept(value); publish({ error: '' }); return value; }
    catch (error) { publish({ error: String(error?.message || error) }); throw error; }
  }
  return {
    getSnapshot: () => state,
    subscribe(listener) { listeners.add(listener); if (!poll) { refresh(); poll = setInterval(refresh, 1000); } return () => { listeners.delete(listener); if (!listeners.size) { clearInterval(poll); poll = null; } }; },
    select(key, item) { if (item) selected.set(key, { ...item, selectionKey: key }); else selected.delete(key); publish({}); },
    update(key, item) { if (selected.has(key) && JSON.stringify(selected.get(key)) !== JSON.stringify({ ...item, selectionKey: key })) { selected.set(key, { ...item, selectionKey: key }); publish({}); } },
    clear() { selected.clear(); publish({}); },
    async enqueueSelected() {
      const captured = [...selected.values()];
      for (let at = 0; at < captured.length; at += 128) {
        const batch = captured.slice(at, at + 128);
        await action({ action: 'enqueue', items: batch.map(({ selectionKey, ...item }) => item) });
        for (const item of batch) if (selected.get(item.selectionKey)?.modelId === item.modelId) selected.delete(item.selectionKey);
        publish({});
      }
    }, action, refresh,
  };
}

export function createDownloadQueueUI(React, Progress, invoke) {
  const h = React.createElement;
  const send = invoke || (args => {
    if (globalThis.__opencoreDownloadQueue?.action) return globalThis.__opencoreDownloadQueue.action(args);
    const native = () => globalThis.__TAURI_INTERNALS__?.invoke('download_queue_action', args) || Promise.reject(Error('Download queue is not available yet.'));
    return globalThis.__opencoreInstallBridge?.invoke('download_queue_action', args, native) || native();
  });
  const store = createDownloadQueueStore(send);
  const useQueue = () => React.useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const safely = promise => promise.catch(() => {});
  const active = item => ['starting', 'downloading', 'preparing', 'verifying', 'cancelling'].includes(item.phase);
  const queued = item => item.phase === 'queued' || item.phase === 'paused';
  const button = (label, action, disabled = false) => h('button', { type: 'button', disabled, style: { minHeight: 44 }, onClick: () => safely(store.action(action)) }, label);
  const progress = item => ({ ...item, operationId: item.id, phase: item.phase === 'starting' ? 'preparing' : item.phase, currentFile: item.currentFile || item.detail || item.label });
  function Item({ item, compact = false }) {
    return h('div', { className: 'download-queue-item', style: { minWidth: 0, padding: '8px 0', borderBottom: '1px solid var(--theme-line,#344653)' } },
      active(item) && Progress ? h(Progress, { progress: progress(item), label: item.label || item.modelId, onPause: () => store.action({ action: 'pause', id: item.id }), onCancel: () => store.action({ action: 'remove', id: item.id }) }) :
        h('div', null, h('strong', null, item.label || item.modelId), h('small', { style: { display: 'block' } }, item.phase === 'queued' ? 'Queued · position ' + item.position : item.phase === 'ready' ? 'Complete' : item.phase), item.error ? h('p', { role: 'alert' }, item.error) : null),
      !active(item) ? h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 8 } },
        item.phase === 'paused' || item.phase === 'failed' ? button('Resume download', { action: 'resume', id: item.id }) : null,
        queued(item) && item.phase !== 'paused' ? button('Pause download', { action: 'pause', id: item.id }) : null,
        compact && item.phase === 'ready' ? null : button(queued(item) ? 'Remove from queue' : 'Remove', { action: 'remove', id: item.id })) : null);
  }
  function Settings() {
    const state = useQueue();
    return !state.available ? null : h('section', { className: 'download-queue-settings', 'aria-label': 'Model download settings', style: { padding: '12px 0' } },
      h('h3', null, 'Model downloads'), h('p', null, 'Choose how queued models download. You can add more at any time.'),
      h('label', null, 'Download mode ', h('select', { 'aria-label': 'Model download mode', value: state.mode, style: { minHeight: 44 }, onChange: event => safely(store.action({ action: 'mode', mode: event.target.value })) },
        h('option', { value: 'sequential' }, 'One at a time · in queue order'), h('option', { value: 'parallel' }, 'All at once · parallel downloads'))),
      h('p', null, 'Parallel downloads share network bandwidth and connections. Runtime preparation runs separately.'),
      h('p', null, globalThis.__OPENCORE_PHONE__ ? 'Downloads continue when you switch apps. The Android download notification has Pause and Cancel controls.' : 'Downloads continue while OpenCore is minimized. Your queue is saved on this PC.'),
      state.error ? h('p', { role: 'alert' }, state.error) : null);
  }
  function Panel({ legacyModelId } = {}) {
    const state = useQueue(), [limit, setLimit] = React.useState(20), [busy, setBusy] = React.useState(false);
    if (!state.available) return null;
    const unfinished = state.items.filter(item => active(item) || queued(item)), activeCount = state.items.filter(active).length;
    async function download() { setBusy(true); try { await store.enqueueSelected(); } catch {} finally { setBusy(false); } }
    return h('section', { className: 'download-queue-panel', 'aria-label': 'Model download queue', style: { padding: 12, margin: '12px 0', border: '1px solid var(--theme-line,#344653)', borderRadius: 10, minWidth: 0 } },
      h('strong', null, 'Download queue'), h('p', { 'aria-live': 'polite' }, activeCount + ' active · ' + unfinished.filter(queued).length + ' waiting · ' + state.selected.length + ' selected'),
      h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 8 } },
        h('button', { type: 'button', style: { minHeight: 44 }, disabled: busy || !state.selected.length, onClick: download }, busy ? 'Adding to queue…' : 'Download selected (' + state.selected.length + ')'),
        h('button', { type: 'button', style: { minHeight: 44 }, disabled: !state.selected.length, onClick: () => store.clear() }, 'Clear selection'),
        unfinished.length ? button(state.paused ? 'Resume queue' : 'Pause queue', { action: state.paused ? 'resume' : 'pause' }) : null),
      ...state.items.filter(item => active(item) && item.modelId !== legacyModelId).map(item => h(Item, { key: item.id, item })),
      h('details', null, h('summary', { style: { padding: '12px 0', cursor: 'pointer' } }, 'Queue details · ' + state.items.length + ' models'),
        ...state.items.slice(0, limit).map(item => h(Item, { key: item.id, item })),
        state.items.length > limit ? h('button', { type: 'button', onClick: () => setLimit(value => value + 20) }, 'Show 20 more') : null),
      state.error ? h('p', { role: 'alert' }, state.error) : null);
  }
  function Checkbox({ groupId, modelId, label, installed = false, installable = true, managedInstallation = false, modelIds = [], legacyModelId }) {
    const state = useQueue(), item = { modelId, label, command: managedInstallation ? 'runtime_setup_start' : 'install_model', args: managedInstallation ? { targetId: modelId, options: { installWeights: true, verifyInference: true } } : { id: modelId } };
    React.useEffect(() => { if (installed || !installable) { if (state.selected.some(value => value.selectionKey === groupId)) store.select(groupId, null); } else store.update(groupId, item); }, [groupId, modelId, label, installed, installable, managedInstallation]);
    if (!state.available) return null;
    const jobs = state.items.filter(value => (value.modelId === modelId || modelIds.includes(value.modelId)) && (active(value) || queued(value)));
    return h('div', { className: 'model-download-selection', style: { minWidth: 0 } },
      h('label', { style: { display: 'flex', alignItems: 'center', gap: 8, minHeight: 44, cursor: installed || !installable ? 'default' : 'pointer' } },
        h('input', { type: 'checkbox', 'aria-label': 'Select download for ' + label, checked: state.selected.some(value => value.selectionKey === groupId), disabled: installed || !installable, style: { width: 20, height: 20, flex: 'none', accentColor: 'var(--theme-accent,#6dacf5)' }, onChange: event => store.select(groupId, event.target.checked ? item : null) }), installed ? 'Installed' : 'Select for download'),
      ...jobs.filter(value => value.modelId !== legacyModelId).map(value => h(Item, { key: value.id, item: value, compact: true })));
  }
  return { store, useQueue, Panel, Settings, Checkbox };
}
