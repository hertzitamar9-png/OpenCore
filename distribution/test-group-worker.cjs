const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),{pathToFileURL}=require('node:url');
const load=()=>import(pathToFileURL(path.join(__dirname,'../src/group-chats.js')).href);
function fixture(options={}){
  const calls=[],records=new Map(),listeners={};
  let runtime={status:'stopped',profile:'old-model'};
  const task={id:'task-one',agentId:'agent-local',modelId:'model-a',text:'Complete this task',context:'Shared group context',leaseToken:'lease-local',conversationId:'group-dedicated',approvalMode:'allow-all'};
  const sharing={enabled:true,models:[{id:'model-a',label:'Model A',name:'Local helper'}],approvalMode:'ask-every-time',maxSubagents:2,...options.sharing};
  const api={
    service:async(action,body)=>{calls.push({service:action,body});
      if(action==='list')return{groups:options.noGroups?[]:[{id:'connection-local',name:'Friends'}]};
      if(action==='view')return{group:{id:'connection-local'},sharing};
      if(action==='worker/poll')return{jobs:[{connectionId:'connection-local',task}]};
      if(action==='worker/register'&&options.rejectRegister)throw Error('Invalid lease');
      return{};
    },
    invoke:async(command,args)=>{calls.push({command,args});
      if(command==='get_snapshot')return{activeConversationIds:options.busy?['private-conversation']:[],runtime,projects:options.projects||[]};
      if(command==='list_studio_jobs')return options.studioBusy?[{status:'running'}]:[];
      if(command==='list_model_library')return{models:[{id:'model-a',category:'text',installed:true,selectable:true}]};
      if(command==='api_provider_list')return options.providers||[];
      if(command==='create_conversation')return{id:'group-dedicated'};
      if(command==='start_profile'){runtime={status:'running',profile:args.request.profile};options.onStart?.(sharing);return{}}
      if(command==='get_conversation')return options.entries||[
        {id:1,conversationId:'private-conversation',kind:'message',role:'assistant',content:'PRIVATE MUST NEVER BE SHARED'},
        {id:2,conversationId:'group-dedicated',kind:'tool_call',role:'assistant',title:'Read task file',content:'Task progress'},
        {id:3,conversationId:'group-dedicated',kind:'message',role:'assistant',content:options.resultText||'Done locally'},
      ];
      if(command==='send_chat_message')return options.send?options.send():{conversationId:'group-dedicated'};
      return{};
    },
    listen:async(name,callback)=>{listeners[name]=callback;return()=>{}},
    storage:{getItem:key=>records.get(key)||null,setItem:(key,value)=>records.set(key,value)},
    schedule:options.schedule||(()=>1),cancel:()=>{},
  };
  return{api,calls,task,listeners};
}
test('no joined groups cause no native runtime probes or task claims',async()=>{
  const {createGroupWorker}=await load(),f=fixture({noGroups:true}),worker=createGroupWorker(f.api);
  await worker.poll();
  assert.deepEqual(f.calls.map(call=>call.service),['list']);
});

test('configured API model IDs are shared exactly and credentials are not copied into choices',async()=>{
  const {groupReadyModels}=await load();
  const providers=[{id:'vendor',name:'Vendor',apiKey:'PRIVATE',model:'fallback',models:[
    {id:'real-model',profileId:'api:vendor:7265616c',enabled:true,available:true,contextTokens:64000},
    {id:'disabled',profileId:'api:vendor:646973',enabled:false,available:true},
    {id:'missing',profileId:'api:vendor:6d6973',enabled:true,available:false}]}];
  const models=groupReadyModels({models:[]},providers);
  assert.deepEqual(models.map(model=>model.id),['api:vendor:7265616c']);
  assert.equal(models[0].executionKind,'api');
  assert.equal(JSON.stringify(models).includes('PRIVATE'),false);
});

test('the team builder can select supported catalog quants before they are installed',async()=>{
  const {groupReadyModels}=await load(),library={models:[
    {id:'catalog-one',label:'Local coder',precision:'Q4_K_M',category:'text',backend:'gguf',installed:false,selectable:true,installable:true,runtimeReady:true},
    {id:'unsupported',label:'Unintegrated architecture',category:'text',backend:'external',installed:false,selectable:false,runtimeReady:false},
    {id:'image',label:'Denoiser',category:'image',backend:'gguf',installed:false,selectable:true},
  ]};
  assert.equal(groupReadyModels(library).length,0);
  const choices=groupReadyModels(library,[],{includeInstallable:true});
  assert.deepEqual(choices.map(model=>model.id),['catalog-one']);
  assert.equal(choices[0].catalogId,'catalog-one');assert.equal(choices[0].ready,false);
  assert.ok(choices[0].label.includes('Q4_K_M'));
});

