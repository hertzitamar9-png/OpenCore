import * as React from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const load = () => import('./model-install-progress.js').catch(() => ({}));
const progress = { modelId: 'model-a', operationId: 'job-a', phase: 'downloading', downloadedBytes: 1e9, totalBytes: 5e9, currentFile: 'weights.gguf' };
afterEach(() => { cleanup(); vi.useRealTimers(); });

it('estimates remaining download time only from recent transferred bytes and resets after pause', async () => {
  const { createDownloadRateTracker } = await load();
  expect(createDownloadRateTracker).toBeTypeOf('function');
  const meter = createDownloadRateTracker();
  expect(meter.observe(progress, 0).remainingSeconds).toBeNull();
  expect(meter.observe({ ...progress, downloadedBytes: 2e9 }, 4000)).toMatchObject({ bytesPerSecond: 250e6, remainingSeconds: 12 });
  expect(meter.observe({ ...progress, phase: 'paused', downloadedBytes: 2e9 }, 5000).remainingSeconds).toBeNull();
  expect(meter.observe({ ...progress, downloadedBytes: 2e9 }, 60000).remainingSeconds).toBeNull();
  expect(meter.observe({ ...progress, downloadedBytes: 3e9 }, 64000).remainingSeconds).toBe(8);
  expect(meter.observe({ ...progress, phase: 'verifying', downloadedBytes: 5e9 }, 65000).remainingSeconds).toBeNull();
});

it('does not reuse an old job rate, a stalled rate, or an unknown total', async () => {
  const { createDownloadRateTracker } = await load();
  expect(createDownloadRateTracker).toBeTypeOf('function');
  const meter = createDownloadRateTracker();
  meter.observe(progress, 0);
  expect(meter.observe({ ...progress, downloadedBytes: 2e9 }, 4000).remainingSeconds).toBe(12);
  expect(meter.observe({ ...progress, downloadedBytes: 2e9 }, 16000).remainingSeconds).toBeNull();
  expect(meter.observe({ ...progress, operationId: 'new-job', downloadedBytes: 2e9 }, 17000).remainingSeconds).toBeNull();
  expect(meter.observe({ ...progress, totalBytes: 0, downloadedBytes: 3e9 }, 21000).remainingSeconds).toBeNull();
});

it('shows the same measured bytes and ETA globally and in the card with separate pause and cancel actions', async () => {
  const { createModelInstallProgress } = await load();
  expect(createModelInstallProgress).toBeTypeOf('function');
  vi.useFakeTimers(); vi.setSystemTime(0);
  const View = createModelInstallProgress(React), pause = vi.fn(), cancel = vi.fn();
  const panels = value => React.createElement(React.Fragment, null,
    React.createElement('section', { 'aria-label': 'Global installation' }, React.createElement(View, { progress: value, label: 'Model A', onPause: pause, onCancel: cancel })),
    React.createElement('article', { 'aria-label': 'Model A card' }, React.createElement(View, { progress: value, label: 'Model A', onPause: pause, onCancel: cancel })));
  const { rerender } = render(panels(progress));
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  rerender(panels({ ...progress, downloadedBytes: 2e9 }));
  const global = within(screen.getByRole('region', { name: 'Global installation' }));
  const card = within(screen.getByRole('article', { name: 'Model A card' }));
  for (const panel of [global, card]) {
    expect(panel.getByText('2.000 GB / 5.000 GB')).toBeVisible();
    expect(panel.getByText('250.00 MB/s')).toBeVisible();
    expect(panel.getByText(/About 12 seconds remaining/)).toBeVisible();
    expect(panel.getByRole('progressbar')).toHaveAttribute('value', '2000000000');
  }
  await act(async () => { fireEvent.click(card.getByRole('button', { name: 'Pause installation' })); });
  expect(pause).toHaveBeenCalledOnce(); expect(cancel).not.toHaveBeenCalled();
  await act(async () => { fireEvent.click(global.getByRole('button', { name: 'Cancel installation' })); });
  expect(cancel).toHaveBeenCalledOnce();
});

it('keeps verification indeterminate and never presents download ETA as verification completion time', async () => {
  const { createModelInstallProgress } = await load();
  expect(createModelInstallProgress).toBeTypeOf('function');
  const View = createModelInstallProgress(React);
  render(React.createElement(View, { progress: { ...progress, phase: 'verifying', downloadedBytes: 5e9 }, label: 'Model A' }));
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('value');
  expect(screen.queryByText(/remaining/)).toBeNull();
  expect(screen.queryByText(/MB\/s/)).toBeNull();
});
