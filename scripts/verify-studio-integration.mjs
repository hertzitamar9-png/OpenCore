// Explicit local integration smoke against an already running debug OpenCore.
// It downloads no models and writes evidence outside the source tree.
import {chromium} from 'playwright';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
if (!process.env.OPENCORE_STUDIO_SMOKE_DIR) throw new Error('Set OPENCORE_STUDIO_SMOKE_DIR for explicit local inference testing');
const root=process.env.OPENCORE_STUDIO_SMOKE_DIR;mkdirSync(root,{recursive:true});
const browser=await chromium.connectOverCDP(process.env.OPENCORE_STUDIO_CDP ?? 'http://127.0.0.1:9227');
const page=browser.contexts().flatMap(c=>c.pages()).find(p=>p.url().startsWith('http://tauri.localhost')&&!p.url().includes('desktop-activity'));
if(!page)throw new Error('Debug OpenCore WebView is not running');
const invoke=(command,args={})=>page.evaluate(async ({command,args})=>window.__TAURI_INTERNALS__.invoke(command,args),{command,args});
async function verify() { try {
  const installed=await invoke('installed_skill_models');
  if(!installed.some(m=>m.id==='yue2'))throw new Error('YuE2 must already be verified and installed');
  const previous=await invoke('list_studio_jobs');
  if(previous.some(j=>['queued','starting','running'].includes(j.status)))throw new Error('An existing generation is active; leave it uninterrupted');
  await invoke('select_profile',{profile:'echo'});
  const conversationId=crypto.randomUUID();
  console.log('Starting one ECHO → music tool → YuE2 audio smoke',conversationId);
  const response=await invoke('send_chat_message',{request:{conversationId,text:"Use the music skill to create a brief upbeat song about AI. Compose original lyrics, title and style. This is a short integration test: use cot='off', mode='song', semantic_sampling={max_tokens:256,min_tokens:1}, ode_steps=8, takes=1. Call music_generate to queue exactly one real generation, then finish your response without polling. Do not only write lyrics.",skills:['music'],files:[],approvalMode:'allow-all',reasoningEffort:'low',projectSkillsEnabled:false,subagentsEnabled:false}});
  writeFileSync(join(root,'chat-result.json'),JSON.stringify(response,null,2));
  const conversation=await invoke('get_conversation',{id:conversationId});
  writeFileSync(join(root,'chat-timeline.json'),JSON.stringify(conversation,null,2));
  console.log('Text response finished; checking the durable generation');
  const deadline=Date.now()+10*60*1000;
  let last='',observedUnloaded=false;
  while(Date.now()<deadline){
    const jobs=(await invoke('list_studio_jobs')).filter(j=>j.request.conversationId===conversationId);
    writeFileSync(join(root,'jobs.json'),JSON.stringify(jobs,null,2));
    if(jobs.length!==1)throw new Error(`Expected one real tool-submitted job, got ${jobs.length}`);
    const job=jobs[0];
    const state=`${job.status}: ${job.stage}`;
    if(state!==last){console.log(state);last=state;}
    if(job.status==='running') {
      const snapshot=await invoke('get_snapshot');
      if(snapshot.runtime.status!=='stopped'||snapshot.runtime.modelPid||snapshot.runtime.echoPid)throw new Error('ECHO stayed resident during the worker job');
      observedUnloaded=true;
    }
    if(job.status==='completed'){
      if(!job.outputs.some(p=>/\.(flac|wav|mp3)$/i.test(p)))throw new Error('No generated audio output');
      const music=await invoke('music_studio_status');
      if(music.modelLoaded)throw new Error('Music model was not unloaded');
      if(!observedUnloaded)throw new Error('The unloaded waiting phase was not observed');
      console.log('Audio complete and music unloaded; waiting for automatic ECHO continuation');
      while(Date.now()<deadline){
        const timeline=await invoke('get_conversation',{id:conversationId});
        const snapshot=await invoke('get_snapshot');
        const event=timeline.find(e=>e.metadata?.backgroundJobId===job.id);
        const answer=event&&timeline.some(e=>e.id>event.id&&e.role==='assistant'&&e.kind==='message'&&!e.metadata?.studioReceipt);
        if(answer&&!snapshot.activeConversationIds.includes(conversationId)){
          if((await invoke('list_studio_jobs')).filter(j=>j.request.conversationId===conversationId).length!==1)throw new Error('Continuation repeated the generation');
          writeFileSync(join(root,'completed-timeline.json'),JSON.stringify(timeline,null,2));
          writeFileSync(join(root,'verified.json'),JSON.stringify({conversationId,job,music,observedUnloaded,resumed:true,runtimeAfter:snapshot.runtime},null,2));
          await invoke('stop_runtime');
          console.log('Verified unload → real audio → automatic ECHO wake-up and response',job.outputs);return;
        }
        await new Promise(resolve=>setTimeout(resolve,1500));
      }
      throw new Error('ECHO did not automatically continue');
    }
    if(['failed','cancelled','interrupted'].includes(job.status))throw new Error(job.error ?? job.stage);
    await new Promise(resolve=>setTimeout(resolve,1500));
  }
  throw new Error('Smoke exceeded ten minutes; job retained for inspection, not interrupted');
} finally {await browser.close();} }
await verify();