test('a missing local model prepares only with owner consent and uses the returned execution profile',async()=>{
  const {createGroupWorker}=await load();
  for(const consent of [false,true]){
    const f=fixture({sharing:{models:[{id:'catalog-model',key:'builder',name:'Builder',catalogId:'catalog-model',prepareIfMissing:consent}]}}),invoke=f.api.invoke;
    f.task.modelId='catalog-model';f.task.agentKey='builder';let preparations=0;
    f.api.invoke=async(command,args)=>{
      if(command==='list_model_library')return{models:[{id:'catalog-model',category:'text',backend:'gguf',installed:false,installable:true,selectable:true,runtimeReady:true}]};
      if(command==='group_prepare_model'){preparations++;assert.equal(args.config.catalogId,'catalog-model');return{profileId:'prepared-profile',catalogId:'catalog-model'}}
      return invoke(command,args);
    };
    await createGroupWorker(f.api).poll();
    assert.equal(preparations,consent?1:0);
    const selected=f.calls.find(call=>call.command==='start_profile');
    if(consent)assert.equal(selected.args.request.profile,'prepared-profile');else assert.equal(selected,undefined);
  }
});

test('agent execution uses its owner-selected effort tools approval and bounded subagents',async()=>{
  const {createGroupWorker}=await load(),f=fixture({sharing:{maxSubagents:2,models:[{
    id:'model-a',key:'role',name:'Role',reasoningEffort:'high',approvalMode:'allow-chat',
    subagentsEnabled:false,maxSubagents:8,skills:['browser-use']}]}});
  f.task.agentKey='role';await createGroupWorker(f.api).poll();
  const request=f.calls.find(call=>call.command==='send_chat_message').args.request;
  assert.equal(request.reasoningEffort,'high');assert.equal(request.approvalMode,'allow-chat');
  assert.equal(request.subagentsEnabled,false);assert.equal(request.maxSubagents,2);
  assert.deepEqual(request.skills,['browser-use']);
});

test('group requests may choose effort or stricter approval but never weaken the PC owner policy',async()=>{
  const {createGroupWorker}=await load();
  for(const [owner,requested,expected] of [['ask-every-time','allow-all','ask-every-time'],['allow-all','ask-every-time','ask-every-time'],['allow-chat','allow-all','allow-chat']]){
    const f=fixture({sharing:{approvalMode:owner}});f.task.requestSettings={approval:requested,effort:'high'};
    await createGroupWorker(f.api).poll();
    const request=f.calls.find(call=>call.command==='send_chat_message').args.request;
    assert.equal(request.approvalMode,expected);assert.equal(request.reasoningEffort,'high');
  }
});

test('worker runs an API agent through native provider selection without requiring downloaded weights',async()=>{
  const {createGroupWorker}=await load(),f=fixture({providers:[{id:'vendor',name:'Vendor',model:'model-one'}],
    sharing:{models:[{id:'api:vendor',key:'api-agent',name:'Helper',label:'API',instructions:'Review changes carefully'}]}});
  f.task.modelId='api:vendor';f.task.agentKey='api-agent';
  await createGroupWorker(f.api).poll();
  assert.equal(f.calls.find(call=>call.command==='start_profile').args.request.profile,'api:vendor');
  assert.ok(f.calls.find(call=>call.command==='send_chat_message').args.request.text.includes('Review changes carefully'));
  assert.equal(f.calls.some(call=>/install|download/i.test(call.command||'')),false);
});

test('worker uses only its local shared-project binding and creates a fresh native chat before assigning it',async()=>{
  const {createGroupWorker}=await load(),f=fixture({projects:[{id:'local-project',folderAvailable:true}],
    sharing:{projectBindings:{shared:'local-project'}}});
  f.task.projectId='shared';f.task.localProjectId='remote-project';f.task.conversationId='remote-guessed-chat';
  await createGroupWorker(f.api).poll();
  const create=f.calls.findIndex(call=>call.command==='create_conversation'),move=f.calls.findIndex(call=>call.command==='move_conversation_to_project'),send=f.calls.findIndex(call=>call.command==='send_chat_message');
  assert.ok(create>=0&&move>create&&send>move);
  assert.deepEqual(f.calls[move].args,{id:'group-dedicated',projectId:'local-project'});
  assert.equal(f.calls[send].args.request.conversationId,'group-dedicated');
});

