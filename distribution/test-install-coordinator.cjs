const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(require('path').join(__dirname, 'install-coordinator.js'), 'utf8').split('const $installCoordinator =')[0];
const {createInstallCoordinator: create, createInstallNotificationPolicy: createNotifications} = vm.runInNewContext(source + ';({createInstallCoordinator,createInstallNotificationPolicy});', { setInterval: () => 1, clearInterval() {}, Date, Set, Promise });
function fixture(notifications) {
  let status = { jobs: [], receipts: [], activeJobId: null }, progress = null, failedReads = false;
  let clock = 0;
  const coordinator = create(async command => {
    if (failedReads) throw new Error('Transient status failure');
    return command === 'runtime_setup_status' ? status : progress;
  }, () => clock, notifications);
  return { coordinator, setStatus(value) { status = value; }, setProgress(value) { progress = value; }, failReads() { failedReads = true; }, advance(value) { clock += value; } };
}

test('a start response error does not unlock a job native admission already created', async () => {
  const state = fixture(); await state.coordinator.refresh();
  await assert.rejects(state.coordinator.invoke('runtime_setup_start', { targetId: 'edge' }, async () => {
    state.setStatus({ activeJobId: 'job', jobs: [{ id: 'job', targetId: 'edge', status: 'running', stage: 'creating-environment' }], receipts: [] });
    throw new Error('IPC response interrupted');
  }));
  assert.equal(state.coordinator.getSnapshot().active?.modelId, 'edge');
});

test('failed status reads retain the lock for an active download', async () => {
  const state = fixture();
  state.setProgress({ modelId: 'gguf', phase: 'downloading', downloadedBytes: 3, totalBytes: 10 });
  await state.coordinator.refresh(); state.failReads(); await state.coordinator.refresh();
  assert.equal(state.coordinator.getSnapshot().active?.modelId, 'gguf');
});

test('unknown initial installation status cannot admit a new installation', async () => {
  const state = fixture(); state.failReads();
  let reached = 0;
  await assert.rejects(state.coordinator.invoke('install_model', { id: 'one' }, async () => reached++));
  assert.equal(reached, 0);
});

test('a second installation cannot reach native admission while the first start is pending', async () => {
  const state = fixture(); await state.coordinator.refresh();
  let release, reached = 0;
  const pending = state.coordinator.invoke('install_model', { id: 'one' }, () => new Promise(resolve => release = resolve));
  await assert.rejects(state.coordinator.invoke('runtime_setup_start', { targetId: 'two' }, async () => reached++));
  assert.equal(reached, 0);
  release(); await pending;
});

test('ready runtime jobs release admission and preserve terminal progress', async () => {
  const state = fixture();
  state.setStatus({ activeJobId: 'job', jobs: [{ id: 'job', targetId: 'edge', status: 'running', stage: 'downloading', totalBytes: 10, downloadedBytes: 4 }], receipts: [] });
  await state.coordinator.refresh();
  state.setStatus({ activeJobId: null, jobs: [{ id: 'job', targetId: 'edge', status: 'ready', stage: 'ready' }], receipts: [] });
  await state.coordinator.refresh();
  assert.equal(state.coordinator.getSnapshot().active, null);
  assert.equal(state.coordinator.getSnapshot().progress.phase, 'ready');
});

test('slow successful admission stays locked when subsequent status reads fail', async () => {
  const state = fixture(); await state.coordinator.refresh();
  await state.coordinator.invoke('install_model', { id: 'slow' }, async () => {
    state.advance(3000); state.failReads();
  });
  assert.equal(state.coordinator.getSnapshot().active?.modelId, 'slow');
  await assert.rejects(state.coordinator.invoke('install_model', { id: 'second' }, async () => {}));
});

test('ordinary download failure remains a failure with its actual error', async () => {
  const state = fixture();
  state.setProgress({ modelId: 'gguf', phase: 'downloading', downloadedBytes: 3, totalBytes: 10 });
  await state.coordinator.refresh();
  state.setProgress({ modelId: 'gguf', phase: 'failed', downloadedBytes: 3, totalBytes: 10, error: 'Disk full' });
  await state.coordinator.refresh();
  assert.equal(state.coordinator.getSnapshot().progress.phase, 'failed');
  assert.equal(state.coordinator.getSnapshot().progress.error, 'Disk full');
});

