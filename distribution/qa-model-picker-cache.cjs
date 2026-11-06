// Exercise the actual bundled model picker with isolated native/Hub fixtures.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=original.slice(0,original.indexOf('(async()=>{await new Promise'));
code=code.replace('Up as TestProfiles','Up as TestProfiles,Hp as TestOptions');
code=code.replace('TestModels,TestHubSettings}from','TestModels,TestHubSettings,TestOptions}from');
code=code.replace("case 'get_snapshot':", "case 'api_provider_list':return [];case 'get_snapshot':");
code=code.replace("TestDOM.createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement(TestModels,{selectedProfile:'none',onSelect:id=>window.selected=id,runtimeActive:false,onNotice:message=>window.notice=message}),React.createElement(TestHubSettings)));", `
fixture.nativeModels=[{id:'fixture-text',label:'Fixture text',category:'text',installed:true,selectable:true},{id:'fixture-image',label:'Fixture image',category:'image',installed:true,selectable:false}];
globalThis.__opencoreModelLibrary.setScope('picker-qa');
function TestPicker(){const [open,setOpen]=React.useState(false);return React.createElement(React.Fragment,null,React.createElement('button',{onClick:()=>setOpen(!open)},'Toggle picker'),open?React.createElement(TestOptions,{selectedProfile:'stopped',onSelect:()=>{},onSelectImage:()=>{}}):null)}
TestDOM.createRoot(document.getElementById('root')).render(React.createElement(TestPicker));`);
code+=String.raw`
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const browser=await chromium.launch({headless:true,args:['--mute-audio']});try{
 const page=await browser.newPage({viewport:{width:1200,height:850}}),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:'+server.address().port);
 const toggle=page.getByRole('button',{name:'Toggle picker'});await toggle.click();await page.getByRole('button',{name:/Fixture image/}).waitFor();await page.getByRole('button',{name:/Fixture text/}).waitFor();
 await page.waitForTimeout(500);await toggle.click();const counts=await page.evaluate(()=>({models:fixture.calls.filter(c=>c.command==='list_model_library').length,providers:fixture.calls.filter(c=>c.command==='api_provider_list').length,status:fixture.calls.filter(c=>c.route==='/status').length}));
 await toggle.click();await page.getByRole('button',{name:/Fixture image/}).waitFor();await page.getByRole('button',{name:/Fixture text/}).waitFor();await page.waitForTimeout(300);
 const after=await page.evaluate(()=>({models:fixture.calls.filter(c=>c.command==='list_model_library').length,providers:fixture.calls.filter(c=>c.command==='api_provider_list').length,status:fixture.calls.filter(c=>c.route==='/status').length}));
 if(JSON.stringify(counts)!==JSON.stringify(after))throw Error('Reopening the picker performed another lookup: '+JSON.stringify({counts,after}));
 await page.evaluate(()=>window.dispatchEvent(new CustomEvent('opencore-model-library-changed',{detail:{models:[{id:'fixture-new',label:'New image',category:'image',installed:true,selectable:false}]}})));
 await page.getByRole('button',{name:/New image/}).waitFor();if(await page.getByRole('button',{name:/Fixture image/}).count())throw Error('Removed image stayed in the picker');
 await toggle.click();await page.evaluate(()=>window.dispatchEvent(new CustomEvent('opencore-model-library-changed',{detail:{models:[{id:'fixture-later',label:'Added while closed',category:'image',installed:true,selectable:false}]}})));
 await toggle.click();await page.getByRole('button',{name:/Added while closed/}).waitFor();if(await page.getByRole('button',{name:/New image/}).count())throw Error('The picker ignored a change made while it was closed');
 if(errors.length)throw Error(errors.join('\n'));console.log('PASS bundled model picker reuses known choices and handles model changes');await page.close();
 }finally{await browser.close();server.close()}})().catch(error=>{console.error(error);process.exitCode=1});`;
const compiled=new Module(__filename,module);compiled.filename=__filename;compiled.paths=module.paths;compiled._compile(code,__filename);
