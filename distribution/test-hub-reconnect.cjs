const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function setup(){
  let probes=0,fail=false;const requests=[];
  const source=fs.readFileSync(path.join(__dirname,'hub-ui.js'),'utf8').split('async function $hubRefresh')[0];
  const context={window:{__TAURI_INTERNALS__:{invoke:async()=>({hubBridge:{available:true,url:'http://127.0.0.1:'+ (++probes===1?4100:4200),secret:'private'}})}},
    fetch:async(url,options)=>{requests.push({url,method:options.method});if(fail){fail=false;throw new TypeError('Failed to fetch');}return new Response('{}');},
    AbortController,setTimeout,clearTimeout,Response,Error,Date,JSON};
  vm.createContext(context);vm.runInContext(source+';this.request=$hubRequest;',context);
  return {context,requests,breakConnection(){fail=true;},get probes(){return probes;}};
}
test('a restarted managed helper is rediscovered after a failed read',async()=>{
  const h=setup();await h.context.request('/status');h.breakConnection();await h.context.request('/status');
  assert.equal(h.probes,2);assert.match(h.requests.at(-1).url,/:4200/);
});
test('an ambiguous failed write is not replayed; the next request uses the new helper',async()=>{
  const h=setup();await h.context.request('/status');h.breakConnection();
  await assert.rejects(h.context.request('/images/generate',{request:{prompt:'private'}}));
  assert.equal(h.requests.filter(r=>r.url.endsWith('/images/generate')).length,1);
  await h.context.request('/status');assert.equal(h.probes,2);
});
