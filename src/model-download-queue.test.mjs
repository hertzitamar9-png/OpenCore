import * as React from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { createDownloadQueueUI } from './model-download-queue.js';

afterEach(() => cleanup());

it('shows active and waiting installs and keeps queue controls without selection controls', async () => {
  let state = { schema: 1, mode: 'sequential', paused: false, activeIds: ['one'], items: [
    { id: 'one', modelId: 'a', label: 'Model A', phase: 'downloading', position: 1 },
    { id: 'two', modelId: 'b', label: 'Model B', phase: 'queued', position: 2 },
  ] };
  const invoke = vi.fn(async request => {
    if (request.action === 'mode') state = { ...state, mode: request.mode };
    if (request.action === 'pause') state = { ...state, paused: true };
    if (request.action === 'resume') state = { ...state, paused: false };
    if (request.action === 'remove') state = { ...state, items: state.items.filter(item => item.id !== request.id) };
    return state;
  });
  const UI = createDownloadQueueUI(React, null, invoke);
  render(React.createElement(React.Fragment, null,
    React.createElement(UI.Panel), React.createElement(UI.Settings),
    React.createElement('article', { 'aria-label': 'Model B' }, React.createElement(UI.CardProgress, { modelId: 'b' }))));
  const queue = within(await screen.findByRole('region', { name: 'Model download queue' }));
  const card = within(screen.getByRole('article', { name: 'Model B' }));
  expect(queue.getByText('1 active · 1 waiting')).toBeVisible();
  expect(card.getByText('Queued · position 2')).toBeVisible();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(queue.queryByRole('button', { name: /Download selected|Clear selection/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Model download mode'), { target: { value: 'parallel' } });
  await waitFor(() => expect(screen.getByLabelText('Model download mode')).toHaveValue('parallel'));
  fireEvent.click(screen.getByRole('button', { name: 'Pause queue' }));
  await screen.findByRole('button', { name: 'Resume queue' });
  fireEvent.click(card.getByRole('button', { name: 'Remove from queue' }));
  await waitFor(() => expect(queue.getByText('1 active · 0 waiting')).toBeVisible());
  expect(card.queryByText('Queued · position 2')).not.toBeInTheDocument();
});
