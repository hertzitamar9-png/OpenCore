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
  app=replaceOnce(app,'Vt({})},[e])','Vt({}),xt([])},[e])');
  app=editFunction(app,'YI',text=>{
    text=replaceOnce(text,'c.role==="assistant"&&c.metadata.phase==="reviewing"||','');
    return replaceOnce(text,'if(c.metadata.phase==="reviewing")continue;','');
  });
  app=editFunction(app,'w8',text=>{
    const update='Tt.current===Fe.conversationId&&Q(ae)';
    if(text.split(update).length!==3)throw Error('Expected checkpoint and final history updates');
    text=text.replaceAll(update,'Tt.current===Fe.conversationId&&Q(previous=>zv(previous,ae))');
    text=replaceOnce(text,'Fe.preservePreview?{...Fe,checkpoint:void 0}:{...Fe,checkpoint:void 0,segments:[],content:"",reasoning:""}','$mergeGenerationPreview(he,Fe,ae)');
    text=replaceOnce(text,':G(Fe)}).then',':G(previous=>$mergeGenerationPreview(previous,Fe))}).then');
    return replaceOnce(text,'L&&ve&&G(ve)','L&&ve&&G(previous=>$mergeGenerationPreview(previous,ve))');
  });
  return app+'\n'+fs.readFileSync(path.join(__dirname,'generation-preview.js'),'utf8');
};
