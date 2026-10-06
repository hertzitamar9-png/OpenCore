import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { listen } from '@tauri-apps/api/event';
import { GitBranch, Maximize2, RefreshCw } from 'lucide-react';
import { AssistantConversation, type ConversationSettings } from './AssistantConversation';
import { createSideChat, sendSideChatMessage, type SideChatBranch } from './side-chat';
import { profileLabel } from './ModelProfiles';
import * as api from './api';
import type { ComposerSkillId } from './composer-skills';
import type { ProjectSummary, RuntimeProfile, RuntimeSnapshot, TelemetrySnapshot, TimelineEntry } from './types';
import type { FileRecord } from './workspaces';
import type { WorkspacePreview, WorkspaceTab } from './WorkspacePanel';

type Stream = {conversationId: string; runId: string; content?: string; reasoning?: string; segments?: {kind: 'thinking' | 'text'; content: string}[]; phase?: string; done?: boolean; checkpoint?: boolean};
type Props = {
  parentId?: string; parentTitle: string; settings: ConversationSettings;
  selectedProfile: RuntimeProfile; onSelectProfile: (profile: RuntimeProfile) => void;
  runtimeSnapshot: RuntimeSnapshot; telemetry: TelemetrySnapshot; running: boolean; projects: ProjectSummary[];
  activeConversationIds: string[]; inferenceOwner?: string; studioActive: boolean; defaultSkills: ComposerSkillId[];
  onNotice: (message: string) => void; onRefresh: () => Promise<void>; onOpenConversation: (id: string, settings?: ConversationSettings) => void;
  onActivityChange: (id: string, active: boolean) => void;
  onOpenWorkspace: (tab: WorkspaceTab) => void; onOpenPreview: (preview: WorkspacePreview) => void;
  onOpenBrowserLink: (url: string) => void; onOpenFileRecord: (file: FileRecord) => void;
  onWorkspaceObscuredChange: (obscured: boolean) => void;
};

