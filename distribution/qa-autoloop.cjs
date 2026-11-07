/* Real packaged Learning Studio; only local service/native boundaries are fixtures. */
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=original.slice(0,original.indexOf('(async()=>{await new Promise'));
code=code.replace('Up as TestProfiles','Up as TestProfiles,nj as TestLearning');
code=code.replace('TestModels,TestHubSettings}from','TestModels,TestHubSettings,TestLearning}from');
code=code.replace('React.createElement(React.Fragment,null,React.createElement(TestModels,{selectedProfile:\'none\',onSelect:id=>window.selected=id,runtimeActive:false,onNotice:message=>window.notice=message}),React.createElement(TestHubSettings))',
  'React.createElement(TestLearning,{onNotice:message=>window.notice=message,renderAssistant:()=>null})');
code=code.replace("case 'runtime_setup_probe':","case 'learning_command':return{runs:[],active:false,root:'fixture',rawRecords:0};case 'runtime_setup_probe':");
code=code.replace("status:'running',stage:'loading-model'", "status:'ready',stage:'ready'");
code=code.replace('window.fetch=async(url,options={})=>{',String.raw`
fixture.autoRuns=[];fixture.autoActive=null;fixture.autoModels=[{id:'source',label:'Installed original Q4',modelPath:'C:/models/original.gguf',bytes:100},{id:'unsupported',label:'Hybrid model',modelPath:'C:/models/unsupported.gguf',bytes:100}];
window.__TAURI_INTERNALS__.transformCallback=()=>1;window.__TAURI_INTERNALS__.unregisterCallback=()=>{};
window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener(){}};
window.fetch=async(url,options={})=>{
 const operation=new URL(url).pathname.replace('/api',''),request=options.body?JSON.parse(options.body):{};
 if(operation.startsWith('/autoloop/')){fixture.calls.push({route:operation,body:request});let value={};
  if(operation==='/autoloop/models')value={models:fixture.autoModels,runtimeAvailable:true};
  if(operation==='/autoloop/status')value={runs:fixture.autoRuns,activeRunId:fixture.autoActive};
  if(operation==='/autoloop/inspect')value=request.modelPath.includes('unsupported')?{supported:false,reason:'Hybrid blocks cannot be safely repeated',contextTokens:32768}:{supported:true,architecture:'llama',blockCount:30,contextTokens:32768};
  if(operation==='/autoloop/start'){const run={id:'a'.repeat(32),name:'Original Q4 experiment',status:'running',stage:'development',candidate:'baseline',split:'development',completed:3,total:31,createdAt:Date.now()/1000,updatedAt:Date.now()/1000};fixture.autoRuns=[run];fixture.autoActive=run.id;value=run;}
  if(operation==='/autoloop/cancel'){fixture.autoRuns[0]={...fixture.autoRuns[0],status:'cancelled',stage:'cancelled'};fixture.autoActive=null;value={cancelling:true};}
  if(operation==='/autoloop/report')value={status:'rejected',reason:'No confirmed gain',baseline:{correct:20},rawAnswersPreserved:true};
  return{ok:true,json:async()=>structuredClone(value)};}
`);
code+=String.raw`
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,args:['--mute-audio']});
 const errors=[],checks=[],out=process.env.OPENCORE_AUTOLOOP_QA_DIR||path.join(__dirname,'.output/autoloop-ui');fs.mkdirSync(out,{recursive:true});
 const check=(name,value)=>{checks.push({name,passed:!!value});if(!value)throw Error(name)};
 try{for(const width of [1440,393]){
  const page=await browser.newPage({viewport:{width,height:1000}});page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.getByRole('tab',{name:'AutoLoop · No training',exact:true}).click();
  const panel=page.getByRole('region',{name:'AutoLoop training-free optimization'});await panel.waitFor();
  const start=panel.getByRole('button',{name:'Optimize and verify',exact:true});await start.waitFor({state:'visible'});
  await page.waitForFunction(()=>document.querySelector('.autoloop-compatible'));
  check(width+' no startup or service alerts',await page.getByRole('alert').count()===0);
  check(width+' starts no inference or downloads on opening',await page.evaluate(()=>!fixture.calls.some(c=>c.route==='/autoloop/start'||c.route==='/install'||c.command==='send_chat_message')));
  await panel.getByLabel('Installed GGUF checkpoint',{exact:true}).selectOption({label:'Hybrid model'});
  await panel.getByText('Hybrid blocks cannot be safely repeated',{exact:true}).waitFor();check(width+' unsupported model remains unchanged and disabled',await start.isDisabled());
  await panel.getByLabel('Installed GGUF checkpoint',{exact:true}).selectOption({label:'Installed original Q4'});await page.waitForFunction(()=>document.querySelector('.autoloop-compatible'));
  await panel.getByText('Experiment settings',{exact:true}).click();await panel.getByLabel('Candidate limit',{exact:true}).fill('5');await panel.getByLabel('Time budget (minutes)',{exact:true}).fill('12');
  await start.click();await panel.getByRole('button',{name:'Stop experiment',exact:true}).waitFor();check(width+' duplicate start is disabled while tests run',await start.isDisabled());
  check(width+' stage has real progress counters',await panel.getByRole('progressbar',{name:'Current AutoLoop test progress'}).getAttribute('value')==='3');
  const sent=await page.evaluate(()=>fixture.calls.find(c=>c.route==='/autoloop/start').body);check(width+' bounded manual budgets reach service',sent.maxVariants===5&&sent.maxMinutes===12&&sent.experimentalDamping===false&&!sent.llamaServer);
  await panel.getByRole('button',{name:'Stop experiment',exact:true}).click();await panel.getByText('Cancelled',{exact:true}).waitFor();
  await page.evaluate(()=>{fixture.autoRuns[0]={...fixture.autoRuns[0],status:'rejected',stage:'rejected',reason:'No independent improvement',updatedAt:Date.now()/1000};});
  await panel.getByText('Original retained',{exact:true}).waitFor();check(width+' rejected model offers no use button',await panel.getByRole('button',{name:'Use verified model in chat'}).count()===0);
  await panel.getByRole('button',{name:'Show exact quality report',exact:true}).click();await panel.getByText(/rawAnswersPreserved/).waitFor();
  await page.evaluate(()=>{fixture.autoRuns[0]={...fixture.autoRuns[0],status:'accepted',stage:'accepted',runtimeReady:true,modelId:'hf-'+('c'.repeat(24)),qualityScope:'Fixture confirmation set only',updatedAt:Date.now()/1000};fixture.catalog=[{id:'hf-'+('c'.repeat(24)),origin:'autoloop',repository:'OpenCore-Local/AutoLoop-fixture',familyLabel:'Verified fixture model',quantization:'Original GGUF precision',runtimeReady:true,contextTokens:32768,category:'text',files:[{role:'model',bytes:100}]}];});
  await panel.getByRole('button',{name:'Use verified model in chat',exact:true}).waitFor();
  await panel.getByRole('button',{name:'Use verified model in chat',exact:true}).click();
  await page.waitForFunction(()=>fixture.calls.some(c=>c.command==='start_profile'));
  check(width+' accepted profile loads its managed runtime before chat selection',await page.evaluate(()=>{
    const prepared=fixture.calls.findIndex(c=>c.route==='/prepare'&&c.body.id===fixture.autoRuns[0].modelId);
    const started=fixture.calls.findIndex(c=>c.command==='start_profile'&&c.args.request.profile.startsWith('api:provider-fixture:'));
    return prepared>=0&&started>prepared;
  }));
  await page.reload();await panel.waitFor();await panel.getByText('Experiment settings',{exact:true}).click();
  check(width+' tab and budgets survive reopening',await panel.getByLabel('Candidate limit',{exact:true}).inputValue()==='5'&&await panel.getByLabel('Time budget (minutes)',{exact:true}).inputValue()==='12');
  check(width+' layout fits viewport',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  check(width+' visible buttons meet touch height',await panel.locator('button:visible').evaluateAll(buttons=>buttons.every(button=>button.getBoundingClientRect().height>=43)));
  await page.evaluate(()=>{document.querySelector('.learning-body').scrollTop=0;window.scrollTo(0,0)});
  await page.screenshot({path:path.join(out,'autoloop-'+width+'.png'),fullPage:true});await page.close();
 }}finally{fs.writeFileSync(path.join(out,'autoloop-ui.json'),JSON.stringify({checks,errors,nativeBoundaries:'fixtures; real model evidence is recorded separately'},null,2));await browser.close();server.close();}
 console.log(JSON.stringify({checks,errors,output:out},null,2));if(errors.length)process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1});`;
const compiled=new Module(__filename,module);compiled.filename=__filename;compiled.paths=module.paths;compiled._compile(code,__filename);
