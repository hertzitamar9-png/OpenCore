import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LearningStudio } from './LearningStudio';
import * as learning from './learningApi';
import { defaultLearningConfig } from './learning-config';
import { useState } from 'react';
vi.mock('@tauri-apps/api/event',()=>({listen:vi.fn().mockResolvedValue(()=>{})}));
vi.mock('@tauri-apps/plugin-dialog',()=>({open:vi.fn().mockResolvedValue(null)}));
afterEach(()=>{vi.restoreAllMocks();vi.useRealTimers();});
const snapshot:learning.LearningSnapshot={runs:[],active:false,root:'C:/Learning',rawRecords:12};
const renderAssistant=vi.fn(()=> <p>Real assistant slot</p>);
function mockCommand(records:learning.LearningRecord[]=[],runs:learning.LearningRun[]=[]){
  return vi.spyOn(learning,'learningCommand').mockImplementation(async(args)=>{
    if(args.action==='status')return {...snapshot,runs} as never;
    if(args.action==='query')return {records,total:records.length,nextCursor:null} as never;
    if(args.action==='logs')return {path:'events.jsonl',content:'',nextOffset:0,totalBytes:0,complete:true,rawFilePreserved:true} as never;
    return {} as never;
  });
}
it('prepares AI configuration without silently launching a training run',async()=>{
  const command=mockCommand();
  render(<LearningStudio onNotice={()=>{}} renderAssistant={renderAssistant}/>);
  await screen.findByText('12 raw records');
  fireEvent.click(screen.getByRole('button',{name:'AI configuration'}));
  fireEvent.change(screen.getByLabelText('What should improve?'),{target:{value:'Follow my code conventions'}});
  fireEvent.click(screen.getByRole('button',{name:'Prepare assistant request'}));
  await waitFor(()=>expect(renderAssistant).toHaveBeenLastCalledWith(expect.stringContaining('Do not start training'),expect.any(Number),expect.any(Function),undefined));
  expect(command.mock.calls.some(([args])=>args.action==='start')).toBe(false);
});

it('runs a reviewed AI configuration as an explicit manual training run',async()=>{
  const command=mockCommand();
  command.mockImplementation(async(args)=>{
    if(args.action==='status')return snapshot as never;
    if(args.action==='start')return {id:'reviewed-run',name:'Reviewed configuration',status:'queued',mode:args.mode,createdAt:'2026-10-07T13:14:59Z',updatedAt:'2026-10-07T13:14:59Z',modelPath:args.modelPath,config:args.config,dataset:{}} as never;
    if(args.action==='logs')return {path:'events.jsonl',content:'',nextOffset:0,totalBytes:0,complete:true,rawFilePreserved:true} as never;
    return {} as never;
  });
  render(<LearningStudio onNotice={()=>{}} renderAssistant={renderAssistant}/>);
  fireEvent.click(screen.getByRole('button',{name:'AI configuration'}));
  fireEvent.click(screen.getByRole('tab',{name:'Configuration'}));
  fireEvent.change(screen.getByPlaceholderText('Choose a local Transformers model folder'),{target:{value:'C:/original-model'}});
  fireEvent.click(screen.getByRole('button',{name:'Start local run'}));
  await waitFor(()=>expect(command).toHaveBeenCalledWith(expect.objectContaining({action:'start',mode:'manual',modelPath:'C:/original-model'})));
  expect(await screen.findByRole('button',{name:/Reviewed configuration/})).toBeVisible();
});

it('names DPO acceptance gates for preference margin and chosen-answer loss',()=>{
  mockCommand();
  render(<LearningStudio onNotice={()=>{}} renderAssistant={renderAssistant}/>);
  fireEvent.click(screen.getByRole('tab',{name:'Configuration'}));
  fireEvent.change(screen.getByLabelText('Method'),{target:{value:'dpo'}});
  expect(screen.getByLabelText('Minimum held-out preference margin improvement')).toBeVisible();
  expect(screen.getByLabelText('Maximum chosen-answer loss regression')).toBeVisible();
  expect(screen.queryByLabelText('Minimum held-out loss improvement')).not.toBeInTheDocument();
});
it('shows the full original wrong response and keeps failure annotations separate',async()=>{
  const content='This was the wrong answer.\n'+'original '.repeat(100)+'PRESERVE THE FINAL BYTES';
  mockCommand([{id:'raw',sourceKind:'timeline',sourceId:'42',revision:1,timestamp:'2026-10-07T13:14:59Z',ingestedAt:'2026-10-07T13:15:00Z',kind:'message',role:'assistant',source:'OpenCore',title:'Wrong answer',content,contentBytes:content.length,contentSha256:'hash',sourceSha256:'sourcehash',evidenceLabel:'failed',status:'failed',metadata:{},raw:{original:content}}]);
  render(<LearningStudio onNotice={()=>{}} renderAssistant={renderAssistant}/>);
  fireEvent.click(screen.getByRole('tab',{name:'Raw records & datasets'}));
  fireEvent.click(await screen.findByRole('button',{name:/Wrong answer/}));
  expect(screen.getByText((_,element)=>element?.tagName==='PRE'&&element.textContent===content).textContent).toContain('PRESERVE THE FINAL BYTES');
  expect(screen.getByRole('button',{name:'Mark known failure'})).toBeVisible();
  expect(screen.getByText(/bytes/)).toBeVisible();
});
it('shows rejected metrics and exact gate reasons as a final evaluated result',async()=>{
  mockCommand([],[{id:'rejected',name:'Style run',status:'rejected',mode:'auto',createdAt:'2026-10-07T13:14:59Z',updatedAt:'2026-10-07T13:16:04Z',modelPath:'C:/base',config:defaultLearningConfig,dataset:{},receipt:{status:'rejected',baseline:{loss:1.1},candidate:{loss:1.2},reasons:['Held-out loss increased from 1.1 to 1.2']}}]);
  render(<LearningStudio onNotice={()=>{}} renderAssistant={renderAssistant}/>);
  fireEvent.click(screen.getByRole('tab',{name:'Runs & checkpoints'}));
  expect((await screen.findAllByText(/Held-out loss increased from 1.1 to 1.2/,{selector:'pre'}))[0]).toBeVisible();
  expect(screen.queryByRole('button',{name:'Continue'})).not.toBeInTheDocument();
});

