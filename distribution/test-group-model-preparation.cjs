const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const load=async()=>(await import(pathToFileURL(path.join(__dirname,'../src/group-model-preparation.js')).href)).prepareGroupModel;
const hubId='hf-'+ 'a'.repeat(24),profileId='api:local:68662d616161';

function fixture({hub=true,installed=false,ready=false,prepared=false}={}){
  const entry={id:hubId,repository:'owner/checkpoint',revision:'b'.repeat(40),category:'text',status:installed?'installed':'not-installed',
    installable:true,runtimeReady:ready,runtime:{adapter:'gguf'}};
  const model=hub?{id:hubId,category:'text',backend:'hub',hubModel:true,hubEntry:entry,installable:true,installed,runtimeReady:ready,selectable:ready}:
    {id:'native-coder',category:'text',backend:'gguf',installable:true,installed,runtimeReady:ready,selectable:ready,
      ...(prepared?{preparedRuntime:{kind:'reviewed-native-runtime'},preparedReady:ready}:{})};
  let snapshot={initialized:true,active:null,progress:null,runtime:{jobs:[],receipts:[]}},waits=0,clock=0,cancelled=false;
  const calls=[],events=[],steps=[];
  const f={entry,model,calls,events,steps,registered:0,models:[model],entries:hub?[entry]:[],
    config:{id:model.id,catalogId:model.id,prepareIfMissing:true},
    setSnapshot:value=>{snapshot=value;},snapshot:()=>snapshot,setCancelled:value=>{cancelled=value;},
    active(phase='downloading',kind='download',id='owned-operation'){
      snapshot={initialized:true,active:{modelId:model.id,kind,...(kind==='runtime'?{jobId:id}:{})},
        progress:{modelId:model.id,operationId:id,phase,downloadedBytes:3,totalBytes:10,currentFile:'weights.gguf'},runtime:{jobs:[],receipts:[]}};
    },
    complete({weights=true,runtime=false}={}){
      if(weights){model.installed=true;entry.status='installed';}
      if(runtime){model.runtimeReady=true;model.selectable=true;entry.runtimeReady=true;model.preparedReady=true;
        entry.runtimePreparation={runtimeReady:true,receiptRevision:entry.revision};}
      snapshot={initialized:true,active:null,progress:{modelId:model.id,operationId:'owned-operation',phase:'ready'},runtime:{jobs:[],receipts:[]}};
    },
    deps:{
      invoke:async(command,args)=>{
        calls.push({command,args:structuredClone(args)});
        if(command==='list_model_library')return{models:f.models.map(m=>({...m}))};
        if(f.onInvoke)return f.onInvoke(command,args);
        throw Error('Unexpected command '+command);
      },
      catalog:()=>f.entries,
      refresh:async force=>{assert.equal(force,true);},
      register:async value=>{
        assert.equal(value.runtimeReady,true,'Registration requires successful model generation');
        assert.equal(value.status,'installed');f.registered++;entry.profileId=profileId;return profileId;
      },
      modes:{ensure:async value=>{events.push('ensure');return value;},activate:async id=>{events.push('activate');return id;},mode:()=> 'echo'},
      installState:{refresh:async force=>{assert.equal(force,true);return true;},getSnapshot:()=>snapshot},
      wait:async milliseconds=>{assert.equal(milliseconds,1000);waits++;clock+=milliseconds;const step=steps.shift();if(!step)throw Error('Unexpected extra polling');await step();},
      now:()=>clock
    },
    cancelled:async()=>cancelled,
    run:async()=> (await load())(f.config,f.deps,value=>events.push(structuredClone(value)),f.cancelled),
    writes:()=>calls.filter(c=>c.command!=='list_model_library'),waits:()=>waits
  };
  return f;
}

