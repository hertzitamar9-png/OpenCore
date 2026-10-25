const fs=require('fs'),path=require('path'),Module=require('module');
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=original.slice(0,original.indexOf('(async()=>{await new Promise'));
const data=String.raw`const presetCatalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'),'utf8'));
const presetScript='fixture.catalog='+JSON.stringify(presetCatalog.entries).replaceAll('<','\\u003c')+';';`;
code=code.replace('const html=',data+'\nconst html=').replace('${fixture}</script>','${fixture}${presetScript}</script>');
code+=String.raw`
(async()=>{
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
const page=await browser.newPage({viewport:{width:1450,height:1050}}),results=[],errors=[];
page.on('pageerror',error=>errors.push(error.message));
const record=(name,pass)=>results.push({name,pass:!!pass});
try{
await page.goto('http://127.0.0.1:'+server.address().port);
await page.getByRole('article').first().waitFor();
const searches=page.getByRole('textbox',{name:'Search models',exact:true});
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
record('opening and browsing built-in cards never inspects or adds a Hugging Face repository',await page.evaluate(()=>!fixture.calls.some(call=>call.route==='/plan'||call.route==='/catalog/add')));
record('opening preset cards does not download weights',await page.evaluate(()=>!fixture.calls.some(call=>call.route==='/install')));
record('the app has no community-quantization search controls',await page.getByRole('button',{name:/Find.*quantization/i}).count()===0&&await page.getByRole('checkbox',{name:/community/i}).count()===0);
record('no browser runtime errors',errors.length===0);
}finally{
await browser.close();await new Promise(resolve=>server.close(resolve));
fs.writeFileSync(path.join(__dirname,'.output/builtin-preset-ui-verification.json'),JSON.stringify({results,errors},null,2));
}
console.log(JSON.stringify(results,null,2));if(results.some(result=>!result.pass))process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});`;
const filename=path.join(__dirname,'qa-builtin-presets.virtual.cjs'),test=new Module(filename,module);
test.filename=filename;test.paths=Module._nodeModulePaths(__dirname);test._compile(code,filename);
