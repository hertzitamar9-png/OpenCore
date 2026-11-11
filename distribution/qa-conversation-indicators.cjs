// Exercise the shipping sidebar with disposable history at the native bridge.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const source=fs.readFileSync(path.join(__dirname,'test-mobile-render.cjs'),'utf8');
let code=source.slice(0,source.indexOf('(async()=>{'));
code=code.replace('<link rel="stylesheet" href="/phone/mobile.css">','<link rel="stylesheet" href="/phone/mobile.css" media="(max-width:767px)">');
code=code.replace("globalThis.__OPENCORE_MOBILE__=true", "globalThis.__OPENCORE_MOBILE__=false");
code=code.replace("res.end(fs.readFileSync(file));","res.end(name==='/phone/mobile-shim.js'?fs.readFileSync(file,'utf8').replace('globalThis.__OPENCORE_MOBILE__=true','globalThis.__OPENCORE_MOBILE__=false'):fs.readFileSync(file));");
code=code.replace("else if(command==='get_conversation')value=fixture.timeline;",`else if(command==='get_conversation'){
  if(fixture.blockHistory===args.id)await new Promise(resolve=>(fixture.waitingHistory||=[]).push(resolve));
  value=fixture.timeline.map(entry=>({...entry,conversationId:args.id}));
}`);
code+=String.raw`
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,args:['--mute-audio'],...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const base=snapshot.conversations[0];
  snapshot.conversations=[{...base,id:'idle',title:'Idle chat',status:'error',messageCount:1},
    {...base,id:'working',title:'Working task',status:'complete',messageCount:2},
    {...base,id:'finished',title:'Finished task',status:'complete',messageCount:2}];
  snapshot.activeConversationIds=['working'];
  try{
    await page.addInitScript(fake,{snapshot,timeline});
    await page.goto('http://127.0.0.1:'+server.address().port+'/index.html');
    await page.locator('.opencore-shell').waitFor({timeout:15000});
    await page.getByRole('navigation',{name:'OpenCore sections'}).getByRole('button',{name:'Conversations',exact:true}).click();
    const row=title=>page.locator('button.conversation-row').filter({hasText:title}).first();
    const dot=title=>row(title).locator('.client-dot');
    await row('Finished task').waitFor();
    assert.equal(await dot('Working task').getAttribute('data-state'),'working');
    assert.equal(await dot('Finished task').getAttribute('data-state'),'unread');
    assert.equal(await dot('Idle chat').getAttribute('data-state'),'idle');
    assert.equal(await dot('Idle chat').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(140, 153, 169)');
    assert.equal(await dot('Finished task').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(251, 191, 36)');
    await page.evaluate(()=>{fixture.blockHistory='finished';});
    await row('Finished task').click();
    await page.waitForFunction(()=>fixture.waitingHistory?.length);
    assert.equal(await dot('Finished task').getAttribute('data-state'),'unread');
    await page.evaluate(()=>{fixture.blockHistory=null;fixture.waitingHistory.splice(0).forEach(resolve=>resolve());});
    await page.waitForFunction(()=>[...document.querySelectorAll('.conversation-row')].find(el=>el.textContent.includes('Finished task'))?.querySelector('.client-dot').dataset.state==='idle');
    await page.evaluate(()=>{fixture.snapshot.activeConversationIds=[];fixture.snapshot.conversations.find(row=>row.id==='working').messageCount=4;});
    await page.waitForFunction(()=>[...document.querySelectorAll('.conversation-row')].find(el=>el.textContent.includes('Working task'))?.querySelector('.client-dot').dataset.state==='unread');
    await page.screenshot({path:path.join(process.env.OPENCORE_INDICATOR_PROOF||require('os').tmpdir(),'conversation-indicators-desktop.png')});
    assert.deepEqual(errors,[]);
    // The disposable mobile transport normally receives persisted preferences
    // from its PC peer on reload. Seed that same round trip in this fixture.
    const preferences=await page.evaluate(()=>Object.fromEntries(Object.keys(localStorage).filter(key=>key.startsWith('opencore.')).map(key=>[key,localStorage.getItem(key)])));
    assert.ok(preferences['opencore.conversation-read.v1']);
    await page.addInitScript(value=>{globalThis.__OPENCORE_PC_PREFS__=value;},preferences);
    await page.reload();
    await page.locator('.opencore-shell').waitFor();
    await page.getByRole('navigation',{name:'OpenCore sections'}).getByRole('button',{name:'Conversations',exact:true}).click();
    await row('Finished task').waitFor();
    assert.equal(await dot('Finished task').getAttribute('data-state'),'idle');
    console.log('Shipping sidebar: green active only, yellow unread, brighter gray, delayed-load acknowledgement and persisted read state passed');
  }catch(error){
    console.error(JSON.stringify({dots:await page.locator('.conversation-row').evaluateAll(rows=>rows.map(row=>({title:row.textContent,state:row.querySelector('.client-dot')?.dataset.state}))),calls:await page.evaluate(()=>fixture.calls.filter(call=>call.command==='get_conversation').slice(-8))}));
    throw error;
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});`;
if(process.env.OPENCORE_INDICATOR_PROOF)fs.writeFileSync(path.join(process.env.OPENCORE_INDICATOR_PROOF,'indicator-qa-source.cjs'),code);
const module_=new Module(__filename,module);module_.filename=__filename;module_.paths=module.paths;module_._compile(code,__filename);
