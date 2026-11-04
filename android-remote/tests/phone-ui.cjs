'use strict';
// Loads the actual desktop React bundle and production phone bridge/CSS.
// Fixture browser evidence is separate from physical Android/JNI evidence.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http'),vm=require('node:vm');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT||'playwright'),ts=require('typescript'),fixture=require('./shared-ui-fixture.cjs');
const repo=path.resolve(__dirname,'../..'),output=path.resolve(process.env.OPENCORE_UI_OUTPUT||path.join(repo,'distribution/.output/ui-285'));
const dist=path.basename(output)==='candidate-dist'?output:path.join(output,'candidate-dist'),evidence=path.resolve(process.env.OPENCORE_PHONE_QA_OUTPUT||path.join(output,'phone-shared-ui-qa'));
assert.ok(fs.existsSync(path.join(dist,'index.html')),'Build desktop UI first; OPENCORE_UI_OUTPUT must contain candidate-dist/index.html');
const values={exports:{},Date};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(repo,'src/mock.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,values);
const preview=JSON.parse(JSON.stringify(values.exports));
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://localhost'),name=decodeURIComponent(url.pathname);
 if(name==='/'||name==='/index.html'){res.setHeader('Content-Type','text/html');res.end(fs.readFileSync(path.join(dist,'index.html'),'utf8').replace('<head>',`<head><meta name="opencore-execution" content="${url.searchParams.get('mode')==='pc'?'pc':'phone'}"><meta name="opencore-native-nonce" content="fixture"><script src="/phone/mobile-shim.js"></script><link rel="stylesheet" href="/phone/mobile.css">`));return;}
 const base=name.startsWith('/phone/')?path.join(repo,'android-remote/assets'):dist;
 const file=name==='/opencore-logo.png'?path.join(repo,'public/opencore-logo.png'):path.resolve(base,'.'+(name.startsWith('/phone/')?name.slice(6):name));
 if((!file.startsWith(base+path.sep)&&name!=='/opencore-logo.png')||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.statusCode=404;res.end();return;}
 res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'image/png');res.end(fs.readFileSync(file));
});
async function layout(page,label){
 // The hidden desktop rail and the visible phone tab bar share this label.
 // Match the visible accessible navigation, as the interaction checks do;
 // querySelector would depend on their current DOM insertion order.
 const navigation=page.getByRole('navigation',{name:'OpenCore sections',exact:true});
 await navigation.waitFor({state:'visible'});
 await page.evaluate(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});
 const g=await navigation.evaluate(nav=>{
  const el=document.querySelector('.chat-composer');
  const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
  return {composer:rect(el),buttons:[...el.querySelectorAll('button')].filter(e=>e.offsetParent!==null).map(e=>({name:e.getAttribute('aria-label')||e.textContent.trim(),...rect(e)})),nav:rect(nav),width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth};
 });
 assert.ok(g.nav.height>0&&g.nav.width>=g.width-1,label+': visible full-width bottom navigation');
 assert.ok(g.buttons.length>=6,label+': shared composer controls');
 for(const b of g.buttons){assert.ok(b.width>=44&&b.height>=44,label+': 44px target '+JSON.stringify(b));assert.ok(b.x>=0&&b.right<=g.width+1&&b.y>=0&&b.bottom<=g.height,label+': control within usable viewport '+JSON.stringify(b));}
 for(let a=0;a<g.buttons.length;a++)for(let b=a+1;b<g.buttons.length;b++){const r=g.buttons[a],s=g.buttons[b];assert.ok(r.right<=s.x+1||s.right<=r.x+1||r.bottom<=s.y+1||s.bottom<=r.y+1,label+': overlap '+r.name+' / '+s.name);}
 assert.ok(g.composer.bottom<=g.nav.y+1&&g.nav.bottom<=g.height+1,label+': composer/navigation above usable viewport bottom '+JSON.stringify(g));
 assert.ok(g.scrollWidth<=g.width,label+': no horizontal overflow');
}
async function controls(page){
 const composer=page.locator('.chat-composer');
 for(const name of [/Add files or choose model/,/Approval:/,/Effort:/,/generation settings/i,/speech|microphone|dictat/i]){const b=composer.getByRole('button',{name});assert.ok(await b.isVisible(),'Original button '+name);assert.ok(await b.locator('svg').first().isVisible(),'Original icon '+name);}
 await composer.getByRole('button',{name:/Approval:/}).click();await page.getByRole('group',{name:'Approval mode'}).waitFor();await composer.getByRole('button',{name:/Approval:/}).click();
 await composer.getByRole('button',{name:/Effort:/}).click();await page.getByRole('slider',{name:'Reasoning effort'}).waitFor();await composer.getByRole('button',{name:/Effort:/}).click();
}
function emptyPhoneSnapshot(){
 const snapshot=structuredClone(preview.previewSnapshot);
 snapshot.runtime={...snapshot.runtime,status:'stopped',profile:'stopped',error:null};
 snapshot.activeConversationIds=[];snapshot.conversations=[];snapshot.projects=[];snapshot.logs=[];snapshot.connectors=[];
 snapshot.telemetry={...snapshot.telemetry,gpuName:'Phone CPU',vramTotalMib:0};return snapshot;
}
async function assertPhoneIdle(page,expected){
 await page.waitForFunction(()=>fixture.calls.some(c=>c.command==='select_profile'),null,{timeout:10000});
 await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
 const calls=await page.evaluate(()=>fixture.calls);
 assert.ok(calls.filter(c=>c.command==='select_profile').every(c=>c.args.profile===expected),'Phone must select only the backend-seeded profile '+expected+': '+JSON.stringify(calls.filter(c=>c.command==='select_profile')));
 assert.equal(calls.some(c=>/^(start_profile|restart_runtime|install_model|download_model|runtime_setup_start)$/.test(c.command||'')),false,'Opening Phone must not start a runtime, prepare or download a model');
 assert.equal(calls.some(c=>c.kind==='hub'&&/^\/api\/(install|prepare|download)(?:\/|$)/.test(c.path)),false,'Opening Phone must not start a Hub model download/preparation');
 assert.equal(calls.some(c=>c.command==='select_profile'&&c.args.profile==='doucode'),false,'Phone must not auto-select desktop DuoCore');
 assert.equal(await page.locator('.toast').count(),0,'Phone startup must not show a red model-selection error toast');
 assert.equal(await page.evaluate(()=>localStorage.getItem('opencore.model-profile')),expected,'Native preference remains the selected Phone profile');
}
async function phoneStartupRegressions(browser){
 const fresh=await browser.newPage({viewport:{width:393,height:804},isMobile:true,hasTouch:true}),errors=[];
 fresh.on('pageerror',e=>errors.push(e.message));
 try{
  await fresh.addInitScript(fixture,{snapshot:emptyPhoneSnapshot(),timeline:[],mode:'phone',profileSeed:'stopped',strictPhoneProfiles:true});
  await fresh.goto(`http://127.0.0.1:${server.address().port}/?mode=phone`);await fresh.locator('.opencore-shell').waitFor();
  await assertPhoneIdle(fresh,'stopped');
  assert.equal(await fresh.evaluate(()=>fixture.models.length),0,'Fresh Phone has no installed model');
  assert.equal(await fresh.evaluate(()=>fixture.snapshot.runtime.status),'stopped','Fresh Phone remains idle without model inference');
  await fresh.getByRole('navigation',{name:'OpenCore sections',exact:true}).getByRole('button',{name:'More',exact:true}).click();
  await fresh.getByRole('dialog',{name:'All OpenCore sections'}).getByRole('button',{name:'Chat Core',exact:true}).click();
  const dialog=fresh.getByRole('dialog',{name:'Chat Core',exact:true});await dialog.waitFor();
  await fresh.waitForFunction(()=>fixture.calls.some(c=>c.kind==='hub'&&c.path.startsWith('/api/groups/')));
  assert.equal(await fresh.evaluate(()=>fixture.calls.some(c=>c.path==='/api/groups/capabilities')),true,'Phone Chat Core must check the native capability before listing groups');
  await dialog.getByText('Chat Core group collaboration requires the paired PC service.',{exact:false}).waitFor();
  assert.equal(await dialog.getByRole('button',{name:'Create group',exact:true}).count(),0,'Unavailable Phone groups must not offer unsupported actions');
  await dialog.getByRole('button',{name:'Choose Phone or Paired PC',exact:true}).click();
  await fresh.waitForFunction(()=>fixture.calls.some(c=>c.kind==='phone-execution-settings'));
  // The normal workspace polls every two seconds. Observe beyond two periods
  // to catch a mounted unsupported workspace or a background list retry loop.
  await fresh.waitForTimeout(4300);
  const groupCalls=await fresh.evaluate(()=>fixture.calls.filter(c=>c.kind==='hub'&&c.path.startsWith('/api/groups/')));
  assert.deepEqual(groupCalls.map(c=>c.path),['/api/groups/capabilities'],'Unavailable Phone Chat Core must make one capability request and no list/view/worker polling');
  await assertPhoneIdle(fresh,'stopped');
  await fresh.screenshot({path:path.join(evidence,'phone-fresh-stopped-chat-core-capability.png')});assert.deepEqual(errors,[]);
  console.log('Fresh Phone stopped selection and Chat Core capability/native-action/no-polling regressions passed');
 }catch(error){console.error('Fresh Phone regression:',error);console.error('Calls:',await fresh.evaluate(()=>fixture.calls.filter(c=>c.command==='select_profile'||c.kind==='hub')));await fresh.screenshot({path:path.join(evidence,'phone-fresh-regression-failure.png')});throw error;}finally{await fresh.close();}
 const alias='phone-fixture-installed-native',hash='b'.repeat(64);
 const models=[{id:alias,label:'Fixture downloaded alias model'},{id:hash,label:'Fixture downloaded hash model'}].map(model=>({...model,description:'Installed phone GGUF fixture',installed:true,selectable:true,runtimeReady:true,installable:true,category:'text',backend:'gguf',precision:'Q4_K_M',totalBytes:1048576,weightBytes:1048576,contextTokens:2048,memoryMode:'native',artifacts:[]}));
 const custom=await browser.newPage({viewport:{width:393,height:804},isMobile:true,hasTouch:true}),customErrors=[];custom.on('pageerror',e=>customErrors.push(e.message));
 async function modelPicker(){
  await custom.getByRole('button',{name:'Add files or choose model',exact:true}).click();
  await custom.getByRole('menu',{name:'Composer actions'}).getByRole('menuitem',{name:/^Model/}).click();
  return custom.getByRole('group',{name:'Choose model profile',exact:true});
 }
 try{
  await custom.addInitScript(fixture,{snapshot:emptyPhoneSnapshot(),timeline:[],mode:'phone',profileSeed:alias,installedModels:models,strictPhoneProfiles:true,persistPhoneSelection:true});
  await custom.goto(`http://127.0.0.1:${server.address().port}/?mode=phone`);await custom.locator('.opencore-shell').waitFor();await assertPhoneIdle(custom,alias);
  let picker=await modelPicker();const initial=picker.getByRole('button',{name:/Fixture downloaded alias model/});await initial.waitFor();assert.equal(await initial.getAttribute('aria-pressed'),'true','Backend-seeded custom alias is selected in the original model picker');
  await picker.getByRole('button',{name:/Fixture downloaded hash model/}).click();await custom.waitForFunction(id=>fixture.calls.some(c=>c.command==='select_profile'&&c.args.profile===id),hash);
  assert.equal(await custom.evaluate(()=>fixture.selectedProfile),hash,'Original model picker selects the installed hash ID through native IPC');
  await custom.reload();await custom.locator('.opencore-shell').waitFor();await assertPhoneIdle(custom,hash);
  picker=await modelPicker();const reopened=picker.getByRole('button',{name:/Fixture downloaded hash model/});await reopened.waitFor();assert.equal(await reopened.getAttribute('aria-pressed'),'true','Reopened Phone honors the backend-seeded selected hash ID');
  await custom.screenshot({path:path.join(evidence,'phone-custom-model-reopened.png')});assert.deepEqual(customErrors,[]);
  console.log('Phone backend-seeded custom alias, original-picker hash selection and reopen regressions passed');
 }catch(error){console.error('Custom Phone model regression:',error);console.error('Browser errors:',customErrors);console.error('Calls:',await custom.evaluate(()=>fixture.calls.slice(-35)));console.error((await custom.locator('body').innerText()).slice(0,3500));await custom.screenshot({path:path.join(evidence,'phone-custom-model-regression-failure.png')});throw error;}finally{await custom.close();}
 return {freshStopped:true,noAutomaticRuntimeOrDownload:true,noStartupToast:true,chatCoreCapability:true,chatCoreNativeAction:true,noUnsupportedGroupPolling:true,customAlias:true,customHashReopened:true};
}
(async()=>{
 fs.mkdirSync(evidence,{recursive:true});fs.rmSync(path.join(evidence,'results.json'),{force:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']}),results=[];
 try{const startupRegressions=await phoneStartupRegressions(browser);for(const mode of ['phone','pc'])for(const width of [360,393,430]){
  const page=await browser.newPage({viewport:{width,height:804},isMobile:true,hasTouch:true}),errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
  const snapshot=structuredClone(preview.previewSnapshot);snapshot.runtime={...snapshot.runtime,status:mode==='phone'?'stopped':'running',profile:mode==='phone'?'stopped':'echo',error:null};snapshot.activeConversationIds=[];
  if(mode==='phone'){snapshot.conversations=[];snapshot.projects=[];snapshot.logs=[];snapshot.connectors=[];snapshot.telemetry={...snapshot.telemetry,gpuName:'Phone CPU',vramTotalMib:0};}
  await page.addInitScript(fixture,{snapshot,timeline:mode==='phone'?[]:preview.previewTimeline,mode});
  try{
   await page.goto(`http://127.0.0.1:${server.address().port}/?mode=${mode}`);await page.locator('.opencore-shell').waitFor({timeout:20000});await page.waitForFunction(()=>fixture.calls.some(c=>c.command==='get_snapshot'));
   assert.equal(await page.evaluate(()=>Object.keys(document.querySelector('#root')).some(k=>k.startsWith('__reactContainer'))),true,'Actual React root mounted');
   assert.equal(requests.some(url=>/\/local\/(?:app.js|index.html)|\/app.html/.test(url)),false,'No bespoke phone UI');
   if(mode==='phone'){
    assert.equal(await page.evaluate(()=>fixture.snapshot.conversations.length),0,'First launch empty real-shape snapshot');
    assert.equal(await page.getByText('Build a data analysis script',{exact:true}).count(),0,'No desktop preview chat on first launch');
    await page.evaluate(({populated,timeline})=>fixture.populate(populated,timeline),{populated:{...preview.previewSnapshot,runtime:{...snapshot.runtime,status:'running',profile:'echo'},activeConversationIds:[]},timeline:[]});
   }
   const nav=page.getByRole('navigation',{name:'OpenCore sections'});assert.deepEqual(await nav.getByRole('button').allTextContents(),['Chats','Overview','Models','Settings','More']);
   await page.getByLabel('Message OpenCore',{exact:true}).fill('SHARED_PHONE_FIXTURE_PROMPT');await controls(page);await layout(page,`${mode}-${width}`);
   // Native Android supplies a content viewport with system navigation insets.
   // Shrinking its usable height validates layout; it does not emulate JNI.
   await page.setViewportSize({width,height:540});await layout(page,`${mode}-${width}-short`);await page.setViewportSize({width,height:804});
   await page.getByRole('button',{name:'Send message',exact:true}).click();await page.waitForFunction(()=>fixture.calls.some(c=>c.command==='send_chat_message'&&c.args.request.text==='SHARED_PHONE_FIXTURE_PROMPT'));
   await page.getByRole('button',{name:'Stop generation',exact:true}).waitFor();await page.evaluate(()=>fixture.stream('STREAMED_PHONE_OUTPUT <script>window.sharedOutputExecuted=true</script> <img src=x onerror="window.sharedOutputExecuted=true">'));
   await page.getByText(/STREAMED_PHONE_OUTPUT/).first().waitFor();assert.equal(await page.evaluate(()=>window.sharedOutputExecuted),undefined,'Raw output cannot execute script');await layout(page,`${mode}-${width}-streaming`);
   await page.evaluate(()=>fixture.complete());await page.getByRole('button',{name:'Stop generation',exact:true}).waitFor({state:'hidden'});await page.getByText(/STREAMED_PHONE_OUTPUT/).first().waitFor();
   assert.equal(await page.evaluate(()=>fixture.snapshot.activeConversationIds.length),0,'Completion clears active status');
   assert.equal(await page.getByText('OpenCore is working · press Stop to cancel',{exact:true}).count(),0,'Visible active status stops after completion');
   await page.getByLabel('Message OpenCore',{exact:true}).fill('CANCEL_FIXTURE_PROMPT');await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByRole('button',{name:'Stop generation',exact:true}).waitFor();
   await page.getByRole('button',{name:'Stop generation',exact:true}).click();await page.waitForFunction(()=>fixture.calls.some(c=>c.command==='cancel_chat_message'));await page.getByRole('button',{name:'Stop generation',exact:true}).waitFor({state:'hidden'});
   await page.screenshot({path:path.join(evidence,`${mode}-chat-${width}.png`)});
   const beforeChats=await page.evaluate(()=>fixture.snapshot.conversations.length);
   await nav.getByRole('button',{name:'Chats',exact:true}).click();
   assert.equal(await page.evaluate(()=>document.body.classList.contains('oc-chats-open')),false,'Chats tab keeps the current conversation; the history button opens the drawer');
   assert.equal(await page.evaluate(()=>fixture.snapshot.conversations.length),beforeChats,'Chats tab does not create another conversation');
   await page.locator('.section-header .section-heading button').click();await page.locator('.conversation-list').waitFor();assert.ok(await page.locator('.conversation-list .conversation-row').count()>=2,'Explicit history button opens existing conversations');
   await page.locator('.conversation-list .conversation-row').filter({hasText:'Generate image prompt ideas'}).first().click();await page.waitForFunction(()=>!document.body.classList.contains('oc-chats-open'));
   await nav.getByRole('button',{name:'Models',exact:true}).click();await page.getByRole('textbox',{name:'Search models',exact:true}).waitFor();await page.getByText('Fixture native model',{exact:true}).first().waitFor();
   await page.evaluate(()=>{fixture.progress={modelId:'echo',operationId:'phone-owned-download',phase:'downloading',downloadedBytes:1e9,totalBytes:5e9,currentFile:'model.gguf'};});
   await page.waitForFunction(()=>document.querySelectorAll('.model-install-progress').length===2);
   assert.equal(await page.getByText('1.000 GB / 5.000 GB',{exact:true}).count(),2,'The actual app shows bytes above Models and inside the owning card');
   assert.equal(await page.getByRole('button',{name:'Pause installation',exact:true}).count(),2);
   assert.equal(await page.getByRole('button',{name:'Cancel installation',exact:true}).count(),2);
   await page.waitForTimeout(2100);await page.evaluate(()=>{fixture.progress={...fixture.progress,downloadedBytes:2e9};});
   await page.getByText(/remaining · measured download rate/).first().waitFor();
   await page.screenshot({path:path.join(evidence,`${mode}-download-${width}.png`)});
   await page.getByLabel('Hugging Face repository',{exact:true}).fill('fixture');await page.getByText('Page 1 · 100 models',{exact:true}).waitFor();assert.equal(await page.locator('.hub-search-results>button').count(),100);
   await page.getByRole('button',{name:'Next page',exact:true}).click();await page.getByText('Page 2 · 100 models',{exact:true}).waitFor();assert.equal(await page.locator('.hub-search-results>button').count(),100);
   assert.equal(await page.evaluate(()=>fixture.calls.filter(c=>c.kind==='hub'&&c.path==='/api/search').at(-1).body.includes('second')),true);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Model/HF page does not overflow');await page.screenshot({path:path.join(evidence,`${mode}-models-${width}.png`)});
   await nav.getByRole('button',{name:'Chats',exact:true}).click();
   await page.getByLabel('Message OpenCore',{exact:true}).waitFor();
   assert.equal(await page.evaluate(()=>document.body.classList.contains('oc-chats-open')),false,'Returning from Models through Chats does not open history');
   await nav.getByRole('button',{name:'Settings',exact:true}).click();await page.locator('[data-phone-execution]').waitFor();
   assert.equal(await page.evaluate(()=>globalThis.__OPENCORE_PHONE__===true),mode==='phone','Execution metadata selects the correct device');
   const deviceDescription=(await page.locator('[data-phone-execution] p').innerText()).split('.')[0];
   assert.match(deviceDescription,mode==='phone'?/phone/i:/paired PC/i,'Initial sentence names the current execution device');
   if(mode==='phone')assert.doesNotMatch(deviceDescription,/paired PC/i,'Phone must not claim execution on PC');
   await page.getByRole('button',{name:'Choose Phone or Paired PC',exact:true}).click();await page.waitForFunction(()=>fixture.calls.some(c=>c.kind==='phone-execution-settings'));
   await nav.getByRole('button',{name:'More',exact:true}).click();const more=page.getByRole('dialog',{name:'All OpenCore sections'});await more.waitFor();const sections=await more.getByRole('button').allTextContents();for(const s of ['Chat Core','Overview','Conversations','Jobs','Spaces','Live Context','Memory / ECHO','Runtime & Logs','Models','API','Learning Studio','Music Studio','Game Dev Studio','Media Studio','Connectors','Settings','Troubleshooting'])assert.ok(sections.includes(s),'Full More section '+s);
   for(const [i,s]of ['Game Dev Studio','Media Studio','Music Studio'].entries()){if(i)await nav.getByRole('button',{name:'More',exact:true}).click();await more.getByRole('button',{name:s,exact:true}).click();await page.locator('.section-header').getByText(s,{exact:true}).waitFor();}
   assert.deepEqual(errors,[],'No browser runtime errors');assert.equal(requests.some(url=>!url.startsWith(`http://127.0.0.1:${server.address().port}/`)&&!url.startsWith('data:')),false,'Only local bridge fixture requests');
   results.push({mode,width,normalHeight:804,shortUsableHeight:540,sharedReact:true,streaming:true,completion:true,hfPages:2});console.log(`Shared desktop UI ${mode} ${width}px passed`);
  }catch(error){console.error(`${mode}-${width}:`,error);console.error('Browser errors:',errors);console.error((await page.locator('body').innerText()).slice(0,5000));await page.screenshot({path:path.join(evidence,`${mode}-${width}-failure.png`)});throw error;}finally{await page.close();}
 }
 fs.writeFileSync(path.join(evidence,'results.json'),JSON.stringify({proof:'Browser fixture, not physical Android/JNI',dist,startupRegressions,results},null,2));console.log('Shared desktop Phone/Paired PC UI passed. Browser fixtures only; real native inference requires Android evidence.');
 }finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
