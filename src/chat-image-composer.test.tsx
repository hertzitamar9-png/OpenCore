import React from 'react';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {ImageGenerationPanel,selectChatImageModel,clearChatImageSelection,submitChatImagePrompt,automaticImageSettings} from './image-generation-panel.js';

afterEach(()=>{cleanup();clearChatImageSelection();vi.restoreAllMocks();delete (window as any).__TAURI_INTERNALS__;});
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
