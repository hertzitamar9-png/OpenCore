import React from 'react';

let chatImageSelection=null;
try{const value=JSON.parse(window.localStorage.getItem('opencore.ui.v1.chat-image-model'));if(typeof value?.id==='string'&&typeof value?.label==='string')chatImageSelection={id:value.id,label:value.label};}catch{}
const chatImageListeners=new Set();
export function getChatImageSelection(){return chatImageSelection;}
function subscribeChatImageSelection(listener){chatImageListeners.add(listener);return()=>chatImageListeners.delete(listener);}
function rememberChatImageModel(model){
  const next=model?{id:model.id,label:model.label||model.id}:null;
  if(next?.id===chatImageSelection?.id&&next?.label===chatImageSelection?.label)return;
  chatImageSelection=next;for(const listener of chatImageListeners)listener();
  try{if(next)window.localStorage.setItem('opencore.ui.v1.chat-image-model',JSON.stringify(next));else window.localStorage.removeItem('opencore.ui.v1.chat-image-model');}catch{}
}
export function useChatImageSelection(){return React.useSyncExternalStore(subscribeChatImageSelection,getChatImageSelection,getChatImageSelection);}
export function selectChatImageModel(model,{scope=null,primary=true}={}){
  if(!model?.id)throw Error('Choose a downloaded image model.');
  const event=new CustomEvent('opencore-chat-image-selected',{cancelable:true,detail:{modelId:model.id,scope,primary}});
  if(!window.dispatchEvent(event))return false;
  if(primary)rememberChatImageModel(model);
  return true;
}
export function clearChatImageSelection({scope=null,primary=true}={}){
  if(!window.dispatchEvent(new CustomEvent('opencore-chat-text-selected',{cancelable:true,detail:{scope,primary}})))return false;
  if(primary)rememberChatImageModel(null);
  return true;
}
export function downloadedImageModels(models){
  return models.filter(model=>model.category==='image'&&model.hubEntry?.packageKind!=='adapter'&&model.hubEntry?.format!=='lora'&&(model.installed||model.runtimeConnected));
}
async function readImageModelLibrary(){
  const run=()=>window.__TAURI_INTERNALS__.invoke('list_model_library',{});
  const invoke=()=>globalThis.__opencoreInstallBridge?globalThis.__opencoreInstallBridge.invoke('list_model_library',{},run):run();
  return globalThis.__opencoreModelLibrary?.read?globalThis.__opencoreModelLibrary.read(invoke):invoke();
}
export function ChatImageModelOptions({onSelect,disabled=false,loadModels=readImageModelLibrary}){
  const h=React.createElement,[models,setModels]=React.useState(null),[error,setError]=React.useState(''),selected=useChatImageSelection();
  React.useEffect(()=>{let active=true;Promise.resolve().then(()=>loadModels()).then(library=>{if(active)setModels(downloadedImageModels(library.models));}).catch(reason=>{if(active)setError(String(reason));});return()=>{active=false;};},[loadModels]);
  return h('div',{className:'chat-image-model-options',role:'group','aria-label':'Downloaded image models'},h('strong',{className:'chat-image-model-heading'},'Image models'),
    error?h('p',{role:'alert'},'Could not check downloaded image models. '+error):models===null?h('p',{role:'status'},'Checking image models…'):models.length===0?h('p',{role:'status'},'No image models are downloaded. Install one in Models.'):null,
    ...(models||[]).map(model=>h('button',{key:model.id,type:'button',className:'model-picker-option'+(selected?.id===model.id?' selected':''),'aria-pressed':selected?.id===model.id,disabled,onClick:event=>onSelect(model,event)},h('span',null,h('strong',null,model.label),h('small',null,(model.precision||'Publisher checkpoint')+' · Generate images in this conversation')))));
}

export const IMAGE_SIZE_OPTIONS = [256,512,768,1024,2048,4096,8192].map(value=>({value,label:(value>=2048?(value/1024)+'K · ':'')+value+' × '+value}));

export function imageSizeSettings(size,modelId='') {
  if(!IMAGE_SIZE_OPTIONS.some(option=>option.value===size))throw Error('Choose an available image output size.');
  const native=modelId.startsWith('qwen-image-21')?2048:1024;
  return {width:Math.min(size,native),height:Math.min(size,native),outputWidth:size,outputHeight:size};
}

