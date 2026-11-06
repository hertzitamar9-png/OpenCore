import * as React from 'react';
import { invoke } from '@tauri-apps/api/core';
import { loadPlatformConfiguration, savePlatformConfigurationPatch } from './agent-platform';
import { createComputerDriverPanel } from './computer-driver-ui.js';
import './computer-driver-ui.css';

async function request(path: string, body?: object) {
  const shared = Reflect.get(globalThis, '__opencoreComputerDriverRequest');
  if (typeof shared === 'function') return shared(path, body);
  const probe = await invoke<{hubBridge?: {available: boolean; url: string; secret: string; authHeader?: string}}>('runtime_setup_probe');
  const bridge = probe.hubBridge;
  if (!bridge?.available || !/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(bridge.url)) throw new Error('The local computer-use service is unavailable.');
  const response = await fetch(bridge.url + '/api' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {'Content-Type': 'application/json', [bridge.authHeader || 'X-OpenCore-Hub-Secret']: bridge.secret},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}), signal: AbortSignal.timeout(45000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The computer-use service request failed.');
  return result;
}

const Panel = createComputerDriverPanel(React, {
  request, loadConfiguration: loadPlatformConfiguration,
  saveServers: mcpServers => savePlatformConfigurationPatch({mcpServers}),
});

export function ComputerDriverSettings() {
  // Browser previews have no native service; never show a failing setup action.
  if (!Reflect.get(globalThis, '__TAURI_INTERNALS__') && !Reflect.get(globalThis, '__opencoreComputerDriverRequest')) return null;
  return <Panel />;
}