test('owner-only instructions in the native user prompt are excluded from shared activity',async()=>{
  const {createGroupWorker}=await load(),f=fixture({entries:[
    {id:1,conversationId:'group-dedicated',kind:'message',role:'user',content:'OWNER_ONLY_INSTRUCTIONS'},
    {id:2,conversationId:'group-dedicated',kind:'tool_call',role:'assistant',content:'Read shared project'},
    {id:3,conversationId:'group-dedicated',kind:'message',role:'assistant',content:'Shared result'},
  ]});
  await createGroupWorker(f.api).poll();
  const updates=f.calls.filter(call=>call.service==='worker/update');
  assert.equal(JSON.stringify(updates).includes('OWNER_ONLY_INSTRUCTIONS'),false);
  assert.ok(JSON.stringify(updates).includes('Read shared project'));
});
test('foreground work and studio generation keep group tasks queued',async()=>{
  const {createGroupWorker}=await load();
  for(const options of [{busy:true},{studioBusy:true}]){
    const f=fixture(options),worker=createGroupWorker(f.api);await worker.poll();
    assert.equal(f.calls.some(call=>call.service==='worker/poll'),false);
    assert.equal(f.calls.some(call=>call.command==='start_profile'||call.command==='send_chat_message'),false);
  }
});
test('a claimed task registers before native execution and uses only local sharing policy',async()=>{
  const {createGroupWorker}=await load(),f=fixture(),worker=createGroupWorker(f.api);await worker.poll();
  const register=f.calls.findIndex(call=>call.service==='worker/register'),start=f.calls.findIndex(call=>call.command==='start_profile'),send=f.calls.findIndex(call=>call.command==='send_chat_message');
  assert.ok(register>=0&&register<start&&start<send);
  const request=f.calls[send].args.request;
  assert.equal(request.approvalMode,'ask-every-time');assert.equal(request.maxSubagents,2);
  assert.equal(request.conversationId,'group-dedicated');assert.equal(request.submissionId,'task-one');
  assert.equal(request.text,'Shared group context\nComplete this task');
  assert.deepEqual(request.files,[]);
  assert.equal(f.calls.some(call=>/focus|navigate/i.test(call.command||'')),false);
  assert.ok(f.calls.filter(call=>call.command==='get_conversation').every(call=>call.args.id==='group-dedicated'));
  const updates=f.calls.filter(call=>call.service==='worker/update');
  assert.ok(updates.some(call=>call.body.status==='completed'&&call.body.text==='Done locally'));
  assert.equal(JSON.stringify(updates).includes('PRIVATE MUST NEVER BE SHARED'),false);
});
test('replayed task delivery never executes the native task twice',async()=>{
  const {createGroupWorker}=await load(),f=fixture(),first=createGroupWorker(f.api);await first.poll();
  const afterRestart=createGroupWorker(f.api);await afterRestart.poll();
  assert.equal(f.calls.filter(call=>call.command==='send_chat_message').length,1);
  assert.equal(f.calls.filter(call=>call.command==='start_profile').length,1);
});
test('local approval policy is refreshed after model loading and before native submission',async()=>{
  const {createGroupWorker}=await load(),f=fixture({onStart:sharing=>{sharing.approvalMode='allow-chat';sharing.maxSubagents=1}});
  await createGroupWorker(f.api).poll();
  const request=f.calls.find(call=>call.command==='send_chat_message').args.request;
  assert.equal(request.approvalMode,'allow-chat');assert.equal(request.maxSubagents,1);
});

test('profile selection uses the fresh idle snapshot rather than the earlier poll snapshot',async()=>{
  const {createGroupWorker}=await load(),f=fixture(),invoke=f.api.invoke;let snapshots=0,selected='model-a';
  f.api.invoke=async(command,args)=>{
    if(command==='get_snapshot'){snapshots++;if(snapshots===2)selected='other-profile';return{activeConversationIds:[],runtime:{status:'running',profile:selected}}}
    if(command==='start_profile')selected=args.request.profile;
    if(command==='send_chat_message')assert.equal(selected,'model-a');
    return invoke(command,args);
  };
  await createGroupWorker(f.api).poll();
  assert.ok(f.calls.some(call=>call.command==='start_profile'&&call.args.request.profile==='model-a'));
  assert.ok(f.calls.some(call=>call.service==='worker/update'&&call.body.status==='completed'));
});

