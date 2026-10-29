import React from 'react';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {ImageGenerationPanel,selectChatImageModel,clearChatImageSelection,submitChatImagePrompt,automaticImageSettings} from './image-generation-panel.js';
import {saveGenerationSettings} from './generation-settings.js';

afterEach(()=>{cleanup();clearChatImageSelection();vi.restoreAllMocks();delete (window as any).__TAURI_INTERNALS__;});
it('keeps an immediate image send out of the text route before selection commits',async()=>{
  const calls:any[]=[],model={id:'qwen-image-21-uc-nvfp4',label:'Qwen Image',category:'image',installed:true};
  (window as any).__TAURI_INTERNALS__={invoke:async(command:string,args:any={})=>{
    calls.push({command,args});
    if(command==='list_model_library')return{models:[model]};
    if(command==='list_studio_jobs')return[];
    if(command==='runtime_setup_status')return{receipts:[{targetId:model.id,dependenciesVerified:true,inferenceVerified:true,python:'managed/python.exe'}]};
    if(command==='submit_studio_job')return{id:'immediate-image',category:'image',status:'queued',createdAt:'2026-10-30',request:args.request,outputs:[]};
    return null;
  }};
  const view=render(<div className="chat-composer-wrap"><ImageGenerationPanel conversationId="immediate-chat"/></div>);
  const scope=view.container.firstElementChild!;let consumed=false;
  act(()=>{selectChatImageModel(model,{scope});consumed=submitChatImagePrompt({scope,prompt:'A blue ceramic cup'});});
  expect(consumed).toBe(true);
  await waitFor(()=>expect(calls.filter(call=>call.command==='submit_studio_job')).toHaveLength(1));
  expect(calls.find(call=>call.command==='submit_studio_job').args.request.modelId).toBe(model.id);
});
it('uses the existing message box and send with automatic quality and attached reference images',async()=>{
  const calls:any[]=[], accepted=vi.fn();
  const model={id:'qwen-image-21-uc-nvfp4',label:'Qwen Image',category:'image',installed:true};
  (window as any).__TAURI_INTERNALS__={invoke:vi.fn(async(command:string,args:any={})=>{
    calls.push({command,args});
    if(command==='list_model_library')return{models:[model]};
    if(command==='list_studio_jobs')return[];
    if(command==='runtime_setup_status')return{receipts:[{targetId:model.id,dependenciesVerified:true,inferenceVerified:true,python:'managed/python.exe'}]};
    if(command==='submit_studio_job')return{id:'real-job',status:'queued',category:'image',createdAt:'2026-10-28',request:args.request,outputs:[]};
    return null;
  })};
  function Composer(){const root=React.useRef<HTMLDivElement>(null),[draft,setDraft]=React.useState('Make this cup blue');return <div ref={root} className="chat-composer-wrap"><ImageGenerationPanel conversationId="same-chat"/><textarea aria-label="Message OpenCore" value={draft} onChange={e=>setDraft(e.target.value)}/><button onClick={()=>submitChatImagePrompt({scope:root.current,prompt:draft,files:['C:/attachments/cup.png'],onAccepted:()=>{accepted();setDraft('');}})}>Send message</button></div>;}
  render(<Composer/>);act(()=>{selectChatImageModel(model);});
  await screen.findByText(/Automatic quality/);
  expect(screen.queryByLabelText('Image generation prompt')).toBeNull();
  expect(screen.queryByRole('button',{name:'Images'})).toBeNull();
  expect(screen.getByLabelText('Message OpenCore')).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'Send message'}));
  fireEvent.click(screen.getByRole('button',{name:'Send message'}));
  await waitFor(()=>expect(accepted).toHaveBeenCalledOnce());
  const requests=calls.filter(call=>call.command==='submit_studio_job');expect(requests).toHaveLength(1);
  expect(requests[0].args.request).toMatchObject({modelId:model.id,prompt:'Make this cup blue',conversationId:'same-chat',settings:{width:2048,height:2048,steps:40,guidanceScale:1,referencePaths:['C:/attachments/cup.png']}});
  expect(screen.getByLabelText('Message OpenCore')).toHaveValue('');
});
it('chooses model-specific quality and aspect ratio without manual controls',()=>{
  expect(automaticImageSettings('moss-image-sft-int8-convrot','A landscape wallpaper')).toMatchObject({width:1024,height:576,steps:50,guidanceScale:3});
  expect(automaticImageSettings('flux-2-klein-4b','A portrait photo')).toMatchObject({width:576,height:1024,steps:4});
  expect(()=>automaticImageSettings('qwen-image-21','test',['C:/secret.txt'])).toThrow(/image/);
});
it('does not redisplay a failure from an older attempt when choosing the image model',async()=>{
  const model={id:'qwen-image-21-uc-nvfp4',label:'Qwen Image',category:'image',installed:true};
  const loaded=vi.fn();
  (window as any).__TAURI_INTERNALS__={invoke:async(command:string)=>{
    if(command==='list_model_library')return{models:[model]};
    if(command==='list_studio_jobs'){loaded();return[{id:'older-failure',category:'image',status:'failed',createdAt:'2026-10-29',request:{modelId:model.id,conversationId:'same-chat'},error:'Old loader needs 18.4 GB',outputs:[]}];}
    return null;
  }};
  render(<ImageGenerationPanel conversationId="same-chat"/>);act(()=>{selectChatImageModel(model);});
  await screen.findByText(/Automatic quality/);
  await waitFor(()=>expect(loaded).toHaveBeenCalled());
  expect(screen.queryByText(/18.4 GB/)).toBeNull();
});
it('keeps an active image prompt from falling through if its composer handler is absent',()=>{
  const onError=vi.fn(),scope=document.createElement('div');
  scope.innerHTML='<section class="chat-image-status" data-image-mode="open"></section>';
  expect(submitChatImagePrompt({scope,prompt:'A blue cup',onError})).toBe(true);
  expect(onError).toHaveBeenCalledWith(expect.stringContaining('Your prompt is kept'));
  expect(submitChatImagePrompt({scope:document.createElement('div'),prompt:'A text question'})).toBe(false);
});
it('prepares learned upscaling for a larger manual output from the normal chat',async()=>{
  const id='qwen-image-21-uc-nvfp4',calls:any[]=[];
  const prepareUpscaler=vi.fn(async()=>({path:'C:/managed/ai-upscaler.pth'}));
  (globalThis as any).__opencoreImageHub={prepareUpscaler};
  (window as any).__TAURI_INTERNALS__={invoke:async(command:string,args:any={})=>{calls.push({command,args});if(command==='list_model_library')return{models:[{id,label:'Qwen Image',category:'image',installed:true}]};if(command==='list_studio_jobs')return[];if(command==='runtime_setup_status')return{receipts:[{targetId:id,dependenciesVerified:true,inferenceVerified:true,python:'managed/python.exe'}]};if(command==='submit_studio_job')return{id:'upscale-test',status:'queued',request:args.request,outputs:[]};return null;}};
  saveGenerationSettings(id,{mode:'manual',values:{width:4096,height:4096,steps:40,guidanceScale:1}});
  try{
    render(<ImageGenerationPanel conversationId="upscale-normal-chat"/>);act(()=>{selectChatImageModel({id,label:'Qwen Image'});});
    await screen.findByText(/Manual settings/);act(()=>{submitChatImagePrompt({prompt:'A blue mug'});});
    await waitFor(()=>expect(calls.some(call=>call.command==='submit_studio_job')).toBe(true));
    expect(prepareUpscaler).toHaveBeenCalledOnce();
    expect(calls.find(call=>call.command==='submit_studio_job').args.request.settings).toMatchObject({width:2048,height:2048,outputWidth:4096,outputHeight:4096,upscaleModelPath:'C:/managed/ai-upscaler.pth'});
  }finally{saveGenerationSettings(id,{mode:'automatic'});delete (globalThis as any).__opencoreImageHub;}
});
