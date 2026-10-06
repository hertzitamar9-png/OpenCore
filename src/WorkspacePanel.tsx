import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { open as chooseFile } from '@tauri-apps/plugin-dialog';
import { AppWindow, FileDown, Files, Globe2, MessageSquare, Plus, SlidersHorizontal, X } from 'lucide-react';
import * as api from './api';
import { NativeBrowserPanel } from './NativeBrowserPanel';
import { DesktopPanel } from './DesktopPanel';
import { SpacesView } from './SpacesView';
import { FileSnapshotView } from './FileSnapshotView';
import type { FileRecord } from './workspaces';
import './WorkspacePanel.css';

export type WorkspaceTab = 'files' | 'browser' | 'computer' | 'side-chat';
export type WorkspacePreview = api.ArtifactPreview | api.ComposerAttachmentPreview | { remoteImage: string };
type FileTab = {id: string; name: string; record?: FileRecord; preview?: WorkspacePreview};
type Props = {
  open: boolean; tab: WorkspaceTab; width: number; snapPx: number;
  onTabChange: (tab: WorkspaceTab) => void; onClose: () => void;
  onWidthChange: (width: number) => void; onSnapChange: (snap: number) => void;
  conversationId?: string; onNotice: (message: string) => void;
  onOpenConversation: (id: string) => void;
  preview?: WorkspacePreview | null; file?: FileRecord | null;
  browserLocation?: {url: string; requestId: string} | null;
  sideChat: ReactNode; obscured?: boolean;
};
const tabs = [
  {id: 'files', label: 'Files', icon: Files},
  {id: 'browser', label: 'Browser', icon: Globe2},
  {id: 'computer', label: 'Computer', icon: AppWindow},
  {id: 'side-chat', label: 'Side chat', icon: MessageSquare},
] as const;

