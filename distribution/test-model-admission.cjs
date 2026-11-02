const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const read=name=>JSON.parse(fs.readFileSync(path.join(__dirname,name),'utf8'));
const nativeCatalog=read('hf-research/expanded-catalog.json');
const artifacts=new Map(nativeCatalog.artifacts.map(item=>[item.id,item]));
const orcaEntries=read('../src-tauri/resources/runtime-setup/hf-extension/publisher-models.json').entries
  .filter(entry=>entry.repository==='orcarouter/OrcaCyber-Nano-1.0');
const nativeModels=nativeCatalog.models.filter(model=>!model.modeAliasOf&&(model.id==='orcacyber-nano-1'||model.variantOf==='orcacyber-nano-1'))
  .map(model=>({...model,installed:false,totalBytes:model.artifacts.reduce((n,id)=>n+artifacts.get(id).bytes,0)}));

function fixture(models=nativeModels,entries=orcaEntries){
  const context=vm.createContext({URL,document:{createElement:()=>({}),head:{appendChild(){}}},
    window:{dispatchEvent(){}},CustomEvent:class{constructor(type,options){this.type=type;Object.assign(this,options);}},models,entries,
    requests:[],nativeCalls:[]});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'hub-ui.js'),'utf8'),context);
  vm.runInContext(`$hubState={...$hubState,catalog:entries,loaded:true,available:true};
    $hubNativeLibrary={models};
    $hubRequest=async(path,body)=>{requests.push({path,body});return {jobId:'managed-job',id:body?.id,status:'running'};};
    $hubRefresh=async()=>$hubState;
    window.__TAURI_INTERNALS__={invoke:async(command,args)=>{nativeCalls.push({command,args});return {models};}};`,context);
  return context;
}

test('Orca offers its two real publisher packages in one family before any installation',()=>{
  const context=fixture();
  const models=vm.runInContext('$hubAugment({models}).models',context);
  assert.equal(models.length,2);
  assert.deepEqual(Array.from(models,model=>model.id).sort(),orcaEntries.map(entry=>entry.id).sort());
  assert.equal(new Set(models.map(model=>model.variantOf||model.id)).size,1);
  assert.ok(models.every(model=>model.hubModel&&!model.hubEntry.nativeProfile));
  assert.deepEqual(Array.from(models,model=>model.precision).sort(),['Mix 13G','Mix 15.7G']);
  assert.ok(orcaEntries.every(entry=>entry.gated&&!entry.runtime.inferenceVerified));
  assert.ok(orcaEntries.every(entry=>entry.files.filter(pin=>pin.role==='model').length===1));
  assert.ok(orcaEntries.every(entry=>entry.files.some(pin=>pin.role==='companion'&&pin.filename.startsWith('mmproj-'))));
});

test('Orca pins still match every currently published main file and projector without using its DFlash draft',()=>{
  const current=read('hf-research/catalog-observations/orcarouter--OrcaCyber-Nano-1.0.api.json');
  const published=new Map(current.siblings.map(item=>[item.rfilename,item]));
  assert.equal(current.gated,'auto');
  assert.equal(current.sha,'97140f3ff7e2034feaef16e04f417f9a69619a7f');
  for(const entry of orcaEntries)for(const pin of entry.files){
    assert.equal(pin.sha256,published.get(pin.filename).lfs.sha256);
    assert.equal(pin.bytes,published.get(pin.filename).size);
    assert.doesNotMatch(pin.filename,/DFlash/i);
  }
});

test('a legacy native Install resolves its exact selected Mix package through the managed installer',async()=>{
  for(const native of nativeModels){
    const context=fixture();context.selected=native.id;
    await vm.runInContext(`$hubInvoke('runtime_setup_start',{targetId:selected,options:{installWeights:true}},async()=>{nativeCalls.push('broken-setup');})`,context);
    assert.deepEqual(Array.from(context.requests,request=>[request.path,request.body.id]),
      [['/install',orcaEntries.find(entry=>entry.quantization===native.precision).id]]);
    assert.equal(context.nativeCalls.length,0,'Installing a managed alias must not enter native require_installed');
  }
});

test('native download aliases also resolve through the selected managed package',async()=>{
  const context=fixture();
  await vm.runInContext(`$hubInvoke('install_model',{id:'orcacyber-nano-1-mix-15-7g'},async()=>{nativeCalls.push('broken-install');})`,context);
  assert.deepEqual(Array.from(context.requests,request=>[request.path,request.body.id]),[['/install',orcaEntries.find(entry=>entry.quantization==='Mix 15.7G').id]]);
  assert.equal(context.nativeCalls.length,0);
});