it('keeps the same assistant and its draft while switching tabs and preparing a request',async()=>{
  mockCommand();
  function Assistant(){const [draft,setDraft]=useState('');return <input aria-label="Assistant draft" value={draft} onChange={event=>setDraft(event.target.value)}/>;}
  render(<LearningStudio onNotice={()=>{}} renderAssistant={()=> <Assistant/>}/>);
  const input=screen.getByLabelText('Assistant draft');
  fireEvent.change(input,{target:{value:'Keep my unsent question'}});
  fireEvent.click(screen.getByRole('tab',{name:'Configuration'}));
  expect(input).toBeInTheDocument();
  expect(input).not.toBeVisible();
  fireEvent.click(screen.getByRole('tab',{name:'Assistant'}));
  expect(screen.getByLabelText('Assistant draft')).toBe(input);
  fireEvent.click(screen.getByRole('button',{name:'Prepare assistant request'}));
  expect(input).toHaveValue('Keep my unsent question');
});

it('shows queued environment preparation in Runs and treats ready as terminal',async()=>{
  const run:learning.LearningRun={id:'setup',name:'Unsloth environment setup',kind:'environment-setup',stage:'Environment import probe passed',status:'ready',mode:'manual',createdAt:'2026-10-07T13:14:59Z',updatedAt:'2026-10-07T13:16:04Z',modelPath:'',config:defaultLearningConfig,dataset:{},environmentReceipt:{ready:true},logNames:['setup.log','setup-receipt.json']};
  const command=mockCommand();
  command.mockImplementation(async(args)=>{
    if(args.action==='status')return {...snapshot,runs:[]} as never;
    if(args.action==='setup')return run as never;
    if(args.action==='logs')return {path:'C:/Learning/runs/setup/setup.log',content:'Environment ready',nextOffset:17,totalBytes:17,complete:true} as never;
    return {} as never;
  });
  render(<LearningStudio onNotice={()=>{}} renderAssistant={renderAssistant}/>);
  fireEvent.click(screen.getByRole('tab',{name:'Configuration'}));
  fireEvent.click(screen.getByRole('button',{name:'Prepare automatically'}));
  await waitFor(()=>expect(screen.getByRole('tab',{name:'Runs & checkpoints'})).toHaveAttribute('aria-selected','true'));
  expect(screen.getAllByText('Unsloth environment setup').length).toBeGreaterThan(0);
  expect(screen.queryByRole('button',{name:'Continue'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Cancel'})).not.toBeInTheDocument();
  expect(screen.getByText('Environment setup receipt')).toBeVisible();
  expect(screen.getByText('Environment import probe passed')).toBeVisible();
  expect(await screen.findByText('C:/Learning/runs/setup/setup.log',{selector:'code'})).toBeVisible();
});

it('shows measured training rejection gates instead of its successful environment probe',async()=>{
  const run:learning.LearningRun={id:'rejected-run',name:'Measured training',status:'rejected',mode:'manual',createdAt:'2026-10-07T13:14:59Z',updatedAt:'2026-10-07T13:16:04Z',modelPath:'C:/model',config:defaultLearningConfig,dataset:{},environmentReceipt:{status:'ready',trainingPerformed:false},receipt:{status:'rejected',gates:{reason:'Held-out completion loss did not improve',baseline:1.2,candidate:1.3}}};
  mockCommand([], [run]);
  render(<LearningStudio onNotice={()=>{}} renderAssistant={renderAssistant}/>);
  await screen.findByText('12 raw records');
  fireEvent.click(screen.getByRole('tab',{name:'Runs & checkpoints'}));
  const details=screen.getByText('Exact evaluation receipt and rejection gates').closest('details');
  expect(details).toHaveTextContent('Held-out completion loss did not improve');
  expect(details).toHaveTextContent('1.3');
  expect(details).not.toHaveTextContent('trainingPerformed');
});

it('prepares automatic tuning in the assistant without starting from the configuration button',async()=>{
  const command=mockCommand();
  render(<LearningStudio onNotice={()=>{}} renderAssistant={renderAssistant}/>);
  fireEvent.click(screen.getByRole('tab',{name:'Configuration'}));
  fireEvent.click(screen.getByRole('button',{name:'Prepare automatic tuning request'}));
  expect(screen.getByRole('tab',{name:'Assistant'})).toHaveAttribute('aria-selected','true');
  expect(renderAssistant).toHaveBeenLastCalledWith(expect.stringContaining('Run automatic tuning end to end'),expect.any(Number),expect.any(Function),undefined);
  expect(command.mock.calls.some(([args])=>args.action==='start')).toBe(false);
});

it('blocks the assistant while a pending environment setup is being handed to the worker',async()=>{
  const run:learning.LearningRun={id:'pending-setup',name:'Unsloth environment setup',status:'queued',mode:'manual',createdAt:'2026-10-07T13:14:59Z',updatedAt:'2026-10-07T13:16:04Z',modelPath:'',config:defaultLearningConfig,dataset:{}};
  const command=mockCommand();
  command.mockImplementation(async(args)=>{
    if(args.action==='status')return {...snapshot,runs:[]} as never;
    if(args.action==='setup')return run as never;
    if(args.action==='logs')return {content:'',nextOffset:0,totalBytes:0,complete:true} as never;
    return {} as never;
  });
  render(<LearningStudio onNotice={()=>{}} renderAssistant={renderAssistant}/>);
  await screen.findByText('12 raw records');
  fireEvent.click(screen.getByRole('tab',{name:'Configuration'}));
  fireEvent.click(screen.getByRole('button',{name:'Prepare automatically'}));
  await waitFor(()=>expect(renderAssistant).toHaveBeenLastCalledWith('',0,expect.any(Function),expect.stringContaining('worker')));
});

it('appends newly polled raw bytes without resetting an expanded log to its first page',async()=>{
  vi.useFakeTimers();
  const run:learning.LearningRun={id:'running',name:'Active run',status:'running',mode:'manual',createdAt:'2026-10-07T13:14:59Z',updatedAt:'2026-10-07T13:16:04Z',modelPath:'C:/base',config:defaultLearningConfig,dataset:{}};
  const command=mockCommand([], [run]);
  command.mockImplementation(async(args)=>{
    if(args.action==='status')return {...snapshot,runs:[run]} as never;
    if(args.action==='logs'){const offset=Number(args.offset)||0;return {content:'ABC'.slice(offset,offset+1),nextOffset:Math.min(offset+1,3),totalBytes:3,complete:offset>=2} as never;}
    return {} as never;
  });
  render(<LearningStudio onNotice={()=>{}} renderAssistant={renderAssistant}/>);
  await act(async()=>{});
  fireEvent.click(screen.getByRole('tab',{name:'Runs & checkpoints'}));
  await act(async()=>{});
  expect(screen.getByText('A',{selector:'pre'})).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'Read next bytes'}));
  await act(async()=>{});
  expect(screen.getByText('AB',{selector:'pre'})).toBeVisible();
  await act(async()=>{vi.advanceTimersByTime(2000);});
  expect(screen.getByText('ABC',{selector:'pre'})).toBeVisible();
  expect(command.mock.calls.filter(([args])=>args.action==='logs').map(([args])=>args.offset)).toEqual([0,1,2]);
});

