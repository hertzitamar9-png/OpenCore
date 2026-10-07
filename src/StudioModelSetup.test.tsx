import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { StudioModelSetup } from './StudioModelSetup';
import * as api from './api';
import * as setup from './runtimeSetupApi';
afterEach(()=>vi.restoreAllMocks());
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
