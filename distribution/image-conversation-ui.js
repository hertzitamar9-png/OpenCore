/* Imported image conversations join the native history without modifying its database. */
let $imageConversations=[], $imageNativeIds=new Set();
globalThis.__opencoreImageConversationInvoke=async(command,args,run)=>{
  args=args||{};
  if(!['get_snapshot','list_conversations','get_conversation','rename_conversation','set_conversation_pinned','move_conversation_to_project','delete_conversation'].includes(command))return run();
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
    const existing=rows.map(row=>{const image=$imageConversations.find(image=>image.id===row.id);return image?{...row,messageCount:row.messageCount+image.messageCount,updatedAt:row.updatedAt>image.updatedAt?row.updatedAt:image.updatedAt}:row;});
    const combined=[...existing,...added].sort((a,b)=>Number(b.pinned)-Number(a.pinned)||b.updatedAt.localeCompare(a.updatedAt));
    return command==='get_snapshot'?{...native,conversations:combined}:combined;
  }
  if(command==='get_conversation'){
    const rows=await run();
    try{const extra=(await $hubRequest('/images/conversation',{id:args.id})).entries||[];return [...rows,...extra].sort((a,b)=>a.timestamp.localeCompare(b.timestamp)||a.id-b.id);}catch{return rows;}
  }
  const image=$imageConversations.some(row=>row.id===args.id);
  if(!image)return run();
  const action={rename_conversation:'rename',set_conversation_pinned:'pin',move_conversation_to_project:'project',delete_conversation:'delete'}[command];
  // Check admission before altering native history; image generation must finish first.
  await $hubRequest('/images/conversation/update',{...args,action});
  if($imageNativeIds.has(args.id))return run();
  return null;
};