it('can search older original revisions when Latest revisions only is unchecked',async()=>{
  const old:learning.LearningRecord={id:'old',sourceKind:'timeline',sourceId:'42',revision:1,timestamp:'2026-10-07T13:14:59Z',ingestedAt:'2026-10-07T13:15:00Z',kind:'message',role:'assistant',source:'OpenCore',title:'Previous original revision',content:'The original text before correction',contentBytes:35,contentSha256:'oldhash',sourceSha256:'sourcehash',evidenceLabel:'failed',status:'failed',metadata:{},raw:{}};
  const command=mockCommand();
  command.mockImplementation(async(args)=>{
    if(args.action==='status')return snapshot as never;
    if(args.action==='query')return {records:args.latestOnly?[]:[old],total:args.latestOnly?0:1,nextCursor:null} as never;
    return {} as never;
  });
  render(<LearningStudio onNotice={()=>{}} renderAssistant={renderAssistant}/>);
  fireEvent.click(screen.getByRole('tab',{name:'Raw records & datasets'}));
  expect(screen.getByLabelText('Latest revisions only')).toBeChecked();
  fireEvent.click(screen.getByLabelText('Latest revisions only'));
  fireEvent.change(screen.getByLabelText('Search raw learning records'),{target:{value:'original text'}});
  fireEvent.click(await screen.findByRole('button',{name:/Previous original revision/}));
  expect(screen.getByText(old.content,{selector:'pre'})).toBeVisible();
  expect(command.mock.calls.some(([args])=>args.action==='query'&&args.latestOnly===false&&args.search==='original text')).toBe(true);
});
