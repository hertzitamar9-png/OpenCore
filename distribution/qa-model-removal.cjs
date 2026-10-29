const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let fixture=original.slice(0,original.indexOf('(async()=>{await new Promise'));
fixture=fixture.replace("else if(route==='/pause')",`else if(route==='/removal-plan'){value={modelId:body.id,label:'tiny-model Q4_K_M',files:Array.from({length:18},(_,i)=>({path:'C:/OpenCore/models/hub/'+body.id+'/model-file-'+i+'.bin',bytes:1000000000,external:false,sharedWith:[]})),retainedFiles:[],totalBytes:18000000000,confirmationToken:'confirmed-fixture'};}
else if(route==='/uninstall'){fixture.catalog.find(entry=>entry.id===body.id).status='not-installed';value={uninstalled:true};}
else if(route==='/pause')`);
fixture+=String.raw`
(async()=>{
await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true,args:['--mute-audio']});
const page=await browser.newPage({viewport:{width:1400,height:950}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
try{
await page.goto('http://127.0.0.1:'+server.address().port);
await page.getByRole('heading',{name:'Add from Hugging Face'}).waitFor();
await page.evaluate(async()=>{fixture.catalog=[{...fixture.variants[0],status:'installed'}];const {TestInvoke}=await import('/assets/App-kNA9sRUP.js');const library=await TestInvoke('list_model_library',{},()=>window.__TAURI_INTERNALS__.invoke('list_model_library'));window.dispatchEvent(new CustomEvent('opencore-model-library-changed',{detail:library}));});
const card=page.getByRole('article',{name:'fixture/tiny-model',exact:true});await card.getByRole('button',{name:'Uninstall',exact:true}).click();
const dialog=page.getByRole('dialog');await dialog.getByText('in 18 local files',{exact:true}).waitFor();
if(!await dialog.getByText('18.00 GB',{exact:true}).isVisible())throw Error('Missing total model size');
await dialog.getByText('Files to remove',{exact:true}).click();
if(await dialog.locator('.model-delete-files li').count()!==18)throw Error('Missing file listing');
if(process.env.OPENCORE_REMOVAL_SCREENSHOT)await page.screenshot({path:process.env.OPENCORE_REMOVAL_SCREENSHOT});
await dialog.getByRole('button',{name:'Uninstall model',exact:true}).click();await dialog.waitFor({state:'hidden'});
const calls=await page.evaluate(()=>fixture.calls.filter(call=>call.route==='/uninstall'));
if(calls.length!==1||calls[0].body.confirmationToken!=='confirmed-fixture')throw Error('Deletion did not use exactly one reviewed plan');
if(errors.length)throw Error(errors.join('\n'));
console.log(JSON.stringify({passed:true,filesListed:18,totalGigabytes:18,singleConfirmation:true,consoleErrors:errors}));
}finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1});`;
const filename=path.join(__dirname,'qa-model-removal.fixture.cjs'),compiled=new Module(filename,module);compiled.filename=filename;compiled.paths=Module._nodeModulePaths(__dirname);compiled._compile(fixture,filename);
