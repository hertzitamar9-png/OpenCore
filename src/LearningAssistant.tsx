import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { listen } from '@tauri-apps/api/event';
import { AssistantConversation, type ComposerDraft, type ConversationSettings } from './AssistantConversation';
import { learningCommand, learningDesktopAvailable } from './learningApi';
import * as api from './api';
import type { ComposerSkillId } from './composer-skills';
import type { ProjectSummary, RuntimeProfile, RuntimeSnapshot, TelemetrySnapshot, TimelineEntry } from './types';
import type { FileRecord } from './workspaces';
import type { WorkspacePreview, WorkspaceTab } from './WorkspacePanel';

type Props = {
  initialPrompt?: string; promptRevision: number; onConversation: (id: string) => void;
  selectedProfile: RuntimeProfile; onSelectProfile: (profile: RuntimeProfile) => void;
  runtimeSnapshot: RuntimeSnapshot; telemetry: TelemetrySnapshot; running: boolean; projects: ProjectSummary[];
  activeConversationIds: string[]; inferenceOwner?: string; inferenceBlocked?: string; defaultSkills: ComposerSkillId[];
  settings: ConversationSettings;
  onSettingsChange?: (settings: ConversationSettings) => void;
  onNotice: (message: string) => void; onRefresh: () => Promise<void>;
  onOpenConversation: (id: string, settings?: ConversationSettings) => void;
  onActivityChange: (id: string, active: boolean) => void;
  onOpenWorkspace: (tab: WorkspaceTab) => void; onOpenPreview: (preview: WorkspacePreview) => void;
  onOpenBrowserLink: (url: string) => void; onOpenFileRecord: (file: FileRecord) => void;
  onWorkspaceObscuredChange: (obscured: boolean) => void;
};
type Stream = {conversationId: string; runId: string; content?: string; reasoning?: string; segments?: {kind: 'thinking' | 'text'; content: string}[]; phase?: string; done?: boolean; checkpoint?: boolean};
type Session = {draft: ComposerDraft; settings: ConversationSettings};
// Each saved assistant keeps its own editor state when navigating away and back.
const sessions = new Map<string, Session>();
export function LearningAssistant(props: Props) {
  const [id, setId] = useState(''); const idRef = useRef('');
  const [entries,setEntries] = useState<TimelineEntry[]>([]); const [stream,setStream] = useState<Stream>();
  const [error,setError] = useState(''); const [settings,setSettings] = useState(props.settings);
  const [subscriptionRevision,setSubscriptionRevision]=useState(0);
  const propsRef=useRef(props);propsRef.current=props;
  const alive=useRef(false);const historyRevision=useRef(0);const openingRevision=useRef(0);
  const reportError=useCallback((reason:unknown)=>{if(alive.current)setError(String(reason).replace(/^Error: /,''));},[]);
  const refresh=useCallback(async()=>{
    const conversationId=idRef.current;if(!conversationId)return;
    const revision=++historyRevision.current;
    try{
      const latest=await api.conversation(conversationId);
      if(!alive.current||revision!==historyRevision.current||idRef.current!==conversationId)return;
      if(latest.some(entry=>entry.conversationId!==conversationId))throw new Error('The assistant history belongs to a different conversation. Refresh to retry.');
      setEntries(latest);setError('');
      await propsRef.current.onRefresh();
    }catch(reason){if(revision===historyRevision.current)reportError(reason);throw reason;}
  },[reportError]);
  const openAssistant=useCallback(async()=>{
    const revision=++openingRevision.current;
    try{
      const value=await learningCommand<{conversationId:string}>({action:'assistant'});
      if(!alive.current||revision!==openingRevision.current)return;
      idRef.current=value.conversationId;
      const session=sessions.get(value.conversationId)||{draft:{text:'',files:[]},settings:{...propsRef.current.settings}};
      sessions.set(value.conversationId,session);setSettings(session.settings);setId(value.conversationId);
      propsRef.current.onConversation(value.conversationId);
      await refresh();
    }catch(reason){if(revision===openingRevision.current)reportError(reason);}
  },[refresh,reportError]);
  useEffect(()=>{
    if(!learningDesktopAvailable()) return;
    alive.current=true;void openAssistant();
    return()=>{alive.current=false;openingRevision.current++;historyRevision.current++;idRef.current='';};
  },[openAssistant]);
  useEffect(()=>{
    let disposed=false;let stop:(()=>void)|undefined;
    if(!learningDesktopAvailable())return;
    void listen<Stream>('opencore-generation',({payload})=>{
      if(disposed||payload.conversationId!==idRef.current)return;
      if(payload.done||payload.checkpoint){setStream(current=>current&&current.runId!==payload.runId?current:payload.done?undefined:{...payload,content:'',reasoning:'',segments:[]});void refresh().catch(()=>{});}
      else setStream(payload);
    }).then(unlisten=>{if(disposed)unlisten();else stop=unlisten;}).catch(reportError);
    return()=>{disposed=true;stop?.();};
  },[refresh,reportError,subscriptionRevision]);
  const visible=useMemo(()=>{
    if(!stream||stream.conversationId!==id)return entries;
    const segments=stream.segments?.length?stream.segments:[...(stream.reasoning?[{kind:'thinking' as const,content:stream.reasoning}]:[]),...(stream.content?[{kind:'text' as const,content:stream.content}]:[])];
    return [...entries,...segments.filter(segment=>segment.content.trim()&&!segment.content.trimStart().startsWith('<echo>')).map((segment,index):TimelineEntry=>({id:-91-index,conversationId:id,timestamp:new Date().toISOString(),kind:segment.kind==='thinking'?'thinking':'message',role:'assistant',source:'OpenCore',title:'Learning assistant',content:segment.content,metadata:{live:true,phase:stream.phase}}))];
  },[entries,stream,id]);
  const rememberDraft=useCallback((draft:ComposerDraft)=>{const session=sessions.get(idRef.current);if(session)session.draft={text:draft.text,files:[...draft.files]};},[]);
  const rememberSettings=useCallback((next:ConversationSettings)=>{setSettings(current=>JSON.stringify(current)===JSON.stringify(next)?current:next);const session=sessions.get(idRef.current);if(session)session.settings={...next};},[]);
  useEffect(()=>{if(id)propsRef.current.onSettingsChange?.(settings);},[id,settings]);
  const draftRequest=useMemo(()=>props.initialPrompt?{id:props.promptRevision,text:props.initialPrompt}:undefined,[props.initialPrompt,props.promptRevision]);
  const retry=()=>{setSubscriptionRevision(value=>value+1);if(id)void refresh().catch(()=>{});else void openAssistant();};
  const blocked=props.inferenceBlocked||(error?'Refresh the learning assistant before sending.':props.activeConversationIds.some(active=>active!==id)||Boolean(props.inferenceOwner&&props.inferenceOwner!==id)?'Another chat is working. Its model will be released before this assistant starts.':undefined);
  if(!learningDesktopAvailable())return <div className="learning-empty">Open the desktop app to chat with your selected local model and run training.</div>;
  return <div className="learning-assistant-chat">{error&&<div className="learning-assistant-error" role="alert"><span>{error}</span><button onClick={retry}>Retry assistant refresh</button></div>}{!id?<p role="status">Opening the learning assistant…</p>:<AssistantConversation key={id} embedded conversationId={id} initialDraft={sessions.get(id)?.draft} draftRequest={draftRequest} onDraftChange={rememberDraft} title="Learning assistant" client="OpenCore" entries={visible} runtimeRunning={props.running} runtimeSnapshot={props.runtimeSnapshot} telemetry={props.telemetry} selectedProfile={props.selectedProfile} onSelectProfile={props.onSelectProfile} liveTokenSpeed={null} promptProgress={null} backendActive={props.activeConversationIds.includes(id)} inferenceBlocked={blocked} onConversationId={()=>{}} onRefresh={refresh} onNotice={props.onNotice} onExport={()=>{void api.exportConversation(id,'markdown').then(path=>props.onNotice(`Exported to ${path}`)).catch(reason=>props.onNotice(String(reason)));}} onRename={()=>props.onOpenConversation(id,settings)} onDelete={()=>props.onOpenConversation(id,settings)} pinned={false} project="" projectId={null} projects={props.projects} onPin={()=>{void api.setConversationPinned(id,true).then(props.onRefresh).catch(reason=>props.onNotice(String(reason)));}} onMoveProject={()=>{}} onCreateProject={async()=>false} defaultSkills={props.defaultSkills} subagentsEnabled={settings.subagentsEnabled} maxSubagents={settings.maxSubagents} projectSkillsEnabled={settings.projectSkillsEnabled} compactAtTokens={settings.compactAtTokens} initialSettings={settings} onSettingsChange={rememberSettings} onActivityChange={props.onActivityChange} onOpenWorkspace={props.onOpenWorkspace} onOpenPreview={props.onOpenPreview} onOpenBrowserLink={props.onOpenBrowserLink} onOpenFileRecord={props.onOpenFileRecord} onWorkspaceObscuredChange={props.onWorkspaceObscuredChange}/>}</div>;
}
