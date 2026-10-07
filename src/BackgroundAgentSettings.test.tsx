import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { BackgroundAgentSettings } from './BackgroundAgentSettings';
import * as agent from './background-agent';

const state: agent.BackgroundAgentStatus = { configuration: { enabled: false, startAtLogin: false, revision: 7 }, trayAvailable: true, loginStartupSupported: true, loginRegistered: false, windowVisible: true };
beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(agent, 'backgroundAgentStatus').mockResolvedValue(structuredClone(state));
  vi.spyOn(agent, 'configureBackgroundAgent').mockImplementation(async configuration => ({ ...state, configuration: { ...configuration, revision: configuration.revision + 1 }, loginRegistered: configuration.startAtLogin }));
});
it('autosaves background execution and keeps login startup independent', async () => {
  render(<BackgroundAgentSettings />);
  const enabled = await screen.findByRole('checkbox', { name: 'Keep jobs running when the window closes' });
  expect((enabled as HTMLInputElement).checked).toBe(false);
  fireEvent.click(enabled);
  await waitFor(() => expect((enabled as HTMLInputElement).checked).toBe(true));
  expect(agent.configureBackgroundAgent).toHaveBeenCalledWith({ enabled: true, startAtLogin: false, revision: 7 });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Start the background agent when I sign in to Windows' }));
  await waitFor(() => expect(agent.configureBackgroundAgent).toHaveBeenLastCalledWith({ enabled: true, startAtLogin: true, revision: 8 }));
  expect(screen.getByText(/Quit OpenCore from its tray menu/)).toBeTruthy();
});
it('disables login registration together with background execution', async () => {
  vi.mocked(agent.backgroundAgentStatus).mockResolvedValue({ ...state, configuration: { enabled: true, startAtLogin: true, revision: 12 }, loginRegistered: true });
  render(<BackgroundAgentSettings />);
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Keep jobs running when the window closes' }));
  await waitFor(() => expect(agent.configureBackgroundAgent).toHaveBeenCalledWith({ enabled: false, startAtLogin: false, revision: 12 }));
});
it('retains the saved state and displays a failed save', async () => {
  vi.mocked(agent.configureBackgroundAgent).mockRejectedValue(new Error('Login registration was denied'));
  render(<BackgroundAgentSettings />);
  const enabled = await screen.findByRole('checkbox', { name: 'Keep jobs running when the window closes' });
  fireEvent.click(enabled);
  expect((await screen.findByRole('alert')).textContent).toContain('Login registration was denied');
  expect((enabled as HTMLInputElement).checked).toBe(false);
});
it('keeps close behavior unchanged when no tray is available', async () => {
  vi.mocked(agent.backgroundAgentStatus).mockResolvedValue({ ...state, trayAvailable: false });
  render(<BackgroundAgentSettings />);
  expect((await screen.findByRole('checkbox', { name: 'Keep jobs running when the window closes' }) as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByText(/system tray is unavailable/)).toBeTruthy();
});
