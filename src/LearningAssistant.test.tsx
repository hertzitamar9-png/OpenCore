import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import { LearningAssistant } from './LearningAssistant';
import * as learning from './learningApi';
import * as api from './api';
import type { TimelineEntry } from './types';

const handlers=vi.hoisted(()=>new Map<string,Set<(event:{payload:unknown})=>void>>());
vi.mock('@tauri-apps/api/event',()=>({listen:vi.fn(async(name:string,callback:(event:{payload:unknown})=>void)=>{
  const listeners=handlers.get(name)||new Set();listeners.add(callback);handlers.set(name,listeners);return()=>listeners.delete(callback);
})}));
vi.mock('@tauri-apps/plugin-dialog',()=>({open:vi.fn(async()=>['C:/draft/notes.txt'])}));
let conversationId='';
beforeEach(()=>{
  conversationId=`learning:${expect.getState().currentTestName}`;
  vi.spyOn(learning,'learningDesktopAvailable').mockReturnValue(true);
  vi.spyOn(learning,'learningCommand').mockResolvedValue({conversationId} as never);
  vi.spyOn(api,'conversation').mockResolvedValue([]);
});
afterEach(()=>{vi.restoreAllMocks();handlers.clear();});
async function props():Promise<ComponentProps<typeof LearningAssistant>>{
  const snapshot=await api.snapshot();
  return {promptRevision:0,onConversation:vi.fn(),settings:{approvalMode:'ask-every-time',reasoningEffort:'off',subagentsEnabled:true,maxSubagents:3,projectSkillsEnabled:true,compactAtTokens:200000},selectedProfile:'echo',onSelectProfile:vi.fn(),runtimeSnapshot:snapshot.runtime,telemetry:snapshot.telemetry,running:false,projects:[],activeConversationIds:[],defaultSkills:[],onNotice:vi.fn(),onRefresh:vi.fn(async()=>{}),onOpenConversation:vi.fn(),onActivityChange:vi.fn(),onOpenWorkspace:vi.fn(),onOpenPreview:vi.fn(),onOpenBrowserLink:vi.fn(),onOpenFileRecord:vi.fn(),onWorkspaceObscuredChange:vi.fn()};
}
const entry=(content:string,id=1):TimelineEntry=>({id,conversationId,timestamp:'2026-10-07T13:14:59Z',kind:'message',role:'assistant',source:'OpenCore',title:'Learning assistant',content,metadata:{}});
const emit=async(payload:unknown)=>{await act(async()=>{for(const callback of handlers.get('opencore-generation')||[])callback({payload});});};

it('appends a prepared request once without discarding the existing draft, attachment or settings',async()=>{
  const initial=await props();const send=vi.spyOn(api,'sendChatMessage');
  const view=render(<LearningAssistant {...initial}/>);
  const input=await screen.findByLabelText('Message side chat');
  fireEvent.change(input,{target:{value:'Keep this question'}});
  fireEvent.click(screen.getByRole('button',{name:'Add files or choose model'}));
  fireEvent.click(screen.getByRole('menuitem',{name:'Upload files or images'}));
  await screen.findByRole('button',{name:'Remove notes.txt'});
  fireEvent.click(screen.getByRole('button',{name:'Effort: Off'}));
  fireEvent.change(screen.getByRole('slider',{name:'Reasoning effort'}),{target:{value:'2'}});
  fireEvent.click(screen.getByRole('button',{name:'Close Effort'}));
  view.rerender(<LearningAssistant {...initial} promptRevision={1} initialPrompt="Inspect eligible examples"/>);
  expect(screen.getByLabelText('Message side chat')).toBe(input);
  expect(input).toHaveValue('Keep this question\n\n---\n\nInspect eligible examples');
  expect(screen.getByRole('button',{name:'Remove notes.txt'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Effort: Medium'})).toBeVisible();
  view.rerender(<LearningAssistant {...initial} promptRevision={1} initialPrompt="Inspect eligible examples"/>);
  expect(input).toHaveValue('Keep this question\n\n---\n\nInspect eligible examples');
  expect(send).not.toHaveBeenCalled();
  view.unmount();
  render(<LearningAssistant {...initial}/>);
  expect(await screen.findByLabelText('Message side chat')).toHaveValue('Keep this question\n\n---\n\nInspect eligible examples');
  expect(screen.getByRole('button',{name:'Effort: Medium'})).toBeVisible();
  expect(screen.getByRole('button',{name:'Remove notes.txt'})).toBeInTheDocument();
});

it('keeps ordered stream segments and ignores events from another chat',async()=>{
  render(<LearningAssistant {...await props()}/>);
  await screen.findByLabelText('Message side chat');
  await emit({conversationId:'unrelated',runId:'other',content:'Do not leak the other chat'});
  expect(screen.queryByText('Do not leak the other chat')).not.toBeInTheDocument();
  await emit({conversationId,runId:'run',segments:[{kind:'text',content:'First visible segment'},{kind:'thinking',content:'Middle reasoning segment'},{kind:'text',content:'Last visible segment'}]});
  const transcript=document.querySelector('.aui-thread-root')!.textContent!;
  expect(transcript.indexOf('First visible segment')).toBeGreaterThanOrEqual(0);
  expect(transcript.indexOf('Middle reasoning segment')).toBeGreaterThan(transcript.indexOf('First visible segment'));
  expect(transcript.indexOf('Last visible segment')).toBeGreaterThan(transcript.indexOf('Middle reasoning segment'));
});

it('ignores an older checkpoint refresh when the final history arrives first',async()=>{
  const initial=await props();vi.mocked(api.conversation).mockResolvedValue([entry('Initial saved history')]);
  render(<LearningAssistant {...initial}/>);
  await screen.findByText('Initial saved history');
  let finishOlder!:(entries:TimelineEntry[])=>void;
  vi.mocked(api.conversation).mockImplementationOnce(()=>new Promise(resolve=>{finishOlder=resolve;})).mockResolvedValueOnce([entry('Newest final history',2)]);
  await emit({conversationId,runId:'run',checkpoint:true});
  await emit({conversationId,runId:'run',done:true});
  await screen.findByText('Newest final history');
  await act(async()=>finishOlder([entry('Obsolete checkpoint history')]));
  expect(screen.getByText('Newest final history')).toBeVisible();
  expect(screen.queryByText('Obsolete checkpoint history')).not.toBeInTheDocument();
});

it('keeps a draft after a history error and recovers through Retry',async()=>{
  vi.mocked(api.conversation).mockRejectedValueOnce(new Error('Temporary history failure')).mockResolvedValue([entry('History recovered')]);
  render(<LearningAssistant {...await props()}/>);
  const input=await screen.findByLabelText('Message side chat');
  fireEvent.change(input,{target:{value:'Keep this retry draft'}});
  expect(await screen.findByRole('alert')).toHaveTextContent('Temporary history failure');
  fireEvent.click(screen.getByRole('button',{name:'Retry assistant refresh'}));
  await screen.findByText('History recovered');
  await waitFor(()=>expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  expect(screen.getByLabelText('Message side chat')).toBe(input);
  expect(input).toHaveValue('Keep this retry draft');
});
