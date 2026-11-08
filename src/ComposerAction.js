import React from 'react';

export function ComposerAction({active,empty,runtime,profile=runtime?.profile,blocked,onSend,onStop,onRefresh,onNotice,imageActive=false}){
  const live=useModelRuntime(profile,runtime);
  const [unloading,setUnloading]=React.useState(false),locked=React.useRef(false);
  const unload=!active&&!blocked&&!imageActive&&empty&&live?.status==='running'&&live.profile===profile&&canControlModel(profile);
  const label=unloading?'Unloading model…':active?'Stop generation':unload?'Unload model':'Send message';
  const title=unloading||unload?label:active?'Stop':blocked||'Send';
  async function release(){
    if(locked.current)return;
    locked.current=true;setUnloading(true);
    try{
      await changeModelRuntime(profile,'unload');
      await onRefresh?.();
    }catch(error){onNotice?.('Could not unload the model: '+String(error));}
    finally{locked.current=false;setUnloading(false);}
  }
  const h=React.createElement;
  return h('button',{type:'button',className:'send-button '+(active?'is-stop':unload?'is-unload':''),
    onClick:active?onStop:unload?()=>void release():onSend,
    disabled:unloading||!active&&!unload&&(!!blocked||empty),title,'aria-label':label,'aria-busy':unloading},
    h('svg',{width:18,height:18,viewBox:'0 0 24 24',fill:active?'currentColor':'none',stroke:'currentColor',strokeWidth:1.8,'aria-hidden':true},
      active?h('rect',{x:4,y:4,width:16,height:16,rx:1}):unload?
        h('path',{d:'m6 6 12 12M18 6 6 18'}):
        h('path',{d:'m22 2-7 20-4-9-9-4 20-7ZM22 2 11 13'})));
}

function canControlModel(profile){
  return !!profile&&profile!=='stopped'&&(!profile.startsWith('api:')||!!globalThis.__opencoreLocalProfiles?.get(profile));
}

export function useModelRuntime(profile,runtime){
  const [sample,setSample]=React.useState(globalThis.__opencoreRuntimeSample);
  React.useEffect(()=>{
    const update=()=>setSample(globalThis.__opencoreRuntimeSample);
    window.addEventListener('opencore-model-runtime',update);
    update();
    return()=>window.removeEventListener('opencore-model-runtime',update);
  },[]);
  return sample&&sample.profile===profile?sample.runtime:runtime;
}

async function changeModelRuntime(profile,action){
  const run=globalThis.__opencoreChangeModelRuntime;
  if(run)return run(profile,action);
  return action==='unload'?window.__TAURI_INTERNALS__.invoke('stop_runtime',{}):
    window.__TAURI_INTERNALS__.invoke('start_profile',{request:{profile,attachUrl:null}});
}

function ModelRuntimeButton({profile,runtime,disabled,onRefresh,onNotice}){
  const current=useModelRuntime(profile,runtime);
  const [pending,setPending]=React.useState(false),[error,setError]=React.useState('');
  if(!canControlModel(profile))return null;
  const loaded=current?.status==='running'&&current.profile===profile;
  const loading=current?.status==='starting'&&current.profile===profile;
  async function change(){
    setPending(true);setError('');
    try{await changeModelRuntime(profile,loaded?'unload':'load');await onRefresh?.();}
    catch(failure){setError(String(failure));onNotice?.(String(failure));}
    finally{setPending(false);}
  }
  const h=React.createElement;
  return h('div',{className:'model-runtime-action'},
    h('button',{type:'button',disabled:disabled||pending||loading,onClick:()=>void change()},
      loading?'Loading model…':pending?(loaded?'Unloading model…':'Loading model…'):loaded?'Unload model':'Load model'),
    error?h('small',{role:'alert'},error):null);
}
