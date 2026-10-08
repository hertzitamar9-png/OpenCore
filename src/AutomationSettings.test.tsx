import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import * as api from './api';
import { BrowserAccessSettings, ComputerAccessSettings } from './AutomationSettings';

vi.mock('./api', async importOriginal => ({ ...await importOriginal<typeof api>(),
  computerAccess: vi.fn(), computerAccessWindows: vi.fn(), setComputerAccess: vi.fn(), setBrowserAccess: vi.fn(),
}));
vi.mock('@tauri-apps/api/event', () => ({ listen: vi.fn(async () => () => {}) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

function setup() {
  vi.mocked(api.computerAccess).mockResolvedValue({ enabled: false, apps: [], revision: 0 });
  vi.mocked(api.computerAccessWindows).mockResolvedValue([
    { windowId: 12, title: 'Notes', path: 'C:\\Apps\\Notes.exe', name: 'Notes.exe' },
    { windowId: 14, title: 'Notes', path: 'D:\\Other\\Notes.exe', name: 'Notes.exe' },
  ]);
  return vi.mocked(api.setComputerAccess).mockImplementation(async policy => ({ ...policy, revision: (policy.revision ?? 0) + 1 }));
}

it('runs PC-wide computer use without app approval controls and can still stop', async () => {
  const save = setup();
  render(<ComputerAccessSettings onNotice={() => {}} />);
  await waitFor(() => expect(screen.getByRole('switch')).toBeEnabled());
  fireEvent.click(screen.getByRole('switch', { name: 'Enable computer use' }));
  await waitFor(() => expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true'));
  expect(screen.queryByLabelText('Application permission')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Allow app' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Deny app' })).toBeNull();
  expect(api.computerAccessWindows).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Stop computer use' }));
  await waitFor(() => expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false'));
  expect(save.mock.calls.at(-1)?.[0].enabled).toBe(false);
});

it('keeps controls usable when running-app enumeration fails and never claims a failed save succeeded', async () => {
  setup();
  vi.mocked(api.computerAccessWindows).mockRejectedValue(new Error('App enumeration unavailable'));
  vi.mocked(api.setComputerAccess).mockRejectedValue(new Error('Cannot save permissions'));
  render(<ComputerAccessSettings onNotice={() => {}} />);
  await waitFor(() => expect(screen.getByRole('switch')).toBeEnabled());
  fireEvent.click(screen.getByRole('switch'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Cannot save permissions');
  expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
});

it('an urgent stop takes priority over the response from an earlier enable request', async () => {
  setup();
  let complete!: (policy: api.ComputerAccess) => void;
  vi.mocked(api.setComputerAccess).mockImplementation(policy => policy.enabled
    ? new Promise(resolve => { complete = resolve; }) : Promise.resolve({ ...policy, revision: 2 }));
  render(<ComputerAccessSettings onNotice={() => {}} />);
  await waitFor(() => expect(screen.getByRole('switch')).toBeEnabled());
  fireEvent.click(screen.getByRole('switch'));
  const stop = screen.getByRole('button', { name: 'Stop computer use' });
  expect(stop).toBeEnabled();
  fireEvent.click(stop);
  await waitFor(() => expect(vi.mocked(api.setComputerAccess)).toHaveBeenCalledTimes(2));
  await act(async () => complete({ enabled: true, apps: [], revision: 1 }));
  expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
});

it('disconnects Chrome and reconnects to a waiting state with no stale tabs', async () => {
  vi.spyOn(api, 'browserBridgeStatus').mockResolvedValue({ port: 8814, token: 'pairing', enabled: true, connected: true });
  vi.spyOn(api, 'browserCommand').mockResolvedValue({ tabs: [{ tabId: 7, title: 'Documentation', url: 'https://example.test', active: true }] });
  const connect = vi.mocked(api.setBrowserAccess).mockImplementation(async enabled => ({ port: 8814, token: 'pairing', enabled, connected: false }));
  render(<BrowserAccessSettings onNotice={() => {}} />);
  expect(await screen.findByText('Documentation')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect Chrome' }));
  expect(await screen.findByText('Browser access disabled')).toBeVisible();
  expect(screen.queryByText('Documentation')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Reconnect Chrome' }));
  expect(await screen.findByText('Waiting for the Chrome extension')).toBeVisible();
  expect(connect.mock.calls.map(([enabled]) => enabled)).toEqual([false, true]);
});

it('a late browser list cannot restore connected tabs after disconnect', async () => {
  vi.spyOn(api, 'browserBridgeStatus').mockResolvedValue({ port: 8814, token: 'pairing', enabled: true, connected: true });
  let complete!: (value: unknown) => void;
  vi.spyOn(api, 'browserCommand').mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  vi.mocked(api.setBrowserAccess).mockResolvedValue({ port: 8814, token: 'pairing', enabled: false, connected: false });
  render(<BrowserAccessSettings onNotice={() => {}} />);
  const disconnect = await screen.findByRole('button', { name: 'Disconnect Chrome' });
  await waitFor(() => expect(disconnect).toBeEnabled());
  fireEvent.click(disconnect);
  expect(await screen.findByText('Browser access disabled')).toBeVisible();
  await act(async () => complete({ tabs: [{ tabId: 7, title: 'Late stale tab', url: 'https://example.test', active: true }] }));
  expect(screen.queryByText('Late stale tab')).toBeNull();
});
