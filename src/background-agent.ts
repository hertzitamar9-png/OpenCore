import { invoke } from '@tauri-apps/api/core';

export interface BackgroundAgentConfig { enabled: boolean; startAtLogin: boolean; revision: number }
export interface BackgroundAgentStatus {
  configuration: BackgroundAgentConfig;
  trayAvailable: boolean;
  loginStartupSupported: boolean;
  loginRegistered: boolean;
  windowVisible: boolean;
}
export async function backgroundAgentStatus(): Promise<BackgroundAgentStatus> {
  if (!('__TAURI_INTERNALS__' in window)) return { configuration: { enabled: false, startAtLogin: false, revision: 0 }, trayAvailable: false, loginStartupSupported: false, loginRegistered: false, windowVisible: true };
  return invoke('background_agent_status');
}
export async function configureBackgroundAgent(configuration: BackgroundAgentConfig): Promise<BackgroundAgentStatus> {
  if (!('__TAURI_INTERNALS__' in window)) throw new Error('Background execution requires the desktop app.');
  return invoke('configure_background_agent', { configuration });
}
