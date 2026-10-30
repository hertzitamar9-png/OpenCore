import React from 'react';

export function ComposerAction({active,empty,runtime,blocked,onSend,onStop,onRefresh,onNotice}){
  const [unloading,setUnloading]=React.useState(false),locked=React.useRef(false);
  const unload=!active&&empty&&runtime?.status==='running';
  const label=unloading?'Unloading model…':active?'Stop generation':unload?'Unload model':'Send message';
  async function release(){
    if(locked.current)return;
    locked.current=true;setUnloading(true);
    try{
      const run=()=>window.__TAURI_INTERNALS__.invoke('stop_runtime',{});
      await (globalThis.__opencoreInstallBridge?.invoke('stop_runtime',{},run)||run());
      await onRefresh?.();
    }catch(error){onNotice?.('Could not unload the model: '+String(error));}
    finally{locked.current=false;setUnloading(false);}
  }
  const h=React.createElement;
  return h('button',{type:'button',className:'send-button '+(active?'is-stop':unload?'is-unload':''),
    onClick:active?onStop:unload?()=>void release():onSend,
    disabled:unloading||!active&&!unload&&(!!blocked||empty),title:label,'aria-label':label,'aria-busy':unloading},
    h('svg',{width:18,height:18,viewBox:'0 0 24 24',fill:active?'currentColor':'none',stroke:'currentColor',strokeWidth:1.8,'aria-hidden':true},
      active?h('rect',{x:4,y:4,width:16,height:16,rx:1}):unload?
        h(React.Fragment,null,h('path',{d:'M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4'})):
        h('path',{d:'m22 2-7 20-4-9-9-4 20-7ZM22 2 11 13'})));
}
