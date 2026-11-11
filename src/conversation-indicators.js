export function createConversationIndicators(storage, visible) {
  const key = 'opencore.conversation-read.v1';
  if (storage === undefined) {
    try { storage = globalThis.localStorage; } catch { storage = null; }
  }
  visible ||= () => !globalThis.document?.hidden;
  let seen = {};
  try {
    const saved = JSON.parse(storage?.getItem(key) || '{}');
    if (saved && !Array.isArray(saved) && typeof saved === 'object') {
      seen = Object.fromEntries(Object.entries(saved).filter(([id, value]) => id.length <= 512 && typeof value === 'string').slice(-5000));
    }
  } catch { /* Retain read state for this session if storage is unavailable. */ }
  const rows = new Map(), listeners = new Set();
  let selected;
  const complete = row => /^(complete|completed|stop|finished|done|success|succeeded)$/i.test(row.status || '');
  const revision = row => String(row.messageCount ?? row.updatedAt ?? '') + ':' + Number(complete(row));
  function remember(items) { for (const row of items) rows.set(row.id, row); }
  function stateFor(row) {
    if (row.indicatorActive === true) return 'working';
    return complete(row) && seen[row.id] !== revision(row) ? 'unread' : 'idle';
  }
  function observe(items, current) { remember(items); selected = current; }
  function trackLoad(id, pending) {
    const requested = rows.get(id), expected = requested && revision(requested);
    return Promise.resolve(pending).then(entries => {
      const current = rows.get(id);
      const loaded = Array.isArray(entries) && (entries.some(entry => entry.conversationId === id) || !entries.length && current?.messageCount === 0);
      if (loaded && current && expected !== undefined && revision(current) === expected && selected === id && !current.indicatorActive && visible() && seen[id] !== expected) {
        seen[id] = expected;
        seen = Object.fromEntries(Object.entries(seen).slice(-5000));
        try { storage?.setItem(key, JSON.stringify(seen)); } catch { /* Session-only acknowledgement. */ }
        for (const listener of listeners) listener();
      }
      return entries;
    });
  }
  function subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; }
  function useRows(React, items, current) {
    React.useEffect(() => { observe(items, current); return () => { if (selected === current) selected = undefined; }; }, [items, current]);
  }
  function useRevision(React, items) {
    const [, refresh] = React.useState(0);
    React.useEffect(() => subscribe(() => refresh(value => value + 1)), []);
    React.useEffect(() => { remember(items); }, [items]);
  }
  const labelFor = row => ({ working: 'Working', unread: 'Finished, unread', idle: 'Idle' })[stateFor(row)];
  return { observe, stateFor, labelFor, trackLoad, subscribe, useRows, useRevision };
}
