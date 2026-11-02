export function installModelModes(React, catalog, service) {
  const h=React.createElement,byId=new Map(catalog.models.map(model=>[model.id,model])),byProfile=new Map();
  const positive=value=>Number.isInteger(value)&&value>0?value:null;
  const text=model=>model.supportsTextGeneration??(model.supportsTextChat!==false&&model.computerRuntime!=='gui-jepa-v1'&&(['text','brain'].includes(model.category)||['omni','computer-use','ocr'].includes(model.category)&&model.inputModalities?.includes('text')&&model.outputModalities?.includes('text')));
  const keyFor=model=>{
    let current=byId.get(model.id)||model,seen=new Set();
    while(current.variantOf&&byId.has(current.variantOf)&&!seen.has(current.id)){seen.add(current.id);current=byId.get(current.variantOf);}
    return current.familyId||current.id;
  };
  const bindings=new Map();
  const listeners=new Set();let initialized=false;
  function reindex(){bindings.clear();byProfile.clear();for(const model of byId.values()){
    if(!model.supportsTextGeneration)continue;
    const key=keyFor(model)+'|'+(model.artifacts||[]).slice().sort().join('|')+'|'+model.precision;
    if(!bindings.has(key))bindings.set(key,model);
    byProfile.set(model.id,bindings.get(key));
    for(const profile of Object.values(model.modeProfiles||{}))byProfile.set(profile,bindings.get(key));
  }}
  // The legacy catalog is sufficient for a conservative first paint. New
  // descriptors arrive once over the app's authenticated loopback service.
  for(const model of byId.values())if(text(model)){
    model.supportsTextGeneration=true;
    const limits=[model.publishedContextTokens,model.contextTokens,model.supportedContextTokens].map(positive).filter(Boolean);
    model.operatingModes||=(['native','echo'].map(id=>({id,contextTokens:limits.length?Math.min(...limits,id==='echo'?32768:1048576):null})));
  }
  reindex();
  const ready=service.request('/runtime/modes').then(data=>{
    for(const model of data.models||[])byId.set(model.id,{...byId.get(model.id),...model});reindex();
  }).catch(()=>{}).finally(()=>{initialized=true;listeners.forEach(callback=>callback());service.changed().catch(()=>{});});
  const policy=model=>model.operatingModes?model:byId.get(model.id)||byProfile.get(model.id);
  const storageKey=model=>'opencore.model-mode.'+(model.familyId||model.modeFamilyKey||keyFor(policy(model)||model));
  const mode=model=>{
    try{const saved=localStorage.getItem(storageKey(model));if(saved==='native'||saved==='echo')return saved;}catch{}
    return model.operatingMode||'echo';
  };
  const context=(model,selected=mode(model))=>{
    const item=(policy(model)?.operatingModes||[]).find(item=>item.id===selected);
    if(item)return positive(item.contextTokens);
    const limits=[model.publishedContextTokens,model.contextTokens,model.supportedContextTokens].map(positive).filter(Boolean);
    return limits.length?Math.min(...limits,selected==='echo'?32768:1048576):null;
  };
  function augment(library){
    const groups=new Map(),rest=[];
    for(const model of library.models||[]){
      const meta=byId.get(model.id)||byProfile.get(model.id);
      if(!meta?.supportsTextGeneration){rest.push(model);continue;}
      const key=keyFor(meta)+'|'+(meta.artifacts||[]).slice().sort().join('|')+'|'+meta.precision;
      const group=groups.get(key)||[];group.push({model,meta});groups.set(key,group);
    }
    for(const group of groups.values()){
      const first=group.find(item=>!item.meta.modeAliasOf)||group[0],selected=mode(first.meta),profiles=first.meta.modeProfiles||{};
      const loaded=group.find(item=>item.model.installed)||first;
      const runnable=profiles[selected]||first.meta.memoryMode===selected;
      rest.push({...first.model,installed:group.some(item=>item.model.installed),runtimeReady:runnable?loaded.model.runtimeReady:false,
        selectable:runnable?loaded.model.selectable:false,preparedReady:loaded.model.preparedReady,modeFamilyKey:keyFor(first.meta),
        operatingModes:first.meta.operatingModes,supportsTextGeneration:true,publishedContextTokens:first.meta.publishedContextTokens,
        modeProfiles:profiles,operatingProfileId:profiles[selected]||first.model.id,memoryMode:selected,
        contextTokens:context(first.meta,selected),label:(first.model.label||first.meta.label||first.model.id).replace(/ · (ECHO|Native)$/i,'')});
    }
    return {...library,models:rest};
  }
  function entry(value){
    if(!value||value.nativeProfile)return value;
    if(!text(value))return value;
    const selected=mode(value),proof=value.runtimePreparations?.[selected]||((value.runtimePreparation?.operatingMode||'echo')===selected?value.runtimePreparation:null);
    return {...value,operatingMode:selected,runtimePreparation:proof||{},runtimeReady:!!value.runtimeReady&&(value.operatingMode||'echo')===selected||!!(value.status==='installed'&&proof?.runtimeReady&&proof.receiptRevision===value.revision)};
  }
  async function idle(){
    const snapshot=await service.invoke('get_snapshot',{});
    if(snapshot.activeConversationIds?.length||['starting','stopping'].includes(snapshot.runtime?.status))throw Error('Wait for the current model request before changing operating mode.');
  }
  async function ensure(value){
    if(!value||value.nativeProfile||!text(value))return value;
    const selected=mode(value);
    if((value.operatingMode||'echo')===selected)return value;
    await idle();return service.request('/runtime/mode',{id:value.id,operatingMode:selected});
  }
  async function activate(id,chosenMode){
    await ready;
    const meta=byProfile.get(id);
    if(!meta?.modeProfiles)return id;
    await idle();
    const selected=chosenMode||Object.entries(meta.modeProfiles).find(([,profile])=>profile===id)?.[0]||mode(meta),result=await service.request('/runtime/native-profile',{id:meta.id,operatingMode:selected});
    return result.profileId;
  }
  function profileInfo(id){
    const local=byId.get(id)||byProfile.get(id);
    if(local)return {mode:local.memoryMode||'native',contextTokens:context(local,local.memoryMode||'native')};
    const managed=service.catalog?.().find(item=>item.profileId===id);
    if(managed)return {mode:managed.operatingMode||'echo',contextTokens:managed.runtimePreparation?.contextTokens||context(managed)};
    return null;
  }
  async function before(command,args){
    if(command==='send_chat_message'&&args.request){
      const snapshot=await service.invoke('get_snapshot',{}),info=profileInfo(snapshot?.runtime?.profile);
      if(info?.mode==='native'&&info.contextTokens&&[undefined,27852,200000].includes(args.request.compactAtTokens))args.request.compactAtTokens=Math.floor(info.contextTokens*.85);
    }
    if(['select_profile','start_profile','model_removal_plan','uninstall_model'].includes(command)){
      const selected=args.profile||args.request?.profile||args.id,meta=byId.get(selected)||byProfile.get(selected);
      const choice=meta&&['model_removal_plan','uninstall_model'].includes(command)?mode(meta):undefined;
      const profile=await activate(selected,choice);
      if(profile!==selected){if(args.request?.profile)args.request.profile=profile;else if(args.profile)args.profile=profile;else args.id=profile;}
    }
  }
  function Control({model,disabled,onChanged}){
    const meta=policy(model),[selected,setSelected]=React.useState(()=>mode(model)),[busy,setBusy]=React.useState(false),[error,setError]=React.useState('');
    React.useEffect(()=>setSelected(mode(model)),[model.id,model.operatingMode]);
    const [,refresh]=React.useState(0);
    React.useEffect(()=>{const update=()=>refresh(value=>value+1);listeners.add(update);return()=>listeners.delete(update);},[]);
    if(!(meta?.supportsTextGeneration||text(model)))return null;
    const cap=context(model,selected),target=selected==='native'?1048576:32768;
    async function change(next){
      if(busy)return;setBusy(true);setError('');
      try{
        await idle();
        if(!model.nativeProfile&&/^hf-[a-f0-9]{24}$/.test(model.id))await service.request('/runtime/mode',{id:model.id,operatingMode:next});
        localStorage.setItem(storageKey(model),next);setSelected(next);await service.changed();await onChanged?.();
      }catch(failure){setError(String(failure));}finally{setBusy(false);}
    }
    return h('section',{className:'model-operating-modes','aria-label':'Operating mode',style:{margin:'12px 0'}},
      h('label',{className:'model-quantization-picker'},'Operating mode',h('select',{'aria-label':'Operating mode',value:selected,disabled:disabled||busy||!initialized,onChange:event=>void change(event.target.value)},
        h('option',{value:'echo'},'ECHO · 32K working window'),h('option',{value:'native'},'Native · 1M target'))),
      h('small',{style:{display:'block',color:'var(--theme-muted)'}},(selected==='echo'?'ECHO keeps archived history beyond the active window. ':'Native uses the model context directly. ')+
        (cap?'Supported window: '+cap.toLocaleString()+' tokens'+(cap<target?' · below the '+target.toLocaleString()+' target.':'.'):'The supported context window is unknown.')+' The loaded window also depends on available memory.'+
        (meta?.operatingModes?.find(item=>item.id===selected)?.supported===false?' This format needs an integrated text runtime.':'')),
      error?h('p',{role:'alert'},error):null);
  }
  const api={augment,entry,mode,context,ensure,activate,policy,profileInfo,before,Control,ready};
  globalThis.__opencoreModelModes=api;return api;
}
