// Replay real native bridge events into the shipped UI with disposable history.
// No installed data, model, account, or app process is used by this fixture.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const harness=fs.readFileSync(path.join(__dirname,'test-mobile-render.cjs'),'utf8');
let code=harness.slice(0,harness.indexOf('(async()=>{'));
code=code.replace('<link rel="stylesheet" href="/phone/mobile.css">','<link rel="stylesheet" href="/phone/mobile.css" media="(max-width:767px)">');
code=code.replace('res.end(fs.readFileSync(file));',"res.end(name==='/phone/mobile-shim.js'?fs.readFileSync(file,'utf8').replace('globalThis.__OPENCORE_MOBILE__=true','globalThis.__OPENCORE_MOBILE__=innerWidth<768'):fs.readFileSync(file));");
code=code.replace('channel.port1.start();window.postMessage',"fixture.emit=(event,payload)=>channel.port1.postMessage(JSON.stringify({type:'event',handler:fixture.listeners[event],payload:{event,payload}}));channel.port1.start();window.postMessage");
code=code.replace("else if(command==='get_conversation')value=fixture.timeline;", "else if(command==='get_conversation'){value=fixture.timelines?.[args.id]||fixture.timeline;if(fixture.historyDelay?.[args.id])await new Promise(resolve=>setTimeout(resolve,fixture.historyDelay[args.id]));fixture.historyCompleted||={};fixture.historyCompleted[args.id]=(fixture.historyCompleted[args.id]||0)+1;}");
code+=String.raw`
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
 const folder=process.env.OPENCORE_ACTIVITY_PROOF||require('os').tmpdir(),proof=[];let page;
 const entry=(id,kind,content,metadata={})=>({id,kind,content,metadata,conversationId:'preview',role:kind==='tool_result'?'tool':id===1?'user':'assistant',timestamp:new Date(Date.UTC(2026,10,2,0,0,id)).toISOString(),source:'OpenCore',title:kind==='tool_call'?'qa_exec_tool':kind});
 const user=entry(1,'message','Check all streamed activity.');
 const call=entry(2,'tool_call','{"id":"qa-one","name":"qa_exec_tool","arguments":{"cmd":"verify-fixture"}}',{id:'qa-one',tool:'qa_exec_tool'});
 const nextCall={...entry(7,'tool_call','{"id":"qa-two","name":"qa_offscreen_tool","arguments":{"path":"fixture.txt"}}',{id:'qa-two',tool:'qa_offscreen_tool'}),title:'qa_offscreen_tool'};
 const first='FIRST_OUTPUT\n'+'x'.repeat(66000),complete=first+'\nFINAL_OUTPUT';
 const summaryOne='Supplied external summary one.',summaryTwo='Supplied external summary two.';
 snapshot.runtime.profile='frognano-4b';snapshot.runtime.contextSize=32768;snapshot.activeConversationIds=['preview'];
 try{
  for(const width of [1366,393]){
   page=await browser.newPage({viewport:{width,height:852},isMobile:width<500,hasTouch:width<500});const errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   await page.addInitScript({content:'('+fake.toString()+')('+JSON.stringify({snapshot,timeline:[user]})+');if(window.name){fixture.timeline=JSON.parse(window.name);fixture.snapshot.activeConversationIds=[];}'});
   await page.goto('http://127.0.0.1:'+server.address().port+'/index.html');
   await page.locator('.opencore-shell').waitFor({timeout:15000});
   await page.waitForFunction(()=>fixture.emit&&fixture.listeners['opencore-tool-progress']&&fixture.listeners['opencore-generation']);
   await page.getByRole('navigation',{name:'OpenCore sections'}).getByRole('button',{name:'Conversations',exact:true}).click();
   await page.getByRole('textbox',{name:'Message OpenCore',exact:true}).waitFor();
   const emit=async(event,payload)=>page.evaluate(({event,payload})=>fixture.emit(event,payload),{event,payload});
   const expand=async()=>{for(const group of await page.locator('details.tool-group').all())if(!await group.evaluate(e=>e.open))await group.locator(':scope>summary').click();for(const activity of await page.locator('details.tool-activity').all())if(!await activity.evaluate(e=>e.open))await activity.locator(':scope>summary').click();};
   const checkFirst=async()=>{await expand();const activity=page.locator('.tool-activity').filter({hasText:'qa_exec_tool'}).first();await activity.waitFor();const text=await activity.innerText();assert.ok(text.includes('FIRST_OUTPUT'),'Early output disappeared');assert.ok(text.includes('FINAL_OUTPUT'),'Final output disappeared');assert.ok(text.includes('verify-fixture'),'Tool arguments disappeared');assert.ok(await activity.isVisible());};
   await emit('opencore-tool-progress',{conversationId:'preview',toolCallId:'qa-one',call,title:'qa_exec_tool',status:'running',delta:first});
   await page.getByText(/Using\s*1\s*tool/).first().waitFor();await expand();await page.getByText('Running',{exact:true}).waitFor();
   await emit('opencore-tool-progress',{conversationId:'preview',toolCallId:'qa-one',status:'completed',delta:'\nFINAL_OUTPUT',result:{output:complete,status:'completed',exitCode:0}});
   await page.getByText(/Used\s*1\s*tool/).first().waitFor();await checkFirst();
   await emit('opencore-generation',{conversationId:'preview',runId:'qa-run',phase:'reasoning',segments:[{kind:'thinking',itemId:'qa-summary-1',content:summaryOne,reasoningSource:'summary'}]});
   await page.getByText(summaryOne,{exact:true}).first().waitFor({state:'attached'});
   await page.evaluate(({user,call})=>{fixture.timeline=[user,call,{id:3,conversationId:'preview',kind:'tool_result',role:'tool',source:'OpenCore',title:'Tool result',timestamp:new Date().toISOString(),content:'{"output":"FIRST_OUTPUT","status":"running"}',metadata:{toolCallId:'qa-one'}}];},{user,call});
   await emit('opencore-generation',{conversationId:'preview',runId:'qa-run',checkpoint:true,phase:'tool',segments:[]});
   await emit('opencore-generation',{conversationId:'preview',runId:'qa-run',phase:'reasoning',segments:[{kind:'thinking',itemId:'qa-summary-2',content:summaryTwo,reasoningSource:'summary'}]});
   await page.getByText(summaryTwo,{exact:true}).first().waitFor({state:'attached'});await checkFirst();assert.equal(await page.getByText('Running',{exact:true}).count(),0);
   // The composer unmounts on another section; the native stream keeps arriving.
   await emit('opencore-navigate',{view:'models'});await page.locator('.section-header').getByText('Models',{exact:true}).waitFor();
   await emit('opencore-tool-progress',{conversationId:'preview',toolCallId:'qa-two',call:nextCall,title:'qa_offscreen_tool',status:'running',delta:'OFFSCREEN_FIRST'});
   await emit('opencore-tool-progress',{conversationId:'preview',toolCallId:'qa-two',status:'completed',delta:'\nOFFSCREEN_LAST',result:{status:'completed',exitCode:0}});
   await emit('opencore-navigate',{view:'conversations'});await page.getByRole('textbox',{name:'Message OpenCore',exact:true}).waitFor();
   await page.waitForFunction(()=>document.querySelectorAll('.tool-activity').length===2);await checkFirst();await expand();
   assert.ok((await page.locator('.tool-activity').filter({hasText:'qa_offscreen_tool'}).innerText()).includes('OFFSCREEN_LAST'));
   await page.evaluate(()=>fixture.snapshot.activeConversationIds=[]);
   await emit('opencore-generation',{conversationId:'preview',runId:'qa-run',done:true});
   await page.getByText(summaryOne,{exact:true}).first().waitFor({state:'attached'});await page.getByText(summaryTwo,{exact:true}).first().waitFor({state:'attached'});await checkFirst();
   const saved=[user,call,entry(3,'tool_result',JSON.stringify({output:complete,status:'completed',exitCode:0}),{toolCallId:'qa-one'}),entry(4,'thinking',summaryOne,{itemId:'qa-summary-1',reasoningSource:'summary'}),entry(5,'thinking',summaryTwo,{itemId:'qa-summary-2',reasoningSource:'summary'}),nextCall,entry(8,'tool_result','{"output":"OFFSCREEN_FIRST\\nOFFSCREEN_LAST","status":"completed","exitCode":0}',{toolCallId:'qa-two'}),entry(9,'message','All fixture checks finished.')];
   await page.evaluate(saved=>fixture.timeline=saved,saved);await emit('opencore-generation',{conversationId:'preview',runId:'qa-run',done:true});
   await page.getByText('All fixture checks finished.',{exact:true}).waitFor();await checkFirst();
   assert.equal(await page.getByText(summaryOne,{exact:true}).count(),1);assert.equal(await page.getByText(summaryTwo,{exact:true}).count(),1);
   await page.evaluate(()=>{window.name=JSON.stringify(fixture.timeline)});await page.reload();
   await page.locator('.opencore-shell').waitFor({timeout:15000});
   await page.getByRole('navigation',{name:'OpenCore sections'}).getByRole('button',{name:'Conversations',exact:true}).click();
   await page.getByText('All fixture checks finished.',{exact:true}).waitFor();
   await page.waitForFunction(()=>document.querySelectorAll('.tool-activity').length===2);await checkFirst();await expand();
   assert.ok((await page.locator('.tool-activity').filter({hasText:'qa_offscreen_tool'}).innerText()).includes('OFFSCREEN_LAST'));
   assert.equal(await page.getByText(summaryOne,{exact:true}).count(),1);assert.equal(await page.getByText(summaryTwo,{exact:true}).count(),1);
   // A background completion races a newer update in the visible second chat.
   const other=await page.evaluate(()=>fixture.snapshot.conversations.find(item=>item.id!=='preview'));
   assert.ok(other,'A second disposable conversation is required');
   await page.evaluate(other=>{fixture.timelines={[other.id]:[{id:101,conversationId:other.id,kind:'message',role:'user',source:'OpenCore',title:'You',content:'Second chat fixture',timestamp:new Date().toISOString(),metadata:{}}]};},other);
   const choose=async title=>{const row=page.locator('.conversation-list .conversation-row').filter({hasText:title}).first();if(!await row.isVisible()){const show=page.getByRole('button',{name:'Show conversations',exact:true});if(await show.count())await show.click();}if(await row.isVisible())await row.click();else await row.evaluate(element=>element.click());};
   const backgroundSummary='Supplied summary while switching chats.',visibleSummary='Visible other chat summary. More detail.';
   await emit('opencore-generation',{conversationId:'preview',runId:'cross-a',phase:'reasoning',segments:[{kind:'thinking',itemId:'cross-a-summary',content:backgroundSummary,reasoningSource:'summary'}]});
   await choose(other.title);
   await emit('opencore-generation',{conversationId:other.id,runId:'cross-b',phase:'reasoning',segments:[{kind:'thinking',itemId:'cross-b-summary',content:'Visible other chat summary.',reasoningSource:'summary'}]});
   await page.getByText('Visible other chat summary.',{exact:true}).waitFor({state:'attached'});
   const reads=await page.evaluate(()=>{fixture.historyDelay={preview:400};return fixture.historyCompleted?.preview||0;});
   await emit('opencore-generation',{conversationId:'preview',runId:'cross-a',done:true});
   await emit('opencore-generation',{conversationId:other.id,runId:'cross-b',phase:'reasoning',segments:[{kind:'thinking',itemId:'cross-b-summary',content:visibleSummary,reasoningSource:'summary'}]});
   await page.waitForFunction(reads=>(fixture.historyCompleted?.preview||0)>reads,reads);
   await page.getByText(visibleSummary,{exact:true}).waitFor({state:'attached'});
   const originalTitle=await page.evaluate(()=>fixture.snapshot.conversations.find(item=>item.id==='preview').title);
   await choose(originalTitle);
   await page.getByText(user.content,{exact:true}).waitFor({state:'attached'});await checkFirst();
   const completed=page.locator('.reasoning-disclosure').filter({hasText:backgroundSummary});await completed.waitFor({state:'attached'});
   assert.equal(await completed.locator('summary>strong').innerText(),'Reasoned','Background completion stayed cached as live after returning to its chat');
   await completed.locator(':scope>summary').scrollIntoViewIfNeeded();if(!await completed.evaluate(element=>element.open))await completed.locator(':scope>summary').click();await page.getByText(backgroundSummary,{exact:true}).waitFor({state:'visible'});
   assert.deepEqual(errors,[]);
   await page.screenshot({path:path.join(folder,'activity-retention-'+width+'.png')});
   proof.push({width,streamedPrefixAndSuffix:true,monotonicCompletion:true,offscreenToolRetained:true,summariesRetainedAtDelayedDone:true,toolsExpandableAfterReload:true,persistedSummariesDeduplicated:true,crossConversationCachedCompletion:true,visibleOtherChatPreserved:true,errors});await page.close();
  }
  fs.writeFileSync(path.join(folder,'activity-retention-render.json'),JSON.stringify({passed:true,proof},null,2));console.log(JSON.stringify({passed:true,proof}));
 }catch(error){console.error(error);if(page){console.error((await page.locator('body').innerText()).slice(-1200));await page.screenshot({path:path.join(folder,'activity-retention-error.png')});}process.exitCode=1;}
 finally{await browser.close();server.close();}
})();`;
const compiled=new Module(__filename,module);compiled.filename=__filename;compiled.paths=module.paths;compiled._compile(code,__filename);