test('cancellation during model startup is checked again before any native submission',async()=>{
  const {createGroupWorker}=await load();let cancelled=false;
  const f=fixture({onStart:()=>{cancelled=true}}),service=f.api.service;
  f.api.service=async(action,body)=>{if(action==='worker/status'&&cancelled)throw Error('This group task has ended');return service(action,body)};
  await createGroupWorker(f.api).poll();
  assert.equal(f.calls.some(call=>call.command==='send_chat_message'),false);
});
test('managed model profile IDs are normalized and native calls pass through the install bridge',async()=>{
  const {groupReadyModels,invokeGroupNative}=await load();
  const library={models:[{id:'hf-card',category:'omni',hubEntry:{category:'text',profileId:'managed-profile'},label:'Managed',installed:true,selectable:true},
    {id:'managed-profile',category:'text',label:'Duplicate',installed:true,selectable:true},{id:'image',category:'image',installed:true,selectable:true}]};
  assert.deepEqual(groupReadyModels(library).map(model=>model.id),['managed-profile']);
  const previousNative=globalThis.__TAURI_INTERNALS__,previousBridge=globalThis.__opencoreInstallBridge,calls=[];
  try{
    globalThis.__TAURI_INTERNALS__={invoke:async(command,args)=>{calls.push({command,args});return'native'}};
    globalThis.__opencoreInstallBridge={invoke:async(command,args,run)=>{calls.push({bridge:command,args});return run()}};
    await invokeGroupNative('start_profile',{request:{profile:'managed-profile'}});
    assert.equal(calls[0].bridge,'start_profile');assert.equal(calls[1].command,'start_profile');
  }finally{globalThis.__TAURI_INTERNALS__=previousNative;globalThis.__opencoreInstallBridge=previousBridge;}
});
test('completion waits for studio work scoped to the dedicated conversation',async()=>{
  const {createGroupWorker}=await load();let submitted=false,studioPolls=0;
  const f=fixture({schedule:(callback,delay)=>{if(delay===1000)queueMicrotask(callback);return 1}}),invoke=f.api.invoke;
  f.api.invoke=async(command,args)=>{
    if(command==='send_chat_message')submitted=true;
    if(command==='list_studio_jobs'&&submitted){studioPolls++;return studioPolls<2?[{request:{conversationId:'group-dedicated'},status:'running'}]:[]}
    if(command==='get_conversation'&&submitted)return[{id:1,conversationId:'group-dedicated',kind:'message',role:'assistant',content:studioPolls>=2?'Studio result ready':'Early response'}];
    return invoke(command,args);
  };
  await createGroupWorker(f.api).poll();
  assert.ok(studioPolls>=2);assert.equal(f.calls.find(call=>call.service==='worker/update'&&call.body.status==='completed').body.text,'Studio result ready');
});
test('the complete native final entry is preserved in the group result',async()=>{
  const {createGroupWorker}=await load(),resultText='Full native output\n'+'x'.repeat(18000)+'\nFinal line שלום';
  const f=fixture({resultText}),worker=createGroupWorker(f.api);await worker.poll();
  assert.equal(f.calls.find(call=>call.service==='worker/update'&&call.body.status==='completed').body.text,resultText);
});
test('progress stays within 24KB while oversized final output has an explicit local full-result reference',async()=>{
  const {createGroupWorker}=await load(),resultText='Full native output\n'+'שלום'.repeat(16000)+'\nTHE END';
  const entries=Array.from({length:80},(_,id)=>({id,conversationId:'group-dedicated',kind:'tool_call',role:'assistant',content:'תוכן'.repeat(4000)}));
  entries.push({id:81,conversationId:'group-dedicated',kind:'message',role:'assistant',content:resultText});
  const f=fixture({entries}),worker=createGroupWorker(f.api);await worker.poll();
  const updates=f.calls.filter(call=>call.service==='worker/update');
  assert.ok(updates.every(call=>Buffer.byteLength(JSON.stringify(call.body.progress))<=24000));
  const text=updates.find(call=>call.body.status==='completed').body.text;
  assert.ok(text.length<=32000);assert.match(text,/preview/i);assert.match(text,/opencore-group-local:\/\/conversation\/group-dedicated/);
  assert.equal(await worker.readResult('connection-local','group-dedicated'),resultText);
  await assert.rejects(()=>worker.readResult('another-group','group-dedicated'));
});
test('failed terminal delivery retries after restart without executing the task again',async()=>{
  const {createGroupWorker}=await load(),f=fixture(),service=f.api.service;let failures=1,attempts=0,claims=0;
  f.api.service=async(action,body)=>{
    if(action==='worker/poll'&&claims++>0)return{jobs:[]};
    if(action==='worker/update'&&body.status==='completed'){attempts++;if(failures-->0)throw Error('Temporary network loss')}
    return service(action,body)};
  await createGroupWorker(f.api).poll();await createGroupWorker(f.api).poll();
  assert.ok(attempts>=2);assert.equal(f.calls.filter(call=>call.command==='send_chat_message').length,1);
  assert.equal(f.calls.some(call=>call.service==='worker/update'&&call.body.status==='failed'),false);
});
test('successful delivery retry clears its stale waiting notice',async()=>{
  const {createGroupWorker}=await load(),f=fixture(),service=f.api.service;let failures=1;
  f.api.service=async(action,body)=>{if(action==='worker/update'&&body.status==='completed'&&failures-->0)throw Error('Temporary network loss');return service(action,body)};
  const worker=createGroupWorker(f.api);await worker.poll();assert.match(worker.getState().error,/waiting for delivery/);
  await worker.poll();assert.equal(worker.getState().error,'');
});
test('revoked sharing cancels only the held dedicated native conversation',async()=>{
  const {createGroupWorker}=await load();let release,revoked=false;const callbacks=[];
  const f=fixture({send:()=>new Promise(resolve=>{release=resolve}),schedule:(callback,delay)=>{callbacks.push({callback,delay});return callbacks.length}}),service=f.api.service;
  f.api.service=async(action,body)=>{if(revoked&&action==='worker/update'&&body.status==='running')throw Error('This local task lease is unavailable');return service(action,body)};
  const worker=createGroupWorker(f.api),running=worker.poll();
  for(let attempt=0;attempt<20&&!release;attempt++)await new Promise(resolve=>setImmediate(resolve));
  assert.ok(release);revoked=true;await callbacks.find(timer=>timer.delay===1500).callback();
  assert.deepEqual(f.calls.filter(call=>call.command==='cancel_chat_message').map(call=>call.args),[{conversationId:'group-dedicated'}]);
  release({conversationId:'group-dedicated'});await running;
});

