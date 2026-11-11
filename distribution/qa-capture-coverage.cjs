// Verify the shipped receipt and empty-chat composer against the reported 101-note payload.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
let fixture=fs.readFileSync(path.join(__dirname,'qa-chat-image-picker.cjs'),'utf8').split('\n(async()=>')[0];
const marker="case 'workspace_files':return";
fixture=fixture.replace(marker,marker+" fixture.receipt??");
fixture=fixture.replace("window.__TAURI_EVENT_PLUGIN_INTERNALS__=", "fixture.receipt={turnId:null,status:'completed',files:[],added:0,removed:0,coverage:Array.from({length:101},(_,index)=>'Capture byte limit 67108864 bytes reached: internal/runtime/file-'+index+'.ts')};window.__TAURI_EVENT_PLUGIN_INTERNALS__=");
fixture+=String.raw`
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,args:['--mute-audio']}),page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.getByLabel('Message OpenCore').waitFor();
  await page.waitForFunction(()=>fixture.calls.some(call=>call.command==='workspace_files'));
  assert.equal(await page.locator('.workspace-coverage').count(),0,'A new chat must not show coverage-only diagnostics');
  const output=process.env.OPENCORE_COVERAGE_QA_DIR;if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'new-chat-desktop.png')});}
  await page.evaluate(()=>{fixture.receipt={...fixture.receipt,turnId:'qa-turn',files:[{id:'real-file',conversationId:'qa-chat',turnId:'qa-turn',path:'game.html',source:'C:/project/game.html',change:'created',mime:'text/html',added:30,removed:0}],added:30};window.dispatchEvent(new Event('opencore-file-changes'));});
  const changes=page.getByRole('region',{name:'Task file changes'});
  await changes.getByRole('button',{name:'Show all file changes'}).waitFor();
  assert.equal(await changes.count(),1,'The empty composer must not mount a second receipt');
  assert.equal(await page.locator('.workspace-coverage').count(),0,'Collapsed file changes must keep diagnostics out of the chat');
  await changes.getByRole('button',{name:'Show all file changes'}).click();
  await changes.getByRole('button',{name:/game.html/}).waitFor();
  assert.equal(await changes.locator('.workspace-coverage').count(),0,'File changes must not expose capture diagnostics');
  await page.evaluate(()=>{fixture.receipt.files.push({id:'cache',path:'C:/ProgramData/Microsoft/Windows/Caches/cversions.2.db',source:'Windows',mime:'application/octet-stream',added:null,removed:null},{id:'snip',path:'C:/Users/test/AppData/Local/Packages/Microsoft.ScreenSketch_8wekyb3d8bbwe/TempState/clip.png',source:'Windows',mime:'image/png',added:null,removed:null});window.dispatchEvent(new Event('opencore-file-changes'));});
  for(const width of [390,1400]){
   await page.setViewportSize({width,height:width===390?844:1000});
   assert.equal(await page.getByText(/Windows process cache|Capture coverage|cversions|ScreenSketch/).count(),0,'Internal Windows cache records stay out of the chat');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   if(output)await page.screenshot({path:path.join(output,'diagnostics-'+width+'.png')});
  }
  await changes.getByRole('button',{name:'Collapse file changes'}).click();
  assert.equal(await page.locator('.workspace-coverage').count(),0);
  await page.evaluate(()=>{fixture.receipt={...fixture.receipt,turnId:'empty-turn',files:[],added:0};window.dispatchEvent(new Event('opencore-file-changes'));});
  await page.waitForFunction(()=>!document.querySelector('.file-changes-receipt'));
  assert.equal(await page.locator('.workspace-coverage').count(),0);
  assert.deepEqual(errors,[]);
  console.log('PASS: real task files remain available; Windows cache, Snip Sketch and capture diagnostics stay hidden at desktop and phone widths.');
 }catch(error){console.error('Coverage fixture errors',errors);console.error((await page.locator('body').innerText()).slice(0,1600));throw error;}finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1});`;
const filename=path.join(__dirname,'qa-capture-coverage.fixture.cjs'),compiled=new Module(filename,module);compiled.filename=filename;compiled.paths=Module._nodeModulePaths(__dirname);compiled._compile(fixture,filename);