test('a rejected pause cannot replace a later successful native result', async () => {
  const state = fixture();
  state.setStatus({ activeJobId: 'job', jobs: [{ id: 'job', targetId: 'edge', status: 'running', stage: 'verifying' }], receipts: [] });
  await state.coordinator.refresh();
  await assert.rejects(state.coordinator.invoke('runtime_setup_cancel', { jobId: 'job' }, async () => { throw new Error('Cancel rejected'); }));
  state.setStatus({ activeJobId: null, jobs: [{ id: 'job', targetId: 'edge', status: 'ready', stage: 'ready' }], receipts: [] });
  await state.coordinator.refresh();
  assert.equal(state.coordinator.getSnapshot().progress.phase, 'ready');
});

test('dismiss clears the matching shared terminal notification', async () => {
  const state = fixture();
  state.setProgress({ modelId: 'gguf', phase: 'downloading', downloadedBytes: 3, totalBytes: 10 });
  await state.coordinator.refresh(); state.setProgress(null); await state.coordinator.refresh();
  const expected = state.coordinator.getSnapshot().progress;
  await state.coordinator.invoke('dismiss_model_install_progress', { expected }, async () => {});
  assert.equal(state.coordinator.getSnapshot().progress, null);
});

test('a delayed dismissal cannot clear a newer active installation', async () => {
  const state = fixture();
  state.setProgress({ modelId: 'one', phase: 'downloading', downloadedBytes: 3, totalBytes: 10 });
  await state.coordinator.refresh(); state.setProgress(null); await state.coordinator.refresh();
  let release;
  const dismiss = state.coordinator.invoke('dismiss_model_install_progress', { expected: state.coordinator.getSnapshot().progress }, () => new Promise(resolve => release = resolve));
  state.setProgress({ modelId: 'two', phase: 'downloading', downloadedBytes: 1, totalBytes: 10 });
  await state.coordinator.refresh(); release(); await dismiss;
  assert.equal(state.coordinator.getSnapshot().progress.modelId, 'two');
  assert.equal(state.coordinator.getSnapshot().active.modelId, 'two');
});

function notificationStorage() {
  const records = new Map(); let scope = 'account-a';
  return {records, setScope: value => { scope = value; }, policy: () => createNotifications({scope: () => scope,
    read: key => records.get(key), write: (key, value) => records.set(key, value)})};
}
const completed = {modelId: 'phonon-2', phase: 'ready', totalBytes: 100, downloadedBytes: 100, currentFile: 'Ready', error: null};
async function library(state, progress) {
  return state.coordinator.invoke('list_model_library', {}, async () => ({models: [{id: 'phonon-2'}], progress}));
}
const visible = state => state.coordinator.visibleProgress(state.coordinator.getSnapshot().progress, state.coordinator.getSnapshot().library?.progress);

test('dismissed legacy completion stays hidden through repeated polls and cached library refreshes', async () => {
  const store = notificationStorage(), state = fixture(store.policy());
  state.setProgress(completed); await state.coordinator.refresh(); await library(state, completed);
  assert.equal(visible(state).modelId, 'phonon-2');
  await state.coordinator.invoke('dismiss_model_install_progress', {expected: completed}, async () => false);
  for (let index = 0; index < 4; index++) {
    await state.coordinator.refresh(); await library(state, {...completed, currentFile: 'Rechecked ready ' + index});
    assert.equal(visible(state), null);
  }
});

test('a recreated coordinator restores the account acknowledgement without storing progress details', async () => {
  const store = notificationStorage(), first = fixture(store.policy()); await library(first, completed);
  await first.coordinator.invoke('dismiss_model_install_progress', {expected: completed}, async () => {});
  const second = fixture(store.policy()); second.setProgress(completed);
  await second.coordinator.refresh(); await library(second, completed);
  assert.equal(visible(second), null);
  const saved = JSON.parse([...store.records.values()][0]);
  assert.deepEqual(saved, {schema: 1, items: [{modelId: 'phonon-2', operationId: null}]});
});

test('a new installation of the same model clears its previous acknowledgement', async () => {
  const state = fixture(); await state.coordinator.refresh(); await library(state, completed);
  await state.coordinator.invoke('dismiss_model_install_progress', {expected: completed}, async () => {});
  await state.coordinator.invoke('install_model', {id: 'phonon-2'}, async () => {
    state.setProgress({...completed, phase: 'downloading', downloadedBytes: 20, operationId: 'new-download'});
  });
  assert.equal(visible(state).phase, 'downloading');
  state.advance(3000); state.setProgress({...completed, operationId: 'new-download'}); await state.coordinator.refresh();
  assert.equal(visible(state).phase, 'ready');
});

