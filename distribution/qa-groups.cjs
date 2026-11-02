// The real recovered sidebar and catalog-installed group components, with only
// transport/native boundaries substituted. Real TLS transport has separate QA.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-chat-image-picker.cjs'),'utf8');
let fixture=original.slice(0,original.indexOf('\n(async()=>'));
fixture=fixture.replace("const dir=path.join(__dirname,'candidate-dist')","const dir=path.join(process.env.OPENCORE_QA_UI||__dirname,'candidate-dist')");
fixture=fixture.replace('const html=`','app+="\\nexport{l8 as TestSidebar};";\nconst html=`');
fixture=fixture.replace('import{TestComposer,TestFooter,TestChanges}','import{TestComposer,TestFooter,TestChanges,TestSidebar}');
fixture=fixture.replace(/function Fixture\(\)\{[\s\S]*?\}TestDOM\.createRoot/,
  "function Fixture(){return h(React.Fragment,null,h(TestSidebar,{conversations:[],projects:[],onSelect:()=>{},onNew:()=>{},onExit:()=>{},onCreateProject:async()=>true,onTogglePin:()=>{},onEditProject:()=>{},onRemoveProject:()=>{},onOpenProjectFolder:async()=>'',onChangeProjectFolder:async()=>'',importRevision:0}),globalThis.__opencoreGroupHost?h(globalThis.__opencoreGroupHost):null)}TestDOM.createRoot");
