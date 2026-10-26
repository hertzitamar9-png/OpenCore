const test = require('node:test');
const assert = require('node:assert/strict');
const {createModelLibraryCache} = require('./model-view-cache.js');
const library = (id, progress = null) => ({models: [{id, label: id}], progress, diskFreeBytes: 10e9, minimumFreeBytes: 0});
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture(version = '1.0.0') {
  const records = new Map(), changes = [];
  const storage = {read: async key => records.get(key), write: async (key, value) => records.set(key, structuredClone(value))};
  const create = current => createModelLibraryCache(current || version, storage, value => changes.push(value));
  return {records, changes, create, cache: create()};
}
test('concurrent initial reads and tab revisits share one completed model library', async () => {
  const {cache} = fixture(); const gate = deferred(); let reads = 0;
  const read = () => { reads++; return gate.promise; };
  const first = cache.read(read), second = cache.read(read);
  await tick(); assert.equal(reads, 1);
  gate.resolve(library('original'));
  await Promise.all([first, second]);
  assert.equal((await cache.read(read)).models[0].id, 'original'); assert.equal(reads, 1);
});
test('a new launch shows saved cards while exactly one background refresh is pending', async () => {
  const f = fixture(); f.cache.setScope('account-a');
  await f.cache.read(async () => library('saved', {phase: 'downloading'}));
  const next = f.create(); next.setScope('account-a'); await next.hydrate();
  assert.equal(next.peek().models[0].id, 'saved'); assert.equal(next.peek().progress, null);
  const gate = deferred(); let reads = 0; const read = () => { reads++; return gate.promise; };
  assert.equal((await next.read(read)).models[0].id, 'saved');
  await next.read(read); assert.equal(reads, 1);
  gate.resolve(library('new-model')); await tick();
  assert.equal(next.peek().models[0].id, 'new-model');
});
test('new catalog builds and different accounts cannot hydrate the previous snapshot', async () => {
  const f = fixture(); f.cache.setScope('account-a'); await f.cache.read(async () => library('private-workspace'));
  const updated = f.create('1.0.1'); updated.setScope('account-a'); await updated.hydrate(); assert.equal(updated.peek(), null);
  const other = f.create(); other.setScope('account-b'); await other.hydrate(); assert.equal(other.peek(), null);
});
test('invalidation during a slow read waits for a subsequent authoritative result', async () => {
  const f = fixture(); const old = deferred(); let reads = 0;
  const result = f.cache.read(() => ++reads === 1 ? old.promise : Promise.resolve(library('after-install')));
  await tick(); f.cache.invalidate(); old.resolve(library('before-install'));
  assert.equal((await result).models[0].id, 'after-install'); assert.equal(reads, 2);
  assert.equal(f.changes.some(value => value.models[0].id === 'before-install'), false);
});
test('account switching ignores an outstanding response from the previous account', async () => {
  const {cache} = fixture(); cache.setScope('account-a'); const old = deferred();
  const first = cache.read(() => old.promise); await tick();
  cache.setScope('account-b'); await cache.read(async () => library('b'));
  old.resolve(library('a')); await first; assert.equal(cache.peek().models[0].id, 'b');
});
test('storage failure cannot block the initial model library or erase loaded cards', async () => {
  const cache = createModelLibraryCache('1.0.0', {read: async () => { throw Error('blocked'); }, write: async () => { throw Error('quota'); }});
  cache.setScope('a'); await cache.read(async () => library('live'));
  await assert.rejects(cache.read(async () => { throw Error('offline'); }, {fresh: true}));
  assert.equal(cache.peek().models[0].id, 'live');
});
test('an explicit refresh replaces installed state and removed models without dropping current cards first', async () => {
  const {cache} = fixture(); await cache.read(async () => library('old'));
  const gate = deferred(); const update = cache.read(() => gate.promise, {fresh: true});
  assert.equal(cache.peek().models[0].id, 'old');
  gate.resolve(library('replacement')); await update;
  assert.equal(cache.peek().models[0].id, 'replacement');
});
