// Exercise the same Tauri commands as the Uninstall dialog on a fresh test home.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.OPENCORE_PLAYWRIGHT || 'playwright');

async function checkModelRemoval(port, home) {
  assert.equal(path.basename(home), 'home');
  assert.match(path.basename(path.dirname(home)), /^opencore-(bridge-policy|native-removal-qa)-/);
  const root = path.join(home, 'OpenCore');
  const removable = [
    'models/library/qwen35-08b-q8/Qwen3.5-0.8B-Q8_0.gguf',
    'models/library/qwen35-08b-q8-q4-k-m/Qwen3.5-0.8B-Q4_K_M.gguf.partial',
  ];
  const protectedFiles = [
    'models/nanbeige/Nanbeige_Nanbeige4.2-3B-bf16.gguf',
    'speech/phonon-2/phonon-2.bps.tar.zst.partial',
    'echo/disposable-history.txt',
  ];
  const content = 'temporary model removal fixture';
  const absolute = file => path.join(root, file);
  for (const file of [...removable, ...protectedFiles]) {
    fs.mkdirSync(path.dirname(absolute(file)), { recursive: true });
    fs.writeFileSync(absolute(file), content, { flag: 'wx' });
  }
  const comparable = file => path.normalize(file.replace(/^\\\\\?\\/, '')).toLowerCase();
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + port);
  try {
    const page = browser.contexts().flatMap(context => context.pages()).find(p => p.url().includes('tauri.localhost'));
    assert.ok(page, 'The packaged app WebView is available');
    await page.waitForFunction(() => typeof window.__TAURI_INTERNALS__?.invoke === 'function');
    const invoke = (command, args) => page.evaluate(async ({ command, args }) => {
      try {
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

    const id = 'qwen35-08b-q8';
    const reviewed = await plan(id);
    assert.equal(reviewed.scope, 'family');
    assert.deepEqual(reviewed.files.map(file => comparable(file.path)).sort(), removable.map(absolute).map(comparable).sort());
    assert.equal(reviewed.totalBytes, Buffer.byteLength(content) * removable.length);
    const profile = await plan(id, 'profile');
    assert.equal(profile.scope, 'profile');
    assert.ok(profile.files.every(file => comparable(file.path) !== comparable(absolute(removable[1]))));
    for (const legacy of ['nanbeige-bf16', 'nanbeige-bf16-echo', 'dualcore-kv', 'dualcore-echo', 'phonon-2'])
      assert.equal((await plan(legacy, 'family')).scope, 'family');
    for (const file of [...removable, ...protectedFiles])
      assert.equal(fs.readFileSync(absolute(file), 'utf8'), content);

    fs.appendFileSync(absolute(removable[0]), ' changed after review');
    const remove = confirmationToken => invoke('uninstall_model', { id, scope: reviewed.scope, confirmationToken });
    assert.match((await remove(reviewed.confirmationToken)).error ?? '', /review|changed/i);
    assert.ok(removable.every(file => fs.existsSync(absolute(file))));
    const refreshed = await plan(id);
    assert.notEqual(refreshed.confirmationToken, reviewed.confirmationToken);
    const removed = await remove(refreshed.confirmationToken);
    assert.equal(removed.error, undefined);
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
  const [port, home] = process.argv.slice(2);
  checkModelRemoval(port, home).then(result => console.log(JSON.stringify(result))).catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
}
