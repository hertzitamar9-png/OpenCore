import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { MusicStudio } from './MusicStudio';
import * as api from './api';
vi.mock('./api', async (original) => ({ ...await original<typeof api>(), musicStudioStatus: vi.fn(), startMusicStudio: vi.fn(), openLocalPath: vi.fn(), modelLibrary: vi.fn().mockResolvedValue({models:[]}), listStudioJobs: vi.fn().mockResolvedValue([]), studioRuntime:vi.fn().mockResolvedValue(null) }));
const status = { installed: true, running: false, owned: false, url: null, folder: 'C:\\Users\\hertz\\YuE', modelLoaded: false, error: null };
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.musicStudioStatus).mockResolvedValue(status); });
it('connects the existing music interface only after the user opens it', async () => {
  vi.mocked(api.startMusicStudio).mockResolvedValue({ ...status, running: true, owned: true, url: 'http://127.0.0.1:7860' });
  render(<MusicStudio runtimeActive={false} onNotice={vi.fn()} />);
  const start = await screen.findByRole('button', { name: 'Open Music Studio' });
  expect(api.startMusicStudio).not.toHaveBeenCalled();
  fireEvent.click(start);
  const frame = await screen.findByTitle('YuE2 Music Studio');
  expect(frame).toHaveAttribute('src', 'http://127.0.0.1:7860');
});
it('does not embed an unrelated URL returned by the backend', async () => {
  vi.mocked(api.musicStudioStatus).mockResolvedValue({ ...status, running: true, url: 'https://other.example' });
  render(<MusicStudio runtimeActive={true} onNotice={vi.fn()} />);
  await screen.findByRole('button', { name: 'Open Music Studio' });
  expect(screen.queryByTitle('YuE2 Music Studio')).not.toBeInTheDocument();
  expect(screen.getByText(/Studio jobs switch models automatically after chat finishes/)).toBeInTheDocument();
});
it('keeps a startup failure visible after a healthy status refresh', async () => {
  vi.mocked(api.startMusicStudio).mockRejectedValue(new Error('Python failed to start'));
  render(<MusicStudio runtimeActive={false} onNotice={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole('button',{name:'Open Music Studio'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'Open Music Studio'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Python failed to start');
  fireEvent.click(screen.getByRole('button',{name:'Refresh Music Studio'}));
  await waitFor(() => expect(api.musicStudioStatus).toHaveBeenCalledTimes(2));
  expect(screen.getByRole('alert')).toHaveTextContent('Python failed to start');
});
