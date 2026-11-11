// Exercise status transitions in the shipped conversation list with disposable data.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const source=fs.readFileSync(path.join(__dirname,'test-mobile-render.cjs'),'utf8');
let code=source.slice(0,source.indexOf('(async()=>{'));
code=code.replace('<link rel="stylesheet" href="/phone/mobile.css">','<link rel="stylesheet" href="/phone/mobile.css" media="(max-width:767px)">');
code=code.replace('res.end(fs.readFileSync(file));',"res.end(name==='/phone/mobile-shim.js'?fs.readFileSync(file,'utf8').replace('globalThis.__OPENCORE_MOBILE__=true','globalThis.__OPENCORE_MOBILE__=innerWidth<768'):fs.readFileSync(file));");
code+=String.raw`
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
 const folder=process.env.OPENCORE_STATUS_PROOF||require('os').tmpdir(),proof=[];let page;
 snapshot.conversations=['active','running','completed','stop','error','interrupted','draft'].map((status,index)=>({
  ...snapshot.conversations[0],id:index===0?'preview':'status-'+index,title:'Chat status '+status,status,pinned:false,project:'OpenCore'}));
 snapshot.activeConversationIds=['status-4'];
 try{
  for(const width of [1366,393]){
   page=await browser.newPage({viewport:{width,height:900}});const errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.addInitScript(fake,{snapshot,timeline});await page.goto('http://127.0.0.1:'+server.address().port);
   await page.getByRole('button',{name:'Open conversations',exact:true}).click();
   await page.getByLabel('Message OpenCore').waitFor({timeout:20000});
   if(width<768)await page.locator('.section-header .section-heading button').click();
   const dot=status=>page.locator('.conversation-list .conversation-row').filter({hasText:'Chat status '+status}).locator('.client-dot');
   const color=async status=>dot(status).evaluate(element=>getComputedStyle(element).backgroundColor);
   const green=await color('error'),yellow=await color('completed');
   assert.notEqual(green,'rgba(0, 0, 0, 0)');
   assert.notEqual(yellow,green);
   assert.equal(await color('stop'),yellow,'Unread completed chats must be yellow');
   for(const status of ['active','running','interrupted','draft'])assert.notEqual(await color(status),green,'Only current active work may be green');
   await page.evaluate(()=>{fixture.snapshot.activeConversationIds=[];});
   await page.waitForFunction(green=>{const row=[...document.querySelectorAll('.conversation-list .conversation-row')].find(element=>element.textContent.includes('Chat status error'));return row&&getComputedStyle(row.querySelector('.client-dot')).backgroundColor!==green;},green);
   await page.evaluate(()=>{fixture.snapshot.conversations.find(item=>item.id==='status-4').status='completed';});
   await page.waitForFunction(yellow=>{const row=[...document.querySelectorAll('.conversation-list .conversation-row')].find(element=>element.textContent.includes('Chat status error'));return row&&getComputedStyle(row.querySelector('.client-dot')).backgroundColor===yellow;},yellow);
   assert.deepEqual(errors,[]);
   await page.screenshot({path:path.join(folder,'conversation-status-'+width+'.png')});
   proof.push({width,green,yellow,activeOnly:true,unreadCompletion:true,liveTransitions:true,errors});await page.close();
  }
  fs.writeFileSync(path.join(folder,'conversation-status-render.json'),JSON.stringify({passed:true,proof},null,2));console.log(JSON.stringify({passed:true,proof}));
 }catch(error){console.error(error);if(page)await page.screenshot({path:path.join(folder,'conversation-status-error.png')});process.exitCode=1;}
 finally{await browser.close();server.close();}
})();`;
const compiled=new Module(__filename,module);compiled.filename=__filename;compiled.paths=module.paths;compiled._compile(code,__filename);
