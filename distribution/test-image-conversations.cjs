const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function setup(){const image={id:'opencore:image',title:'A cup',client:'OpenCore',updatedAt:'2026-10-28T01:00:00Z',messageCount:2,pinned:false};const calls=[];const context=vm.createContext({$hubDescriptor:{},$hubRequest:async(path,body)=>{calls.push({path,body});return path==='/images/conversations'?{conversations:[image]}:path==='/images/conversation'?{entries:[{id:-1,timestamp:'2026-10-28T01:00:00Z'}]}:{};}});vm.runInContext(fs.readFileSync(require.resolve('./image-conversation-ui.js'),'utf8'),context);return{invoke:context.__opencoreImageConversationInvoke,calls};}
test('image-only conversations join history without changing native rows',async()=>{const{invoke}=setup();const snapshot=await invoke('get_snapshot',undefined,async()=>({conversations:[{id:'native',updatedAt:'2026-10-27',pinned:false,messageCount:3}]}));assert.equal(snapshot.conversations.length,2);assert.equal(snapshot.conversations[0].id,'opencore:image');const merged=await invoke('get_snapshot',{},async()=>({conversations:[{id:'opencore:image',title:'User title',updatedAt:'2026-10-27',messageCount:3}]}));assert.equal(merged.conversations.length,1);assert.equal(merged.conversations[0].title,'User title');assert.equal(merged.conversations[0].messageCount,5);});
test('image entries are added to the same conversation and virtual actions persist',async()=>{const{invoke,calls}=setup();await invoke('get_snapshot',{},async()=>({conversations:[]}));const entries=await invoke('get_conversation',{id:'opencore:image'},async()=>[{id:5,timestamp:'2026-10-27'}]);assert.equal(entries.length,2);let nativeCalled=false;await invoke('rename_conversation',{id:'opencore:image',title:'My images'},async()=>{nativeCalled=true});assert.equal(nativeCalled,false);assert.equal(calls.at(-1).body.action,'rename');});
test('unrelated conversation operations retain the native path',async()=>{const{invoke}=setup();let called=false;await invoke('delete_conversation',{id:'other'},async()=>{called=true});assert.equal(called,true);});
test('deletion admission precedes native removal and managed images are purged afterward',async()=>{
  const calls=[],context=vm.createContext({$hubDescriptor:{},$hubRequest:async route=>{calls.push(route);return{};},__opencoreClearImagePreviews:()=>calls.push('clear previews')});
  vm.runInContext(fs.readFileSync(require.resolve('./image-conversation-ui.js'),'utf8'),context);
  await context.__opencoreImageConversationInvoke('delete_conversation',{id:'native-chat'},async()=>calls.push('native delete'));
  assert.deepEqual(calls,['/images/conversation/delete-check','native delete','/images/conversation/delete','clear previews']);
  calls.length=0;context.$hubRequest=async()=>{throw Error('Stop image generation');};
  await assert.rejects(context.__opencoreImageConversationInvoke('delete_conversation',{id:'native-chat'},async()=>calls.push('native delete')),/Stop image/);
  assert.deepEqual(calls,[]);
});
test('chat images stay out of the Studio gallery but remain available to the chat',async()=>{
  const {invoke}=setup(),jobs=[{id:'chat',category:'image',request:{conversationId:'one'}},{id:'studio',category:'image',request:{}},{id:'video',category:'video',request:{conversationId:'one'}}];
  const gallery=await invoke('list_studio_jobs',{},async()=>jobs);
  assert.deepEqual(gallery.map(job=>job.id),['studio','video']);
  assert.equal(await invoke('list_studio_jobs',{includeChatImages:true},async()=>jobs),jobs);
});
test('saved image history reconnects in the background after a renderer restart',async()=>{const context=vm.createContext({$hubDescriptor:null,localStorage:{getItem:()=> '1'},$hubConnect:async()=>{context.$hubDescriptor={};},$hubRequest:async()=>({conversations:[{id:'image',updatedAt:'2026-10-28',pinned:false}]})});vm.runInContext(fs.readFileSync(require.resolve('./image-conversation-ui.js'),'utf8'),context);const first=await context.__opencoreImageConversationInvoke('get_snapshot',{},async()=>({conversations:[]}));assert.equal(first.conversations.length,0);await Promise.resolve();const next=await context.__opencoreImageConversationInvoke('get_snapshot',{},async()=>({conversations:[]}));assert.equal(next.conversations[0].id,'image');});
test('an empty native draft gets the image title while a user title is retained',async()=>{const{invoke}=setup();const snapshot=await invoke('get_snapshot',{},async()=>({conversations:[{id:'opencore:image',title:'New conversation',messageCount:0,updatedAt:'2026-10-27'}]}));assert.equal(snapshot.conversations[0].title,'A cup');const renamed=await invoke('get_snapshot',{},async()=>({conversations:[{id:'opencore:image',title:'My title',messageCount:0,updatedAt:'2026-10-27'}]}));assert.equal(renamed.conversations[0].title,'My title');});

