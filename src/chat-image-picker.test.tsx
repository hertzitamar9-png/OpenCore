import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import * as api from './api';
import {ModelProfileOptions} from './ModelProfiles';
import {ImageGenerationPanel,selectChatImageModel,clearChatImageSelection,getChatImageSelection,submitChatImagePrompt,stopChatImagePrompt} from './image-generation-panel.js';

const moss={id:'moss-image-sft-int8-convrot',label:'MOSS Image',category:'image',backend:'comfyui',precision:'INT8',installed:true,selectable:false};
const qwen={id:'qwen-image-21-uc-nvfp4',label:'Qwen Image',category:'image',backend:'comfyui',precision:'NVFP4',installed:true,selectable:false};

afterEach(()=>{cleanup();clearChatImageSelection();vi.restoreAllMocks();delete (window as any).__TAURI_INTERNALS__;delete (globalThis as any).__opencoreImageAdapters;});

describe('image models in the regular chat picker',()=>{
  it('stops automatic preparation through the actual native cancellation command',async()=>{
    const calls:any[]=[];(window as any).__TAURI_INTERNALS__={invoke:async(command:string,args:any={})=>{calls.push({command,args});
      if(command==='list_model_library')return{models:[moss]};if(command==='list_studio_jobs')return[];
      if(command==='runtime_setup_status')return{receipts:[],recipes:[{modelIds:[moss.id]}],jobs:[]};
      if(command==='runtime_setup_start')return{id:'pending-runtime'};return null;}};
    render(<ImageGenerationPanel conversationId="cancel-image"/>);selectChatImageModel(moss);await screen.findByText('MOSS Image · Automatic quality');submitChatImagePrompt({prompt:'A blue cup'});
    await waitFor(()=>expect(calls.some(call=>call.command==='runtime_setup_start')).toBe(true));
    stopChatImagePrompt();await waitFor(()=>expect(calls).toContainEqual({command:'runtime_setup_cancel',args:{jobId:'pending-runtime'}}));
    expect(calls.some(call=>call.command==='submit_studio_job')).toBe(false);
  });
  it('submits the chosen LoRA with its base and rechecks removed base weights before queuing',async()=>{
    let installed=true;const calls:any[]=[];
    const adapter={id:'qa-lora',name:'MOSS add-on',repository:'axiomofmind/Moss-image-nsfw-uncensored-lora',variantKey:'lora:adapter-ComfyUI.safetensors',format:'lora',status:'installed',baseRepository:'OpenMOSS-Team/MOSS-Image-SFT',installReceipt:{files:[{role:'model',localPath:'C:/managed/adapter.safetensors'}]}};
    (globalThis as any).__opencoreImageAdapters=async()=>[adapter];
    (window as any).__TAURI_INTERNALS__={invoke:async(command:string,args:any={})=>{calls.push({command,args});
      if(command==='list_model_library')return{models:[{...moss,installed,runtimeConnected:true}]};
      if(command==='list_studio_jobs')return[];
      if(command==='get_snapshot')return{runtime:{status:'stopped'},activeConversationIds:[]};
      if(command==='runtime_setup_status')return{receipts:[{targetId:moss.id,dependenciesVerified:true,inferenceVerified:true,python:'C:/managed/python.exe'}]};
      if(command==='submit_studio_job')return{id:'image',status:'completed',request:args.request,outputs:[]};
      return null;}};
    render(<ImageGenerationPanel conversationId="lora-chat"/>);
    selectChatImageModel({...moss,adapterId:adapter.id,adapterLabel:adapter.name});
    await screen.findByText(/MOSS Image.*MOSS add-on.*Automatic quality/);
    expect(screen.queryByText('Image style')).toBeNull();
    submitChatImagePrompt({prompt:'A blue cup'});
    await waitFor(()=>expect(calls.some(call=>call.command==='submit_studio_job')).toBe(true));
    expect(calls.find(call=>call.command==='submit_studio_job').args.request.settings).toMatchObject({loraPath:'C:/managed/adapter.safetensors',loraStrength:.8});
    await waitFor(()=>expect(screen.queryByText(/Preparing image generation/)).toBeNull());
    installed=false;submitChatImagePrompt({prompt:'A second cup'});
    await screen.findByText(/no longer installed/i);
    expect(calls.filter(call=>call.command==='submit_studio_job')).toHaveLength(1);
  });
  it('offers downloaded image generators through a separate image selection callback',async()=>{
    const inventory=await api.modelLibrary();
    vi.spyOn(api,'modelLibrary').mockResolvedValue({...inventory,models:[moss,qwen,{...qwen,id:'missing',installed:false},{...moss,id:'adapter',hubEntry:{packageKind:'adapter',format:'lora'}}] as any});
    const text=vi.fn(),image=vi.fn();
    render(<ModelProfileOptions selectedProfile="echo" onSelect={text} onSelectImage={image}/>);
    const option=await screen.findByRole('button',{name:/Qwen Image.*NVFP4/});
    expect(screen.getByRole('button',{name:/MOSS Image.*INT8/})).toBeVisible();
    expect(screen.queryByRole('button',{name:/missing|adapter/})).toBeNull();
    fireEvent.click(option);
    expect(image.mock.calls[0][0].id).toBe(qwen.id);
    expect(text).not.toHaveBeenCalled();
  });

  it('opens the selected image model in this conversation and submits it without a text runtime',async()=>{
    const calls:any[]=[];
    (window as any).__TAURI_INTERNALS__={invoke:vi.fn(async(command:string,args:any={})=>{
      calls.push({command,args});
      if(command==='list_model_library')return {models:[moss,qwen]};
      if(command==='list_studio_jobs')return [];
      if(command==='runtime_setup_status')return {receipts:[{targetId:qwen.id,dependenciesVerified:true,inferenceVerified:true,python:'C:/managed/python.exe'}]};
      if(command==='submit_studio_job')return {id:'image-job',category:'image',status:'completed',request:args.request,outputs:[]};
      return null;
    })};
    render(<ImageGenerationPanel conversationId="opencore:image-picker-test"/>);
    expect(screen.queryByLabelText('Downloaded image model')).toBeNull();
    selectChatImageModel(qwen);
    await screen.findByText('Qwen Image · Automatic quality');
    expect(screen.queryByLabelText('Image generation prompt')).toBeNull();
    submitChatImagePrompt({prompt:'A blue cup'});
    await waitFor(()=>expect(calls.some(call=>call.command==='submit_studio_job')).toBe(true));
    const request=calls.find(call=>call.command==='submit_studio_job').args.request;
    expect(request.modelId).toBe(qwen.id);
    expect(request.conversationId).toBe('opencore:image-picker-test');
    expect(calls.some(call=>call.command==='start_runtime'||call.command==='send_message')).toBe(false);
    clearChatImageSelection();
    await waitFor(()=>expect(screen.queryByLabelText('Downloaded image model')).toBeNull());
  });

  it('does not send a selection from one composer into another chat',async()=>{
    const invoke=vi.fn(async(command:string)=>command==='list_model_library'?{models:[moss,qwen]}:[]);
    (window as any).__TAURI_INTERNALS__={invoke};
    const first=render(<div className="chat-composer-wrap"><ImageGenerationPanel conversationId="one"/></div>);
    const second=render(<div className="chat-composer-wrap"><ImageGenerationPanel conversationId="two" embedded/></div>);
    selectChatImageModel(qwen,{scope:second.container,primary:false});
    await waitFor(()=>expect(second.container.textContent).toContain('Qwen Image · Automatic quality'));
    expect(first.container.textContent).not.toContain('Automatic quality');
    expect(getChatImageSelection()).toBeNull();
  });
});
