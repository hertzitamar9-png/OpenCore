import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { invoke } from '@tauri-apps/api/core';
import { SpeechRuntimeControls } from './SpeechRuntimeControls';

vi.mock('@tauri-apps/api/core', () => ({invoke: vi.fn()}));
const speech = {modelId: 'phonon-2', enabled: true, installed: true, idleMode: 'cold' as const,
  workerReady: false, phase: 'ready', runtimePrecision: 'bf16' as const, runtimeCacheBytes: 1_254_343_385,
  runtimeCacheEntries: [{precision: 'bf16', bytes: 1_254_343_385}], denseCacheHit: true};
afterEach(() => vi.resetAllMocks());

it('labels the cache as derived disk tensors and prepares only the current session', async () => {
  vi.mocked(invoke).mockResolvedValue({...speech, workerReady: true, prewarmedForSession: true});
  render(<SpeechRuntimeControls speech={speech} />);
  expect(screen.getByText(/Derived dense runtime cache/)).toHaveTextContent('1.25 GB');
  expect(screen.getByText(/same installed checkpoint/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', {name: 'Prepare next dictation'}));
  await waitFor(() => expect(invoke).toHaveBeenCalledWith('speech_prewarm_session'));
  expect(invoke).not.toHaveBeenCalledWith('speech_set_idle_mode', expect.anything());
  expect(await screen.findByText(/Prepared in CPU RAM for the next dictation/)).toBeVisible();
});

it('cancels a real prewarm while the start command is pending', async () => {
  let reject!: (reason: unknown) => void;
  vi.mocked(invoke).mockImplementation(command => command === 'speech_prewarm_session'
    ? new Promise((_, failure) => {reject = failure;})
    : Promise.resolve(speech));
  render(<SpeechRuntimeControls speech={speech} />);
  fireEvent.click(screen.getByRole('button', {name: 'Prepare next dictation'}));
  fireEvent.click(await screen.findByRole('button', {name: 'Cancel preparation'}));
  await waitFor(() => expect(invoke).toHaveBeenCalledWith('speech_cancel_prewarm'));
  reject(new Error('Speech startup was cancelled'));
});

it('clears the optional derived cache through its exact native action', async () => {
  vi.mocked(invoke).mockResolvedValue({...speech, runtimeCacheBytes: 0, runtimeCacheEntries: []});
  render(<SpeechRuntimeControls speech={speech} />);
  fireEvent.click(screen.getByRole('button', {name: 'Clear derived cache'}));
  await waitFor(() => expect(invoke).toHaveBeenCalledWith('speech_clear_runtime_cache'));
});
