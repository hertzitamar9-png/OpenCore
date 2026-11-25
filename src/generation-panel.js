/* Native generation controls share the managed pipeline used by ECHO tools. */
export function createGenerationPanel(React, service) {
  const h=React.createElement,active=job=>['queued','preparing','starting','running'].includes(job?.status);
  return function GenerationPanel({entry}){
    const [file,setFile]=React.useState(null),[budget,setBudget]=React.useState(.5),[steps,setSteps]=React.useState(50),
      [seed,setSeed]=React.useState(42),[guidance,setGuidance]=React.useState(3),[imageMode,setImageMode]=React.useState('synthetic'),
      [gaussians,setGaussians]=React.useState(262144),[job,setJob]=React.useState(null),[busy,setBusy]=React.useState(false),
      [error,setError]=React.useState(''),[preview,setPreview]=React.useState('');
    const alive=React.useRef(true),original=entry.repository==='speridlabs/idol';
    React.useEffect(()=>{alive.current=true;let timer;
      async function poll(){try{
        const jobs=(await service.request('/generation/jobs')).jobs||[],latest=jobs.find(job=>job.request?.modelId===entry.id);
        if(alive.current){setJob(latest||null);timer=setTimeout(poll,active(latest)?800:3000);}
      }catch(failure){if(alive.current){setError(String(failure));timer=setTimeout(poll,5000);}}}
      void poll();return()=>{alive.current=false;clearTimeout(timer);};
    },[entry.id]);
    async function generate(){if(busy||active(job))return;setBusy(true);setError('');setPreview('');
      try{
        if(!file)throw Error('Choose an input image first.');
        if(file.size>16*1024*1024)throw Error('Choose an input image up to 16 MB.');
        const image=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('The input image could not be read.'));reader.readAsDataURL(file);});
        const input=await service.request('/generation/input',{image});
        const mode=globalThis.__opencoreModelModes?.mode(entry)||entry.operatingMode||'native';
        await globalThis.__opencoreModelModes?.ensure(entry);
        if(mode==='echo')await service.connect();
        const snapshot=await service.invoke('get_snapshot',{});
        if(snapshot.activeConversationIds?.length)throw Error('Let the current chat finish before starting 3D generation.');
        if(['running','starting'].includes(snapshot.runtime?.status))await service.invoke('stop_runtime',{});
        const value=await service.request('/generation/generate',{request:{modelId:entry.id,operatingMode:mode,
          prompt:'Generate 3D from '+file.name,settings:{inputPath:input.path,budget:original?1:budget,steps,seed,
            guidanceScale:guidance,imageMode,numGaussians:gaussians,outputFormat:'ply'}}});
        if(alive.current)setJob(value);
      }catch(failure){if(alive.current)setError(String(failure));}finally{if(alive.current)setBusy(false);}}
    async function cancel(){try{await service.request('/generation/cancel',{id:job.id});if(alive.current)setJob({...job,stage:'Cancelling and releasing the worker'});}catch(failure){if(alive.current)setError(String(failure));}}
    async function download(path){setError('');try{await service.download(job.id,path);}catch(failure){if(alive.current)setError(String(failure));}}
    async function inspect(path){setError('');try{const value=await service.request('/generation/preview',{id:job.id,path});if(alive.current)setPreview(value.dataUrl);}catch(failure){if(alive.current)setError(String(failure));}}
    const locked=busy||active(job);
    const number=(label,value,set,min,max,step=1)=>h('label',null,label,h('input',{type:'number',value,min,max,step,disabled:locked,'aria-label':label,onChange:event=>set(Number(event.target.value))}));
    return h('section',{className:'hub-generation-panel','aria-label':'3D generation'},
      h('strong',null,'Image to 3D Gaussian splats'),
      h('p',null,'Exports a Gaussian PLY file. Choose the object image; the model does not use a text prompt to shape it.'),
      h('label',null,'Object image',h('input',{type:'file',accept:'image/png,image/jpeg,image/webp','aria-label':'3D input image',disabled:locked,onChange:event=>{setFile(event.target.files?.[0]||null);setError('');}})),
      !original?h('label',null,'Token budget',h('select',{'aria-label':'3D token budget',value:budget,disabled:locked,onChange:event=>setBudget(Number(event.target.value))},
        ...[.0625,.125,.25,.5,1].map(value=>h('option',{key:value,value},value*100+'% Â· '+Math.round(value*8192)+' tokens')))):null,
      h('div',{className:'generation-controls'},number('Sampling steps',steps,setSteps,1,150),number('Seed',seed,setSeed,0,4294967295),number('Guidance',guidance,setGuidance,0,15,.1),
        h('label',null,'Image type',h('select',{'aria-label':'3D image type',value:imageMode,disabled:locked,onChange:event=>setImageMode(event.target.value)},h('option',{value:'synthetic'},'Synthetic / illustration'),h('option',{value:'real'},'Photograph'))),
        h('label',null,'Gaussian count',h('select',{'aria-label':'Gaussian count',value:gaussians,disabled:locked,onChange:event=>setGaussians(Number(event.target.value))},...[65536,131072,262144,524288].map(value=>h('option',{key:value,value},value.toLocaleString()))))),
      h('p',null,entry.dependenciesPrepared?'Dependencies prepared. Generation uses the installed files offline.':'The first run prepares the pinned IDOL components and isolated Python runtime. DINOv3 requires publisher access on Hugging Face.'),
      h('div',{className:'generation-actions'},h('button',{type:'button',disabled:locked||!file,onClick:()=>void generate()},busy?'Startingâ€¦':'Generate 3D'),
        active(job)?h('button',{type:'button',onClick:()=>void cancel()},'Cancel 3D generation'):null,
        !entry.dependenciesPrepared?h('button',{type:'button',disabled:locked,onClick:()=>void service.invoke('hub_prepare_model',{id:entry.id}).catch(failure=>setError(String(failure)))},'Prepare 3D dependencies'):null,
        h('button',{type:'button',disabled:locked,onClick:()=>void service.connect().catch(failure=>setError(String(failure)))},'Connect ECHO pipeline tools')),
      job?h('div',{'aria-label':'3D job status'},h('p',{role:'status'},job.stage||job.status),job.progress?.total?h('progress',{max:job.progress.total,value:job.progress.step||0,'aria-label':'3D sampling progress'}):null,
        job.error?h('p',{role:'alert'},job.error):null,
        job.status==='completed'?h('div',{className:'generation-actions'},...job.outputs.map(path=>h('button',{key:path,type:'button',onClick:()=>void(path.endsWith('.ply')?download(path):inspect(path))},path.endsWith('.ply')?'Download Gaussian PLY':'View prepared input'))):null):null,
      preview?h('img',{src:preview,alt:'The input image after background removal and framing',style:{width:'100%',maxWidth:320,borderRadius:8}}):null,
      error?h('p',{role:'alert'},error):null);
  };
}

