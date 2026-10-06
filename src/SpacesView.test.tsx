import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { SpacesView } from './SpacesView';
import * as files from './workspaces';
import type { FileRecord } from './workspaces';

const record: FileRecord = { id: 'file-1', conversationId: 'origin-chat', turnId: 'turn-1', path: 'src/main.ts', change: 'modified', added: 2, removed: 1, beforeHash: 'a'.repeat(64), afterHash: 'b'.repeat(64), timestamp: '2026-10-06T00:00:00Z', size: 25, mime: 'text/plain', source: 'C:/project/src/main.ts', snapshotAvailable: true };
beforeEach(() => vi.restoreAllMocks());

it('routes files and their known originating chats to the workspace panel', async () => {
  vi.spyOn(files, 'workspaceFiles').mockResolvedValue({ files: [record], coverage: ['Excluded node_modules'] });
  const onOpenFile = vi.fn(); const onOpenConversation = vi.fn();
  render(<SpacesView onNotice={vi.fn()} onOpenFile={onOpenFile} onOpenConversation={onOpenConversation} />);
  fireEvent.click(await screen.findByRole('button', { name: /^src\/main.ts/ }));
  expect(onOpenFile).toHaveBeenCalledWith(record);
  fireEvent.click(screen.getByRole('button', { name: /open originating chat/i }));
  expect(onOpenConversation).toHaveBeenCalledWith('origin-chat');
  expect(screen.getByText('Excluded node_modules')).toBeInTheDocument();
});

it('searches persisted file history and keeps its requested chat filter', async () => {
  const list = vi.spyOn(files, 'workspaceFiles').mockResolvedValue({ files: [], coverage: [] });
  render(<SpacesView conversationId="chat-filter" onNotice={vi.fn()} />);
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search file history' }), { target: { value: 'main.ts' } });
  await waitFor(() => expect(list).toHaveBeenLastCalledWith({ action: 'list', conversationId: 'chat-filter', search: 'main.ts', limit: 300 }));
});

it('opens a Space file externally while keeping snapshot review available separately', async () => {
  vi.spyOn(files, 'workspaceFiles').mockResolvedValue({ files: [record], coverage: [] });
  const onOpenExternal = vi.fn(); const onOpenFile = vi.fn();
  render(<SpacesView onNotice={vi.fn()} onOpenFile={onOpenFile} onOpenExternal={onOpenExternal} />);
  fireEvent.click(await screen.findByRole('button', { name: /^src\/main.ts/ }));
  await waitFor(() => expect(onOpenExternal).toHaveBeenCalledWith(record));
  expect(onOpenFile).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Preview saved version of src/main.ts' }));
  expect(onOpenFile).toHaveBeenCalledWith(record);
});

it('reports a failed external open without changing the selected chat or hiding its file', async () => {
  vi.spyOn(files, 'workspaceFiles').mockResolvedValue({ files: [record], coverage: [] });
  const onNotice = vi.fn(); const onOpenConversation = vi.fn();
  render(<SpacesView onNotice={onNotice} onOpenConversation={onOpenConversation} onOpenExternal={async () => { throw new Error('Recorded snapshot is unavailable'); }} />);
  fireEvent.click(await screen.findByRole('button', { name: /^src\/main.ts/ }));
  await waitFor(() => expect(onNotice).toHaveBeenCalledWith(expect.stringContaining('Recorded snapshot is unavailable')));
  expect(onOpenConversation).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: /^src\/main.ts/ })).toBeEnabled();
});
