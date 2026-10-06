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
