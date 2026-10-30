import React from 'react';
import {applyImageGenerationOptions,useGenerationSettings} from './generation-settings.js';

let chatImageSelection=null;
try{const value=JSON.parse(window.localStorage.getItem('opencore.ui.v1.chat-image-model'));if(typeof value?.id==='string'&&typeof value?.label==='string')chatImageSelection={id:value.id,label:value.label,...(typeof value.adapterId==='string'?{adapterId:value.adapterId,adapterLabel:value.adapterLabel||value.adapterId}:{})};}catch{}
const chatImageListeners=new Set();
export function getChatImageSelection(){return chatImageSelection;}
function subscribeChatImageSelection(listener){chatImageListeners.add(listener);return()=>chatImageListeners.delete(listener);}
function rememberChatImageModel(model){
  const next=model?{id:model.id,label:model.label||model.id,...(model.adapterId?{adapterId:model.adapterId,adapterLabel:model.adapterLabel||model.adapterId}:{})}:null;
  if(JSON.stringify(next)===JSON.stringify(chatImageSelection))return;
  chatImageSelection=next;for(const listener of chatImageListeners)listener();
  try{if(next)window.localStorage.setItem('opencore.ui.v1.chat-image-model',JSON.stringify(next));else window.localStorage.removeItem('opencore.ui.v1.chat-image-model');}catch{}
}
export function useChatImageSelection(){return React.useSyncExternalStore(subscribeChatImageSelection,getChatImageSelection,getChatImageSelection);}
export function imageSelectionLabel(model){return model?model.label+(model.adapterId?' + '+(model.adapterLabel||'LoRA'):''):'';}
export function imageErrorMessage(error){
  const raw=String(error?.message||error||'');
  if(/failed to fetch|PC did not respond|connection (lost|interrupted)/i.test(raw))return 'The connection to your PC was interrupted. Reconnect, then retry your image prompt.';
  if(/required pinned .* component is missing|installed model folder is missing/i.test(raw))return 'Base model files are missing. Reinstall the selected base in Models, then retry.';
  const memory=raw.match(/needs about ([\d.]+) GB available RAM/i);
  if(memory)return 'This model needs '+memory[1]+' GB of free RAM. Free memory or choose a smaller quantization, then retry.';
  const lines=raw.trim().split(/\r?\n/).filter(Boolean);return (lines.at(-1)||'Image generation failed. Please retry.').replace(/^(?:Error|RuntimeError|ValueError):\s*/,'').slice(0,300);
}
function ImageError({error}){const h=React.createElement,raw=String(error?.message||error||''),message=imageErrorMessage(error);return h('div',{className:'image-error',role:'alert'},h('p',null,message),raw.replace(/^Error:\s*/,'')!==message?h('details',null,h('summary',null,'Error details'),h('pre',null,raw)):null);}
let currentImageProgress=null;
const imageProgressListeners=new Set();
function publishImageProgress(value){if(JSON.stringify(value)===JSON.stringify(currentImageProgress))return;currentImageProgress=value;for(const listener of imageProgressListeners)listener();}
export function useImageProgress(){return React.useSyncExternalStore(listener=>{imageProgressListeners.add(listener);return()=>imageProgressListeners.delete(listener);},()=>currentImageProgress,()=>null);}
function imageDuration(seconds){const value=Math.max(0,Math.floor(seconds));return value<60?value+'s':Math.floor(value/60)+'m '+(value%60)+'s';}
export function imageProgressView(job,now=Date.now()){
  const value=job?.progress||{},step=Number.isInteger(value.step)&&value.step>=0?value.step:null,steps=Number.isInteger(value.steps)&&value.steps>0?value.steps:null;
  const done=job?.status==='completed'&&imageOutputs(job).length>0;
  const begin=Date.parse(job?.createdAt||''),end=['completed','failed','cancelled','interrupted'].includes(job?.status)?Date.parse(job.updatedAt||''):now;
  const elapsed=Number.isFinite(begin)&&Number.isFinite(end)?Math.max(0,(end-begin)/1000):0;
  const speed=Number.isFinite(value.stepsPerSecond)&&value.stepsPerSecond>0?value.stepsPerSecond:null;
  return {done,percent:done?100:step!==null&&steps!==null?Math.min(99,Math.round(100*step/(steps+1))):null,
    elapsedLabel:imageDuration(elapsed),stepLabel:step!==null&&steps!==null?'Step '+Math.min(step,steps)+' of '+steps:'',
    speedLabel:speed?speed.toFixed(2)+' steps/s':'',stage:done?'Image ready':job?.status==='failed'?'Image generation failed':job?.status==='cancelled'?'Image generation cancelled':job?.status==='interrupted'?'Image generation interrupted':job?.status==='completed'?'No image output was returned':job?.stage||'Preparing image generation'};
}
export function ImageSamplingStats(){
  const h=React.createElement,selected=useChatImageSelection(),progress=useImageProgress();
  if(!selected)return null;
  const view=progress?.modelId===selected.id?imageProgressView(progress.job):null;
  return h('span',{className:'statusbar-image-speed'},view?.done?'Image ready · '+view.elapsedLabel:progress?.modelId!==selected.id?'Image model ready':['failed','cancelled','interrupted','completed'].includes(progress?.job.status)?view.stage:/upscal|saving|decod/i.test(view?.stage||'')?'Finishing image':view?.speedLabel||'Preparing image');
}
function ImageProgressCard({job,stage}){
  const h=React.createElement,[now,setNow]=React.useState(Date.now());
  React.useEffect(()=>{if(!job||['completed','failed','cancelled','interrupted'].includes(job.status))return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[job?.id,job?.status]);
  const view=job?imageProgressView(job,now):null;
  const terminal=['failed','cancelled','interrupted'].includes(job?.status)||job?.status==='completed'&&!view?.done;
  return h('div',{className:'image-progress-card',role:'status'},h('div',{className:'image-progress-heading'},h('strong',null,view?.done?'Image ready':terminal?view.stage:stage||view?.stage||'Preparing image generation'),h('span',null,view?.percent!==null&&view?.percent!==undefined?view.percent+'%':'')),
    h('progress',{'aria-label':'Image generation progress',max:100,...(terminal?{value:view?.percent||0}:view?.percent!==null&&view?.percent!==undefined?{value:view.percent}:{})}),
    h('div',{className:'image-progress-metrics'},...[view?.stepLabel,view?.speedLabel,view?'Elapsed '+view.elapsedLabel:'Your prompt is running automatically'].filter(Boolean).map(text=>h('span',{key:text},text))));
}
export function selectChatImageModel(model,{scope=null,primary=true}={}){
  if(!model?.id)throw Error('Choose a downloaded image model.');
  const event=new CustomEvent('opencore-chat-image-selected',{cancelable:true,detail:{modelId:model.id,adapterId:model.adapterId||'',scope,primary}});
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
  return models.filter(model=>model.category==='image'&&model.hubEntry?.packageKind!=='adapter'&&model.hubEntry?.format!=='lora'&&model.installed===true);
}
async function readImageModelLibrary(options={}){
  const run=()=>window.__TAURI_INTERNALS__.invoke('list_model_library',{});
  const invoke=()=>globalThis.__opencoreInstallBridge?globalThis.__opencoreInstallBridge.invoke('list_model_library',{},run):run();
  return globalThis.__opencoreModelLibrary?.read?globalThis.__opencoreModelLibrary.read(invoke,options):invoke();
}
export function imageModelChoices(models,adapters=[]){
  return downloadedImageModels(models).flatMap(model=>[model,...imageAdaptersForModel(model,adapters).map(adapter=>({...model,adapterId:adapter.id,adapterLabel:adapter.familyLabel||adapter.name||adapter.repository}))]);
}
export function ChatImageModelOptions({onSelect,disabled=false,loadModels=readImageModelLibrary,selectedId,selectedAdapterId}){
  const h=React.createElement,[models,setModels]=React.useState(null),[error,setError]=React.useState(''),selected=useChatImageSelection();
  React.useEffect(()=>{let active=true;Promise.all([Promise.resolve().then(()=>loadModels()),Promise.resolve().then(()=>globalThis.__opencoreImageAdapters?.()||[]).catch(()=>[])]).then(([library,adapters])=>{if(active)setModels(imageModelChoices(library.models,adapters));}).catch(reason=>{if(active)setError(String(reason));});return()=>{active=false;};},[loadModels]);
  const pickedId=selectedId===undefined?selected?.id:selectedId,pickedAdapter=selectedAdapterId===undefined?selected?.adapterId:selectedAdapterId;
  const picked=model=>pickedId===model.id&&(pickedAdapter||'')===(model.adapterId||'');
  return h('div',{className:'chat-image-model-options',role:'group','aria-label':'Downloaded image models'},h('strong',{className:'chat-image-model-heading'},'Image models'),
    error?h('p',{role:'alert'},'Could not check downloaded image models. '+error):models===null?h('p',{role:'status'},'Checking image models…'):models.length===0?h('p',{role:'status'},'No image models are downloaded. Install one in Models.'):null,
    ...(models||[]).map(model=>h('button',{key:model.id+':'+(model.adapterId||'base'),type:'button',className:'model-picker-option'+(picked(model)?' selected':''),'aria-pressed':picked(model),disabled,onClick:event=>onSelect(model,event)},h('span',null,h('strong',null,model.label+(model.adapterId?' + '+model.adapterLabel:'')),h('small',null,(model.precision||'Publisher checkpoint')+(model.adapterId?' · LoRA enabled':'')+' · Generate images in this conversation')))));
}

export const IMAGE_SIZE_OPTIONS = [256,512,768,1024,2048,4096,8192].map(value=>({value,label:(value>=2048?(value/1024)+'K · ':'')+value+' × '+value}));

export function imageSizeSettings(size,modelId='',repository='') {
  if(!IMAGE_SIZE_OPTIONS.some(option=>option.value===size))throw Error('Choose an available image output size.');
  const native=modelId.startsWith('qwen-image-21')||repository.toLowerCase()==='qwen/qwen-image-2.1-turbo'?2048:1024;
  return {width:Math.min(size,native),height:Math.min(size,native),outputWidth:size,outputHeight:size};
}

export function imageAdaptersForModel(model, adapters) {
  const id=model?.id||'';
  let base=id.startsWith('moss-')?'OpenMOSS-Team/MOSS-Image-SFT':id.startsWith('qwen-image-21')?'Qwen/Qwen-Image-2.1':model?.hubEntry?.repository;
  if(!base&&model?.sourceUrl){try{base=new URL(model.sourceUrl).pathname.split('/').filter(Boolean).slice(0,2).join('/');}catch{}}
  if(!base)return [];
  const related={
    'qwen/qwen-image-2.1-turbo':'qwen/qwen-image-2.1',
  };
  const legacy={'axiomofmind/Moss-image-nsfw-uncensored-lora':'OpenMOSS-Team/MOSS-Image-SFT','f23gg/NSFW-LORA-Qwen-Image-2.1':'Qwen/Qwen-Image-2.1'};
  return adapters.filter(a=>{
    const bases=[a.baseRepository,...(a.baseRepositories||[]),legacy[a.repository]].filter(Boolean);
    const normalized=base.toLowerCase(),matched=bases.some(value=>value.toLowerCase()===normalized||related[normalized]===value.toLowerCase());
    const quantizedMoss=id.startsWith('moss-')&&(model.backend==='comfyui'||id.includes('convrot'));
    const format=!(quantizedMoss)||/comfy/i.test(a.variantKey||'');
    return matched&&format&&a.status==='installed'&&(a.packageKind==='adapter'||a.format==='lora')&&a.installReceipt?.files?.some(f=>f.role==='model'&&f.localPath);
  });
}

export function imageGenerationDefaults(id,repository='') {
  if(id.startsWith('moss-')||id==='kromoss')return {steps:50,guidance:3};
  if(id==='qwen-image-21-turbo'||repository.toLowerCase()==='qwen/qwen-image-2.1-turbo')return {steps:8,guidance:1};
  if(id.startsWith('qwen-image-21'))return {steps:40,guidance:1};
  if(id==='z-image-turbo')return {steps:9,guidance:0};
  if(id==='flux-2-klein-4b')return {steps:4,guidance:1};
  if(id==='flux-2-klein-base-4b')return {steps:30,guidance:4};
  if(id==='animation-diffusion-2d')return {steps:30,guidance:7.5};
  return {steps:30,guidance:id.startsWith('moss-')?3:id.startsWith('qwen-image-21')?4:4.5};
}

export function imageGenerationPreset(model,adapter){
  const preset=model?.hubEntry?.imageDefaults||imageGenerationDefaults(model?.id||'',model?.hubEntry?.repository);
  if(adapter?.repository==='axiomofmind/Moss-image-nsfw-uncensored-lora')return {...preset,steps:30,guidance:3,loraStrength:(adapter.variantKey||'').includes('original-checkpoint-2000/')?0.7:0.8};
  if(adapter?.repository==='f23gg/NSFW-LORA-Qwen-Image-2.1')return {...preset,loraStrength:1};
  return preset;
}

export function automaticImageSettings(id,prompt,files=[],preset,repository='') {
  if(files.length>4||files.some(path=>typeof path!=='string'||!/^([A-Za-z]:[\\/]|\/)/.test(path)||! /\.(png|jpe?g|webp)$/i.test(path)))throw Error('Attach up to four PNG, JPEG or WebP reference images.');
  const qwen=id.startsWith('qwen-image-21')||repository.toLowerCase()==='qwen/qwen-image-2.1-turbo';
  const defaults=preset||imageGenerationDefaults(id,repository),portrait=/\b(portrait|vertical|phone wallpaper|9:16)\b/i.test(prompt),wide=/\b(landscape|wide|panorama|wallpaper|16:9)\b/i.test(prompt),scale=qwen?2:1;
  return {width:(portrait?576:1024)*scale,height:(portrait?1024:wide?576:1024)*scale,steps:defaults.steps,guidanceScale:defaults.guidance,numImages:1,outputFormat:'png',seed:crypto.getRandomValues(new Uint32Array(1))[0],...(files.length?{referencePaths:[...files]}:{})};
}

export function submitChatImagePrompt(detail) {
  if(!window.dispatchEvent(new CustomEvent('opencore-chat-image-prompt',{cancelable:true,detail})))return true;
  // A missing image handler must never turn an image prompt into a text request.
  const image=detail.scope?.querySelector('.chat-image-status[data-image-mode="open"]');
  if(image||!detail.scope&&getChatImageSelection()){
    detail.onError?.('The image composer is still opening. Your prompt is kept; please send it again.');return true;
  }
  return false;
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
  const [open,setOpen]=React.useState(studio||!!initialSelection),[models,setModels]=React.useState([]),[modelId,setModelId]=React.useState(initialModelId||initialSelection?.id||''),[adapters,setAdapters]=React.useState([]),[adapterId,setAdapterId]=React.useState(initialSelection?.adapterId||''),[prompt,setPrompt]=React.useState(''),[strength,setStrength]=React.useState(0.8),[steps,setSteps]=React.useState(30),[guidance,setGuidance]=React.useState(3),[size,setSize]=React.useState(512),[busy,setBusy]=React.useState(false),[stage,setStage]=React.useState(''),[error,setError]=React.useState(''),[jobs,setJobs]=React.useState([]),[images,setImages]=React.useState({});
  const panel=React.useRef(null),requestedModel=React.useRef(initialSelection?.id||''),pending=React.useRef(null),inFlight=React.useRef(false),previewing=React.useRef(new Set()),conversation=React.useRef(conversationId),submitting=React.useRef(false),fingerprint=React.useRef('');
  const route=React.useRef(null);route.current={open,modelId,adapterId};
  const [attemptJobId,setAttemptJobId]=React.useState(null);
  const generationPreference=useGenerationSettings(modelId);
  const activeJob=jobs.find(job=>['queued','starting','running','preparing'].includes(job.status));
  const shownJob=activeJob||jobs.find(job=>job.id===attemptJobId);
  React.useEffect(()=>{if(!studio&&!embedded&&open)publishImageProgress(busy?{modelId,job:{status:'preparing',stage:stage||'Preparing image generation'}}:shownJob?{modelId,job:shownJob}:null);},[open,studio,embedded,modelId,shownJob,busy,stage]);
  React.useEffect(()=>{conversation.current=conversationId;},[conversationId]);
  React.useLayoutEffect(()=>{
    if(studio)return;
    function accept(event){
      const detail=event.detail||{};
      if(detail.scope?!detail.scope.contains(panel.current):embedded)return;
      if(busy||activeJob||submitting.current){event.preventDefault();onNotice?.('Wait for image generation to finish before changing models.');return;}
      if(event.type==='opencore-chat-text-selected'){route.current.open=false;setOpen(false);return;}
      route.current={open:true,modelId:detail.modelId,adapterId:detail.adapterId||''};
      requestedModel.current=detail.modelId;setModelId(detail.modelId);setAdapterId(detail.adapterId||'');setOpen(true);setError('');setAttemptJobId(null);
    }
    window.addEventListener('opencore-chat-image-selected',accept);window.addEventListener('opencore-chat-text-selected',accept);
    return()=>{window.removeEventListener('opencore-chat-image-selected',accept);window.removeEventListener('opencore-chat-text-selected',accept);};
  },[studio,embedded,busy,!!activeJob,onNotice]);
  React.useLayoutEffect(()=>{
    if(studio)return;
    const matches=event=>event.detail?.scope?event.detail.scope.contains(panel.current):!embedded;
    const send=event=>{if(event.defaultPrevented||!matches(event)||!route.current.open)return;event.preventDefault();if(!submitting.current&&!busy&&!activeJob)void generate(event.detail);};
    const retry=event=>{
      if(event.defaultPrevented||!matches(event))return;
      const requestedConversation=event.detail?.conversationId;
      if(requestedConversation&&requestedConversation!==conversation.current)return;
      event.preventDefault();
      void retryImageJob(event.detail?.jobId);
    };
    const stop=event=>{if(event.defaultPrevented||!matches(event)||!route.current.open)return;event.preventDefault();void (async()=>{try{if(pending.current){await invoke('runtime_setup_cancel',{jobId:pending.current.setupId});pending.current=null;submitting.current=false;setBusy(false);setStage('');}else if(activeJob){await (activeJob.source==='hub-image'?globalThis.__opencoreImageHub.cancel(activeJob.id):invoke('cancel_studio_job',{id:activeJob.id}));}}catch(error){setError(String(error));}})();};
    window.addEventListener('opencore-chat-image-prompt',send);window.addEventListener('opencore-chat-image-stop',stop);window.addEventListener('opencore-chat-image-retry',retry);
    return()=>{window.removeEventListener('opencore-chat-image-prompt',send);window.removeEventListener('opencore-chat-image-stop',stop);window.removeEventListener('opencore-chat-image-retry',retry);};
  });
  const selectedImageModel=models.find(model=>model.id===modelId),imagePreset=selectedImageModel?.hubEntry?.imageDefaults;
  React.useEffect(()=>{const defaults=imagePreset||imageGenerationDefaults(modelId,selectedImageModel?.hubEntry?.repository);setSteps(defaults.steps);setGuidance(defaults.guidance);setSize(modelId.startsWith('qwen-image-21')||selectedImageModel?.hubEntry?.repository?.toLowerCase()==='qwen/qwen-image-2.1-turbo'?2048:1024);},[modelId,imagePreset?.steps,imagePreset?.guidance,selectedImageModel?.hubEntry?.repository]);
  const model=models.find(m=>m.id===modelId),choices=imageAdaptersForModel(model,adapters);
  const activeAdapter=choices.find(adapter=>adapter.id===adapterId),adapterLabel=activeAdapter?.familyLabel||activeAdapter?.name||activeAdapter?.repository||initialSelection?.adapterLabel;
  const selectionPreset=imageGenerationPreset(model,activeAdapter),presetSteps=selectionPreset.steps,presetGuidance=selectionPreset.guidance;
  React.useLayoutEffect(()=>{onStateChange?.({active:open,busy:busy||!!activeJob,modelId,adapterId,defaults:{steps:presetSteps,guidance:presetGuidance},label:model?imageSelectionLabel({...model,adapterId,adapterLabel}):undefined});},[open,busy,!!activeJob,modelId,adapterId,model,adapterLabel,presetSteps,presetGuidance,onStateChange]);
  React.useEffect(()=>{if(open&&!studio&&!embedded&&model)rememberChatImageModel({...model,...(adapterId?{adapterId,adapterLabel}:{})});},[open,studio,embedded,model,adapterId,adapterLabel]);
  React.useEffect(()=>{const draft=globalThis.__opencoreImageDraft;if(draft?.modelId===modelId&&choices.some(a=>a.id===draft.adapterId)){setAdapterId(draft.adapterId);setPrompt(draft.prompt||'');setStrength(draft.strength??0.8);delete globalThis.__opencoreImageDraft;}},[modelId,adapters]);
  async function invoke(command,args={}) {
    const run=()=>window.__TAURI_INTERNALS__.invoke(command,args);
    return globalThis.__opencoreInstallBridge?globalThis.__opencoreInstallBridge.invoke(command,args,run):run();
  }
  async function refresh() {
    const lib=await (globalThis.__opencoreModelLibrary?.read?globalThis.__opencoreModelLibrary.read(()=>invoke('list_model_library')):invoke('list_model_library'));
    const available=downloadedImageModels(lib.models);setModels(old=>old.length===available.length&&old.every((model,index)=>model===available[index])?old:available);
    const requested=requestedModel.current||globalThis.__opencoreRequestedImageModel;
    setModelId(id=>requested||id||available[0]?.id||'');
    if(requested&&available.some(m=>m.id===requested)){requestedModel.current='';delete globalThis.__opencoreRequestedImageModel;}
    const load=globalThis.__opencoreImageAdapters; if(load)setAdapters(await load());
    const all=await invoke('list_studio_jobs',{includeChatImages:!studio}),hub=globalThis.__opencoreImageHub;
    const imported=hub?await hub.list():[];
    const next=[...all,...imported].filter(j=>j.category==='image'&&!j.internalVerification&&!j.hidden&&(studio?!j.request.conversationId:j.request.conversationId===conversation.current)).slice().sort((a,b)=>b.createdAt.localeCompare(a.createdAt)).slice(0,6);
    setJobs(next);const signature=JSON.stringify(next.map(j=>[j.id,j.status,j.outputs]));if(!studio&&signature!==fingerprint.current){fingerprint.current=signature;onRefresh?.();}
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
    setAttemptJobId(job.id);
    if(id){conversation.current=id;onConversationId?.(id);}
    request.onAccepted?.();setPrompt('');submitting.current=false;setBusy(false);setStage('');onRefresh?.();
    if(!studio)setJobs(old=>[job,...old].slice(0,6));
  }
  async function retryImageJob(jobId){
    if(submitting.current||busy||activeJob){onNotice?.('Wait for the current image generation to finish before retrying.');return;}
    route.current={...route.current,open:true};setOpen(true);submitting.current=true;setBusy(true);setError('');setAttemptJobId(null);setStage('Retrying the saved image request');
    try{
      let job=jobs.find(value=>value.id===jobId);
      if(!job){
        const native=await invoke('list_studio_jobs'),hub=globalThis.__opencoreImageHub,imported=hub?await hub.list():[];
        job=[...native,...imported].find(value=>value.id===jobId);
      }
      if(!job?.request?.modelId||!job.request.prompt)throw Error('The saved image request could not be found. Send the prompt again to start a new image job.');
      const original=job.request,modelId=original.modelId;
      if(!studio&&original.conversationId&&conversation.current&&original.conversationId!==conversation.current)throw Error('This image belongs to a different conversation. Open that conversation and retry there.');
      const library=await readImageModelLibrary({fresh:true}),model=downloadedImageModels(library.models).find(value=>value.id===modelId);
      if(!model)throw Error('The image model from this request is no longer installed. Reinstall it in Models before retrying.');
      const settings={...(original.settings||{})};
      const request={...original,modelId,prompt:original.prompt,conversationId:original.conversationId||(studio?null:conversation.current||'opencore:'+crypto.randomUUID()),settings};
      let matchingAdapterId='';
      if(settings.loraPath){
        const liveAdapters=await globalThis.__opencoreImageAdapters?.()||[];
        matchingAdapterId=imageAdaptersForModel(model,liveAdapters).find(adapter=>adapter.installReceipt.files.some(file=>file.role==='model'&&file.localPath?.toLowerCase()===String(settings.loraPath).toLowerCase()))?.id||'';
      }
      route.current={open:true,modelId,adapterId:matchingAdapterId};setModelId(modelId);setAdapterId(matchingAdapterId);setOpen(true);
      const snapshot=await invoke('get_snapshot');
      if(snapshot?.activeConversationIds?.length)throw Error('Another conversation is generating. Let it finish before retrying the image.');
      if(['running','starting'].includes(snapshot?.runtime?.status)){setStage('Releasing the text model from memory for image generation');await invoke('stop_runtime');}
      if((model.hubEntry?.format==='diffusers'||model.hubEntry?.imageRuntime==='iris-sdcpp-v1')&&!model.hubEntry?.nativeProfile){
        const hub=globalThis.__opencoreImageHub;if(!hub)throw Error('Update OpenCore to enable imported image generation.');
        try{window.localStorage.setItem('opencore.image-chat-history','1');}catch{}
        const next=await hub.generate(request);setAttemptJobId(next.id);setJobs(old=>[next,...old.filter(value=>value.id!==next.id)].slice(0,6));
        if(request.conversationId){conversation.current=request.conversationId;onConversationId?.(request.conversationId);}
        submitting.current=false;setBusy(false);setStage('');onRefresh?.();return;
      }
      const status=await invoke('runtime_setup_status'),receipt=status.receipts.find(value=>value.targetId===modelId&&value.dependenciesVerified&&value.inferenceVerified);
      if(receipt){await submitReady(request,receipt);return;}
      const recipe=status.recipes.find(value=>value.modelIds.includes(modelId));
      if(!recipe)throw Error('Automatic runtime setup is not available for this image architecture yet.');
      if(recipe.requiresLicenseAcceptance)throw Error('Accept this model’s publisher license in Models before retrying.');
      const setup=await invoke('runtime_setup_start',{targetId:modelId,options:{installWeights:!model.installed,verifyInference:true,acceptLicenses:false,allowAdministrator:false}});
      pending.current={...request,setupId:setup.id};setStage('Preparing and verifying the image model. Your saved prompt will run automatically afterward.');
    }catch(error){pending.current=null;submitting.current=false;setBusy(false);setStage('');setError(String(error));}
  }
  async function generate(input){
    const {modelId,adapterId}=route.current;
    const text=studio?prompt:input?.prompt||'';
    if(submitting.current||busy||!text.trim())return;
    submitting.current=true;setBusy(true);setError('');setAttemptJobId(null);setStage('Preparing image generation');
    try{
      const live=await readImageModelLibrary({fresh:true}),installed=downloadedImageModels(live.models).find(value=>value.id===modelId);
      if(!installed)throw Error('This image model is no longer installed. Choose a downloaded model or install its base weights in Models.');
      const model=installed;
      const liveAdapters=adapterId?await globalThis.__opencoreImageAdapters?.()||[]:[],adapter=imageAdaptersForModel(installed,liveAdapters).find(a=>a.id===adapterId),file=adapter?.installReceipt.files.find(f=>f.role==='model'&&f.localPath);
      if(adapterId&&!file)throw Error('This LoRA is no longer installed or compatible with the selected base. Choose another model option.');
      const snapshot=await invoke('get_snapshot');
      if(snapshot?.activeConversationIds?.length)throw Error('Another conversation is generating. Let it finish before starting image generation.');
      if(['running','starting'].includes(snapshot?.runtime?.status)){setStage('Releasing the text model from memory for image generation');await invoke('stop_runtime');}
      const preset=imageGenerationPreset(model,adapter);
      const request={modelId,prompt:adapter?.repository==='axiomofmind/Moss-image-nsfw-uncensored-lora'&&file&&!/\bmoss_xvstyle\b/i.test(text)?'moss_xvstyle, '+text.trim():text.trim(),conversationId:conversation.current,settings:{...(studio?{...imageSizeSettings(size,modelId,model.hubEntry?.repository),steps,guidanceScale:guidance,numImages:1,outputFormat:'png'}:automaticImageSettings(modelId,text,input?.files,preset,model.hubEntry?.repository)),...(file?{loraPath:file.localPath,loraStrength:preset.loraStrength??strength}:{})},onAccepted:input?.onAccepted};
      request.settings.cpuOffload=generationPreference.cpuOffload===true;
      if(!studio)request.settings=applyImageGenerationOptions(modelId,request.settings);
      if(globalThis.__opencorePromptSettings?.().enabled&&globalThis.__opencorePromptSettings().automaticImages){
        try{const better=await globalThis.__opencoreImproveLocalPrompt(request.prompt,conversation.current,'image',setStage);request.settings.originalPrompt=text.trim();request.settings.promptRecap=better.recap;request.prompt=better.prompt;}
        catch(error){setError('Prompt assistant: '+String(error)+' Your original prompt will be used.');}
      }
      if(request.settings.outputWidth>request.settings.width||request.settings.outputHeight>request.settings.height){setStage('Preparing the AI detail upscaler…');const upscaler=await globalThis.__opencoreImageHub.prepareUpscaler();request.settings.upscaleModelPath=upscaler.path;}
      if((model.hubEntry?.format==='diffusers'||model.hubEntry?.imageRuntime==='iris-sdcpp-v1')&&!model.hubEntry?.nativeProfile){
        const hub=globalThis.__opencoreImageHub;if(!hub)throw Error('Update OpenCore to enable imported image generation.');
        try{window.window.localStorage.setItem('opencore.image-chat-history','1');}catch{}
        request.conversationId=request.conversationId||(studio?null:'opencore:'+crypto.randomUUID());
        const job=await hub.generate(request);setAttemptJobId(job.id);if(request.conversationId){conversation.current=request.conversationId;onConversationId?.(request.conversationId);}
        request.onAccepted?.();submitting.current=false;setJobs(old=>[job,...old].slice(0,6));setPrompt('');setBusy(false);setStage('');onRefresh?.();return;
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
  if(!studio)return h('section',{ref:panel,className:'chat-image-status','data-image-mode':open?'open':'closed','aria-label':'Chat image generation'},open?h(React.Fragment,null,h('span',null,(model?.label||initialSelection?.label||'Image model')+(adapterId?' + '+(adapterLabel||'LoRA'):'')+(generationPreference.mode==='manual'?' · Manual settings':' · Automatic quality')+(generationPreference.cpuOffload?' · CPU RAM offload':'')),busy||stage||shownJob?h(ImageProgressCard,{job:activeJob||(!busy?shownJob:null),stage:busy?stage||'Preparing image generation':undefined}):null,error||!busy&&!activeJob&&shownJob?.error?h(ImageError,{error:error||shownJob.error}):null):null);
  return h('section',{ref:panel,className:'image-generation-panel','data-image-mode':open?'open':'closed','aria-label':studio?'Image generation':'Chat image generation'},
    !studio?h('div',{className:'image-mode-tabs',role:'group','aria-label':'Chat generation mode'},h('button',{type:'button','aria-pressed':!open,disabled:busy,onClick:()=>clearChatImageSelection({scope:panel.current?.closest('.chat-composer-wrap')||panel.current,primary:!embedded})},'Text'),h('button',{type:'button','aria-pressed':open,disabled:busy,onClick:()=>setOpen(true)},'Images')):null,
    open?h(React.Fragment,null,h('h3',null,studio?'Generate an image':'Generate images in this chat'),
      models.length?h('label',null,'Downloaded image model',h('select',{'aria-label':'Downloaded image model',value:modelId,disabled:busy,onChange:e=>{setModelId(e.target.value);setAdapterId('');}},...models.map(m=>h('option',{key:m.id,value:m.id},m.label+' · '+m.precision)))):h('p',{role:'status'},'No image models are downloaded. Install an image model in Models to start.'),
      choices.length?h('div',{className:'image-panel-fields'},h('label',null,'LoRA',h('select',{'aria-label':'Image LoRA',value:choices.some(a=>a.id===adapterId)?adapterId:'',disabled:busy,onChange:e=>setAdapterId(e.target.value)},h('option',{value:''},'None'),...choices.map(a=>h('option',{key:a.id,value:a.id},a.familyLabel||a.name||a.repository)))),adapterId?h('label',null,'LoRA strength',h('input',{'aria-label':'Image LoRA strength',type:'number',min:0,max:2,step:0.1,value:strength,disabled:busy,onChange:e=>setStrength(Number(e.target.value))})):null):null,
      h('label',null,'Image prompt',h('textarea',{'aria-label':'Image generation prompt',placeholder:'Describe the image you want…',rows:3,value:prompt,disabled:busy,onChange:e=>setPrompt(e.target.value)})),
      h('div',{className:'image-panel-fields'},h('label',null,'Output size',h('select',{'aria-label':'Image size',value:size,disabled:busy,onChange:e=>setSize(Number(e.target.value))},...IMAGE_SIZE_OPTIONS.map(option=>h('option',{key:option.value,value:option.value},option.label)))),h('label',null,'Steps',h('input',{'aria-label':'Image steps',type:'number',min:1,max:100,value:steps,disabled:busy,onChange:e=>setSteps(Number(e.target.value))})),h('label',null,'Guidance',h('input',{'aria-label':'Image guidance',type:'number',min:0,max:30,step:0.5,value:guidance,disabled:busy,onChange:e=>setGuidance(Number(e.target.value))}))),
      size>imageSizeSettings(size,modelId,model?.hubEntry?.repository).width?h('small',{className:'image-output-size-note'},'Output: '+size+' × '+size+'. Generated at '+imageSizeSettings(size,modelId,model?.hubEntry?.repository).width+' × '+imageSizeSettings(size,modelId,model?.hubEntry?.repository).height+', then enhanced locally with the Real-ESRGAN AI upscaler.'):null,
      h('button',{type:'button',className:'image-generate-button',disabled:busy||!model||!prompt.trim()||!Number.isInteger(steps)||steps<1||steps>100||!Number.isFinite(strength)||strength<0||strength>2||!Number.isFinite(guidance)||guidance<0||guidance>30,onClick:()=>void generate()},busy?'Preparing…':'Generate image'),
      stage?h('div',{role:'status'},h('progress',{'aria-label':'Image runtime preparation'}),h('span',null,stage)):null,
      error?h(ImageError,{error}):null,
      ...jobs.map(job=>h('article',{className:'chat-image-job',key:job.id},h('p',null,job.request.prompt),h(ImageProgressCard,{job}),job.error?h(ImageError,{error:job.error}):null,...imageOutputs(job).map(path=>images[job.id+':'+path]?h('img',{key:path,src:images[job.id+':'+path],alt:job.request.prompt}):null),['queued','starting','running','preparing'].includes(job.status)?h('button',{type:'button',onClick:()=>void (job.source==='hub-image'?globalThis.__opencoreImageHub.cancel(job.id):invoke('cancel_studio_job',{id:job.id})).catch(e=>setError(String(e)))},'Cancel image generation'):null))):null);
}
