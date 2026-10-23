const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(require('path').join(__dirname, 'install-coordinator.js'), 'utf8').split('const $installCoordinator =')[0];
const create = vm.runInNewContext(source + ';createInstallCoordinator;', { setInterval: () => 1, clearInterval() {}, Date, Set, Promise });
function fixture() {
  let status = { jobs: [], receipts: [], activeJobId: null }, progress = null, failedReads = false;
  let clock = 0;
  const coordinator = create(async command => {
    if (failedReads) throw new Error('Transient status failure');
    return command === 'runtime_setup_status' ? status : progress;
  }, () => clock);
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