export function imageAdaptersForModel(model, adapters) {
  const id=model?.id||'';
  let base=id.startsWith('moss-')?'OpenMOSS-Team/MOSS-Image-SFT':id.startsWith('qwen-image-21')?'Qwen/Qwen-Image-2.1':model?.hubEntry?.repository;
  if(!base&&model?.sourceUrl){try{base=new URL(model.sourceUrl).pathname.split('/').filter(Boolean).slice(0,2).join('/');}catch{}}
  if(!base)return [];
  const legacy={'axiomofmind/Moss-image-nsfw-uncensored-lora':'OpenMOSS-Team/MOSS-Image-SFT','f23gg/NSFW-LORA-Qwen-Image-2.1':'Qwen/Qwen-Image-2.1'};
  return adapters.filter(a=>{
    const bases=[a.baseRepository,...(a.baseRepositories||[]),legacy[a.repository]].filter(Boolean);
    const matched=bases.some(value=>value.toLowerCase()===base.toLowerCase());
    const quantizedMoss=id.startsWith('moss-')&&(model.backend==='comfyui'||id.includes('convrot'));
    const format=!(quantizedMoss)||/comfy/i.test(a.variantKey||'');
    return matched&&format&&a.status==='installed'&&(a.packageKind==='adapter'||a.format==='lora')&&a.installReceipt?.files?.some(f=>f.role==='model'&&f.localPath);
  });
}

export function imageGenerationDefaults(id) {
  if(id.startsWith('moss-')||id==='kromoss')return {steps:50,guidance:3};
  if(id.startsWith('qwen-image-21'))return {steps:40,guidance:1};
  if(id==='z-image-turbo')return {steps:9,guidance:0};
  if(id==='flux-2-klein-4b')return {steps:4,guidance:1};
  if(id==='flux-2-klein-base-4b')return {steps:30,guidance:4};
  if(id==='animation-diffusion-2d')return {steps:30,guidance:7.5};
  return {steps:30,guidance:id.startsWith('moss-')?3:id.startsWith('qwen-image-21')?4:4.5};
}

export function automaticImageSettings(id,prompt,files=[]) {
  if(files.length>4||files.some(path=>typeof path!=='string'||!/^([A-Za-z]:[\\/]|\/)/.test(path)||! /\.(png|jpe?g|webp)$/i.test(path)))throw Error('Attach up to four PNG, JPEG or WebP reference images.');
  const defaults=imageGenerationDefaults(id),portrait=/\b(portrait|vertical|phone wallpaper|9:16)\b/i.test(prompt),wide=/\b(landscape|wide|panorama|wallpaper|16:9)\b/i.test(prompt),scale=id.startsWith('qwen-image-21')?2:1;
  return {width:(portrait?576:1024)*scale,height:(portrait?1024:wide?576:1024)*scale,steps:defaults.steps,guidanceScale:defaults.guidance,numImages:1,outputFormat:'png',seed:crypto.getRandomValues(new Uint32Array(1))[0],...(files.length?{referencePaths:[...files]}:{})};
}

export function submitChatImagePrompt(detail) {
  return !window.dispatchEvent(new CustomEvent('opencore-chat-image-prompt',{cancelable:true,detail}));
}
export function stopChatImagePrompt(scope) {
  return !window.dispatchEvent(new CustomEvent('opencore-chat-image-stop',{cancelable:true,detail:{scope}}));
}

export function imageOutputs(job) {
  const images=(job.outputs||[]).filter(path=>/\.(png|jpe?g|webp)$/i.test(path));
  const published=images.filter(path=>!/[\\/](comfy-output|comfy-inputs|reference-inputs)[\\/]/i.test(path));
  return published.length?published:images;
}

