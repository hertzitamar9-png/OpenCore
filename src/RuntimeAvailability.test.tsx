import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { RuntimeAvailability } from './RuntimeAvailability';
import { previewSnapshot } from './mock';

it('keeps a healthy gateway ready when the model is unloaded', () => {
  render(<RuntimeAvailability gateway={{ status: 'ready', port: 8812, restartCount: 0, error: null }} runtime={{ ...previewSnapshot.runtime, status: 'stopped' }} />);
  expect(screen.getByText('Gateway ready')).toBeVisible();
  expect(screen.getByText('Model unloaded')).toBeVisible();
  expect(screen.queryByText('Runtime stopped')).toBeNull();
});

it('does not show a failed gateway as ready just because a model is running', () => {
  render(<RuntimeAvailability gateway={{ status: 'recovering', port: 8812, restartCount: 1, error: 'Port is busy' }} runtime={{ ...previewSnapshot.runtime, status: 'running' }} />);
  expect(screen.getByText('Gateway reconnecting')).toHaveAttribute('title', 'Port is busy');
  expect(screen.getByText('Model loaded')).toBeVisible();
  expect(screen.queryByText('Gateway ready')).toBeNull();
});

it('does not claim availability before receiving gateway health', () => {
  render(<RuntimeAvailability runtime={previewSnapshot.runtime} />);
  expect(screen.getByText('Gateway checking')).toBeVisible();
  expect(screen.queryByText('Gateway ready')).toBeNull();
});
