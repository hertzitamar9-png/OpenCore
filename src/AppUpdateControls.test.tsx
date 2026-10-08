import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { getVersion } from '@tauri-apps/api/app';
import * as api from './api';
import { UpdateButton, UpdateSettings } from './AppUpdateControls';

const openUrl = vi.fn();
vi.mock('@tauri-apps/api/app', () => ({ getVersion: vi.fn(async () => '1.2.0') }));
vi.mock('@tauri-apps/plugin-opener', () => ({ openUrl: (...args: unknown[]) => openUrl(...args) }));
vi.mock('./api', async (original) => ({
  ...await original<typeof api>(),
  checkLatestAppVersion: vi.fn(),
  installLatestAppUpdate: vi.fn(),
}));

beforeEach(() => { vi.resetAllMocks(); vi.mocked(getVersion).mockResolvedValue('1.2.0'); });

it('always shows the running version and check action without claiming latest before a successful check', async () => {
  let completeCheck!: (value: api.AppUpdateCheck) => void;
  vi.mocked(api.checkLatestAppVersion).mockReturnValue(new Promise(resolve => { completeCheck = resolve; }));
  render(<UpdateButton />);

  expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument();
  expect(await screen.findByText('v1.2.0')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Check for updates' })).toBeDisabled();
  expect(screen.queryByText('Up to date')).toBeNull();
  await act(async () => completeCheck({ currentVersion: '1.2.0', available: false, version: null }));
  expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument();
  const version = screen.getByText('v1.2.0');
  const check = screen.getByRole('button', { name: 'Check for updates' });
  expect(version.compareDocumentPosition(check) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(check).toBeEnabled();
  expect(screen.getByText('Up to date')).toBeVisible();
  fireEvent.click(check);
  expect(await screen.findByRole('status')).toHaveTextContent('OpenCore 1.2.0 is up to date.');
  expect(version.parentElement?.nextElementSibling).toBe(check);
  expect(api.installLatestAppUpdate).not.toHaveBeenCalled();
});

it('shows a confirmed update and requires a second click to install', async () => {
  vi.mocked(api.checkLatestAppVersion).mockResolvedValue({ currentVersion: '1.2.0', available: true, version: '1.3.0' });
  render(<UpdateButton />);

  fireEvent.click(await screen.findByRole('button', { name: 'Update' }));
  expect(await screen.findByText('OpenCore 1.3.0 is available.')).toBeVisible();
  expect(api.installLatestAppUpdate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Install update' }));
  expect(api.installLatestAppUpdate).toHaveBeenCalledOnce();
  expect(await screen.findByText('The installer is starting. OpenCore will reopen after the update.')).toBeVisible();
});

it('hides the inline update message after five seconds while retaining the install action', async () => {
  vi.mocked(api.checkLatestAppVersion).mockResolvedValue({ currentVersion: '1.2.0', available: true, version: '1.3.0' });
  render(<UpdateButton />);
  const update = await screen.findByRole('button', { name: 'Update' });
  vi.useFakeTimers();
  try {
    await act(async () => { fireEvent.click(update); });
    const message = screen.getByText('OpenCore 1.3.0 is available.');
    expect(message.compareDocumentPosition(update) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    act(() => vi.advanceTimersByTime(4999));
    expect(message).toBeVisible();
    act(() => vi.advanceTimersByTime(1));
    expect(message).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Install update' })).toBeVisible();
  } finally { vi.useRealTimers(); }
});

it('keeps the running version and retry action when checking fails', async () => {
  vi.mocked(api.checkLatestAppVersion).mockRejectedValue(new Error('Feed unavailable'));
  await act(async () => { render(<UpdateButton />); });

  expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument();
  expect(screen.getByText('v1.2.0')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Check for updates' })).toBeEnabled();
  expect(screen.queryByText('Up to date')).toBeNull();
  vi.mocked(api.checkLatestAppVersion).mockResolvedValue({ currentVersion: '1.2.0', available: false, version: null });
  fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
  expect(await screen.findByText('Up to date')).toBeVisible();
  expect(api.installLatestAppUpdate).not.toHaveBeenCalled();
});

it('rechecks from the header and changes to Update only when a newer version is found', async () => {
  vi.mocked(api.checkLatestAppVersion).mockResolvedValue({ currentVersion: '1.2.0', available: false, version: null });
  render(<UpdateButton />);
  const check = await screen.findByRole('button', { name: 'Check for updates' });
  await screen.findByText('Up to date');
  vi.mocked(api.checkLatestAppVersion).mockResolvedValue({ currentVersion: '1.2.0', available: true, version: '1.3.0' });
  fireEvent.click(check);
  expect(await screen.findByRole('button', { name: 'Update' })).toBeVisible();
  expect(screen.getByText('v1.2.0')).toBeVisible();
  expect(screen.queryByText('Up to date')).toBeNull();
  expect(api.installLatestAppUpdate).not.toHaveBeenCalled();
});

it('shares an availability request across headers and does not recheck on rerender', async () => {
  vi.mocked(api.checkLatestAppVersion).mockResolvedValue({ currentVersion: '1.2.0', available: true, version: '1.3.0' });
  const headers = <><UpdateButton /><UpdateButton /></>;
  const { rerender } = render(headers);

  expect(await screen.findAllByRole('button', { name: 'Update' })).toHaveLength(2);
  expect(api.checkLatestAppVersion).toHaveBeenCalledOnce();
  expect(api.installLatestAppUpdate).not.toHaveBeenCalled();
  rerender(<><UpdateButton /><UpdateButton /></>);
  expect(api.checkLatestAppVersion).toHaveBeenCalledOnce();
  expect(api.installLatestAppUpdate).not.toHaveBeenCalled();
});

it('keeps header availability in sync with manual Settings checks', async () => {
  const current = { currentVersion: '1.2.0', available: false, version: null };
  vi.mocked(api.checkLatestAppVersion).mockResolvedValue(current);
  await act(async () => { render(<><UpdateButton /><UpdateSettings /></>); });
  expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument();

  vi.mocked(api.checkLatestAppVersion).mockResolvedValue({ currentVersion: '1.2.0', available: true, version: '1.3.0' });
  fireEvent.click(screen.getByRole('button', { name: 'Check latest version' }));
  expect(await screen.findByText('OpenCore 1.3.0 is available.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Update' })).toBeVisible();

  vi.mocked(api.checkLatestAppVersion).mockResolvedValue(current);
  fireEvent.click(screen.getByRole('button', { name: 'Check latest version' }));
  expect(await screen.findByText('OpenCore 1.2.0 is up to date.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument();
  expect(api.installLatestAppUpdate).not.toHaveBeenCalled();
});

it('tells the user active model work is stopped before installation begins', async () => {
  vi.mocked(api.checkLatestAppVersion).mockResolvedValue({ currentVersion: '1.2.0', available: true, version: '1.3.0' });
  let complete!: () => void;
  vi.mocked(api.installLatestAppUpdate).mockReturnValue(new Promise<void>(resolve => { complete = resolve; }));
  render(<UpdateButton />);
  fireEvent.click(await screen.findByRole('button', { name: 'Update' }));
  await screen.findByText('OpenCore 1.3.0 is available.');
  fireEvent.click(screen.getByRole('button', { name: 'Install update' }));
  expect(await screen.findByText('Stopping active model work, then installing OpenCore 1.3.0…')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Updating…' })).toBeDisabled();
  await act(async () => complete());
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
