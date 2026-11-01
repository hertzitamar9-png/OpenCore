const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('imported text profiles carry ECHO mode through refresh and removal',()=>{
  const context=vm.createContext({document:{createElement:()=>({}),head:{appendChild(){}}}});
  vm.runInContext(fs.readFileSync(require.resolve('./hub-ui.js'),'utf8'),context);
  vm.runInContext('$hubPublish({catalog:[{profileId:"api:text",category:"text",memoryMode:"echo"},{profileId:"api:image",category:"image"}]})',context);
  assert.equal(vm.runInContext('globalThis.__opencoreHubEchoProfiles?.has("api:text")',context),true);
  assert.equal(vm.runInContext('globalThis.__opencoreHubEchoProfiles.has("api:image")',context),false);
  vm.runInContext('$hubPublish({catalog:[]})',context);
  assert.equal(vm.runInContext('globalThis.__opencoreHubEchoProfiles.has("api:text")',context),false);
});

test('text-capable specialist imports retain their category and get their actual ECHO profile',()=>{
  for(const libraryCategory of ['omni','ocr','computer-use']){
    const context=vm.createContext({document:{createElement:()=>({}),head:{appendChild(){}}},libraryCategory});
    vm.runInContext(fs.readFileSync(require.resolve('./hub-ui.js'),'utf8'),context);
    vm.runInContext(`globalThis.sample={id:'hf-one',profileId:'api:local:aa',repository:'fixture/model',category:'text',libraryCategory,memoryMode:'echo',runtime:{adapter:'gguf'},runtimeReady:true,status:'installed',files:[]};
      $hubPublish({catalog:[sample]});globalThis.model=$hubModel(sample);`,context);
    assert.equal(vm.runInContext('__opencoreHubEchoProfiles.has("api:local:aa")',context),true,libraryCategory);
    assert.equal(context.model.memoryMode,'echo');
    assert.equal(context.model.category,libraryCategory);
    assert.equal(context.model.selectable,true);
    assert.equal(context.model.hubEntry.profileId,'api:local:aa');
  }
});

test('unregistered or non-text imports do not offer a broken chat profile',()=>{
  const context=vm.createContext({document:{createElement:()=>({}),head:{appendChild(){}}}});
  vm.runInContext(fs.readFileSync(require.resolve('./hub-ui.js'),'utf8'),context);
  vm.runInContext(`globalThis.text=$hubModel({id:'hf-pending',repository:'fixture/text',category:'text',runtime:{adapter:'gguf'},runtimeReady:true,status:'installed',files:[]});
    globalThis.image=$hubModel({id:'hf-image',profileId:'api:image:aa',repository:'fixture/image',category:'image',runtime:{adapter:'diffusers'},runtimeReady:true,status:'installed',files:[]});
    $hubPublish({catalog:[image.hubEntry]});`,context);
  assert.equal(context.text.selectable,false);
  assert.equal(context.image.selectable,false);
  assert.equal(vm.runInContext('__opencoreHubEchoProfiles.has("api:image:aa")',context),false);
});

test('a verified smaller runtime allocation takes precedence over catalog estimates',()=>{
  const context=vm.createContext({document:{createElement:()=>({}),head:{appendChild(){}}}});
  vm.runInContext(fs.readFileSync(require.resolve('./hub-ui.js'),'utf8'),context);
  vm.runInContext(`globalThis.entry={id:'hf-one',profileId:'api:local:aa',repository:'fixture/model',category:'text',runtime:{adapter:'gguf'},runtimeReady:true,status:'installed',files:[],contextTokens:32768,echoWorkingWindowTokens:32768,runtimePreparation:{contextTokens:8192}};
    globalThis.model=$hubModel(entry);`,context);
  assert.equal(context.model.contextTokens,8192);
  assert.equal(vm.runInContext('$hubContext(entry)',context),8192);
});

test('reconnecting a ready multimodal text profile refreshes only its own local provider',async()=>{
  const context=vm.createContext({document:{createElement:()=>({}),head:{appendChild(){}}},setTimeout,clearTimeout});
  vm.runInContext(fs.readFileSync(require.resolve('./hub-ui.js'),'utf8'),context);
  await vm.runInContext(`$hubDescriptor={url:'http://127.0.0.1:1234'};
    $hubRequest=async()=>({catalog:[{id:'text',category:'text',libraryCategory:'omni',runtimeReady:true,providerId:'local'},
      {id:'image',category:'image',libraryCategory:'image',runtimeReady:true,providerId:'image'}]});
    globalThis.registered=[];$hubRegister=async entry=>registered.push(entry.id);
    $hubRefresh(true)`,context);
  await new Promise(resolve=>setTimeout(resolve,20));
  assert.deepEqual(Array.from(context.registered),['text']);
});