test('revocation cancels scoped studio work as well as its chat without touching another chat job',async()=>{
  const {createGroupWorker}=await load();let release,revoked=false;const callbacks=[];
  const f=fixture({send:()=>new Promise(resolve=>{release=resolve}),schedule:(callback,delay)=>{callbacks.push({callback,delay});return callbacks.length}}),service=f.api.service,invoke=f.api.invoke;
  f.api.service=async(action,body)=>{if(revoked&&action==='worker/status')throw Error('This group task has ended');return service(action,body)};
  f.api.invoke=async(command,args)=>{if(command==='list_studio_jobs'&&revoked)return[{id:'own-studio',status:'running',request:{conversationId:'group-dedicated'}},{id:'other-studio',status:'running',conversationId:'private-chat'}];return invoke(command,args)};
  const worker=createGroupWorker(f.api),running=worker.poll();
  for(let attempt=0;attempt<20&&!release;attempt++)await new Promise(resolve=>setImmediate(resolve));
  assert.ok(release);revoked=true;await callbacks.find(timer=>timer.delay===1500).callback();
  assert.deepEqual(f.calls.filter(call=>call.command==='cancel_studio_job').map(call=>call.args),[{id:'own-studio'}]);
  release({});await running;
});

test('stopping a worker cancels its submitted native turn',async()=>{
  const {createGroupWorker}=await load();let release;
  const f=fixture({send:()=>new Promise(resolve=>{release=resolve})}),worker=createGroupWorker(f.api),running=worker.poll();
  for(let attempt=0;attempt<20&&!release;attempt++)await new Promise(resolve=>setImmediate(resolve));
  assert.ok(release);await worker.stop();
  assert.equal(f.calls.filter(call=>call.command==='cancel_chat_message').length,1);
  release({});await running;
});

test('a later native error is reported as failure even if partial assistant text exists',async()=>{
  const {createGroupWorker}=await load(),f=fixture({entries:[
    {id:1,conversationId:'group-dedicated',kind:'message',role:'assistant',content:'Earlier partial output'},
    {id:2,conversationId:'group-dedicated',kind:'error',role:'assistant',content:'Studio continuation failed'},
  ]});
  await createGroupWorker(f.api).poll();
  assert.equal(f.calls.some(call=>call.service==='worker/update'&&call.body.status==='completed'),false);
  assert.ok(f.calls.some(call=>call.service==='worker/update'&&call.body.status==='failed'&&call.body.error.includes('Studio continuation failed')));
});

