/* Imported image conversations join the native history without modifying its database. */
let $imageConversations=[], $imageNativeIds=new Set();
async function $imageInlineEntries(rows,identity){
  try{
    const native=await window.__TAURI_INTERNALS__.invoke('list_studio_jobs',{}),hub=globalThis.__opencoreImageHub;
    const jobs=[...native,...(hub?await hub.list():[])].filter(job=>job.category==='image'&&job.request.conversationId===identity&&!job.hidden&&!job.internalVerification);
    const output=rows.slice();
    for(const job of jobs){
      if(!output.some(row=>row.role==='user'&&(row.metadata?.studioJobId===job.id||row.metadata?.hubImageJobId===job.id||(row.content===job.request.prompt&&Math.abs(Date.parse(row.timestamp)-Date.parse(job.createdAt))<10000))))output.push({id:-parseInt(job.id.replace(/[^a-f0-9]/gi,'').slice(-11),16)-1,conversationId:identity,source:'OpenCore',kind:'message',role:'user',title:'You',timestamp:job.createdAt,content:job.request.settings?.originalPrompt||job.request.prompt,metadata:{studioJobId:job.id,files:job.request.settings?.referencePaths||[]}});
      const index=output.findIndex(row=>row.role==='assistant'&&(row.metadata?.studioJobId===job.id||row.metadata?.hubImageJobId===job.id));
      const files=(job.outputs||[]).filter(path=>/\.(png|jpe?g|webp)$/i.test(path)&&!/[\\/](comfy-output|comfy-inputs|reference-inputs)[\\/]/i.test(path));
      const content=job.status==='completed'?'Image generated.'+(job.request.settings?.promptRecap?'\n\nRequest summary: '+job.request.settings.promptRecap:'')+'\n\n'+files.map(path=>'![Generated image]('+encodeURI('file:///'+path.replace(/\\/g,'/').replace(/^\//,'')).replace(/[()]/g,c=>'%'+c.charCodeAt(0).toString(16))+')').join('\n\n'):job.error?'Image generation failed. '+job.error:(job.stage||'Generating image…');
      const previous=index>=0?output[index]:null;
      const row={...(previous||{}),id:previous?.id||-parseInt(job.id.replace(/[^a-f0-9]/gi,'').slice(-11),16),conversationId:identity,source:'OpenCore',kind:'message',role:'assistant',title:'OpenCore',timestamp:job.updatedAt||job.createdAt,content,metadata:{...previous?.metadata,studioJobId:job.id,studioReceipt:true,files:[]}};
      const elapsed=Date.parse(job.updatedAt||'')-Date.parse(job.createdAt||'');
      if(['completed','failed','cancelled','interrupted'].includes(job.status)&&Number.isFinite(elapsed)&&elapsed>=0)row.metadata={...row.metadata,responseDurationMs:elapsed,responseStatus:job.status};
      if(index>=0)output[index]=row;else output.push(row);
    }
    return output.sort((a,b)=>a.timestamp.localeCompare(b.timestamp)||a.id-b.id);
  }catch{return rows;}
}
globalThis.__opencoreImageConversationInvoke=async(command,args,run)=>{
  args=args||{};
  if(command==='list_studio_jobs'){
    const jobs=await run();return args.includeChatImages?jobs:jobs.filter(job=>job.category!=='image'||!job.request?.conversationId);
  }
  if(!['get_snapshot','list_conversations','get_conversation','rename_conversation','set_conversation_pinned','move_conversation_to_project','delete_conversation'].includes(command))return run();
  if(command==='delete_conversation'){
    if(!$hubDescriptor&&typeof $hubConnect==='function')await $hubConnect();
    if(!$hubDescriptor)throw Error('OpenCore is reconnecting its local image storage. Please retry deleting the chat.');
    await $hubRequest('/images/conversation/delete-check',{id:args.id});
    if($imageNativeIds.has(args.id)||!$imageConversations.some(row=>row.id===args.id))await run();
    await $hubRequest('/images/conversation/delete',{id:args.id});
    $imageConversations=$imageConversations.filter(row=>row.id!==args.id);
    globalThis.__opencoreClearImagePreviews?.();
    globalThis.dispatchEvent?.(new CustomEvent('opencore-chat-media-deleted',{detail:{conversationId:args.id}}));
    return null;
  }
  if(command==='get_conversation'){
    const rows=await run();let extra=[];
    try{if($hubDescriptor)extra=(await $hubRequest('/images/conversation',{id:args.id})).entries||[];}catch{}
    let result=await $imageInlineEntries([...rows,...extra],args.id);
    if($hubDescriptor&&result.some(row=>row.title==='Automatic memory'))try{
      const {receipts}=await $hubRequest('/memory/receipts',{conversationId:args.id});
      result=result.map(row=>{const saved=receipts?.[row.metadata?.userEntryId];return row.title==='Automatic memory'&&saved?{...row,content:'Saved '+saved.savedCount+' verified '+(saved.savedCount===1?'memory':'memories')+' from your message.',metadata:{...row.metadata,status:'complete',savedCount:saved.savedCount,memoryRecovered:true}}:row;});
    }catch{}
    return result;
  }
  if(!$hubDescriptor){
    try{if(command==='get_snapshot'&&localStorage.getItem('opencore.image-chat-history')==='1')void $hubConnect().catch(()=>{});}catch{}
    return run();
  }
  if(command==='get_snapshot'||command==='list_conversations'){
    const native=await run();
    try{$imageConversations=(await $hubRequest('/images/conversations')).conversations||[];}catch{return native;}
    const rows=command==='get_snapshot'?native.conversations:native;
    if(!Array.isArray(rows))return native;
    $imageNativeIds=new Set(rows.map(row=>row.id));
    const query=String(args.query||'').toLowerCase();
    const added=$imageConversations.filter(row=>!$imageNativeIds.has(row.id)&&(!query||row.title.toLowerCase().includes(query)));
    const existing=rows.map(row=>{const image=$imageConversations.find(image=>image.id===row.id);return image?{...row,title:row.messageCount===0&&/^(new conversation|new chat|untitled)$/i.test(row.title)?image.title:row.title,messageCount:row.messageCount+image.messageCount,updatedAt:row.updatedAt>image.updatedAt?row.updatedAt:image.updatedAt}:row;});
    const combined=[...existing,...added].sort((a,b)=>Number(b.pinned)-Number(a.pinned)||b.updatedAt.localeCompare(a.updatedAt));
    return command==='get_snapshot'?{...native,conversations:combined}:combined;
  }
  const image=$imageConversations.some(row=>row.id===args.id);
  if(!image)return run();
  const action={rename_conversation:'rename',set_conversation_pinned:'pin',move_conversation_to_project:'project',delete_conversation:'delete'}[command];
  // Check admission before altering native history; image generation must finish first.
  await $hubRequest('/images/conversation/update',{...args,action});
  if($imageNativeIds.has(args.id))return run();
  return null;
};
