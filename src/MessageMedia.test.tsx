import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ArtifactActionsContext, MessageImage, ResponseActivity } from './AssistantConversation';
import * as api from './api';
import type { TimelineEntry } from './types';

const actions = { preview: vi.fn(), previewAttachment: vi.fn(), download: vi.fn(), remoteImage: vi.fn(), browserLink: vi.fn() };
const imageData = 'data:image/png;base64,aGVsbG8=';
beforeEach(() => { vi.restoreAllMocks(); Object.values(actions).forEach(action => action.mockClear()); });
const withActions = (children: React.ReactNode) => <ArtifactActionsContext.Provider value={actions}>{children}</ArtifactActionsContext.Provider>;

it('loads a Windows file URL through the native preview and opens its local file', async () => {
  const preview = vi.spyOn(api, 'previewAttachmentImage').mockResolvedValue(imageData);
  render(withActions(<MessageImage src="file:///C:/generated/My%20picture.png" alt="Generated picture" />));
  await waitFor(() => expect(screen.getByRole('img', { name: 'Generated picture' })).toHaveAttribute('src', imageData));
  expect(preview).toHaveBeenCalledWith('C:/generated/My picture.png');
  fireEvent.click(screen.getByRole('button', { name: 'Preview Generated picture' }));
  expect(actions.previewAttachment).toHaveBeenCalledWith('C:/generated/My picture.png');
  expect(actions.remoteImage).not.toHaveBeenCalled();
});

it('keeps the new image when an older local preview finishes late', async () => {
  let finishOld!: (data: string) => void;
  vi.spyOn(api, 'previewAttachmentImage').mockImplementation(path => path.endsWith('old.png') ? new Promise(resolve => { finishOld = resolve; }) : Promise.resolve(imageData));
  const view = render(withActions(<MessageImage src="C:/generated/old.png" alt="Picture" />));
  view.rerender(withActions(<MessageImage src="C:/generated/new.png" alt="Picture" />));
  await waitFor(() => expect(screen.getByRole('img')).toHaveAttribute('src', imageData));
  await act(async () => finishOld('data:image/png;base64,b2xk'));
  expect(screen.getByRole('img')).toHaveAttribute('src', imageData);
});

it('shows a readable failure with a working local preview action instead of a broken image', async () => {
  vi.spyOn(api, 'previewAttachmentImage').mockRejectedValue(new Error('Image no longer exists'));
  render(withActions(<MessageImage src="C:/generated/missing.png" alt="Missing output" />));
  expect(await screen.findByText(/Image preview unavailable/)).toBeVisible();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Preview Missing output' }));
  expect(actions.previewAttachment).toHaveBeenCalledWith('C:/generated/missing.png');
});

it('renders assistant file metadata alongside its message', async () => {
  vi.spyOn(api, 'previewAttachmentImage').mockResolvedValue(imageData);
  const entry: TimelineEntry = { id: 1, conversationId: 'chat', timestamp: '2026-10-06T10:00:00Z', kind: 'message', role: 'assistant', source: 'OpenCore', title: 'Answer', content: 'Here are your files.', metadata: { files: ['C:/generated/result.png', { name: 'snake.html', path: 'C:/generated/snake.html' }] } };
  render(withActions(<ResponseActivity events={[entry]} active={false} />));
  expect(await screen.findByRole('img', { name: 'result.png' })).toHaveAttribute('src', imageData);
  fireEvent.click(screen.getByRole('button', { name: 'Preview snake.html' }));
  expect(actions.previewAttachment).toHaveBeenCalledWith('C:/generated/snake.html');
});

it('normalizes file URLs in assistant attachment metadata before loading or opening them', async () => {
  const preview = vi.spyOn(api, 'previewAttachmentImage').mockResolvedValue(imageData);
  const entry: TimelineEntry = { id: 2, conversationId: 'chat', timestamp: '2026-10-06T10:00:00Z', kind: 'message', role: 'assistant', source: 'OpenCore', title: 'Answer', content: 'Saved image.', metadata: { files: ['file:///C:/generated/My%20picture.png', { name: 'source.html', path: 'file:///C:/generated/source.html' }, 'C:/generated/100% literal%20name.png'] } };
  render(withActions(<ResponseActivity events={[entry]} active={false} />));
  expect(await screen.findByRole('img', { name: 'My picture.png' })).toHaveAttribute('src', imageData);
  expect(preview).toHaveBeenCalledWith('C:/generated/My picture.png');
  await waitFor(() => expect(preview).toHaveBeenCalledWith('C:/generated/100% literal%20name.png'));
  fireEvent.click(screen.getByRole('button', { name: 'Preview source.html' }));
  expect(actions.previewAttachment).toHaveBeenCalledWith('C:/generated/source.html');
});
