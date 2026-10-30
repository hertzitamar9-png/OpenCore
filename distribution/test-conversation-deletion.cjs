const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function fixture(){
  const native=[{id:'claude-uuid',client:'Claude Code Mods'},{id:'codex-uuid',client:'Codex SDK'},{id:'local-uuid',client:'OpenCore'}];
  const imported=Array.from({length:205},(_,n)=>({id:'import:'+n,client:n===0?'Imported Claude Code':'Imported JSON'}));
  const state={native,imported,active:[],operations:[],calls:[],removed:[],fail:null};
  const context={globalThis:null,document:{head:{appendChild(){}},createElement:()=>({})},setTimeout,clearTimeout,CustomEvent:class{},dispatchEvent(){},p:async(command,args={})=>{
    state.calls.push({command,args});
    if(command==='get_snapshot')return {activeConversationIds:state.active};
    if(command==='list_operations')return state.operations;
    if(command==='list_conversations')return state.native.slice(0,500).map(row=>({...row}));
    if(command==='list_saved_conversations'){
      const all=state.native.filter(row=>row.id>(args.after||'')).sort((a,b)=>a.id.localeCompare(b.id));
      return {conversations:all.slice(0,args.limit).map(row=>({...row})),nextCursor:all.length>args.limit?all[args.limit-1].id:null};
    }
    if(command==='list_imported_conversations')return state.badPage?null:{conversations:state.imported.slice(args.offset,args.offset+args.limit).map(row=>({...row})),total:state.imported.length,offset:args.offset,limit:args.limit};
    if(command==='delete_conversation'){
      if(args.id===state.fail)throw Error('Deletion blocked by active image generation');
      state.removed.push(args.id);state.native=state.native.filter(row=>row.id!==args.id);state.imported=state.imported.filter(row=>row.id!==args.id);return;
    }
    throw Error('Unexpected command '+command);
  }};
  context.globalThis=context;vm.runInNewContext(fs.readFileSync(path.join(__dirname,'conversation-deletion.js'),'utf8'),context);
  return {api:context.__opencoreChatDeletion,state};
}
test('source deletion includes UUID Claude Mods and exported Claude chats without touching Codex or local chats',async()=>{
  const {api,state}=fixture();const plan=await api.plan('claude-code');
  assert.deepEqual(Array.from(plan.items,row=>row.id).sort(),['claude-uuid','import:0']);
  await api.execute(plan);assert.deepEqual(state.removed.sort(),['claude-uuid','import:0']);
  assert.ok(state.native.some(row=>row.id==='codex-uuid'));assert.ok(state.native.some(row=>row.id==='local-uuid'));
});
test('all-chat deletion plans every imported page, deduplicates overlaps and never deletes a later new chat',async()=>{
  const {api,state}=fixture();state.native.push({...state.imported[0]},...Array.from({length:620},(_,n)=>({id:'older-'+String(n).padStart(4,'0'),client:'OpenCore'})));const plan=await api.plan('all');
  assert.equal(plan.items.length,828);assert.deepEqual(state.calls.filter(row=>row.command==='list_imported_conversations').map(row=>row.args.offset),[0,100,200]);
  state.native.push({id:'created-later',client:'OpenCore'});await api.execute(plan);
  assert.equal(state.removed.length,828);assert.deepEqual(state.native.map(row=>row.id),['created-later']);
});
test('active generation or history sync blocks deletion before any chat is changed',async()=>{
  for(const active of ['generation','sync']){
    const {api,state}=fixture();const plan=await api.plan('all');
    if(active==='generation')state.active=['local-uuid'];else state.operations=[{kind:'history_sync',status:'running'}];
    await assert.rejects(api.execute(plan),/Stop|Wait|finish/i);assert.deepEqual(state.removed,[]);
  }
});
test('a failed deletion stops the batch and reports exactly how many chats were already deleted',async()=>{
  const {api,state}=fixture();const plan=await api.plan('all');state.fail=plan.items[1].id;
  await assert.rejects(api.execute(plan),error=>error.deletedCount===1&&/active image/.test(error.message));
  assert.equal(state.removed.length,1);assert.ok(state.native.some(row=>row.id==='local-uuid'));
});
test('a new plan is required for unknown source names and malformed imported paging results',async()=>{
  const {api,state}=fixture();await assert.rejects(api.plan('something-else'),/source/i);
  state.badPage=true;await assert.rejects(api.plan('all'),/completely/);assert.deepEqual(state.removed,[]);
});
