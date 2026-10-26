/* Device-local model metadata only. Never cache account tokens or conversation data. */
function createModelLibraryCache(version, storage, changed = () => {}) {
  let scope = '', generation = 0, revision = 0, value = null, pending = null;
  let hydration = null, reader = null, verified = false;
  const valid = data => data && Array.isArray(data.models) && data.models.every(model => typeof model.id === 'string');
  const same = (left, right) => left === right || JSON.stringify(left) === JSON.stringify(right);
  function save(data) {
    if (scope) void Promise.resolve(storage.write(scope, {schema: 1, version, library: {...data, progress: null}})).catch(() => {});
  }
  function accept(data) {
    if (!valid(data) || same(value, data)) return;
    value = data;
    save(data);
    changed(data);
  }
  function setScope(next) {
    if (scope === next) return;
    scope = next; generation++; revision++;
    value = null; pending = null; hydration = null; reader = null; verified = false;
  }
  function hydrate() {
    if (!scope || value) return Promise.resolve(value);
    if (!hydration) {
      const ticket = generation, startingRevision = revision;
      hydration = Promise.resolve().then(() => storage.read(scope)).then(record => {
        if (ticket === generation && startingRevision === revision && !value && record?.schema === 1 && record.version === version && valid(record.library)) {
          value = {...record.library, progress: null};
        }
        return value;
      }).catch(() => value);
    }
    return hydration;
  }
  function refresh() {
    if (pending) return pending;
    if (!reader) return Promise.resolve(value);
    const ticket = generation;
    const request = (async () => {
      while (ticket === generation) {
        const startingRevision = revision, read = reader;
        const result = await read();
        if (ticket !== generation) return result;
        if (startingRevision !== revision) continue;
        if (!valid(result)) throw new Error('The model library returned an incomplete catalog.');
        verified = true;
        accept(result);
        return result;
      }
      return value;
    })().finally(() => { if (pending === request) pending = null; });
    pending = request;
    return request;
  }
  async function read(read, options = {}) {
    reader = read;
    if (options.fresh) { revision++; verified = false; return refresh(); }
    if (!value) await hydrate();
    if (value) {
      if (!verified) void refresh().catch(() => {});
      return value;
    }
    return refresh();
  }
  function invalidate() {
    revision++; verified = false;
    if (reader) void refresh().catch(() => {});
  }
  return {setScope, getScope: () => scope, hydrate, read, accept, invalidate, peek: () => value};
}

function createModelCacheStorage() {
  // Each account gets one replaceable metadata snapshot. Older app schemas and
  // catalogs are ignored; broken or restricted storage falls back to memory.
  function transaction(scope, record) {
    return new Promise(resolve => {
      if (!globalThis.indexedDB) return resolve(null);
      let database, finished = false;
      const finish = value => {
        if (finished) return;
        finished = true; clearTimeout(timer); database?.close(); resolve(value);
      };
      const timer = setTimeout(() => finish(null), 1500);
      let open;
      try { open = indexedDB.open('opencore-model-metadata-v1', 1); } catch { return finish(null); }
      open.onupgradeneeded = () => { open.result.createObjectStore('libraries'); };
      open.onerror = () => finish(null);
      open.onblocked = () => finish(null);
      open.onsuccess = () => {
        database = open.result;
        if (finished) { database.close(); return; }
        try {
          const tx = database.transaction('libraries', record ? 'readwrite' : 'readonly');
          const store = tx.objectStore('libraries');
          const request = record ? store.put(record, scope) : store.get(scope);
          let result = null;
          request.onsuccess = () => { result = record || request.result || null; };
          tx.oncomplete = () => finish(result);
          tx.onerror = () => finish(null);
          tx.onabort = () => finish(null);
        } catch { finish(null); }
      };
    });
  }
  return {read: scope => transaction(scope), write: (scope, record) => transaction(scope, record)};
}

if (typeof window !== 'undefined') {
  const cache = createModelLibraryCache($viewCacheVersion, createModelCacheStorage(), library => {
    window.dispatchEvent(new CustomEvent('opencore-model-library-changed', {detail: library}));
  });
  globalThis.__opencoreModelLibrary = cache;
  window.addEventListener('opencore-model-library-changed', event => cache.accept(event.detail));
  window.addEventListener('opencore-model-library-invalidated', () => cache.invalidate());
  window.addEventListener('focus', () => cache.invalidate());
}
if (typeof module !== 'undefined' && module.exports) module.exports = {createModelLibraryCache};