test('stale activity for an acknowledged operation does not erase its acknowledgement', async () => {
  const state = fixture(), old = {...completed, operationId: 'previous'};
  await state.coordinator.refresh(); await library(state, old);
  await state.coordinator.invoke('dismiss_model_install_progress', {expected: old}, async () => {});
  state.setProgress({...old, phase: 'downloading'}); await state.coordinator.refresh();
  assert.equal(visible(state).phase, 'downloading');
  state.setProgress(old); await state.coordinator.refresh();
  assert.equal(visible(state), null);
});

test('a new externally observed operation remains visible after the old completion was dismissed', async () => {
  const state = fixture(), old = {...completed, operationId: 'previous'};
  await state.coordinator.refresh(); await library(state, old);
  await state.coordinator.invoke('dismiss_model_install_progress', {expected: old}, async () => {});
  state.setProgress({...completed, phase: 'downloading', operationId: 'later'}); await state.coordinator.refresh();
  state.setProgress({...completed, operationId: 'later'}); await state.coordinator.refresh();
  assert.equal(visible(state).operationId, 'later');
});

test('native cleanup failure cannot resurrect a locally acknowledged completion', async () => {
  const state = fixture(); await library(state, completed);
  assert.equal(await state.coordinator.invoke('dismiss_model_install_progress', {expected: completed}, async () => { throw Error('IPC unavailable'); }), false);
  await library(state, completed); assert.equal(visible(state), null);
});

test('a dismissal cannot hide active work or a different newer completion', async () => {
  const state = fixture(); state.setProgress({...completed, phase: 'downloading', operationId: 'active'});
  await state.coordinator.refresh(); let calls = 0;
  await state.coordinator.invoke('dismiss_model_install_progress', {expected: completed}, async () => { calls++; });
  assert.equal(calls, 0); assert.equal(visible(state).phase, 'downloading');
  state.setProgress({...completed, operationId: 'newer'}); await state.coordinator.refresh();
  await state.coordinator.invoke('dismiss_model_install_progress', {expected: completed}, async () => { calls++; });
  assert.equal(calls, 0); assert.equal(visible(state).operationId, 'newer');
});

test('acknowledgements are isolated between account scopes', async () => {
  const store = notificationStorage(), state = fixture(store.policy()); await library(state, completed);
  await state.coordinator.invoke('dismiss_model_install_progress', {expected: completed}, async () => {});
  store.setScope('account-b'); assert.equal(visible(state).modelId, 'phonon-2');
  store.setScope('account-a'); assert.equal(visible(state), null);
});

test('runtime and native catalog identities of one completed install stay acknowledged after reload', async () => {
  const store = notificationStorage(), state = fixture(store.policy());
  const native = {...completed, operationId: 'native-download'};
  state.setStatus({activeJobId: 'runtime-job', jobs: [{id: 'runtime-job', targetId: 'phonon-2', status: 'running', stage: 'verifying'}], receipts: []});
  state.setProgress(native); await state.coordinator.refresh(); await library(state, native);
  state.setStatus({activeJobId: null, jobs: [{id: 'runtime-job', targetId: 'phonon-2', status: 'ready', stage: 'ready'}], receipts: []});
  await state.coordinator.refresh(); const expected = state.coordinator.getSnapshot().progress;
  assert.equal(expected.operationId, 'runtime-job');
  await state.coordinator.invoke('dismiss_model_install_progress', {expected}, async () => false);
  assert.equal(visible(state), null);
  const reloaded = fixture(store.policy()); await library(reloaded, native);
  assert.equal(visible(reloaded), null);
  await reloaded.coordinator.invoke('install_model', {id:'phonon-2'}, async()=>{});
  reloaded.advance(3000);reloaded.setProgress({...native,operationId:'another-install'});await reloaded.coordinator.refresh();
  await library(reloaded, {...native, operationId: 'another-install'});
  assert.equal(visible(reloaded).operationId, 'another-install');
});

test('a Phonon startup validation with a new worker ID does not revive its dismissed success', async () => {
  const store=notificationStorage(),state=fixture(store.policy()),old={...completed,operationId:'old-install'};
  await library(state,old);await state.coordinator.invoke('dismiss_model_install_progress',{expected:old},async()=>{});
  const next=fixture(store.policy());
  next.setProgress({...completed,phase:'preparing',operationId:'startup-check'});await next.coordinator.refresh();
  assert.equal(visible(next).phase,'preparing');
  next.setProgress({...completed,operationId:'startup-check'});await next.coordinator.refresh();
  assert.equal(visible(next),null);
});
