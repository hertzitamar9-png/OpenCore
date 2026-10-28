import React from 'react';

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
  if(id==='z-image-turbo')return {steps:9,guidance:0};
  if(id==='flux-2-klein-4b')return {steps:4,guidance:1};
  if(id==='flux-2-klein-base-4b')return {steps:30,guidance:4};
  if(id==='animation-diffusion-2d')return {steps:30,guidance:7.5};
  return {steps:30,guidance:id.startsWith('moss-')?3:id.startsWith('qwen-image-21')?4:4.5};
}

export function imageOutputs(job) {
  const images=(job.outputs||[]).filter(path=>/\.(png|jpe?g|webp)$/i.test(path));
  const published=images.filter(path=>!/[\\/]comfy-output[\\/]/i.test(path));
  return published.length?published:images;
}

export function ImageGenerationPanel({conversationId,onConversationId,onNotice,onRefresh,studio=false,initialModelId=''}) {
  const h=React.createElement;
  const [open,setOpen]=React.useState(studio),[models,setModels]=React.useState([]),[modelId,setModelId]=React.useState(initialModelId),[adapters,setAdapters]=React.useState([]),[adapterId,setAdapterId]=React.useState(''),[prompt,setPrompt]=React.useState(''),[strength,setStrength]=React.useState(0.8),[steps,setSteps]=React.useState(30),[guidance,setGuidance]=React.useState(3),[size,setSize]=React.useState(512),[busy,setBusy]=React.useState(false),[stage,setStage]=React.useState(''),[error,setError]=React.useState(''),[jobs,setJobs]=React.useState([]),[images,setImages]=React.useState({});
  const pending=React.useRef(null),inFlight=React.useRef(false),previewing=React.useRef(new Set()),conversation=React.useRef(conversationId);
  React.useEffect(()=>{conversation.current=conversationId;},[conversationId]);
  React.useEffect(()=>{const defaults=imageGenerationDefaults(modelId);setSteps(defaults.steps);setGuidance(defaults.guidance);setAdapterId('');},[modelId]);
  const model=models.find(m=>m.id===modelId),choices=imageAdaptersForModel(model,adapters);
  React.useEffect(()=>{const draft=globalThis.__opencoreImageDraft;if(draft?.modelId===modelId&&choices.some(a=>a.id===draft.adapterId)){setAdapterId(draft.adapterId);setPrompt(draft.prompt||'');setStrength(draft.strength??0.8);delete globalThis.__opencoreImageDraft;}},[modelId,adapters]);
  async function invoke(command,args={}) {
    const run=()=>window.__TAURI_INTERNALS__.invoke(command,args);
    return globalThis.__opencoreInstallBridge?globalThis.__opencoreInstallBridge.invoke(command,args,run):run();
  }
  async function refresh() {
    const lib=await (globalThis.__opencoreModelLibrary?.read?globalThis.__opencoreModelLibrary.read(()=>invoke('list_model_library')):invoke('list_model_library'));
    const available=lib.models.filter(m=>m.category==='image'&&m.hubEntry?.packageKind!=='adapter'&&m.hubEntry?.format!=='lora'&&(m.installed||m.runtimeConnected));setModels(available);
    const requested=globalThis.__opencoreRequestedImageModel;
    setModelId(id=>available.some(m=>m.id===requested)?requested:available.some(m=>m.id===id)?id:available[0]?.id||'');
    if(requested&&available.some(m=>m.id===requested))delete globalThis.__opencoreRequestedImageModel;
    const load=globalThis.__opencoreImageAdapters; if(load)setAdapters(await load());
    const all=await invoke('list_studio_jobs'),hub=globalThis.__opencoreImageHub;
    const imported=hub?await hub.list():[];
    setJobs([...all,...imported].filter(j=>j.category==='image'&&(studio?j.source==='hub-image':j.request.conversationId===conversation.current)).slice().sort((a,b)=>b.createdAt.localeCompare(a.createdAt)).slice(0,6));
  }
  React.useEffect(()=>{
    if(!open)return;let alive=true;
    async function tick(){if(inFlight.current)return;inFlight.current=true;try{
      if(alive)await refresh();
      if(pending.current){const status=await invoke('runtime_setup_status'),request=pending.current;
        const receipt=status.receipts.find(r=>r.targetId===request.modelId&&r.dependenciesVerified&&r.inferenceVerified);
        const job=status.jobs.find(j=>j.id===request.setupId);
        if(receipt){pending.current=null;await submitReady(request,receipt);}
        else if(job&&['failed','cancelled','interrupted'].includes(job.status)){pending.current=null;setBusy(false);setStage('');setError(job.error||'Model preparation stopped. Retry to continue.');}
        else if(job)setStage(job.detail||job.stage||'Preparing image model…');
      }
    }catch(e){if(alive){setError(String(e));if(!pending.current)setBusy(false);}}finally{inFlight.current=false;}}
    void tick();const timer=setInterval(()=>void tick(),2000);return()=>{alive=false;clearInterval(timer);};
  },[open,conversationId,studio]);
  React.useEffect(()=>{
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
    setPrompt('');setBusy(false);setStage('');onNotice?.('Image generation queued. Progress and the image appear here.');onRefresh?.();
    if(!studio)setJobs(old=>[job,...old].slice(0,6));
  }
  async function generate(){
    if(!model||busy||!prompt.trim())return;setBusy(true);setError('');
    try{
      const adapter=choices.find(a=>a.id===adapterId),file=adapter?.installReceipt.files.find(f=>f.role==='model'&&f.localPath);
      const request={modelId,prompt:adapter?.repository==='axiomofmind/Moss-image-nsfw-uncensored-lora'&&file&&!/\bmoss_xvstyle\b/i.test(prompt)?'moss_xvstyle, '+prompt.trim():prompt.trim(),conversationId:conversation.current,settings:{width:size,height:size,steps,guidanceScale:guidance,numImages:1,outputFormat:'png',...(file?{loraPath:file.localPath,loraStrength:strength}:{})}};
      if(model.hubEntry?.format==='diffusers'&&!model.hubEntry?.nativeProfile){
        const hub=globalThis.__opencoreImageHub;if(!hub)throw Error('Update OpenCore to enable imported image generation.');
        try{window.localStorage.setItem('opencore.image-chat-history','1');}catch{}
        request.conversationId=request.conversationId||(studio?null:'opencore:'+crypto.randomUUID());
        const job=await hub.generate(request);if(request.conversationId){conversation.current=request.conversationId;onConversationId?.(request.conversationId);}
        setJobs(old=>[job,...old].slice(0,6));setPrompt('');setBusy(false);onNotice?.('Image generation queued. Runtime preparation and progress appear here.');onRefresh?.();return;
      }
      const status=await invoke('runtime_setup_status');const receipt=status.receipts.find(r=>r.targetId===modelId&&r.dependenciesVerified&&r.inferenceVerified);
      if(receipt){await submitReady(request,receipt);return;}
      const recipe=status.recipes.find(r=>r.modelIds.includes(modelId));
      if(!recipe)throw Error('Automatic runtime setup is not available for this image architecture yet.');
      if(recipe.requiresLicenseAcceptance)throw Error('Accept this model’s publisher license in Models before preparing its runtime.');
      const job=await invoke('runtime_setup_start',{targetId:modelId,options:{installWeights:!model.installed,verifyInference:true,acceptLicenses:false,allowAdministrator:false}});
      pending.current={...request,setupId:job.id};setStage('Preparing and verifying the image model. Your prompt will run automatically afterward.');
    }catch(e){pending.current=null;setBusy(false);setStage('');setError(String(e));}
  }
  return h('section',{className:'image-generation-panel','data-image-mode':open?'open':'closed','aria-label':studio?'Image generation':'Chat image generation'},
    !studio?h('div',{className:'image-mode-tabs',role:'group','aria-label':'Chat generation mode'},h('button',{type:'button','aria-pressed':!open,disabled:busy,onClick:()=>setOpen(false)},'Text'),h('button',{type:'button','aria-pressed':open,disabled:busy,onClick:()=>setOpen(true)},'Images')):null,
    open?h(React.Fragment,null,h('h3',null,studio?'Generate an image':'Generate images in this chat'),
      models.length?h('label',null,'Downloaded image model',h('select',{'aria-label':'Downloaded image model',value:modelId,disabled:busy,onChange:e=>{setModelId(e.target.value);setAdapterId('');}},...models.map(m=>h('option',{key:m.id,value:m.id},m.label+' · '+m.precision)))):h('p',{role:'status'},'No image models are downloaded. Install an image model in Models to start.'),
      choices.length?h('div',{className:'image-panel-fields'},h('label',null,'LoRA',h('select',{'aria-label':'Image LoRA',value:choices.some(a=>a.id===adapterId)?adapterId:'',disabled:busy,onChange:e=>setAdapterId(e.target.value)},h('option',{value:''},'None'),...choices.map(a=>h('option',{key:a.id,value:a.id},a.familyLabel||a.name||a.repository)))),adapterId?h('label',null,'LoRA strength',h('input',{'aria-label':'Image LoRA strength',type:'number',min:0,max:2,step:0.1,value:strength,disabled:busy,onChange:e=>setStrength(Number(e.target.value))})):null):null,
      h('label',null,'Image prompt',h('textarea',{'aria-label':'Image generation prompt',placeholder:'Describe the image you want…',rows:3,value:prompt,disabled:busy,onChange:e=>setPrompt(e.target.value)})),
      h('div',{className:'image-panel-fields'},h('label',null,'Size',h('select',{'aria-label':'Image size',value:size,disabled:busy,onChange:e=>setSize(Number(e.target.value))},...[256,512,768,1024].map(n=>h('option',{key:n,value:n},n+' × '+n)))),h('label',null,'Steps',h('input',{'aria-label':'Image steps',type:'number',min:1,max:100,value:steps,disabled:busy,onChange:e=>setSteps(Number(e.target.value))})),h('label',null,'Guidance',h('input',{'aria-label':'Image guidance',type:'number',min:0,max:30,step:0.5,value:guidance,disabled:busy,onChange:e=>setGuidance(Number(e.target.value))}))),
      h('button',{type:'button',className:'image-generate-button',disabled:busy||!model||!prompt.trim()||!Number.isInteger(steps)||steps<1||steps>100||!Number.isFinite(strength)||strength<0||strength>2||!Number.isFinite(guidance)||guidance<0||guidance>30,onClick:()=>void generate()},busy?'Preparing…':'Generate image'),
      stage?h('div',{role:'status'},h('progress',{'aria-label':'Image runtime preparation'}),h('span',null,stage)):null,
      error?h('p',{role:'alert'},error):null,
      ...jobs.map(job=>h('article',{className:'chat-image-job',key:job.id},h('p',null,job.request.prompt),h('small',{role:'status'},job.stage||job.status),job.error?h('p',{role:'alert'},job.error):null,...imageOutputs(job).map(path=>images[job.id+':'+path]?h('img',{key:path,src:images[job.id+':'+path],alt:job.request.prompt}):null),['queued','starting','running','preparing'].includes(job.status)?h('button',{type:'button',onClick:()=>void (job.source==='hub-image'?globalThis.__opencoreImageHub.cancel(job.id):invoke('cancel_studio_job',{id:job.id})).catch(e=>setError(String(e)))},'Cancel image generation'):null))):null);
}