test('managed missing weights use one admitted download, then verified preparation and exact registered profile',async()=>{
  const f=fixture();
  f.onInvoke=async command=>{
    if(command==='install_model'){f.events.push('install');f.active();return null;}
    if(command==='hub_prepare_model'){f.events.push('prepare');f.active('verifying');return {jobId:'owned-operation'};}
    throw Error(command);
  };
  f.steps.push(()=>f.complete(),()=>f.complete({runtime:true}));
  assert.deepEqual(await f.run(),{profileId,catalogId:hubId});
  assert.deepEqual(f.writes(),[{command:'install_model',args:{id:hubId}},{command:'hub_prepare_model',args:{id:hubId}}]);
  assert.ok(f.events.indexOf('ensure')<f.events.indexOf('prepare'));
  assert.ok(f.events.some(value=>value?.phase==='downloading'&&value.downloadedBytes===3&&value.totalBytes===10));
  assert.ok(f.events.some(value=>value?.phase==='verifying'));
  assert.equal(f.registered,1);
});

test('an already verified managed checkpoint registers its actual profile without another installation',async()=>{
  const f=fixture({installed:true,ready:true});
  assert.deepEqual(await f.run(),{profileId,catalogId:hubId});
  assert.equal(f.writes().length,0);assert.equal(f.registered,1);
});

test('an installed native checkpoint activates the selected mode alias and returns that execution profile',async()=>{
  const f=fixture({hub:false,installed:true,ready:true});
  f.model.operatingProfileId='native-coder-mode-echo';
  f.model.modeProfiles={echo:'native-coder-mode-echo',native:'native-coder'};
  f.deps.modes.activate=async(id,mode)=>{assert.equal(id,'native-coder-mode-echo');assert.equal(mode,'echo');return id;};
  assert.deepEqual(await f.run(),{profileId:'native-coder-mode-echo',catalogId:'native-coder'});
  assert.equal(f.writes().length,0);
});

test('a downloaded native prepared runtime verifies inference without downloading the weights again',async()=>{
  const f=fixture({hub:false,installed:true,prepared:true});
  f.onInvoke=async command=>{assert.equal(command,'runtime_setup_start');f.active('verifying','runtime','native-job');return{id:'native-job'};};
  f.steps.push(()=>f.complete({runtime:true}));
  assert.deepEqual(await f.run(),{profileId:'native-coder',catalogId:'native-coder'});
  assert.deepEqual(f.writes(),[{command:'runtime_setup_start',args:{targetId:'native-coder',options:{installWeights:false,verifyInference:true,acceptLicenses:false,allowAdministrator:false}}}]);
});

test('owner authorization, exact local catalog identity and text adapter support are checked before mutation',async()=>{
  for(const [change,error] of [
    [f=>{f.config.prepareIfMissing=false;},/owner.*authoriz/i],
    [f=>{f.config.catalogId='https://peer.invalid/model.gguf';},/owner-selected model.*catalog/i],
    [f=>{f.config.id='api:unrelated:616263';},/profile does not match/i],
    [f=>{f.entry.runtime.adapter=null;},/runtime adapter/i],
    [f=>{f.entry.category='image';},/conversational text runtime/i],
    [f=>{f.entries=[];f.models=[];},/owner-selected model.*catalog/i],
  ]){
    const f=fixture();change(f);
    await assert.rejects(f.run(),error);assert.equal(f.writes().length,0);assert.equal(f.registered,0);
  }
});

test('a rejected normal installation admission is never retried',async()=>{
  const f=fixture();f.onInvoke=async()=>{throw Error('Another model is installing');};
  await assert.rejects(f.run(),/Another model is installing/);
  assert.equal(f.writes().length,1);assert.equal(f.waits(),0);
});

test('real failed, paused and cancelled operations stop without redownload or registration',async()=>{
  for(const phase of ['failed','paused','cancelled']){
    const f=fixture();
    f.onInvoke=async()=>{f.active();return null;};
    f.steps.push(()=>f.setSnapshot({initialized:true,active:null,progress:{modelId:hubId,phase,error:phase==='failed'?'Disk full':null},runtime:{jobs:[],receipts:[]}}));
    await assert.rejects(f.run(),phase==='failed'?/Disk full/:/paused|cancelled/i);
    assert.equal(f.writes().length,1);assert.equal(f.registered,0);
  }
});