export function createPipelineHandoff(service){
  // This uses the same native stop command as the image form; it never kills a process by name.
  return async function handoff(){
    const pending=(await service.request('/pipelines/pending')).jobs||[];
    if(!pending.length)return;
    const snapshot=await service.invoke('get_snapshot',{});
    if(snapshot.activeConversationIds?.length||['starting','stopping'].includes(snapshot.runtime?.status))return;
    if(snapshot.runtime?.status==='running')await service.invoke('stop_runtime',{});
    for(const job of pending)await service.request('/pipelines/admit',{jobId:job.jobId});
  };
}

export function installGenerationPanel(React, service){
  let connection;
  async function connect(){
    if(connection)return connection;
    connection=(async()=>{
      const server=await service.request('/pipelines/connection',{}),config=await service.invoke('agent_platform_configuration',{});
      if(!config||!Array.isArray(config.mcpServers))throw Error('OpenCore agent settings are unavailable.');
      const current=config.mcpServers.find(item=>item.id===server.id);
      if(JSON.stringify(current)!==JSON.stringify(server)){
        const mcpServers=[...config.mcpServers.filter(item=>item.id!==server.id),server];
        await service.invoke('agent_platform_action',{name:'app_control',args:{action:'set',settings:{mcpServers},source:'settings-ui'}});
        const saved=await service.invoke('agent_platform_configuration',{});
        window.dispatchEvent(new CustomEvent('opencore-agent-settings-changed',{detail:saved}));
      }
      return {connected:true};
    })().finally(()=>{connection=null;});return connection;
  }
  async function download(id,path){
    const descriptor=await service.descriptor();
    const response=await fetch(descriptor.url+'/api/generation/artifact',{method:'POST',
      headers:{'Content-Type':'application/json',[descriptor.authHeader||'X-OpenCore-Hub-Secret']:descriptor.secret},
      body:JSON.stringify({id,path}),signal:AbortSignal.timeout(120000)});
    if(!response.ok){const value=await response.json();throw Error(value.error||'The generated artifact could not be opened.');}
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download=path.split(/[\\/]/).at(-1)||'object.ply';link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }
  globalThis.__opencoreGenerationConnect=connect;
  if(!globalThis.__OPENCORE_PHONE__&&!globalThis.__opencorePipelineAdmissionStarted){
    globalThis.__opencorePipelineAdmissionStarted=true;
    const handoff=createPipelineHandoff(service);let retry=2000;
    async function poll(){try{await handoff();retry=2000;}catch{retry=Math.min(retry*2,15000);}finally{setTimeout(poll,retry);}}
    setTimeout(poll,2000);
  }
  const style=document.createElement('style');style.textContent='.hub-generation-panel{display:grid;gap:12px;margin-top:14px;min-width:0}.hub-generation-panel label{display:grid;gap:6px}.hub-generation-panel input,.hub-generation-panel select{width:100%;min-width:0;box-sizing:border-box;min-height:38px;padding:8px 10px;border:1px solid #316aa4;border-radius:7px;background:#112438;color:#f0f5fc;font:inherit}.hub-generation-panel input[type=file]{background:#101419}.hub-generation-panel :is(input,select):focus-visible{outline:2px solid #69b5ff;outline-offset:2px}.hub-generation-panel :disabled{opacity:.6}.hub-generation-panel button{min-height:36px}.generation-controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}.generation-actions{display:flex;gap:8px;flex-wrap:wrap}.hub-generation-panel progress{width:100%}.hub-generation-panel p{overflow-wrap:anywhere}';document.head.appendChild(style);
  return createGenerationPanel(React,{...service,connect,download});
}
