// Browser plugin not available. Exercise the exact recovered update component
// with Playwright and a disposable native IPC fixture; never install a live app.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict'),acorn=require('acorn');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT||'playwright');
const root=__dirname,directory=path.join(root,'candidate-dist'),output=path.join(root,'.output');
fs.mkdirSync(output,{recursive:true});
const screenshotOutput=process.env.OPENCORE_UPDATE_QA_DIR||path.join(require('node:os').tmpdir(),'opencore-update-controls-qa');
fs.mkdirSync(screenshotOutput,{recursive:true});
const current=process.env.OPENCORE_RELEASE_VERSION||'0.3.0';
const next=current.split('.').map(Number);next[2]++;
const later=[...next];later[2]++;
const indexName='index-CD0EH-dA.js',appName='App-kNA9sRUP.js';
let entry=fs.readFileSync(path.join(directory,'assets',indexName),'utf8');
const tree=acorn.parse(entry,{ecmaVersion:'latest',sourceType:'module'});
const mount=tree.body.find(node=>node.type==='ExpressionStatement'&&entry.slice(node.start,node.end).includes('createRoot('));
entry=entry.slice(0,mount.start)+entry.slice(mount.end)+'\nexport{ev as TestDOM};';
const app=fs.readFileSync(path.join(directory,'assets',appName),'utf8')+'\nexport{Pj as TestUpdate};';
const html=`<!doctype html><title>OpenCore update controls QA</title><link rel="icon" href="data:,"><link rel="stylesheet" href="/assets/index-CsoxYKnK.css"><link rel="stylesheet" href="/assets/App-C9duitZV.css"><style>body{background:#0c1016;color:#dce4ef;margin:0;padding:24px}.qa-header{display:flex;align-items:center;gap:12px}.qa-header .manual-update-control{margin-left:auto}</style><header class="qa-header"><img src="/opencore-logo.png" alt="OpenCore" width="40" height="40"><strong>OpenCore</strong><div id="root"></div></header><script>
window.fixture={calls:[],version:${JSON.stringify(next.join('.'))},fail:false,complete:null,reject:null};
window.__TAURI_INTERNALS__={invoke:async(command,args)=>{fixture.calls.push(command);if(command==='check_latest_app_version'){if(fixture.fail)throw Error('Feed unavailable');return{currentVersion:'0.2.305',available:true,version:fixture.version};}if(command==='install_latest_app_update')return new Promise((resolve,reject)=>{fixture.complete=resolve;fixture.reject=reject;});if(command==='runtime_setup_status')return{jobs:[],receipts:[]};if(command==='model_install_progress')return null;if(command==='list_model_library')return{models:[],progress:null};if(command==='api_provider_list')return[];if(command==='plugin:app|version')return'0.2.305';return null;}};
</script><script type="module">import{r as React,TestDOM}from'/assets/${indexName}';import{TestUpdate}from'/assets/${appName}';TestDOM.createRoot(document.getElementById('root')).render(React.createElement(TestUpdate));</script>`;
const server=http.createServer((request,response)=>{
 const name=new URL(request.url,'http://local').pathname;
 if(name==='/'){response.setHeader('Content-Type','text/html');return response.end(html);}
 if(name==='/opencore-logo.png'){response.setHeader('Content-Type','image/png');return response.end(fs.readFileSync(path.join(root,'../src-tauri/icons/icon.png')));}
 response.setHeader('Content-Type',name.endsWith('.css')?'text/css':'text/javascript');
 if(name==='/assets/'+indexName)return response.end(entry);
 if(name==='/assets/'+appName)return response.end(app);
 const file=path.resolve(directory,'.'+name);
 if(!file.startsWith(directory+path.sep)||!fs.existsSync(file)){response.statusCode=404;return response.end();}
 response.end(fs.readFileSync(file));
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,args:['--mute-audio'],...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{})});
 const page=await browser.newPage({viewport:{width:1280,height:250}}),errors=[],results=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
 const installs=()=>page.evaluate(()=>fixture.calls.filter(command=>command==='install_latest_app_update').length);
 try{
  await page.clock.install();
  const url='http://127.0.0.1:'+server.address().port;
  await page.goto(url);assert.equal(await page.title(),'OpenCore update controls QA');assert.equal(page.url(),url+'/');
  await page.getByRole('button',{name:'Update ready',exact:true}).waitFor();
  assert.equal(await page.getByText('v'+current,{exact:true}).count(),1);assert.equal(await installs(),0);
  results.push({name:'startup checks metadata and displays the installed distribution version without downloading',pass:true});
  const readyButton=await page.getByRole('button',{name:'Update ready',exact:true}).elementHandle();
  await page.screenshot({path:path.join(screenshotOutput,'update-ready.png')});
  await page.getByRole('button',{name:'Update ready',exact:true}).click();
  await page.getByRole('button',{name:'Updating…',exact:true}).waitFor();
  assert.equal(await installs(),1);assert.equal(await page.getByRole('button',{name:'Updating…',exact:true}).isDisabled(),true);
  assert.equal(await page.getByRole('button',{name:'Download update',exact:true}).count(),0);
  assert.equal(await page.getByRole('button').count(),1);
  assert.equal(await page.getByRole('button',{name:'Updating…',exact:true}).evaluate((element,previous)=>element===previous,readyButton),true);
  await page.getByRole('progressbar',{name:'Update download in progress'}).waitFor();
  await page.clock.fastForward(6000);
  assert.equal(await page.getByText('Preparing and downloading the update…',{exact:true}).isVisible(),true);
  await page.getByRole('button',{name:'Updating…',exact:true}).dispatchEvent('click');assert.equal(await installs(),1);
  await page.screenshot({path:path.join(screenshotOutput,'update-in-progress.png')});
  results.push({name:'one click starts the update and the same button shows Updating with duplicate clicks disabled',pass:true});
  await page.evaluate(()=>fixture.reject(Error('Network interrupted')));
  await page.getByText('Could not install the update: Error: Network interrupted',{exact:true}).waitFor();
  assert.equal(await page.getByRole('progressbar',{name:'Update download in progress'}).count(),0);
  assert.equal(await page.getByRole('button',{name:'Update ready',exact:true}).isEnabled(),true);assert.equal(await installs(),1);
  results.push({name:'failure unlocks the same button and never retries automatically',pass:true});
  await page.getByRole('button',{name:'Update ready',exact:true}).click();await page.getByRole('button',{name:'Updating…',exact:true}).waitFor();assert.equal(await installs(),2);
  await page.evaluate(()=>fixture.complete());
  await page.getByText('The installer is starting. OpenCore will reopen after the update.',{exact:true}).waitFor();
  results.push({name:'retry and installer handoff use the same control without a second download button',pass:true});
  await page.evaluate(version=>{fixture.version=version;},current);
  await page.clock.fastForward(300001);
  await page.getByText('Up to date',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Update ready',exact:true}).count(),0);assert.equal(await installs(),2);
  results.push({name:'an installed release is not offered again against the pinned native version',pass:true});
  await page.evaluate(version=>{fixture.version=version;},later.join('.'));
  await page.clock.fastForward(300001);
  await page.getByRole('button',{name:'Update ready',exact:true}).waitFor();assert.equal(await installs(),2);
  results.push({name:'the five-minute check exposes the next main release without downloading',pass:true});
  await page.evaluate(()=>{fixture.fail=true;});await page.clock.fastForward(300001);
  await page.getByRole('button',{name:'Check for updates',exact:true}).waitFor();assert.equal(await page.getByText('Up to date',{exact:true}).count(),0);
  results.push({name:'a failed check is not presented as Up to date',pass:true});
  const logo=await page.getByRole('img',{name:'OpenCore',exact:true}).evaluate(image=>({complete:image.complete,width:image.naturalWidth}));
  assert.equal(logo.complete,true);assert.ok(logo.width>0);assert.deepEqual(errors,[]);
  const report={url,viewport:{width:1280,height:250},browser:'Playwright; Browser plugin not available',results,errors,passed:true};
  fs.writeFileSync(path.join(output,'update-controls-qa.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
