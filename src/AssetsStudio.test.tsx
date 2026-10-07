import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { GameDevStudio } from './AssetsStudio';
import * as api from './api';

afterEach(() => vi.restoreAllMocks());

it('forwards Browse models from the active Game Dev Studio category', async () => {
  vi.spyOn(api, 'modelLibrary').mockResolvedValue({models: [], progress: null, diskFreeBytes: 88e9, minimumFreeBytes: 64e6});
  vi.spyOn(api, 'listStudioJobs').mockResolvedValue([]);
  const browse = vi.fn();
  render(<GameDevStudio onNotice={vi.fn()} onBrowseModels={browse} />);
  fireEvent.click(await screen.findByRole('button', {name: 'Browse models'}));
  expect(browse).toHaveBeenLastCalledWith('image');
  fireEvent.click(screen.getByRole('button', {name: '3D animation'}));
  fireEvent.click(await screen.findByRole('button', {name: 'Browse models'}));
  expect(browse).toHaveBeenLastCalledWith('3d-animation');
});
