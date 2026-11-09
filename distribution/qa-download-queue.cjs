// Exercise the shipped model cards and queue controls with the two transport paths.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict'),acorn=require('acorn');
const{chromium}=require(process.env.OPENCORE_PLAYWRIGHT||'playwright');
const dist=path.join(process.env.OPENCORE_UI_OUTPUT||__dirname,'candidate-dist');
const index='index-CD0EH-dA.js',appName='App-kNA9sRUP.js';
let entry=fs.readFileSync(path.join(dist,'assets',index),'utf8');
const mount=acorn.parse(entry,{ecmaVersion:'latest',sourceType:'module'}).body.find(node=>node.type==='ExpressionStatement'&&entry.slice(node.start,node.end).includes('createRoot('));
entry=entry.slice(0,mount.start)+entry.slice(mount.end)+'\nexport{ev as TestDOM};';
const app=fs.readFileSync(path.join(dist,'assets',appName),'utf8')+'\nexport{yj as TestModels};';
const html=`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel=stylesheet href=/assets/index-CsoxYKnK.css><link rel=stylesheet href=/assets/App-C9duitZV.css><div id=root></div><script>
window.fixture={calls:[],queue:{schema:1,mode:'sequential',paused:false,items:[],activeIds:[]}};
const base={description:'Queue fixture',contextTokens:8192,license:'test',precision:'Q4_K_M',experimental:false,note:'test',selectable:true,installed:false,externalManaged:false,downloadBytes:5e9,totalBytes:5e9,category:'text',installable:true,runtimeReady:true,memoryMode:'native',backend:'gguf'};
fixture.models=['A','B','C','D'].map(letter=>({...base,id:'model-'+letter,label:'Queue model '+letter,installed:letter==='D'}));
fixture.action=args=>{fixture.calls.push(args);const q=fixture.queue;
 if(args.action==='enqueue')for(const choice of args.items){if(q.items.some(item=>item.modelId===choice.modelId))continue;const id='job-'+q.items.length;const phase=q.mode==='parallel'||q.activeIds.length===0?'downloading':'queued';q.items.push({...choice,id,phase,position:q.items.length+1,downloadedBytes:1e9,totalBytes:5e9});if(phase==='downloading')q.activeIds.push(id);}
 if(args.action==='mode'){q.mode=args.mode;if(q.mode==='parallel')for(const item of q.items)if(item.phase==='queued'){item.phase='downloading';q.activeIds.push(item.id);}}
 if(args.action==='pause')q.paused=true;if(args.action==='resume')q.paused=false;
 if(args.action==='remove'){q.items=q.items.filter(item=>item.id!==args.id);q.activeIds=q.activeIds.filter(id=>id!==args.id);}
 return structuredClone(q);};
window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{switch(command){
 case'download_queue_action':return fixture.action(args);case'runtime_setup_probe':return{hubBridge:{available:true,url:'http://127.0.0.1:1',secret:'fixture'}};
 case'list_model_library':return{models:fixture.models,progress:null,diskFreeBytes:1e11,minimumFreeBytes:0};case'runtime_setup_status':return{recipes:[],jobs:[],receipts:[],activeJobId:null};case'model_install_progress':return null;
 case'speech_status':return{modelId:'none',installed:false,enabled:false,idleMode:'cold',workerReady:false,phase:'off'};case'model_weight_update_status':return{enabled:false,phase:'idle',models:[]};default:return null;}}};
window.fetch=async(url,options={})=>{const route=new URL(url).pathname,args=JSON.parse(options.body||'{}');const value=route==='/api/download-queue/action'?fixture.action(args):{catalog:[],progress:null,tokenConfigured:false,catalogRevision:'a'.repeat(64)};return{ok:true,status:200,json:async()=>value};};
</script><script type=module>import{r as React,TestDOM}from'/assets/${index}';import{TestModels}from'/assets/${appName}';
TestDOM.createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement(TestModels,{selectedProfile:'none',onSelect:()=>{},runtimeActive:false,onNotice:()=>{}}),React.createElement(globalThis.__opencoreDownloadQueueUI.Settings)));
</script>`;
const server=http.createServer((req,res)=>{const url=new URL(req.url,'http://local');if(url.pathname==='/'){res.setHeader('Content-Type','text/html');return res.end(html);}if(url.pathname==='/assets/'+index){res.setHeader('Content-Type','text/javascript');return res.end(entry);}if(url.pathname==='/assets/'+appName){res.setHeader('Content-Type','text/javascript');return res.end(app);}const file=path.resolve(dist,'.'+url.pathname);if(!file.startsWith(dist+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}res.setHeader('Content-Type',file.endsWith('.css')?'text/css':'text/javascript');res.end(fs.readFileSync(file));});
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const browser=await chromium.launch({headless:true,args:['--mute-audio']});try{for(const phone of [false,true]){
 const page=await browser.newPage({viewport:{width:phone?393:1440,height:900}}),errors=[];page.on('pageerror',error=>errors.push(error.message));await page.addInitScript(phone=>{globalThis.__OPENCORE_PHONE__=phone},phone);
 try{await page.goto('http://127.0.0.1:'+server.address().port);const card=label=>page.getByRole('article').filter({has:page.getByRole('heading',{name:'Queue model '+label,exact:true})});
 const queue=page.getByRole('region',{name:'Model download queue'});
 await queue.waitFor();
 await card('D').getByText('Installed',{exact:true}).waitFor();
 assert.equal(await page.getByRole('checkbox',{name:/Select download for/}).count(),0);
 assert.equal(await queue.getByRole('button',{name:/Download selected|Clear selection/}).count(),0);
 for(const label of ['A','B'])await card(label).getByRole('button',{name:'Install',exact:true}).click();
 await page.waitForFunction(()=>fixture.queue.items.length===2);assert.deepEqual(await page.evaluate(()=>fixture.queue.items.map(item=>item.phase)),['downloading','queued']);
 await card('B').getByText('Queued · position 2',{exact:true}).waitFor();
 await card('C').getByRole('button',{name:'Install',exact:true}).click();await page.waitForFunction(()=>fixture.queue.items.length===3);assert.equal(await card('C').getByRole('button',{name:'Install',exact:true}).isDisabled(),true);
 await page.getByLabel('Model download mode').selectOption('parallel');await page.waitForFunction(()=>fixture.queue.activeIds.length===3);
 await queue.getByText('3 active · 0 waiting',{exact:true}).waitFor();
 assert.ok(await page.getByRole('button',{name:'Cancel installation',exact:true}).count()>=3,'Separate cancel actions exist for active downloads');
 if(process.env.OPENCORE_QUEUE_QA_DIR)await page.screenshot({path:path.join(process.env.OPENCORE_QUEUE_QA_DIR,phone?'queue-phone.png':'queue-pc.png')});
 await card('B').getByRole('button',{name:'Cancel installation',exact:true}).click();
 await queue.getByText('2 active · 0 waiting',{exact:true}).waitFor();
 assert.deepEqual(errors,[]);console.log('PASS model cards without selection checkboxes: queued Install, per-card progress, parallel and cancel in '+(phone?'Phone':'PC')+' mode');
 }finally{await page.close();}
 }}finally{await browser.close();server.close();}})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
