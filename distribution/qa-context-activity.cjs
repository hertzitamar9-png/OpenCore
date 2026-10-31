// Render the shipped frontend and replay delayed native events at the real
// bridge boundary. This fixture never reads or changes installed conversations.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const source=fs.readFileSync(path.join(__dirname,'test-mobile-render.cjs'),'utf8');
let code=source.slice(0,source.indexOf('(async()=>{'));
code=code.replace('<link rel="stylesheet" href="/phone/mobile.css">','<link rel="stylesheet" href="/phone/mobile.css" media="(max-width:767px)">');
code=code.replace('res.end(fs.readFileSync(file));',"res.end(name==='/phone/mobile-shim.js'?fs.readFileSync(file,'utf8').replace('globalThis.__OPENCORE_MOBILE__=true','globalThis.__OPENCORE_MOBILE__=innerWidth<768'):fs.readFileSync(file));");
code=code.replace("else if(command==='get_conversation')value=fixture.timeline;", "else if(command==='get_conversation')value=fixture.timeline;else if(command==='echo_working_set')value=fixture.context;");
code=code.replace('channel.port1.start();window.postMessage',"fixture.emit=(event,payload)=>channel.port1.postMessage(JSON.stringify({type:'event',handler:fixture.listeners[event],payload:{event,payload}}));channel.port1.start();window.postMessage");
code=code.replace("const body=request.path==='/api/status'?","const body=request.path==='/api/telemetry'?{telemetry:fixture.liveHardware||{},context:fixture.context}:request.path==='/api/status'?");
code+=String.raw`
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
  const proof=[],folder=process.env.OPENCORE_CONTEXT_PROOF||require('os').tmpdir();let page;
  const entry=(id,kind,content,metadata={})=>({id,kind,content,metadata,conversationId:'preview',role:kind==='tool_result'?'tool':id===1?'user':'assistant',timestamp:new Date(Date.UTC(2026,9,31,0,0,id)).toISOString(),source:'OpenCore',title:kind==='tool_call'?'Find game files':kind});
  const user=entry(1,'message','Inspect the game files.');
  const call=entry(3,'tool_call','{"id":"find-1","name":"find_game","arguments":{"pattern":"*.html"}}',{id:'find-1',tool:'find_game'});
  const result=entry(4,'tool_result','{"output":"Found game.html","status":"completed"}',{toolCallId:'find-1'});
  snapshot.runtime.profile='frognano-4b';snapshot.runtime.contextSize=32768;snapshot.activeConversationIds=['preview'];
  try{
    for(const width of [1366,393]){
      page=await browser.newPage({viewport:{width,height:852},isMobile:width<500,hasTouch:width<500});const errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.addInitScript(fake,{snapshot,timeline:[user]});
      await page.addInitScript(()=>{document.addEventListener('DOMContentLoaded',()=>{fixture.context={available:true,active:true,promptTokens:12400,liveTokens:11000,windowTokens:32768,contextMode:'persistent_echo'};});});
      await page.goto('http://127.0.0.1:'+server.address().port+'/index.html');
      await page.locator('.opencore-shell').waitFor({timeout:15000});
      await page.waitForFunction(()=>fixture.listeners['opencore-generation']&&fixture.emit);
      const meter=page.locator('progress[aria-label="Model context usage"]');
      await page.waitForFunction(()=>document.querySelector('progress[aria-label="Model context usage"]')?.getAttribute('value')==='12400');
      assert.equal(await meter.getAttribute('max'),'32768');
      await page.evaluate(()=>{fixture.liveHardware={gpuUtilization:87,vramUsedMib:5120,tokensPerSecond:35,completionTokens:81};fixture.context={...fixture.context,modelActiveTokens:12480,modelUsageKind:'live',modelOutputTokens:81,modelSessionActive:true};});
      await page.waitForFunction(()=>document.querySelector('progress[aria-label="Model context usage"]')?.getAttribute('value')==='12480');
      const footer=page.locator('footer.statusbar');
      await footer.getByText('GPU 87%',{exact:true}).waitFor({state:'attached'});
      assert.ok((await footer.innerText()).includes('5.0GB VRAM'));
      assert.ok((await footer.innerText()).includes('35.0 tokens/s'));
      assert.ok((await footer.innerText()).includes('81 out'));
      await page.evaluate(()=>{fixture.context={...fixture.context,modelActiveTokens:12520,modelOutputTokens:121};fixture.liveHardware={gpuUtilization:63,vramUsedMib:6144,tokensPerSecond:40,completionTokens:121};});
      await page.waitForFunction(()=>document.querySelector('progress[aria-label="Model context usage"]')?.getAttribute('value')==='12520');
      await footer.getByText('GPU 63%',{exact:true}).waitFor({state:'attached'});
      assert.ok((await footer.innerText()).includes('6.0GB VRAM'));
      assert.ok((await footer.innerText()).includes('40.0 tokens/s'));
      await page.getByText('40.0 output tokens/s · 121 output tokens',{exact:true}).waitFor({state:'attached'});
      if(width>767){
        const nav=page.locator('.conversation-app-rail .nav-item.active').first(),row=page.locator('.conversation-row-shell.selected').first();
        const fill=element=>getComputedStyle(element).backgroundColor;
        assert.equal(await nav.evaluate(fill),await row.evaluate(fill),'Navigation and selected chats must use one highlight fill');
        const memory=page.locator('.conversation-app-rail .nav-item').nth(5);await memory.hover();
        await page.waitForFunction(()=>{const nav=document.querySelectorAll('.conversation-app-rail .nav-item')[5],row=document.querySelector('.conversation-row-shell.selected');return nav&&row&&getComputedStyle(nav).backgroundColor===getComputedStyle(row).backgroundColor;});
        assert.equal(await memory.evaluate(fill),await row.evaluate(fill),'Hover and selection must share the highlight color');
      }
      if(width>767)assert.ok(await meter.isVisible(),'The desktop live context bar must be visible');
      if(width>767){
        const selected=page.locator('.conversation-row-shell.selected'),other=page.locator('.conversation-row-shell:not(.selected)').first();
        await other.hover();
        assert.equal(await selected.count(),1,'Hover must not select another chat');
        assert.notEqual(await selected.evaluate(e=>getComputedStyle(e).borderColor),await other.evaluate(e=>getComputedStyle(e).borderColor),'Unselected chat must not copy selected border');
        const input=page.getByRole('textbox',{name:'Message OpenCore',exact:true});await input.focus();
        assert.equal(await input.evaluate(e=>getComputedStyle(e).outlineStyle),'none','Typing must not outline the composer');
      }
      await page.evaluate(({call,result})=>{fixture.timeline.push(call,result);fixture.emit('opencore-generation',{conversationId:'preview',runId:'qa-run',checkpoint:true,phase:'tool',segments:[]});},{call,result});
      await page.getByText(/Used\s*1\s*tool/).first().waitFor({timeout:10000});
      await page.evaluate(()=>fixture.emit('opencore-generation',{conversationId:'preview',runId:'qa-run',phase:'reasoning',segments:[{kind:'thinking',itemId:'reason-1',content:'I will inspect the file that was found.'}]}));
      await page.getByText(/I will inspect the file that was found/).first().waitFor({state:'attached',timeout:10000});
      // A checkpoint read and both polling loops now see an older snapshot.
      await page.evaluate(()=>{fixture.timeline=fixture.timeline.slice(0,1);fixture.emit('opencore-generation',{conversationId:'preview',runId:'qa-run',checkpoint:true,phase:'tool',segments:[]});});
      await page.waitForTimeout(1600);
      await page.evaluate(()=>fixture.emit('opencore-generation',{conversationId:'preview',runId:'qa-run',phase:'reasoning',segments:[{kind:'thinking',itemId:'reason-2',content:'Now I can validate the next step.'}]}));
      await page.getByText(/Now I can validate the next step/).first().waitFor({state:'attached',timeout:10000});
      assert.ok(await page.getByText(/Used\s*1\s*tool/).first().isVisible(),'tool group disappeared after a stale poll');
      assert.ok(await page.getByText(/I will inspect the file that was found/).count(),'earlier reasoning disappeared after checkpoint');
      // Save both items, then complete. The persisted copy replaces the preview.
      await page.evaluate(({user,call,result,thinking,next})=>{fixture.timeline=[user,call,result,thinking,next];fixture.snapshot.activeConversationIds=[];fixture.emit('opencore-generation',{conversationId:'preview',runId:'qa-run',done:true});},{user,call,result,thinking:entry(5,'thinking','I will inspect the file that was found.',{itemId:'reason-1'}),next:entry(6,'thinking','Now I can validate the next step.',{itemId:'reason-2'})});
      await page.waitForTimeout(400);
      assert.ok(await page.getByText(/Used\s*1\s*tool/).first().isVisible());
      const cards=await page.getByText('I will inspect the file that was found.',{exact:true}).evaluateAll(items=>new Set(items.map(item=>item.closest('details'))).size);
      assert.equal(cards,1,'The saved reasoning and its preview must share one disclosure');
      await page.evaluate(()=>{fixture.snapshot.runtime.status='stopped';fixture.context={available:false,windowTokens:65536};});
      await page.waitForFunction(()=>{const bar=document.querySelector('progress[aria-label="Model context usage"]');return bar?.getAttribute('max')==='32768'&&!bar.hasAttribute('value');});
      assert.deepEqual(errors,[]);
      await page.screenshot({path:path.join(folder,'context-activity-'+width+'.png')});
      proof.push({width,contextUsed:12400,contextCapacity:32768,toolRetainedAcrossStalePoll:true,reasoningRetainedAcrossCheckpoint:true,savedPreviewDeduplicated:true,unloadedCatalogLimitVerified:true,liveContextSamples:[12480,12520],liveHardwareSamples:[87,63],liveOutputRates:[35,40],sharedHighlightsVerified:width>767,errors});
      await page.close();
    }
    fs.writeFileSync(path.join(folder,'context-activity-render.json'),JSON.stringify({passed:true,proof},null,2));
    console.log(JSON.stringify({passed:true,proof}));
  }catch(error){console.error(error);if(page){console.error((await page.locator('body').innerText()).slice(-5000));await page.screenshot({path:path.join(folder,'context-activity-error.png')});}process.exitCode=1;}
  finally{await browser.close();server.close();}
})();`;
const compiled=new Module(__filename,module);compiled.filename=__filename;compiled.paths=module.paths;compiled._compile(code,__filename);
