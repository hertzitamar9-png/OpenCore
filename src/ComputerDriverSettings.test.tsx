import * as React from 'react';
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { createComputerDriverPanel } from './computer-driver-ui.js';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function fixture(installed = true) {
  const other = {id: 'other', name: 'other', enabled: true, env: {API_KEY: '[redacted]'}};
  const cua = {id: 'opencore-cua-driver', name: 'cua_driver', enabled: true};
  const services = {
    request: vi.fn(async (path: string) => path.endsWith('/connection') ? cua :
      {supported: true, installed, status: 'idle', version: '0.40.2', downloadedBytes: 0, totalBytes: 35951555}),
    loadConfiguration: vi.fn(async () => ({mcpServers: [other]})),
    saveServers: vi.fn(async (_servers: unknown[]) => {}),
  };
  return {services, other, cua, Panel: createComputerDriverPanel(React, services)};
}

it('connects an installed driver without downloading it and preserves other MCP servers', async () => {
  const {services, Panel, other, cua} = fixture();
  render(<Panel />);
  fireEvent.click(await screen.findByRole('button', {name: 'Connect Cua Driver'}));
  await screen.findByText('Connected to agent');
  expect(services.saveServers).toHaveBeenCalledWith([other, cua]);
  expect(services.request.mock.calls.some(([path]) => path.endsWith('/install'))).toBe(false);
  fireEvent.click(screen.getByRole('button', {name: 'Disconnect Cua Driver'}));
  await waitFor(() => expect(services.saveServers).toHaveBeenLastCalledWith([other]));
  expect(services.request).toHaveBeenCalledWith('/computer-driver/disconnect', {});
});

it('requires an explicit install and displays actual byte progress', async () => {
  const {services, Panel} = fixture(false);
  render(<Panel />);
  const install = await screen.findByRole('button', {name: 'Install and connect · 36 MB'});
  expect(services.request.mock.calls).toEqual([['/computer-driver/status']]);
  services.request.mockImplementation(async path => ({supported: true, installed: false,
    status: 'downloading', stage: 'Downloading Cua Driver', version: '0.40.2', downloadedBytes: 18e6, totalBytes: 35951555}));
  fireEvent.click(install);
  const progress = await screen.findByRole('progressbar', {name: 'Cua Driver installation progress'});
  expect(progress.getAttribute('value')).toBe('18000000');
  expect(screen.getByText(/18.0 \/ 36.0 MB/)).toBeTruthy();
  expect(screen.getByRole('button', {name: 'Installing…'}).hasAttribute('disabled')).toBe(true);
});

it('explains the paired-PC option in phone mode without making desktop setup calls', async () => {
  vi.stubGlobal('__OPENCORE_PHONE__', true);
  const {services, Panel} = fixture();
  render(<Panel />);
  await screen.findByText(/Available on the paired 64-bit Windows PC/);
  expect(services.request).not.toHaveBeenCalled();
  expect(services.loadConfiguration).not.toHaveBeenCalled();
  expect(screen.queryByRole('alert')).toBeNull();
});
