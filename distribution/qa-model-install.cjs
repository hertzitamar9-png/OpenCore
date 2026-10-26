const fs = require('fs');
const path = require('path');
const http = require('http');
const acorn = require('acorn');
const { chromium } = require(process.env.OPENCORE_PLAYWRIGHT || 'playwright');

const directory = path.join(__dirname, process.argv[2] || 'recovered-dist');
const indexName = 'index-CD0EH-dA.js', appName = 'App-kNA9sRUP.js';
let entry = fs.readFileSync(path.join(directory, 'assets', indexName), 'utf8');
const entryTree = acorn.parse(entry, { ecmaVersion: 'latest', sourceType: 'module' });
const mount = entryTree.body.find(node => node.type === 'ExpressionStatement' && entry.slice(node.start, node.end).includes('createRoot('));
if (!mount) throw new Error('Expected one application mount');
entry = entry.slice(0, mount.start) + entry.slice(mount.end) + '\nexport {ev as TestDOM};';
const app = fs.readFileSync(path.join(directory, 'assets', appName), 'utf8') + '\nexport {yj as TestModels};';
const fixture = `
window.fixture={calls:[],jobs:[],receipts:[],activeJobId:null,progress:null,holdStart:true,release:null};
const base={description:'Installation QA fixture',contextTokens:8192,license:'test',precision:'BF16',experimental:false,note:'test',selectable:true,installed:false,externalManaged:false,downloadBytes:100,totalBytes:100,category:'text',installable:true,runtimeReady:false,memoryMode:'echo'};
fixture.models=[{...base,id:'edgerock-4b',label:'EdgeRock-4B',backend:'managed-text',managedInstallation:true},{...base,id:'orca-cyber',label:'OrcaCyber Nano 1.0',backend:'gguf',managedInstallation:true},{...base,id:'spark-x26',label:'Spark X2.6 4B',backend:'managed-text',managedInstallation:true},{...base,id:'plain-gguf',label:'Plain GGUF',backend:'gguf',runtimeReady:true,managedInstallation:false}];
const oldTerminal=sessionStorage.getItem('notification-qa-terminal');if(oldTerminal){fixture.progress=JSON.parse(oldTerminal);fixture.models.push({...fixture.models[3],id:'phonon-2',label:'Phonon-2'});}
fixture.recipes=fixture.models.filter(m=>m.managedInstallation).map(m=>({id:'fixture-'+m.id,label:m.label,kind:m.backend==='managed-text'?'text':'gguf',modelIds:[m.id],sourceUrls:[],limitations:'QA fixture'}));
fixture.complete=function(id){const m=this.models.find(m=>m.id===id);m.installed=true;m.runtimeReady=true;const job=this.jobs.findLast(j=>j.targetId===id);if(job){job.status='ready';job.stage='ready';job.detail='Ready';job.error=null;}this.activeJobId=null;this.progress=null;this.receipts=[...this.receipts.filter(r=>r.targetId!==id),{targetId:id,dependenciesVerified:true,inferenceVerified:true}];};
window.__TAURI_INTERNALS__={invoke:async function(command,args={}){
fixture.calls.push({command,args});
switch(command){
case 'list_model_library':return structuredClone({models:fixture.models,progress:fixture.progress,diskFreeBytes:100000000000,minimumFreeBytes:0});
case 'runtime_setup_status':return structuredClone({recipes:fixture.recipes,jobs:fixture.jobs,receipts:fixture.receipts,activeJobId:fixture.activeJobId,managedRoot:'fixture'});
case 'model_install_progress':return structuredClone(fixture.progress);
case 'speech_status':return {modelId:'whisper-large-v3-turbo',installed:false,enabled:false,idleMode:'cold',workerReady:false,phase:'off',coldStartMs:null,warmWakeMs:null};
case 'model_weight_update_status':return {enabled:false,phase:'idle',intervalHours:24,models:[]};
case 'runtime_setup_start':{
 if(fixture.holdStart)await new Promise(resolve=>fixture.release=resolve);
 const job={id:'job-'+fixture.jobs.length,targetId:args.targetId,status:'running',stage:'downloading',detail:'Downloading pinned checkpoint',downloadedBytes:30,totalBytes:100,diagnostics:[],error:null};fixture.jobs.push(job);fixture.activeJobId=job.id;return structuredClone(job);}
case 'install_model':
 if(fixture.holdStart)await new Promise(resolve=>fixture.release=resolve);
 fixture.progress={modelId:args.id,phase:'downloading',downloadedBytes:30,totalBytes:100,currentFile:'weights.gguf',error:null,operationId:'fixture-download'};return null;
case 'runtime_setup_cancel':{const job=fixture.jobs.find(j=>j.id===args.jobId);if(job){job.status='cancelled';job.stage='cancelled';job.error='Model preparation cancelled';}fixture.activeJobId=null;return null;}
case 'cancel_model_install':fixture.progress=null;return null;
default:return null;
}}};
`;
const html = `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/assets/index-CsoxYKnK.css"><link rel="stylesheet" href="/assets/App-C9duitZV.css"><div id="test-root"></div><script>${fixture}</script><script type="module">
import {r as React,TestDOM,i as invoke} from '/assets/${indexName}';
import {TestModels} from '/assets/${appName}';
globalThis.__opencoreModelLibrary?.setScope('notification-qa-account');
window.invokeForTest=invoke;
const root=TestDOM.createRoot(document.getElementById('test-root'));
window.remountModels=()=>{root.render(null);setTimeout(()=>root.render(React.createElement(TestModels,{selectedProfile:'none',onSelect:id=>window.selectedModel=id,runtimeActive:false,onNotice:message=>window.lastNotice=message})),0);};
root.render(React.createElement(TestModels,{selectedProfile:'none',onSelect:id=>window.selectedModel=id,runtimeActive:false,onNotice:message=>window.lastNotice=message}));
</script>`;

