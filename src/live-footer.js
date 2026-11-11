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

function LiveReasoningIcon(){
  const h=React.createElement;
  return h('svg',{xmlns:'http://www.w3.org/2000/svg',width:14,height:14,viewBox:'0 0 24 24',fill:'none',stroke:'currentColor',strokeWidth:2,strokeLinecap:'round',strokeLinejoin:'round',className:'lucide lucide-brain-circuit','aria-hidden':true},
    ...['M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z','M9 13a4.5 4.5 0 0 0 3-4','M6.003 5.125A3 3 0 0 0 6.401 6.5','M3.477 10.896a4 4 0 0 1 .585-.396','M6 18a4 4 0 0 1-1.967-.516','M12 13h4','M12 18h6a2 2 0 0 1 2 2v1','M12 8h8','M16 8V5a2 2 0 0 1 2-2'].map((d,key)=>h('path',{d,key})),
    ...[[16,13],[18,3],[20,21],[20,8]].map(([cx,cy])=>h('circle',{cx,cy,r:.5,key:cx+':'+cy})));
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
  const normalized=value=>modelDisplayText(value).replace(/\s+/g,' ').trim();
  const isSaved=(text,reasoning)=>saved.some(entry=>{
    const isReasoning=['generation','thinking'].includes(entry.kind);
    return isReasoning===reasoning&&normalized(entry.content).includes(normalized(text));
  })||reasoning&&normalized(saved.filter(entry=>['generation','thinking'].includes(entry.kind)).map(entry=>entry.content).join('\n\n')).includes(normalized(text));
  const records=draft?.key===key?draft.records:[];
  const visible=records.map(record=>{
    const text=modelDisplayText(record.text?.split('<tool_call>')[0]).trim();
    const reasoning=modelDisplayText(record.reasoning).trim();
    return {id:record.id,text:text&&!isSaved(text,false)?text:null,
      reasoning:reasoning&&!isSaved(reasoning,true)?reasoning:null};
  }).filter(record=>record.text||record.reasoning);
  const blocks=visible.map(record=>h('div',{className:'assistant-response live-model-preview',key:record.id},
      record.reasoning?h('details',{className:'assistant-disclosure kind-thinking reasoning-disclosure',open:active&&record.id===preview?.id},
        h('summary',null,h(LiveReasoningIcon),h('strong',null,active&&record.id===preview?.id?'Reasoning':'Reasoned'),
          h('span',null,'Streamed model reasoning')),
        h('div',{className:'reasoning-output-body'},h('p',{className:'reasoning-text',style:{whiteSpace:'pre-wrap'}},record.reasoning))):null,
      record.text?h('p',{className:'assistant-progress'},record.text):null));
  const progress=phase==='reading-prompt'?preview.promptProgress:null;
  const promptStatus=progress?h('div',{className:'generation-state prompt-read-progress',role:'status'},
    h('progress',{'aria-label':'Reading prompt and context',value:progress.processed,max:progress.total}),
    `${progress.processed.toLocaleString()} / ${progress.total.toLocaleString()} tokens · ${Math.floor(100*progress.processed/progress.total)}%`):null;
  return h(React.Fragment,null,
    ...blocks,
    active?(label?h('div',{className:'generation-state',role:'status'},h('span',{className:'generation-pulse'}),label):fallback):null,
    active&&promptStatus);
}
