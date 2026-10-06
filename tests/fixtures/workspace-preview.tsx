// Isolated Vite browser fixture. It exercises the real UI using native API doubles;
// it never starts inference, controls a real Windows app, or writes app data.
import { createRoot } from 'react-dom/client';
import App from '../../src/App';
import { previewSnapshot, previewTimeline } from '../../src/mock';
import { defaultPlatformConfiguration } from '../../src/agent-platform';
import { installExternalLinkGuard } from '../../src/external-links';
import catalog from '../../src-tauri/resources/model-catalog.json';
import type { TimelineEntry } from '../../src/types';
import '../../src/styles.css';

const callbacks = new Map<number, (value: unknown) => void>();
const listeners = new Map<number, {event: string; handler: number}>();
const branches = new Map<string, TimelineEntry[]>();
const commands: Array<{command: string; args: Record<string, any>}> = [];
let next = 1;
const emit = (event: string, payload: unknown) => {
  for (const [id, listener] of listeners) if (listener.event === event) callbacks.get(listener.handler)?.({event, id, payload});
};
const file = {id: 'preview-file', conversationId: 'preview', turnId: 'preview-turn', path: 'scripts/analyze.py', change: 'modified', added: 8, removed: 2, beforeHash: 'before', afterHash: 'after', timestamp: '2026-10-06T10:00:00Z', size: 230, mime: 'text/plain', source: 'C:/preview/scripts/analyze.py', snapshotAvailable: true, origin: 'workspace'};
const after = 'import pandas as pd\n\nsales = pd.read_csv("sales.csv")\nmonthly = sales.groupby("month")["revenue"].sum()\nmonthly.plot.bar()\n';
const internals = {
  metadata: {currentWindow: {label: 'main'}, currentWebview: {label: 'main'}},
  transformCallback(callback: (value: unknown) => void) { const id = next++; callbacks.set(id, callback); return id; },
  unregisterCallback(id: number) { callbacks.delete(id); },
  async invoke(command: string, args: Record<string, any> = {}): Promise<unknown> {
    commands.push({command, args});
    if (command === 'plugin:event|listen') { const id = next++; listeners.set(id, {event: args.event, handler: args.handler}); return id; }
    if (command === 'plugin:event|unlisten') { listeners.delete(args.eventId); return null; }
    if (command === 'get_snapshot') return {...structuredClone(previewSnapshot), conversations: [...previewSnapshot.conversations, ...[...branches.keys()].map(id => ({...previewSnapshot.conversations[0], id, title: 'Workspace exploration branch'}))], activeConversationIds: []};
    if (command === 'get_conversation') return structuredClone(branches.get(args.id) || (args.id === 'preview' ? previewTimeline : []));
    if (command === 'plugin:app|version') return '0.2.110 preview';
    if (command === 'agent_platform_configuration' || command === 'agent_platform_save_configuration') return defaultPlatformConfiguration();
    if (command === 'list_model_library') return {models: catalog.models.map(model => ({...model, installed: ['doucode','echo','native1m'].includes(model.id), externalManaged: false, downloadBytes: 1, totalBytes: 1})), diskFreeBytes: 200e9, minimumFreeBytes: 64e6, progress: null};
    if (command === 'workspace_files') {
      if (args.args.action === 'changes') return {turnId: 'preview-turn', files: [file], added: 8, removed: 2, coverage: []};
      if (args.args.action === 'preview') return {name: 'analyze.py', mime: 'text/plain', text: args.args.version === 'before' ? 'print("Sales")' : after, sha256: args.args.version === 'before' ? 'before' : 'after', size: 230};
      if (args.args.action === 'diff') return {path: file.path, before: 'print("Sales")', after, added: 8, removed: 2};
      return {files: [file], coverage: []};
    }
    if (command === 'background_command') return {tasks: [], runs: [], webhook: {url: 'http://127.0.0.1:8812/events', token: 'preview-token'}, execution: {appMustBeOpen: true}};
    if (command === 'create_side_chat') {
      const id = `side:preview-${branches.size + 1}`; branches.set(id, previewTimeline.map(entry => ({...entry, conversationId: id})));
      return {conversationId: id, parentId: args.conversationId, title: 'Workspace exploration branch', contextTokens: 262144, sharedWorkspace: true, inheritedEntries: previewTimeline.length, contextSource: 'timeline-branch'};
    }
    if (command === 'send_side_chat_message') {
      const request = args.request; const entries = branches.get(request.conversationId) || [];
      entries.push({id: next++, conversationId: request.conversationId, timestamp: new Date().toISOString(), kind: 'message', role: 'user', source: 'OpenCore', title: 'You', content: request.text, metadata: {}});
      emit('opencore-generation', {conversationId: request.conversationId, runId: 'preview-run', content: 'This is a mocked preview response in the side chat branch.'});
      entries.push({id: next++, conversationId: request.conversationId, timestamp: new Date().toISOString(), kind: 'message', role: 'assistant', source: 'OpenCore', title: 'Preview response', content: 'This is a mocked preview response in the side chat branch.', metadata: {}});
      emit('opencore-generation', {conversationId: request.conversationId, runId: 'preview-run', done: true});
      return {conversationId: request.conversationId, title: 'Workspace exploration branch'};
    }
    if (command === 'native_browser_command') return {open: true, url: args.args?.url || 'https://example.com'};
    if (command === 'desktop_command') return args.action === 'list' ? {windows: []} : {};
    if (command === 'echo_working_set') return {available: true, windowTokens: 262144, liveTokens: 2048, contextMode: 'echo'};
    if (command === 'speech_status') return {modelId: 'whisper-large-v3-turbo', installed: false, enabled: false, idleMode: 'cold', workerReady: false, coldStartMs: null, warmWakeMs: null, phase: 'off'};
    if (['list_studio_jobs','installed_skill_models','agent_platform_skills','agent_platform_plugins','agent_platform_activity'].includes(command)) return [];
    if (command === 'preview_composer_attachment') return {name: 'notes.md', mime: 'text/plain', size: 15, dataUrl: '', text: 'Saved preview notes'};
    if (command === 'check_latest_app_version') return {currentVersion: 'preview', available: false, version: null};
    return null;
  },
};
Object.assign(window, {__TAURI_INTERNALS__: internals, __TAURI_EVENT_PLUGIN_INTERNALS__: {unregisterListener: () => {}}, __workspacePreviewCommands: commands});
installExternalLinkGuard(async () => {});
createRoot(document.getElementById('root')!).render(<App />);
