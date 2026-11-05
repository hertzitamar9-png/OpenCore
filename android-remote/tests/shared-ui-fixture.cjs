module.exports=({snapshot,timeline,mode='pc',firstRun=false,startupDelay=0,hangSnapshot=false,nativeCoreDelay=0,profileSeed,installedModels,strictPhoneProfiles=false,persistPhoneSelection=false})=>{
  let saved={};
  if(persistPhoneSelection)try{saved=JSON.parse(window.name||'{}');}catch{}
  const selected=saved.selectedProfile||profileSeed;
  if(selected!==undefined)window.__OPENCORE_PC_PREFS__={'opencore.model-profile':selected};
  window.fixture={calls:[],snapshot,timeline,listeners:{},nextListener:0,nextEntryId:10000,hangSnapshot,nativeReady:!nativeCoreDelay};
  fixture.selectedProfile=selected;
  if(nativeCoreDelay)setTimeout(()=>{fixture.nativeReady=true;},nativeCoreDelay);
  document.addEventListener('DOMContentLoaded',()=>{
    const model={id:'echo',label:'Fixture native model',description:'A fixture model exposed through the desktop ModelLibrary DTO',installed:true,selectable:true,runtimeReady:true,installable:true,category:'text',backend:'gguf',precision:'Q4_K_M',totalBytes:1048576,weightBytes:1048576,contextTokens:2048,memoryMode:'native',artifacts:[]};
    fixture.models=installedModels|| (mode==='phone'?[]:[model]);
    fixture.populate=(next,entries)=>{fixture.snapshot=next;fixture.timeline=entries;fixture.models=[model];fixture.emit('opencore-bridge-activity',{conversationId:next.conversations[0]?.id||''});};
    fixture.stream=content=>{fixture.content=content;fixture.emit('opencore-generation',{conversationId:fixture.conversationId,runId:'fixture-run',content,phase:'generating'});};
    fixture.complete=()=>{fixture.timeline.push({id:++fixture.nextEntryId,conversationId:fixture.conversationId,timestamp:new Date().toISOString(),kind:'message',role:'assistant',source:'OpenCore',title:'Assistant',content:fixture.content||'Fixture completed reply',metadata:{}});fixture.snapshot.activeConversationIds=[];fixture.emit('opencore-generation',{conversationId:fixture.conversationId,runId:'fixture-run',done:true});fixture.emit('opencore-bridge-activity',{conversationId:fixture.conversationId});};
    const channel=new MessageChannel();fixture.packet=value=>channel.port1.postMessage(JSON.stringify(value));channel.port1.onmessage=async event=>{
      const request=JSON.parse(event.data);fixture.calls.push(request);let value=null;
      if(request.kind==='invoke'){
        const command=request.command,args=request.args||{};
        if(command==='select_profile'){
          if(strictPhoneProfiles&&args.profile!=='stopped'&&!fixture.models.some(model=>model.id===args.profile)){
            channel.port1.postMessage(JSON.stringify({type:'result',id:request.id,ok:false,error:'No installed phone model is selected. Open Models, install a compatible text GGUF, then choose Use or select it in the chat model picker.'}));return;
          }
          fixture.selectedProfile=args.profile;
          if(persistPhoneSelection)window.name=JSON.stringify({selectedProfile:args.profile});
        }
        if(['get_snapshot','account_action'].includes(command)&&!fixture.nativeReady){channel.port1.postMessage(JSON.stringify({type:'result',id:request.id,ok:false,error:'state not managed for field `core` on command `'+command+'`. You must call `.manage()` before using this command'}));return;}
        if(firstRun&&command==='account_action')return;
        if(command==='get_snapshot'&&fixture.hangSnapshot)return;
        if(command==='get_snapshot'&&startupDelay)await new Promise(resolve=>setTimeout(resolve,startupDelay));
        if(command==='startup_status'||command==='account_action'&&args.action==='startup_status')value={configured:true,user:{id:'fixture-user',name:'Fixture User'}};
        else if(command==='get_snapshot')value=fixture.snapshot;
        else if(command==='get_conversation')value=fixture.timeline;
        else if(command==='list_conversations')value=fixture.snapshot.conversations;
        else if(command==='list_imported_conversations')value={conversations:[],total:0,offset:0,limit:100};
        else if(command==='plugin:event|listen'){value=++fixture.nextListener;fixture.listeners[args.event]=args.handler;}
        else if(command==='runtime_setup_probe')value={hubBridge:{available:true,url:'http://127.0.0.1:1',secret:'fixture'}};
        else if(command==='runtime_setup_status')value={jobs:[],receipts:[],recipes:[],activeJobId:null};
        else if(command==='list_model_library')value={models:fixture.models,progress:fixture.progress||null,diskFreeBytes:100e9,minimumFreeBytes:0};
        else if(command==='model_install_progress')value=fixture.progress||null;
        else if(command==='api_provider_list')value=[];
        else if(command==='speech_status')value={enabled:false,installed:false,modelId:'whisper-large-v3-turbo',phase:'off'};
        else if(command==='get_version'||command==='plugin:app|version')value='0.3.168';
        else if(command==='agent_platform_configuration')value={systemPrompt:'',compactAtTokens:200000,verification:'default',repairAttempts:3,appearance:{theme:'dark',accentColor:'#4389d0',fontFamily:'system',fontSize:14,density:'comfortable',reducedMotion:false,highContrast:false},skillDirectories:[],pluginDirectories:[],disabledSkills:[],disabledPlugins:[],mcpServers:[],activityEnabled:true,memoryEnabled:true};
        else if(command==='workspace_files')value={files:[],coverage:[]};
        else if(command==='agent_platform_skills'||command==='agent_platform_plugins')value=[];
        else if(command==='list_studio_jobs')value=[];
        else if(command==='experimental_fast_mode')value={supported:false,enabled:false,active:false,restartRequired:false};
        else if(command==='send_chat_message'){
          const text=args.request?.text||'',id=args.request?.conversationId||'preview';fixture.conversationId=id;fixture.snapshot.activeConversationIds=[id];
          fixture.timeline.push({id:++fixture.nextEntryId,conversationId:id,timestamp:new Date().toISOString(),kind:'message',role:'user',source:'OpenCore',title:'You',content:text,metadata:{}});
          value={conversationId:id};
          setTimeout(()=>fixture.emit('opencore-bridge-activity',{conversationId:id}),20);
        }
        else if(command==='cancel_chat_message'){fixture.snapshot.activeConversationIds=[];value=true;setTimeout(()=>{fixture.emit('opencore-generation',{conversationId:fixture.conversationId,runId:'fixture-run',done:true});fixture.emit('opencore-bridge-activity',{conversationId:fixture.conversationId});},20);}
      }else if(request.kind==='hub'){
        const args=request.body?JSON.parse(request.body):{};
        const capability={available:false,executionDevice:'phone',reason:'Chat Core group collaboration requires the paired PC service. Choose Paired PC in Execution device settings to use your PC groups.',action:'phone-execution-settings'};
        const unsupportedPhoneGroup=mode==='phone'&&request.path.startsWith('/api/groups/')&&request.path!=='/api/groups/capabilities';
        const body=request.path==='/api/groups/capabilities'?(mode==='phone'?capability:{available:true,executionDevice:'pc'}):unsupportedPhoneGroup?{error:{message:capability.reason}}:request.path==='/api/status'?{catalog:[],progress:null,tokenConfigured:false,catalogRevision:'a'.repeat(64)}:request.path==='/api/remote/status'?{enabled:mode==='pc',pcName:'Fixture PC',addresses:[],devices:[]}:request.path==='/api/search'?{models:Array.from({length:100},(_,i)=>({repository:'fixture/Model-'+(args.cursor?100+i:i),downloads:1000,likes:0,task:'text-generation',library:'llama.cpp'})),nextCursor:args.cursor?'':'second'}:{};
        value={status:unsupportedPhoneGroup?501:200,contentType:'application/json',body:JSON.stringify(body)};
      }
      channel.port1.postMessage(JSON.stringify({type:'result',id:request.id,ok:true,value}));
    };fixture.emit=(name,payload)=>{const handler=fixture.listeners[name];if(handler!==undefined)channel.port1.postMessage(JSON.stringify({type:'event',handler,payload:{event:name,id:1,payload}}));};channel.port1.start();window.postMessage('opencore-mobile-port:fixture',location.origin,[channel.port2]);
  });
};
