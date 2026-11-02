/* Render the shipped catalog and exercise its install route without downloading weights. */
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=original.slice(0,original.indexOf('(async()=>{await new Promise'));
const data=String.raw`const nativeCatalog=JSON.parse(fs.readFileSync(path.join(__dirname,'hf-research/expanded-catalog.json'),'utf8'));
const nativeArtifacts=new Map(nativeCatalog.artifacts.map(item=>[item.id,item]));
const admissionModels=nativeCatalog.models.filter(model=>model.id==='jev-computer-use'||model.id==='orcacyber-nano-1'||model.variantOf==='orcacyber-nano-1').map(model=>({...model,installed:false,totalBytes:model.artifacts.reduce((n,id)=>n+nativeArtifacts.get(id).bytes,0),downloadBytes:model.artifacts.reduce((n,id)=>n+nativeArtifacts.get(id).bytes,0)}));
const admissionEntries=JSON.parse(fs.readFileSync(path.join(__dirname,'../src-tauri/resources/runtime-setup/hf-extension/publisher-models.json'),'utf8')).entries.filter(entry=>entry.repository==='orcarouter/OrcaCyber-Nano-1.0');
const admissionScript='fixture.nativeModels='+JSON.stringify(admissionModels).replaceAll('<','\\u003c')+';fixture.catalog='+JSON.stringify(admissionEntries).replaceAll('<','\\u003c')+';';`;
code=code.replace('const html=',data+'\nconst html=').replace('${fixture}</script>','${fixture}${admissionScript}</script>');
code+=String.raw`
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
  const page=await browser.newPage({viewport:{width:1450,height:1050}}),checks=[],errors=[];
  const output=process.env.OPENCORE_CATALOG_QA_DIR||path.join(require('node:os').tmpdir(),'opencore-catalog-admission-20261101');fs.mkdirSync(output,{recursive:true});
  page.on('pageerror',error=>errors.push(error.message));
  const check=(name,passed)=>{checks.push({name,passed:!!passed});if(!passed)throw Error(name);};
  try{
    await page.goto('http://127.0.0.1:'+server.address().port);
    const search=page.getByRole('textbox',{name:'Search models',exact:true});await search.fill('OrcaCyber');
    const card=page.getByRole('article',{name:'OrcaCyber Nano 1.0',exact:true});await card.waitFor();
    check('Orca renders one family card',await card.count()===1);
    const picker=card.getByRole('combobox',{name:'Quantization for orcarouter/OrcaCyber-Nano-1.0',exact:true});
    check('Orca offers exactly its two genuine mixed-precision packages',await picker.locator('option').count()===2);
    check('Orca retains the publisher access requirement',await card.innerText().then(text=>text.includes('Publisher-gated')&&text.includes('authorized token')));
    const selected=admissionEntries.find(entry=>entry.quantization==='Mix 15.7G');await picker.selectOption(selected.id);
    check('Mix 15.7G selects its managed pinned package',await picker.inputValue()===selected.id);
    await card.screenshot({path:path.join(output,'orcacyber-publisher-packages.png')});
    await card.getByRole('button',{name:'Install',exact:true}).click();
    await card.getByRole('button',{name:'Installing…',exact:true}).waitFor();
    check('Install sends the selected package to the managed download path',await page.evaluate(id=>fixture.calls.some(call=>call.route==='/install'&&call.body.id===id),selected.id));
    check('Orca never enters the native installer that rejected missing weights',await page.evaluate(()=>!fixture.calls.some(call=>call.command==='runtime_setup_start'||call.command==='install_model')));
    await page.locator('.model-install-progress').getByRole('button',{name:'Pause installation',exact:true}).click();
    await card.getByRole('button',{name:'Install',exact:true}).waitFor();
    await search.fill('JEV');
    const reference=page.getByRole('article').filter({has:page.getByRole('heading',{name:'JEV Computer Use',exact:true})});await reference.waitFor();
    check('JEV exact repository is visible once',await reference.count()===1);
    check('JEV explains its missing published weights and quants',await reference.innerText().then(text=>text.includes('No published weights')&&text.includes('No weights or quantizations')));
    check('JEV does not claim a controller capability without published weights',!(await reference.innerText()).includes('Controller of other models'));
    check('JEV cannot advertise an installable or executable package',await reference.getByRole('button',{name:'Install',exact:true}).count()===0&&!(await reference.innerText()).includes('Ready to use'));
    await reference.screenshot({path:path.join(output,'jev-source-only.png')});
    check('Rendered model controls have no JavaScript errors',errors.length===0);
  }finally{
    fs.writeFileSync(path.join(output,'catalog-admission.json'),JSON.stringify({checks,errors,inferenceVerified:false,weightsDownloaded:false},null,2)+'\n');
    await browser.close();server.close();
  }
  console.log(JSON.stringify({checks,errors,output,inferenceVerified:false,weightsDownloaded:false},null,2));
  if(checks.some(check=>!check.passed)||errors.length)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});`;
const moduleObject=new Module(__filename,module);moduleObject.filename=__filename;moduleObject.paths=module.paths;moduleObject._compile(code,__filename);
