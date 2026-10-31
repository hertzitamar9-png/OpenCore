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
          globalThis.__opencoreLiveMetrics={conversationId,active:!!value.context?.modelSessionActive,...value.telemetry};
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

function LiveOutputStats({conversationId, fallback}) {
  const [sample,setSample]=React.useState(globalThis.__opencoreLiveMetrics);
  React.useEffect(()=>{
    const refresh=()=>setSample(globalThis.__opencoreLiveMetrics);
    window.addEventListener('opencore-live-metrics',refresh);
    return()=>window.removeEventListener('opencore-live-metrics',refresh);
  },[]);
  const live=sample?.conversationId===conversationId&&sample.active&&Number.isFinite(sample.completionTokens);
  return React.createElement('span',{className:'composer-token-speed'},live?`${(sample.tokensPerSecond||0).toFixed(1)} output tokens/s · ${sample.completionTokens.toLocaleString()} output tokens`:fallback);
}
