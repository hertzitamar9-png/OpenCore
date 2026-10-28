const $startupStyle=document.createElement('style');
$startupStyle.textContent='.startup-progress-card{width:min(390px,100%);padding:24px!important}.startup-progress-card h1{font-size:22px;margin:12px 0}.startup-progress-card>img{width:42px;height:42px}.startup-progress-card progress{width:100%;height:8px;margin:12px 0;accent-color:#8cc8ff}.startup-progress-card ol{list-style:none;padding:0;margin:12px 0;text-align:left;display:grid;gap:10px;font-size:13px}.startup-progress-card li{color:var(--theme-muted,#a6b5c6)}.startup-progress-card li.active{color:var(--theme-ink,#edf6ff);font-weight:650}.startup-progress-card li.done{color:#8cdfbb}.startup-progress-card small{display:block;font-size:12px;color:var(--theme-muted,#a6b5c6)}.startup-progress-card button{min-height:40px;margin:8px 6px 0 0}.startup-progress-card pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;text-align:left}.startup-progress-card details{text-align:left;margin-top:12px}';
document.head.appendChild($startupStyle);
function $StartupMounted({onReady}){K.useEffect(()=>{onReady();},[onReady]);return null;}
class $StartupBoundary extends K.Component{constructor(props){super(props);this.state={failed:false};}static getDerivedStateFromError(){return{failed:true};}componentDidCatch(error){this.props.onFailure(error);}render(){return this.state.failed?this.props.fallback:this.props.children;}}
function Xv({workspace:Workspace=Gv}={}){
  const[ready,setReady]=K.useState(false),[opened,setOpened]=K.useState(false),[restarting,setRestarting]=K.useState(false),[phase,setPhase]=K.useState(0),[error,setError]=K.useState(''),[elapsed,setElapsed]=K.useState(0),[copied,setCopied]=K.useState(false);
  const generation=K.useRef(0),started=K.useRef(Date.now()),deadline=K.useRef(null);
  const onOpened=K.useCallback(()=>setOpened(true),[]);
  const onModuleFailure=K.useCallback(()=>setError('The app interface could not load. Retry opening OpenCore.'),[]);
  K.useEffect(()=>{
    if(opened||error)return;
    const timer=setInterval(()=>setElapsed(Math.floor((Date.now()-started.current)/1000)),1000);
    const moduleTimeout=ready?setTimeout(()=>setError('The app interface did not finish loading. Retry opening OpenCore.'),45000):null;
    return()=>{clearInterval(timer);clearTimeout(moduleTimeout);};
  },[ready,opened,error]);
  const load=K.useCallback(async()=>{
    const ticket=++generation.current;started.current=Date.now();setElapsed(0);setError('');setCopied(false);setPhase(1);
    let timeout;
    try{
      // Native IPC selects the current local workspace. Optional online account
      // configuration must not hold a new installation on the splash screen.
      await Promise.race([
        window.__TAURI_INTERNALS__.invoke('get_snapshot',{}),
        new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Your local workspace did not respond in time.')),20000);deadline.current=timeout;})
      ]);
      if(ticket!==generation.current)return;
      const cache=globalThis.__opencoreModelLibrary;
      cache.setScope(cache.getScope()||'device-local');void cache.hydrate();
      setPhase(2);setReady(true);
    }catch(reason){
      if(ticket===generation.current)setError(String(reason?.message||reason||'The local workspace could not open.').slice(0,500));
    }finally{clearTimeout(timeout);}
  },[]);
  K.useEffect(()=>{
    void load();let closed=false,unlisten;
    Si('opencore-account-changed',({payload})=>{
      if(closed)return;
      if(payload?.restarting){generation.current++;clearTimeout(deadline.current);globalThis.__opencoreModelLibrary.setScope('');setReady(false);setRestarting(true);}
      else{if(payload?.user?.id)globalThis.__opencoreModelLibrary.setScope(String(payload.user.id));void load();}
    }).then(listener=>{if(closed)listener();else unlisten=listener;}).catch(()=>{});
    return()=>{closed=true;generation.current++;clearTimeout(deadline.current);unlisten?.();};
  },[load]);
  const details='OpenCore '+$viewCacheVersion+'\nStage: '+phase+'\nElapsed: '+elapsed+'s\n'+error;
  const loading=()=>r.jsxs('div',{className:'account-entry',children:[r.jsx(Yv,{}),r.jsx('main',{className:'account-entry-stage','aria-label':'Opening OpenCore',children:r.jsxs('section',{className:'account-entry-card account-entry-loading startup-progress-card','aria-live':'polite',children:[
    r.jsx('img',{src:'/opencore-logo.png',alt:''}),
    r.jsx('h1',{children:error?'OpenCore needs attention':restarting?'Switching your workspace':'Opening OpenCore'}),
    r.jsx('p',{children:error?error:restarting?'Your saved work remains on this PC.':phase===2?'Loading chats and tools…':'Opening your local workspace…'}),
    !error&&r.jsx('progress',{'aria-label':'Opening your local workspace'}),
    r.jsx('ol',{'aria-label':'Startup progress',children:['App opened','Local workspace','Chats and tools'].map((label,index)=>r.jsxs('li',{className:index<phase?'done':index===phase?'active':'',children:[index<phase?'✓ ':String(index+1)+'. ',label]},label))}),
    r.jsx('small',{children:error?'Retry to reconnect to the local app.':'Local startup · '+elapsed+'s'}),
    error?r.jsxs(r.Fragment,{children:[r.jsx('button',{type:'button',onClick:()=>{if(ready)window.location.reload();else void load();},children:'Retry opening OpenCore'}),r.jsx('button',{type:'button',onClick:()=>{void navigator.clipboard.writeText(details).then(()=>setCopied(true)).catch(()=>{});},children:copied?'Copied':'Copy startup details'}),r.jsxs('details',{children:[r.jsx('summary',{children:'Startup details'}),r.jsx('pre',{children:details})]})]}):null
  ]})})]});
  if(ready&&!restarting)return r.jsx($StartupBoundary,{fallback:loading(),onFailure:onModuleFailure,children:r.jsx(K.Suspense,{fallback:loading(),children:r.jsxs(K.Fragment,{children:[r.jsx(Workspace,{}),r.jsx($StartupMounted,{onReady:onOpened})]})})});
  return loading();
}
