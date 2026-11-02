const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../src-tauri/resources/runtime-setup/hf-extension/native-model-bindings.json'),'utf8'));
async function fixture(initial=new Map(),active=[]){
  const calls=[],requests=[],saved=initial,context=vm.createContext({console,Map,Set,localStorage:{getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)}});
  const React={createElement:()=>{},useState:()=>{},useEffect:()=>{}};
  const service={invoke:async(command,args)=>{calls.push({command,args});return {activeConversationIds:active,runtime:{status:'stopped'}};},
    request:async(route,body)=>{requests.push({route,body});return route==='/runtime/modes'?catalog:{profileId:catalog.models.find(m=>m.id===body.id).modeProfiles[body.operatingMode]};},changed:async()=>{},catalog:()=>[]};
  context.React=React;context.catalog=structuredClone(catalog);context.service=service;
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/model-modes.js'),'utf8').replace('export function ','function ')+';globalThis.modeApi=installModelModes(React,catalog,service);',context);
  await context.modeApi.ready;return {api:context.modeApi,saved,calls,requests};
}

test('all native text quant profiles collapse to one row with identical file identities and quant choices in both modes',async()=>{
  const f=await fixture(),models=catalog.models.map(m=>({...m,installed:!m.modeAliasOf,selectable:true,runtimeReady:true}));
  const echo=f.api.augment({models}).models;
  for(const model of echo)assert.equal(model.memoryMode,'echo');
  for(const model of echo)f.saved.set('opencore.model-mode.'+model.modeFamilyKey,'native');
  const native=f.api.augment({models}).models;
  const signature=m=>[m.modeFamilyKey,m.precision,...m.artifacts].join('|');
  assert.deepEqual(Array.from(native,signature).sort(),Array.from(echo,signature).sort());
  assert.equal(new Set(native.map(signature)).size,native.length,'There is only one row per physical quant');
  assert.ok(native.length<models.length,'Execution profiles do not become duplicate card options');
  for(const model of native){
    if(!model.modeProfiles.native)continue;
    assert.equal(model.operatingProfileId,model.modeProfiles.native);
    assert.equal(model.memoryMode,'native');
    assert.ok(model.contextTokens<=1048576);
  }
});

test('using Native activates its frozen execution profile without installing any checkpoint',async()=>{
  const f=await fixture(),model=catalog.models.find(m=>m.modeProfiles?.native&&m.modeProfiles?.echo);
  const result=await f.api.activate(model.id,'native');
  assert.equal(result,model.modeProfiles.native);
  assert.deepEqual(f.requests.map(call=>call.route),['/runtime/modes','/runtime/native-profile']);
  assert.equal(f.requests[1].body.operatingMode,'native');
  assert.equal(f.api.profileInfo(result).mode,'native');
});

test('active generations block switching profiles before any receipt changes',async()=>{
  const f=await fixture(new Map(),['live-chat']),model=catalog.models.find(m=>m.modeProfiles);
  await assert.rejects(f.api.activate(model.id,'native'),/current model request/);
  assert.equal(f.requests.length,1);
});

test('uninstall resolves the selected mode while explicit execution profile IDs remain authoritative',async()=>{
  const f=await fixture(),model=catalog.models.find(m=>m.modeProfiles?.native&&m.modeProfiles?.echo);
  const row=f.api.augment({models:[model]}).models[0];
  f.saved.set('opencore.model-mode.'+row.modeFamilyKey,'native');
  const args={id:row.id};await f.api.before('model_removal_plan',args);
  assert.equal(args.id,model.modeProfiles.native);
  assert.equal(await f.api.activate(model.modeProfiles.echo),model.modeProfiles.echo);
});

test('a persisted family mode is restored and keeps a short published context truthful',async()=>{
  const f=await fixture(),model=catalog.models.find(m=>m.modeProfiles&&m.operatingModes.find(x=>x.id==='native')?.contextTokens<1048576);
  const row=f.api.augment({models:[{...model,installed:true}]}).models[0];
  f.saved.set('opencore.model-mode.'+row.modeFamilyKey,'native');
  const restarted=await fixture(f.saved);
  const next=restarted.api.augment({models:[{...model,installed:true}]}).models[0];
  assert.equal(next.memoryMode,'native');
  assert.equal(next.contextTokens,model.operatingModes.find(x=>x.id==='native').contextTokens);
  assert.ok(next.contextTokens<1048576);
});
