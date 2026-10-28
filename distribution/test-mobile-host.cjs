const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function host(){
  const sent=[],calls=[],callbacks=new Map();let next=0;
  const native={transformCallback(fn){const id=++next;callbacks.set(id,fn);return id;},async invoke(command,args){calls.push({command,args});
    if(command==='runtime_setup_probe')return{hubBridge:{available:true,url:'http://127.0.0.1:4444',secret:'PC_ONLY_SECRET'}};
    if(command==='plugin:event|listen')return 71;
    return command==='get_snapshot'?{conversations:[{id:'same-pc-chat'}]}:null;
  }};
  const context=vm.createContext({window:{__TAURI_INTERNALS__:native,__TAURI_EVENT_PLUGIN_INTERNALS__:{unregisterListener(){}},addEventListener(){}},
    globalThis:{__OPENCORE_MOBILE__:true},setTimeout(){},TextDecoder,Response,ReadableStream,
    $hubRequest:async(route,body)=>{sent.push({route,body});return{};},
    $hubConnect:async()=>({url:'http://127.0.0.1:4444',secret:'PC_ONLY_SECRET'}),
    fetch:async()=>new Response('data: one\n\ndata: two\n\n',{headers:{'Content-Type':'text/event-stream'}})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'mobile-host.js'),'utf8')+'\nthis.runTask=$mobileHostTask;',context);
  return{sent,calls,callbacks,run:context.runTask};
}
test('the phone can read PC state without receiving the local helper secret',async()=>{
  const h=host();await h.run({device:'phone',session:'session',id:'one',kind:'invoke',command:'runtime_setup_probe',args:{}});
  const result=h.sent.at(-1).body;assert.equal(result.value.hubBridge.url,'http://127.0.0.1:1');
  assert.equal(JSON.stringify(result).includes('PC_ONLY_SECRET'),false);
  await h.run({device:'phone',session:'session',id:'two',kind:'invoke',command:'get_snapshot',args:{}});
  assert.equal(h.sent.at(-1).body.value.conversations[0].id,'same-pc-chat');
});
test('event callbacks are registered on the PC and delivered to the originating phone session',async()=>{
  const h=host();await h.run({device:'phone',session:'session',id:'listen',kind:'invoke',command:'plugin:event|listen',args:{event:'opencore-generation',handler:8,target:{kind:'Any'}}});
  const phoneListenerId=h.sent.at(-1).body.value;
  assert.equal(phoneListenerId,8);
  h.callbacks.get(1)({event:'opencore-generation',payload:{text:'one'}});
  await new Promise(resolve=>setImmediate(resolve));
  const event=h.sent.find(item=>item.route.endsWith('/event')).body;
  assert.equal(event.device,'phone');assert.equal(event.session,'session');assert.equal(event.handler,8);assert.equal(event.payload.payload.text,'one');
  await h.run({device:'phone',session:'session',id:'stop',kind:'invoke',command:'plugin:event|unlisten',args:{event:'opencore-generation',eventId:phoneListenerId}});
  assert.equal(h.calls.at(-1).command,'plugin:event|unlisten');
  assert.equal(h.calls.at(-1).args.eventId,71);
});
test('local service streaming is forwarded in order from the PC',async()=>{
  const h=host();await h.run({device:'phone',session:'session',id:'stream',kind:'hub',method:'POST',path:'/api/private/send',body:'{}'});
  const phases=h.sent.filter(item=>item.route.endsWith('/result')).map(item=>item.body.phase);
  assert.deepEqual(phases,['start','chunk','end']);
  assert.match(h.sent[1].body.data,/data: one/);
});
test('managed model registration uses the actual PC-local service endpoint',async()=>{
  const h=host();await h.run({device:'phone',session:'session',id:'save',kind:'invoke',command:'api_provider_save',args:{input:{baseUrl:'http://127.0.0.1:1/v1',apiKey:'paired-phone'}}});
  assert.equal(h.calls[0].args.input.baseUrl,'http://127.0.0.1:4444/v1');assert.equal(h.calls[0].args.input.apiKey,'PC_ONLY_SECRET');
});

test('a stale phone presentation task cannot navigate the desktop or write its preferences',async()=>{
  const h=host();await h.run({device:'phone',session:'session',id:'page',kind:'preference',action:'set',key:'opencore.ui.v1.navigation.view',value:'"media"'});
  assert.equal(h.sent.at(-1).body.ok,true);
  await h.run({device:'phone',session:'session',id:'nav',kind:'invoke',command:'plugin:event|emit',args:{event:'opencore-navigate',payload:{view:'models'}}});
  assert.equal(h.calls.length,0);
});

test('phone window and webview focus requests never reach the PC window',async()=>{
 const h=host();
 for(const command of ['plugin:window|set_focus','plugin:window|show','plugin:window|unminimize','plugin:window|minimize','plugin:webview|set_webview_focus']){
  await h.run({device:'phone',session:'session',id:'focus',kind:'invoke',command,args:{label:'main'}});
  assert.equal(h.sent.at(-1).body.ok,true);
 }
 assert.equal(h.calls.length,0);
 await h.run({device:'phone',session:'session',id:'read',kind:'invoke',command:'get_snapshot',args:{}});
 assert.equal(h.sent.at(-1).body.value.conversations[0].id,'same-pc-chat');
});
