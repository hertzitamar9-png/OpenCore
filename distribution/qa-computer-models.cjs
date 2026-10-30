const fs=require('fs'),path=require('path'),Module=require('module');
// Browser plugin not available; exercise the packaged UI with the existing Playwright fixture.
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=original.slice(0,original.indexOf('(async()=>{await new Promise'));
const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'),'utf8'));
const entries=catalog.entries.filter(entry=>entry.computerRuntime).map(entry=>({...entry,status:'installed',specialist:'computer',specialistReady:true}));
code=code.replace('${fixture}</script>','${fixture}fixture.catalog='+JSON.stringify(entries).replaceAll('<','\\u003c')+';</script>');
code=code.replace("else if(route==='/plan')", "else if(route==='/specialist/computer/status'){value=fixture.computerJob||{status:'idle'};}else if(route==='/specialist/computer'){value=fixture.computerJob={id:'computer-fixture',modelId:body.id,status:'running',stage:'Loading the selected local model',startedAt:Date.now()/1000};}else if(route==='/specialist/computer/stop'){fixture.computerJob={...fixture.computerJob,status:'failed',error:'Stopped'};value={stopped:true};}else if(route==='/plan')");
code+=String.raw`
(async()=>{
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,args:['--mute-audio']});
const page=await browser.newPage({viewport:{width:1450,height:1000}}),errors=[],results=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('console',message=>{if(message.type()==='error'&&!message.text().includes('favicon'))errors.push(message.text());});
const record=(name,pass)=>results.push({name,pass:!!pass});
const output=path.join(require('os').tmpdir(),'opencore-computer-model-qa');fs.mkdirSync(output,{recursive:true});
try{
await page.goto('http://127.0.0.1:'+server.address().port);
await page.getByRole('article').first().waitFor();
record('the local model library has the intended page identity',await page.title()==='OpenCore Hub QA'&&new URL(page.url()).hostname==='127.0.0.1');
const search=page.getByRole('textbox',{name:'Search models',exact:true});await search.fill('HobbyLM');
const hobby=page.getByRole('article',{name:'HobbyLM 1B Computer Use',exact:true});
record('computer-use cards identify the verified action runtime accurately',await hobby.getByText('Computer-use · Action prediction',{exact:true}).count()===1&&await hobby.getByText('Local action prediction verified',{exact:true}).count()===1&&await hobby.getByText('Local OCR verified',{exact:true}).count()===0);
await hobby.getByRole('button',{name:'Use computer-use',exact:true}).click();
const prediction=hobby.getByRole('region',{name:'Computer-use prediction'});
await prediction.getByRole('textbox',{name:'Task',exact:true}).fill('Open the calculator');
record('the computer adapter does not open the image LoRA base picker',await hobby.locator('.hub-base-picker').count()===0);
await prediction.getByRole('button',{name:'Predict action',exact:true}).click();
await prediction.getByRole('progressbar',{name:'Computer-use prediction progress'}).waitFor();
record('a real request carries the selected model and task',await page.evaluate(()=>fixture.calls.some(call=>call.route==='/specialist/computer'&&call.body.task==='Open the calculator'&&call.body.id==='hf-de5e149fce74297ac87d954d')));
record('prediction shows progress and an elapsed clock',await prediction.innerText().then(text=>text.includes('Elapsed')&&text.includes('Loading')));
await page.evaluate(()=>{fixture.computerJob={...fixture.computerJob,status:'complete',elapsedSeconds:1.5,result:{text:'OPEN Calculator',outputTokens:3}};});
await prediction.getByText('OPEN Calculator',{exact:true}).waitFor();
record('completion shows the returned action and measured duration',await prediction.innerText().then(text=>text.includes('Took 1.5s'))&&await prediction.getByRole('button',{name:'Predict action',exact:true}).isEnabled());
await page.screenshot({path:path.join(output,'desktop-hobby.png')});
await search.fill('GUI-JEPA');const gui=page.getByRole('article',{name:'GUI-JEPA',exact:true});
await gui.getByRole('button',{name:'Use computer-use',exact:true}).click();
const policy=gui.getByRole('region',{name:'Computer-use prediction'});
await policy.getByRole('textbox',{name:'Task',exact:true}).fill('Tap Save');
record('GUI-JEPA requires screenshot and real candidates before running',await policy.getByRole('button',{name:'Predict action',exact:true}).isDisabled());
await policy.getByLabel('Screenshot',{exact:true}).setInputFiles({name:'screen.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH7sAAAAASUVORK5CYII=','base64')});
await policy.getByRole('textbox',{name:'Accessibility candidates (JSON)',exact:true}).fill('[{"text":"Save","bounds":[0.1,0.2,0.3,0.4],"clickable":true}]');
await policy.getByRole('button',{name:'Predict action',exact:true}).click();
await policy.getByRole('button',{name:'Stop prediction',exact:true}).waitFor();
record('GUI-JEPA sends the uploaded image and candidate tree with its own model identity',await page.evaluate(()=>fixture.calls.some(call=>call.route==='/specialist/computer'&&call.body.id==='hf-423d29d79a8b3e9673477770'&&call.body.image&&call.body.candidates.length===1)));
await policy.getByRole('button',{name:'Stop prediction',exact:true}).click();
await policy.getByText('Stopped',{exact:true}).waitFor();
record('the stop control reaches the owned computer-use service',await page.evaluate(()=>fixture.calls.some(call=>call.route==='/specialist/computer/stop')));
await page.setViewportSize({width:390,height:844});
record('phone controls stay within the viewport',await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));
await page.screenshot({path:path.join(output,'phone-policy.png'),fullPage:true});
record('the interface is meaningful without a framework overlay or runtime errors',await page.locator('article').count()>0&&await page.locator('vite-error-overlay,nextjs-portal').count()===0&&errors.length===0);
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
fs.writeFileSync(path.join(output,'verification.json'),JSON.stringify({results,errors},null,2));
console.log(JSON.stringify({results,errors},null,2));if(results.some(result=>!result.pass))process.exitCode=1;
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});`;
const filename=path.join(__dirname,'qa-computer-models.virtual.cjs'),test=new Module(filename,module);
test.filename=filename;test.paths=Module._nodeModulePaths(__dirname);test._compile(code,filename);