fixture=fixture.replace('</script><script type=module>',String.raw`
fixture.groupCalls=[];fixture.groups=[];fixture.groupMessages=[];fixture.sharing={enabled:false,models:[],approvalMode:'ask-every-time',maxSubagents:3};
fixture.snapshot=id=>({group:{id,name:fixture.groups.find(group=>group.id===id).name},self:{id:'me',name:'Owner',role:fixture.groups.find(group=>group.id===id).hosting?'owner':'member'},
  members:[{id:'me',name:'Owner',pcName:'This PC',online:true},{id:'friend',name:'Friend',pcName:'Friend PC',online:true}],
  agents:[...fixture.sharing.models.map(model=>({id:'agent-'+model.id,name:model.name,modelId:model.id,modelLabel:model.label,memberId:'me',pcName:'This PC',online:true})),...(fixture.extraAgents||[])],
  messages:fixture.groupMessages,tasks:fixture.groupMessages.filter(message=>message.agentId).map(message=>({id:'task-'+message.id,agentId:message.agentId,agentName:'Helper',status:'queued',text:message.text})),plan:[],sharing:fixture.sharing});
window.fetch=async(url,options={})=>{
 const action=String(url).split('/groups/')[1];let value;
 if(action){const body=JSON.parse(options.body||'{}');fixture.groupCalls.push({action,body});
 switch(action){case'list':value={groups:fixture.groups,localName:'Owner'};break;
 case'create':case'join':{const group={id:'group-'+(fixture.groups.length+1),name:action==='create'?body.name:'Friend host',hosting:action==='create',connected:true};fixture.groups.push(group);value=group;break;}
 case'view':value=fixture.snapshot(body.id);break;
 case'sharing':fixture.sharing={...body};value=fixture.offlineSharing?{localOnly:true,notice:'Settings saved on this PC; group host is unavailable',sharing:fixture.sharing,connectionId:body.id}:fixture.snapshot(body.id);break;
 case'message':fixture.groupMessages.push({id:'message-'+fixture.groupMessages.length,kind:'user',authorName:'Owner',text:body.text,agentId:body.agentIds[0],createdAt:'2026-11-01T12:00:00Z'});value=fixture.snapshot(body.id);break;
 case'invite':value={invite:'opencore-group:v1:fixture',expiresIn:300,network:{address:'127.0.0.1'}};break;
 case'worker/poll':value={jobs:[]};break;default:value={};}}
 else value={catalog:fixture.adapters,jobs:[],entries:[],progress:null,tokenConfigured:false,catalogRevision:'a'.repeat(64)};
 return{ok:true,json:async()=>value};};
</script><script type=module>`);
fixture+=String.raw`
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,args:['--mute-audio']});
 try{for(const width of [1400,390]){
  const page=await browser.newPage({viewport:{width,height:900},permissions:['clipboard-read','clipboard-write']}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(mobile=>{globalThis.__OPENCORE_MOBILE__=mobile},width<600);
  await page.goto('http://127.0.0.1:'+server.address().port);
  if(width<600)await page.getByRole('button',{name:'Chats',exact:true}).click();
  await page.getByRole('button',{name:'Chat Core',exact:true}).click({timeout:4000});
  const dialog=page.getByRole('dialog',{name:'Chat Core'});await dialog.waitFor();
  await dialog.getByRole('button',{name:'Create group',exact:true}).click();
  await dialog.getByLabel('Group name',{exact:true}).fill('Friends QA');
  await dialog.getByLabel('Your name',{exact:true}).fill('Owner');
  await dialog.getByRole('button',{name:'Create group',exact:true}).last().click();
  await dialog.getByRole('heading',{name:'Friends QA',exact:true}).waitFor();
  await dialog.getByRole('tab',{name:'Share my PC',exact:true}).click();
  assert.equal(await dialog.getByLabel('Share models from this PC',{exact:true}).isChecked(),false);
  assert.equal(await dialog.getByLabel('Group tool approval',{exact:true}).inputValue(),'ask-every-time');
  await dialog.getByLabel('Share models from this PC',{exact:true}).check();
  await dialog.getByLabel('Model for new agent',{exact:true}).selectOption('echo');
  await dialog.getByRole('button',{name:'Add agent',exact:true}).click();
  await dialog.getByLabel(/^Agent name for ECHO 3T/).fill('Helper');
  await dialog.getByRole('button',{name:'Save sharing',exact:true}).click();
  await page.waitForFunction(()=>fixture.sharing.enabled&&fixture.sharing.models.some(model=>model.name==='Helper'));
  assert.equal(await page.evaluate(()=>fixture.sharing.approvalMode),'ask-every-time');
  await dialog.getByRole('tab',{name:'Chat',exact:true}).click();
  await dialog.getByLabel('Group message',{exact:true}).fill('@Helper Write a plan');
  await dialog.getByRole('button',{name:'Send group message',exact:true}).click();
  await dialog.getByText('@Helper Write a plan',{exact:true}).first().waitFor();
  const sent=await page.evaluate(()=>fixture.groupCalls.find(call=>call.action==='message').body);
  assert.deepEqual(sent.agentIds,['agent-echo']);assert.ok(sent.requestId);
  await page.evaluate(()=>{fixture.extraAgents=[{id:'friend-echo',name:'Helper',modelId:'friend-model',modelLabel:'Friend model',memberId:'friend',pcName:'Friend PC',online:true}]});
  await dialog.getByLabel('Ask Helper on Friend PC',{exact:true}).waitFor({state:'attached'});
  await dialog.getByLabel('Group message',{exact:true}).fill('@Helper Which PC?');
  await dialog.getByRole('button',{name:'Send group message',exact:true}).click();
  assert.equal(await page.evaluate(()=>fixture.groupCalls.filter(call=>call.action==='message').length),1);
  await dialog.getByText('Choose which PC should run @Helper',{exact:true}).waitFor();
  assert.equal(await dialog.getByLabel('Group message',{exact:true}).inputValue(),'@Helper Which PC?');
  await dialog.locator('.group-targets > summary').click();
  await dialog.getByLabel('Ask Helper on This PC',{exact:true}).check();
  await dialog.getByRole('button',{name:'Send group message',exact:true}).click();
  await page.waitForFunction(()=>fixture.groupCalls.filter(call=>call.action==='message').length===2);
  assert.deepEqual(await page.evaluate(()=>fixture.groupCalls.filter(call=>call.action==='message').at(-1).body.agentIds),['agent-echo']);
  await dialog.getByRole('tab',{name:'Plan',exact:true}).click();
  await dialog.getByText('Queued',{exact:true}).first().waitFor();
  await dialog.getByRole('button',{name:'Create invite',exact:true}).click();
  assert.equal(await dialog.getByLabel('Group invite',{exact:true}).inputValue(),'opencore-group:v1:fixture');
  await dialog.getByRole('tab',{name:'Share my PC',exact:true}).click();
  await page.evaluate(()=>{fixture.offlineSharing=true});
  await dialog.getByLabel('Share models from this PC',{exact:true}).uncheck();
  await dialog.getByRole('button',{name:'Save sharing',exact:true}).click();
  await dialog.getByText('Settings saved on this PC; group host is unavailable',{exact:true}).waitFor();
  assert.equal(await dialog.getByLabel('Share models from this PC',{exact:true}).isChecked(),false);
  await dialog.getByRole('heading',{name:'Friends QA',exact:true}).waitFor();
  await dialog.getByRole('tab',{name:'Groups',exact:true}).click();
  await dialog.getByRole('button',{name:'Join with invite',exact:true}).click();
  await dialog.getByLabel('Paste group invite',{exact:true}).fill('opencore-group:v1:friend');
  await dialog.getByRole('button',{name:'Join group',exact:true}).click();
  await dialog.getByRole('heading',{name:'Friend host',exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelector('.group-tabs [aria-selected=true]')?.textContent==='Chat');
  await page.waitForFunction(()=>![...document.querySelectorAll('.group-header-actions button')].some(button=>button.textContent==='Create invite'));
  assert.equal(await dialog.getByRole('button',{name:'Leave',exact:true}).isEnabled(),true);
  assert.equal(await page.evaluate(()=>fixture.calls.filter(call=>call.command==='send_chat_message').length),0);
  const bounds=await dialog.boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width+1);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  if(process.env.OPENCORE_GROUP_QA_SCREENSHOTS)await page.screenshot({path:path.join(process.env.OPENCORE_GROUP_QA_SCREENSHOTS,'groups-'+width+'.png')});
  await dialog.getByRole('button',{name:'Close Chat Core',exact:true}).click();await dialog.waitFor({state:'hidden'});
  assert.deepEqual(errors,[]);console.log('PASS packaged shared-group create/join/sharing/mention/task UI at '+width+'px');
  await page.close();
 }}finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1});`;
const filename=path.join(__dirname,'qa-groups.fixture.cjs'),compiled=new Module(filename,module);
compiled.filename=filename;compiled.paths=Module._nodeModulePaths(__dirname);compiled._compile(fixture,filename);