test('a failed turn keeps the tool activity already shown in the shared task',async()=>{
  const {createGroupWorker}=await load(),f=fixture({entries:[
    {id:1,conversationId:'group-dedicated',kind:'tool_call',role:'assistant',content:'Verified file tool'},
    {id:2,conversationId:'group-dedicated',kind:'message',role:'assistant',content:'Partial result'},
    {id:3,conversationId:'group-dedicated',kind:'error',role:'assistant',content:'Later failure'},
  ]});
  await createGroupWorker(f.api).poll();
  const failed=f.calls.find(call=>call.service==='worker/update'&&call.body.status==='failed');
  assert.ok(failed.body.progress.entries.some(entry=>entry.content==='Verified file tool'));
});
test('disabled sharing, unshared models, and rejected leases cannot start native execution',async()=>{
  const {createGroupWorker}=await load();
  for(const options of [{sharing:{enabled:false}},{sharing:{models:[]}},{rejectRegister:true}]){
    const f=fixture(options),worker=createGroupWorker(f.api);await worker.poll();
    assert.equal(f.calls.some(call=>call.command==='start_profile'||call.command==='send_chat_message'),false);
  }
});
test('tool approval events are limited to the local dedicated task conversation',async()=>{
  const {createGroupWorker}=await load();let release;
  const f=fixture({send:()=>new Promise(resolve=>{release=resolve})}),worker=createGroupWorker(f.api);
  await worker.start();const running=worker.poll();
  for(let attempt=0;attempt<20&&!release;attempt++)await new Promise(resolve=>setImmediate(resolve));
  assert.ok(release,'The native task must start');
  const approval=f.listeners['opencore-tool-approval-request'];
  approval({payload:{requestId:'foreign',conversationId:'private-conversation',name:'Private tool'}});
  assert.equal(worker.getState().approvals.length,0);
  assert.equal(await worker.answer('foreign',true),false);
  approval({payload:{requestId:'local',conversationId:'group-dedicated',name:'Task tool',arguments:'{}'}});
  assert.equal(worker.getState().approvals.length,1);
  await worker.answer('local',false);
  assert.deepEqual(f.calls.filter(call=>call.command==='resolve_tool_approval').map(call=>call.args),[{requestId:'local',approved:false}]);
  release({conversationId:'group-dedicated'});await running;worker.stop();
});
test('literal mentions select a unique agent',async()=>{
  const {resolveGroupTargets}=await load();
  assert.deepEqual(resolveGroupTargets('@Helper, make a plan',[{id:'pc-a',name:'Helper'}],[]),{agentIds:['pc-a'],error:''});
});

test('custom aliases and names with regex punctuation are matched literally',async()=>{
  const {resolveGroupTargets}=await load(),agents=[{id:'pc-a',name:'Builder [A]+',aliases:['code.bot']}];
  assert.deepEqual(resolveGroupTargets('@Builder [A]+ review',agents,[]),{agentIds:['pc-a'],error:''});
  assert.deepEqual(resolveGroupTargets('@code.bot, review',agents,[]),{agentIds:['pc-a'],error:''});
  assert.deepEqual(resolveGroupTargets('@codeXbot review',agents,[]),{agentIds:[],error:''});
});
test('a duplicate alias requires an explicit PC choice before sending',async()=>{
  const {resolveGroupTargets}=await load(),agents=[{id:'pc-a',name:'Helper'},{id:'pc-b',name:'Helper'}];
  assert.deepEqual(resolveGroupTargets('@helper make a plan',agents,[]),{agentIds:[],error:'Choose which PC should run @Helper'});
});
test('an explicit PC target disambiguates a duplicate literal alias',async()=>{
  const {resolveGroupTargets}=await load(),agents=[{id:'pc-a',name:'Helper'},{id:'pc-b',name:'Helper'}];
  assert.deepEqual(resolveGroupTargets('@Helper make a plan',agents,['pc-a']),{agentIds:['pc-a'],error:''});
});
test('multiple PCs run a shared alias only when each was explicitly selected',async()=>{
  const {resolveGroupTargets}=await load(),agents=[{id:'pc-a',name:'Helper'},{id:'pc-b',name:'Helper'}];
  assert.deepEqual(resolveGroupTargets('@Helper make a plan',agents,['pc-a','pc-b']),{agentIds:['pc-a','pc-b'],error:''});
});
