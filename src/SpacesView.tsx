import { useCallback, useEffect, useRef, useState } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import { ArchiveRestore, ArrowLeft, Eye, FileCode2, Files, FolderPlus, LayoutGrid, List, MessageSquare, RefreshCw, Search } from 'lucide-react';
import { CaptureCoverage, FileLineCounts } from './FileChangesReceipt';
import { FileSnapshotView } from './FileSnapshotView';
import { fileSize, subscribeWorkspaceFiles, workspaceFiles, type FileRecord, type WorkspaceFilesResult } from './workspaces';
import './workspaces.css';
import './message-media.css';

export function SpacesView({ conversationId, onNotice, onOpenConversation, onOpenFile, onOpenExternal }: { conversationId?: string; onNotice: (message: string) => void; onOpenConversation?: (id: string) => void; onOpenFile?: (file: FileRecord) => void; onOpenExternal?: (file: FileRecord) => Promise<void> | void }) {
  const [history, setHistory] = useState<WorkspaceFilesResult>({ files: [], coverage: [] });
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [grid, setGrid] = useState(false);
  const [selected, setSelected] = useState<FileRecord | null>(null);
  const [openingId, setOpeningId] = useState<string | null>(null);
  const revision = useRef(0);
  const alive = useRef(true);
  const refresh = useCallback(async () => {
    const request = ++revision.current;
    setBusy(true);
    try {
      const value = await workspaceFiles({ action: 'list', conversationId, search, limit: 300 });
      if (alive.current && request === revision.current) { setHistory(value); setError(''); }
    } catch (cause) { if (alive.current && request === revision.current) setError(String(cause)); }
    finally { if (alive.current && request === revision.current) setBusy(false); }
  }, [conversationId, search]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; ++revision.current; }; }, []);
  useEffect(() => {
    ++revision.current;
    const timer = window.setTimeout(() => void refresh(), search ? 180 : 0);
    const unsubscribe = subscribeWorkspaceFiles(() => void refresh());
    return () => { window.clearTimeout(timer); unsubscribe(); ++revision.current; };
  }, [refresh, search]);
  useEffect(() => { setSelected(null); setHistory({ files: [], coverage: [] }); }, [conversationId]);
  async function indexFolder() {
    try {
      if (!('__TAURI_INTERNALS__' in window)) throw new Error('Folder selection requires the desktop app.');
      const workspace = await open({ directory: true, multiple: false, title: 'Index existing workspace files' });
      if (typeof workspace !== 'string') return;
      setBusy(true);
      const result = await workspaceFiles({ action: 'index', workspace, conversationId });
      onNotice(`${result.files.length} existing ${result.files.length === 1 ? 'file' : 'files'} indexed. Capture coverage is listed in Spaces.`);
      await refresh();
    } catch (cause) { onNotice(String(cause)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function indexSavedOutputs() {
    try {
      setBusy(true);
      const result = await workspaceFiles({ action: 'index', conversationId });
      onNotice(`${result.files.length} saved output ${result.files.length === 1 ? 'version' : 'versions'} indexed from recorded evidence.`);
      await refresh();
    } catch (cause) { onNotice(String(cause)); }
    finally { if (alive.current) setBusy(false); }
  }
  function select(file: FileRecord) { if (onOpenFile) onOpenFile(file); else setSelected(file); }
  async function openFile(file: FileRecord) {
    if (!onOpenExternal) return select(file);
    setOpeningId(file.id);
    try { await onOpenExternal(file); }
    catch (cause) { onNotice(`Could not open file in browser: ${String(cause)}`); }
    finally { if (alive.current) setOpeningId(null); }
  }
  const versions = new Map<string, number>();
  for (const file of history.files) versions.set(file.source, (versions.get(file.source) || 0) + 1);
  return <section className="spaces-view" aria-label={conversationId ? 'Chat files' : 'Spaces'}>
    <header className="spaces-heading"><div><Files size={19} /><h2>{conversationId ? 'Files' : 'Spaces'}</h2><span>{history.files.length} saved {history.files.length === 1 ? 'version' : 'versions'}</span></div><div>
      <button aria-label="Index saved outputs" title="Index existing outputs linked by saved studio and chat records" onClick={() => void indexSavedOutputs()} disabled={busy}><ArchiveRestore size={16} /></button>
      <button aria-label="Index existing folder" title="Index existing files without generating them again" onClick={() => void indexFolder()} disabled={busy}><FolderPlus size={16} /></button>
      <button aria-label="Refresh file history" onClick={() => void refresh()} disabled={busy}><RefreshCw size={16} className={busy ? 'workspace-spinning' : ''} /></button>
    </div></header>
    <p className="spaces-description">Saved file versions and generated outputs, linked to their recorded chats and tasks.</p>
    <div className="spaces-toolbar"><label><Search size={15} /><input type="search" aria-label="Search file history" placeholder="Search files, chats, or tasks" value={search} onChange={event => setSearch(event.target.value)} /></label><div>
      <button aria-label="List view" aria-pressed={!grid} onClick={() => setGrid(false)}><List size={16} /></button>
      <button aria-label="Grid view" aria-pressed={grid} onClick={() => setGrid(true)}><LayoutGrid size={16} /></button>
    </div></div>
    {error && <p role="alert" className="workspace-inline-error">{error}</p>}
    {selected && <div className="spaces-selected-file"><button className="spaces-back" onClick={() => setSelected(null)}><ArrowLeft size={14} />Back to files</button><FileSnapshotView file={selected} onNotice={onNotice} /></div>}
    {!selected && <div className={grid ? 'spaces-file-grid' : 'spaces-file-list'}>{history.files.map(file => <article className="spaces-file" key={file.id}>
      <button className="spaces-file-open" onClick={() => void openFile(file)} disabled={openingId !== null} title={onOpenExternal ? `Open ${file.path} in the external browser` : file.source}><FileCode2 size={grid ? 25 : 17} /><span><strong>{file.path}</strong><small>{openingId === file.id ? 'Opening in browser…' : `${file.change} · ${fileSize(file.size)} · ${file.snapshotAvailable ? 'Snapshot saved' : 'Source reference'}`}</small></span>
        <FileLineCounts added={file.added} removed={file.removed} />
      </button>
      <div className="spaces-file-origin"><time dateTime={file.timestamp} title={file.timestamp}>{new Date(file.timestamp).toLocaleString()}</time><span title={file.turnId}>{file.origin || 'workspace'} · {(versions.get(file.source) || 0)} {(versions.get(file.source) || 0) === 1 ? 'version' : 'versions'}</span>
        {onOpenExternal && <button onClick={() => select(file)} aria-label={`Preview saved version of ${file.path}`} title="Preview snapshot and diff"><Eye size={14} /></button>}
        {file.conversationId && onOpenConversation && <button onClick={() => onOpenConversation(file.conversationId)} aria-label={`Open originating chat for ${file.path}`} title="Open originating chat"><MessageSquare size={13} /></button>}
      </div>
    </article>)}</div>}
    {!selected && !history.files.length && !busy && <div className="spaces-empty"><Files size={26} /><p>{search ? 'No recorded files match this search.' : 'File versions appear after an agent task changes workspace files or a studio job produces an output.'}</p><p>Index an existing folder to save its current versions.</p></div>}
    <CaptureCoverage coverage={history.coverage} />
  </section>;
}
