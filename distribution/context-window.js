function g8({conversationId:t,profile:e,runtime:n}){
  const [s,r]=p.useState(null);
  p.useEffect(()=>{
    r(null);if(!t)return;
    let active=true,pending=false;
    const refresh=async()=>{if(pending)return;pending=true;try{const value=await tp(t);if(active)r(value)}catch{}finally{pending=false}};
    refresh();const timer=['running','starting'].includes(n.status)?window.setInterval(refresh,1500):null;
    return()=>{active=false;if(timer!==null)window.clearInterval(timer)};
  },[t,e,n.profile,n.status]);
  const running=n.status==='running'&&n.profile===e;
  const capacity=running&&n.contextSize>0?n.contextSize:s?.modelContextTokens||s?.windowTokens;
  const windowTokens=s?.contextMode==='persistent_echo'&&s.windowTokens>0?Math.min(capacity||s.windowTokens,s.windowTokens):capacity;
  const maximum=Math.max(1,windowTokens||1);
  const slot=s?.modelActiveTokens;
  const reported=typeof slot==='number'&&Number.isFinite(slot)?slot:s?.available?s.promptTokens??s.liveTokens:undefined;
  const known=windowTokens>0&&typeof reported==='number'&&Number.isFinite(reported)&&reported>=0;
  const used=known?Math.min(maximum,reported):undefined;
  const kind=typeof slot==='number'&&s?.modelUsageKind!=='prompt'?(running&&s?.active?'live':'last'):s?.active?'prompt':'last';
  const idle=n.status==='starting'?'Model loading':'Model not running';
  const title=known?`${Kp(e)?'ECHO rolling context':'Model context'}: ${used.toLocaleString()} / ${maximum.toLocaleString()} tokens (${kind==='prompt'?'latest prompt; token-by-token slot usage is unavailable':kind==='live'?'live backend usage':'last reported usage'}).`:`${running?'Waiting for context usage from the model':idle}. ${windowTokens?`Context capacity: ${maximum.toLocaleString()} tokens.`:''}`;
  return i.jsxs('div',{className:'statusbar-context',title,'aria-label':'Live context window',children:[
    i.jsx('span',{className:'statusbar-context-label',children:'Context'}),
    i.jsx('progress',{'aria-label':'Model context usage',value:used,max:maximum}),
    i.jsx('span',{className:'statusbar-context-reading',children:windowTokens?`${known?hr(used):'—'} / ${hr(maximum)}${known?' '+kind:''}`:idle})
  ]});
}
