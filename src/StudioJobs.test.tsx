import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { GenerationForm, StudioJobs } from './StudioJobs';
import * as api from './api';
import * as setup from './StudioModelSetup';
import * as runtimeSetup from './runtimeSetupApi';

it.each([
  ['image', 'Negative prompt'], ['3d', 'Mesh resolution'], ['3d-animation', 'Motion description'], ['2d-animation', 'Frame count'],
])('offers Browse models for %s while keeping draft controls available', async (category, control) => {
  vi.spyOn(api, 'modelLibrary').mockResolvedValue({models: [], progress: null, diskFreeBytes: 88e9, minimumFreeBytes: 64e6});
  const browse = vi.fn();
  render(<GenerationForm category={category} onNotice={vi.fn()} onBrowseModels={browse} />);
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'A game asset'}});
  expect(screen.getByLabelText(control)).toBeVisible();
  expect(screen.getByRole('combobox', {name: 'Presets'})).toBeVisible();
  expect(screen.getByRole('button', {name: 'Generate'})).toBeDisabled();
  fireEvent.click(await screen.findByRole('button', {name: 'Browse models'}));
  expect(browse).toHaveBeenCalledWith(category);
});
afterEach(()=>vi.restoreAllMocks());
const music: api.InstalledModel={id:'yue2',label:'YuE2',category:'music',precision:'BF16',installed:true,selectable:false,externalManaged:true,description:'Music',license:'CC-BY-NC',experimental:true,note:'',contextTokens:0,downloadBytes:0,totalBytes:1};
it('defaults to FLUX.2 Klein 4B and its controls when older uninstalled image models appear first', async () => {
  const old = {...music, id: 'sana-16', label: 'Sana', category: 'image', installed: false};
  const latest = {...old, id: 'flux-2-klein-4b', label: 'FLUX.2 Klein 4B', backend: 'external'};
  vi.spyOn(api, 'modelLibrary').mockResolvedValue({models: [old, latest], progress: null, diskFreeBytes: 88e9, minimumFreeBytes: 64e6});
  vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  render(<GenerationForm category="image" onNotice={vi.fn()} />);
  await waitFor(() => expect(screen.getByLabelText('Model')).toHaveValue(latest.id));
  await waitFor(() => expect(screen.getByLabelText('Inference steps')).toHaveValue(4));
  expect(screen.getByLabelText('Guidance scale')).toHaveValue(1);
  expect(screen.getByRole('button', {name: 'Generate'})).toBeDisabled();
});
it.each([
  ['image', 'Inference steps'], ['3d', 'Mesh resolution'],
  ['3d-animation', 'Motion description'], ['2d-animation', 'Frame count'],
])('shows editable %s controls before downloading a model', async (category, control) => {
  const model: api.InstalledModel = {...music, id: `${category}-model`, label: 'Publisher model', category, installed: false, externalManaged: false, installable: true, backend: 'external', downloadBytes: 2000000000, totalBytes: 2000000000};
  vi.spyOn(api, 'modelLibrary').mockResolvedValue({models: [model], progress: null, diskFreeBytes: 88e9, minimumFreeBytes: 64e6});
  vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  const install = vi.spyOn(api, 'installModel').mockResolvedValue();
  render(<GenerationForm category={category} onNotice={vi.fn()} />);
  await screen.findByRole('option', {name: 'Publisher model'});
  expect(screen.getByLabelText(control)).toBeVisible();
  expect(screen.getByLabelText('Preset name')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'A game character'}});
  expect(screen.getByRole('button', {name: 'Generate'})).toBeDisabled();
  expect(install).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: /Install weights/}));
  await waitFor(() => expect(install).toHaveBeenCalledWith(`${category}-model`));
});
it('keeps generation controls editable when a category has no catalog entries', async () => {
  vi.spyOn(api, 'modelLibrary').mockResolvedValue({models: [], progress: null, diskFreeBytes: 88e9, minimumFreeBytes: 64e6});
  vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  render(<GenerationForm category="image" onNotice={vi.fn()} />);
  expect(screen.getByLabelText('Width')).toBeVisible();
  expect(screen.getByRole('button', {name: 'Generate'})).toBeDisabled();
});
it('requires the matching runtime before generating with installed image weights', async () => {
  const model: api.InstalledModel = {...music, id: 'sana-16', label: 'Sana', category: 'image', installed: true, backend: 'diffusers'};
  vi.spyOn(api, 'modelLibrary').mockResolvedValue({models: [model], progress: null, diskFreeBytes: 88e9, minimumFreeBytes: 64e6});
  vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  render(<GenerationForm category="image" onNotice={vi.fn()} />);
  await screen.findByRole('option', {name: 'Sana'});
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'A game environment'}});
  expect(screen.getByRole('button', {name: 'Generate'})).toBeDisabled();
});
it('refreshes the selected managed runtime after setup completes without clearing the draft', async () => {
  const model: api.InstalledModel = {...music, id: 'sana-16', label: 'Sana', category: 'image', installed: true, backend: 'diffusers'};
  vi.spyOn(api, 'modelLibrary').mockResolvedValue({models: [model], progress: null, diskFreeBytes: 88e9, minimumFreeBytes: 64e6});
  const runtime = vi.spyOn(api, 'studioRuntime').mockResolvedValueOnce(null).mockResolvedValue({modelId: model.id, python: 'C:/managed/python.exe', runner: null, sourceDir: null});
  vi.spyOn(setup, 'StudioModelSetup').mockImplementation(({onRefresh}) => <button onClick={() => void onRefresh()}>Setup completed</button>);
  render(<GenerationForm category="image" onNotice={vi.fn()} />);
  await screen.findByRole('option', {name: 'Sana'});
  await waitFor(() => expect(runtime).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'Keep this draft after setup'}});
  expect(screen.getByRole('button', {name: 'Generate'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button', {name: 'Setup completed'}));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Generate'})).toBeEnabled());
  expect(runtime).toHaveBeenCalledTimes(2);
  expect(screen.getByLabelText('Prompt')).toHaveValue('Keep this draft after setup');
});
it('shows TRELLIS 2 supported resolution controls before installation', async () => {
  const model: api.InstalledModel = {...music, id: 'trellis-2-4b', label: 'TRELLIS.2', category: '3d', installed: false, backend: 'external'};
  vi.spyOn(api, 'modelLibrary').mockResolvedValue({models: [model], progress: null, diskFreeBytes: 88e9, minimumFreeBytes: 64e6});
  vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  render(<GenerationForm category="3d" onNotice={vi.fn()} />);
  await screen.findByRole('option', {name: 'TRELLIS.2'});
  await waitFor(() => expect(screen.getByLabelText('Mesh resolution')).toHaveValue(512));
  expect(screen.getByLabelText('Mesh resolution')).toHaveAttribute('max', '1536');
  expect(screen.queryByLabelText('Render chunk size')).not.toBeInTheDocument();
});
it('requires a driving video and reference image for Wan Animate 2 and sends their exact paths', async () => {
  const model: api.InstalledModel = {...music, id: 'wan-animate-2-distilled', label: 'Wan Animate 2 distilled', category: '2d-animation', installed: true, backend: 'external'};
  vi.spyOn(api, 'modelLibrary').mockResolvedValue({models: [model], progress: null, diskFreeBytes: 88e9, minimumFreeBytes: 64e6});
  vi.spyOn(api, 'studioRuntime').mockResolvedValue({modelId: model.id, python: 'python.exe', runner: 'worker.py', sourceDir: 'C:\\wan'});
  vi.spyOn(api, 'pickStudioFile').mockResolvedValueOnce('C:\\assets\\character.png').mockResolvedValueOnce('C:\\assets\\driving.mp4');
  const submit = vi.spyOn(api, 'submitStudioJob').mockResolvedValue({} as api.StudioJob);
  render(<GenerationForm category="2d-animation" onNotice={vi.fn()} />);
  await screen.findByRole('option', {name: 'Wan Animate 2 distilled'});
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'A silver cartoon cat wearing a school uniform'}});
  await waitFor(() => expect(screen.getByLabelText('Inference steps')).toHaveValue(10));
  expect(screen.getByLabelText('Height')).toBeValid();
  expect(screen.getByLabelText('Output format')).toHaveValue('mp4');
  expect(screen.getByRole('button', {name: 'Generate'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button', {name: 'Choose image or asset'}));
  await screen.findByText('C:\\assets\\character.png');
  expect(screen.getByRole('button', {name: 'Generate'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button', {name: 'Choose driving video'}));
  await waitFor(() => expect(screen.getByLabelText('Driving video path')).toHaveValue('C:\\assets\\driving.mp4'));
  fireEvent.click(screen.getByRole('button', {name: 'Generate'}));
  await waitFor(() => expect(submit).toHaveBeenCalledWith({modelId: model.id, prompt: 'A silver cartoon cat wearing a school uniform', settings: {seed: 831001, steps: 10, width: 640, height: 800, frameCount: 81, fps: 24, loop: false, outputFormat: 'mp4', guidanceScale: 1, flowSolver: 'euler', drivingVideoPath: 'C:\\assets\\driving.mp4', inputPath: 'C:\\assets\\character.png'}}));
});
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
  const readyRuntime:api.StudioRuntime={modelId:motion.id,python:'python.exe',sourceDir:null,runner:'worker.py'};
  let resolveRuntime!:(runtime:api.StudioRuntime)=>void;
  const runtimeReady=new Promise<api.StudioRuntime>(resolve=>{resolveRuntime=resolve;});
  vi.spyOn(api,'studioRuntime').mockReturnValue(runtimeReady);
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
  const generate=screen.getByRole('button',{name:'Generate'});
  expect(generate).toBeDisabled();
  fireEvent.click(generate);
  expect(submit).not.toHaveBeenCalled();
  await act(async()=>{resolveRuntime(readyRuntime);});
  await waitFor(()=>expect(generate).toBeEnabled());
  fireEvent.click(generate);
  await waitFor(()=>expect(submit).toHaveBeenCalledWith({modelId:'hy-motion-1',prompt:'A character walks into the room',settings:{seed:42,durationSeconds:6,fps:30,frameCount:180,loop:true,outputFormat:'glb',motionPrompt:'Walk slowly, then wave'}}));
});
it.each([
  ['sana-16','Sana 1.6B'],
  ['hunyuan-dit-v12-distilled','Hunyuan-DiT v1.2 Distilled'],
])('prepares %s automatically without requesting an interpreter or custom worker',async(id,label)=>{
  const model:api.InstalledModel={id,label,category:'image',backend:'diffusers',precision:'FP16',installed:true,selectable:false,externalManaged:false,description:'Image generation',license:'Open weights',experimental:true,note:'',contextTokens:0,downloadBytes:1,totalBytes:1};
  vi.spyOn(api,'modelLibrary').mockResolvedValue({models:[model],progress:null,diskFreeBytes:88e9,minimumFreeBytes:64e6});
  vi.spyOn(api,'studioRuntime').mockResolvedValue(null);
  const pick=vi.spyOn(api,'pickStudioFile').mockImplementation(async kind=>kind==='python'?'python.exe':null);
  const configure=vi.spyOn(api,'configureStudioRuntime').mockResolvedValue();
  vi.spyOn(runtimeSetup,'setupDesktopAvailable').mockReturnValue(true);
  const recipe:runtimeSetup.SetupRecipe={id:'images',label:'Image runtime',kind:'studio',modelIds:[id],packages:{},minimumDiskBytes:1e9,minimumRamBytes:8e9,requiresCuda:true,sourceUrls:[],limitations:''};
  vi.spyOn(runtimeSetup,'runtimeSetupStatus').mockResolvedValue({recipes:[recipe],jobs:[],receipts:[],activeJobId:null,managedRoot:'C:/managed'});
  const prepare=vi.spyOn(runtimeSetup,'runtimeSetupStart').mockResolvedValue({id:'setup',targetId:id,recipeId:recipe.id,status:'queued',stage:'queued',detail:'',createdAt:'',updatedAt:'',downloadedBytes:0,totalBytes:0,diagnostics:[],error:null,receipt:null});
  render(<GenerationForm category="image" onNotice={vi.fn()}/>);
  await screen.findByRole('option',{name:label});
  await waitFor(()=>expect(prepare).toHaveBeenCalledWith(id,expect.objectContaining({installWeights:false})));
  expect(screen.queryByRole('button',{name:'Configure publisher runtime'})).not.toBeInTheDocument();
  expect(configure).not.toHaveBeenCalled();
  expect(pick).not.toHaveBeenCalled();
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
it('acknowledges cancellation immediately while the worker stops and ignores stale polling',async()=>{
  const job:api.StudioJob={id:'cancel-now',category:'music',request:{modelId:'yue2',prompt:'A song',settings:{}},status:'running',stage:'Verifying model files',createdAt:'today',updatedAt:'today',backendRun:null,progress:{},outputs:[],error:null};
  vi.spyOn(api,'listStudioJobs').mockResolvedValue([job]);
  let finish!:()=>void;
  vi.spyOn(api,'cancelStudioJob').mockImplementation(()=>new Promise<void>(resolve=>{finish=resolve;}));
  render(<StudioJobs category="music" onNotice={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Cancel'}));
  expect(screen.getByText(/cancelled · Cancelled/)).toBeVisible();
  expect(screen.queryByRole('button',{name:'Cancel'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Generate another version'})).not.toBeInTheDocument();
  finish();
  await waitFor(()=>expect(api.listStudioJobs).toHaveBeenCalledTimes(2));
  expect(screen.getByText(/cancelled · Cancelled/)).toBeVisible();
  expect(screen.queryByText(/running · Verifying/)).not.toBeInTheDocument();
});
