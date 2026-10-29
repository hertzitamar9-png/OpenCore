const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-chat-image-picker.cjs'),'utf8');
let fixture=original.slice(0,original.indexOf('\n(async()=>'));
fixture=fixture.replace("export{ev as TestDOM};","export{ev as TestDOM,L0 as TestSend};");
fixture=fixture.replace('</script><script type=module>',`const priorFetch=window.fetch;window.fetch=async(url,options={})=>{fixture.calls.push({route:new URL(url).pathname.replace('/api',''),body:options.body?JSON.parse(options.body):{}});return priorFetch(url,options);};</script><script type=module>`);
fixture+=String.raw`
(async()=>{
await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true,args:['--mute-audio']});
const page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
try{
await page.goto('http://127.0.0.1:'+server.address().port);await page.getByLabel('Message OpenCore').waitFor();
const gear=page.getByRole('button',{name:'Generation settings',exact:true});
assert.equal(await gear.evaluate(element=>{let next=element.nextElementSibling;while(next&&getComputedStyle(next).display==='none')next=next.nextElementSibling;return next?.classList.contains('approval-trigger');}),true,'Settings must be immediately left of Approvals');
await gear.click();const dialog=page.getByRole('dialog',{name:'Generation settings',exact:true});await dialog.waitFor();
assert.equal(await dialog.getByRole('button',{name:'Automatic',exact:true}).getAttribute('aria-pressed'),'true');assert.equal(await dialog.getByLabel('Temperature',{exact:true}).isDisabled(),true);
await dialog.getByRole('button',{name:'Manual',exact:true}).click();await dialog.getByLabel('Temperature',{exact:true}).fill('0.25');await dialog.getByLabel('Top K',{exact:true}).fill('20');await dialog.getByLabel('Maximum output tokens',{exact:true}).fill('512');await dialog.getByRole('button',{name:'Done',exact:true}).click();
await page.evaluate(async()=>{const {TestSend}=await import('/assets/index-CD0EH-dA.js');await TestSend('qa-chat','A settings test',[],'off','ask');});
let config=await page.evaluate(()=>fixture.calls.filter(call=>call.route==='/generation/settings').at(-1));assert.ok(config,JSON.stringify(await page.evaluate(()=>({bridge:!!globalThis.__opencoreInstallBridge,settings:globalThis.__opencoreGenerationRequest('qa-chat'),calls:fixture.calls.slice(-5)}))));assert.equal(config.body.parameters.temperature,.25);assert.equal(config.body.parameters.top_k,20);assert.equal(config.body.parameters.max_tokens,512);
const sent=await page.evaluate(()=>fixture.calls.find(call=>call.command==='send_chat_message'));assert.equal(sent.args.request.generationSettings,undefined,'Internal preferences must be removed before native deserialization');
await gear.click();assert.equal(await dialog.getByRole('button',{name:'Manual',exact:true}).getAttribute('aria-pressed'),'true');await dialog.getByRole('button',{name:'Automatic',exact:true}).click();await dialog.getByRole('button',{name:'Done',exact:true}).click();
await page.evaluate(async()=>{const {TestSend}=await import('/assets/index-CD0EH-dA.js');await TestSend('qa-chat','Automatic again',[],'off','ask');});
config=await page.evaluate(()=>fixture.calls.filter(call=>call.route==='/generation/settings').at(-1));assert.equal(config.body.parameters,null,'Automatic clears manual overrides');
await page.evaluate(()=>globalThis.__opencoreSelectChatImageModel({id:'qwen-image-21-uc-nvfp4',label:'Qwen Image'}));await page.getByText('Qwen Image · Automatic quality',{exact:true}).waitFor();
await gear.click();assert.equal(await dialog.getByLabel('Sampling steps',{exact:true}).inputValue(),'40');assert.equal(await dialog.getByLabel('Sampling steps',{exact:true}).isDisabled(),true);
await dialog.getByRole('button',{name:'Manual',exact:true}).click();await dialog.getByLabel('Sampling steps',{exact:true}).fill('19');await dialog.getByLabel('Guidance (CFG)',{exact:true}).fill('1.3');await dialog.getByLabel('Output width',{exact:true}).fill('1024');await dialog.getByLabel('Output height',{exact:true}).fill('1024');await dialog.getByLabel('Seed (blank = random)',{exact:true}).fill('42');
for(const width of [360,390,1400]){await page.setViewportSize({width,height:width<600?844:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow at '+width);const box=await dialog.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width);if(process.env.OPENCORE_GENERATION_QA_DIR){fs.mkdirSync(process.env.OPENCORE_GENERATION_QA_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.OPENCORE_GENERATION_QA_DIR,'generation-settings-'+width+'.png')});}}
await dialog.getByRole('button',{name:'Done',exact:true}).click();await page.getByLabel('Message OpenCore').fill('An orange owl');await page.getByRole('button',{name:'Send message',exact:true}).click();await page.waitForFunction(()=>fixture.calls.some(call=>call.command==='submit_studio_job'));
const request=await page.evaluate(()=>fixture.calls.find(call=>call.command==='submit_studio_job').args.request);assert.equal(request.settings.steps,19);assert.equal(request.settings.guidanceScale,1.3);assert.equal(request.settings.width,1024);assert.equal(request.settings.seed,42);
assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,settingsLeftOfApprovals:true,automaticDefault:true,manualTextReachesBridge:true,automaticClearsOverrides:true,manualImageReachesWorker:true,viewportWidths:[360,390,1400],consoleErrors:errors}));
}finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1});`;
const filename=path.join(__dirname,'qa-generation-settings.fixture.cjs'),compiled=new Module(filename,module);compiled.filename=filename;compiled.paths=Module._nodeModulePaths(__dirname);compiled._compile(fixture,filename);
