import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import * as api from './api';
import { BrowserPanel } from './BrowserPanel';

vi.mock('./api', async importOriginal => ({ ...await importOriginal<typeof api>(), setBrowserAccess: vi.fn() }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const status = { port: 8814, token: 'pairing', enabled: true, connected: true };
const tabs = [{ tabId: 7, title: 'Fixture', url: 'https://example.test', active: true }];
const shot = { tabId: 7, dataUrl: 'data:image/png;base64,YQ==', viewport: { width: 800, height: 600 } };

it('disconnect clears the capture and late screenshot responses cannot restore it', async () => {
  vi.spyOn(api, 'browserBridgeStatus').mockResolvedValue(status);
  let complete!: (value: typeof shot) => void;
  vi.spyOn(api, 'browserCommand').mockImplementation(async action => action === 'list' ? { tabs } as never
    : new Promise(resolve => { complete = resolve; }));
  vi.mocked(api.setBrowserAccess).mockResolvedValue({ ...status, enabled: false, connected: false });
  render(<BrowserPanel embedded onNotice={() => {}} />);
  const disconnect = await screen.findByRole('button', { name: 'Disconnect Chrome' });
  await waitFor(() => expect(disconnect).toBeEnabled());
  fireEvent.click(disconnect);
  expect(await screen.findByText('Browser access disabled')).toBeVisible();
  await act(async () => complete(shot));
  expect(screen.queryByAltText('Chrome tab screenshot')).toBeNull();
  expect(screen.getByRole('button', { name: 'Reconnect Chrome' })).toBeEnabled();
});

it('address Enter cannot send a command while browser access is disconnected', async () => {
  vi.spyOn(api, 'browserBridgeStatus').mockResolvedValue({ ...status, connected: false });
  const command = vi.spyOn(api, 'browserCommand').mockResolvedValue({ tabs: [] });
  render(<BrowserPanel embedded onNotice={() => {}} />);
  await screen.findByText('Connect Chrome');
  const input = screen.getByLabelText('Browser address');
  fireEvent.change(input, { target: { value: 'example.test' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await act(async () => { await Promise.resolve(); });
  expect(command).not.toHaveBeenCalled();
});
