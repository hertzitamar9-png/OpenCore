import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { GenerationForm, StudioJobs } from './StudioJobs';
import * as api from './api';
afterEach(()=>vi.restoreAllMocks());
const music: api.InstalledModel={id:'yue2',label:'YuE2',category:'music',precision:'BF16',installed:true,selectable:false,externalManaged:true,description:'Music',license:'CC-BY-NC',experimental:true,note:'',contextTokens:0,downloadBytes:0,totalBytes:1};
it('submits exact music settings through the same durable job API used by chat',async()=>{
  vi.spyOn(api,'modelLibrary').mockResolvedValue({models:[music],progress:null,diskFreeBytes:88e9,minimumFreeBytes:64e6});
  vi.spyOn(api,'studioRuntime').mockResolvedValue(null);
  const submit=vi.spyOn(api,'submitStudioJob').mockResolvedValue({} as api.StudioJob);
  render(<GenerationForm category="music" onNotice={vi.fn()}/>);
  await screen.findByRole('option',{name:'YuE2'});
  fireEvent.change(screen.getByLabelText('Prompt'),{target:{value:'Make an AI song'}});
  fireEvent.change(screen.getByLabelText('Title'),{target:{value:'Tomorrow'}});
  fireEvent.change(screen.getByLabelText('Style'),{target:{value:'Synth pop'}});
  fireEvent.change(screen.getByLabelText('Lyrics'),{target:{value:'We build tomorrow'}});
  fireEvent.click(screen.getByRole('button',{name:'Generate'}));
  await waitFor(()=>expect(submit).toHaveBeenCalledWith({modelId:'yue2',prompt:'Make an AI song',settings:{title:'Tomorrow',style:'Synth pop',lyrics:'We build tomorrow',memory:{quantization:'none',offload_ar:true}}}));
});
it('builds a customized animation job from an installed animation model',async()=>{
  const motion:api.InstalledModel={id:'hy-motion-1',label:'HY-Motion 1.0',category:'3d-animation',precision:'BF16',installed:true,selectable:false,externalManaged:false,description:'Text-to-motion',license:'Apache',experimental:true,note:'',contextTokens:0,downloadBytes:1,totalBytes:1};
  vi.spyOn(api,'modelLibrary').mockResolvedValue({models:[motion],progress:null,diskFreeBytes:88e9,minimumFreeBytes:64e6});
  vi.spyOn(api,'studioRuntime').mockResolvedValue({modelId:motion.id,python:'python.exe',sourceDir:null,runner:'worker.py'});
  const submit=vi.spyOn(api,'submitStudioJob').mockResolvedValue({} as api.StudioJob);
  render(<GenerationForm category="3d-animation" onNotice={vi.fn()}/>);
  await screen.findByRole('option',{name:'HY-Motion 1.0'});
  fireEvent.change(screen.getByLabelText('Prompt'),{target:{value:'A character walks into the room'}});
  fireEvent.change(screen.getByLabelText('Motion description'),{target:{value:'Walk slowly, then wave'}});
  fireEvent.change(screen.getByLabelText('Seed'),{target:{value:'42'}});
  fireEvent.change(screen.getByLabelText('Duration (seconds)'),{target:{value:'6'}});
  fireEvent.change(screen.getByLabelText('Frames per second'),{target:{value:'30'}});
  fireEvent.change(screen.getByLabelText('Frame count'),{target:{value:'180'}});
  fireEvent.click(screen.getByLabelText('Loop animation'));
  fireEvent.click(screen.getByRole('button',{name:'Generate'}));
  await waitFor(()=>expect(submit).toHaveBeenCalledWith({modelId:'hy-motion-1',prompt:'A character walks into the room',settings:{seed:42,durationSeconds:6,fps:30,frameCount:180,loop:true,outputFormat:'glb',motionPrompt:'Walk slowly, then wave'}}));
});
it.each([
  ['sana-16','Sana 1.6B'],
  ['hunyuan-dit-v12-distilled','Hunyuan-DiT v1.2 Distilled'],
])('connects %s to the built-in image worker without requesting a custom runner',async(id,label)=>{
  const model:api.InstalledModel={id,label,category:'image',backend:'diffusers',precision:'FP16',installed:true,selectable:false,externalManaged:false,description:'Image generation',license:'Open weights',experimental:true,note:'',contextTokens:0,downloadBytes:1,totalBytes:1};
  vi.spyOn(api,'modelLibrary').mockResolvedValue({models:[model],progress:null,diskFreeBytes:88e9,minimumFreeBytes:64e6});
  vi.spyOn(api,'studioRuntime').mockResolvedValue(null);
  const pick=vi.spyOn(api,'pickStudioFile').mockImplementation(async kind=>kind==='python'?'python.exe':null);
  const configure=vi.spyOn(api,'configureStudioRuntime').mockResolvedValue();
  render(<GenerationForm category="image" onNotice={vi.fn()}/>);
  await screen.findByRole('option',{name:label});
  fireEvent.click(screen.getByText(/Runtime connection/));
  fireEvent.click(await screen.findByRole('button',{name:'Connect runtime'}));
  await waitFor(()=>expect(configure).toHaveBeenCalledWith({modelId:id,python:'python.exe',runner:null,sourceDir:null}));
  expect(pick).toHaveBeenCalledWith('python');
  expect(pick).not.toHaveBeenCalledWith('worker');
});
it('loads saved Game Dev generation presets back into the form',async()=>{
  const motion:api.InstalledModel={id:'hy-motion-1',label:'HY-Motion 1.0',category:'3d-animation',precision:'BF16',installed:true,selectable:false,externalManaged:false,description:'Text-to-motion',license:'Apache',experimental:true,note:'',contextTokens:0,downloadBytes:1,totalBytes:1};
  vi.spyOn(api,'modelLibrary').mockResolvedValue({models:[motion],progress:null,diskFreeBytes:88e9,minimumFreeBytes:64e6});
  vi.spyOn(api,'studioRuntime').mockResolvedValue({modelId:motion.id,python:'python.exe',sourceDir:null,runner:'worker.py'});
  const submit=vi.spyOn(api,'submitStudioJob').mockResolvedValue({} as api.StudioJob);
  const key='opencore.game-dev-studio.presets.v1.3d-animation';
  const localValues = new Map<string,string>();
  const storage = { getItem: (name:string) => localValues.get(name) ?? null, setItem: (name:string,value:string) => localValues.set(name,value), removeItem: (name:string) => localValues.delete(name) };
  const descriptor = Object.getOwnPropertyDescriptor(window,'localStorage');
  const globalDescriptor = Object.getOwnPropertyDescriptor(globalThis,'localStorage');
  Object.defineProperty(window,'localStorage',{configurable:true,value:storage});
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:storage});
  window.localStorage.setItem(key,JSON.stringify([{id:'walk',name:'Slow walk',modelId:'hy-motion-1',prompt:'A character walks',inputPath:'',advancedJson:'{}',controls:{seed:42,durationSeconds:6,fps:30,frameCount:180,loop:true,outputFormat:'glb',motionPrompt:'Walk slowly'}}]));
  try {
    render(<GenerationForm category="3d-animation" onNotice={vi.fn()}/>);
    await screen.findByRole('option',{name:'HY-Motion 1.0'});
    fireEvent.change(screen.getByLabelText('Presets'),{target:{value:'walk'}});
    expect(screen.getByLabelText('Prompt')).toHaveValue('A character walks');
    expect(screen.getByLabelText('Motion description')).toHaveValue('Walk slowly');
    fireEvent.click(screen.getByRole('button',{name:'Generate'}));
    await waitFor(()=>expect(submit).toHaveBeenCalledWith({modelId:'hy-motion-1',prompt:'A character walks',settings:{seed:42,durationSeconds:6,fps:30,frameCount:180,loop:true,outputFormat:'glb',motionPrompt:'Walk slowly'}}));
  } finally {
    if(descriptor)Object.defineProperty(window,'localStorage',descriptor);else delete (window as unknown as {localStorage?:Storage}).localStorage;
    if(globalDescriptor)Object.defineProperty(globalThis,'localStorage',globalDescriptor);else delete (globalThis as unknown as {localStorage?:Storage}).localStorage;
  }
});
it('shows chat-submitted prompts, progress and failures without calling them completed',async()=>{
  const job:api.StudioJob={id:'job',category:'music',request:{modelId:'yue2',prompt:'Make an AI song',settings:{lyrics:'Original lyrics'},conversationId:'chat'},status:'queued',stage:'Waiting for chat and GPU',createdAt:'today',updatedAt:'today',backendRun:null,progress:{},outputs:[],error:null};
  vi.spyOn(api,'listStudioJobs').mockResolvedValue([job]);const cancel=vi.spyOn(api,'cancelStudioJob').mockResolvedValue();
  render(<StudioJobs category="music" onNotice={vi.fn()}/>);
  expect(await screen.findByText('Make an AI song')).toBeVisible();
  expect(screen.getByText('queued · Waiting for chat and GPU')).toBeVisible();
  expect(screen.getByText(/Original lyrics/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Cancel'}));await waitFor(()=>expect(cancel).toHaveBeenCalledWith('job'));
});
