/** Shared by the source UI and the shipped, recovered Learning Studio. */
export function autoLoopStartRequest(modelPath, options={}) {
  if (typeof modelPath!=='string'||!modelPath.toLowerCase().endsWith('.gguf')) throw Error('Choose a single-file GGUF checkpoint.');
  const result={modelPath};
  for (const [key,fallback,min,max] of [['maxVariants',3,1,8],['threads',2,1,8],['maxMinutes',60,1,1440],['maxDiskGiB',128,1,2048]]) {
    const value=options[key]??fallback;
    if (!Number.isInteger(value)||value<min||value>max) throw Error(`${key} must be between ${min} and ${max}.`);
    result[key]=value;
  }
  result.experimentalDamping=options.experimentalDamping===true;
  if(options.benchmarkPath)result.benchmarkPath=options.benchmarkPath;
  return result;
}

export function createAutoLoopPanel(React,deps) {
  const h=React.createElement,terminal=new Set(['accepted','rejected','failed','cancelled','baseline_complete']);
  const settingsKey='opencore.autoloop.settings.v1';
  const stageName=value=>({queued:'Queued',loading:'Loading the test model',development:'Testing development questions',holdout:'Confirming the selected candidate',
    'hashing-source':'Sealing the original checkpoint','hashing-runtime':'Verifying the bundled runtime','candidate-start':'Building a candidate',
    'verifying-app-runtime':'Verifying the OpenCore chat runtime',accepted:'Improved model ready',rejected:'Original retained',failed:'Run failed',cancelled:'Cancelled',cancelling:'Stopping safely'}[value]||String(value||'Preparing').replaceAll('-',' '));
  return function AutoLoopPanel({onNotice=()=>{}}={}) {
    const [models,setModels]=React.useState([]),[modelPath,setModelPath]=React.useState(''),[inspection,setInspection]=React.useState(null);
    const [snapshot,setSnapshot]=React.useState({runs:[],activeRunId:null}),[selected,setSelected]=React.useState('');
    const [error,setError]=React.useState(''),[busy,setBusy]=React.useState(''),[report,setReport]=React.useState(null),[now,setNow]=React.useState(Date.now());
    const [options,setOptions]=React.useState(()=>{try{return JSON.parse(localStorage.getItem(settingsKey)||'{}')}catch{return {}}});
    const alive=React.useRef(false),inspectRevision=React.useRef(0),pending=React.useRef(false),operationRevision=React.useRef(0);
    const request=deps.request;
    const refresh=React.useCallback(async()=>{if(pending.current)return;pending.current=true;const revision=operationRevision.current;try{const value=await request('/autoloop/status');if(alive.current&&revision===operationRevision.current)setSnapshot(value)}finally{pending.current=false}},[]);
    React.useEffect(()=>{alive.current=true;let stopped=false;
      void request('/autoloop/models').then(value=>{if(stopped)return;setModels(value.models||[]);setModelPath(path=>path||value.models?.[0]?.modelPath||'')}).catch(reason=>!stopped&&setError(String(reason)));
      void refresh().catch(reason=>!stopped&&setError(String(reason)));
      const timer=setInterval(()=>void refresh().catch(reason=>!stopped&&setError(String(reason))),1500);
      return()=>{alive.current=false;stopped=true;inspectRevision.current++;clearInterval(timer)};
    },[refresh]);
    React.useEffect(()=>{const revision=++inspectRevision.current;setInspection(null);setError('');if(!modelPath)return;
      void request('/autoloop/inspect',{modelPath}).then(value=>{if(alive.current&&revision===inspectRevision.current)setInspection(value)}).catch(reason=>{if(alive.current&&revision===inspectRevision.current)setError(String(reason))});
    },[modelPath]);
    React.useEffect(()=>{try{localStorage.setItem(settingsKey,JSON.stringify(options))}catch{}},[options]);
    React.useEffect(()=>{if(!snapshot.activeRunId)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[snapshot.activeRunId]);
    const act=async(name,operation)=>{if(busy)return;setBusy(name);setError('');try{return await operation()}catch(reason){if(alive.current)setError(String(reason).replace(/^Error: /,''))}finally{if(alive.current)setBusy('')}};
    const choose=async(kind)=>{const file=await deps.pickFile(kind);if(typeof file==='string'){if(kind==='model')setModelPath(file);else setOptions(value=>({...value,benchmarkPath:file}))}};
    const start=()=>act('start',async()=>{const runtime=await deps.invoke('get_snapshot');if(runtime?.activeConversationIds?.length)throw Error('Let the current chat finish before starting AutoLoop.');
      const run=await request('/autoloop/start',autoLoopStartRequest(modelPath,options));if(!alive.current)return;operationRevision.current++;setSnapshot(current=>({...current,activeRunId:run.id,runs:[run,...current.runs.filter(value=>value.id!==run.id)]}));setSelected(run.id);setReport(null);await refresh();onNotice('AutoLoop started. The original stays unchanged; follow its tests here.');});
    const run=snapshot.runs.find(value=>value.id===(selected||snapshot.activeRunId))||snapshot.runs[0];
    const compatible=inspection?.supported===true&&Number.isInteger(inspection.contextTokens)&&inspection.contextTokens>=8192;
    const option=(key,value)=>setOptions(current=>({...current,[key]:value}));
    const useModel=()=>act('use',async()=>{if(!run?.runtimeReady||run.status!=='accepted')return;
      const catalog=await deps.refreshCatalog(),entry=catalog.catalog?.find(value=>value.id===run.modelId);
      if(!entry?.runtimeReady)throw Error('This accepted model is no longer installed and ready.');
      const profile=await deps.register(entry);const state=await deps.invoke('get_snapshot');
      if(state?.activeConversationIds?.length)throw Error('The model is available in your chat model menu. Finish the active chat before selecting it.');
      await deps.invoke('start_profile',{request:{profile,attachUrl:null}});
      onNotice('The verified AutoLoop model is selected for chat. The original model is still available.');
    });
    const numberField=(key,label,fallback,min,max)=>h('label',{key},label,h('input',{'aria-label':label,type:'number',min,max,value:options[key]??fallback,disabled:!!snapshot.activeRunId,onChange:event=>option(key,Number(event.target.value))}));
    return h('section',{className:'autoloop-panel','aria-label':'AutoLoop training-free optimization'},
      h('div',{className:'learning-card autoloop-intro'},h('h2',null,'AutoLoop · No training'),h('p',null,'Try repeated transformer layers, test quality, and keep a separate model only if it beats the original on independent confirmation questions.'),
        h('p',{className:'learning-muted'},'The bundled 48-question test checks elementary reasoning and exact answers. It does not prove better coding or overall intelligence. Your source model stays unchanged.')),
      error&&h('p',{className:'learning-error',role:'alert'},error),
      h('div',{className:'autoloop-columns'},
        h('div',{className:'learning-card'},h('h2',null,'Choose a model'),
          h('label',null,'Installed GGUF checkpoint',h('select',{'aria-label':'Installed GGUF checkpoint',value:models.some(value=>value.modelPath===modelPath)?modelPath:'',disabled:!!snapshot.activeRunId,onChange:event=>setModelPath(event.target.value)},
            h('option',{value:'',disabled:true},models.length?'Choose a checkpoint':'No installed GGUF checkpoints found'),models.map(value=>h('option',{key:value.modelPath,value:value.modelPath},value.label)))),
          h('div',{className:'learning-actions'},h('button',{disabled:!!busy||!!snapshot.activeRunId,onClick:()=>void act('choose',()=>choose('model'))},'Choose another GGUF')),
          modelPath&&h('p',{className:'autoloop-path'},modelPath),
          modelPath&&!inspection&&!error&&h('p',{role:'status'},'Checking architecture and layer layout…'),
          inspection&&h('p',{className:compatible?'autoloop-compatible':'learning-muted',role:'status'},compatible?`${inspection.architecture} · ${inspection.blockCount} layers · Compatible for an experiment`:inspection.reason||'This file cannot be safely promoted to OpenCore chat. Its architecture or context length is unsupported.'),
          h('details',null,h('summary',null,'Experiment settings'),h('div',{className:'learning-fields'},numberField('maxVariants','Candidate limit',3,1,8),numberField('maxMinutes','Time budget (minutes)',60,1,1440),numberField('maxDiskGiB','Candidate disk budget (GiB)',128,1,2048),numberField('threads','CPU threads',2,1,8)),
            h('p',{className:'learning-muted'},'Tests run in an isolated CPU process. No training, external account, or cloud upload is used. Repeated layers can increase file size and inference time.'),
            h('label',{className:'learning-checkbox'},h('input',{type:'checkbox',checked:options.experimentalDamping===true,disabled:!!snapshot.activeRunId,onChange:event=>option('experimentalDamping',event.target.checked)}),'Try experimental residual damping'),
            h('p',{className:'learning-muted'},'Damping is an approximation and can reduce quality. It still has to pass the same confirmation gate.'),
            h('div',{className:'learning-actions'},h('button',{disabled:!!snapshot.activeRunId||!!busy,onClick:()=>void act('benchmark',()=>choose('benchmark'))},'Use my own test questions'),options.benchmarkPath&&h('button',{disabled:!!snapshot.activeRunId,onClick:()=>option('benchmarkPath','')},'Use bundled test')),
            h('p',{className:'autoloop-path'},options.benchmarkPath||'Bundled test · 48 objective questions'),
            h('p',{className:'learning-muted'},'Custom JSON: an array of at least 48 objects with prompt, answer, and kind (exact or number). Use fresh questions with correct answers; development and confirmation questions are kept separate.')),
          h('div',{className:'learning-actions'},h('button',{className:'primary',disabled:!!busy||!!snapshot.activeRunId||!compatible,onClick:()=>void start()},busy==='start'?'Starting…':'Optimize and verify'))),
        h('div',{className:'learning-card autoloop-run'},h('h2',null,'Experiments'),!snapshot.runs.length&&h('p',{className:'learning-muted'},'No experiments yet. Models with no confirmed gain keep their original version.'),
          snapshot.runs.length>1&&h('label',null,'Select experiment',h('select',{'aria-label':'Select experiment',value:run?.id||'',onChange:event=>{setSelected(event.target.value);setReport(null)}},snapshot.runs.map(value=>h('option',{key:value.id,value:value.id},value.name+' · '+value.status)))),
          run&&h('div',{'aria-live':'polite'},h('strong',null,run.name),h('p',{className:'learning-status '+run.status},stageName(run.stage)),
            !terminal.has(run.status)&&h('progress',{'aria-label':'Current AutoLoop test progress',...(run.total>0?{max:run.total,value:Math.min(run.completed||0,run.total)}:{})}),
            run.total>0&&h('p',null,`${run.split||'Test'} · ${run.candidate||'Original'} · ${run.completed||0} / ${run.total} questions`),
            h('p',{className:'learning-muted'},`Elapsed ${Math.max(0,Math.floor(((terminal.has(run.status)?run.updatedAt*1000:now)-run.createdAt*1000)/1000))}s`),
            run.detail&&h('p',null,run.detail),run.reason&&h('p',null,run.reason),run.error&&h('p',{className:'learning-error'},run.error),
            run.status==='rejected'&&h('p',null,'No verified gain. Candidate weights were discarded; the original model remains available.'),
            run.status==='accepted'&&h('p',null,run.qualityScope||'Improvement confirmed on the selected benchmark only.'),
            run.runtimeReady&&run.status==='accepted'&&h('button',{className:'primary',disabled:!!busy,onClick:()=>void useModel()},busy==='use'?'Selecting model…':'Use verified model in chat'),
            snapshot.activeRunId===run.id&&h('button',{disabled:!!busy||run.status==='cancelling',onClick:()=>void act('cancel',async()=>{await request('/autoloop/cancel',{runId:run.id});await refresh()})},run.status==='cancelling'?'Stopping…':'Stop experiment'),
            terminal.has(run.status)&&h('div',{className:'learning-actions'},h('button',{disabled:!!busy,onClick:()=>void act('report',async()=>{const value=await request('/autoloop/report',{runId:run.id});if(alive.current)setReport(value)})},'Show exact quality report')),
            report&&h('details',{open:true},h('summary',null,'Scores, raw answers and provenance'),h('pre',null,JSON.stringify(report,null,2)))))));
  };
}
