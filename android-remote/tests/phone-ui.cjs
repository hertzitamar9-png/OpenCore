'use strict';
// Browser proof of bundled phone UI behavior. Native inference has a separate real-model check.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT||'playwright');
const root=path.join(__dirname,'../assets/local'),output=path.join(__dirname,'../../distribution/.output/phone-ui');
const server=http.createServer((req,res)=>{const name=new URL(req.url,'http://localhost').pathname;const file=name==='/opencore-logo.png'?path.join(__dirname,'../../public/opencore-logo.png'):path.join(root,name==='/'?'index.html':name.slice(1));if(!fs.existsSync(file)){res.statusCode=404;res.end();return;}res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.png')?'image/png':'text/html');res.end(fs.readFileSync(file));});
const fixture=()=>{
 document.addEventListener('DOMContentLoaded',()=>{
  const channel=new MessageChannel();window.phoneFixture={calls:[],messages:[],chats:[],models:[],loadedId:'',generating:false};const f=window.phoneFixture;
  const settings={device:'phone',paired:true,pcName:'Test PC',context:2048,maxTokens:512,temperature:.7,tokenConfigured:false};
  const model={id:'a'.repeat(64),filename:'Starter-Q4.gguf',bytes:105454432,architecture:'llama',runtimeReady:false};
  const state=()=>({models:f.models,chats:f.chats,loadedId:f.loadedId,generating:f.generating,downloading:!!f.downloading,generation:{},download:{},settings,runtime:'Fixture CPU',runtimeAvailable:true,availableMemory:4e9,freeDisk:20e9});
  const emit=(event,value)=>channel.port1.postMessage(JSON.stringify({event,value}));
  f.emit=emit;f.model=model;f.state=state;
  channel.port1.onmessage=async event=>{
   const p=JSON.parse(event.data);f.calls.push(p);let value=null;
   try{
    if(p.action==='state')value=state();
    else if(p.action==='catalog')value={items:Array.from({length:100},(_,i)=>({id:'builtin-'+i,label:'Built-in '+i,category:'text',files:5})),page:p.args.page||0,pages:9,total:900};
    else if(p.action==='chat.create'){await new Promise(r=>setTimeout(r,40));const chat={id:'chat-'+f.chats.length,title:'Phone conversation'};f.chats.push(chat);value={id:chat.id};}
    else if(p.action==='chat.get')value=f.messages;
    else if(p.action==='chat.delete'){f.chats=f.chats.filter(c=>c.id!==p.args.chat);f.messages=[];}
    else if(p.action==='hub.search'){value={items:Array.from({length:100},(_,i)=>({id:'publisher/Model-'+(p.args.cursor?i+100:i),downloads:1000})),nextCursor:p.args.cursor?'':'/api/models?cursor=page2'};}
    else if(p.action==='hub.inspect')value={repo:p.args.repo,note:'Verified fixture files',files:[{...model,problem:''},{...model,id:'c'.repeat(64),filename:'Starter-Q8.gguf',problem:''},{id:'b'.repeat(64),filename:'model.safetensors',bytes:30e9,problem:'This format needs the paired PC runtime.'}]};
    else if(p.action==='download'){f.downloading=true;if(!f.holdDownload)setTimeout(()=>{f.models=[model];f.downloading=false;emit('state',state());emit('download',{phase:'Downloaded · verified weights',total:105454432,received:105454432});},30);else emit('download',{phase:'Downloading',total:105454432,received:1000});}
    else if(p.action==='download.cancel'){f.downloading=false;emit('download',{phase:'Stopped'});emit('state',state());}
    else if(p.action==='send'){f.generating=true;f.loadedId=model.id;f.messages.push({id:'user',role:'user',content:p.args.text,status:'completed',metrics:{}},{id:'assistant',role:'assistant',content:'',status:'running',metrics:{}});value={assistant:'assistant'};}
    else if(p.action==='cancel'){f.generating=false;emit('generation',{chat:p.args.chat||'chat-0',assistant:'assistant',stage:'Stopped',running:false,status:'stopped',content:'Partial native output',metrics:{promptTokens:16,completionTokens:4,elapsedMs:1500,context:2048,tokensPerSecond:2.6}});emit('state',state());}
    else if(p.action==='unload'){f.loadedId='';emit('state',state());}
    else if(p.action==='settings.save'){Object.assign(settings,p.args);delete settings.token;settings.tokenConfigured=!!p.args.token;value=settings;}
    channel.port1.postMessage(JSON.stringify({id:p.id,ok:true,value}));
   }catch(error){channel.port1.postMessage(JSON.stringify({id:p.id,ok:false,error:error.message}));}
  };channel.port1.start();window.postMessage('opencore-phone:__OPENCORE_NONCE__',location.origin,[channel.port2]);
 });
};
(async()=>{
 fs.mkdirSync(output,{recursive:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true,args:['--mute-audio']});const errors=[];
 try{
  for(const width of [360,393,430]){
   const page=await browser.newPage({viewport:{width,height:852},isMobile:true,hasTouch:true});page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());const external=[];page.on('request',r=>{if(!r.url().startsWith('http://127.0.0.1:'))external.push(r.url());});await page.addInitScript(fixture);await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>phoneFixture.calls.some(p=>p.action==='state'));
   assert.equal(await page.getByText('On this phone',{exact:true}).isVisible(),true);assert.equal(await page.getByRole('button',{name:'Phone',exact:false}).first().isVisible(),true);assert.equal((await page.evaluate(()=>phoneFixture.calls)).some(p=>/pc|remote|pair/.test(p.action)),false,'First launch must not connect to a PC');
   await page.getByRole('button',{name:'Models',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('#catalog-results .card').length===100);await page.getByRole('button',{name:'Next 100',exact:true}).click();await page.waitForFunction(()=>phoneFixture.calls.some(p=>p.action==='catalog'&&p.args.page===1));
   await page.getByRole('button',{name:'Small starter model'}).click();await page.evaluate(()=>phoneFixture.holdDownload=true);await page.getByRole('button',{name:'Download to phone'}).first().click();await page.getByRole('button',{name:'Stop',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#quantizations [data-file-id]').disabled);
   await page.getByRole('button',{name:'Download to phone'}).first().click();await page.waitForFunction(()=>phoneFixture.calls.filter(p=>p.action==='download').length===2&&phoneFixture.downloading);await page.evaluate(()=>{phoneFixture.downloading=false;phoneFixture.emit('download',{phase:'Download failed',error:'Test interrupted download'});phoneFixture.emit('state',phoneFixture.state());});await page.waitForFunction(()=>!document.querySelector('#quantizations [data-file-id]').disabled);
   await page.evaluate(()=>phoneFixture.holdDownload=false);await page.getByRole('button',{name:'Download to phone'}).first().click();await page.waitForFunction(()=>phoneFixture.models.length===1);await page.getByRole('button',{name:'Use in chat'}).click();await page.getByRole('textbox',{name:'Message OpenCore'}).fill('A neutral local prompt');
   await page.locator('#composer').evaluate(form=>{form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));});
   await page.waitForFunction(()=>phoneFixture.calls.some(p=>p.action==='send'));assert.equal(await page.evaluate(()=>phoneFixture.calls.filter(p=>p.action==='send').length),1,'Rapid clicks create one generation');assert.equal(await page.evaluate(()=>phoneFixture.calls.filter(p=>p.action==='chat.create').length),1,'Rapid clicks create one chat');
   await page.evaluate(()=>phoneFixture.emit('generation',{chat:'chat-0',assistant:'assistant',stage:'Generating on this phone',running:true,content:'Real native output appears here <script>window.injected=true</script>',metrics:{promptTokens:16,completionTokens:5,elapsedMs:2000,context:2048,tokensPerSecond:2.5}}));await page.getByText('Real native output appears here <script>window.injected=true</script>',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>window.injected),undefined,'Model output cannot execute JavaScript');await page.getByRole('button',{name:'Stop generation'}).click();await page.getByRole('button',{name:'Unload phone model'}).waitFor();await page.getByRole('button',{name:'Unload phone model'}).click();
   await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('button',{name:'Choose Phone or Paired PC'}).click();await page.waitForFunction(()=>phoneFixture.calls.some(p=>p.action==='device-settings'));await page.getByRole('button',{name:'Save generation settings'}).click();await page.getByRole('button',{name:'Dismiss message'}).click();
   await page.getByRole('button',{name:'Models',exact:true}).click();await page.getByRole('textbox',{name:'Search model names or enter owner/model'}).fill('Qwen');await page.getByRole('button',{name:'Search',exact:true}).click();await page.getByRole('button',{name:'Next 100 models'}).waitFor();assert.equal(await page.locator('#hub-results .card').count(),100);await page.getByRole('button',{name:'Next 100 models'}).click();await page.waitForFunction(()=>document.querySelectorAll('#hub-results .card').length===200);assert.equal(await page.evaluate(()=>phoneFixture.calls.filter(p=>p.action==='hub.search').at(-1).args.cursor),'/api/models?cursor=page2');
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);assert.equal(overflow,false,'No horizontal overflow at '+width);assert.deepEqual(external,[],'Local UI must not use network or PC requests');await page.screenshot({path:path.join(output,'phone-models-'+width+'.png')});
   await page.getByRole('button',{name:'Chats',exact:true}).click();await page.screenshot({path:path.join(output,'phone-chat-'+width+'.png')});await page.getByRole('button',{name:'Show conversations'}).click();await page.getByRole('button',{name:'Open Phone conversation'}).waitFor();await page.close();
  }
  assert.deepEqual(errors,[]);console.log('Bundled phone UI: offline launch, local routing, quant download, single submission, streaming, Stop, unload, settings, 100-result pagination, XSS and 360/393/430px layout passed');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
