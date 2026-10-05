import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import * as api from './api';
import { UpdateButton, UpdateSettings } from './AppUpdateControls';

const openUrl = vi.fn();
vi.mock('@tauri-apps/plugin-opener', () => ({ openUrl: (...args: unknown[]) => openUrl(...args) }));
vi.mock('./api', async (original) => ({
  ...await original<typeof api>(),
  checkLatestAppVersion: vi.fn(),
  installLatestAppUpdate: vi.fn(),
}));

beforeEach(() => vi.clearAllMocks());

it('checks only after the user opens Update and requires a second click to install', async () => {
  vi.mocked(api.checkLatestAppVersion).mockResolvedValue({ currentVersion: '1.2.0', available: true, version: '1.3.0' });
  render(<UpdateButton />);

  expect(api.checkLatestAppVersion).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Update' }));
  expect(await screen.findByText('OpenCore 1.3.0 is available.')).toBeVisible();
  expect(api.installLatestAppUpdate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Install update' }));
  expect(api.installLatestAppUpdate).toHaveBeenCalledOnce();
});

it('tells the user active model work is stopped before installation begins', async () => {
  vi.mocked(api.checkLatestAppVersion).mockResolvedValue({ currentVersion: '1.2.0', available: true, version: '1.3.0' });
  vi.mocked(api.installLatestAppUpdate).mockReturnValue(new Promise(() => {}));
  render(<UpdateButton />);
  fireEvent.click(screen.getByRole('button', { name: 'Update' }));
  await screen.findByText('OpenCore 1.3.0 is available.');
  fireEvent.click(screen.getByRole('button', { name: 'Install update' }));
  expect(await screen.findByText('Stopping active model work, then installing OpenCore 1.3.0…')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Updating…' })).toBeDisabled();
});

it('keeps Settings update checks manual and exposes the reinstall download action', async () => {
  vi.mocked(api.checkLatestAppVersion).mockResolvedValue({ currentVersion: '1.2.0', available: false, version: null });
  render(<UpdateSettings />);
  expect(api.checkLatestAppVersion).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Check latest version' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Download installer again' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Check latest version' }));
  expect(await screen.findByText('OpenCore 1.2.0 is up to date.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Download installer again' }));
  expect(openUrl).toHaveBeenCalledWith('https://github.com/hertzitamar9-png/OpenCore/releases/latest');
});
