const fs=require('fs'),path=require('path'),Module=require('module');
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=original.slice(0,original.indexOf('(async()=>{await new Promise'));
const data=String.raw`const presetCatalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'),'utf8'));
const presetScript='fixture.catalog='+JSON.stringify(presetCatalog.entries).replaceAll('<','\\u003c')+';';`;
code=code.replace('const html=',data+'\nconst html=').replace('${fixture}</script>','${fixture}${presetScript}</script>');
code=code.replace('<title>OpenCore Hub QA</title>','<title>OpenCore Hub QA</title><link rel="icon" href="data:,">');
code+=String.raw`
(async()=>{
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
const page=await browser.newPage({viewport:{width:1450,height:1050}}),results=[],errors=[],consoleErrors=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('console',message=>{if(message.type()==='error')consoleErrors.push({text:message.text(),location:message.location()});});
const record=(name,pass)=>results.push({name,pass:!!pass});
try{
await page.goto('http://127.0.0.1:'+server.address().port);
await page.getByRole('article').first().waitFor();
const searches=page.getByRole('textbox',{name:'Search models',exact:true});
record('the model library renders on its intended local QA page',await page.title()==='OpenCore Hub QA'&&new URL(page.url()).hostname==='127.0.0.1');
if(await searches.count()!==1)throw new Error('Expected the existing model search field: '+JSON.stringify(await page.locator('input').evaluateAll(inputs=>inputs.map(input=>({type:input.type,placeholder:input.placeholder,label:input.getAttribute('aria-label')})))));
for(const id of ['llama32-1b','sage-a15-q4-k-m','holo4-27b']){
 const entry=presetCatalog.entries.find(entry=>entry.nativeModelIds?.includes(id));
 if(!entry)throw new Error('Missing bundled family '+id);
 await searches.fill(entry.familyLabel);
 const card=page.getByRole('article',{name:entry.familyLabel,exact:true});await card.waitFor();
 record(id+' has at least five preset choices before any repository check',await card.locator('select option').count()>=5);
 record(id+' offers direct installation',await card.getByRole('button',{name:'Install',exact:true}).isEnabled());
 await card.screenshot({path:path.join(__dirname,'.output/builtin-'+id+'.png')});
}
const requested=[['publisher-hobbylm-1b-computer-use',1],['publisher-mano-cua-2-0-4b',14],['publisher-terminal-rsi-27b',28],['publisher-discoveryrsi-27b',29],['publisher-tiel-coder-apex-miniplus-v2-1',1],['publisher-tals-coder-flash-02',1],['publisher-kestrel-4b-coder',5],['publisher-hi3d-v1',1],['publisher-qwen35-4b-3d-coder',3],['publisher-lfm3-vl-2b',8],['publisher-lfm3-1-7b',8],['publisher-carbon-x',13],['publisher-apex-35b-v1',26],['publisher-groot-19b-a3b-261023',28]];
const screenshotDirectory=process.env.OPENCORE_MODEL_QA_DIR||path.join(require('os').tmpdir(),'opencore-requested-models-qa');fs.mkdirSync(screenshotDirectory,{recursive:true});
for(const [family,count] of requested){
 const entries=presetCatalog.entries.filter(entry=>entry.familyId===family),entry=entries[0];
 if(!entry)throw new Error('Missing requested model '+family);
 await searches.fill(entry.familyLabel);
 const card=page.getByRole('article',{name:entry.familyLabel,exact:true});await card.waitFor();
 record(entry.familyLabel+' has one card with every reviewed preset package',await card.count()===1&&await card.locator('select option').count()===count);
 record(entry.familyLabel+' has a direct Install button',await card.getByRole('button',{name:'Install',exact:true}).isEnabled());
 if(family==='publisher-hobbylm-1b-computer-use')record('HobbyLM explains the separate base model and missing Windows adapter',await card.innerText().then(text=>text.includes('harims95/hobbylm-1B-instruct')&&text.includes('no OpenCore Windows adapter')));
 else if(entries.some(value=>value.format==='gguf'&&['Q4_K_M','Q8_0'].includes(value.quantization))){
  const quant=entries.find(value=>value.format==='gguf'&&value.quantization==='Q4_K_M')||entries.find(value=>value.format==='gguf'&&value.quantization==='Q8_0');
  await card.locator('select').selectOption(quant.id);
  record(entry.familyLabel+' switches to its pinned '+quant.quantization+' package',await card.locator('select').inputValue()===quant.id&&await card.innerText().then(text=>text.includes(quant.repository)));
 }
 else{
  record(entry.familyLabel+' keeps the publisher custom quantization label',await card.locator('select').inputValue()===entry.id&&await card.locator('select option').first().innerText().then(text=>text.includes(entry.quantization)));
  if(family==='publisher-tals-coder-flash-02')record('Tals shows its required Hadamard runtime without claiming readiness',await card.innerText().then(text=>text.includes('Hadamard')&&text.includes('Compatible adapter required')&&!text.includes('Ready to use')));
 }
 if(family==='publisher-groot-19b-a3b-261023'){
  record('Groot preserves its exact publisher identity and ECHO working window',await card.innerText().then(text=>text.includes('GrootTeam/Groot-19B-A3B-261023')&&text.includes('32K working window')));
  const fp4=entries.find(value=>value.format==='safetensors'&&value.quantization==='NVFP4');await card.locator('select').selectOption(fp4.id);
  record('Groot NVFP4 shows its actual runtime requirement',await card.innerText().then(text=>text.includes('compatible NVFP4 hardware')&&text.includes('Compatible adapter required')&&!text.includes('Ready to use')));
 }
 if(family==='publisher-kestrel-4b-coder'){
  record('Kestrel offers exactly its four publisher GGUFs plus the original BF16 without duplicate cards',entries.filter(value=>value.format==='gguf').length===4&&await card.locator('select option').count()===5&&await card.innerText().then(text=>text.includes('32K working window')));
 }
 await card.screenshot({path:path.join(screenshotDirectory,family+'.png')});
}
record('opening and browsing built-in cards never inspects or adds a Hugging Face repository',await page.evaluate(()=>!fixture.calls.some(call=>call.route==='/plan'||call.route==='/catalog/add')));
record('opening preset cards does not download weights',await page.evaluate(()=>!fixture.calls.some(call=>call.route==='/install')));
record('the app has no community-quantization search controls',await page.getByRole('button',{name:/Find.*quantization/i}).count()===0&&await page.getByRole('checkbox',{name:/community/i}).count()===0);
await searches.fill('Mano-CUA 2.0 4B');
const manoCard=page.getByRole('article',{name:'Mano-CUA 2.0 4B',exact:true}),mano=presetCatalog.entries.find(entry=>entry.familyId==='publisher-mano-cua-2-0-4b'&&entry.format==='gguf'&&entry.quantization==='Q4_K_M');
await manoCard.locator('select').selectOption(mano.id);await manoCard.getByRole('button',{name:'Install',exact:true}).click();
await manoCard.getByRole('button',{name:'Installing…',exact:true}).waitFor();
record('requested model installation uses its selected preset and locks duplicate installs',await manoCard.getByRole('button',{name:'Installing…',exact:true}).isDisabled()&&await page.evaluate(id=>fixture.calls.some(call=>call.route==='/install'&&call.body.id===id),mano.id));
await page.locator('.model-install-progress progress').waitFor();
record('requested model installation appears in the shared progress panel',await page.locator('.model-install-progress progress').getAttribute('value')==='30');
await page.locator('.model-install-progress').getByRole('button',{name:'Pause installation',exact:true}).click();await manoCard.getByRole('button',{name:'Install',exact:true}).waitFor();
record('pausing the requested download releases its Install button',await manoCard.getByRole('button',{name:'Install',exact:true}).isEnabled());
const kestrel=presetCatalog.entries.find(entry=>entry.familyId==='publisher-kestrel-4b-coder'&&entry.format==='gguf'&&entry.quantization==='Q4_K_M');
await searches.fill(kestrel.familyLabel);const kestrelCard=page.getByRole('article',{name:kestrel.familyLabel,exact:true});
await kestrelCard.locator('select').selectOption(kestrel.id);await kestrelCard.getByRole('button',{name:'Install',exact:true}).click();
await kestrelCard.getByRole('button',{name:'Installing…',exact:true}).waitFor();
record('Kestrel installs its selected publisher Q4 package and locks duplicate installation',await kestrelCard.getByRole('button',{name:'Installing…',exact:true}).isDisabled()&&await page.evaluate(id=>fixture.calls.some(call=>call.route==='/install'&&call.body.id===id),kestrel.id));
await page.locator('.model-install-progress progress').waitFor();
record('Kestrel appears in the shared download progress panel',await page.locator('.model-install-progress progress').getAttribute('value')==='30');
await page.locator('.model-install-progress').getByRole('button',{name:'Pause installation',exact:true}).click();await kestrelCard.getByRole('button',{name:'Install',exact:true}).waitFor();
record('pausing Kestrel releases its Install button',await kestrelCard.getByRole('button',{name:'Install',exact:true}).isEnabled());
const groot=presetCatalog.entries.find(entry=>entry.familyId==='publisher-groot-19b-a3b-261023'&&entry.repository==='GrootTeam/Groot-19B-A3B-261023-GGUF');
await searches.fill(groot.familyLabel);const grootCard=page.getByRole('article',{name:groot.familyLabel,exact:true});
await grootCard.locator('select').selectOption(groot.id);await grootCard.getByRole('button',{name:'Install',exact:true}).click();
await grootCard.getByRole('button',{name:'Installing…',exact:true}).waitFor();
record('Groot installs the exact publisher Q4 package and prevents duplicate installation',await grootCard.getByRole('button',{name:'Installing…',exact:true}).isDisabled()&&await page.evaluate(id=>fixture.calls.some(call=>call.route==='/install'&&call.body.id===id),groot.id));
await page.locator('.model-install-progress').getByRole('button',{name:'Pause installation',exact:true}).click();await grootCard.getByRole('button',{name:'Install',exact:true}).waitFor();
record('no framework error overlay or browser console errors',await page.locator('vite-error-overlay,nextjs-portal').count()===0&&consoleErrors.length===0);
record('no browser runtime errors',errors.length===0);
}finally{
await browser.close();await new Promise(resolve=>server.close(resolve));
fs.writeFileSync(path.join(__dirname,'.output/builtin-preset-ui-verification.json'),JSON.stringify({results,errors,consoleErrors},null,2));
}
console.log(JSON.stringify(results,null,2));if(results.some(result=>!result.pass))process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});`;
const filename=path.join(__dirname,'qa-builtin-presets.virtual.cjs'),test=new Module(filename,module);
test.filename=filename;test.paths=Module._nodeModulePaths(__dirname);test._compile(code,filename);
