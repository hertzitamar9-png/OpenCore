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
it('shows chat-submitted prompts, progress and failures without calling them completed',async()=>{
  const job:api.StudioJob={id:'job',category:'music',request:{modelId:'yue2',prompt:'Make an AI song',settings:{lyrics:'Original lyrics'},conversationId:'chat'},status:'queued',stage:'Waiting for chat and GPU',createdAt:'today',updatedAt:'today',backendRun:null,progress:{},outputs:[],error:null};
  vi.spyOn(api,'listStudioJobs').mockResolvedValue([job]);const cancel=vi.spyOn(api,'cancelStudioJob').mockResolvedValue();
  render(<StudioJobs category="music" onNotice={vi.fn()}/>);
  expect(await screen.findByText('Make an AI song')).toBeVisible();
  expect(screen.getByText('queued · Waiting for chat and GPU')).toBeVisible();
  expect(screen.getByText(/Original lyrics/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Cancel'}));await waitFor(()=>expect(cancel).toHaveBeenCalledWith('job'));
});
