const fs=require('fs'),path=require('path'),http=require('http'),acorn=require('acorn');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT || 'playwright');
const directory=path.join(__dirname,'candidate-dist'),entryName='index-CD0EH-dA.js',appName='App-kNA9sRUP.js';
let entry=fs.readFileSync(path.join(directory,'assets',entryName),'utf8');
const tree=acorn.parse(entry,{ecmaVersion:'latest',sourceType:'module'}),mount=tree.body.find(n=>n.type==='ExpressionStatement'&&entry.slice(n.start,n.end).includes('createRoot('));
entry=entry.slice(0,mount.start)+entry.slice(mount.end)+'\nexport{ev as TestDOM};';
const app=fs.readFileSync(path.join(directory,'assets',appName),'utf8')+'\nexport{yj as TestModels,$HubSettings as TestHubSettings,$hubAugment as TestAugment};';
const fixture=`
window.fixture={catalog:[],progress:null,calls:[],token:false};
fixture.variants=['Q4_K_M','Q8_0'].map((q,j)=>({id:'hf-'+String(j+1).repeat(24),repository:'fixture/tiny-model',revision:'a'.repeat(40),variantKey:'gguf:'+q,name:'tiny-model',quantization:q,format:'gguf',subdirectory:'',bytes:100+j,files:[{filename:'model-'+q+'.gguf',bytes:100+j,role:'model',sha256:'b'.repeat(64)}],installable:true,runtime:{adapter:'gguf'},sourceUrl:'https://huggingface.co/fixture/tiny-model',status:'not-installed'}));
window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{fixture.calls.push({command,args});switch(command){
case 'runtime_setup_probe':return{hubBridge:{available:true,url:'http://127.0.0.1:59999',secret:'fixture-secret-secret-secret-secret',authHeader:'X-OpenCore-Hub-Secret'}};
case 'runtime_setup_status':return{jobs:[],receipts:[],recipes:[],activeJobId:null};
case 'model_install_progress':return null;
case 'list_model_library':return{models:[],progress:null,diskFreeBytes:100e9,minimumFreeBytes:0};
case 'speech_status':return{enabled:false,installed:false,modelId:'whisper-large-v3-turbo',phase:'off'};
case 'model_weight_update_status':return{enabled:false,phase:'idle',models:[]};
case 'api_provider_save':return{id:'provider-fixture',name:args.input.name,models:[{id:args.input.model,profileId:'api:provider-fixture:'+args.input.model,enabled:true,available:true,contextTokens:8192}]};
case 'api_provider_model':return{id:args.id,name:'Local fixture',models:[{id:args.model,profileId:'api:provider-fixture:'+args.model,enabled:true,available:true,contextTokens:8192}]};
default:return null;}}};
window.fetch=async(url,options={})=>{const route=new URL(url).pathname.replace('/api',''),body=options.body?JSON.parse(options.body):{};fixture.calls.push({route,body,headers:options.headers});let value={};
if(route==='/status')value={catalog:fixture.catalog,progress:fixture.progress,tokenConfigured:fixture.token};
else if(route==='/plan')value={planId:'plan-fixture',repository:'fixture/tiny-model',revision:'a'.repeat(40),variants:fixture.variants};
else if(route==='/catalog/add'){fixture.catalog=fixture.variants.filter(v=>body.variantIds.includes(v.id));value={added:fixture.catalog.map(v=>v.id),catalog:fixture.catalog};}
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
await page.getByRole('button',{name:'Install',exact:true}).click();await page.getByRole('button',{name:'Installing…',exact:true}).waitFor();
record('install immediately locks its card',await page.getByRole('button',{name:'Installing…',exact:true}).isDisabled());
await page.waitForTimeout(1400);record('Hub progress uses the shared top panel',await page.locator('.model-install-progress progress').getAttribute('value')==='30');
await page.locator('.model-install-progress').getByRole('button',{name:'Pause installation',exact:true}).click();await page.getByRole('button',{name:'Install',exact:true}).waitFor();
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
record('no runtime errors',errors.length===0);await page.screenshot({path:path.join(__dirname,'hub-qa.png'),fullPage:true});
}catch(error){console.log('QA failure',error.message,'errors',errors,'body',(await page.locator('body').innerText()).slice(0,6000));throw error;}finally{await browser.close();await new Promise(r=>server.close(r));fs.writeFileSync(path.join(__dirname,'hub-qa.json'),JSON.stringify({results,errors},null,2));}console.log(JSON.stringify(results,null,2));if(results.some(r=>!r.pass))process.exitCode=1;})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