export function SideChat({parentId, parentTitle, settings, selectedProfile, onSelectProfile, runtimeSnapshot, telemetry, running, projects, activeConversationIds, inferenceOwner, studioActive, defaultSkills, onNotice, onRefresh, onOpenConversation, onActivityChange, onOpenWorkspace, onOpenPreview, onOpenBrowserLink, onOpenFileRecord, onWorkspaceObscuredChange}: Props) {
  const [branches, setBranches] = useState<Record<string, {branch: SideChatBranch; settings: ConversationSettings}>>({});
  const stored = parentId ? branches[parentId] : undefined;
  const branch = stored?.branch;
  const [creating, setCreating] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [entries, setEntries] = useState<TimelineEntry[]>([]);
  const [stream, setStream] = useState<Stream>();
  const branchRef = useRef(branch?.conversationId);
  branchRef.current = branch?.conversationId;
  const blocked = studioActive ? 'Wait for the active studio task to finish.' : activeConversationIds.some(id => id !== branch?.conversationId) || Boolean(inferenceOwner && inferenceOwner !== branch?.conversationId) ? 'Wait for the active chat to finish before using side chat.' : undefined;

  const refreshBranch = useCallback(async () => {
    const id = branchRef.current;
    if (!id) return;
    setRefreshing(true);
    try {
      const latest = await api.conversation(id);
      if (branchRef.current === id) { setEntries(latest); setError(''); }
      await onRefresh();
    } catch (reason) { if (branchRef.current === id) setError(`Could not refresh side chat: ${String(reason)}`); }
    finally { setRefreshing(false); }
  }, [onRefresh]);
  useEffect(() => { setEntries([]); setStream(undefined); setError(''); if (branch) void refreshBranch(); }, [branch?.conversationId, refreshBranch]);
  useEffect(() => {
    let disposed = false; let stop: (() => void) | undefined;
    void listen<Stream>('opencore-generation', ({payload}) => {
      if (disposed || payload.conversationId !== branchRef.current) return;
      if (payload.done || payload.checkpoint) {
        if (payload.done) setStream(undefined);
        else setStream({...payload, content: '', reasoning: '', segments: []});
        void refreshBranch();
      } else setStream(payload);
    }).then(unlisten => { if (disposed) unlisten(); else stop = unlisten; }).catch(() => {});
    return () => { disposed = true; stop?.(); };
  }, [refreshBranch]);
  const visibleEntries = useMemo(() => {
    if (!branch || stream?.conversationId !== branch.conversationId) return entries;
    const segments = stream.segments?.length ? stream.segments : [...(stream.reasoning ? [{kind: 'thinking', content: stream.reasoning}] : []), ...(stream.content ? [{kind: 'text', content: stream.content}] : [])];
    return [...entries, ...segments.filter(segment => segment.content.trim() && !segment.content.trimStart().startsWith('<echo>')).map((segment, index): TimelineEntry => ({id: -2-index, conversationId: branch.conversationId, timestamp: new Date().toISOString(), kind: segment.kind === 'thinking' ? 'thinking' : 'message', role: 'assistant', source: 'OpenCore', title: 'Side chat response', content: segment.content, metadata: {live: true, phase: stream.phase}}))];
  }, [entries, stream, branch]);
  const create = async () => {
    if (!parentId || blocked || creating) return;
    const origin = parentId;
    const inherited = {...settings};
    setCreating(true); setError('');
    try {
      const created = await createSideChat(origin, selectedProfile, inherited);
      setBranches(current => ({...current, [origin]: {branch: created, settings: inherited}}));
      await onRefresh();
    } catch (reason) { setError(String(reason).replace(/^Error: /, '')); }
    finally { setCreating(false); }
  };

  if (!branch || !stored) return <section className="side-chat-intro" aria-label="Side chat"><GitBranch size={27} /><h3>Explore a side chat</h3><p>Create a branch of {parentTitle || 'this conversation'} with the saved context at this moment. The branch has its own messages and shares this chat’s workspace.</p><p className="side-chat-muted">It uses the same selected model and approval settings. The history snapshot stays fixed when later messages are added to the main chat.</p>{blocked ? <p role="status">{blocked}</p> : null}{!parentId ? <p>Send a message in the main chat first to create a saved branch.</p> : null}{error ? <p className="side-chat-error" role="alert">{error}</p> : null}<button className="primary" disabled={!parentId || Boolean(blocked) || creating} onClick={() => void create()}><GitBranch size={15} />{creating ? 'Creating branch…' : 'Create side chat'}</button></section>;

  return <section className="side-chat-session" aria-label="Side chat branch">
    <header className="side-chat-context"><div><GitBranch size={15} /><strong>Branch of {parentTitle}</strong></div><p>{profileLabel(selectedProfile)} · {branch.contextTokens.toLocaleString()}-token context · Shared workspace</p><small>{branch.contextSource === 'codex-fork' ? 'Saved Codex context fork' : 'Saved conversation snapshot'}{typeof branch.inheritedEntries === 'number' ? ` · ${branch.inheritedEntries.toLocaleString()} inherited entries` : ''}</small><div className="side-chat-actions"><button disabled={refreshing} aria-label="Refresh side chat" title="Refresh side chat" onClick={() => void refreshBranch()}><RefreshCw size={14} /></button><button aria-label="Open as full chat" onClick={() => onOpenConversation(branch.conversationId, stored.settings)}><Maximize2 size={14} /> Open as full chat</button></div></header>
    {branch.contextWarning ? <p className="side-chat-error" role="status">{branch.contextWarning}</p> : null}
    {error ? <p className="side-chat-error" role="alert">{error}</p> : null}
    <AssistantConversation key={branch.conversationId} embedded conversationId={branch.conversationId} title={branch.title} client="OpenCore" entries={visibleEntries} runtimeRunning={running} runtimeSnapshot={{...runtimeSnapshot, contextSize: branch.contextTokens}} telemetry={telemetry} selectedProfile={selectedProfile} onSelectProfile={onSelectProfile} liveTokenSpeed={null} promptProgress={null} backendActive={activeConversationIds.includes(branch.conversationId)} inferenceBlocked={blocked} onConversationId={() => {}} onRefresh={refreshBranch} onNotice={onNotice} onExport={() => { void api.exportConversation(branch.conversationId, 'markdown').then(path => onNotice(`Exported to ${path}`)).catch(reason => onNotice(String(reason))); }} onRename={() => onOpenConversation(branch.conversationId)} onDelete={() => onOpenConversation(branch.conversationId)} pinned={false} project="" projectId={null} projects={projects} onPin={() => { void api.setConversationPinned(branch.conversationId, true).then(onRefresh).catch(reason => onNotice(String(reason))); }} onMoveProject={() => {}} onCreateProject={async () => false} defaultSkills={defaultSkills} subagentsEnabled={stored.settings.subagentsEnabled} maxSubagents={stored.settings.maxSubagents} projectSkillsEnabled={stored.settings.projectSkillsEnabled} compactAtTokens={stored.settings.compactAtTokens} initialSettings={stored.settings} sendMessage={sendSideChatMessage} onActivityChange={onActivityChange} onOpenWorkspace={onOpenWorkspace} onOpenPreview={onOpenPreview} onOpenBrowserLink={onOpenBrowserLink} onOpenFileRecord={onOpenFileRecord} onWorkspaceObscuredChange={onWorkspaceObscuredChange} />
  </section>;
}
