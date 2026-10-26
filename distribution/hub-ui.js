/* Managed Hub imports use the installed model library and installation admission. */
const $hubListeners = new Set();
let $hubDescriptor = null, $hubConnecting = null, $hubStatusPending = null, $hubStatusAt = 0, $hubNativeLibrary=null;
let $hubState = {catalog: [], progress: null, available: false, loaded: false, error: '', tokenConfigured: false};
let $hubTimer = null;
const $hubProviderSync=new Set();
function $hubPublish(value) {
  if (Object.entries(value).some(([key,item])=>item!==$hubState[key]&&JSON.stringify(item)!==JSON.stringify($hubState[key]))) {
    $hubState = {...$hubState,...value}; $hubListeners.forEach(fn => fn());
  }
}
async function $hubConnect() {
  if ($hubDescriptor) return $hubDescriptor;
  if (!$hubConnecting) $hubConnecting = window.__TAURI_INTERNALS__.invoke('runtime_setup_probe', {}).then(inventory => {
    const descriptor = inventory?.hubBridge;
    if (!descriptor?.available || !/^http:\/\/127\.0\.0\.1:\d+$/.test(descriptor.url) || !descriptor.secret) throw new Error(descriptor?.reason || 'The managed Hugging Face service could not start.');
    $hubDescriptor = descriptor; $hubPublish({available:true, error:''}); return descriptor;
  }).catch(error => { $hubPublish({available:false, loaded:true, error:String(error)}); throw error; }).finally(() => {$hubConnecting=null;});
  return $hubConnecting;
}
async function $hubRequest(path, body, method) {
  const descriptor = await $hubConnect(), controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  try {
    const response = await fetch(descriptor.url + '/api' + path, {method:method || (body === undefined ? 'GET' : 'POST'),
      headers:{'Content-Type':'application/json', [descriptor.authHeader || 'X-OpenCore-Hub-Secret']:descriptor.secret,
        ...(path==='/status'&&/^[a-f0-9]{64}$/.test($hubState.catalogRevision||'')?{'X-OpenCore-Catalog-Revision':$hubState.catalogRevision}:{})},
      ...(body===undefined?{}:{body:JSON.stringify(body)}), signal:controller.signal});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error?.message || value.error || 'The Hugging Face operation failed.');
    return value;
  } finally {clearTimeout(timeout);}
}
async function $hubRefresh(force=false) {
  if ($hubStatusPending) return $hubStatusPending;
  if (!force && Date.now()-$hubStatusAt<800) return $hubState;
  $hubStatusPending=$hubRequest('/status').then(value=>{
    const old=$hubState.catalog;
    $hubStatusAt=Date.now();$hubPublish({...value,available:true,loaded:true,error:''});
    const changed=old!==$hubState.catalog;
    if(changed&&$hubNativeLibrary)window.dispatchEvent(new CustomEvent('opencore-model-library-changed',{detail:$hubAugment($hubNativeLibrary)}));
    for(const entry of value.catalog||[]){
      const key=$hubDescriptor.url+'/'+entry.id;
      if(entry.runtimeReady&&entry.providerId&&!$hubProviderSync.has(key)){
        $hubProviderSync.add(key);
        setTimeout(()=>void $hubRegister(entry).catch(()=>{$hubProviderSync.delete(key);}),0);
      }
    }
    return $hubState;
  }).catch(error=>{$hubPublish({error:String(error),loaded:true});throw error;}).finally(()=>{$hubStatusPending=null;});
  return $hubStatusPending;
}
function $useHub() {
  return p.useSyncExternalStore(listener=>{
    $hubListeners.add(listener);
    if(!$hubTimer)$hubTimer=setInterval(()=>void $hubRefresh().catch(()=>{}),1200);
    void $hubRefresh().catch(()=>{});
    return ()=>{$hubListeners.delete(listener);if(!$hubListeners.size&&$hubTimer){clearInterval($hubTimer);$hubTimer=null;}};
  },()=>$hubState,()=>$hubState);
}
function $hubProgress() {
  const job=$hubState.progress;if(!job)return null;
  const preparing=job.kind==='prepare'||/^(installing-runtime|installing-dependencies|checking-hardware|loading-model|verifying-files|verifying-inference|preparing-echo|verifying-echo)$/.test(job.stage);
  const phase=job.status==='running'||job.status==='pausing'?preparing?'preparing':/verif/.test(job.stage)?'verifying':'downloading':
    job.status==='paused'?'paused':job.status==='failed'?'failed':job.status==='ready'||job.status==='installed'?'ready':null;
  return phase?{modelId:job.id,operationId:job.jobId,phase,downloadedBytes:job.bytesDone||0,totalBytes:preparing?0:job.bytesTotal||0,
    currentFile:job.detail||job.currentFile||String(job.stage||'').replaceAll('-',' '),error:job.status==='failed'?job.error:null}:null;
}
function $hubModel(entry) {
  return {id:entry.id,label:entry.familyLabel||entry.repository.split('/').at(-1),description:entry.repository,precision:entry.quantization,
    variantOf:'hub-family-'+(entry.familyId||entry.repository.toLowerCase()).replace(/[^a-z0-9]+/g,'-'),memoryMode:entry.runtime?.adapter?'echo':'native',backend:'hub',hubModel:true,hubEntry:entry,
    selectable:!!entry.runtimeReady,installed:entry.status==='installed',runtimeReady:!!entry.runtimeReady,installable:entry.installable,
    category:entry.libraryCategory||entry.category||'text',contextTokens:entry.contextTokens||32768,totalBytes:entry.bytes,downloadBytes:entry.status==='installed'?0:entry.bytes,
    weightBytes:entry.files.filter(f=>f.role==='model').reduce((n,f)=>n+f.bytes,0),experimental:false,sourceUrl:entry.sourceUrl,
    note:'Hugging Face checkpoint. Download, runtime preparation and actual generation are verified separately.',license:entry.license||'Publisher terms'};
}
function $hubAugment(library) {
  if(!library?.models)return library;
  const families=new Map(),nativeFamilies=new Map();
  for(const entry of $hubState.catalog){const family=entry.familyId||entry.repository.toLowerCase();if(!families.has(family))families.set(family,[]);families.get(family).push(entry);for(const id of entry.nativeModelIds||[])nativeFamilies.set(id,family);}
  const imported=new Set($hubState.catalog.map(entry=>entry.repository.toLowerCase())),parents=new Set(library.models.map(model=>model.variantOf).filter(Boolean));
  const models=library.models.filter(model=>{if(nativeFamilies.has(model.id))return false;
    if(model.backend==='external'&&!model.installed&&!parents.has(model.id)){try{const url=new URL(model.sourceUrl||model.setupUrl);if(['huggingface.co','hf.co'].includes(url.hostname)&&imported.has(url.pathname.split('/').filter(Boolean).slice(0,2).join('/').toLowerCase()))return false;}catch{}}
    return true;});
  const precision=value=>String(value||'').toUpperCase().replace(/^FP16$/,'F16').replace(/^FP32$/,'F32');
  for(const [family,entries] of families){
    const packaged=library.models.filter(model=>nativeFamilies.get(model.id)===family&&(model.installed||model.id===globalThis.__opencoreHubSelectedProfile||model.totalBytes>0||model.weightBytes>0));
    const nativeQuants=new Set(packaged.filter(model=>model.installed||(model.runtimeReady!==false&&model.installable!==false)).map(model=>precision(model.precision)));
    const variants=entries.filter(entry=>!nativeQuants.has(precision(entry.quantization))).map($hubModel);
    const hubQuants=new Set(variants.map(model=>precision(model.precision)));
    for(const native of packaged.filter(model=>model.installed||!hubQuants.has(precision(model.precision)))){
      const first=entries[0],entry={id:native.id,nativeProfile:true,familyLabel:first.familyLabel,repository:first.originalRepository||first.repository,
        name:native.label,quantization:native.precision,bytes:native.totalBytes||native.weightBytes||0,status:native.installed?'installed':'not-installed',
        installable:native.installable!==false&&native.runtimeReady!==false,runtimeReady:!!native.installed&&native.selectable!==false&&native.runtimeReady!==false&&(!native.preparedRuntime||native.preparedReady!==false),profileId:native.id,
        runtime:{adapter:native.runtimeReady===false?null:'native',reason:native.note||'Existing installed OpenCore profile'},category:native.category||'text'};
      variants.push({...native,hubModel:true,hubEntry:entry});
    }
    if(!variants.length)continue;
    for(const [index,model] of variants.entries()){if(index)model.variantOf=variants[0].id;else delete model.variantOf;models.push(model);}
  }
  return {...library,models};
}
async function $hubRegister(entry) {
  const descriptor=await $hubConnect();
  const input={...(entry.providerId?{id:entry.providerId}:{}),name:'Local · '+(entry.familyLabel||entry.repository.split('/').at(-1))+' · '+entry.quantization,baseUrl:descriptor.url+'/v1',model:entry.id,
    contextTokens:Math.max(8192,Math.min(entry.runtimePreparation?.contextTokens||entry.contextTokens||32768,32768)),apiKey:descriptor.secret};
  let provider=await window.__TAURI_INTERNALS__.invoke('api_provider_save',{input});
  provider=await window.__TAURI_INTERNALS__.invoke('api_provider_model',{id:provider.id,model:entry.id,enabled:true,manual:true});
  const profile=provider.models?.find(m=>m.id===entry.id);
  if(!profile?.profileId)throw new Error('The local model ran, but its chat profile could not be registered.');
  await $hubRequest('/catalog/profile',{id:entry.id,providerId:provider.id,profileId:profile.profileId});
  window.dispatchEvent(new CustomEvent(sc,{detail:{action:'saved',provider}}));
  await $hubRefresh(true);return profile.profileId;
}
async function $hubInvoke(command,args,run) {
  if(command==='list_model_library'){
    const library=await run();$hubNativeLibrary=library;await $hubRefresh().catch(()=>{});return $hubAugment(library);
  }
  if(command==='model_install_progress'){
    const native=await run();if($hubDescriptor)await $hubRefresh().catch(()=>{});
    const hub=$hubProgress();return native&&['preparing','downloading','verifying','uninstalling'].includes(native.phase)?native:hub||native;
  }
  if(command==='install_model'&&$hubState.catalog.some(e=>e.id===args.id)){
    const value=await $hubRequest('/install',{id:args.id});await $hubRefresh(true);return value;
  }
  if(command==='hub_prepare_model'){
    const value=await $hubRequest('/prepare',{id:args.id});await $hubRefresh(true);return value;
  }
  if(command==='cancel_model_install'&&['running','pausing'].includes($hubState.progress?.status)){
    const value=await $hubRequest('/pause',{jobId:$hubState.progress.jobId});await $hubRefresh(true);return value;
  }
  if(command==='stop_runtime'&&$hubDescriptor){await $hubRequest('/stop').catch(()=>{});return run();}
  if(command==='send_chat_message'&&$hubDescriptor){
    const request=args.request||{},entry=$hubState.catalog.find(e=>e.profileId===globalThis.__opencoreHubSelectedProfile);
    if(entry){const requestId=request.submissionId||crypto.randomUUID();await $hubRequest('/context',{modelId:entry.id,conversationId:request.conversationId,requestId});
      try{return await run();}finally{await $hubRequest('/context/release',{requestId}).catch(()=>{});}}
  }
  if(command==='select_profile'||command==='start_profile'){
    const selected=args.profile||args.request?.profile;
    if($hubDescriptor&&!$hubState.catalog.some(e=>e.profileId===selected))await $hubRequest('/stop').catch(()=>{});
    globalThis.__opencoreHubSelectedProfile=selected;
  }
  return run();
}
const $hubSearchCache=new Map();
function $HubBrowser({onChanged,onNotice}) {
  const state=$useHub(),[repository,setRepository]=p.useState(''),[plan,setPlan]=p.useState(null),[selected,setSelected]=p.useState([]),[busy,setBusy]=p.useState(false),[error,setError]=p.useState(''),checking=p.useRef(false);
  const [filters,setFilters]=p.useState({match:'prefix',task:'',library:'',author:'',license:'',sort:'downloads',private:false}),[results,setResults]=p.useState(null),[searching,setSearching]=p.useState(false),searchId=p.useRef(0);
  const h=p.createElement;
  async function search(){
    const id=++searchId.current,options={query:repository,...filters},key=JSON.stringify(options),cached=!filters.private&&$hubSearchCache.get(key);
    setError('');if(cached&&Date.now()-cached.at<300000){setResults(cached.value);setSearching(false);return;}
    setSearching(true);
    try{const value=await $hubRequest('/search',options);if(id!==searchId.current)return;
      if(!filters.private){$hubSearchCache.set(key,{at:Date.now(),value});if($hubSearchCache.size>32)$hubSearchCache.delete($hubSearchCache.keys().next().value);}
      setResults(value);
    }catch(e){if(id===searchId.current){setResults(null);setError(String(e));}}finally{if(id===searchId.current)setSearching(false);}
  }
  p.useEffect(()=>{++searchId.current;setSearching(false);setResults(null);if(!repository.trim()||repository.includes('/'))return;
    const timer=setTimeout(()=>void search(),350);return()=>{clearTimeout(timer);++searchId.current;};},[repository,JSON.stringify(filters)]);
  p.useEffect(()=>{if(state.loaded&&!state.tokenConfigured&&filters.private)setFilters(old=>({...old,private:false}));},[state.loaded,state.tokenConfigured,filters.private]);
  function filter(key,value){setFilters(old=>({...old,[key]:value}));}
  function picker(key,label,values){return h('label',null,label,h('select',{'aria-label':label,value:filters[key],onChange:e=>filter(key,e.target.value)},...values.map(([value,text])=>h('option',{key:value,value},text))));}
  async function inspectRepository(value){if(checking.current)return;checking.current=true;setBusy(true);setError('');setPlan(null);setRepository(value);
    try{const plan=await $hubRequest('/plan',{repository:value});setPlan(plan);const seen=new Set();setSelected(plan.variants.filter(v=>{if(!v.installable||seen.has(v.quantization))return false;seen.add(v.quantization);return true;}).map(v=>v.id));}
    catch(e){setError(String(e));}finally{checking.current=false;setBusy(false);}}
  async function inspect(event){event.preventDefault();if(repository.includes('/'))await inspectRepository(repository);else await search();}
  async function add(){setBusy(true);setError('');try{const value=await $hubRequest('/catalog/add',{planId:plan.planId,variantIds:selected});await $hubRefresh(true);await onChanged?.();onNotice?.(`Added ${value.added.length} published variants. Choose a quantization to install.`);setPlan(null);}
    catch(e){setError(String(e));}finally{setBusy(false);}}
  return h('section',{className:'hub-browser','aria-label':'Add from Hugging Face'},
    h('h3',null,'Add from Hugging Face'),h('p',null,'Search by name or words, or paste a repository link. Choose a result to see its published packages.'),
    h('form',{onSubmit:inspect,className:'hub-repository-form'},h('input',{'aria-label':'Hugging Face repository',placeholder:'Search models or enter owner/model',value:repository,onChange:e=>{setRepository(e.target.value);setPlan(null);}}),
      h('button',{type:'submit',disabled:busy||searching||!repository.trim()},busy?'Checking…':searching?'Searching…':repository.includes('/')?'Check repository':'Search models')),
    h('div',{className:'hub-search-filters'},
      picker('match','Match',[['prefix','Starts with'],['words','All words'],['contains','Contains']]),
      picker('task','Task',[['','All tasks'],['text-generation','Text / coding'],['image-text-to-text','Vision / text'],['text-to-image','Image generation'],['image-to-image','Image editing'],['automatic-speech-recognition','Speech recognition'],['text-to-speech','Speech generation'],['text-to-video','Video generation'],['feature-extraction','Embeddings']]),
      picker('library','Format / library',[['','All libraries'],['gguf','GGUF'],['transformers','Transformers'],['diffusers','Diffusers'],['mlx','MLX'],['peft','LoRA / PEFT']]),
      picker('sort','Sort',[['downloads','Downloads'],['likes','Likes'],['lastModified','Recently updated'],['createdAt','Newest'],['trendingScore','Trending'],['name','Name']]),
      h('label',null,'Publisher',h('input',{'aria-label':'Publisher',placeholder:'Any publisher',value:filters.author,onChange:e=>filter('author',e.target.value)})),
      h('label',null,'License',h('input',{'aria-label':'License',placeholder:'e.g. apache-2.0',value:filters.license,onChange:e=>filter('license',e.target.value)}))),
    h('label',{className:'hub-private-search'},h('input',{type:'checkbox',checked:filters.private,disabled:!state.tokenConfigured&&!filters.private,onChange:e=>filter('private',e.target.checked)}),'Include models accessible with my token'),
    h('details',null,h('summary',null,state.tokenConfigured?'Hugging Face access · token saved':'Private / gated models · add access token'),h($HubSettings,{inline:true})),
    searching?h('p',{role:'status'},'Searching Hugging Face…'):null,
    results?h('section',{'aria-label':'Hugging Face search results',className:'hub-search-results'},
      h('p',{role:'status'},results.models.length?`${results.models.length} results${results.limited?' · Refine your filters for more matches':''}`:'No matching models. Try All words or another publisher.'),
      ...results.models.map(model=>h('button',{key:model.repository,type:'button',disabled:busy,onClick:()=>void inspectRepository(model.repository)},
        h('strong',null,model.repository),h('span',null,[model.task,model.library,`${model.downloads.toLocaleString()} downloads`,model.private?'Private':model.gated?'Gated':'Public'].filter(Boolean).join(' · '))))):null,
    error?h('p',{role:'alert'},error):null,
    plan?h('div',{className:'hub-plan'},h('strong',null,plan.repository),h('p',null,`${plan.variants.length} published model variants · Revision ${plan.revision.slice(0,8)}`),
      h('p',null,'Select quantizations to add. Weights download only when you press Install on a card.'),
      h('p',{role:'status'},`${plan.quantizationCount??new Set(plan.variants.map(v=>v.quantization)).size} distinct quantizations in this repository`),
      h('div',{className:'hub-variant-list'},...plan.variants.map(v=>h('label',{key:v.id},h('input',{type:'checkbox',checked:selected.includes(v.id),disabled:busy||!v.installable,onChange:e=>setSelected(s=>e.target.checked?[...s,v.id]:s.filter(id=>id!==v.id))}),
        h('span',null,v.quantization,' · ',(v.bytes/1e9).toFixed(3),' GB · ',v.repository,v.reason?' · '+v.reason:'')))),
      h('button',{disabled:busy||!selected.length,onClick:add},busy?'Adding…':`Add ${selected.length} variants`)):null,
    state.error&&!state.available?h('p',{role:'status'},state.error):null);
}
function $HubSettings({inline=false}) {
  const state=$useHub(),[token,setToken]=p.useState(''),[busy,setBusy]=p.useState(false),[message,setMessage]=p.useState('');const h=p.createElement;
  async function save(event){event.preventDefault();setBusy(true);setMessage('');try{await $hubRequest('/token',{token},'PUT');setToken('');await $hubRefresh(true);setMessage('Hugging Face token saved securely for this Windows user.');}catch(e){setMessage(String(e).replaceAll(token||'\u0000','[redacted]'));}finally{setBusy(false);}}
  async function remove(){setBusy(true);try{await $hubRequest('/token',undefined,'DELETE');await $hubRefresh(true);setMessage('Token removed. Public model downloads remain available.');}catch(e){setMessage(String(e));}finally{setBusy(false);}}
  return h('section',{className:'hub-browser','aria-label':inline?'Hugging Face access for model browser':'Hugging Face access'},h('h2',null,'Hugging Face access'),h('p',null,'Public models work without a token. A read token enables private and gated repositories your account can access.'),
    h('p',{role:'status'},state.tokenConfigured?'Access token saved':'No token saved'),h('form',{onSubmit:save,className:'hub-repository-form'},h('input',{type:'password',autoComplete:'off',value:token,'aria-label':inline?'Hugging Face access token for model browser':'Hugging Face access token',placeholder:'hf_…',onChange:e=>setToken(e.target.value)}),
      h('button',{disabled:busy||!token},busy?'Saving…':'Save token')),state.tokenConfigured?h('button',{disabled:busy,onClick:remove},'Remove token'):null,message?h('p',{role:'status'},message):null);
}
function $HubCard({group,disabled,onSelect,onNotice,onChanged,onNativeRemove,selectedProfile}) {
  const state=$useHub(),shared=$useInstallState(),h=p.createElement,[choice,setChoice]=p.useState(()=>group.variants.find(v=>v.hubEntry?.profileId===selectedProfile)?.id||group.variants.find(v=>v.installed)?.id||group.variants[0].id),[busy,setBusy]=p.useState(false),[error,setError]=p.useState(''),[removing,setRemoving]=p.useState(false);
  const model=group.variants.find(v=>v.id===choice)||group.variants[0],entry=state.catalog.find(v=>v.id===model.id)||model.hubEntry;
  const active=shared.active?.modelId===entry.id,locked=disabled||!!shared.active||busy;
  async function start(){setError('');try{await Ue('install_model',{id:entry.id});}catch(e){setError(String(e));}}
  async function prepare(){setError('');try{await Ue(entry.nativeProfile?'install_model':'hub_prepare_model',{id:entry.id});}catch(e){setError(String(e));}}
  async function use(){setBusy(true);setError('');try{await window.__TAURI_INTERNALS__.invoke('stop_runtime',{});const profile=entry.nativeProfile?entry.id:await $hubRegister(entry);await onSelect(profile);await onChanged?.();}catch(e){setError(String(e));}finally{setBusy(false);}}
  function askRemove(){setError('');if(entry.nativeProfile){onNativeRemove?.(model);return;}setRemoving(true);}
  async function uninstall(){setBusy(true);try{await $hubRequest('/uninstall',{id:entry.id});await $hubRefresh(true);await onChanged?.();setRemoving(false);}catch(e){setError(String(e));}finally{setBusy(false);}}
  return h('article',{'aria-label':entry.familyLabel||entry.repository,className:'model-library-card hub-model-card'},h('header',null,h('h3',null,entry.familyLabel||entry.repository.split('/').at(-1)),h('span',{className:'model-install-state'},active?shared.progress?.phase==='preparing'?'Preparing runtime…':'Installing…':entry.runtimeReady?'Ready to use':entry.status==='installed'?'Downloaded':'Not installed')),
    h('p',null,entry.repository),entry.builtInDescription?h('p',null,entry.builtInDescription):null,h('p',null,entry.nativeProfile?'OpenCore profile':entry.runtime?.adapter?'ECHO · Text model':entry.category+' model'),h('label',{className:'model-quantization-picker'},'Quantization',h('select',{'aria-label':'Quantization for '+entry.repository,value:entry.id,disabled:locked,onChange:e=>setChoice(e.target.value)},
      ...group.variants.map(v=>h('option',{key:v.id,value:v.id},v.precision,' · ',((v.totalBytes||v.hubEntry?.bytes||0)/1e9).toFixed(3),' GB · ',v.hubEntry?.nativeProfile?(v.installed?'Installed':'Native')+' · '+(v.memoryMode||'native').toUpperCase():v.hubEntry?.repository.split('/')[0])))),
    h('dl',null,h('div',null,h('dt',null,'Download'),h('dd',null,(entry.bytes/1e9).toFixed(3)+' GB')),h('div',null,h('dt',null,'Runtime'),h('dd',null,entry.nativeProfile&&entry.runtimeReady?'Installed native runtime':entry.runtimeReady?'Generation verified · ECHO connected':entry.runtime?.adapter?'Prepared automatically after download':'Compatible adapter required'))),
    h('small',null,entry.runtime?.reason||'The publisher’s package is pinned and verified. Local execution depends on architecture support and available memory.'),
    h('footer',null,entry.status!=='installed'?h('button',{disabled:locked||!entry.installable,onClick:start},active?'Installing…':'Install'):
      entry.runtimeReady?h('button',{disabled:locked,onClick:use},busy?'Connecting…':selectedProfile===entry.profileId?'Selected':'Use model'):
      h('button',{disabled:locked||!entry.runtime?.adapter,onClick:prepare},active?'Preparing…':'Prepare runtime'),
      entry.status==='installed'?h('button',{className:'model-delete-button',disabled:locked,onClick:askRemove},'Uninstall'):null),
    removing?h('section',{role:'dialog','aria-label':'Uninstall '+entry.name},h('p',null,'Remove this downloaded package? Conversation history is kept.'),h('button',{disabled:busy,onClick:uninstall},'Remove downloaded files'),h('button',{disabled:busy,onClick:()=>setRemoving(false)},'Cancel')):null,
    active?h('p',{role:'status'},shared.progress?.currentFile||'Starting installation…'):null,error?h('p',{role:'alert'},error):null,
    entry.downloadProgress?.status==='failed'?h('p',{role:'alert'},entry.downloadProgress.error):null);
}
const $hubStyle=document.createElement('style');$hubStyle.textContent=`.hub-browser{padding:16px;border:1px solid var(--border,#34404c);border-radius:10px;margin:16px 0}.hub-browser h3{margin:0 0 8px}.hub-browser p{font-size:12px}.hub-repository-form{display:flex;gap:10px;flex-wrap:wrap}.hub-repository-form input{flex:1;min-width:260px}.hub-repository-form input,.hub-search-filters input,.hub-search-filters select{color:var(--theme-text,#dbe5f0);background:var(--theme-input-bg,#132435);border:1px solid var(--theme-border,#527397);border-radius:6px;padding:10px;font:inherit;min-height:42px;box-sizing:border-box}.hub-repository-form input:focus,.hub-search-filters input:focus,.hub-search-filters select:focus{outline:2px solid var(--theme-accent,#6dacf5);outline-offset:2px}.hub-repository-form input::placeholder,.hub-search-filters input::placeholder{color:var(--theme-muted,#aab7c6)}.hub-search-filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:14px 0}.hub-search-filters label{display:grid;gap:6px;font-size:12px}.hub-search-filters input,.hub-search-filters select{width:100%;min-width:0}.hub-private-search{display:flex;align-items:center;gap:8px;margin:12px 0}.hub-browser summary{cursor:pointer;color:var(--theme-text,#dbe5f0);padding:8px 0}.hub-search-results{display:grid;gap:8px;max-height:350px;overflow:auto;margin:12px 0}.hub-search-results button{display:grid;gap:6px;text-align:left;padding:12px;min-height:48px;overflow-wrap:anywhere}.hub-search-results span{color:var(--theme-muted,#aab7c6);font-size:12px}.hub-plan{margin-top:14px}.hub-variant-list{display:grid;gap:9px;max-height:240px;overflow:auto;margin:12px 0}.hub-variant-list label{display:flex;gap:10px}.hub-model-card header{display:flex;gap:12px;justify-content:space-between}.hub-model-card label{display:grid;gap:8px}.hub-model-card footer{display:flex;gap:8px}`;document.head.appendChild($hubStyle);
