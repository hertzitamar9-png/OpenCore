// Browser plugin unavailable. Render the shipped surfaces with disposable IPC and HTTP fixtures.
const fs=require('fs'),path=require('path'),Module=require('module');
const source=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=source.slice(0,source.indexOf('(async()=>{await new Promise'));
code=code.replace('$hubInvoke as TestInvoke','$hubInvoke as TestInvoke,$RemoteSettings as TestRemote,$PrivateChatLauncher as TestPrivate');
code=code.replace('TestModels,TestHubSettings}', 'TestModels,TestHubSettings,TestRemote,TestPrivate}');
code=code.replace('React.createElement(TestHubSettings)))','React.createElement(TestHubSettings),React.createElement(TestRemote),React.createElement(TestPrivate)))');
const extra=String.raw`
const originalFetch=window.fetch;fixture.remote={enabled:false,devices:[],addresses:['192.168.1.33','100.101.102.103']};fixture.privateCount=0;
window.fetch=async(url,options={})=>{
 const route=new URL(url).pathname.replace('/api',''),body=options.body?JSON.parse(options.body):{};
 if(!['/search','/private/open','/private/send','/private/close','/remote/status','/remote/start','/remote/stop','/remote/pairing','/remote/revoke'].includes(route))return originalFetch(url,options);
 fixture.calls.push({route,body});let value={};
 if(route==='/search')value={models:['Falcon-Instruct','Fable','Fennec','Fugu','Flame','Fjord'].map(name=>({repository:'fixture/'+name,task:'text-generation',library:'gguf',downloads:1234,gated:false})),limited:false};
 if(route==='/remote/status')value=fixture.remote;
 if(route==='/remote/start')value=fixture.remote={enabled:true,port:8842,devices:[],addresses:['192.168.1.33','100.101.102.103']};
 if(route==='/remote/stop')value=fixture.remote={enabled:false,devices:[],addresses:['192.168.1.33','100.101.102.103']};
 if(route==='/remote/pairing')value={pairingText:'opencore://pair?fixture=public-test-only',expiresIn:120,qrRows:Array.from({length:29},(_,y)=>Array.from({length:29},(_,x)=>(x+y)%2?'1':'0').join(''))};
 if(route==='/private/open')value={sessionId:'private-fixture-'+(++fixture.privateCount)};
 if(route==='/private/send')return new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('data: {"delta":"PRIVATE_"}\n\ndata: {"delta":"REPLY"}\n\ndata: {"done":true}\n\n'));controller.close();}}),{headers:{'Content-Type':'text/event-stream'}});
 return{ok:true,json:async()=>value};
};`;
code=code.replace('${fixture}</script>','${fixture}${'+JSON.stringify(extra)+'}</script>');
code+=String.raw`
fs.mkdirSync(path.join(__dirname,'.output'),{recursive:true});
fs.writeFileSync(path.join(__dirname,'.output/managed-harness.html'),html);
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
 const page=await browser.newPage({viewport:{width:1450,height:1050}}),results=[],errors=[];
 page.on('pageerror',error=>errors.push(error.message));const record=(name,pass)=>results.push({name,pass:!!pass});
 try{
  await page.goto('http://127.0.0.1:'+server.address().port);
  const hub=page.getByRole('region',{name:'Add from Hugging Face',exact:true}),query=hub.getByLabel('Hugging Face repository',{exact:true});
  await hub.getByRole('tab',{name:'Trending',exact:true}).click();
  await page.waitForFunction(()=>fixture.calls.some(call=>call.route==='/search'&&call.body.sort==='trendingScore'&&call.body.query===''));
  record('the discovery tabs browse trending models without typing',await hub.getByRole('tab',{name:'Trending',exact:true}).getAttribute('aria-selected')==='true');
  await query.fill('F');await page.getByRole('button',{name:/fixture\/Falcon-Instruct/}).waitFor();
  record('typing a single letter runs prefix discovery',await page.evaluate(()=>fixture.calls.some(call=>call.route==='/search'&&call.body.query==='F'&&call.body.match==='prefix')));
  const placements=await hub.locator('.hub-search-results button').evaluateAll(nodes=>nodes.slice(0,6).map(node=>{const r=node.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,right:r.right};}));
  record('model search uses three columns and wraps the fourth card to a new row',placements.length===6&&placements[0].y===placements[1].y&&placements[1].y===placements[2].y&&placements[0].x<placements[1].x&&placements[1].x<placements[2].x&&placements[3].y>placements[0].y&&placements[3].x===placements[0].x);
  record('search cards do not overlap each other',placements[0].right<=placements[1].x&&placements[1].right<=placements[2].x);
  await page.setViewportSize({width:390,height:844});
  record('search results remain readable at phone width',await hub.locator('.hub-search-results').evaluate(element=>getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length===1));
  await page.setViewportSize({width:1450,height:1050});
  record('search controls use a themed background and visible text',await query.evaluate(input=>{const style=getComputedStyle(input);return style.backgroundColor!=='rgb(59, 59, 59)'&&style.color!==style.backgroundColor&&parseFloat(style.minHeight)>=40;}));
  await hub.getByLabel('Match',{exact:true}).selectOption('words');await hub.getByLabel('Task',{exact:true}).selectOption('text-generation');await hub.getByLabel('Format / library',{exact:true}).selectOption('gguf');
  await page.waitForFunction(()=>fixture.calls.some(call=>call.route==='/search'&&call.body.match==='words'&&call.body.task==='text-generation'&&call.body.library==='gguf'));
  record('word matching and task / format filters reach the metadata API',true);
  await hub.locator('summary').click();await hub.getByLabel('Hugging Face access token for model browser').fill('hf_fixture_private_access');await hub.getByRole('button',{name:'Save token',exact:true}).click();
  await hub.getByRole('checkbox',{name:'Include models accessible with my token'}).check();await page.waitForFunction(()=>fixture.calls.some(call=>call.route==='/search'&&call.body.private===true));
  record('private discovery is explicit and the token field clears after saving',await hub.getByLabel('Hugging Face access token for model browser').inputValue()==='');
  await hub.getByRole('button',{name:'Remove token',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('.hub-private-search input').checked);
  record('removing a token returns discovery to public mode',await hub.getByRole('checkbox',{name:'Include models accessible with my token'}).isChecked()===false);
  const remote=page.getByRole('region',{name:'Remote access',exact:true});await remote.getByRole('button',{name:'Enable remote access',exact:true}).click();
  await remote.getByRole('button',{name:'192.168.1.33',exact:true}).click();await remote.getByRole('button',{name:'Create pairing code',exact:true}).click();
  await remote.getByRole('img',{name:'Pairing QR code'}).waitFor();
  record('remote guide selects a detected address and shows a one-time QR code',await page.evaluate(()=>fixture.calls.some(call=>call.route==='/remote/pairing'&&call.body.address==='192.168.1.33'))&&await remote.getByText(/Code expires in \d+ seconds/).count()===1);
  record('remote guide displays the firewall port',await remote.getByText('Remote connection port: 8842',{exact:true}).count()===1);
  await remote.getByText('Can’t scan? Paste the pairing text instead').click();
  record('a manual pairing-text fallback is available',await remote.getByLabel('Phone pairing text').isVisible());
  await page.setViewportSize({width:390,height:844});
  record('remote guide stacks its instructions on a narrow display',await remote.locator('.remote-steps').evaluate(element=>getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length===1));
  await page.setViewportSize({width:1450,height:1050});
  await page.evaluate(()=>{fixture.remote.devices=[{id:'phone-fixture',name:'Test phone',connected:true}];});
  await remote.getByText('Test phone · Connected',{exact:true}).waitFor({timeout:6000});
  record('the PC guide reports when a paired phone is connected',await remote.getByText('1 phone connected',{exact:true}).count()===1);
  await remote.getByRole('button',{name:'Turn off remote access',exact:true}).click();record('turning remote access off clears pairing text',await remote.getByLabel('Phone pairing text').count()===0);
  await page.getByRole('button',{name:'Temporary chat',exact:true}).click();const dialog=page.getByRole('dialog',{name:'Temporary chat',exact:true});await dialog.getByLabel('Temporary message').fill('LOCAL_PRIVATE_MARKER');await dialog.getByRole('button',{name:'Send',exact:true}).click();await dialog.getByText('PRIVATE_REPLY',{exact:true}).waitFor();
  record('temporary chat streams a response without the native saved-chat command',await page.evaluate(()=>!fixture.calls.some(call=>call.command==='send_chat_message')));
  record('temporary prompts never enter Hugging Face discovery',await page.evaluate(()=>fixture.calls.filter(call=>call.route==='/search').every(call=>!JSON.stringify(call).includes('LOCAL_PRIVATE_MARKER'))));
  fs.mkdirSync(path.join(__dirname,'.output'),{recursive:true});await page.screenshot({path:path.join(__dirname,'.output/managed-desktop.png')});
  await dialog.getByRole('button',{name:'Close and clear',exact:true}).click();await page.waitForFunction(()=>fixture.calls.some(call=>call.route==='/private/close'));
  await page.getByRole('button',{name:'Temporary chat',exact:true}).click();record('reopening temporary chat starts with empty messages',await dialog.getByText('LOCAL_PRIVATE_MARKER',{exact:true}).count()===0&&await dialog.getByText('PRIVATE_REPLY',{exact:true}).count()===0);
  await page.setViewportSize({width:390,height:844});await dialog.waitFor();record('temporary chat fits phone-width rendering',await dialog.evaluate(element=>element.getBoundingClientRect().width<=window.innerWidth));await page.screenshot({path:path.join(__dirname,'.output/managed-phone.png')});
  await page.keyboard.press('Escape');record('Escape clears the temporary session and returns focus',await dialog.count()===0&&await page.getByRole('button',{name:'Temporary chat',exact:true}).evaluate(button=>document.activeElement===button));
  record('rendered surfaces have no runtime errors',errors.length===0);
  const report={passed:results.every(result=>result.pass),results,errors};fs.writeFileSync(path.join(__dirname,'.output/managed-qa.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));if(!report.passed)process.exitCode=1;
 }catch(error){console.error(error);console.error(errors);console.error((await page.locator('body').innerText()).slice(0,2500));process.exitCode=1;}
 finally{await browser.close();server.close();}
})();`;
const test=new Module(__filename,module);test.filename=__filename;test.paths=module.paths;test._compile(code,__filename);
