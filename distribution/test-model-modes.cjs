const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../src-tauri/resources/runtime-setup/hf-extension/native-model-bindings.json'),'utf8'));
async function fixture(initial=new Map(),active=[],source=catalog,metadata=source){
  const calls=[],requests=[],saved=initial,context=vm.createContext({console,Map,Set,localStorage:{getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)}});
  const React={createElement:(type,props,...children)=>({type,props,children}),useState:value=>[typeof value==='function'?value():value,()=>{}],useEffect:()=>{}};
  const service={invoke:async(command,args)=>{calls.push({command,args});return {activeConversationIds:active,runtime:{status:'stopped'}};},
    request:async(route,body)=>{requests.push({route,body});return route==='/runtime/modes'?metadata:{profileId:source.models.find(m=>m.id===body.id).modeProfiles[body.operatingMode]};},changed:async()=>{},catalog:()=>[]};
  context.React=React;context.catalog=structuredClone(source);context.service=service;
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/model-modes.js'),'utf8').replace('export function ','function ')+';globalThis.modeApi=installModelModes(React,catalog,service);',context);
  await context.modeApi.ready;return {api:context.modeApi,saved,calls,requests};
}

test('first paint includes conversational VL and OCR categories while preserving unavailable runtime status',async()=>{
  for(const category of ['omni','ocr','computer-use']){
    const model={id:'specialist-'+category,category,inputModalities:['text','image'],outputModalities:['text'],backend:'external',runtimeReady:false,contextTokens:0};
    const f=await fixture(new Map(),[],{models:[model]},{models:[]});
    assert.equal(f.api.policy(model).supportsTextGeneration,true,category);
    assert.equal(f.api.context(model),null,'An unknown checkpoint window is not presented as a supported 32K allocation');
    const control=f.api.Control({model});
    assert.equal(control.type,'section',category+' retains its separate mode control');
    assert.equal(f.api.augment({models:[model]}).models[0].runtimeReady,false);
  }
});

test('catalog descriptors retain reviewed caps before the runtime metadata response',async()=>{
  const model={id:'capped',category:'text',contextTokens:262144,supportsTextGeneration:true,
    operatingModes:[{id:'native',contextTokens:32768,supported:false},{id:'echo',contextTokens:8192,supported:false}]};
  const f=await fixture(new Map(),[],{models:[model]},{models:[]});
  assert.equal(f.api.context(model,'native'),32768);
  assert.equal(f.api.context(model,'echo'),8192);
  assert.equal(f.api.policy({...model,operatingModes:undefined}).operatingModes[0].supported,false);
});

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

test('removal reads the selected profile and confirms the reviewed ID without activating either mode',async()=>{
  const f=await fixture(),model=catalog.models.find(m=>m.modeProfiles?.native&&m.modeProfiles?.echo);
  const row=f.api.augment({models:[model]}).models[0];
  f.saved.set('opencore.model-mode.'+row.modeFamilyKey,'native');
  const review={id:row.id};
  const confirmation={id:model.modeProfiles.echo,scope:'profile',confirmationToken:'reviewed-files'};
  const confirmed=structuredClone(confirmation);
  await f.api.before('model_removal_plan',review);
  await f.api.before('uninstall_model',confirmation);
  assert.equal(review.id,row.id);
  assert.deepEqual(confirmation,confirmed);
  assert.deepEqual(f.requests.map(call=>call.route),['/runtime/modes']);
  assert.deepEqual(f.calls,[]);
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