const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost');
  if (url.pathname === '/') { response.setHeader('Content-Type', 'text/html'); return response.end(html); }
  if (url.pathname === '/assets/' + indexName) { response.setHeader('Content-Type', 'text/javascript'); return response.end(entry); }
  if (url.pathname === '/assets/' + appName) { response.setHeader('Content-Type', 'text/javascript'); return response.end(app); }
  const file = path.resolve(directory, '.' + url.pathname);
  if (!file.startsWith(directory + path.sep) || !fs.existsSync(file)) { response.statusCode = 404; return response.end(); }
  response.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : 'text/javascript');
  response.end(fs.readFileSync(file));
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),  headless: true, args: ['--mute-audio'] });
  const page = await browser.newPage({ viewport: { width: 1450, height: 1000 } });
  const errors = [], results = [];
  page.on('pageerror', error => errors.push(error.message));
  function record(name, passed, detail) { results.push({ name, passed: Boolean(passed), detail }); }
  const card = label => page.getByRole('article').filter({ has: page.getByRole('heading', { name: label, exact: true }) });
  try {
    await page.goto('http://127.0.0.1:' + server.address().port);
    await page.waitForTimeout(500);
    if (!(await card('EdgeRock-4B').count())) console.log('FIXTURE DIAGNOSTICS', JSON.stringify({errors,body:(await page.locator('body').innerText()).slice(0,3500),calls:await page.evaluate(()=>window.fixture?.calls)}));
    await card('EdgeRock-4B').getByRole('button', { name: 'Install', exact: true }).waitFor({timeout:5000});
    await card('EdgeRock-4B').getByRole('button', { name: 'Install', exact: true }).click();
    record('active managed model immediately says Installing', await card('EdgeRock-4B').getByRole('button', { name: 'Installing…', exact: true }).count());
    record('Orca install is disabled before admission resolves', await card('OrcaCyber Nano 1.0').getByRole('button', { name: 'Install', exact: true }).isDisabled());
    record('plain GGUF install is disabled before admission resolves', await card('Plain GGUF').getByRole('button', { name: 'Install', exact: true }).isDisabled());
    record('Spark install is disabled before admission resolves', await card('Spark X2.6 4B').getByRole('button', { name: 'Install', exact: true }).isDisabled());
    record('top progress appears immediately for managed models', await page.locator('.model-install-progress').count());
    await page.evaluate(() => { fixture.holdStart = false; fixture.release(); });
    await page.waitForTimeout(2200);
    const progress = page.locator('.model-install-progress progress');
    record('managed download reports bytes in the top panel', await progress.count() && await progress.getAttribute('value') === '30');
    const rejected = await page.evaluate(async () => { try { await invokeForTest('runtime_setup_start', { targetId: 'orca-cyber', options: {} }); return false; } catch { return true; } });
    record('shared guard rejects a second start before native IPC', rejected);
    await page.evaluate(() => {
      fixture.complete('edgerock-4b');
      const receipts=fixture.receipts;fixture.receipts=[];
      setTimeout(()=>fixture.receipts=receipts,1600);
    });
    await page.waitForTimeout(2800);
    record('completed model offers Use model after delayed receipt publication', await card('EdgeRock-4B').getByRole('button', { name: 'Use model', exact: true }).count());
    if (await card('OrcaCyber Nano 1.0').getByRole('button', { name: 'Install', exact: true }).isEnabled()) {
      await card('OrcaCyber Nano 1.0').getByRole('button', { name: 'Install', exact: true }).click();
      await page.waitForTimeout(1800);
      const pause = page.locator('.model-install-progress').getByRole('button', { name: 'Pause installation', exact: true });
      record('Orca has shared progress and Pause', await pause.count());
      if (await pause.count()) {
        await pause.click();
        await page.waitForTimeout(1200);
        record('pausing releases the global lock', await card('Plain GGUF').getByRole('button', { name: 'Install', exact: true }).isEnabled());
        record('paused model does not remain Installing', await card('OrcaCyber Nano 1.0').getByRole('button', { name: 'Installing…', exact: true }).count() === 0);
        await card('Plain GGUF').getByRole('button', { name: 'Install', exact: true }).click();
        await page.waitForTimeout(1200);
        record('ordinary downloads use the same shared progress', await page.locator('.model-install-progress').innerText().then(text => text.includes('Plain GGUF')));
        await page.evaluate(() => remountModels());
        await page.waitForTimeout(1200);
        record('reopening Models retains the active installation', await page.locator('.model-install-progress').innerText().then(text => text.includes('Plain GGUF')));
        await page.locator('.model-install-progress').getByRole('button',{name:'Pause installation',exact:true}).click();
        await page.waitForTimeout(1100);
        await card('Spark X2.6 4B').getByRole('button',{name:'Install',exact:true}).click();
        await page.waitForTimeout(1100);
        record('Spark uses the same shared progress',await page.locator('.model-install-progress').innerText().then(text=>text.includes('Spark X2.6 4B')));
        await page.evaluate(()=>{const job=fixture.jobs.findLast(j=>j.targetId==='spark-x26');job.status='failed';job.stage='failed';job.error='Fixture setup failure';fixture.activeJobId=null;});
        await page.waitForTimeout(1200);
        record('failed setup unlocks other model installs',await card('Plain GGUF').getByRole('button',{name:'Install',exact:true}).isEnabled());
        record('failed setup reports the real error',await page.locator('.model-install-progress').innerText().then(text=>text.includes('Fixture setup failure')));
        await page.locator('.model-install-progress').getByRole('button',{name:'Dismiss notification',exact:true}).click();
        await page.waitForTimeout(200);
        record('terminal notification can be dismissed',await page.locator('.model-install-progress').count()===0);
      }
    }
    await page.evaluate(async()=>{
      const terminal={modelId:'phonon-2',operationId:'native-phonon-download',phase:'ready',downloadedBytes:100,totalBytes:100,currentFile:'Ready',error:null};
      fixture.models.push({...fixture.models.find(model=>model.id==='plain-gguf'),id:'phonon-2',label:'Phonon-2'});
      fixture.progress=terminal;sessionStorage.setItem('notification-qa-terminal',JSON.stringify(terminal));
      const runtime={id:'runtime-phonon-prepare',targetId:'phonon-2',status:'running',stage:'verifying',detail:'Checking the installed speech runtime'};
      fixture.jobs.push(runtime);fixture.activeJobId=runtime.id;await invokeForTest('runtime_setup_status');
      runtime.status='ready';runtime.stage='ready';fixture.activeJobId=null;await invokeForTest('runtime_setup_status');
      const library=await invokeForTest('list_model_library');
      window.dispatchEvent(new CustomEvent('opencore-model-library-changed',{detail:library}));
    });
    await page.waitForTimeout(2200);
    record('old Phonon completion stays hidden through repeated native polls without another dismissal',await page.locator('.model-install-progress').count()===0);
    await page.evaluate(async()=>{
      const library=await invokeForTest('list_model_library');
      library.progress={...library.progress,currentFile:'Readiness checked again'};
      window.dispatchEvent(new CustomEvent('opencore-model-library-changed',{detail:library}));
      remountModels();
    });
    await card('Phonon-2').waitFor();await page.waitForTimeout(250);
    record('old Phonon success stays hidden through model-library refresh and component remount',await page.locator('.model-install-progress').count()===0);
    await page.reload();await card('Phonon-2').waitFor();await page.waitForTimeout(1500);
    record('old Phonon success stays hidden after an app page reload',await page.locator('.model-install-progress').count()===0);
    await page.evaluate(async()=>{fixture.holdStart=false;await invokeForTest('install_model',{id:'phonon-2'});});
    await page.locator('.model-install-progress progress').waitFor();
    record('a new installation of the same model shows progress after the previous dismissal',await page.locator('.model-install-progress').innerText().then(text=>text.includes('Phonon-2')));
    await page.evaluate(()=>{fixture.complete('phonon-2');sessionStorage.removeItem('notification-qa-terminal');});
    await page.locator('.model-install-progress').getByText('ready',{exact:true}).waitFor();
    record('the new installation can display its own completion notification',await page.locator('.model-install-progress').getByRole('button',{name:'Dismiss notification',exact:true}).count()===1);
    await page.locator('.model-install-progress').getByRole('button',{name:'Dismiss notification',exact:true}).click();
    await page.waitForTimeout(1200);
    record('the new Phonon completion remains dismissed',await page.locator('.model-install-progress').count()===0);
    await page.screenshot({ path: path.join(__dirname, (process.argv[2] || 'baseline') + '-models.png'), fullPage: true });
    record('no browser runtime errors', errors.length === 0, errors);
  } finally {
    await browser.close(); await new Promise(resolve => server.close(resolve));
  }
  fs.writeFileSync(path.join(__dirname, (process.argv[2] || 'baseline') + '-qa.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
  if (results.some(result => !result.passed)) process.exitCode = 1;
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
