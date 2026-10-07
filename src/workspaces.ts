import { invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';

export interface FileRecord {
  id: string;
  conversationId: string;
  turnId: string;
  path: string;
  change: 'created' | 'modified' | 'deleted' | 'output';
  added: number | null;
  removed: number | null;
  beforeHash: string | null;
  afterHash: string | null;
  timestamp: string;
  size: number;
  mime: string;
  source: string;
  snapshotAvailable: boolean;
  origin?: 'workspace' | 'studio' | 'indexed' | 'published';
  status?: string;
}
export interface WorkspaceFilesResult { files: FileRecord[]; coverage: string[]; nextCursor?: string | null; hasMore?: boolean }
export interface FileChangesResult extends WorkspaceFilesResult { turnId: string | null; added: number; removed: number; status?: string; timestamp?: string }
export interface FilePreviewResult { name: string; mime: string; text?: string; dataUrl?: string; sha256: string; size: number; snapshotAvailable?: boolean }
export interface FileDiffResult { path: string; before?: string | null; after?: string | null; added: number | null; removed: number | null; binary?: boolean; coverage?: string[] }
export type WorkspaceFilesArgs =
  | { action: 'list'; conversationId?: string; search?: string; limit?: number; cursor?: string }
  | { action: 'changes'; conversationId: string; turnId?: string }
  | { action: 'preview'; id: string; version?: 'before' | 'after' }
  | { action: 'diff'; id: string }
  | { action: 'index'; workspace?: string; conversationId?: string; turnId?: string; paths?: string[]; entries?: { path: string; name?: string; mime?: string }[]; jobId?: string; source?: 'studio' | 'published' | 'indexed'; live?: boolean };
export type WorkspaceFilesResponse = WorkspaceFilesResult | FileChangesResult | FilePreviewResult | FileDiffResult;

export function workspaceFiles(args: Extract<WorkspaceFilesArgs, { action: 'list' | 'index' }>): Promise<WorkspaceFilesResult>;
export function workspaceFiles(args: Extract<WorkspaceFilesArgs, { action: 'changes' }>): Promise<FileChangesResult>;
export function workspaceFiles(args: Extract<WorkspaceFilesArgs, { action: 'preview' }>): Promise<FilePreviewResult>;
export function workspaceFiles(args: Extract<WorkspaceFilesArgs, { action: 'diff' }>): Promise<FileDiffResult>;
export function workspaceFiles(args: WorkspaceFilesArgs): Promise<WorkspaceFilesResponse>;
export async function workspaceFiles(args: WorkspaceFilesArgs): Promise<WorkspaceFilesResponse> {
  if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) return invoke('workspace_files', { args });
  if (args.action === 'list') return { files: [], coverage: ['Open the desktop app to inspect saved workspace files.'] };
  if (args.action === 'changes') return { turnId: null, files: [], added: 0, removed: 0, coverage: [] };
  throw new Error('File snapshots require the OpenCore desktop app.');
}

/** Subscribe without polling or waking inference. Window events also serve local UI previews. */
export function subscribeWorkspaceFiles(refresh: () => void): () => void {
  let closed = false;
  let unlisten: UnlistenFn | undefined;
  window.addEventListener('opencore-file-changes', refresh);
  if ('__TAURI_INTERNALS__' in window) {
    void listen('opencore-file-changes', refresh).then(stop => { if (closed) stop(); else unlisten = stop; }).catch(() => {});
  }
  return () => { closed = true; unlisten?.(); window.removeEventListener('opencore-file-changes', refresh); };
}

export const fileName = (path: string) => path.split(/[\\/]/).pop() || path;
export async function openWorkspaceFileExternal(id: string, version?: 'before' | 'after'): Promise<{ url: string; name: string; sha256: string }> {
  return invoke('open_workspace_file', { id, version });
}
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
