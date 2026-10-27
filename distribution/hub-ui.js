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
    return true;}).map(model=>{const adapter=$hubBuiltInAdapter(model);return adapter?{...model,hubModel:true,hubEntry:adapter}:model;});
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
  const [filters,setFilters]=p.useState({match:'prefix',task:'',library:'',author:'',license:'',sort:'downloads',private:false}),[results,setResults]=p.useState(null),[searching,setSearching]=p.useState(false),[browse,setBrowse]=p.useState(false),[page,setPage]=p.useState(0),searchId=p.useRef(0),pages=p.useRef([]),resultList=p.useRef(null);
  const tabs=[['downloads','Popular'],['trendingScore','Trending'],['lastModified','Recently updated'],['likes','Most liked'],['createdAt','Newly added'],['name','A–Z']];
  const h=p.createElement;
  async function search(cursor=null,target=0){
    const id=++searchId.current,options={query:repository,...filters,limit:100,...(cursor?{cursor}:{})},key=JSON.stringify([state.instanceId,options]),cached=!filters.private&&$hubSearchCache.get(key);
    function show(value){if(target===0)pages.current=[value];else pages.current[target]=value;setPage(target);setResults(value);if(resultList.current)resultList.current.scrollTop=0;}
    setError('');if(cached&&Date.now()-cached.at<300000){show(cached.value);setSearching(false);return;}
    setSearching(true);
    try{const value=await $hubRequest('/search',options);if(id!==searchId.current)return;
      if(!filters.private){$hubSearchCache.set(key,{at:Date.now(),value});if($hubSearchCache.size>32)$hubSearchCache.delete($hubSearchCache.keys().next().value);}
      show(value);
    }catch(e){if(id===searchId.current){if(cursor&&String(e).toLowerCase().includes('search page expired')){const first={query:repository,...filters,limit:100};$hubSearchCache.delete(JSON.stringify([state.instanceId,first]));pages.current=[];setPage(0);setResults(null);await search();return;}if(target===0)setResults(null);setError(String(e));}}finally{if(id===searchId.current)setSearching(false);}
  }
  p.useEffect(()=>{++searchId.current;setSearching(false);setResults(null);setPage(0);pages.current=[];if((!repository.trim()&&!browse)||repository.includes('/'))return;
    const timer=setTimeout(()=>void search(),350);return()=>{clearTimeout(timer);++searchId.current;};},[repository,JSON.stringify(filters),browse]);
  p.useEffect(()=>{if(state.loaded&&!state.tokenConfigured&&filters.private)setFilters(old=>({...old,private:false}));},[state.loaded,state.tokenConfigured,filters.private]);
  function filter(key,value){setFilters(old=>({...old,[key]:value}));}
  function selectTab(value){if(repository.includes('/'))setRepository('');setBrowse(true);setPlan(null);filter('sort',value);}
  function picker(key,label,values){return h('label',null,label,h('select',{'aria-label':label,value:filters[key],onChange:e=>filter(key,e.target.value)},...values.map(([value,text])=>h('option',{key:value,value},text))));}
  async function inspectRepository(value){if(checking.current)return;checking.current=true;setBusy(true);setError('');setPlan(null);setRepository(value);
    try{const plan=await $hubRequest('/plan',{repository:value});setPlan(plan);const seen=new Set();setSelected(plan.variants.filter(v=>{if(!v.installable||seen.has(v.quantization))return false;seen.add(v.quantization);return true;}).map(v=>v.id));}
    catch(e){setError(String(e));}finally{checking.current=false;setBusy(false);}}
  p.useEffect(()=>{const onBase=event=>{const repo=event.detail?.repository;if(typeof repo==='string'&&/^[^/]+\/[^/]+$/.test(repo)){setBrowse(false);void inspectRepository(repo);document.querySelector('.hub-browser[aria-label="Add from Hugging Face"]')?.scrollIntoView({behavior:'smooth',block:'start'});}};window.addEventListener('opencore-hub-find-base',onBase);return()=>window.removeEventListener('opencore-hub-find-base',onBase);},[]);
  async function inspect(event){event.preventDefault();if(repository.includes('/'))await inspectRepository(repository);else await search();}
  function previousPage(){if(page>0){setPage(page-1);setResults(pages.current[page-1]);if(resultList.current)resultList.current.scrollTop=0;}}
  function nextPage(){if(pages.current[page+1]){setPage(page+1);setResults(pages.current[page+1]);if(resultList.current)resultList.current.scrollTop=0;}else if(results?.nextCursor)void search(results.nextCursor,page+1);}
  async function add(){setBusy(true);setError('');try{const value=await $hubRequest('/catalog/add',{planId:plan.planId,variantIds:selected});await $hubRefresh(true);await onChanged?.();onNotice?.(`Added ${value.added.length} published variants. Choose a quantization to install.`);setPlan(null);}
    catch(e){setError(String(e));}finally{setBusy(false);}}
  return h('section',{className:'hub-browser','aria-label':'Add from Hugging Face'},
    h('h3',null,'Add from Hugging Face'),h('p',null,'Search by name or words, or paste a repository link. Choose a result to see its published packages.'),
    h('form',{onSubmit:inspect,className:'hub-repository-form'},h('input',{'aria-label':'Hugging Face repository',placeholder:'Search models or enter owner/model',value:repository,onChange:e=>{setRepository(e.target.value);setPlan(null);}}),
      h('button',{type:'submit',disabled:busy||searching||!repository.trim()},busy?'Checking…':searching?'Searching…':repository.includes('/')?'Check repository':'Search models')),
    h('div',{role:'tablist','aria-label':'Browse Hugging Face models',className:'hub-discovery-tabs'},...tabs.map(([value,label],index)=>h('button',{
      key:value,type:'button',role:'tab','aria-selected':filters.sort===value,tabIndex:filters.sort===value?0:-1,
      onClick:()=>selectTab(value),onKeyDown:e=>{
        if(!['ArrowRight','ArrowLeft','Home','End'].includes(e.key))return;e.preventDefault();
        const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
        selectTab(tabs[next][0]);e.currentTarget.parentElement.querySelectorAll('[role="tab"]')[next]?.focus();
      }},label))),
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
    results?h('section',{'aria-label':'Hugging Face search results'},
      h('div',{className:'hub-pagination','aria-label':'Search result pages'},h('button',{type:'button',disabled:searching||page===0,onClick:previousPage},'Previous page'),
        h('span',{role:'status'},`Page ${page+1} · ${results.models.length} models`),
        h('button',{type:'button',disabled:searching||!(pages.current[page+1]||results.nextCursor),onClick:nextPage},'Next page')),
      !results.models.length?h('p',{role:'status'},results.nextCursor?'No matches on this page. More results are available.':'No matching models. Try All words or another publisher.'):null,
      h('div',{className:'hub-search-results',ref:resultList},...results.models.map(model=>h('button',{key:model.repository,type:'button',disabled:busy,onClick:()=>void inspectRepository(model.repository)},
        h('strong',null,model.repository),h('span',null,[model.task,model.library,`${model.downloads.toLocaleString()} downloads`,model.private?'Private':model.gated?'Gated':'Public'].filter(Boolean).join(' · ')))))):null,
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
function $HubOCR({entry}) {
  const h=p.createElement,[file,setFile]=p.useState(null),[job,setJob]=p.useState(null),[error,setError]=p.useState(''),[starting,setStarting]=p.useState(false);
  p.useEffect(()=>{let alive=true;
    async function poll(){try{const value=await $hubRequest('/specialist/ocr/status');if(alive&&value.status!=='idle')setJob(value);}catch(e){if(alive)setError(String(e));}}
    void poll();return()=>{alive=false;};},[entry.id]);
  p.useEffect(()=>{if(job?.status!=='running')return;let alive=true,timer;
    async function poll(){try{const value=await $hubRequest('/specialist/ocr/status');if(!alive)return;setJob(value);if(value.status==='running')timer=setTimeout(poll,1200);}catch(e){if(alive)setError(String(e));}}
    timer=setTimeout(poll,1200);return()=>{alive=false;clearTimeout(timer);};},[job?.id,job?.status]);
  async function recognize(){if(!file)return;setError('');setStarting(true);try{
    if(file.size>5*1024*1024)throw new Error('Choose an image up to 5 MB.');
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error('The image could not be opened.'));reader.readAsDataURL(file);});
    const image=data.slice(data.indexOf(',')+1);setJob(await $hubRequest('/specialist/ocr',{id:entry.id,image}));
  }catch(e){setError(String(e));}finally{setStarting(false);}}
  const busy=starting||job?.status==='running';
  return h('section',{className:'hub-ocr','aria-label':'Unlimited OCR'},h('p',null,'Choose an image to extract text locally on this PC. PNG, JPEG or WebP, up to 5 MB.'),
    h('input',{type:'file',accept:'image/png,image/jpeg,image/webp','aria-label':'Image for OCR',disabled:busy,onChange:e=>{setFile(e.target.files?.[0]||null);setJob(null);setError('');}}),
    h('button',{type:'button',disabled:busy||!file,onClick:recognize},busy?'Reading image…':'Extract text'),
    job?.status==='complete'?h('label',null,'Recognized text',h('textarea',{readOnly:true,value:job.text||'',rows:6,'aria-label':'Recognized text'})):null,
    job?.status==='complete'&&!job.text?h('p',{role:'status'},'No text was recognized.'):null,
    job?.status==='failed'?h('p',{role:'alert'},job.error):null,error?h('p',{role:'alert'},error):null);
}
function $hubRepositoryOf(url) {
  try {const parsed=new URL(url||'');return ['huggingface.co','hf.co'].includes(parsed.hostname)?parsed.pathname.split('/').filter(Boolean).slice(0,2).join('/').toLowerCase():'';}catch{return '';}
}
function $hubIsAdapterCheckpoint(model) {
  if(model?.packageKind==='adapter'||model?.format==='lora')return true;
  // Only known publisher LoRA repositories, rather than loosely excluding fine-tunes by name.
  const repository=$hubRepositoryOf(model?.sourceUrl);
  return ['lightx2v/qwen-image-2.1-lightning','akhaliq/qwen-image-2.1-multiple-angles-lora',
    'akhaliq/qwen-image-2.1-next-scene-lora','gribbliest/qwen-image-2.1-qma-camera-angles',
    'ausboss/qwen-image-2.1-outfit-swap-consistency-lora'].includes(repository);
}
function $hubBuiltInAdapter(model) {
  const repo=$hubRepositoryOf(model.sourceUrl);
  if(!['lightx2v/qwen-image-2.1-lightning','akhaliq/qwen-image-2.1-multiple-angles-lora'].includes(repo))return null;
  return {id:model.id,nativeProfile:true,repository:repo,baseRepository:'Qwen/Qwen-Image-2.1',packageKind:'adapter',
    format:'lora',familyLabel:model.label,quantization:model.precision,bytes:model.totalBytes||model.downloadBytes||0,
    category:'image',installable:model.installable!==false,status:model.installed?'installed':'not-installed',
    runtime:{adapter:null,reason:'This is a LoRA add-on, not a standalone base generator. Select an installed compatible Qwen checkpoint.'}};
}
function $hubBaseModels(entry) {
  const repository=String(entry?.baseRepository||'').toLowerCase();
  if(!/^[a-z0-9_.-]+\/[a-z0-9_.-]+$/.test(repository))return [];
  const native=$hubNativeLibrary?.models||[];
  const catalog=$hubState.catalog||[];
  const all=[...native.map(model=>({...model,native:true})),...catalog.map(model=>({...model,native:false}))];
  const byId=new Map(all.map(model=>[model.id,model]));
  // Only expand a quantization family after a publisher-declared or explicitly researched anchor matches.
  function family(model,seen=new Set()) {
    if(!model?.id||seen.has(model.id))return '';
    seen.add(model.id);
    return model.variantOf&&byId.has(model.variantOf)?family(byId.get(model.variantOf),seen):model.id;
  }
  const knownArchitecture=repository==='qwen/qwen-image-2.1'?'qwen21':repository==='openmoss-team/moss-image-sft'?'moss-sft':entry.repository?.startsWith('Muapi/ltx-2-2.3-')?'ltx23':'';
  function matches(model) {
    if(!model||model.category!==entry.category||$hubIsAdapterCheckpoint(model))return null;
    if(model.id===entry.id)return null;
    if(!model.native) {
      if(knownArchitecture==='ltx23'&&model.repository?.toLowerCase()==='lightricks/ltx-2.3')return 'family';
      if(knownArchitecture==='ltx23'&&model.repository?.toLowerCase()==='lightricks/ltx-2.5')return 'related';
      if(model.repository?.toLowerCase()===repository&&model.packageKind!=='adapter')return 'exact';
      return null;
    }
    const fromSource=$hubRepositoryOf(model.sourceUrl);
    const lineage=String(model.installation?.sourceIdentity||'').split('@')[0].toLowerCase();
    if(fromSource===repository||lineage===repository)return 'exact';
    if(knownArchitecture==='moss-sft'&&['moss-image-sft','moss-image-sft-int4-convrot','moss-image-sft-int8-convrot'].includes(model.id))return 'family';
    if(knownArchitecture==='ltx23') {
      if(model.id.startsWith('ltx-25-'))return 'related'; // Official LTX reports many, but not all, 2.3 LoRAs work with 2.5.
      if(model.id==='ltx-2'||model.id.startsWith('ltx-2-published-')||model.id.startsWith('ltx-23-'))return 'family';
      if(model.id==='ltx-video'||model.id.startsWith('ltx-video-'))return 'related';
    }
    if(knownArchitecture==='qwen21') {
      if(model.id==='qwen-image-21'||model.id.startsWith('qwen-image-21-gguf'))return 'family';
      // Publisher labels these as Qwen Image 2.1 derived/quantized checkpoints, not identical weights.
      if(model.id==='qwen-image-21-uc'||model.id.startsWith('qwen-image-21-uc-'))return 'related';
    }
    // Other architectures can still reuse quantizations of the same authoritative base model.
    return null;
  }
  const anchors=new Map(all.map(model=>[model.id,matches(model)]).filter(([,kind])=>kind));
  const suggestions=all.filter(model=>model.id&&!$hubIsAdapterCheckpoint(model)&&model.category===entry.category)
    .map(model=>({model,compatibility:anchors.get(model.id)||anchors.get(family(model))}))
    .filter(item=>item.compatibility)
    .map(({model,compatibility})=>({
      id:model.id,label:model.label||model.name||model.repository||model.id,precision:model.precision||model.quantization||'Publisher format',
      bytes:model.totalBytes||model.bytes||model.downloadBytes||0,installed:model.native?!!model.installed:model.status==='installed',
      installable:model.installable!==false,backend:model.backend||model.runtime?.adapter||null,
      native:!!model.native,category:model.category,compatibility,sourceRepository:$hubRepositoryOf(model.sourceUrl)||model.repository||'',
      runtimeSupported:!!model.native&&model.backend==='comfyui'&&model.runtimeReady!==false
    }));
  const unique=new Map();
  for(const model of suggestions)if(!unique.has(model.id))unique.set(model.id,model);
  const order={exact:0,family:1,related:2};
  return [...unique.values()].sort((a,b)=>Number(b.installed)-Number(a.installed)||order[a.compatibility]-order[b.compatibility]||a.label.localeCompare(b.label)||a.precision.localeCompare(b.precision));
}
function $hubChosenAdapterPath(entry) {
  if(entry?.status!=='installed'||!Array.isArray(entry.installReceipt?.files))return null;
  return entry.installReceipt.files.find(file=>file.role==='model'&&typeof file.localPath==='string')?.localPath||null;
}
function $HubCard({group,disabled,onSelect,onNotice,onChanged,onNativeRemove,selectedProfile}) {
  const state=$useHub(),shared=$useInstallState(),h=p.createElement,[choice,setChoice]=p.useState(()=>group.variants.find(v=>v.hubEntry?.profileId===selectedProfile)?.id||group.variants.find(v=>v.installed)?.id||group.variants[0].id),[busy,setBusy]=p.useState(false),[error,setError]=p.useState(''),[removing,setRemoving]=p.useState(false),[ocrOpen,setOCROpen]=p.useState(false),[baseChoice,setBaseChoice]=p.useState(''),[adapterPrompt,setAdapterPrompt]=p.useState(''),[adapterStrength,setAdapterStrength]=p.useState(0.8),[pairedBaseId,setPairedBaseId]=p.useState(()=>{try{return JSON.parse(window.localStorage.getItem('opencore-lora-base-pairings-v1')||'{}')[group.variants[0].id]||'';}catch{return '';}});
  const model=group.variants.find(v=>v.id===choice)||group.variants[0],entry=state.catalog.find(v=>v.id===model.id)||model.hubEntry;
  const active=shared.active?.modelId===entry.id,locked=disabled||!!shared.active||busy;
  const adapter=entry.packageKind==='adapter'||entry.format==='lora';
  const bases=adapter?$hubBaseModels(entry):[];
  const selectedBase=bases.find(item=>item.id===baseChoice)||bases.find(item=>item.id===pairedBaseId)||bases[0]||null;
  p.useEffect(()=>{try{setPairedBaseId(JSON.parse(window.localStorage.getItem('opencore-lora-base-pairings-v1')||'{}')[entry.id]||'');}catch{setPairedBaseId('');}},[entry.id]);
  const pair=bases.find(item=>item.id===pairedBaseId&&item.installed);
  const connected=!!pair&&selectedBase?.id===pair.id;
  function connectBase() {
    if(!selectedBase?.installed)return;
    try {
      const key='opencore-lora-base-pairings-v1';
      const current=JSON.parse(window.localStorage.getItem(key)||'{}');
      current[entry.id]=selectedBase.id;
      window.localStorage.setItem(key,JSON.stringify(current));
      setPairedBaseId(selectedBase.id);
      onNotice?.('Connected this LoRA to the existing '+selectedBase.label+' checkpoint. No base model was downloaded.');
    }catch(e){setError('The model pairing could not be saved: '+e);}
  }
  function disconnectBase() {
    try {
      const key='opencore-lora-base-pairings-v1';
      const current=JSON.parse(window.localStorage.getItem(key)||'{}');
      delete current[entry.id];
      window.localStorage.setItem(key,JSON.stringify(current));
      setPairedBaseId('');
    }catch(e){setError('The model pairing could not be cleared: '+e);}
  }
  const readyMoss=adapter&&entry.repository==='axiomofmind/Moss-image-nsfw-uncensored-lora'&&entry.variantKey?.toLowerCase().includes('comfyui')&&selectedBase?.backend==='comfyui'&&selectedBase.installed&&connected&&entry.status==='installed';
  const readyQwen=adapter&&entry.repository==='f23gg/NSFW-LORA-Qwen-Image-2.1'&&entry.variantKey==='lora:NSFW Qwen Lora.safetensors'&&selectedBase?.installed&&connected&&entry.status==='installed'&&((selectedBase.id==='qwen-image-21'&&selectedBase.backend==='diffusers')||(selectedBase.id.startsWith('qwen-image-21-uc')&&selectedBase.backend==='comfyui'));
  const experimentalQwen=readyQwen&&selectedBase.compatibility==='related';
  const canGenerate=readyMoss||readyQwen;
  async function installBase(){if(!selectedBase?.installable)return;setBusy(true);setError('');try{await Ue('install_model',{id:selectedBase.id});await onChanged?.();}catch(e){setError(String(e));}finally{setBusy(false);}}
  async function prepareBase(){if(!selectedBase?.native||!['comfyui','diffusers'].includes(selectedBase.backend))return;setBusy(true);setError('');try{
    await window.__TAURI_INTERNALS__.invoke('runtime_setup_start',{targetId:selectedBase.id,options:{installWeights:!selectedBase.installed,acceptLicenses:false,allowAdministrator:false}});
    onNotice?.('The selected base runtime setup was queued. Its progress appears in Runtime setup.');
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function generateWithBase(){if(!canGenerate||!adapterPrompt.trim())return;setBusy(true);setError('');try{
    const path=$hubChosenAdapterPath(entry);if(!path)throw Error('Install and verify the supported LoRA file before generating.');
    const status=await window.__TAURI_INTERNALS__.invoke('runtime_setup_status',{});
    const receipt=status.receipts?.find(value=>value.targetId===selectedBase.id&&value.dependenciesVerified);
    if(!receipt?.python||(selectedBase.backend==='comfyui'&&(!receipt.runner||!receipt.sourceDir)))
      throw Error('Prepare the selected image base runtime first. A verified interpreter and supported engine are required.');
    const runtime={modelId:selectedBase.id,python:receipt.python,...(receipt.runner?{runner:receipt.runner}:{}),...(receipt.sourceDir?{sourceDir:receipt.sourceDir}:{})};
    await window.__TAURI_INTERNALS__.invoke('configure_studio_runtime',{runtime});
    await window.__TAURI_INTERNALS__.invoke('submit_studio_job',{request:{modelId:selectedBase.id,prompt:readyMoss&&!/\bmoss_xvstyle\b/i.test(adapterPrompt)?'moss_xvstyle, '+adapterPrompt.trim():adapterPrompt.trim(),settings:{loraPath:path,loraStrength:adapterStrength,width:512,height:512,steps:30,guidanceScale:3,numImages:1,outputFormat:'png'}}});
    window.dispatchEvent(new CustomEvent('opencore-open-studio',{detail:'image'}));
    onNotice?.('Generation queued with the selected image base quantization and verified LoRA.');
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  function findBase(repository){const preferred=repository||(entry.repository?.startsWith('Muapi/ltx-2-2.3-')?'Lightricks/LTX-2.3':entry.baseRepository);if(!preferred)return;window.dispatchEvent(new CustomEvent('opencore-hub-find-base',{detail:{repository:preferred}}));}

  async function start(){setError('');try{await Ue('install_model',{id:entry.id});}catch(e){setError(String(e));}}
  async function prepare(){setError('');try{await Ue(entry.nativeProfile?'install_model':'hub_prepare_model',{id:entry.id});}catch(e){setError(String(e));}}
  async function use(){setBusy(true);setError('');try{await window.__TAURI_INTERNALS__.invoke('stop_runtime',{});const profile=entry.nativeProfile?entry.id:await $hubRegister(entry);await onSelect(profile);await onChanged?.();}catch(e){setError(String(e));}finally{setBusy(false);}}
  function askRemove(){setError('');if(entry.nativeProfile){onNativeRemove?.(model);return;}setRemoving(true);}
  async function uninstall(){setBusy(true);try{await $hubRequest('/uninstall',{id:entry.id});await $hubRefresh(true);await onChanged?.();setRemoving(false);}catch(e){setError(String(e));}finally{setBusy(false);}}
  return h('article',{'aria-label':entry.familyLabel||entry.repository,className:'model-library-card hub-model-card'},h('header',null,h('h3',null,entry.familyLabel||entry.repository.split('/').at(-1)),h('span',{className:'model-install-state'},active?shared.progress?.phase==='preparing'?'Preparing runtime…':'Installing…':entry.runtimeReady||entry.specialistReady?'Ready to use':entry.status==='installed'?'Downloaded':'Not installed')),
    h('p',null,entry.repository),entry.builtInDescription?h('p',null,entry.builtInDescription):null,h('p',null,entry.nativeProfile?'OpenCore profile':entry.specialist==='ocr'?'OCR · Image to text':entry.runtime?.adapter?'ECHO · Text model':entry.category+' model'),h('label',{className:'model-quantization-picker'},'Quantization',h('select',{'aria-label':'Quantization for '+entry.repository,value:entry.id,disabled:locked,onChange:e=>setChoice(e.target.value)},
      ...group.variants.map(v=>h('option',{key:v.id,value:v.id},v.precision,' · ',((v.totalBytes||v.hubEntry?.bytes||0)/1e9).toFixed(3),' GB · ',v.hubEntry?.nativeProfile?(v.installed?'Installed':'Native')+' · '+(v.memoryMode||'native').toUpperCase():v.hubEntry?.repository.split('/')[0])))),
    h('dl',null,h('div',null,h('dt',null,'Download'),h('dd',null,(entry.bytes/1e9).toFixed(3)+' GB')),h('div',null,h('dt',null,'Runtime'),h('dd',null,entry.specialistReady?'Local OCR verified':entry.specialist==='ocr'?'OCR runtime prepares after download':entry.nativeProfile&&entry.runtimeReady?'Installed native runtime':entry.runtimeReady?'Generation verified · ECHO connected':entry.runtime?.adapter?'Prepared automatically after download':'Compatible adapter required'))),
    adapter?h('section',{className:'hub-base-picker','aria-label':'Compatible base model'},
      h('strong',null,'Base model and quantization'),
      h('p',null,entry.baseRepository?'Publisher base: '+entry.baseRepository:'The publisher did not declare a base model. Automatic compatibility cannot be verified.'),
      bases.length?h('label',null,'Select base model or quantization already in your library',h('select',{'aria-label':'Base model for '+entry.repository,value:selectedBase?.id||'',disabled:locked,onChange:e=>setBaseChoice(e.target.value)},
        ...[true,false].map(installed=>h('optgroup',{key:installed?'installed':'available',label:installed?'Installed on this PC':'Available quantizations (not downloaded)'},
          ...bases.filter(base=>base.installed===installed).map(base=>h('option',{key:base.id,value:base.id},
            base.label,' · ',base.precision,' · ',(base.bytes/1e9).toFixed(2),' GB',
            base.compatibility==='related'?' · related version (unverified)':'',
            !base.runtimeSupported?' · runtime not verified':'')))))):null,
      selectedBase?h('p',null,selectedBase.installed?'Base weights already downloaded. You can connect this LoRA without downloading them again.':'Base weights not yet installed.'):null,
      selectedBase?.compatibility==='related'?h('small',{role:'status'},'This is a related checkpoint rather than the exact publisher training base. Tensor compatibility and generation have not been verified; the LoRA may have no effect.'):null,
      entry.repository?.startsWith('Muapi/ltx-2-2.3-')?h('small',{role:'status'},'LTX 2.3-compatible checkpoints are preferred. LTX 2.5 can sometimes reuse LTX 2.3 LoRAs, but this specific adapter is untested. The OpenCore LTX generation runtime is not installed or verified.'):null,
      pair?h('p',{role:'status'},'Connected to: '+pair.label+' · '+pair.precision):null,
      h('div',{className:'hub-base-actions'},
        selectedBase&&!selectedBase.installed?h('button',{type:'button',disabled:locked||!selectedBase.installable,onClick:()=>void installBase()},'Install selected base'):null,
        selectedBase?.installed&&!connected?h('button',{type:'button',disabled:locked,onClick:connectBase},'Connect to installed model'):null,
        connected?h('button',{type:'button',disabled:locked,onClick:disconnectBase},'Disconnect LoRA'):null,
        selectedBase?.native&&['comfyui','diffusers'].includes(selectedBase.backend)?h('button',{type:'button',disabled:locked||!selectedBase.installed,onClick:()=>void prepareBase()},'Prepare base runtime'):null,
        entry.baseRepository?h('button',{type:'button',disabled:locked,onClick:()=>findBase()},bases.length?'Browse other base variants':'Find base checkpoint'):null,
        entry.repository?.startsWith('Muapi/ltx-2-2.3-')?h('button',{type:'button',disabled:locked,onClick:()=>findBase('Lightricks/LTX-2.5')},'Inspect LTX 2.5 (experimental)'):null,
        null),
      canGenerate?h('div',{className:'hub-adapter-generate'},
        experimentalQwen?h('small',{role:'status'},'Experimental pairing with a Qwen uncensored fine-tune. LoRA tensor names may not match; a real generated image is needed to verify this combination.'):null,
        h('label',null,'Image prompt',h('textarea',{'aria-label':'Image prompt for selected LoRA',value:adapterPrompt,rows:3,placeholder:'Describe the image to generate',onChange:e=>setAdapterPrompt(e.target.value)})),
        h('label',null,'LoRA strength',h('input',{type:'number','aria-label':'Selected LoRA strength',min:0,max:2,step:0.1,value:adapterStrength,onChange:e=>{const n=Number(e.target.value);if(Number.isFinite(n)&&n>=0&&n<=2)setAdapterStrength(n);}})),
        h('button',{type:'button',disabled:locked||!adapterPrompt.trim(),onClick:()=>void generateWithBase()},busy?'Starting…':'Generate with this LoRA and base')):null,
      !canGenerate?h('small',null,connected
        ? 'Connected to the selected checkpoint. Inference remains unavailable until OpenCore verifies a compatible LoRA loading runtime for this specific model and adapter.'
        : selectedBase?.installed?'Connect this LoRA to the downloaded checkpoint. Pairing and runnable inference are separate checks.'
        :selectedBase?'Install only the chosen quantization, or choose another model already on this PC.':'No verified base-model lineage is available for this adapter.'):null):null,
    h('small',null,entry.specialist==='ocr'?'Local image OCR needs a CUDA-capable NVIDIA GPU with BF16 and enough free GPU memory.':entry.runtime?.reason||'The publisher’s package is pinned and verified. Local execution depends on architecture support and available memory.'),
    h('footer',null,entry.status!=='installed'?h('button',{disabled:locked||!entry.installable,onClick:start},active?'Installing…':'Install'):
      entry.specialistReady?h('button',{disabled:locked,onClick:()=>setOCROpen(v=>!v)},ocrOpen?'Close OCR':'Use OCR'):
      entry.runtimeReady?h('button',{disabled:locked,onClick:use},busy?'Connecting…':selectedProfile===entry.profileId?'Selected':'Use model'):
      h('button',{disabled:locked||!(entry.runtime?.adapter||entry.specialist==='ocr'),onClick:prepare},active?'Preparing…':entry.specialist==='ocr'?'Prepare OCR':'Prepare runtime'),
      entry.status==='installed'?h('button',{className:'model-delete-button',disabled:locked,onClick:askRemove},'Uninstall'):null),
    ocrOpen&&entry.specialistReady?h($HubOCR,{entry}):null,
    removing?h('section',{role:'dialog','aria-label':'Uninstall '+entry.name},h('p',null,'Remove this downloaded package? Conversation history is kept.'),h('button',{disabled:busy,onClick:uninstall},'Remove downloaded files'),h('button',{disabled:busy,onClick:()=>setRemoving(false)},'Cancel')):null,
    active?h('p',{role:'status'},shared.progress?.currentFile||'Starting installation…'):null,error?h('p',{role:'alert'},error):null,
    entry.downloadProgress?.status==='failed'?h('p',{role:'alert'},entry.downloadProgress.error):null);
}
const $hubStyle=document.createElement('style');$hubStyle.textContent=`.hub-browser{padding:16px;border:1px solid var(--border,#34404c);border-radius:10px;margin:16px 0}.hub-browser h3{margin:0 0 8px}.hub-browser p{font-size:12px}.hub-repository-form{display:flex;gap:10px;flex-wrap:wrap}.hub-repository-form input{flex:1;min-width:260px}.hub-repository-form input,.hub-search-filters input,.hub-search-filters select{color:var(--theme-text,#dbe5f0);background:var(--theme-input-bg,#132435);border:1px solid var(--theme-border,#527397);border-radius:6px;padding:10px;font:inherit;min-height:42px;box-sizing:border-box}.hub-repository-form input:focus,.hub-search-filters input:focus,.hub-search-filters select:focus{outline:2px solid var(--theme-accent,#6dacf5);outline-offset:2px}.hub-repository-form input::placeholder,.hub-search-filters input::placeholder{color:var(--theme-muted,#aab7c6)}.hub-discovery-tabs{display:flex;gap:8px;overflow-x:auto;padding:14px 0 4px}.hub-discovery-tabs [role=tab]{white-space:nowrap;min-height:40px;padding:8px 14px;border:1px solid var(--theme-border,#527397);border-radius:7px;background:#142233;color:#dbe5f0}.hub-discovery-tabs [aria-selected=true]{background:#235aa3;border-color:#80baff;color:#fff}.hub-discovery-tabs [role=tab]:focus-visible{outline:2px solid #80baff;outline-offset:2px}.hub-search-filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:14px 0}.hub-search-filters label{display:grid;gap:6px;font-size:12px}.hub-search-filters input,.hub-search-filters select{width:100%;min-width:0}.hub-private-search{display:flex;align-items:center;gap:8px;margin:12px 0}.hub-browser summary{cursor:pointer;color:var(--theme-text,#dbe5f0);padding:8px 0}.hub-search-results{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));align-content:start;gap:8px;max-height:350px;overflow:auto;margin:12px 0}.hub-search-results>p{grid-column:1/-1}.hub-search-results button{display:grid;align-content:start;justify-content:start;justify-items:start;gap:6px;width:100%;min-width:0;text-align:left;padding:12px;min-height:60px;overflow-wrap:anywhere}@media(max-width:980px){.hub-search-results{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:640px){.hub-search-results{grid-template-columns:1fr}}.hub-search-results span{color:var(--theme-muted,#aab7c6);font-size:12px}.hub-plan{margin-top:14px}.hub-variant-list{display:grid;gap:9px;max-height:240px;overflow:auto;margin:12px 0}.hub-variant-list label{display:flex;gap:10px}.hub-model-card header{display:flex;gap:12px;justify-content:space-between}.hub-model-card label{display:grid;gap:8px}.hub-model-card footer{display:flex;gap:8px}`;document.head.appendChild($hubStyle);
$hubStyle.textContent+=`.hub-adapter-generate{display:grid;gap:8px}.hub-adapter-generate textarea,.hub-adapter-generate input{width:100%;min-width:0;box-sizing:border-box}.hub-base-picker{display:grid;gap:8px;padding:12px;margin:12px 0;border:1px solid var(--theme-border,#527397);border-radius:8px}.hub-base-actions{display:flex;gap:8px;flex-wrap:wrap}.hub-base-picker p,.hub-base-picker small{overflow-wrap:anywhere}.hub-base-picker select{width:100%;min-width:0}.hub-pagination{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}.hub-pagination button{min-height:40px}@media(max-width:480px){.hub-pagination{display:grid;grid-template-columns:auto 1fr auto;gap:6px}.hub-pagination button{font-size:12px;padding:6px}.hub-pagination span{text-align:center;font-size:12px}}.hub-ocr{display:grid;gap:10px;margin-top:14px;min-width:0}.hub-ocr input,.hub-ocr textarea{max-width:100%;min-width:0;font:inherit}.hub-ocr textarea{box-sizing:border-box;width:100%;color:var(--theme-text,#dbe5f0);background:var(--theme-input-bg,#132435);border:1px solid var(--theme-border,#527397);border-radius:6px;padding:10px}.hub-ocr button{min-height:42px;justify-self:start}`;