test('a ready installation marker cannot substitute for downloaded weights or verified generation',async()=>{
  const f=fixture({installed:true});
  f.onInvoke=async command=>{assert.equal(command,'hub_prepare_model');f.active('verifying');return{};};
  f.steps.push(()=>f.complete());
  await assert.rejects(f.run(),/verif|ready|generation/i);
  assert.equal(f.registered,0);assert.equal(f.writes().length,1);
});

test('an installation completion without downloaded weights cannot start runtime preparation',async()=>{
  const f=fixture();f.onInvoke=async command=>{assert.equal(command,'install_model');f.active();return null;};
  f.steps.push(()=>f.complete({weights:false}));
  await assert.rejects(f.run(),/downloaded weights/i);
  assert.equal(f.registered,0);assert.equal(f.writes().length,1);
});

test('unchanged polling still awaits the progress lease callback and stops on lease loss',async()=>{
  const f=fixture();let heartbeats=0,finished=0;
  f.onInvoke=async command=>{if(command==='install_model'){f.active();return null;}assert.equal(command,'cancel_model_install');return null;};
  f.steps.push(()=>{},()=>{});
  await assert.rejects((await load())(f.config,f.deps,async value=>{
    assert.equal(value.phase,'downloading');assert.equal(value.downloadedBytes,3);
    assert.equal(finished,heartbeats);heartbeats++;
    await Promise.resolve();finished++;
    if(heartbeats===3)throw Error('The group task has ended');
  }),/group task has ended/);
  assert.equal(heartbeats,3);assert.equal(f.waits(),2);
  assert.deepEqual(f.writes().map(c=>c.command),['install_model','cancel_model_install']);
});

test('managed registration must return a real execution profile rather than its Hub card ID',async()=>{
  const f=fixture({installed:true,ready:true});f.deps.register=async()=>hubId;
  await assert.rejects(f.run(),/registered chat execution profile/i);
  assert.equal(f.writes().length,0);
});

test('cancellation pauses only the download started for the exact selected model',async()=>{
  const f=fixture();
  f.onInvoke=async command=>{if(command==='install_model'){f.active();return null;}assert.equal(command,'cancel_model_install');return null;};
  f.steps.push(()=>f.setCancelled(true));
  await assert.rejects(f.run(),/cancelled/i);
  assert.deepEqual(f.writes().map(c=>c.command),['install_model','cancel_model_install']);
});

test('cancellation of native runtime setup targets its own job ID',async()=>{
  const f=fixture({hub:false,installed:true,prepared:true});
  f.onInvoke=async command=>{if(command==='runtime_setup_start'){f.active('verifying','runtime','native-job');return{id:'native-job'};}assert.equal(command,'runtime_setup_cancel');return null;};
  f.steps.push(()=>f.setCancelled(true));
  await assert.rejects(f.run(),/cancelled/i);
  assert.deepEqual(f.writes().at(-1),{command:'runtime_setup_cancel',args:{jobId:'native-job'}});
});

test('cancellation never pauses an unrelated model or a newer operation for the same model',async()=>{
  for(const sameModel of [false,true]){
    const f=fixture();f.onInvoke=async command=>{assert.equal(command,'install_model');f.active();return null;};
    f.steps.push(()=>{f.active('downloading','download','someone-elses-operation');if(!sameModel){f.snapshot().active.modelId='other-model';f.snapshot().progress.modelId='other-model';}f.setCancelled(true);});
    await assert.rejects(f.run(),/cancelled/i);
    assert.deepEqual(f.writes().map(c=>c.command),['install_model']);
  }
});

test('the ninety minute bound fails and cancels only its own active download',async()=>{
  const f=fixture();let now=0;f.deps.now=()=>now;
  f.onInvoke=async command=>{if(command==='install_model'){f.active();return null;}assert.equal(command,'cancel_model_install');return null;};
  f.steps.push(()=>{now=90*60*1000;});
  await assert.rejects(f.run(),/timed out|90 minute/i);
  assert.equal(f.registered,0);assert.deepEqual(f.writes().map(c=>c.command),['install_model','cancel_model_install']);
});