test('a restored Models cache resolves its native quantization with one fresh read before installing',async()=>{
  const context=fixture();
  await vm.runInContext(`$hubNativeLibrary=null;$hubInvoke('install_model',{id:'orcacyber-nano-1-mix-15-7g'},async()=>{nativeCalls.push('broken-install');})`,context);
  assert.deepEqual(Array.from(context.nativeCalls,call=>call.command),['list_model_library']);
  assert.deepEqual(Array.from(context.requests,request=>[request.path,request.body.id]),[['/install',orcaEntries.find(entry=>entry.quantization==='Mix 15.7G').id]]);
});

test('an installed native profile remains on the same family card and keeps its existing runtime',async()=>{
  const installed=nativeModels.map((model,index)=>({...model,installed:index===0}));
  const context=fixture(installed);
  const models=vm.runInContext('$hubAugment({models}).models',context);
  assert.equal(models.length,2);
  assert.ok(models.some(model=>model.id===installed[0].id&&model.installed&&model.hubEntry.nativeProfile));
  assert.ok(models.some(model=>model.id===orcaEntries.find(entry=>entry.quantization===installed[1].precision).id&&!model.hubEntry.nativeProfile));
  assert.equal(new Set(models.map(model=>model.variantOf||model.id)).size,1);
  context.selected=installed[0].id;
  await vm.runInContext(`$hubInvoke('runtime_setup_start',{targetId:selected,options:{installWeights:false}},async()=>{nativeCalls.push('installed-runtime');})`,context);
  assert.equal(context.requests.length,0,'Preparing an existing native runtime must not redownload its weights');
  assert.deepEqual(Array.from(context.nativeCalls),['installed-runtime']);
});

test('an ambiguous alias never picks a different precision or silently falls through to native setup',async()=>{
  const conflicting=orcaEntries.map(entry=>({...entry,quantization:'Mix 13G'}));
  const context=fixture(nativeModels,conflicting);
  await assert.rejects(vm.runInContext(`$hubInvoke('install_model',{id:'orcacyber-nano-1'},async()=>{nativeCalls.push('native-install');})`,context),/quantization|package|precision/i);
  assert.equal(context.requests.length,0);
  assert.equal(context.nativeCalls.length,0);
});

test('a native-only quantization without an equivalent publisher package keeps its existing install route',async()=>{
  const other={...nativeModels[0],id:'orca-native-only-fixture',variantOf:nativeModels[0].id,precision:'Q8_0'};
  const context=fixture([...nativeModels,other]);
  const models=vm.runInContext('$hubAugment({models}).models',context);
  assert.ok(models.some(model=>model.id===other.id&&model.hubEntry.nativeProfile));
  context.selected=other.id;
  await vm.runInContext(`$hubInvoke('install_model',{id:selected},async()=>{nativeCalls.push('original-native-install');})`,context);
  assert.equal(context.requests.length,0);
  assert.deepEqual(Array.from(context.nativeCalls),['original-native-install']);
});

test('native and ECHO operating profiles remain options on one card with one uninstalled publisher package per quant',async()=>{
  const nativeMode={...nativeModels[0],id:'orca-native-mode-fixture',variantOf:nativeModels[0].id,memoryMode:'native',label:'OrcaCyber Nano 1.0'};
  const context=fixture([...nativeModels,nativeMode]);
  const available=vm.runInContext('$hubAugment({models}).models',context);
  assert.equal(available.length,2,'An uninstalled mode must not create another checkpoint or quantization');
  assert.equal(new Set(available.map(model=>model.variantOf||model.id)).size,1);
  context.selected=nativeMode.id;
  await vm.runInContext(`$hubInvoke('install_model',{id:selected},async()=>{nativeCalls.push('broken-mode-install');})`,context);
  assert.equal(context.requests[0].body.id,orcaEntries.find(entry=>entry.quantization===nativeMode.precision).id);
  assert.equal(context.nativeCalls.length,0);
  const installed=fixture([...nativeModels.map((model,index)=>({...model,installed:index===0})),{...nativeMode,installed:true}]);
  const choices=vm.runInContext('$hubAugment({models}).models',installed);
  assert.equal(new Set(choices.map(model=>model.variantOf||model.id)).size,1);
  assert.deepEqual(Array.from(choices.filter(model=>model.installed),model=>model.memoryMode).sort(),['echo','native']);
});

test('an explicit runtime-only request prepares the managed package without starting another weight download',async()=>{
  const context=fixture();
  await vm.runInContext(`$hubInvoke('runtime_setup_start',{targetId:'orcacyber-nano-1',options:{installWeights:false}},async()=>{nativeCalls.push('broken-setup');})`,context);
  assert.deepEqual(Array.from(context.requests,request=>[request.path,request.body.id]),[['/prepare',orcaEntries.find(entry=>entry.quantization==='Mix 13G').id]]);
  assert.equal(context.nativeCalls.length,0);
});
