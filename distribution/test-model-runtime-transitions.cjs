const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function fixture(){
  const context=vm.createContext({document:{createElement:()=>({}),head:{appendChild(){}}},window:{dispatchEvent(){}},
    CustomEvent:class{constructor(type,options){this.type=type;Object.assign(this,options);}},Event:class{},AbortController,Response,setTimeout,clearTimeout});
  vm.runInContext(fs.readFileSync(require.resolve('./hub-ui.js'),'utf8'),context);
  vm.runInContext(`globalThis.realHubRequest=$hubRequest;$hubDescriptor={url:'http://127.0.0.1:1234',secret:'fixture'};$hubState={...$hubState,loaded:true,catalog:[{id:'dory',profileId:'api:dory'}]};
    globalThis.__opencoreHubSelectedProfile='lfm';globalThis.calls=[];
    $hubRequest=async(path,body)=>{calls.push(path);if(path==='/stop')throw Error('Stop failed');return {}};`,context);
  return context;
}
test('a failed profile selection keeps the previous acknowledged model',async()=>{
  const context=fixture();
  await assert.rejects(vm.runInContext(`$hubInvoke('select_profile',{profile:'api:dory'},async()=>{throw Error('Selection failed')})`,context),/Selection failed/);
  assert.equal(context.__opencoreHubSelectedProfile,'lfm');
});
test('managed stop errors cannot be swallowed before reporting that the runtime stopped',async()=>{
  const context=fixture();
  await assert.rejects(vm.runInContext(`$hubInvoke('stop_runtime',{},async()=>{calls.push('native-stop');return {stopped:true}})`,context),/Stop failed/);
  assert.deepEqual(Array.from(context.calls),['/stop']);
});
test('runtime stop uses the managed servers POST route before stopping the native runtime',async()=>{
  const context=fixture();const wire=[];
  context.fetch=async(url,options)=>{wire.push([new URL(url).pathname,options.method]);return new Response(
    options.method==='POST'?'{"stopped":true}':'{"error":"Unknown bridge endpoint"}',{status:options.method==='POST'?200:404});};
  await vm.runInContext(`$hubRequest=realHubRequest;$hubInvoke('stop_runtime',{},async()=>{calls.push('native-stop');return {stopped:true}})`,context);
  assert.deepEqual(wire,[['/api/stop','POST']]);
  assert.deepEqual(Array.from(context.calls),['native-stop']);
});

test('the shipped composer picker stops the loaded worker and starts the newly selected model',async()=>{
  const acorn=require('acorn');
  let code=fs.readFileSync(require.resolve('./recovered-dist/assets/App-kNA9sRUP.js'),'utf8');
  const helpers={editFunction(text,name,edit){const f=acorn.parse(text,{ecmaVersion:'latest',sourceType:'module'}).body.find(n=>n.type==='FunctionDeclaration'&&n.id.name===name);return text.slice(0,f.start)+edit(text.slice(f.start,f.end))+text.slice(f.end);},
    replaceOnce(text,from,to){assert.equal(text.split(from).length,2);return text.replace(from,to);}};
  if(fs.existsSync(__dirname+'/model-selection-patches.cjs'))code=require('./model-selection-patches.cjs')(code,helpers);
  const root=acorn.parse(code,{ecmaVersion:'latest',sourceType:'module'}).body.find(n=>n.type==='FunctionDeclaration'&&n.id.name==='w8');
  let picker,transition;
  function walk(node){if(!node||typeof node!=='object')return;
    if(node.type==='ObjectExpression'&&node.properties.some(p=>p.key?.name==='liveTokenSpeed'))picker=node.properties.find(p=>p.key?.name==='onSelectProfile').value;
    for(const value of Object.values(node))if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')walk(value);}
  walk(root);
  for(const declaration of root.body.body)if(declaration.type==='VariableDeclaration')for(const d of declaration.declarations)if(d.id?.name==='Ut')transition=d.init;
  assert.ok(picker&&transition);
  const calls=[],bindings={l:{runtime:{status:'running',profile:'lfm'}},v:'lfm',Pe:{current:false},Ie:undefined,D:false,
    Ne(){},$(){},Ks:async()=>calls.push('stop'),tb:async id=>calls.push('select:'+id),ac:async id=>calls.push('start:'+id),
    at:id=>calls.push('selected:'+id),yt:async()=>{}};
  bindings.Ut=vm.runInNewContext('('+code.slice(transition.start,transition.end)+')',bindings);
  const select=vm.runInNewContext('('+code.slice(picker.start,picker.end)+')',bindings);
  select('api:dory');await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls,['stop','select:api:dory','selected:api:dory','start:api:dory']);
});
