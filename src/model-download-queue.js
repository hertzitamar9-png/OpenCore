// The same queue controls are used by the source UI and the packaged UI.
export function createDownloadQueueStore(invoke) {
  let state = { available: false, mode: 'sequential', paused: false, items: [], activeIds: [], error: '' };
  const listeners = new Set(); let poll = null, pending = null, revision = 0;
  const publish = value => { state = { ...state, ...value }; for (const listener of listeners) listener(); };
  const accept = value => {
    if (!value || value.schema !== 1 || !Array.isArray(value.items)) return;
    const next = { available: true, mode: value.mode === 'parallel' ? 'parallel' : 'sequential', paused: !!value.paused, items: value.items.filter(item => item.phase !== 'cancelled'), activeIds: value.activeIds || [] };
    if (JSON.stringify(next) !== JSON.stringify({ available: state.available, mode: state.mode, paused: state.paused, items: state.items, activeIds: state.activeIds })) publish(next);
  };
  async function refresh() {
    if (pending) return pending;
    const started = revision;
    pending = Promise.resolve().then(() => invoke({ action: 'snapshot' }))
      .then(value => { if (started === revision) accept(value); })
      .catch(() => {}).finally(() => { pending = null; });
    return pending;
  }
  async function action(args) {
    try { const value = await invoke(args); revision++; accept(value); publish({ error: '' }); return value; }
    catch (error) { publish({ error: String(error?.message || error) }); throw error; }
  }
  return {
    getSnapshot: () => state,
    subscribe(listener) { listeners.add(listener); if (!poll) { refresh(); poll = setInterval(refresh, 1000); } return () => { listeners.delete(listener); if (!listeners.size) { clearInterval(poll); poll = null; } }; },
    action, refresh,
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
  function Item({ item }) {
    return h('div', { className: 'download-queue-item', style: { minWidth: 0, padding: '8px 0', borderBottom: '1px solid var(--theme-line,#344653)' } },
      active(item) && Progress ? h(Progress, { progress: progress(item), label: item.label || item.modelId, onPause: () => store.action({ action: 'pause', id: item.id }), onCancel: () => store.action({ action: 'remove', id: item.id }) }) :
        h('div', null, h('strong', null, item.label || item.modelId), h('small', { style: { display: 'block' } }, item.phase === 'queued' ? 'Queued · position ' + item.position : item.phase === 'ready' ? 'Complete' : item.phase), item.error ? h('p', { role: 'alert' }, item.error) : null),
      !active(item) ? h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 8 } },
        item.phase === 'paused' || item.phase === 'failed' ? button('Resume download', { action: 'resume', id: item.id }) : null,
        queued(item) && item.phase !== 'paused' ? button('Pause download', { action: 'pause', id: item.id }) : null,
        button(queued(item) ? 'Remove from queue' : 'Remove', { action: 'remove', id: item.id })) : null);
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
    const state = useQueue(), [limit, setLimit] = React.useState(20);
    if (!state.available) return null;
    const unfinished = state.items.filter(item => active(item) || queued(item)), activeCount = state.items.filter(active).length;
    return h('section', { className: 'download-queue-panel', 'aria-label': 'Model download queue', style: { padding: 12, margin: '12px 0', border: '1px solid var(--theme-line,#344653)', borderRadius: 10, minWidth: 0 } },
      h('strong', null, 'Download queue'), h('p', { 'aria-live': 'polite' }, activeCount + ' active · ' + unfinished.filter(queued).length + ' waiting'),
      unfinished.length ? button(state.paused ? 'Resume queue' : 'Pause queue', { action: state.paused ? 'resume' : 'pause' }) : null,
      ...state.items.filter(item => active(item) && item.modelId !== legacyModelId).map(item => h(Item, { key: item.id, item })),
      h('details', null, h('summary', { style: { padding: '12px 0', cursor: 'pointer' } }, 'Queue details · ' + state.items.length + ' models'),
        ...state.items.slice(0, limit).map(item => h(Item, { key: item.id, item })),
        state.items.length > limit ? h('button', { type: 'button', onClick: () => setLimit(value => value + 20) }, 'Show 20 more') : null),
      state.error ? h('p', { role: 'alert' }, state.error) : null);
  }
  function CardProgress({ modelId, modelIds = [], legacyModelId }) {
    const state = useQueue();
    const jobs = state.items.filter(item => item.modelId !== legacyModelId &&
      (item.modelId === modelId || modelIds.includes(item.modelId)) && (active(item) || queued(item)));
    return jobs.length ? h(React.Fragment, null, ...jobs.map(item => h(Item, { key: item.id, item }))) : null;
  }
  return { store, useQueue, Panel, Settings, CardProgress };
}
