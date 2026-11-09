/* Exercise the packaged model card; native execution is separately verified. */
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=original.slice(0,original.indexOf('(async()=>{await new Promise'));
const data=String.raw`const modes=JSON.parse(fs.readFileSync(path.join(__dirname,'../src-tauri/resources/runtime-setup/hf-extension/native-model-bindings.json'),'utf8'));
const family=modes.models.filter(m=>m.id==='neohorse-1-9b'||m.variantOf==='neohorse-1-9b');
const modeScript='fixture.nativeModels='+JSON.stringify(family.map(m=>({...m,installed:true,selectable:true,runtimeReady:true,totalBytes:100}))).replaceAll('<','\\u003c')+';fixture.modes='+JSON.stringify(modes).replaceAll('<','\\u003c')+';';`;
code=code.replace('const html=',data+'\nconst html=').replace('${fixture}</script>','${fixture}${modeScript}</script>')
  .replace("selectedProfile:'none'","selectedProfile:'neohorse-1-9b'").replace('runtimeActive:false','runtimeActive:true')
  .replace("case 'runtime_setup_probe':","case 'get_snapshot':return{activeConversationIds:[],runtime:{status:'running',profile:'neohorse-1-9b'}};case 'runtime_setup_probe':")
  .replace("confirmationToken:'verified-native-removal-plan'","confirmationToken:args.id+':'+(fixture.receiptRevision||0)")
  .replace("case 'uninstall_model':fixture.nativeModels=[];","case 'uninstall_model':if(args.confirmationToken!==args.id+':'+(fixture.receiptRevision||0))throw Error('Model files changed. Open Uninstall again and review the updated confirmation.');fixture.nativeModels=[];")
  .replace("if(route==='/status')","if(route==='/runtime/modes')value=fixture.modes;else if(route==='/runtime/native-profile'){const model=fixture.modes.models.find(m=>m.id===body.id);fixture.receiptRevision=(fixture.receiptRevision||0)+1;value={profileId:model.modeProfiles[body.operatingMode],weightsReused:true};}else if(route==='/status')");
code+=String.raw`
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
 const checks=[],errors=[],output=process.env.OPENCORE_MODE_QA_DIR||path.join(require('node:os').tmpdir(),'opencore-model-modes-20261102');fs.mkdirSync(output,{recursive:true});
 const check=(name,passed)=>{checks.push({name,passed:!!passed});if(!passed)throw Error(name);};
 try{for(const width of [1450,390]){
   const page=await browser.newPage({viewport:{width,height:950}});page.on('pageerror',error=>errors.push(error.message));
   await page.goto('http://127.0.0.1:'+server.address().port);
   await page.getByRole('textbox',{name:'Search models',exact:true}).fill('NeoHorse');
   const card=page.locator('.model-library-card').filter({has:page.getByRole('heading',{name:'NeoHorse 1 9B',exact:true})});await card.waitFor();
   check(width+' one card for Native and ECHO',await card.count()===1);
   const mode=card.getByRole('combobox',{name:'Operating mode',exact:true});await mode.waitFor();
   await (await mode.elementHandle()).waitForElementState('enabled');
   check(width+' two mode choices outside the quant menu',await mode.locator('option').count()===2);
   const quant=card.getByRole('combobox',{name:/^Quantization for /}),before=await quant.locator('option').allTextContents();
   check(width+' genuine quant menu includes all available files',before.length>=5);
   await mode.selectOption('native');await page.waitForFunction(()=>localStorage.getItem('opencore.model-mode.neohorse-1-9b')==='native');
   check(width+' same quants remain in Native',JSON.stringify(await quant.locator('option').allTextContents())===JSON.stringify(before));
   check(width+' supported cap remains truthful',await card.innerText().then(text=>text.includes('Supported window: 32,768 tokens')&&text.includes('below the 1,048,576 target')));
   check(width+' mode change does not install or redownload',await page.evaluate(()=>!fixture.calls.some(call=>call.route==='/install'||call.command==='install_model')));
   await card.getByRole('button',{name:'Use model',exact:true}).click();
   check(width+' Use selects Native execution',await page.evaluate(()=>window.selected?.endsWith('-native')));
   await page.reload();await page.getByRole('textbox',{name:'Search models',exact:true}).fill('NeoHorse');
   await card.waitFor();await (await mode.elementHandle()).waitForElementState('enabled');
   check(width+' Native selection survives restart',await mode.inputValue()==='native');
   await mode.selectOption('echo');
   await page.waitForFunction(()=>localStorage.getItem('opencore.model-mode.neohorse-1-9b')==='echo');
   check(width+' returning to ECHO retains quant options',JSON.stringify(await quant.locator('option').allTextContents())===JSON.stringify(before));
   await card.screenshot({path:path.join(output,'model-modes-'+width+'.png')});
   check(width+' mode selector stays within its card',await mode.evaluate(element=>{const a=element.getBoundingClientRect(),b=element.closest('.model-library-card').getBoundingClientRect();return a.left>=b.left&&a.right<=b.right;}));
   const receiptRevision=await page.evaluate(()=>fixture.receiptRevision||0);
   await card.getByRole('button',{name:/^Uninstall\b/}).click();
   const dialog=page.getByRole('dialog');
   await dialog.getByText('Files to remove',{exact:true}).waitFor();
   await dialog.getByRole('button',{name:'Stop runtime and uninstall',exact:true}).click();
   await dialog.waitFor({state:'hidden',timeout:5000}).catch(async error=>{
     throw Error(await dialog.getByRole('alert').textContent().catch(()=>null)||error.message);
   });
   check(width+' removal confirms the reviewed receipt and scope without activation',await page.evaluate(revision=>{
     const review=fixture.calls.find(call=>call.command==='model_removal_plan');
     const removal=fixture.calls.find(call=>call.command==='uninstall_model');
     return review&&removal?.args.id===review.args.id&&removal.args.scope==='verified-native-scope'&&(fixture.receiptRevision||0)===revision;
   },receiptRevision));
   await page.close();
 }}finally{fs.writeFileSync(path.join(output,'model-modes.json'),JSON.stringify({checks,errors,inferenceVerified:false,weightsDownloaded:false},null,2)+'\n');await browser.close();server.close();}
 console.log(JSON.stringify({checks,errors,output},null,2));if(errors.length)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});`;
const moduleObject=new Module(__filename,module);moduleObject.filename=__filename;moduleObject.paths=module.paths;moduleObject._compile(code,__filename);
