// Run the released composer and saved-answer renderer against native API fixtures.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
let fixture=fs.readFileSync(path.join(__dirname,'qa-chat-image-picker.cjs'),'utf8').split('\n(async()=>')[0];
fixture=fixture.replace("const html=", "app+='\\nexport{Jv as TestArtifact};';\nconst html=");
fixture=fixture.replace("case 'preview_attachment_image':return png;", "case 'preview_attachment_image':return png;case 'preview_artifact':return {text:'<!doctype html><html><body>saved snake source</body></html>',mime:'text/markdown',name:'answer.md'};");
fixture=fixture.replace('TestComposer,TestFooter,TestChanges}', 'TestComposer,TestFooter,TestChanges,TestArtifact}');
fixture=fixture.replace('h(TestChanges,{','h(TestArtifact,{id:"saved-answer",name:"Complete answer.md",mime:"text/markdown",size:12000}),h(TestChanges,{');
fixture=fixture.replace("runtimeRunning:false,runtimeSnapshot:{status:'stopped'", "runtimeRunning:true,runtimeSnapshot:{status:'running'");
fixture=fixture.replace("inferenceBlocked:'Text runtime is not loaded'", 'inferenceBlocked:null');
fixture+=String.raw`
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,args:['--mute-audio']}),page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.goto('http://127.0.0.1:'+server.address().port);
  const message=page.getByLabel('Message OpenCore');await message.waitFor();
  await page.getByText('<!doctype html><html><body>saved snake source</body></html>',{exact:true}).waitFor();
  assert.equal(await page.getByText('Complete answer.md',{exact:true}).count(),0);
  await message.fill('');const unload=page.getByRole('button',{name:'Unload model',exact:true});await unload.waitFor();
  assert.equal(await unload.isEnabled(),true);await unload.click();
  await page.waitForFunction(()=>fixture.calls.some(call=>call.command==='stop_runtime'));
  assert.equal(await page.evaluate(()=>fixture.calls.filter(call=>call.command==='stop_runtime').length),1);
  await message.fill('A normal text message');await page.getByRole('button',{name:'Send message',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Unload model',exact:true}).count(),0);
  for(const width of [390,1400]){
    await page.setViewportSize({width,height:width===390?844:1000});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    if(process.env.OPENCORE_DELIVERABLE_QA_DIR){fs.mkdirSync(process.env.OPENCORE_DELIVERABLE_QA_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.OPENCORE_DELIVERABLE_QA_DIR,'chat-'+width+'.png')});}
  }
  assert.deepEqual(errors,[]);console.log('PASS: full saved HTML source stays in chat, empty composer unloads the real runtime, typing restores Send, desktop and phone widths.');
 }catch(error){console.error('Renderer errors:',errors);console.error((await page.locator('body').innerText()).slice(0,1200));throw error;}finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1});`;
const filename=path.join(__dirname,'qa-chat-deliverables.fixture.cjs'),compiled=new Module(filename,module);compiled.filename=filename;compiled.paths=Module._nodeModulePaths(__dirname);compiled._compile(fixture,filename);
