// Exercise managed speech through the actual packaged model library.
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module');
const original = fs.readFileSync(path.join(__dirname, 'qa-hub.cjs'), 'utf8');
let fixture = original.slice(0, original.indexOf('\n(async()=>'));
const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, '../src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'), 'utf8'));
const variants = catalog.entries.filter(entry => entry.runtime?.adapter === 'phonon25').map(entry => ({...entry, status: 'installed', runtimeReady: true}));
if (variants.length !== 5) throw Error('Expected the five pinned Phonon-2.5 packages');
const native = {id: 'phonon-2', label: 'Phonon-2', description: 'English speech recognition', precision: 'Five-value checkpoint', category: 'speech', speechLanguage: 'English', installed: true, selectable: false, installable: true, runtimeReady: true, backend: 'native', contextTokens: 0, note: 'Original checkpoint'};
fixture = fixture.replace('window.fixture={catalog:[],progress:null,calls:[],token:false};',
  'window.fixture={catalog:' + JSON.stringify(variants) + ',nativeModels:[' + JSON.stringify(native) + '],speech:JSON.parse(sessionStorage.getItem("qa-speech-prefs")||"null"),progress:null,calls:[],token:false};fixture.catalog.forEach(entry=>entry.speechSelected=entry.id===fixture.speech?.modelId);');
fixture = fixture.replace("case 'speech_status':return{enabled:false,installed:false,modelId:'whisper-large-v3-turbo',phase:'off'};",
  "case 'speech_status':return{enabled:false,installed:true,modelId:'phonon-2',idleMode:'cold',phase:'off'};");
fixture = fixture.replace("if(route==='/status'){", String.raw`
if(route==='/speech/status') value={status:fixture.speech};
else if(route==='/speech/select') {
  fixture.speech=body.id?{modelId:body.id,enabled:true,installed:true,idleMode:fixture.speech?.idleMode||'ram',phase:'idle',workerReady:false}:null;
  fixture.catalog.forEach(entry=>entry.speechSelected=entry.id===body.id);
  value=fixture.speech;
}
else if(route==='/speech/enabled'){fixture.speech.enabled=body.enabled;value=fixture.speech;}
else if(route==='/speech/idle-mode'){if(fixture.failIdleMode)throw Error('Settings could not be saved');fixture.speech.idleMode=body.idleMode;value=fixture.speech;}
else if(route==='/status'){`);
fixture = fixture.replace('return{ok:true,json:async()=>structuredClone(value)};',
  'if(route.startsWith("/speech/")&&route!=="/speech/status")sessionStorage.setItem("qa-speech-prefs",JSON.stringify(fixture.speech));return{ok:true,json:async()=>structuredClone(value)};');
fixture = fixture.replace('React.createElement(TestHubSettings)', 'null');
fixture += String.raw`
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const url='http://127.0.0.1:'+server.address().port;
  if(process.env.OPENCORE_SPEECH_QA_SERVE==='1'){console.log(url);return;}
  const assert=require('node:assert/strict');
  const output=path.resolve(process.env.OPENCORE_SPEECH_QA_OUTPUT||path.join(directory,'../phonon-settings-qa'));
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({headless:true});
  const errors=[];
  try{
    for(const width of [1366,390]){
      const page=await browser.newPage({viewport:{width,height:900}});
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto(url);
      const card=page.getByRole('article',{name:'Phonon-2.5',exact:true});
      await card.waitFor();
      await page.screenshot({path:path.join(output,'phonon-settings-before-'+width+'.png'),fullPage:true});
      const settings=card.getByLabel('Phonon-2.5 speech settings',{exact:true});
      await settings.waitFor({timeout:5000});
      const disk=settings.getByRole('radio',{name:/Load from disk each time/});
      const ram=settings.getByRole('radio',{name:/Keep sleeping in RAM/});
      const toggle=settings.getByRole('switch',{name:'Speech to text',exact:true});
      await page.evaluate(()=>fixture.failIdleMode=true);
      await disk.click();
      await settings.getByRole('alert').waitFor();
      await page.waitForTimeout(2000);
      assert.equal(await settings.getByRole('alert').isVisible(),true,'A status refresh must not hide a failed settings save');
      await page.evaluate(()=>fixture.failIdleMode=false);
      await disk.check();assert.equal(await disk.isChecked(),true);
      await page.waitForFunction(()=>fixture.calls.some(call=>call.route==='/speech/idle-mode'&&call.body.idleMode==='cold'));
      await ram.check();assert.equal(await ram.isChecked(),true);
      await toggle.click();await page.waitForFunction(()=>fixture.speech.enabled===false);
      await toggle.click();await page.waitForFunction(()=>fixture.speech.enabled===true);
      await page.reload();await card.waitFor();await settings.waitFor();
      await ram.check();
      assert.equal(await card.getByRole('combobox').locator('option').count(),5);
      assert.equal(await settings.evaluate(element=>element.scrollWidth<=element.clientWidth+1),true);
      assert.equal(await settings.evaluate(element=>getComputedStyle(element).display),'grid');
      await page.screenshot({path:path.join(output,'phonon-settings-after-'+width+'.png'),fullPage:true});
      await page.close();
      console.log('PASS packaged Phonon-2.5 disk/RAM mode, dictation switch, five quants, reload and layout at '+width+'px');
    }
    assert.deepEqual(errors,[]);
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
`;
const filename = path.join(__dirname, 'qa-phonon-settings.fixture.cjs');
const compiled = new Module(filename, module);
compiled.filename = filename;
compiled.paths = Module._nodeModulePaths(__dirname);
compiled._compile(fixture, filename);
