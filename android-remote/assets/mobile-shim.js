/* The phone renders OpenCore locally; the paired PC executes its commands. */
(()=>{
  'use strict';
  globalThis.__OPENCORE_MOBILE__=true;
  let port,sequence=0,callbackSequence=0,connectionError=null,appReady=false,rootObserver;
  const nonce=document.querySelector('meta[name="opencore-native-nonce"]')?.content;
  const waiting=new Map(),callbacks=new Map(),listeners=new Map(),streams=new Map();
  const storage=window.localStorage,storagePrototype=Object.getPrototypeOf(storage);
  const originalSet=storagePrototype.setItem,originalRemove=storagePrototype.removeItem,originalClear=storagePrototype.clear;
  const preferenceSeed=globalThis.__OPENCORE_PC_PREFS__&&typeof globalThis.__OPENCORE_PC_PREFS__==='object'?globalThis.__OPENCORE_PC_PREFS__:{};
  const preferenceKey=key=>typeof key==='string'&&/^opencore\.[A-Za-z0-9._:-]{1,180}$/.test(key);
  for(let index=storage.length-1;index>=0;index--){const key=storage.key(index);if(key?.startsWith('opencore.'))originalRemove.call(storage,key);}
  for(const [key,value] of Object.entries(preferenceSeed))if(preferenceKey(key)&&typeof value==='string')originalSet.call(storage,key,value);
  let resolvePort;
  const ready=new Promise(resolve=>{resolvePort=resolve;});
  function startupError(message){if(port&&!appReady)port.postMessage(JSON.stringify({id:'phone-startup-error',kind:'phone-startup-error',error:message}));}
  function request(value){
    if(connectionError)return Promise.reject(new Error(connectionError));
    const id='phone-'+(++sequence);
    return new Promise((resolve,reject)=>{
      const startup=value.command==='startup_status'||value.command==='account_action'&&value.args?.action==='startup_status';
      const timer=setTimeout(()=>{
        if(!waiting.delete(id))return;
        const message='The PC did not respond in time. Keep OpenCore open on the PC, then retry.';
        reject(new Error(message));if(startup)startupError(message);
      },startup?18000:180000);
      waiting.set(id,{resolve,reject,timer});
      ready.then(()=>{if(waiting.has(id))port.postMessage(JSON.stringify({id,...value}));}).catch(error=>{clearTimeout(timer);waiting.delete(id);reject(error);});
    });
  }
  globalThis.__opencoreCheckAppUpdates=()=>request({kind:'phone-app-update'});
  globalThis.__opencoreDisconnectPhone=()=>request({kind:'phone-disconnect'});
  function checkAppReady(){
    if(!port||appReady||!document.getElementById('root')?.querySelector('.opencore-shell .section-header'))return;
    appReady=true;rootObserver?.disconnect();
    port.postMessage(JSON.stringify({id:'phone-ready',kind:'phone-ready'}));
  }
  rootObserver=new MutationObserver(checkAppReady);
  rootObserver.observe(document.documentElement,{childList:true,subtree:true});
  function deliver(packet){
    if(packet.type==='connection-lost'){
      connectionError=packet.error||'PC disconnected. Reconnecting…';
      for(const item of waiting.values()){clearTimeout(item.timer);item.reject(new Error(connectionError));}
      for(const controller of streams.values())try{controller.error(new Error(connectionError));}catch{}
      waiting.clear();streams.clear();return;
    }
    if(packet.type==='preferences'){
      const next=packet.values&&typeof packet.values==='object'?packet.values:{};
      const old=new Map();for(let index=0;index<storage.length;index++){const key=storage.key(index);if(key?.startsWith('opencore.'))old.set(key,storage.getItem(key));}
      for(const key of old.keys())if(!Object.hasOwn(next,key))originalRemove.call(storage,key);
      for(const [key,value] of Object.entries(next))if(preferenceKey(key)&&typeof value==='string')originalSet.call(storage,key,value);
      for(const [key,oldValue] of old){const newValue=storage.getItem(key);if(newValue!==oldValue)window.dispatchEvent(new StorageEvent('storage',{key,oldValue,newValue,url:location.href,storageArea:storage}));}
      return;
    }
    if(packet.type==='event'){callbacks.get(packet.handler)?.(packet.payload);return;}
    const item=waiting.get(packet.id);
    if(packet.type==='result'){
      if(!item)return;clearTimeout(item.timer);waiting.delete(packet.id);
      if(packet.ok)item.resolve(packet.value);else item.reject(new Error(packet.error||'PC request failed.'));
    }else if(packet.type==='stream-start'){
      if(!item)return;
      clearTimeout(item.timer);
      const stream=new ReadableStream({start(controller){streams.set(packet.id,controller);},cancel(){streams.delete(packet.id);}});
      item.resolve(new Response(stream,{status:packet.status,headers:{'Content-Type':packet.contentType}}));
    }else if(packet.type==='stream-chunk'){
      streams.get(packet.id)?.enqueue(new TextEncoder().encode(packet.data||''));
    }else if(packet.type==='stream-end'){
      streams.get(packet.id)?.close();streams.delete(packet.id);waiting.delete(packet.id);
    }
  }
  window.addEventListener('message',event=>{
    if(!nonce||event.data!=='opencore-mobile-port:'+nonce||!event.ports?.length||port)return;
    port=event.ports[0];port.onmessage=event=>{try{deliver(JSON.parse(event.data));}catch{}};port.start();resolvePort();checkAppReady();
  });
  window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener(){}};
  window.__TAURI_INTERNALS__={
    metadata:{currentWindow:{label:'main'},currentWebview:{label:'main'}},
    transformCallback(callback,once=false){const id=++callbackSequence;callbacks.set(id,(...args)=>{if(once)callbacks.delete(id);return callback(...args);});return id;},
    invoke(command,args={}){
      if(command==='plugin:window|is_maximized')return Promise.resolve(false);
      if(command.startsWith('plugin:window|'))return Promise.resolve(null);
      if(command==='plugin:webview|get_all_webviews')return Promise.resolve([]);
      if(command.startsWith('plugin:dialog|'))return request({kind:'phone-dialog',command,args});
      if(command.startsWith('plugin:opener|'))return request({kind:'phone-open',command,args});
      if(command==='plugin:event|listen')return request({kind:'invoke',command,args}).then(id=>{listeners.set(id,args.handler);return id;});
      if(command==='plugin:event|unlisten')return request({kind:'invoke',command,args}).finally(()=>{callbacks.delete(listeners.get(args.eventId));listeners.delete(args.eventId);});
      return request({kind:'invoke',command,args});
    }
  };
  storagePrototype.setItem=function(key,value){originalSet.call(this,key,value);if(this===storage&&preferenceKey(String(key)))request({kind:'preference',action:'set',key:String(key),value:String(value)}).catch(()=>{});};
  storagePrototype.removeItem=function(key){originalRemove.call(this,key);if(this===storage&&preferenceKey(String(key)))request({kind:'preference',action:'remove',key:String(key)}).catch(()=>{});};
  storagePrototype.clear=function(){const keys=[];for(let index=0;index<this.length;index++){const key=this.key(index);if(preferenceKey(key))keys.push(key);}originalClear.call(this);if(this===storage)for(const key of keys)request({kind:'preference',action:'remove',key}).catch(()=>{});};
  const originalFetch=window.fetch.bind(window);
  window.fetch=async(input,options={})=>{
    const url=typeof input==='string'?input:input instanceof URL?input.href:input.url;
    if(!url.startsWith('http://127.0.0.1:1/'))return originalFetch(input,options);
    const parsed=new URL(url),headers=new Headers(options.headers||{});
    const result=await request({kind:'hub',path:parsed.pathname,method:(options.method||'GET').toUpperCase(),
      ...(typeof options.body==='string'?{body:options.body}:{}),revision:headers.get('X-OpenCore-Catalog-Revision')||''});
    if(result instanceof Response)return result;
    return new Response(result.body||'',{status:result.status,headers:{'Content-Type':result.contentType||'application/json'}});
  };
})();
