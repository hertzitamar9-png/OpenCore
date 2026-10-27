/* Execute paired-phone requests through this PC's existing OpenCore runtime. */
const $mobileNative=window.__TAURI_INTERNALS__;
const $mobileHostListeners=new Map();
let $mobileHostStopped=false, $mobileEventWrites=Promise.resolve(),$mobilePreferenceSyncAt=0;
const $mobileHostSend=(path,value)=>$hubRequest('/mobile/host/'+path,value);
function $mobileHostEvent(device,session,handler,payload){
  $mobileEventWrites=$mobileEventWrites.then(()=>$mobileHostSend('event',{device,session,handler,payload})).catch(()=>{});
}
async function $mobileHostUnlisten(key,item){
  $mobileHostListeners.delete(key);
  try{window.__TAURI_EVENT_PLUGIN_INTERNALS__?.unregisterListener(item.event,item.callbackId);}catch{}
  try{await $mobileNative.invoke('plugin:event|unlisten',{event:item.event,eventId:item.nativeEventId});}catch{}
}
async function $mobileHostTask(task){
  const {device,session,id}=task;
  const finish=value=>$mobileHostSend('result',{device,session,id,phase:'complete',...value});
  try{
    if(task.kind==='preference'){
      if(task.action==='set')window.localStorage.setItem(task.key,task.value);
      else if(task.action==='remove')window.localStorage.removeItem(task.key);
      else throw Error('Invalid OpenCore preference update');
      await finish({ok:true,value:null});return;
    }
    if(task.kind==='hub'){
      const descriptor=await $hubConnect();
      const headers={'Content-Type':'application/json',[descriptor.authHeader||'X-OpenCore-Hub-Secret']:descriptor.secret};
      if(task.revision&&/^[a-f0-9]{64}$/.test(task.revision))headers['X-OpenCore-Catalog-Revision']=task.revision;
      const response=await fetch(descriptor.url+task.path,{method:task.method,headers,...(typeof task.body==='string'?{body:task.body}:{})});
      const contentType=response.headers.get('Content-Type')||'application/json';
      if(contentType.includes('text/event-stream')&&response.body){
        await $mobileHostSend('result',{device,session,id,phase:'start',status:response.status,contentType});
        const reader=response.body.getReader(),decoder=new TextDecoder();
        try{while(true){const {value,done}=await reader.read();if(done)break;const data=decoder.decode(value,{stream:true});if(data)await $mobileHostSend('result',{device,session,id,phase:'chunk',data});}
          const tail=decoder.decode();if(tail)await $mobileHostSend('result',{device,session,id,phase:'chunk',data:tail});
        }finally{reader.releaseLock();}
        await $mobileHostSend('result',{device,session,id,phase:'end'});return;
      }
      await finish({ok:true,value:{status:response.status,contentType,body:await response.text()}});return;
    }
    const command=task.command,args={...(task.args||{})};let value;
    if(command==='plugin:event|listen'){
      const handler=args.handler,event=args.event;
      if(typeof event!=='string'||event.length>200)throw Error('Invalid event name');
      const callback=$mobileNative.transformCallback(payload=>$mobileHostEvent(device,session,handler,payload));
      const nativeEventId=await $mobileNative.invoke(command,{...args,handler:callback});
      $mobileHostListeners.set(device+':'+handler,{device,session,event,clientHandler:handler,callbackId:callback,nativeEventId});
      value=handler;
    }else if(command==='plugin:event|unlisten'){
      const key=device+':'+args.eventId,item=$mobileHostListeners.get(key);
      if(item)await $mobileHostUnlisten(key,item);value=null;
    }else{
      if(command==='api_provider_save'&&args.input?.baseUrl==='http://127.0.0.1:1/v1'){
        const descriptor=await $hubConnect();args.input={...args.input,baseUrl:descriptor.url+'/v1',apiKey:descriptor.secret};
      }
      value=await $mobileNative.invoke(command,args);
      if(command==='runtime_setup_probe'&&value?.hubBridge?.available){
        value={...value,hubBridge:{available:true,url:'http://127.0.0.1:1',secret:'paired-phone',authHeader:'X-OpenCore-Hub-Secret'}};
      }
    }
    await finish({ok:true,value:value===undefined?null:value});
  }catch(error){await finish({ok:false,error:String(error?.message||error||'The PC request failed.')}).catch(()=>{});}
}
async function $mobileHostLoop(){
  if($mobileHostStopped)return;
  let delay=5000;
  try{
    if(Date.now()-$mobilePreferenceSyncAt>2500){
      $mobilePreferenceSyncAt=Date.now();const values={};
      for(let index=0;index<window.localStorage.length&&index<512;index++){const key=window.localStorage.key(index);if(key&&/^opencore\.[A-Za-z0-9._:-]{1,180}$/.test(key)){const value=window.localStorage.getItem(key);if(value!==null&&value.length<=32768)values[key]=value;}}
      await $mobileHostSend('preferences',{values});
    }
    const result=await $hubRequest('/mobile/host/poll');delay=result.active?250:5000;
    const sessions=result.sessions||{};
    for(const [key,item] of $mobileHostListeners)if(sessions[item.device]!==item.session)void $mobileHostUnlisten(key,item);
    for(const task of result.tasks||[])void $mobileHostTask(task);
  }catch{}
  if(!$mobileHostStopped)setTimeout(()=>void $mobileHostLoop(),delay);
}
if(!globalThis.__OPENCORE_MOBILE__){window.addEventListener('pagehide',()=>{$mobileHostStopped=true;},{once:true});void $mobileHostLoop();}
