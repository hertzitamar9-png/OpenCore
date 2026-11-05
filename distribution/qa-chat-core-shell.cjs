// Exercise the shipped App shell and Chat Core navigation using isolated fixtures.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=original.slice(0,original.indexOf('(async()=>{await new Promise'));
code=code.replace('Up as TestProfiles','Up as TestProfiles,w8 as TestApp');
code=code.replace('TestModels,TestHubSettings}from','TestModels,TestHubSettings,TestApp}from');
code=code.replace("TestDOM.createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement(TestModels,{selectedProfile:'none',onSelect:id=>window.selected=id,runtimeActive:false,onNotice:message=>window.notice=message}),React.createElement(TestHubSettings)));",
  "delete window.__TAURI_INTERNALS__;window.__opencoreGroupService=async(action)=>action==='list'?{groups:[],localName:'Tester'}:{};TestDOM.createRoot(document.getElementById('root')).render(React.createElement(TestApp));");
code+=String.raw`
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const browser=await chromium.launch({headless:true,args:['--mute-audio']});try{
 for(const width of [1440,393]){
  const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  if(width<500)await page.addInitScript(()=>{window.__OPENCORE_MOBILE__=true;});
  await page.goto('http://127.0.0.1:'+server.address().port);
  if(width<500)await page.getByRole('navigation',{name:'OpenCore sections'}).getByRole('button',{name:'Chat Core',exact:true}).click();
  else await page.getByRole('button',{name:'Open Chat Core',exact:true}).click();
  await page.getByRole('region',{name:'Chat Core',exact:true}).waitFor();
  if(await page.getByRole('dialog',{name:'Chat Core',exact:true}).count())throw Error('Chat Core opened as a dialog');
  if(!(await page.locator('.section-main .group-embedded').count()))throw Error('Chat Core is outside the app workspace');
  if(width>=1000){await page.getByRole('button',{name:'Workspace',exact:true}).click();await page.locator('#opencore-workspace').waitFor();}
  if(!await page.locator('.statusbar').innerText().then(text=>text.includes('No model selected')))throw Error('Fresh model selection is misleading');
  if(errors.length)throw Error(errors.join('\n'));await page.close();console.log('PASS main app shell with Chat Core at '+width+'px');
 }
}finally{await browser.close();server.close()}})().catch(error=>{console.error(error);process.exitCode=1});`;
const compiled=new Module(__filename,module);compiled.filename=__filename;compiled.paths=module.paths;compiled._compile(code,__filename);
