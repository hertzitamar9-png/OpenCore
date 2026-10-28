const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict'),acorn=require('acorn');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT||'playwright');
const directory=path.join(__dirname,'candidate-dist'),entryName='index-CD0EH-dA.js',appName='App-kNA9sRUP.js';
let entry=fs.readFileSync(path.join(directory,'assets',entryName),'utf8');
const mount=acorn.parse(entry,{ecmaVersion:'latest',sourceType:'module'}).body.find(n=>n.type==='ExpressionStatement'&&entry.slice(n.start,n.end).includes('createRoot('));
entry=entry.slice(0,mount.start)+entry.slice(mount.end)+'\nexport{ev as TestDOM};';
let app=fs.readFileSync(path.join(directory,'assets',appName),'utf8');
const panel=acorn.parse(app,{ecmaVersion:'latest',sourceType:'module'}).body.find(n=>n.type==='VariableDeclaration'&&app.slice(n.start,n.end).includes('Chat image generation'));
app+='\nexport{Hp as TestPicker,x8 as TestFooter,'+panel.declarations[0].id.name+' as TestPanel};';
const fixture=`window.fixture={calls:[],jobs:[]};
const moss={id:'moss-image-sft-int8-convrot',label:'MOSS Image',category:'image',backend:'comfyui',precision:'INT8',installed:true,selectable:false};
const qwen={id:'qwen-image-21-uc-nvfp4',label:'Qwen Image',category:'image',backend:'comfyui',precision:'NVFP4',installed:true,selectable:false};
window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{fixture.calls.push({command,args});switch(command){
case 'runtime_setup_probe':return{hubBridge:{available:true,url:'http://127.0.0.1:59999',secret:'fixture-only-secret',authHeader:'X-OpenCore-Hub-Secret'}};
case 'list_model_library':return{models:[moss,qwen,{...moss,id:'not-installed',label:'Not downloaded',installed:false},{...moss,id:'lora',label:'Adapter only',hubEntry:{packageKind:'adapter',format:'lora'}},{id:'echo',label:'ECHO 3T',description:'Text model',category:'text',installed:true,selectable:true}],progress:null};
case 'list_studio_jobs':return fixture.jobs;
case 'runtime_setup_status':return{jobs:[],recipes:[],receipts:[moss,qwen].map(model=>({targetId:model.id,dependenciesVerified:true,inferenceVerified:true,python:'C:/managed/python.exe',runner:'C:/managed/worker.py'})),activeJobId:null};
case 'submit_studio_job':{const job={id:'native-picker-job',category:'image',request:args.request,status:'completed',stage:'Generation complete',createdAt:new Date().toISOString(),outputs:[]};fixture.jobs.push(job);return job;}
default:return[];}}};
window.fetch=async()=>({ok:true,json:async()=>({catalog:[],jobs:[],progress:null,tokenConfigured:false,catalogRevision:'a'.repeat(64)})});`;
const html=`<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><link rel=stylesheet href=/assets/index-CsoxYKnK.css><link rel=stylesheet href=/assets/App-C9duitZV.css><div id=root style="max-width:700px;margin:auto"></div><script>${fixture}</script><script type=module>
import{r as React,TestDOM}from'/assets/${entryName}';import{TestPicker,TestFooter,TestPanel}from'/assets/${appName}';
const h=React.createElement;TestDOM.createRoot(document.getElementById('root')).render(h(React.Fragment,null,
h('div',{className:'chat-composer-wrap'},h(TestPanel,{conversationId:'qa-picker-chat'}),h('div',{className:'chat-composer'},h('textarea',{'aria-label':'Message OpenCore',defaultValue:'Keep this draft'}),h(TestPicker,{selectedProfile:'echo',id:'composer-model-profile-options',onSelect:id=>fixture.calls.push({command:'text_profile',id}),onSelectImage:model=>globalThis.__opencoreSelectChatImageModel(model)}))),
h('div',{style:{position:'fixed',bottom:12,right:16}},h(TestFooter,{selectedProfile:'echo',onSelect:id=>fixture.calls.push({command:'text_profile',id}),blockedReason:null}))));</script>`;
const server=http.createServer((req,res)=>{const name=new URL(req.url,'http://local').pathname;if(name==='/'){res.setHeader('Content-Type','text/html');return res.end(html);}res.setHeader('Content-Type',name.endsWith('.css')?'text/css':'text/javascript');if(name==='/assets/'+entryName)return res.end(entry);if(name==='/assets/'+appName)return res.end(app);const file=path.join(directory,name);if(!file.startsWith(directory)||!fs.existsSync(file)){res.statusCode=404;return res.end();}res.end(fs.readFileSync(file));});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true,args:['--mute-audio']});const page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];page.on('pageerror',error=>errors.push(error.message));try{
await page.goto('http://127.0.0.1:'+server.address().port);
const picker=page.locator('#composer-model-profile-options');await picker.getByRole('button',{name:/Qwen Image/}).waitFor();assert.equal(await picker.getByRole('button',{name:/Not downloaded|Adapter only/}).count(),0);
await picker.getByRole('button',{name:/Qwen Image/}).click();await page.waitForFunction(()=>document.querySelector('[aria-label="Downloaded image model"]')?.value==='qwen-image-21-uc-nvfp4');assert.match(await page.getByRole('button',{name:/Choose model profile, currently/}).getAttribute('aria-label'),/Qwen Image/);
await page.getByLabel('Image generation prompt').fill('A blue cup in the same conversation');await page.getByRole('button',{name:'Generate image',exact:true}).click();await page.waitForFunction(()=>fixture.jobs.length===1);const request=await page.evaluate(()=>fixture.jobs[0].request);assert.equal(request.modelId,'qwen-image-21-uc-nvfp4');assert.equal(request.conversationId,'qa-picker-chat');assert.equal(await page.evaluate(()=>fixture.calls.some(call=>['text_profile','start_runtime','send_message'].includes(call.command))),false);
await page.getByRole('button',{name:'Text',exact:true}).click();await page.getByLabel('Message OpenCore').waitFor();assert.equal(await page.getByLabel('Message OpenCore').inputValue(),'Keep this draft');
await page.getByRole('button',{name:/Choose model profile, currently/}).click();await page.locator('.statusbar-model-popover').getByRole('button',{name:/MOSS Image/}).click();await page.waitForFunction(()=>document.querySelector('[aria-label="Downloaded image model"]')?.value==='moss-image-sft-int8-convrot');assert.equal(await page.locator('.statusbar-model-popover').count(),0);
await page.getByRole('button',{name:/Choose model profile, currently/}).click();await page.locator('.statusbar-model-popover').getByRole('button',{name:/^ECHO 3T/}).click();await page.getByLabel('Message OpenCore').waitFor();assert.equal(await page.evaluate(()=>fixture.calls.filter(call=>call.command==='text_profile').length),1);
await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:/Choose model profile, currently/}).click();const imageOption=page.locator('.statusbar-model-popover').getByRole('button',{name:/Qwen Image/});assert.ok((await imageOption.boundingBox()).height>=40);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
console.log('PASS: chat and footer image model selection, exact selected model, image-only dispatch, retained text draft, return to text, and phone picker.');
}catch(error){console.error('PAGE ERRORS',errors);console.error((await page.locator('body').innerText()).slice(0,1200));throw error;}finally{await browser.close();server.close();}})().catch(error=>{console.error(error);process.exitCode=1});
