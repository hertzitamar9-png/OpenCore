// Exercise the same Tauri commands as the Uninstall dialog on a fresh test home.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { validateNativeFamilies, nativeCatalogOffsets, nativeCatalogSlotBytes } = require('./native-catalog.cjs');

// Windows CI can verify the exact executable it installed without relying
// on remote debugging being available in the runner's WebView2 policy.
function inspectExecutable(executable) {
  const data = fs.readFileSync(executable);
  const slots = nativeCatalogOffsets.map(start => data.subarray(start, start + nativeCatalogSlotBytes));
  assert.ok(slots.every(slot => slot.length === nativeCatalogSlotBytes && slot.equals(slots[0])),
    'All embedded model catalogs must match');
  const { models } = JSON.parse(slots[0].toString('utf8'));
  assert.ok(models.length > 0);
  validateNativeFamilies(models);
  for (const [root, alias] of [['nanbeige-bf16', 'nanbeige-bf16-echo'], ['dualcore-kv', 'dualcore-echo']]) {
    const familyRoot = models.find(model => model.id === root);
    assert.ok(familyRoot && !familyRoot.variantOf, root + ' must be the family root');
    assert.equal(models.find(model => model.id === alias)?.variantOf, root);
  }
  return { passed: true, catalogSlots: slots.length, profilesChecked: models.length };
}

async function checkModelRemoval(port, home) {
  const { chromium } = require(process.env.OPENCORE_PLAYWRIGHT || 'playwright');
  assert.equal(path.basename(home), 'home');
  assert.match(path.basename(path.dirname(home)), /^opencore-(bridge-policy|native-removal-qa)-/);
  const root = path.join(home, 'OpenCore');
  const id = 'qwen35-08b-q8';
  const receipt = 'models/receipts/model-' + id + '.json';
  const removable = [
    'models/library/qwen35-08b-q8/Qwen3.5-0.8B-Q8_0.gguf',
    'models/library/qwen35-08b-q8-q4-k-m/Qwen3.5-0.8B-Q4_K_M.gguf.partial',
    receipt,
  ];
  const protectedFiles = [
    'models/nanbeige/Nanbeige_Nanbeige4.2-3B-bf16.gguf',
    'speech/phonon-2/phonon-2.bps.tar.zst.partial',
    'echo/disposable-history.txt',
  ];
  const content = 'temporary model removal fixture';
  const absolute = file => path.join(root, file);
  const bindings = require('../src-tauri/resources/runtime-setup/hf-extension/native-model-bindings.json');
  const model = bindings.models.find(model => model.id === id);
  const native = bindings.models.find(row => row.id === model.modeProfiles.native);
  const contents = file => file === receipt ? JSON.stringify(model.artifacts) : content;
  for (const file of [...removable, ...protectedFiles]) {
    fs.mkdirSync(path.dirname(absolute(file)), { recursive: true });
    fs.writeFileSync(absolute(file), contents(file), { flag: 'wx' });
  }
  const comparable = file => path.normalize(file.replace(/^\\\\\?\\/, '')).toLowerCase();
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + port);
  try {
    const page = browser.contexts().flatMap(context => context.pages()).find(p => p.url().includes('tauri.localhost'));
    assert.ok(page, 'The packaged app WebView is available');
    await page.waitForFunction(() => typeof window.__TAURI_INTERNALS__?.invoke === 'function');
    let activationRequests = 0;
    // The previous mode hook rewrote this receipt on each activation.
    await page.exposeFunction('fixtureActivateNativeModel', profile => {
      activationRequests++;
      fs.utimesSync(absolute(receipt), 1700000000, 1700000000 + activationRequests);
      return { profileId: profile };
    });
    await page.evaluate(async models => {
      const { installModelModes } = await import('/assets/model-catalog-B8SfuDgQ.js');
      const catalog = { models };
      const invoke = (command, args) => window.__TAURI_INTERNALS__.invoke(command, args);
      const request = (route, body) => route === '/runtime/modes' ? Promise.resolve(catalog) :
        window.fixtureActivateNativeModel(models[0].modeProfiles[body.operatingMode]);
      window.removalTestModes = installModelModes({ createElement() {} }, catalog, { invoke, request, changed: async () => {} });
      await window.removalTestModes.ready;
    }, [model, native]);
    const invoke = (command, args) => page.evaluate(async ({ command, args }) => {
      try {
        await window.removalTestModes.before(command, args);
        return { value: await window.__TAURI_INTERNALS__.invoke(command, args) };
      } catch (error) {
        return { error: String(error) };
      }
    }, { command, args });
    const plan = async (id, scope) => {
      const response = await invoke('model_removal_plan', { id, scope });
      assert.ok(response.value, response.error);
      return response.value;
    };

    const reviewed = await plan(id);
    assert.equal(reviewed.scope, 'family');
    assert.deepEqual(reviewed.files.map(file => comparable(file.path)).sort(), removable.map(absolute).map(comparable).sort());
    assert.equal(reviewed.totalBytes, removable.reduce((sum, file) => sum + Buffer.byteLength(contents(file)), 0));
    const profile = await plan(id, 'profile');
    assert.equal(profile.scope, 'profile');
    assert.ok(profile.files.every(file => comparable(file.path) !== comparable(absolute(removable[1]))));
    for (const legacy of ['nanbeige-bf16', 'nanbeige-bf16-echo', 'dualcore-kv', 'dualcore-echo', 'phonon-2'])
      assert.equal((await plan(legacy, 'family')).scope, 'family');
    for (const file of [...removable, ...protectedFiles])
      assert.equal(fs.readFileSync(absolute(file), 'utf8'), contents(file));

    fs.appendFileSync(absolute(removable[0]), ' changed after review');
    const remove = confirmationToken => invoke('uninstall_model', { id, scope: reviewed.scope, confirmationToken });
    assert.match((await remove(reviewed.confirmationToken)).error ?? '', /review|changed/i);
    assert.ok(removable.every(file => fs.existsSync(absolute(file))));
    const refreshed = await plan(id);
    assert.notEqual(refreshed.confirmationToken, reviewed.confirmationToken);
    const removed = await remove(refreshed.confirmationToken);
    assert.equal(removed.error, undefined);
    assert.equal(activationRequests, 0);
    assert.ok(removable.every(file => !fs.existsSync(absolute(file))));
    for (const file of protectedFiles) assert.equal(fs.readFileSync(absolute(file), 'utf8'), content);
    assert.equal((await plan(id)).files.length, 0);
    return { passed: true, familyPlan: true, profilePlan: true, checkedLegacyAliases: true,
      staleConfirmationRejected: true, reviewedFilesRemoved: removable.length, unrelatedFilesPreserved: protectedFiles.length };
  } finally {
    await browser.close();
  }
}

if (require.main === module) {
  const [mode, target] = process.argv.slice(2);
  const run = async () => mode === '--inspect-exe' ? inspectExecutable(target) : checkModelRemoval(mode, target);
  run().then(result => console.log(JSON.stringify(result))).catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}
