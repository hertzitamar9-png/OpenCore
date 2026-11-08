const fs=require('node:fs'),path=require('node:path');
module.exports=function applyContextActivity(app,{editFunction,replaceOnce}){
  app=replaceOnce(app,'i.jsxs("span",{className:"composer-token-speed",children:[','i.jsx(globalThis.__opencoreOutputStats,{conversationId:e,fallback:[');
  app=editFunction(app,'v8',text=>{
    text=replaceOnce(text,'const d=','const $live=globalThis.__opencoreLiveFooter(t,r,$hubRequest,e);t=$live.snapshot;const d=');
    text=replaceOnce(text,'" tokens/s"]','" tokens/s",t.telemetry.completionTokens?` · ${t.telemetry.completionTokens} out`:""]');
    return replaceOnce(text,'conversationId:r,profile:d,runtime:t.runtime','conversationId:r,profile:d,runtime:t.runtime,liveContext:$live.context');
  });
  app=replaceOnce(app,'Optional Q8_0 multimodal coding model · ECHO archive · 16K attention','Optional Q8_0 multimodal coding model · ECHO archive · 32K attention');
  app=replaceOnce(app,'Optional Q4_K_M with MTP draft head · ECHO archive · 16K attention','Optional Q4_K_M with MTP draft head · ECHO archive · 32K attention');
  app=editFunction(app,'g8',()=>fs.readFileSync(path.join(__dirname,'context-window.js'),'utf8'));
  // Only background history polls use this merge. Explicit deletion/reload still
  // replaces the authoritative history and unmounts a deleted conversation.
  app=editFunction(app,'zv',()=>`function zv(previous,incoming){
    if(previous.length&&incoming.length&&previous[0].conversationId!==incoming[0].conversationId)return incoming;
    const entries=new Map(previous.map(entry=>[entry.id,entry]));
    for(const entry of incoming)entries.set(entry.id,entry);
    const merged=[...entries.values()].sort((a,b)=>a.id-b.id);
    return JSON.stringify(previous)===JSON.stringify(merged)?previous:merged;
  }`);
  // The composer and its parent poll independently; neither may discard entries
  // already delivered by the other while a turn is running.
  app=replaceOnce(app,'ae=ye.length?ye:l.filter(he=>he.metadata.live!==!0)','ae=zv(ye,l.filter(he=>he.metadata.live!==!0))');
  app=editFunction(app,'FI',()=> 'function FI(state,payload){return $toolProgress(state,payload)}');
  app=editFunction(app,'EI',()=> 'function EI(entries,progress){return $toolTimeline(entries,progress)}');
  app=replaceOnce(app,'[dn,Vt]=p.useState({})','[dn,Vt]=p.useState(()=>$toolPreviewState(e))');
  app=replaceOnce(app,'Vt({})},[e])','Vt($toolPreviewState(e)),xt([])},[e])');
  app=replaceOnce(app,'It("opencore-tool-progress",','$listenToolProgress(');
  app=replaceOnce(app,'Vt(De=>FI(De,he))','Vt($toolPreviewState(he.conversationId))');
  app=editFunction(app,'YI',text=>{
    text=replaceOnce(text,'c.role==="assistant"&&c.metadata.phase==="reviewing"||','');
    text=replaceOnce(text,'if(c.metadata.phase==="reviewing")continue;','');
    text=replaceOnce(text,'const d=c.content.trim()','const d=$modelText(c.content).trim()');
    text=replaceOnce(text,'b.content.trim().startsWith(d)','$modelText(b.content).trim().startsWith(d)');
    return replaceOnce(text,'if(g&&x)return g===x;const v=m.content.trim();return!!d&&(v===d||d.length>=48&&v.startsWith(d))',
      'const v=$modelText(m.content).trim();return !!d&&v===d||(g&&x?g===x:d.length>=48&&v.startsWith(d))');
  });
  app=editFunction(app,'w8',text=>{
    const update='Tt.current===Fe.conversationId&&Q(ae)';
    if(text.split(update).length!==3)throw Error('Expected checkpoint and final history updates');
    text=text.replaceAll(update,'Tt.current===Fe.conversationId&&Q(previous=>zv(previous,ae))');
    text=replaceOnce(text,'?void 0:he))','?$mergeGenerationPreview(he,Fe,ae):he))');
    text=replaceOnce(text,'Fe.preservePreview?{...Fe,checkpoint:void 0}:{...Fe,checkpoint:void 0,segments:[],content:"",reasoning:""}','$mergeGenerationPreview(he,Fe,ae)');
    text=replaceOnce(text,'const A=++ve;Fe.done?','const A=$generationRevision(Fe);Fe.done?');
    const settle='A===ve&&(he==null?void 0:he.runId)===Fe.runId?$mergeGenerationPreview(he,Fe,ae):he';
    if(text.split(settle).length!==3)throw Error('Expected checkpoint and completion reconciliation');
    text=text.replaceAll(settle,'$settleGenerationPreview(he,Fe,ae,A)');
    text=replaceOnce(text,':G(Fe)}).then',':G(previous=>$mergeGenerationPreview(previous,Fe))}).then');
    text=replaceOnce(text,'if(!y)return;let L=!0;const ne=()=>{document.hidden||d1(y)','if(!y)return;G($generationPreviewFor(y));let L=!0;const ne=()=>{document.hidden||d1(y)');
    text=replaceOnce(text,'live:!0,phase:E.phase','live:!0,previewComplete:!!E.done,phase:E.phase');
    return replaceOnce(text,'L&&ve&&G(ve)','L&&ve&&G(previous=>$mergeGenerationPreview(previous,ve))');
  });
  app=editFunction(app,'TO',text=>{
    text=replaceOnce(text,'c=new Map,d=new Set,u=','c=new Map,u=');
    const hideStart=text.indexOf('const j=g.content.trim()===C.trim()'),hideEnd=text.indexOf(';break}',hideStart);
    if(hideStart<0||hideEnd<0)throw Error('Missing narration suppression');
    text=text.slice(0,hideStart)+text.slice(hideEnd+1);
    text=replaceOnce(text,':d.has(m.key)?null:m.type==="reasoning"',':m.type==="reasoning"');
    text=replaceOnce(text,'f.metadata.live===!0','f.metadata.live===!0&&!f.metadata.previewComplete');
    text=replaceOnce(text,'f===n&&!h','h?Yp(h.content)==="running":f===n');
    const start=text.indexOf('const k=qu(x.title,x.content).label.replace('),end=text.indexOf(',y=Zv(x.content);',start);
    if(start<0||end<0)throw Error('Missing inferred tool-action label');
    text=text.slice(0,start)+'const y=Zv(x.content);'+text.slice(end+',y=Zv(x.content);'.length);
    text=replaceOnce(text,'i.jsx(pf,{content:m.entry.content})','i.jsx(pf,{content:$modelText(m.entry.content)})');
    return replaceOnce(text,'`I’ll ${k}','`Tool action: ${qu(x.title,x.content).label}');
  });
  app=editFunction(app,'_O',text=>replaceOnce(text,'e?`Using ${c}`:`Used ${c}`',
    'e?`Using ${c}`:t.some(({call,result})=>call.kind==="tool_call"&&!result)?`Requested ${c} · result missing`:`Used ${c}`'));
  app=editFunction(app,'uO',text=>{
    text=replaceOnce(text,'&&C.metadata.reasoningSource!=="summary"','');
    text=replaceOnce(text,'b=d.filter((C,j)=>d.findIndex(_=>_.content===C.content)===j).map(C=>C.content).join(`\n\n`),m=b,f=t.flatMap','f=t.flatMap');
    return replaceOnce(text,':[]),h=(n?n:"")',':[]),m=[...new Set([...d.map(C=>C.content),...f].filter(Boolean))].join("\\n\\n"),h=(n?n:"")');
  });
  return app+'\n'+fs.readFileSync(path.join(__dirname,'tool-progress.js'),'utf8')+'\n'+fs.readFileSync(path.join(__dirname,'generation-preview.js'),'utf8');
};
