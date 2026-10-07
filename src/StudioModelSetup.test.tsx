import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { StudioModelSetup } from './StudioModelSetup';
import * as api from './api';
import * as setup from './runtimeSetupApi';
const bridge = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock('@tauri-apps/api/core', () => ({ invoke: bridge.invoke }));

afterEach(()=>{vi.restoreAllMocks(); bridge.invoke.mockReset(); Reflect.deleteProperty(window, '__TAURI_INTERNALS__');});
it('installs and connects a supported studio runtime with one managed action',async()=>{
  const recipe:setup.SetupRecipe={id:'diffusers-images-v1',label:'Image runtime',kind:'studio',modelIds:['sana-16'],packages:{},minimumDiskBytes:1e9,minimumRamBytes:8e9,requiresCuda:true,sourceUrls:[],limitations:''};
  vi.spyOn(setup,'runtimeSetupStatus').mockResolvedValue({recipes:[recipe],jobs:[],receipts:[],activeJobId:null,managedRoot:'C:/managed'});
  vi.spyOn(setup,'setupDesktopAvailable').mockReturnValue(true);
  const start=vi.spyOn(setup,'runtimeSetupStart').mockResolvedValue({id:'setup',targetId:'sana-16',recipeId:recipe.id,status:'queued',stage:'queued',detail:'',createdAt:'',updatedAt:'',downloadedBytes:0,totalBytes:0,diagnostics:[],error:null,receipt:null});
  const weights=vi.spyOn(api,'installModel').mockResolvedValue();
  const model:api.InstalledModel={id:'sana-16',label:'Sana',description:'Image model',selectable:false,precision:'BF16',contextTokens:0,license:'apache-2.0',experimental:false,note:'',installed:false,externalManaged:false,downloadBytes:1000000,totalBytes:1000000,category:'image'};
  render(<StudioModelSetup model={model} connected={false} onRefresh={async()=>{}} onNotice={()=>{}}/>);
  expect(screen.queryByRole('button',{name:/Install weights/})).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole('button',{name:/Install and prepare automatically/}));
  await waitFor(()=>expect(start).toHaveBeenCalledWith('sana-16',expect.objectContaining({installWeights:true})));
  expect(weights).not.toHaveBeenCalled();
});

it('ignores an old terminal receipt while the initial fresh install read waits across a poll tick', async () => {
  Object.defineProperty(window, '__TAURI_INTERNALS__', { configurable: true, value: {} });
  vi.spyOn(setup, 'setupDesktopAvailable').mockReturnValue(false);
  vi.spyOn(setup, 'runtimeSetupStatus').mockResolvedValue({ recipes: [], jobs: [], receipts: [], activeJobId: null, managedRoot: '' });
  const model: api.InstalledModel = { id: 'publisher-test', label: 'Publisher model', description: '', selectable: false,
    precision: 'BF16', contextTokens: 0, license: 'apache-2.0', experimental: false, note: '', installed: false,
    externalManaged: false, downloadBytes: 100, totalBytes: 100, category: 'image' };
  const progress = { modelId: model.id, phase: 'downloading', downloadedBytes: 20, totalBytes: 100, currentFile: 'weights', error: null };
  const library: api.ModelLibrary = { models: [model], progress, diskFreeBytes: 1e9, minimumFreeBytes: 0 };
  let finishOld!: (library: api.ModelLibrary) => void;
  bridge.invoke.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; })).mockResolvedValue(library);
  const oldPoll = api.modelLibrary();
  vi.spyOn(api, 'installModel').mockResolvedValue();
  let tick: (() => void) | undefined;
  const originalInterval = globalThis.setInterval.bind(globalThis);
  vi.spyOn(globalThis, 'setInterval').mockImplementation((callback, delay, ...args) => {
    if (delay === 1000 && typeof callback === 'function') tick = callback as () => void;
    return originalInterval(callback, delay, ...args);
  });
  const refresh = vi.fn(async () => {});
  const view = render(<StudioModelSetup model={model} connected={false} onRefresh={refresh} onNotice={()=>{}} />);
  try {
    fireEvent.click(screen.getByRole('button', { name: /Install weights/ }));
    await waitFor(() => expect(tick).toBeTypeOf('function'));
    await act(async () => tick!());
    expect(bridge.invoke).toHaveBeenCalledTimes(1);
    await act(async () => { finishOld({ ...library, progress: { ...progress, phase: 'complete', downloadedBytes: 100 } }); await oldPoll; });
    await waitFor(() => expect(screen.getByRole('progressbar', { name: 'Weight download progress' })).toHaveValue(20));
    expect(refresh).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Cancel download' })).toBeEnabled();
    expect(bridge.invoke).toHaveBeenCalledTimes(2);
  } finally { finishOld(library); view.unmount(); }
});
