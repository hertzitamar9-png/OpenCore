import * as React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { installGroupWorkspaceUI } from './group-workspace-ui.js';
import { groupReadyModels, resolveGroupTargets } from './group-chats.js';

const phoneReason = 'Chat Core group collaboration requires the paired PC service. Choose Paired PC in Execution device settings to use your PC\'s real groups.';

function fixture(capability = Promise.resolve({ available: false, reason: phoneReason })) {
  const group = { id: 'group-one', name: 'Existing workspace', hosting: true, connected: true };
  const view = {
    group, self: { id: 'owner', name: 'Owner', role: 'owner' }, members: [], agents: [],
    channel: { id: 'general', name: 'General', private: false },
    channels: [{ id: 'general', name: 'General', private: false }], projects: [],
    messages: [], tasks: [], plan: [], sharing: { enabled: false, models: [], projectBindings: {} },
  };
  const service = vi.fn(async (action, body) => {
    if (action === 'capabilities') {
      if (capability instanceof Error) throw capability;
      return capability;
    }
    if (action === 'list') return { groups: [group], localName: 'Owner' };
    if (action === 'view') return view;
    if (action === 'message') return {
      ...view, messages: [{ id: 'sent-message', kind: 'user', authorName: 'Owner', text: body.text }],
    };
    throw Error('Unexpected group action: ' + action);
  });
  const invoke = vi.fn(async command => {
    if (command === 'list_model_library') return { models: [] };
    if (command === 'api_provider_list') return [];
    if (command === 'get_snapshot') return { projects: [] };
    throw Error('Unexpected native command: ' + command);
  });
  function Dialog({ label, children }) {
    return React.createElement('section', { role: 'dialog', 'aria-label': label }, children);
  }
  const Panel = installGroupWorkspaceUI(React, {
    service, invoke, Dialog, Board: () => null, groupReadyModels, resolveGroupTargets,
  });
  const close = vi.fn();
  return { service, invoke, close, mount: () => render(React.createElement(Panel, { close })) };
}

beforeEach(() => {
  const values = new Map();
  vi.stubGlobal('localStorage', {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
    clear: () => values.clear(),
  });
  localStorage.setItem('opencore.group.workspace.v2', JSON.stringify({ id: 'group-one', channels: { 'group-one': 'general' } }));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('checks Phone capability before any groups, model probes, or unavailable polling', async () => {
  vi.stubGlobal('__OPENCORE_PHONE__', true);
  vi.useFakeTimers();
  let resolveCapability;
  const f = fixture(new Promise(resolve => { resolveCapability = resolve; }));
  f.mount();
  expect(f.service.mock.calls.map(([action]) => action)).toEqual(['capabilities']);
  expect(f.invoke).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Close Chat Core' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: 'Create group', exact: true })).toBeNull();

  await act(async () => resolveCapability({ available: false, executionDevice: 'phone', reason: phoneReason, action: 'phone-execution-settings' }));
  expect(screen.getByRole('heading', { name: 'Chat Core runs on your PC' })).toBeVisible();
  expect(screen.getByText(phoneReason)).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByRole('tablist')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Join with invite' })).toBeNull();
  expect(screen.queryByRole('textbox', { name: 'Group message' })).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(f.service.mock.calls.map(([action]) => action)).toEqual(['capabilities']);
  expect(f.invoke).not.toHaveBeenCalled();
});

it('opens the native execution chooser and preserves the Phone close action without group mutations', async () => {
  vi.stubGlobal('__OPENCORE_PHONE__', true);
  const chooseExecution = vi.fn();
  vi.stubGlobal('__opencoreExecutionSettings', chooseExecution);
  const f = fixture();
  f.mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Choose Phone or Paired PC' }));
  expect(chooseExecution).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Close Chat Core' }));
  expect(f.close).toHaveBeenCalledOnce();
  expect(f.service.mock.calls.map(([action]) => action)).toEqual(['capabilities']);
  expect(f.invoke).not.toHaveBeenCalled();
});

it('keeps a failed Phone capability probe actionable without exposing generic desktop runtime errors', async () => {
  vi.stubGlobal('__OPENCORE_PHONE__', true);
  const f = fixture(Error('requires desktop tools/media runtime'));
  f.mount();
  expect(await screen.findByRole('heading', { name: 'Chat Core runs on your PC' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Choose Phone or Paired PC' })).toBeEnabled();
  expect(screen.queryByText(/requires desktop tools\/media runtime/)).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(f.service.mock.calls.map(([action]) => action)).toEqual(['capabilities']);
  expect(f.invoke).not.toHaveBeenCalled();
});

it('mounts group requests only after a Phone service explicitly confirms availability', async () => {
  vi.stubGlobal('__OPENCORE_PHONE__', true);
  let resolveCapability;
  const f = fixture(new Promise(resolve => { resolveCapability = resolve; }));
  f.mount();
  expect(f.service.mock.calls.map(([action]) => action)).toEqual(['capabilities']);
  await act(async () => resolveCapability({ available: true }));
  expect(await screen.findByRole('heading', { name: 'Existing workspace', exact: true })).toBeVisible();
  expect(f.service.mock.calls.map(([action]) => action)).toEqual(['capabilities', 'list', 'view']);
  expect(screen.getByRole('textbox', { name: 'Group message' })).toBeVisible();
});

it.each([['desktop', false], ['paired PC', true]])('preserves the full %s workspace and composer without a capability probe', async (_name, mobile) => {
  vi.stubGlobal('__OPENCORE_PHONE__', false);
  vi.stubGlobal('__OPENCORE_MOBILE__', mobile);
  const f = fixture();
  f.mount();
  expect(await screen.findByRole('heading', { name: 'Existing workspace', exact: true })).toBeVisible();
  expect(f.service.mock.calls.map(([action]) => action)).toEqual(['list', 'view']);
  const navigation = screen.getByRole('navigation', { name: 'Workspace sections' });
  expect(Array.from(navigation.querySelectorAll('button'), button => button.textContent))
    .toEqual(['People & agents', 'Tasks', 'Projects', 'Settings']);
  fireEvent.change(screen.getByRole('textbox', { name: 'Group message' }), { target: { value: 'Existing group message' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send group message' }));
  await waitFor(() => expect(screen.getByText('Existing group message', { selector: '.gw-message-text' })).toBeVisible());
  expect(f.service.mock.calls.find(([action]) => action === 'message')[1]).toMatchObject({ id: 'group-one', channelId: 'general', text: 'Existing group message' });
  expect(f.service.mock.calls.some(([action]) => action === 'capabilities')).toBe(false);
});
