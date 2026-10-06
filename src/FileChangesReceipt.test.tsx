import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { FileChangesReceipt } from './FileChangesReceipt';
import * as files from './workspaces';
import type { FileChangesResult, FileRecord } from './workspaces';

const record: FileRecord = { id: 'file-1', conversationId: 'chat', turnId: 'turn-1', path: 'src/main.ts', change: 'modified', added: 2, removed: 1, beforeHash: 'a'.repeat(64), afterHash: 'b'.repeat(64), timestamp: '2026-10-06T00:00:00Z', size: 25, mime: 'text/plain', source: 'C:/project/src/main.ts', snapshotAvailable: true };
const result: FileChangesResult = { turnId: 'turn-1', files: [record], added: 2, removed: 1, coverage: [], status: 'completed' };
beforeEach(() => vi.restoreAllMocks());

it('shows real counts for the latest task and opens its actual file record', async () => {
  vi.spyOn(files, 'workspaceFiles').mockResolvedValue(result);
  const onOpen = vi.fn();
  render(<FileChangesReceipt conversationId="chat" active={false} onOpen={onOpen} />);
  const file = await screen.findByRole('button', { name: /src\/main.ts/ });
  expect(screen.getByLabelText('2 lines added')).toBeVisible();
  expect(screen.getByLabelText('1 lines removed')).toBeVisible();
  fireEvent.click(file);
  expect(onOpen).toHaveBeenCalledWith(record);
});

it('hides the old receipt throughout a new task and rejects a stale completion fetch', async () => {
  const fetch = vi.spyOn(files, 'workspaceFiles').mockResolvedValue(result);
  const props = { conversationId: 'chat', onOpen: vi.fn() };
  const view = render(<FileChangesReceipt {...props} active={false} />);
  await screen.findByRole('button', { name: /src\/main.ts/ });
  view.rerender(<FileChangesReceipt {...props} active />);
  expect(screen.queryByRole('button', { name: /src\/main.ts/ })).not.toBeInTheDocument();
  view.rerender(<FileChangesReceipt {...props} active={false} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole('button', { name: /src\/main.ts/ })).not.toBeInTheDocument();
  fetch.mockResolvedValue({ ...result, turnId: 'turn-2', files: [{ ...record, id: 'file-2', turnId: 'turn-2', path: 'new.txt' }] });
  window.dispatchEvent(new Event('opencore-file-changes'));
  expect(await screen.findByRole('button', { name: /new.txt/ })).toBeVisible();
});

it('keeps failed partial files and reports binary output without fake line counts', async () => {
  vi.spyOn(files, 'workspaceFiles').mockResolvedValue({ ...result, status: 'failed', added: 0, removed: 0, coverage: ['Skipped large file weights.gguf'], files: [{ ...record, path: 'cover.png', mime: 'image/png', change: 'output', added: null, removed: null }] });
  render(<FileChangesReceipt conversationId="chat" active={false} onOpen={vi.fn()} />);
  await screen.findByRole('button', { name: /cover.png/ });
  expect(screen.getByText(/partial changes/i)).toBeVisible();
  expect(screen.getByText('Binary output')).toBeVisible();
  expect(screen.getByText(/Skipped large file/)).toBeInTheDocument();
  expect(screen.queryByText('+0')).not.toBeInTheDocument();
});

it('does not restore changes from a prior task when the latest task changed no files', async () => {
  vi.spyOn(files, 'workspaceFiles').mockResolvedValue({ ...result, turnId: 'empty', files: [], added: 0, removed: 0 });
  const view = render(<FileChangesReceipt conversationId="chat" active={false} onOpen={vi.fn()} />);
  await waitFor(() => expect(files.workspaceFiles).toHaveBeenCalled());
  expect(view.container).toBeEmptyDOMElement();
});
