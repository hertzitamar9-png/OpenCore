// Installed once with the existing frontend feature bundle; no full model reads.
function useLiveFooter(snapshot, conversationId, request, selectedProfile) {
  const [sample, setSample] = React.useState(null);
  const runtime = snapshot.runtime;
  const profile=['running','starting'].includes(runtime.status)?runtime.profile:selectedProfile;
  const scope = JSON.stringify([conversationId, profile, runtime.status, runtime.echoPort]);
  React.useEffect(() => {
    let active = true, pending = false;
    const refresh = async () => {
      if (pending || document.hidden) return;
      pending = true;
      try {
        const value = await request('/telemetry', {runtime, conversationId, profile});
        if (active) {
          setSample({scope, value});
          const effective=value.runtime?{...runtime,...value.runtime}:runtime;
          const old=globalThis.__opencoreRuntimeSample;
          if(!old||old.profile!==profile||old.runtime?.status!==effective.status||old.runtime?.profile!==effective.profile){
            globalThis.__opencoreRuntimeSample={profile,runtime:effective};
            window.dispatchEvent(new Event('opencore-model-runtime'));
          }
          globalThis.__opencoreLiveMetrics={conversationId,active:!!value.context?.modelSessionActive,
            preview:value.context?.preview,loadStage:value.runtime?.loadStage,...value.telemetry};
          window.dispatchEvent(new Event('opencore-live-metrics'));
        }
      } catch {} finally { pending = false; }
    };
    refresh();
    const timer = setInterval(refresh, 1000);
    document.addEventListener('visibilitychange', refresh);
    return () => { active = false; clearInterval(timer); document.removeEventListener('visibilitychange', refresh); };
  }, [scope, request]);
  const value = sample?.scope === scope ? sample.value : null;
  return {snapshot: value ? {...snapshot,runtime:value.runtime?{...runtime,...value.runtime}:runtime,telemetry: {...snapshot.telemetry, ...value.telemetry}} : snapshot, context:value?.context};
}

function useLiveMetrics(conversationId) {
  const [sample,setSample]=React.useState(globalThis.__opencoreLiveMetrics);
  React.useEffect(()=>{
    const refresh=()=>setSample(globalThis.__opencoreLiveMetrics);
    window.addEventListener('opencore-live-metrics',refresh);
    return()=>window.removeEventListener('opencore-live-metrics',refresh);
  },[]);
  return sample&&sample.conversationId===conversationId?sample:null;
}

function LiveOutputStats({conversationId, fallback}) {
  const sample=useLiveMetrics(conversationId);
  const live=sample?.active&&sample.conversationId===conversationId&&Number.isFinite(sample.completionTokens);
  return React.createElement('span',{className:'composer-token-speed'},live?`${(sample.tokensPerSecond||0).toFixed(1)} output tokens/s · ${sample.completionTokens.toLocaleString()} output tokens`:fallback);
}

function modelDisplayText(value='') {
  return (value??'').replace(/^(?:\s*<\/?think>)+\s*/i,'');
}

function LiveModelActivity({conversationId, active, entries=[], fallback}) {
  const sample=useLiveMetrics(conversationId);
  const preview=sample?.preview;
  const lastUser=entries.findLastIndex(entry=>entry.kind==='message'&&entry.role==='user');
  const turn=lastUser>=0?entries[lastUser]:null;
  const key=turn?.metadata?.submissionId??turn?.id??null;
  const [draft,setDraft]=React.useState(null);
  React.useEffect(()=>{
    if(!active||!preview?.id||!key||(!preview.reasoning&&!preview.text))return;
    setDraft(previous=>{
      const records=previous?.key===key?previous.records:[];
      const prior=records.at(-1);
      if(prior?.id===preview.id&&prior.reasoning===preview.reasoning&&prior.text===preview.text)return previous;
      return {key,records:(prior?.id===preview.id?records.slice(0,-1):records).concat(preview).slice(-32)};
    });
  },[active,key,preview?.id,preview?.reasoning,preview?.text]);
  const phase=preview?.phase;
  const label=sample?.loadStage||({
    'reading-prompt':'Reading prompt and context…',
    'preparing-tool':'Preparing tool call…',
    working:'Generating…',
    reviewing:'Checking the response…'
  })[phase];
  const h=React.createElement;
  const saved=entries.slice(lastUser+1).filter(entry=>entry.role==='assistant');
  const isSaved=(text,reasoning)=>saved.some(entry=>{
    const isReasoning=['generation','thinking'].includes(entry.kind);
    return isReasoning===reasoning&&modelDisplayText(entry.content).includes(text);
  });
  const records=draft?.key===key?draft.records:[];
  const visible=records.map(record=>{
    const text=modelDisplayText(record.text?.split('<tool_call>')[0]).trim();
    const reasoning=modelDisplayText(record.reasoning).trim();
    return {id:record.id,text:text&&!isSaved(text,false)?text:null,
      reasoning:reasoning&&!isSaved(reasoning,true)?reasoning:null};
  }).filter(record=>record.text||record.reasoning);
  const blocks=visible.map(record=>h('div',{className:'assistant-response live-model-preview',key:record.id},
      record.reasoning?h('details',{className:'assistant-disclosure kind-thinking reasoning-disclosure',open:true},
        h('summary',null,h('span',{'aria-hidden':true},'◇'),h('strong',null,active&&record.id===preview?.id?'Reasoning':'Reasoned'),
          h('span',null,'Streamed model reasoning')),
        h('div',{className:'reasoning-output-body'},h('p',{className:'reasoning-text',style:{whiteSpace:'pre-wrap'}},record.reasoning))):null,
      record.text?h('p',{className:'assistant-progress'},record.text):null));
  const progress=phase==='reading-prompt'?preview.promptProgress:null;
  const promptStatus=progress?h('div',{className:'generation-state prompt-read-progress',role:'status'},
    h('progress',{'aria-label':'Reading prompt and context',value:progress.processed,max:progress.total}),
    `${progress.processed.toLocaleString()} / ${progress.total.toLocaleString()} tokens · ${Math.floor(100*progress.processed/progress.total)}%`):null;
  return h(React.Fragment,null,
    active?blocks:blocks.length>0&&h('details',{className:'assistant-disclosure kind-thinking'},
      h('summary',null,h('span',{'aria-hidden':true},'◇'),h('strong',null,'Uncommitted model output')),...blocks),
    active?(label?h('div',{className:'generation-state',role:'status'},h('span',{className:'generation-pulse'}),label):fallback):null,
    active&&promptStatus);
}
