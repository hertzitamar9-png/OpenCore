import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { FileSnapshotView } from './FileSnapshotView';
import * as files from './workspaces';
import type { FileRecord } from './workspaces';

const file: FileRecord = { id: 'html-1', conversationId: 'chat', turnId: 'task', path: 'index.html', change: 'modified', added: 1, removed: 1, beforeHash: 'a'.repeat(64), afterHash: 'b'.repeat(64), timestamp: '2026-10-06T00:00:00Z', size: 40, mime: 'text/html', source: 'C:/project/index.html', snapshotAvailable: true };
beforeEach(() => vi.restoreAllMocks());

it('renders stored HTML in a sandbox without script or origin permissions', async () => {
  vi.spyOn(files, 'workspaceFiles').mockResolvedValue({ name: 'index.html', mime: 'text/html', text: '<h1>Hello</h1><script>bad()</script>', sha256: file.afterHash!, size: 40 });
  render(<FileSnapshotView file={file} />);
  const frame = await screen.findByTitle('Preview of index.html');
  expect(frame).toHaveAttribute('sandbox', '');
  expect(frame).toHaveAttribute('srcdoc', '<h1>Hello</h1><script>bad()</script>');
  expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer');
});

it('opens the preserved before version of a deleted file and shows exact diff counts', async () => {
  const invoke = vi.spyOn(files, 'workspaceFiles').mockImplementation(async args => args.action === 'diff'
    ? { path: 'gone.txt', before: 'old\n', after: null, added: 0, removed: 1 }
    : { name: 'gone.txt', mime: 'text/plain', text: 'old\n', sha256: file.beforeHash!, size: 4 });
  render(<FileSnapshotView file={{ ...file, path: 'gone.txt', mime: 'text/plain', change: 'deleted', afterHash: null }} />);
  expect(await screen.findByText('old')).toBeVisible();
  expect(invoke).toHaveBeenCalledWith({ action: 'preview', id: file.id, version: 'before' });
  fireEvent.click(screen.getByRole('button', { name: 'Diff' }));
  await waitFor(() => expect(invoke).toHaveBeenCalledWith({ action: 'diff', id: file.id }));
  expect(await screen.findByLabelText('1 lines removed')).toBeVisible();
});

it('rejects late preview results after selecting another file', async () => {
  let resolveFirst: (value: files.FilePreviewResult) => void = () => {};
  vi.spyOn(files, 'workspaceFiles').mockImplementation(async args => args.action === 'preview' && args.id === 'html-1'
    ? await new Promise<files.FilePreviewResult>(resolve => { resolveFirst = resolve; })
    : { name: 'other.txt', mime: 'text/plain', text: 'new selection', sha256: 'c'.repeat(64), size: 13 });
  const view = render(<FileSnapshotView file={file} />);
  view.rerender(<FileSnapshotView file={{ ...file, id: 'other', path: 'other.txt', mime: 'text/plain' }} />);
  expect(await screen.findByText('new selection')).toBeVisible();
  resolveFirst({ name: 'index.html', mime: 'text/html', text: '<h1>stale selection</h1>', sha256: file.afterHash!, size: 24 });
  await waitFor(() => expect(screen.queryByTitle('Preview of index.html')).not.toBeInTheDocument());
});
