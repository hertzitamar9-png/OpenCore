import * as React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { createDownloadQueueUI } from './model-download-queue.js';

afterEach(() => cleanup());

it('selects multiple models, enqueues each, and changes the persisted download mode', async () => {
  let state = { schema: 1, mode: 'sequential', paused: false, items: [], activeIds: [] };
  const invoke = vi.fn(async request => {
    if (request.action === 'enqueue') state = { ...state, items: [...state.items, ...request.items.map((item, index) => ({ ...item, id: 'job-' + index, phase: index ? 'queued' : 'downloading', position: index + 1 }))] };
    if (request.action === 'mode') state = { ...state, mode: request.mode };
    if (request.action === 'pause') state = { ...state, paused: true };
    if (request.action === 'resume') state = { ...state, paused: false };
    return state;
  });
  const UI = createDownloadQueueUI(React, null, invoke);
  render(React.createElement(React.Fragment, null,
    React.createElement(UI.Panel), React.createElement(UI.Settings),
    React.createElement(UI.Checkbox, { groupId: 'one', modelId: 'a', label: 'Model A' }),
    React.createElement(UI.Checkbox, { groupId: 'two', modelId: 'b', label: 'Model B' })));
  await screen.findByLabelText('Select download for Model A');
  fireEvent.click(screen.getByLabelText('Select download for Model A'));
  fireEvent.click(screen.getByLabelText('Select download for Model B'));
  fireEvent.click(screen.getByRole('button', { name: 'Download selected (2)' }));
  await waitFor(() => expect(invoke).toHaveBeenCalledWith(expect.objectContaining({ action: 'enqueue', items: [expect.objectContaining({ modelId: 'a' }), expect.objectContaining({ modelId: 'b' })] })));
  expect(screen.getByRole('button', { name: 'Download selected (0)' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Model download mode'), { target: { value: 'parallel' } });
  await waitFor(() => expect(screen.getByLabelText('Model download mode')).toHaveValue('parallel'));
  fireEvent.click(screen.getByRole('button', { name: 'Pause queue' }));
  await screen.findByRole('button', { name: 'Resume queue' });
  expect(within(screen.getByRole('region', { name: 'Model download queue' })).getByText(/1 active · 1 waiting/)).toBeVisible();
});

it('adds more than one batch without losing the remaining selected models', async () => {
  const batches = [];
  const UI = createDownloadQueueUI(React, null, async request => {
    if (request.action === 'enqueue') batches.push(request.items);
    return { schema: 1, mode: 'sequential', paused: false, items: [], activeIds: [] };
  });
  for (let index = 0; index < 301; index++) UI.store.select('group-' + index, { modelId: 'model-' + index });
  await act(() => UI.store.enqueueSelected());
  expect(batches.map(batch => batch.length)).toEqual([128, 128, 45]);
  expect(UI.store.getSnapshot().selected).toEqual([]);
});
