// Exercise managed speech through the actual packaged model library.
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module');
if (!process.env.OPENCORE_SPEECH_QA_URL || !process.env.OPENCORE_SPEECH_QA_SECRET) {
  throw Error('Run qa-phonon-settings.py to start the real speech HTTP controller');
}
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
fixture = fixture.replace('window.fetch=async(url,options={})=>{', String.raw`
const actualFetch=window.fetch.bind(window);
window.fetch=async(url,options={})=>{
  const speechRoute=new URL(url).pathname.replace('/api','');
  if(speechRoute.startsWith('/speech/')){
    const body=options.body?JSON.parse(options.body):{};
    fixture.calls.push({route:speechRoute,body});
    if(speechRoute==='/speech/idle-mode'&&fixture.failIdleMode)throw Error('Settings could not be saved');
    const response=await actualFetch('/qa'+speechRoute,options);
    if(response.ok){
      const value=await response.clone().json();
      fixture.speech=speechRoute==='/speech/status'?value.status:value;
      sessionStorage.setItem('qa-speech-prefs',JSON.stringify(fixture.speech));
      fixture.catalog.forEach(entry=>entry.speechSelected=entry.id===fixture.speech?.modelId);
    }
    return response;
  }
`);
fixture = fixture.replace('const server=http.createServer((req,res)=>{', String.raw`
const server=http.createServer(async(req,res)=>{
  if(req.url.startsWith('/qa/speech/')){
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const body=Buffer.concat(chunks);
    const response=await fetch(process.env.OPENCORE_SPEECH_QA_URL+'/api'+req.url.slice(3),{
      method:req.method,headers:{'Content-Type':'application/json','X-OpenCore-Hub-Secret':process.env.OPENCORE_SPEECH_QA_SECRET},
      ...(body.length?{body}:{}),
    });
    res.statusCode=response.status;res.setHeader('Content-Type','application/json');return res.end(await response.text());
  }
`);
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
      const reset=await fetch(process.env.OPENCORE_SPEECH_QA_URL+'/api/speech/idle-mode',{
        method:'POST',headers:{'Content-Type':'application/json','X-OpenCore-Hub-Secret':process.env.OPENCORE_SPEECH_QA_SECRET},body:JSON.stringify({mode:'ram'}),
      });
      assert.equal(reset.status,200);
      const page=await browser.newPage({viewport:{width,height:900}});
      page.on('pageerror',error=>errors.push(error.message));
      page.on('response',async response=>{if(response.status()>=400&&response.url().includes('/qa/speech/'))console.log('Speech HTTP '+response.status()+': '+await response.text());});
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
      await disk.click();
      await page.waitForFunction(()=>fixture.speech?.idleMode==='cold',null,{timeout:5000});
      await page.waitForFunction(input=>input.checked,await disk.elementHandle());
      assert.equal(await disk.isChecked(),true);
      await page.waitForFunction(()=>fixture.calls.some(call=>call.route==='/speech/idle-mode'&&call.body.mode==='cold'));
      await ram.click();
      await page.waitForFunction(()=>fixture.speech?.idleMode==='ram');
      await page.waitForFunction(input=>input.checked,await ram.elementHandle());
      assert.equal(await ram.isChecked(),true);
      await toggle.click();await page.waitForFunction(()=>fixture.speech.enabled===false);
      await toggle.click();await page.waitForFunction(()=>fixture.speech.enabled===true);
      await disk.click();
      await page.waitForFunction(()=>fixture.speech.idleMode==='cold');
      await page.reload();await card.waitFor();await settings.waitFor();
      await page.waitForFunction(()=>fixture.speech?.idleMode==='cold');
      assert.equal(await disk.isChecked(),true,'The real server must preserve disk mode across reload');
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