export function ImageGenerationPanel({conversationId,onConversationId,onNotice,onRefresh,onStateChange,studio=false,embedded=false,initialModelId=''}) {
  const h=React.createElement;
  const initialSelection=!studio&&!embedded?getChatImageSelection():null;
  const [open,setOpen]=React.useState(studio||!!initialSelection),[models,setModels]=React.useState([]),[modelId,setModelId]=React.useState(initialModelId||initialSelection?.id||''),[adapters,setAdapters]=React.useState([]),[adapterId,setAdapterId]=React.useState(''),[prompt,setPrompt]=React.useState(''),[strength,setStrength]=React.useState(0.8),[steps,setSteps]=React.useState(30),[guidance,setGuidance]=React.useState(3),[size,setSize]=React.useState(512),[busy,setBusy]=React.useState(false),[stage,setStage]=React.useState(''),[error,setError]=React.useState(''),[jobs,setJobs]=React.useState([]),[images,setImages]=React.useState({});
  const panel=React.useRef(null),requestedModel=React.useRef(initialSelection?.id||''),pending=React.useRef(null),inFlight=React.useRef(false),previewing=React.useRef(new Set()),conversation=React.useRef(conversationId),submitting=React.useRef(false),fingerprint=React.useRef('');
  const activeJob=jobs.find(job=>['queued','starting','running','preparing'].includes(job.status));
  React.useEffect(()=>{onStateChange?.({active:open,busy:busy||!!activeJob,label:models.find(m=>m.id===modelId)?.label});},[open,busy,!!activeJob,modelId,models,onStateChange]);
  React.useEffect(()=>{conversation.current=conversationId;},[conversationId]);
  React.useEffect(()=>{
    if(studio)return;
    function accept(event){
      const detail=event.detail||{};
      if(detail.scope?!detail.scope.contains(panel.current):embedded)return;
      if(busy||activeJob||submitting.current){event.preventDefault();onNotice?.('Wait for image generation to finish before changing models.');return;}
      if(event.type==='opencore-chat-text-selected'){setOpen(false);return;}
      requestedModel.current=detail.modelId;setModelId(detail.modelId);setOpen(true);setError('');
    }
    window.addEventListener('opencore-chat-image-selected',accept);window.addEventListener('opencore-chat-text-selected',accept);
    return()=>{window.removeEventListener('opencore-chat-image-selected',accept);window.removeEventListener('opencore-chat-text-selected',accept);};
  },[studio,embedded,busy,!!activeJob,onNotice]);
  React.useEffect(()=>{
    if(studio||!open)return;
    const matches=event=>event.detail?.scope?event.detail.scope.contains(panel.current):!embedded;
    const send=event=>{if(!matches(event))return;event.preventDefault();if(!submitting.current&&!busy&&!activeJob)void generate(event.detail);};
    const stop=event=>{if(!matches(event))return;event.preventDefault();void (async()=>{try{if(pending.current){await invoke('runtime_setup_pause',{id:pending.current.setupId});pending.current=null;submitting.current=false;setBusy(false);setStage('');}else if(activeJob){await (activeJob.source==='hub-image'?globalThis.__opencoreImageHub.cancel(activeJob.id):invoke('cancel_studio_job',{id:activeJob.id}));}}catch(error){setError(String(error));}})();};
    window.addEventListener('opencore-chat-image-prompt',send);window.addEventListener('opencore-chat-image-stop',stop);
    return()=>{window.removeEventListener('opencore-chat-image-prompt',send);window.removeEventListener('opencore-chat-image-stop',stop);};
  });
  React.useEffect(()=>{const defaults=imageGenerationDefaults(modelId);setSteps(defaults.steps);setGuidance(defaults.guidance);setSize(modelId.startsWith('qwen-image-21')?2048:1024);setAdapterId('');},[modelId]);
  const model=models.find(m=>m.id===modelId),choices=imageAdaptersForModel(model,adapters);
  React.useEffect(()=>{if(open&&!studio&&!embedded&&model)rememberChatImageModel(model);},[open,studio,embedded,model]);
  React.useEffect(()=>{const draft=globalThis.__opencoreImageDraft;if(draft?.modelId===modelId&&choices.some(a=>a.id===draft.adapterId)){setAdapterId(draft.adapterId);setPrompt(draft.prompt||'');setStrength(draft.strength??0.8);delete globalThis.__opencoreImageDraft;}},[modelId,adapters]);
  async function invoke(command,args={}) {
    const run=()=>window.__TAURI_INTERNALS__.invoke(command,args);
    return globalThis.__opencoreInstallBridge?globalThis.__opencoreInstallBridge.invoke(command,args,run):run();
  }
  async function refresh() {
    const lib=await (globalThis.__opencoreModelLibrary?.read?globalThis.__opencoreModelLibrary.read(()=>invoke('list_model_library')):invoke('list_model_library'));
    const available=downloadedImageModels(lib.models);setModels(available);
    const requested=requestedModel.current||globalThis.__opencoreRequestedImageModel;
    setModelId(id=>available.some(m=>m.id===requested)?requested:available.some(m=>m.id===id)?id:available[0]?.id||'');
    if(requested&&available.some(m=>m.id===requested)){requestedModel.current='';delete globalThis.__opencoreRequestedImageModel;}
    const load=globalThis.__opencoreImageAdapters; if(load)setAdapters(await load());
    const all=await invoke('list_studio_jobs'),hub=globalThis.__opencoreImageHub;
    const imported=hub?await hub.list():[];
    const next=[...all,...imported].filter(j=>j.category==='image'&&!j.internalVerification&&!j.hidden&&(studio?j.source==='hub-image':j.request.conversationId===conversation.current)).slice().sort((a,b)=>b.createdAt.localeCompare(a.createdAt)).slice(0,6);
    setJobs(next);const signature=JSON.stringify(next.map(j=>[j.id,j.status,j.stage,j.outputs]));if(!studio&&signature!==fingerprint.current){fingerprint.current=signature;onRefresh?.();}
  }
  React.useEffect(()=>{
    if(!open)return;let alive=true;
    async function tick(){if(inFlight.current)return;inFlight.current=true;try{
      if(alive)await refresh();
      if(pending.current){const status=await invoke('runtime_setup_status'),request=pending.current;
        const receipt=status.receipts.find(r=>r.targetId===request.modelId&&r.dependenciesVerified&&r.inferenceVerified);
        const job=status.jobs.find(j=>j.id===request.setupId);
        if(receipt){pending.current=null;await submitReady(request,receipt);}
        else if(job&&['failed','cancelled','interrupted'].includes(job.status)){pending.current=null;submitting.current=false;setBusy(false);setStage('');setError(job.error||'Model preparation stopped. Your prompt is ready to retry.');}
        else if(job)setStage(job.detail||job.stage||'Preparing image model…');
      }
    }catch(e){if(alive){setError(String(e));if(!pending.current)setBusy(false);}}finally{inFlight.current=false;}}
    void tick();const timer=setInterval(()=>void tick(),2000);return()=>{alive=false;clearInterval(timer);};
  },[open,conversationId,studio]);
  React.useEffect(()=>{
    if(!studio)return;
    for(const job of jobs)for(const path of imageOutputs(job)){const key=job.id+':'+path;if(!/\.(png|jpe?g|webp)$/i.test(path)||images[key]||previewing.current.has(key))continue;previewing.current.add(key);
      const preview=job.source==='hub-image'?globalThis.__opencoreImageHub.preview(job.id,path):invoke('studio_output_preview',{id:job.id,path});
      void preview.then(value=>setImages(old=>({...old,[key]:value.dataUrl}))).catch(()=>previewing.current.delete(key));
    }
  },[jobs]);
  async function submitReady(request,receipt){
    await invoke('configure_studio_runtime',{runtime:{modelId:request.modelId,python:receipt.python,runner:receipt.runner||null,sourceDir:receipt.sourceDir||null}});
    const id=request.conversationId||(studio?null:'opencore:'+crypto.randomUUID());
    const job=await invoke('submit_studio_job',{request:{modelId:request.modelId,prompt:request.prompt,settings:request.settings,conversationId:id}});
    if(id){conversation.current=id;onConversationId?.(id);}
    request.onAccepted?.();setPrompt('');submitting.current=false;setBusy(false);setStage('');onRefresh?.();
    if(!studio)setJobs(old=>[job,...old].slice(0,6));
  }
  async function generate(input){
    const text=studio?prompt:input?.prompt||'';
    if(submitting.current||busy||!text.trim())return;
    if(!model){setError('The selected image model is still loading. Send your prompt again in a moment.');return;}
    submitting.current=true;setBusy(true);setError('');
    try{
      const adapter=choices.find(a=>a.id===adapterId),file=adapter?.installReceipt.files.find(f=>f.role==='model'&&f.localPath);
      const request={modelId,prompt:adapter?.repository==='axiomofmind/Moss-image-nsfw-uncensored-lora'&&file&&!/\bmoss_xvstyle\b/i.test(text)?'moss_xvstyle, '+text.trim():text.trim(),conversationId:conversation.current,settings:{...(studio?{...imageSizeSettings(size,modelId),steps,guidanceScale:guidance,numImages:1,outputFormat:'png'}:automaticImageSettings(modelId,text,input?.files)),...(file?{loraPath:file.localPath,loraStrength:strength}:{})},onAccepted:input?.onAccepted};
      if(globalThis.__opencorePromptSettings?.().enabled&&globalThis.__opencorePromptSettings().automaticImages){
        try{const better=await globalThis.__opencoreImproveLocalPrompt(request.prompt,conversation.current,'image',setStage);request.settings.originalPrompt=text.trim();request.settings.promptRecap=better.recap;request.prompt=better.prompt;}
        catch(error){setError('Prompt assistant: '+String(error)+' Your original prompt will be used.');}
      }
      if(studio&&request.settings.outputWidth>request.settings.width){setStage('Preparing the AI detail upscaler…');const upscaler=await globalThis.__opencoreImageHub.prepareUpscaler();request.settings.upscaleModelPath=upscaler.path;}
      if(model.hubEntry?.format==='diffusers'&&!model.hubEntry?.nativeProfile){
        const hub=globalThis.__opencoreImageHub;if(!hub)throw Error('Update OpenCore to enable imported image generation.');
        try{window.window.localStorage.setItem('opencore.image-chat-history','1');}catch{}
        request.conversationId=request.conversationId||(studio?null:'opencore:'+crypto.randomUUID());
        const job=await hub.generate(request);if(request.conversationId){conversation.current=request.conversationId;onConversationId?.(request.conversationId);}
        request.onAccepted?.();submitting.current=false;setJobs(old=>[job,...old].slice(0,6));setPrompt('');setBusy(false);onRefresh?.();return;
      }
      const status=await invoke('runtime_setup_status');const receipt=status.receipts.find(r=>r.targetId===modelId&&r.dependenciesVerified&&r.inferenceVerified);
      if(receipt){await submitReady(request,receipt);return;}
      const recipe=status.recipes.find(r=>r.modelIds.includes(modelId));
      if(!recipe)throw Error('Automatic runtime setup is not available for this image architecture yet.');
      if(recipe.requiresLicenseAcceptance)throw Error('Accept this model’s publisher license in Models before preparing its runtime.');
      const job=await invoke('runtime_setup_start',{targetId:modelId,options:{installWeights:!model.installed,verifyInference:true,acceptLicenses:false,allowAdministrator:false}});
      pending.current={...request,setupId:job.id};setStage('Preparing and verifying the image model. Your prompt will run automatically afterward.');
    }catch(e){pending.current=null;submitting.current=false;setBusy(false);setStage('');setError(String(e));}
  }
  if(!studio)return h('section',{ref:panel,className:'chat-image-status','data-image-mode':open?'open':'closed','aria-label':'Chat image generation'},open?h(React.Fragment,null,h('span',null,(model?.label||'Image model')+' · Automatic quality'),choices.length?h('details',null,h('summary',null,'Image style'),h('label',null,'LoRA',h('select',{'aria-label':'Image LoRA',value:adapterId,disabled:busy||!!activeJob,onChange:e=>setAdapterId(e.target.value)},h('option',{value:''},'None'),...choices.map(a=>h('option',{key:a.id,value:a.id},a.familyLabel||a.name||a.repository))))):null,stage||activeJob?h('div',{role:'status'},h('progress',{'aria-label':'Image generation progress'}),h('span',null,stage||activeJob.stage||activeJob.status)):null,error?h('p',{role:'alert'},error):null):null);
  return h('section',{ref:panel,className:'image-generation-panel','data-image-mode':open?'open':'closed','aria-label':studio?'Image generation':'Chat image generation'},
    !studio?h('div',{className:'image-mode-tabs',role:'group','aria-label':'Chat generation mode'},h('button',{type:'button','aria-pressed':!open,disabled:busy,onClick:()=>clearChatImageSelection({scope:panel.current?.closest('.chat-composer-wrap')||panel.current,primary:!embedded})},'Text'),h('button',{type:'button','aria-pressed':open,disabled:busy,onClick:()=>setOpen(true)},'Images')):null,
    open?h(React.Fragment,null,h('h3',null,studio?'Generate an image':'Generate images in this chat'),
      models.length?h('label',null,'Downloaded image model',h('select',{'aria-label':'Downloaded image model',value:modelId,disabled:busy,onChange:e=>{setModelId(e.target.value);setAdapterId('');}},...models.map(m=>h('option',{key:m.id,value:m.id},m.label+' · '+m.precision)))):h('p',{role:'status'},'No image models are downloaded. Install an image model in Models to start.'),
      choices.length?h('div',{className:'image-panel-fields'},h('label',null,'LoRA',h('select',{'aria-label':'Image LoRA',value:choices.some(a=>a.id===adapterId)?adapterId:'',disabled:busy,onChange:e=>setAdapterId(e.target.value)},h('option',{value:''},'None'),...choices.map(a=>h('option',{key:a.id,value:a.id},a.familyLabel||a.name||a.repository)))),adapterId?h('label',null,'LoRA strength',h('input',{'aria-label':'Image LoRA strength',type:'number',min:0,max:2,step:0.1,value:strength,disabled:busy,onChange:e=>setStrength(Number(e.target.value))})):null):null,
      h('label',null,'Image prompt',h('textarea',{'aria-label':'Image generation prompt',placeholder:'Describe the image you want…',rows:3,value:prompt,disabled:busy,onChange:e=>setPrompt(e.target.value)})),
      h('div',{className:'image-panel-fields'},h('label',null,'Output size',h('select',{'aria-label':'Image size',value:size,disabled:busy,onChange:e=>setSize(Number(e.target.value))},...IMAGE_SIZE_OPTIONS.map(option=>h('option',{key:option.value,value:option.value},option.label)))),h('label',null,'Steps',h('input',{'aria-label':'Image steps',type:'number',min:1,max:100,value:steps,disabled:busy,onChange:e=>setSteps(Number(e.target.value))})),h('label',null,'Guidance',h('input',{'aria-label':'Image guidance',type:'number',min:0,max:30,step:0.5,value:guidance,disabled:busy,onChange:e=>setGuidance(Number(e.target.value))}))),
      size>imageSizeSettings(size,modelId).width?h('small',{className:'image-output-size-note'},'Output: '+size+' × '+size+'. Generated at '+imageSizeSettings(size,modelId).width+' × '+imageSizeSettings(size,modelId).height+', then enhanced locally with the Real-ESRGAN AI upscaler.'):null,
      h('button',{type:'button',className:'image-generate-button',disabled:busy||!model||!prompt.trim()||!Number.isInteger(steps)||steps<1||steps>100||!Number.isFinite(strength)||strength<0||strength>2||!Number.isFinite(guidance)||guidance<0||guidance>30,onClick:()=>void generate()},busy?'Preparing…':'Generate image'),
      stage?h('div',{role:'status'},h('progress',{'aria-label':'Image runtime preparation'}),h('span',null,stage)):null,
      error?h('p',{role:'alert'},error):null,
      ...jobs.map(job=>h('article',{className:'chat-image-job',key:job.id},h('p',null,job.request.prompt),h('small',{role:'status'},job.stage||job.status),job.error?h('p',{role:'alert'},job.error):null,...imageOutputs(job).map(path=>images[job.id+':'+path]?h('img',{key:path,src:images[job.id+':'+path],alt:job.request.prompt}):null),['queued','starting','running','preparing'].includes(job.status)?h('button',{type:'button',onClick:()=>void (job.source==='hub-image'?globalThis.__opencoreImageHub.cancel(job.id):invoke('cancel_studio_job',{id:job.id})).catch(e=>setError(String(e)))},'Cancel image generation'):null))):null);
}