test('native image receipts render actual output images in the existing chat timeline',async()=>{
  const job={id:'01234567-abcd-1234-abcd-0123456789ab',category:'image',request:{conversationId:'chat',prompt:'A cup'},status:'completed',createdAt:'2026-10-28',outputs:['C:\\Studio\\image-1.png','C:\\Studio\\comfy-output\\original.png']};
  const context=vm.createContext({$hubDescriptor:null,window:{__TAURI_INTERNALS__:{invoke:async()=>[job]}}});
  vm.runInContext(fs.readFileSync(require.resolve('./image-conversation-ui.js'),'utf8'),context);
  const rows=await context.__opencoreImageConversationInvoke('get_conversation',{id:'chat'},async()=>[{id:8,role:'assistant',timestamp:'2026-10-28',metadata:{studioJobId:job.id},content:'Open Game Dev Studio'}]);
  assert.equal(rows.length,2);assert.match(rows.find(row=>row.role==='assistant').content,/!\[Generated image\]\(file:\/\/\/C:\/Studio\/image-1.png\)/);
  assert.doesNotMatch(rows.find(row=>row.role==='assistant').content,/comfy-output|Game Dev Studio/);
});

test('a verified memory receipt never marks a completed answer as interrupted',async()=>{
  const context=vm.createContext({$hubDescriptor:{},$hubRequest:async route=>route==='/memory/receipts'?{receipts:{7:{savedCount:1}}}:{entries:[]},window:{__TAURI_INTERNALS__:{invoke:async()=>[]}}});
  vm.runInContext(fs.readFileSync(require.resolve('./image-conversation-ui.js'),'utf8'),context);
  const rows=await context.__opencoreImageConversationInvoke('get_conversation',{id:'chat'},async()=>[{id:8,title:'Automatic memory',timestamp:'2026-10-28',metadata:{userEntryId:7,status:'failed'},content:'Failed'}]);
  assert.equal(rows[0].metadata.memoryRecovered,true);
  assert.equal(rows[0].metadata.recovered,undefined);
  assert.match(rows[0].content,/Saved 1 verified memory/);
});

test('image duration survives chat reload with the generated response',async()=>{
  const job={id:'01234567-abcd-1234-abcd-0123456789ab',category:'image',request:{conversationId:'chat',prompt:'A cup'},status:'completed',createdAt:'2026-10-28T12:00:00Z',updatedAt:'2026-10-28T12:02:13Z',outputs:['C:\\Studio\\image-1.png']};
  const context=vm.createContext({$hubDescriptor:null,window:{__TAURI_INTERNALS__:{invoke:async()=>[job]}}});
  vm.runInContext(fs.readFileSync(require.resolve('./image-conversation-ui.js'),'utf8'),context);
  for(let attempt=0;attempt<2;attempt++){
    const rows=await context.__opencoreImageConversationInvoke('get_conversation',{id:'chat'},async()=>[]);
    assert.equal(rows.find(row=>row.role==='assistant').metadata.responseDurationMs,133000);
  }
});
