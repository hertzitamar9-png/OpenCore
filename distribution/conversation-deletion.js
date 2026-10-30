// Shared deletion controls for desktop and phone conversations.
const $chatSources={all:'All chats',opencore:'OpenCore',claude:'Claude Code',codex:'Codex',hermes:'Hermes',opencode:'OpenCode',imported:'Other imported chats'};
const $chatPlans=new WeakSet();let $chatDeleting=false;
const $chatCount=count=>count+' '+(count===1?'chat':'chats');
function $chatSource(value){const key=value==='claude-code'?'claude':value;if(!Object.hasOwn($chatSources,key))throw Error('Choose a valid conversation source.');return key;}
function $chatMatches(row,source){
  const client=String(row.client||'').toLowerCase();
  if(source==='all')return true;
  if(['claude','codex','hermes','opencode'].includes(source))return client.includes(source);
  if(source==='opencore')return client.includes('opencore')&&!client.startsWith('imported ');
  return client.startsWith('imported ')&&!['claude','codex','hermes','opencode'].some(key=>client.includes(key));
}
async function $chatDeletionPlan(value){
  const source=$chatSource(value),native=await p('list_conversations',{query:null});
  if(!Array.isArray(native))throw Error('The saved chat list could not be read. Nothing was deleted.');
  const rows=new Map(native.map(row=>[row.id,row]));let offset=0,after=null;
  do{
    const page=await p('list_saved_conversations',{after,limit:200});
    if(!page||!Array.isArray(page.conversations)||(page.nextCursor!==null&&(typeof page.nextCursor!=='string'||page.nextCursor<=String(after||''))))throw Error('The saved chat list could not be read completely. Nothing was deleted.');
    for(const row of page.conversations)rows.set(row.id,row);
    after=page.nextCursor;
  }while(after!==null);
  while(true){
    const page=await p('list_imported_conversations',{query:'',offset,limit:100,source:'all'});
    if(!page||!Array.isArray(page.conversations)||!Number.isSafeInteger(page.total)||page.total<0||!Number.isSafeInteger(page.limit)||page.limit<1||page.offset!==offset)throw Error('The imported chat list could not be read completely. Nothing was deleted.');
    for(const row of page.conversations)rows.set(row.id,row);
    const next=offset+page.limit;if(next>=page.total)break;
    if(!page.conversations.length)throw Error('The imported chat list changed. Read the list again before deleting.');
    offset=next;
  }
  const items=[...rows.values()].filter(row=>$chatMatches(row,source)).map(row=>{
    if(typeof row.id!=='string'||!row.id.trim()||row.id.length>512)throw Error('A saved chat has an invalid identity. Nothing was deleted.');
    return Object.freeze({id:row.id,title:row.title,client:row.client});
  });
  const plan=Object.freeze({source,items:Object.freeze(items)});$chatPlans.add(plan);return plan;
}
async function $chatDeletionIdle(){
  const [snapshot,operations]=await Promise.all([p('get_snapshot',{}),p('list_operations',{})]);
  if(!snapshot||!Array.isArray(operations))throw Error('Chat activity could not be checked. Nothing was deleted.');
  if(snapshot.activeConversationIds?.length)throw Error('Stop the current generation before deleting chats.');
  if(operations.some(row=>row.kind==='history_sync'&&['queued','running'].includes(row.status)))throw Error('Wait for the history import to finish, or cancel it, before deleting chats.');
}
async function $executeChatDeletion(plan,progress=()=>{}){
  if(!$chatPlans.has(plan))throw Error('Read the chat list before confirming deletion.');
  if($chatDeleting)throw Error('Another chat deletion is still running.');
  $chatDeleting=true;let deleted=0;
  try{
    await $chatDeletionIdle();
    for(const row of plan.items){await p('delete_conversation',{id:row.id});deleted++;progress({deleted,total:plan.items.length});}
    const remaining=await $chatDeletionPlan(plan.source),planned=new Set(plan.items.map(row=>row.id));
    const left=remaining.items.filter(row=>planned.has(row.id)).length;
    if(left){deleted-=left;throw Error(left+' selected chats are still present. Retry deletion after the chat list refreshes.');}
    return {deleted,total:plan.items.length};
  }catch(reason){const error=Error('Deleted '+deleted+' of '+plan.items.length+' chats. '+String(reason?.message||reason));error.deletedCount=deleted;throw error;}
  finally{$chatDeleting=false;if(deleted){globalThis.dispatchEvent?.(new CustomEvent('opencore-connector-history-changed'));globalThis.dispatchEvent?.(new CustomEvent('opencore-chats-deleted'));}}
}
function $ChatDeletionControl({source='all',onDeleted}){
  const h=K.createElement,[open,setOpen]=K.useState(false),[selected,select]=K.useState('all'),[plan,setPlan]=K.useState(null),[reading,setReading]=K.useState(false),[working,setWorking]=K.useState(false),[message,setMessage]=K.useState(''),[count,setCount]=K.useState(0),dialog=K.useRef(null);
  K.useEffect(()=>{const element=dialog.current;if(open){element.showModal?element.showModal():element.setAttribute('open','');}else element.close?.();},[open]);
  K.useEffect(()=>{
    if(!open)return;let current=true;setPlan(null);setReading(true);setMessage('');
    $chatDeletionPlan(selected).then(value=>{if(current)setPlan(value);}).catch(error=>{if(current)setMessage(String(error.message||error));}).finally(()=>{if(current)setReading(false);});
    return()=>{current=false;};
  },[open,selected]);
  async function remove(){
    if(!plan||reading||working)return;setWorking(true);setCount(0);setMessage('');
    try{const result=await $executeChatDeletion(plan,value=>setCount(value.deleted));setMessage('Deleted '+$chatCount(result.deleted)+' from OpenCore and ECHO.');setPlan(null);onDeleted?.();}
    catch(error){setMessage(String(error.message||error));setPlan(null);if(error.deletedCount)onDeleted?.();}
    finally{setWorking(false);}
  }
  return h(K.Fragment,null,
    h('button',{type:'button',className:'chat-delete-menu',title:'Delete chats from any source','aria-label':'Delete conversations',onClick:()=>{select(Object.hasOwn($chatSources,source)?source:'all');setOpen(true);}},
      h('svg',{width:16,height:16,viewBox:'0 0 24 24',fill:'none',stroke:'currentColor',strokeWidth:1.8,'aria-hidden':true},h('path',{d:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7'}))),
    h('dialog',{ref:dialog,className:'chat-delete-dialog','aria-label':'Delete conversations',onCancel:event=>{if(working)event.preventDefault();else setOpen(false);},onClose:()=>{if(!working)setOpen(false);}},
      h('h2',null,'Delete conversations'),
      h('label',null,'Chats to delete',h('select',{'aria-label':'Chats to delete',value:selected,disabled:working,onChange:event=>select(event.target.value)},...Object.entries($chatSources).map(([key,label])=>h('option',{key,value:key},label)))),
      h('p',null,'Delete saved chats, their images and ECHO history from OpenCore. Original transcripts and exported files in other apps stay in place.'),
      reading?h('p',{role:'status'},'Reading all saved chats…'):plan?h('p',{role:'status'},plan.items.length+' chats selected. This includes every page, regardless of the current search.'):null,
      working?h(K.Fragment,null,h('progress',{max:plan.items.length||1,value:count,'aria-label':'Deleting conversations'}),h('p',{role:'status'},'Deleting '+count+' of '+plan.items.length+' chats…')):null,
      message?h('p',{role:'status'},message):null,
      h('footer',null,h('button',{type:'button',disabled:working,onClick:()=>setOpen(false)},plan?'Cancel':'Done'),plan?h('button',{type:'button',className:'danger',disabled:reading||working||!plan.items.length,onClick:()=>void remove()},working?'Deleting…':'Delete '+$chatCount(plan.items.length)):null)));
}
globalThis.__opencoreChatDeletion={plan:$chatDeletionPlan,execute:$executeChatDeletion,Control:$ChatDeletionControl,clear:async source=>{
  const plan=await $chatDeletionPlan(source),result=await $executeChatDeletion(plan);
  return 'Deleted '+result.deleted+' '+$chatSources[plan.source]+' '+(result.deleted===1?'conversation':'conversations')+' from OpenCore and ECHO.';
}};
const $chatDeleteStyle=document.createElement('style');
$chatDeleteStyle.textContent='.conversation-list-actions .chat-delete-menu{flex:0 0 32px;display:grid;place-items:center;padding:6px;min-width:32px}.chat-delete-dialog{width:min(420px,calc(100vw - 32px));max-height:calc(100dvh - 32px);overflow:auto;border:1px solid #65788c;border-radius:16px;padding:22px;background:var(--theme-panel,#111820);color:var(--theme-ink,#edf6ff)}.chat-delete-dialog::backdrop{background:#0009}.chat-delete-dialog label{display:grid;gap:8px}.chat-delete-dialog select{width:100%;padding:10px}.chat-delete-dialog p{line-height:1.5;overflow-wrap:anywhere}.chat-delete-dialog progress{width:100%}.chat-delete-dialog footer{display:flex;justify-content:flex-end;gap:10px}.chat-delete-dialog button{min-height:42px;padding:8px 14px}.chat-delete-dialog .danger{border:1px solid #d77b85;background:#60272f;color:#fff}';
document.head.appendChild($chatDeleteStyle);
