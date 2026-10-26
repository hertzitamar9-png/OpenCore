const fs=require('fs'),path=require('path'),http=require('http'),acorn=require('acorn');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT || 'playwright');
const directory=path.join(__dirname,'candidate-dist'),entryName='index-CD0EH-dA.js',appName='App-kNA9sRUP.js';
let entry=fs.readFileSync(path.join(directory,'assets',entryName),'utf8');
const tree=acorn.parse(entry,{ecmaVersion:'latest',sourceType:'module'}),mount=tree.body.find(n=>n.type==='ExpressionStatement'&&entry.slice(n.start,n.end).includes('createRoot('));
entry=entry.slice(0,mount.start)+entry.slice(mount.end)+'\nexport{ev as TestDOM};';
const app=fs.readFileSync(path.join(directory,'assets',appName),'utf8')+'\nexport{yj as TestModels,$HubSettings as TestHubSettings,$hubAugment as TestAugment,$hubInvoke as TestInvoke};';
const fixture=`
window.fixture={catalog:[],progress:null,calls:[],token:false};
fixture.variants=['Q4_K_M','Q8_0'].map((q,j)=>({id:'hf-'+String(j+1).repeat(24),repository:'fixture/tiny-model',revision:'a'.repeat(40),variantKey:'gguf:'+q,name:'tiny-model',quantization:q,format:'gguf',subdirectory:'',bytes:100+j,files:[{filename:'model-'+q+'.gguf',bytes:100+j,role:'model',sha256:'b'.repeat(64)}],installable:true,runtime:{adapter:'gguf'},sourceUrl:'https://huggingface.co/fixture/tiny-model',status:'not-installed'}));
window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{fixture.calls.push({command,args});switch(command){
case 'runtime_setup_probe':return{hubBridge:{available:true,url:'http://127.0.0.1:59999',secret:'fixture-secret-secret-secret-secret',authHeader:'X-OpenCore-Hub-Secret'}};
case 'runtime_setup_status':return{jobs:[],receipts:[],recipes:[],activeJobId:null};
case 'model_install_progress':return null;
case 'list_model_library':return{models:fixture.nativeModels||[],progress:null,diskFreeBytes:100e9,minimumFreeBytes:0};
case 'model_removal_plan':return{modelId:args.id,label:'Existing Q4 profile',scope:'verified-native-scope',confirmationToken:'verified-native-removal-plan',profiles:[],files:[{path:'fixture/models/native.gguf',bytes:100,external:!!fixture.nativeExternal}],retainedFiles:[{path:'fixture/shared/tokenizer.json',bytes:50,sharedWith:['another-profile']}],totalBytes:100};
case 'uninstall_model':fixture.nativeModels=[];return null;
case 'install_model':{const model=(fixture.nativeModels||[]).find(model=>model.id===args.id);if(model)model.installed=true;return null;}
case 'speech_status':return{enabled:false,installed:false,modelId:'whisper-large-v3-turbo',phase:'off'};
case 'model_weight_update_status':return{enabled:false,phase:'idle',models:[]};
case 'api_provider_save':return{id:'provider-fixture',name:args.input.name,models:[{id:args.input.model,profileId:'api:provider-fixture:'+args.input.model,enabled:true,available:true,contextTokens:8192}]};
case 'api_provider_model':return{id:args.id,name:'Local fixture',models:[{id:args.model,profileId:'api:provider-fixture:'+args.model,enabled:true,available:true,contextTokens:8192}]};
default:return null;}}};
window.fetch=async(url,options={})=>{const route=new URL(url).pathname.replace('/api',''),body=options.body?JSON.parse(options.body):{};fixture.calls.push({route,body,headers:options.headers});let value={};
if(route==='/status'){const snapshot=JSON.stringify(fixture.catalog);if(snapshot!==fixture.snapshot){fixture.snapshot=snapshot;fixture.revision=(fixture.revision||0)+1;fixture.catalogRevision=fixture.revision.toString(16).padStart(64,'0');}value={...(options.headers?.['X-OpenCore-Catalog-Revision']===fixture.catalogRevision?{}:{catalog:fixture.catalog}),catalogRevision:fixture.catalogRevision,progress:fixture.progress,tokenConfigured:fixture.token};}
else if(route==='/plan'){fixture.currentPlan=fixture.variants;value={planId:'plan-fixture',repository:'fixture/tiny-model',revision:'a'.repeat(40),variants:fixture.currentPlan};}
else if(route==='/catalog/add'){fixture.catalog=fixture.currentPlan.filter(v=>body.variantIds.includes(v.id));value={added:fixture.catalog.map(v=>v.id),catalog:fixture.catalog};}
else if(route==='/install'){fixture.progress={id:body.id,jobId:'download-fixture',kind:'download',status:'running',stage:'downloading',bytesDone:30,bytesTotal:100};value=fixture.progress;}
else if(route==='/pause'){fixture.progress={...fixture.progress,status:'paused',stage:'paused'};value={paused:true};}
else if(route==='/prepare'){fixture.progress={id:body.id,jobId:'prepare-fixture',kind:'prepare',status:'running',stage:'loading-model',bytesDone:0,bytesTotal:0};value=fixture.progress;}
else if(route==='/catalog/profile'){Object.assign(fixture.catalog.find(v=>v.id===body.id),body);value=fixture.catalog.find(v=>v.id===body.id);}
else if(route==='/token'){if(options.method==='PUT')fixture.token=true;if(options.method==='DELETE')fixture.token=false;value={configured:fixture.token};}
return{ok:true,json:async()=>structuredClone(value)};};
`;
const html=`<!doctype html><meta charset=utf-8><title>OpenCore Hub QA</title><link rel=stylesheet href=/assets/index-CsoxYKnK.css><link rel=stylesheet href=/assets/App-C9duitZV.css><div id=root></div><script>${fixture}</script><script type=module>
import{r as React,TestDOM}from'/assets/${entryName}';import{TestModels,TestHubSettings}from'/assets/${appName}';
TestDOM.createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement(TestModels,{selectedProfile:'none',onSelect:id=>window.selected=id,runtimeActive:false,onNotice:message=>window.notice=message}),React.createElement(TestHubSettings)));
</script>`;
const server=http.createServer((req,res)=>{const name=new URL(req.url,'http://local').pathname;if(name==='/'){res.setHeader('Content-Type','text/html');return res.end(html);}res.setHeader('Content-Type',name.endsWith('.css')?'text/css':'text/javascript');if(name==='/assets/'+entryName)return res.end(entry);if(name==='/assets/'+appName)return res.end(app);const file=path.join(directory,name);if(!file.startsWith(directory)||!fs.existsSync(file)){res.statusCode=404;return res.end();}res.end(fs.readFileSync(file));});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});const page=await browser.newPage({viewport:{width:1450,height:1000}}),results=[],errors=[];page.on('pageerror',e=>errors.push(e.message));const record=(name,pass)=>results.push({name,pass:!!pass});try{
await page.goto('http://127.0.0.1:'+server.address().port);await page.getByLabel('Hugging Face repository',{exact:true}).fill('fixture/tiny-model');await page.getByRole('button',{name:'Check repository',exact:true}).click();await page.getByText('2 published model variants',{exact:false}).waitFor();
record('repo metadata exposes both real quantization choices',await page.locator('.hub-variant-list input:checked').count()===2);
record('metadata check does not download weights',await page.evaluate(()=>!fixture.calls.some(c=>c.route==='/install')));
await page.getByRole('button',{name:'Add 2 variants',exact:true}).click();await page.getByRole('heading',{name:'tiny-model',exact:true}).waitFor();
record('all variants share a regular library card',await page.getByRole('article').count()===1&&await page.getByLabel('Quantization for fixture/tiny-model').locator('option').count()===2);
record('imported models use the regular card and quantization field styling',await page.getByRole('article').evaluate(card=>card.classList.contains('model-library-card')&&parseFloat(getComputedStyle(card).borderTopWidth)>=1&&parseFloat(getComputedStyle(card.querySelector('select')).minHeight)>=36));
await page.getByRole('button',{name:'Install',exact:true}).click();await page.getByRole('button',{name:'Installing…',exact:true}).waitFor();
record('install immediately locks its card',await page.getByRole('button',{name:'Installing…',exact:true}).isDisabled());
await page.waitForTimeout(1400);record('Hub progress uses the shared top panel',await page.locator('.model-install-progress progress').getAttribute('value')==='30');
await page.locator('.model-install-progress').getByRole('button',{name:'Pause installation',exact:true}).click();await page.getByRole('button',{name:'Install',exact:true}).waitFor();
// The card can render before the shared coordinator has confirmed the pause.
// Wait for authoritative admission to release the control, not just its label.
await (await page.getByRole('button',{name:'Install',exact:true}).elementHandle()).waitForElementState('enabled');
record('pause reaches service and releases the same install lock',await page.getByRole('button',{name:'Install',exact:true}).isEnabled()&&await page.evaluate(()=>fixture.calls.some(c=>c.route==='/pause')));
await page.evaluate(()=>{fixture.catalog[0].status='installed';fixture.progress=null;});await page.getByRole('button',{name:'Prepare runtime',exact:true}).waitFor();
await page.getByRole('button',{name:'Prepare runtime',exact:true}).click();await page.waitForTimeout(1300);record('runtime preparation uses indeterminate shared progress',await page.locator('.model-install-progress progress').getAttribute('value')===null);
await page.evaluate(()=>{fixture.catalog[0].runtimeReady=true;fixture.catalog[0].runtimePreparation={contextTokens:8192,runtimeReady:true};fixture.progress={...fixture.progress,status:'ready',stage:'ready'};});
await page.getByRole('button',{name:'Use model',exact:true}).waitFor();await page.getByRole('button',{name:'Use model',exact:true}).click();await page.waitForTimeout(400);
record('Use model registers and selects an actual native chat profile',await page.evaluate(()=>window.selected?.startsWith('api:provider-fixture:')));
await page.getByLabel('Hugging Face access token',{exact:true}).fill('hf_fake_fixture_token');await page.getByRole('button',{name:'Save token',exact:true}).click();await page.getByText('Access token saved',{exact:true}).waitFor();
record('token save clears password field and returns status only',await page.getByLabel('Hugging Face access token',{exact:true}).inputValue()===''&&!await page.locator('body').innerText().then(t=>t.includes('hf_fake_fixture_token')));
await page.getByRole('button',{name:'Remove token',exact:true}).click();await page.getByText('No token saved',{exact:true}).waitFor();record('token removal is independent from public catalog',await page.getByRole('article').count()===1);
await page.evaluate(()=>{fixture.catalog=fixture.variants.map(v=>({...v,status:'not-installed',runtimeReady:false,profileId:null,providerId:null,familyId:'publisher-flint',familyLabel:'Flint 40B A2.5B',nativeModelIds:['flint'],repository:v.quantization==='Q8_0'?'publisher/flint-other-quants':v.repository}));fixture.catalog.push({...fixture.catalog[0],id:'hf-'+'3'.repeat(24),familyId:'publisher-raon-1-mini',familyLabel:'Raon 1 mini',repository:'KRAFTON/Raon-1-mini-preview-gguf',nativeModelIds:['raon-1-mini']});});
await page.getByRole('heading',{name:'Flint 40B A2.5B',exact:true}).waitFor();await page.getByRole('heading',{name:'Raon 1 mini',exact:true}).waitFor();
record('publisher quantizations from multiple repositories share two family cards',await page.getByRole('article').count()===2&&await page.getByLabel('Quantization for fixture/tiny-model').locator('option').count()===2);
record('managed families replace their duplicate native catalog entries',await page.evaluate(async()=>{const{TestAugment}=await import('/assets/App-kNA9sRUP.js');const result=TestAugment({models:[{id:'flint'},{id:'raon-1-mini'},{id:'unrelated'}]});return result.models.length===4&&!result.models.some(v=>v.id==='flint'||v.id==='raon-1-mini')&&result.models.some(v=>v.id==='unrelated');}));
await page.getByRole('article',{name:'Flint 40B A2.5B',exact:true}).getByRole('button',{name:'Install',exact:true}).click();await page.getByRole('button',{name:'Installing…',exact:true}).waitFor();
record('another publisher model cannot install during the active download',await page.getByRole('article',{name:'Raon 1 mini',exact:true}).getByRole('button',{name:'Install',exact:true}).isDisabled());
await page.evaluate(()=>{fixture.progress={...fixture.progress,stage:'verifying-echo'};});await page.waitForTimeout(1400);
record('ECHO verification remains visible in the shared preparation progress',await page.locator('.model-install-progress progress').getAttribute('value')===null);
await page.locator('.model-install-progress').getByRole('button',{name:'Pause installation',exact:true}).click();
const planCount=await page.evaluate(()=>fixture.calls.filter(c=>c.route==='/plan').length);
await page.evaluate(()=>{fixture.progress=null;fixture.catalog=['Q2_K','Q3_K_M','Q4_K_M','Q5_K_M','Q6_K'].map((q,j)=>({...fixture.variants[0],id:'hf-'+String(j+4).repeat(24),quantization:q,familyId:'builtin-fixture',familyLabel:'Preset checkpoint',nativeModelIds:['native-existing'],status:'not-installed',runtimeReady:false}));});
const preset=page.getByRole('article',{name:'Preset checkpoint',exact:true});await preset.waitFor();
record('five preset choices are ready on a built-in card without repository inspection',await preset.locator('select option').count()===5&&await page.evaluate(()=>fixture.calls.filter(c=>c.route==='/plan').length)===planCount);
record('there are no community search controls on model cards or the importer',await page.getByRole('button',{name:/Find.*quantization/}).count()===0&&await page.getByRole('checkbox',{name:/community/i}).count()===0);
record('the separate importer inspects only the entered repository',await page.evaluate(()=>fixture.calls.filter(c=>c.route==='/plan').every(c=>c.body.repository==='fixture/tiny-model'&&!c.body.includeCommunity)));
await preset.locator('select').selectOption('hf-'+'6'.repeat(24));await preset.getByRole('button',{name:'Install',exact:true}).click();
record('a preset quant installs directly without Add or Check repository',await page.evaluate(()=>fixture.calls.filter(c=>c.route==='/install').at(-1).body.id==='hf-'+'6'.repeat(24)&&fixture.calls.filter(c=>c.route==='/plan').length)===planCount);
await page.locator('.model-install-progress').getByRole('button',{name:'Pause installation',exact:true}).click();
await page.evaluate(async()=>{fixture.progress=null;fixture.nativeModels=[{id:'native-existing',label:'Existing Q4 profile',precision:'Q4_K_M',memoryMode:'native',category:'text',contextTokens:8192,installed:true,selectable:true,runtimeReady:true,totalBytes:100}];const {TestInvoke}=await import('/assets/App-kNA9sRUP.js');const library=await TestInvoke('list_model_library',{},()=>window.__TAURI_INTERNALS__.invoke('list_model_library'));window.dispatchEvent(new CustomEvent('opencore-model-library-changed',{detail:library}));});
try{await preset.locator('option[value="native-existing"]').waitFor({state:'attached',timeout:5000});}catch(error){console.log('Native preservation state',await page.evaluate(async()=>{const {TestAugment}=await import('/assets/App-kNA9sRUP.js');return {native:fixture.nativeModels,augmented:TestAugment({models:fixture.nativeModels}).models.map(model=>({id:model.id,variantOf:model.variantOf,installed:model.installed})),calls:fixture.calls.filter(call=>call.command==='list_model_library').length};}));throw error;}
record('an existing installed native profile remains in the same card without a duplicate Q4 choice',await preset.locator('select option').count()===5&&await preset.locator('option[value="native-existing"]').innerText().then(t=>t.includes('Installed')));
await preset.locator('select').selectOption('native-existing');const providersBefore=await page.evaluate(()=>fixture.calls.filter(c=>c.command==='api_provider_save').length);await preset.getByRole('button',{name:'Use model',exact:true}).click();
record('existing native selection preserves its profile and does not create a replacement API provider',await page.evaluate(()=>window.selected==='native-existing'&&fixture.calls.filter(c=>c.command==='api_provider_save').length)===providersBefore);
await page.evaluate(()=>fixture.nativeExternal=true);await preset.getByRole('button',{name:'Uninstall',exact:true}).click();
record('native removal uses the original shared-file and external-checkpoint review',await page.getByRole('dialog').getByRole('button',{name:'Uninstall model',exact:true}).isDisabled()&&await page.getByText('1 shared files will be kept',{exact:true}).isVisible()&&await page.getByText('I also want to remove these existing checkpoint files.',{exact:true}).isVisible());
await page.getByRole('dialog').getByRole('button',{name:'Cancel',exact:true}).click();
record('cancelling native removal does not uninstall the existing model',await page.evaluate(()=>!fixture.calls.some(c=>c.command==='uninstall_model')));
await page.evaluate(()=>fixture.nativeExternal=false);await preset.getByRole('button',{name:'Uninstall',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Uninstall model',exact:true}).click();
record('native removal retains the backend confirmation token and returned scope',await page.evaluate(()=>fixture.calls.some(c=>c.command==='uninstall_model'&&c.args.confirmationToken==='verified-native-removal-plan'&&c.args.scope==='verified-native-scope')&&fixture.calls.filter(c=>c.command==='model_removal_plan').every(c=>!c.args.scope)));
await page.evaluate(async()=>{fixture.catalog.forEach(entry=>entry.nativeModelIds.push('native-extra'));fixture.nativeModels=[{id:'native-extra',label:'Original Q8 option',precision:'Q8_0',memoryMode:'native',category:'text',contextTokens:8192,installed:false,selectable:true,runtimeReady:true,installable:true,totalBytes:200}];const {TestInvoke}=await import('/assets/App-kNA9sRUP.js');const library=await TestInvoke('list_model_library',{},()=>window.__TAURI_INTERNALS__.invoke('list_model_library'));window.dispatchEvent(new CustomEvent('opencore-model-library-changed',{detail:library}));});
await preset.locator('option[value="native-extra"]').waitFor({state:'attached'});await preset.locator('select').selectOption('native-extra');
record('an original native quant outside the five presets remains available to install',await preset.locator('select option').count()===6&&await preset.getByRole('button',{name:'Install',exact:true}).isEnabled());
const hubDownloadsBefore=await page.evaluate(()=>fixture.calls.filter(call=>call.route==='/install').length);await preset.getByRole('button',{name:'Install',exact:true}).click();
record('original native quant installation keeps its native backend',await page.evaluate(()=>fixture.calls.some(call=>call.command==='install_model'&&call.args.id==='native-extra')&&fixture.calls.filter(call=>call.route==='/install').length)===hubDownloadsBefore);
record('no runtime errors',errors.length===0);await page.screenshot({path:path.join(__dirname,'hub-qa.png'),fullPage:true});
}catch(error){console.log('QA failure',error.message,'errors',errors,'body',(await page.locator('body').innerText()).slice(0,6000));throw error;}finally{await browser.close();await new Promise(r=>server.close(r));fs.writeFileSync(path.join(__dirname,'hub-qa.json'),JSON.stringify({results,errors},null,2));}console.log(JSON.stringify(results,null,2));if(results.some(r=>!r.pass))process.exitCode=1;})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
