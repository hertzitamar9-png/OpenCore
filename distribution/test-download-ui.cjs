const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),acorn=require('acorn');
const source=fs.readFileSync(require('node:path').join(__dirname,'hub-ui.js'),'utf8');
const node=acorn.parse(source,{ecmaVersion:'latest',sourceType:'script'}).body.find(value=>value.type==='FunctionDeclaration'&&value.id.name==='$hubInvoke');
function fixture({phone=false,hub=null,clock=Date}={}){
  const calls=[];let progress={modelId:'native-one',phase:'downloading'};
  const context={globalThis:{__OPENCORE_PHONE__:phone},window:{__TAURI_INTERNALS__:{invoke:async(command,args)=>{
    calls.push({command,args});if(command==='model_install_progress')return progress;return null;
  }}},$hubState:{progress:hub,catalog:[],loaded:true},$hubRefresh:async()=>{},$hubRequest:async(route,args)=>{calls.push({route,args});return{}},setTimeout,Date:clock};
  const invoke=vm.runInNewContext(source.slice(node.start,node.end)+';$hubInvoke',context);
  return{invoke,calls,setProgress:value=>{progress=value}};
}
test('Phone cancel delegates exactly to its native operation without calling PC cancellation or cleanup',async()=>{
  const f=fixture({phone:true});let forwarded=0;
  await f.invoke('cancel_model_installation',{modelId:'phone-one',operationId:'owned'},async()=>{forwarded++});
  assert.equal(forwarded,1);assert.deepEqual(f.calls,[]);
});
test('managed Hub cancel targets the current exact job and stays separate from pause',async()=>{
  const f=fixture({hub:{id:'hf-one',jobId:'owned-job',status:'running'}});
  await f.invoke('cancel_model_installation',{modelId:'hf-one',operationId:'owned-job'},()=>assert.fail('Must not invoke the unknown native command'));
  assert.equal(f.calls[0].route,'/cancel');assert.equal(f.calls[0].args.jobId,'owned-job');assert.equal(f.calls.length,1);
  await assert.rejects(f.invoke('cancel_model_installation',{modelId:'hf-one',operationId:'foreign-job'},async()=>{}),/changed/);
  assert.equal(f.calls.length,1);
});
test('native cancel waits for its own worker terminal state before requesting manifest-bound cleanup',async()=>{
  const f=fixture();const cancelling=f.invoke('cancel_model_installation',{modelId:'native-one'},async()=>assert.fail('Unknown native command'));
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(f.calls.some(value=>value.route),false);
  assert.ok(f.calls.some(value=>value.command==='cancel_model_install'));
  f.setProgress({modelId:'native-one',phase:'failed',error:'Installation cancelled; partial retained'});
  await cancelling;
  assert.equal(f.calls.at(-1).route,'/native/cancel');assert.equal(f.calls.at(-1).args.id,'native-one');
});
test('changed ownership or an unresponsive native worker cannot trigger partial cleanup',async()=>{
  const changed=fixture();changed.setProgress({modelId:'someone-else',phase:'downloading'});
  await assert.rejects(changed.invoke('cancel_model_installation',{modelId:'native-one'},async()=>{}),/changed/);
  assert.equal(changed.calls.some(value=>value.command==='cancel_model_install'||value.route),false);
  let clock=0;const stalled=fixture({clock:{now:()=>clock+=40000}});
  await assert.rejects(stalled.invoke('cancel_model_installation',{modelId:'native-one'},async()=>{}),/still stopping/);
  assert.equal(stalled.calls.some(value=>value.route),false);
});