export function WorkspacePanel({open, tab, width, snapPx, onTabChange, onClose, onWidthChange, onSnapChange, conversationId, onNotice, onOpenConversation, preview, file, browserLocation, sideChat, obscured = false}: Props) {
  const root = useRef<HTMLElement>(null);
  const drag = useRef<{x: number; width: number} | null>(null);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [maxWidth, setMaxWidth] = useState(Math.max(320, window.innerWidth - 390));
  const [snapOpen, setSnapOpen] = useState(false);
  const [visited, setVisited] = useState<Set<WorkspaceTab>>(new Set());
  const [files, setFiles] = useState<FileTab[]>([]);
  const [activeFile, setActiveFile] = useState('history');

  useEffect(() => { if (open) setVisited(current => current.has(tab) ? current : new Set([...current, tab])); }, [open, tab]);
  useEffect(() => {
    const update = () => setMaxWidth(Math.max(320, (root.current?.parentElement?.getBoundingClientRect().width || window.innerWidth - 58) - 340));
    update();
    const observer = new ResizeObserver(update);
    if (root.current?.parentElement) observer.observe(root.current.parentElement);
    window.addEventListener('resize', update);
    return () => { observer.disconnect(); window.removeEventListener('resize', update); };
  }, []);
  useEffect(() => {
    if (!preview) return;
    const item = {id: crypto.randomUUID(), name: 'remoteImage' in preview ? 'Image' : preview.name, preview};
    setFiles(current => [...current, item]); setActiveFile(item.id);
  }, [preview]);
  const selectRecord = (record: FileRecord) => {
    setFiles(current => current.some(item => item.id === record.id) ? current : [...current, {id: record.id, name: record.path.split(/[\\/]/).pop() || record.path, record}]);
    setActiveFile(record.id);
  };
  useEffect(() => { if (file) selectRecord(file); }, [file]);

  const selected = files.find(item => item.id === activeFile);
  const visibleWidth = Math.min(width, maxWidth);
  const resize = (value: number) => onWidthChange(Math.round(Math.max(320, Math.min(maxWidth, value))));
  const finishResize = () => {
    drag.current = null;
    const total = root.current?.parentElement?.getBoundingClientRect().width;
    if (!total || snapPx <= 0) return;
    const target = [.35, .5, .65].map(fraction => total * fraction).find(stop => Math.abs(stop - visibleWidth) <= snapPx);
    if (target) resize(target);
  };
  const openLocal = async () => {
    try {
      const path = await chooseFile({multiple: false, directory: false, title: 'Open file in workspace'});
      if (typeof path !== 'string') return;
      const loaded = await api.previewComposerAttachment(path);
      const item = {id: crypto.randomUUID(), name: loaded.name, preview: loaded};
      setFiles(current => [...current, item]); setActiveFile(item.id);
    } catch (error) { onNotice(`Could not open file: ${String(error)}`); }
  };
  const download = (id: string) => { void api.downloadArtifact(id).then(path => onNotice(`Downloaded to ${path}`)).catch(error => onNotice(`Could not download file: ${String(error)}`)); };

  return <aside ref={root} id="opencore-workspace" className="unified-workspace" aria-label="Workspace" hidden={!open} style={{'--workspace-width': `${visibleWidth}px`} as CSSProperties}>
    <div className="unified-workspace-resizer" role="separator" aria-label="Resize workspace" aria-orientation="vertical" aria-valuemin={320} aria-valuemax={maxWidth} aria-valuenow={visibleWidth} tabIndex={0}
      onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        resize(event.key === 'Home' ? 320 : event.key === 'End' ? maxWidth : visibleWidth + (event.key === 'ArrowLeft' ? 24 : -24));
      }}
      onPointerDown={event => { drag.current = {x: event.clientX, width: visibleWidth}; event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={event => { if (drag.current) resize(drag.current.width + drag.current.x - event.clientX); }}
      onPointerCancel={() => { drag.current = null; }}
      onPointerUp={event => { finishResize(); if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} />
    <header className="unified-workspace-header"><Files size={16} /><strong>Workspace</strong><button title="Workspace layout settings" aria-label="Workspace layout settings" aria-expanded={snapOpen} onClick={() => setSnapOpen(current => !current)}><SlidersHorizontal size={15} /></button><button aria-label="Close workspace" title="Close workspace" onClick={onClose}><X size={17} /></button></header>
    {snapOpen ? <div className="workspace-snap-control"><span>Snap strength</span><input type="range" aria-label="Workspace snap strength" min="0" max="80" value={snapPx} onChange={event => onSnapChange(Number(event.target.value))} /><strong>{snapPx}px</strong></div> : null}
    <div className="unified-workspace-tabs" role="tablist" aria-label="Workspace views">{tabs.map((item, index) => <button key={item.id} id={`workspace-tab-${item.id}`} ref={node => { tabRefs.current[index] = node; }} role="tab" aria-selected={tab === item.id} aria-controls={`workspace-view-${item.id}`} tabIndex={tab === item.id ? 0 : -1} onClick={() => onTabChange(item.id)} onKeyDown={event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      onTabChange(tabs[next].id); tabRefs.current[next]?.focus();
    }}><item.icon size={14} /><span>{item.label}</span></button>)}</div>
    <div id="workspace-view-files" role="tabpanel" aria-labelledby="workspace-tab-files" className="unified-workspace-content workspace-files-content" hidden={tab !== 'files'}>
      <div className="workspace-browser-pages" role="tablist" aria-label="Files tabs"><button className="workspace-history-tab" role="tab" aria-selected={activeFile === 'history'} onClick={() => setActiveFile('history')}>History</button>{files.map(item => <div className="workspace-browser-page-tab" key={item.id}><button role="tab" aria-selected={activeFile === item.id} onClick={() => setActiveFile(item.id)} title={item.record?.path || item.name}>{item.name}</button><button aria-label={`Close ${item.name}`} onClick={() => { setFiles(current => current.filter(file => file.id !== item.id)); if (activeFile === item.id) setActiveFile('history'); }}><X size={12} /></button></div>)}<button className="workspace-browser-new-tab" aria-label="Open file in new tab" title="Open file in new tab" onClick={() => void openLocal()}><Plus size={15} /></button></div>
      {activeFile === 'history' ? <SpacesView conversationId={conversationId} onNotice={onNotice} onOpenFile={selectRecord} onOpenConversation={onOpenConversation} /> : selected?.record ? <FileSnapshotView file={selected.record} onNotice={onNotice} /> : selected?.preview ? <div className="workspace-file-view"><div className="workspace-file-toolbar"><strong>{selected.name}</strong>{'id' in selected.preview ? <button onClick={() => download((selected.preview as api.ArtifactPreview).id)}><FileDown size={14} /> Download</button> : null}</div>{'remoteImage' in selected.preview ? <img src={selected.preview.remoteImage} alt="Preview" /> : selected.preview.mime.startsWith('image/') ? <img src={selected.preview.dataUrl} alt={selected.name} /> : selected.preview.mime === 'text/html' ? <iframe title={selected.name} sandbox="allow-scripts" srcDoc={selected.preview.text || ''} /> : selected.preview.mime === 'application/pdf' ? <iframe title={selected.name} src={selected.preview.dataUrl} /> : <pre>{selected.preview.text ?? 'Preview unavailable'}</pre>}</div> : null}
    </div>
    <div id="workspace-view-browser" role="tabpanel" aria-labelledby="workspace-tab-browser" className="unified-workspace-content" hidden={tab !== 'browser'}>{visited.has('browser') ? <NativeBrowserPanel embedded active={open && tab === 'browser'} obscured={obscured} navigateTo={browserLocation} onClose={onClose} onNotice={onNotice} preview={null} onDownload={download} width={visibleWidth} onWidthChange={resize} side="right" onSideChange={() => {}} snapPx={snapPx} onSnapChange={onSnapChange} /> : null}</div>
    <div id="workspace-view-computer" role="tabpanel" aria-labelledby="workspace-tab-computer" className="unified-workspace-content" hidden={tab !== 'computer'}>{visited.has('computer') ? <DesktopPanel embedded active={open && tab === 'computer'} onClose={onClose} onNotice={onNotice} /> : null}</div>
    <div id="workspace-view-side-chat" role="tabpanel" aria-labelledby="workspace-tab-side-chat" className="unified-workspace-content" hidden={tab !== 'side-chat'}>{visited.has('side-chat') ? sideChat : null}</div>
  </aside>;
}
