// Browser plugin not available. Exercise the shipped entry, support-page cache
// and model library with Playwright; IPC stays inside a disposable fixture.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),os=require('node:os');
const assert=require('node:assert/strict'),acorn=require('acorn');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT||'playwright');
const root=__dirname,directory=path.join(root,'candidate-dist');
const screenshots=process.env.OPENCORE_VIEW_QA_DIR||path.join(os.tmpdir(),'opencore-cached-views-qa');
fs.mkdirSync(screenshots,{recursive:true});
const indexName='index-CD0EH-dA.js',appName='App-kNA9sRUP.js';
let entry=fs.readFileSync(path.join(directory,'assets',indexName),'utf8');
const tree=acorn.parse(entry,{ecmaVersion:'latest',sourceType:'module'});
const mount=tree.body.find(node=>node.type==='ExpressionStatement'&&entry.slice(node.start,node.end).includes('createRoot('));
entry=entry.slice(0,mount.start)+entry.slice(mount.end)+'\nexport{ev as TestDOM,Xv as TestStartup};';
const app=fs.readFileSync(path.join(directory,'assets',appName),'utf8')+'\nexport{h8 as TestSupport,$RetainedViews as TestViews};';
const fixtureSource=fs.readFileSync(path.join(root,'qa-model-install.cjs'),'utf8');
const fixtureTree=acorn.parse(fixtureSource,{ecmaVersion:'latest',sourceType:'script'});
const fixtureNode=fixtureTree.body.flatMap(node=>node.declarations||[]).find(node=>node.id.name==='fixture').init;
assert.equal(fixtureNode.expressions.length,0);
const nativeFixture=fixtureNode.quasis[0].value.cooked;
const baseHtml=(title,script,module)=>`<!doctype html><meta charset="utf-8"><title>${title}</title><link rel="icon" href="data:,"><link rel="stylesheet" href="/assets/index-CsoxYKnK.css"><link rel="stylesheet" href="/assets/App-C9duitZV.css"><style>body{margin:0;background:#0c1016;color:#dce4ef}.qa-nav{display:flex;padding:12px;gap:12px}#root{height:100vh}.qa-content{height:calc(100vh - 70px)}</style><div id="root"></div><script>${script}</script><script type="module">${module}</script>`;
const startupScript=`window.fixture={attempts:0,accountCalls:0,authenticated:false,mode:'slow'};window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};window.__TAURI_INTERNALS__={metadata:{currentWindow:{label:'main'},currentWebview:{label:'main'}},transformCallback:()=>1,unregisterCallback:()=>{},invoke:async(command,args)=>{if(command==='account_action'){fixture.accountCalls++;throw Error('Optional account service is offline');}if(command==='get_snapshot'){fixture.attempts++;if(fixture.mode==='offline')return new Promise(()=>{});if(fixture.mode==='slow')await new Promise(resolve=>{fixture.releaseStartup=resolve;});return{conversations:[],runtime:{status:'stopped'}};}return null;}};`;
const startup=baseHtml('OpenCore startup QA',startupScript,`
import{r as React,TestDOM,TestStartup}from'/assets/${indexName}';
const root=TestDOM.createRoot(document.getElementById('root'));
window.showStartup=()=>root.render(React.createElement(TestStartup,{key:fixture.mode,workspace:()=>React.createElement('main',null,'Workspace is open')}));
window.hideStartup=()=>root.render(null);window.showStartup();`);
const modelsScript=nativeFixture+`
const originalInvoke=window.__TAURI_INTERNALS__.invoke;
fixture.reads=0;fixture.holdLibrary=sessionStorage.getItem('hold-library')==='yes';
window.__TAURI_INTERNALS__.invoke=async(command,args)=>{if(command==='list_model_library'){fixture.reads++;if(fixture.holdLibrary)await new Promise(resolve=>fixture.releaseLibrary=resolve);}return originalInvoke(command,args);};`;
const models=baseHtml('OpenCore cached Models QA',modelsScript,`
import{r as React,TestDOM}from'/assets/${indexName}';import{TestSupport,TestViews}from'/assets/${appName}';
globalThis.__opencoreModelLibrary.setScope('qa-model-account');await globalThis.__opencoreModelLibrary.hydrate();
const root=TestDOM.createRoot(document.getElementById('root'));
function Navigation(){const[view,setView]=React.useState('models');return React.createElement(React.Fragment,null,
React.createElement('nav',{className:'qa-nav'},...['models','other'].map(value=>React.createElement('button',{key:value,onClick:()=>setView(value)},value==='models'?'Models tab':'Other tab'))),
React.createElement('div',{className:'qa-content section-main'},React.createElement(TestViews,{view,content:view==='models'?React.createElement(TestSupport,{view:'models',snapshot:{runtime:{status:'stopped',modelPath:''},activeConversationIds:[]},selectedProfile:'none',onSelectProfile:()=>{},onNotice:()=>{},onRefresh:async()=>{},appearance:{},platformConfiguration:{}}):React.createElement('p',null,'Another tab is open')})));}
root.render(React.createElement(Navigation));`);
const server=http.createServer((request,response)=>{
 const name=new URL(request.url,'http://local').pathname;
 if(name==='/startup'||name==='/models'){response.setHeader('Content-Type','text/html');return response.end(name==='/startup'?startup:models);}
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
 const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[],results=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
 const record=name=>results.push({name,pass:true});
 const url='http://127.0.0.1:'+server.address().port;
 try{
  await page.clock.install();
  await page.goto(url+'/startup');assert.equal(await page.title(),'OpenCore startup QA');
  // Hold native startup explicitly, including when a slow CI worker misses a brief splash.
  await page.waitForFunction(()=>fixture.releaseStartup);
  await page.clock.runFor(3000);
  await page.getByRole('heading',{name:'Opening OpenCore',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Retry opening OpenCore',exact:true}).count(),0);
  await page.getByRole('progressbar',{name:'Opening your local workspace'}).waitFor();
  await page.screenshot({path:path.join(screenshots,'startup-preparing.png')});
  await page.evaluate(()=>fixture.releaseStartup());
  await page.clock.runFor(1700);await page.getByText('Workspace is open',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>fixture.attempts),1);assert.equal(await page.evaluate(()=>fixture.accountCalls),0);
  record('local IPC opens the workspace with progress and no hosted account request');
  await page.evaluate(()=>{fixture.mode='signed-out';window.showStartup();});
  await page.getByText('Workspace is open',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>fixture.authenticated),false);
  assert.equal(await page.evaluate(()=>fixture.accountCalls),0);
  assert.equal(await page.evaluate(()=>globalThis.__opencoreModelLibrary.getScope()),'device-local');
  record('local use keeps account authentication unchanged and caches only device-local metadata');
  await page.evaluate(()=>{fixture.mode='offline';window.showStartup();});
  await page.getByRole('heading',{name:'Opening OpenCore',exact:true}).waitFor();
  await page.clock.runFor(10000);assert.equal(await page.getByRole('button',{name:'Retry opening OpenCore',exact:true}).count(),0);
  await page.clock.runFor(11000);await page.getByRole('button',{name:'Retry opening OpenCore',exact:true}).waitFor();
  record('a stalled local request reaches a visible bounded error instead of an endless splash');
  await page.evaluate(()=>{fixture.mode='recovered';});
  await page.getByRole('button',{name:'Retry opening OpenCore',exact:true}).click();
  await page.getByText('Workspace is open',{exact:true}).waitFor();
  record('Retry reopens the real local workspace without changing account authentication');
  await page.goto(url+'/models');assert.equal(await page.title(),'OpenCore cached Models QA');
  await page.getByRole('heading',{name:'EdgeRock-4B',exact:true}).waitFor();
  const search=page.getByRole('textbox',{name:'Search models',exact:true});
  await search.fill('EdgeRock');const previous=await search.elementHandle();
  const repository=page.getByRole('textbox',{name:'Hugging Face repository',exact:true});
  await repository.fill('publisher/unfinished-selection');
  const reads=await page.evaluate(()=>fixture.reads);
  await page.getByRole('button',{name:'Other tab',exact:true}).click();
  await page.getByText('Another tab is open',{exact:true}).waitFor();
  assert.equal(await page.getByRole('heading',{name:'EdgeRock-4B',exact:true}).count(),0);
  await page.getByRole('button',{name:'Models tab',exact:true}).click();
  assert.equal(await search.inputValue(),'EdgeRock');
  assert.equal(await repository.inputValue(),'publisher/unfinished-selection');
  assert.equal(await search.evaluate((element,old)=>element===old,previous),true);
  assert.equal(await page.evaluate(()=>fixture.reads),reads);
  record('switching away and back retains the actual model DOM, search and completed library read');
  await search.fill('');
  const pane=page.locator('[data-opencore-page="models"]');
  const scroll=await pane.evaluate(element=>{element.scrollTop=220;return element.scrollTop;});
  assert.ok(scroll>0);
  await page.getByRole('button',{name:'Other tab',exact:true}).click();
  await page.getByRole('button',{name:'Models tab',exact:true}).click();
  assert.equal(await pane.evaluate(element=>element.scrollTop),scroll);
  record('returning to Models keeps its scroll position and unfinished repository input');
  await page.screenshot({path:path.join(screenshots,'models-retained.png')});
  await page.evaluate(()=>sessionStorage.setItem('hold-library','yes'));
  await page.reload();
  await page.getByRole('heading',{name:'EdgeRock-4B',exact:true}).waitFor();
  await page.waitForFunction(()=>fixture.releaseLibrary);
  assert.equal(await page.evaluate(()=>fixture.reads),1);
  assert.equal(await page.getByText('Loading model library…',{exact:true}).count(),0);
  record('a new launch shows saved cards immediately while the live catalog is still loading');
  await page.evaluate(()=>{
    fixture.models.push({...fixture.models[0],id:'new-model',label:'New catalog model'});
    fixture.holdLibrary=false;fixture.releaseLibrary();sessionStorage.removeItem('hold-library');
  });
  await page.getByRole('heading',{name:'New catalog model',exact:true}).waitFor();
  record('a changed live catalog adds its new card without clearing existing cards');
  await page.evaluate(()=>{
    fixture.models=fixture.models.filter(model=>model.id!=='new-model');
    window.dispatchEvent(new Event('focus'));
  });
  await page.getByRole('heading',{name:'New catalog model',exact:true}).waitFor({state:'detached'});
  await page.getByRole('heading',{name:'EdgeRock-4B',exact:true}).waitFor();
  record('background revalidation also removes obsolete cards while retaining the library');
  assert.equal(await page.locator('vite-error-overlay,nextjs-portal').count(),0);assert.deepEqual(errors,[]);
  record('meaningful pages render without framework overlays or browser runtime and console errors');
  console.log(JSON.stringify({passed:true,browser:'Playwright; Browser plugin not available',url,results,errors},null,2));
  if(process.env.OPENCORE_VIEW_QA_REPORT)fs.writeFileSync(process.env.OPENCORE_VIEW_QA_REPORT,JSON.stringify({passed:true,results,errors},null,2));
 }catch(error){console.error('Rendered state:',(await page.locator('body').innerText()).slice(0,3000),'Errors:',errors);throw error;}
 finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
